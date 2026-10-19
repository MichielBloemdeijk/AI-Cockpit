"""Loopback-only self-test host around the unmodified production application.

Launched exclusively by app.selftest.harness.run_case. The wrappers below only
record provider traffic and configuration; they never change behavior. A
deterministic fixture worker completes tasks at scenario-controlled moments.
"""
from contextlib import asynccontextmanager, ExitStack
from datetime import datetime, timezone
import asyncio
import json
import os
from pathlib import Path
import secrets
import time
from unittest.mock import patch

from fastapi import Depends, FastAPI, HTTPException, Request
from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.db.tables import (Task, TaskEvent, TaskAttempt, TaskPresentation, LiveSession, LiveSessionEvent,
    NotificationOutbox, ConversationObservation)
from app.main import app as production_app, lifespan as production_lifespan
from app.services.jarvis_coordinator import jarvis_coordinator
from app.services.openai_live_adapter import OpenAILiveAdapter, openai_live_adapter
from app.services.task_presentations import task_presentation_service
from app.services.task_service import task_service
from app.services.worker_coordinator import worker_coordinator

ROOT = Path(__file__).resolve().parents[3]
FIXTURE_RESULTS = {
    "desk": "# Desk checklist\n\n- Keep notebooks together.\n- Leave space for a lamp.\n- Clear a spot for tea.\n",
    "default": "# Hello World\n\nHello, World!\n",
}


def isolated_root(path: Path, database: Path) -> Path:
    path = path.resolve()
    base = (ROOT / "data" / "selftest").resolve()
    if path == base or not path.is_relative_to(base) or not database.resolve().is_relative_to(path):
        raise ValueError("Self-test host requires its own data/selftest directory and database")
    return path


def serial(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).timestamp() * 1000 if value.tzinfo is None else value.timestamp() * 1000
    return value


def row_values(row, names):
    return {key: serial(getattr(row, key)) for key in names}


class Recorder:
    def __init__(self, root: Path):
        self.root = root
        self.events = []
        self.claims = {}
        self.completed = {}
        self.worker_id = None
        self.stop = asyncio.Event()

    def record(self, stream, event, **identity):
        # Never retain audio, transport SDP, credentials or encrypted reasoning here.
        from app.services.live_model_diagnostics import clean
        row = {"epoch_ms": time.time_ns() / 1e6, "monotonic_ns": time.perf_counter_ns(),
            "stream": stream, **identity, "event": clean(event)}
        self.events.append(row)
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    async def heartbeat(self):
        while not self.stop.is_set():
            await worker_coordinator.heartbeat(self.worker_id)
            try:
                await asyncio.wait_for(self.stop.wait(), 2)
            except TimeoutError:
                pass

    async def finish(self, task_id, status, summary):
        if task_id in self.completed:
            current = await task_service.get_task(task_id)
            if current.task.status != "queued":
                # Redelivery resynchronizes the projection rather than reusing a stale worker fence.
                await task_presentation_service.sync_candidates()
                return current
            # Re-queued after finishing (a background retry): work it again.
            self.completed.pop(task_id); self.claims.pop(task_id, None)
        claim = self.claims.get(task_id)
        detail = await task_service.get_task(task_id)
        if claim is None and detail.task.status not in {"queued"}:
            raise HTTPException(409, f"Task is {detail.task.status}, not claimable; the scenario expected it to be queued")
        if claim is None:
            claimed = await worker_coordinator.claim_next(self.worker_id)
            claim = claimed.task
            if claim is None or claim.task_id != task_id:
                raise ValueError("Fixture claim differs from requested task; task order must be explicit")
            self.claims[task_id] = claim
        kwargs = dict(summary=summary, status=status, idempotency_key=f"selftest-finish:{task_id}",
            attempt_id=claim.attempt_id, execution_run_id=claim.execution_run_id, claim_token=claim.claim_token)
        if status == "completed":
            detail = await task_service.get_task(task_id)
            kwargs["final_text"] = FIXTURE_RESULTS["desk" if "desk" in detail.task.goal.lower() else "default"]
        result = await task_service.complete(task_id, **kwargs)
        self.completed[task_id] = kwargs
        await worker_coordinator.release_claim(self.worker_id, claim.attempt_id, claim_token=claim.claim_token)
        self.record("fixture", {"type": "task.finished", "task_id": task_id, "status": status})
        return result


@asynccontextmanager
async def selftest_lifespan(application):
    root = isolated_root(Path(os.environ["JARVIS_EVAL_ROOT"]), settings.database_path)
    if settings.task_pool_enabled and not os.environ.get("JARVIS_EVAL_REAL_WORKER"):
        raise ValueError("Deterministic scenarios require the fixture worker")
    recorder = Recorder(root)
    application.state.selftest = recorder
    original_send = openai_live_adapter._send
    original_connect = openai_live_adapter.connect
    original_handle = openai_live_adapter._handle_event
    original_config = OpenAILiveAdapter._session_config
    original_trace = jarvis_coordinator._trace_response_gate

    async def connect(**kwargs):
        recorder.record("requests", {"type": "provider.connect.request"},
            session_id=kwargs["session_id"], generation=kwargs["generation"])
        try:
            return await original_connect(**kwargs)
        except Exception as error:
            recorder.record("requests", {"type": "provider.connect.failed", "error": str(error)},
                session_id=kwargs["session_id"], generation=kwargs["generation"])
            raise

    async def send(provider_session, command):
        recorder.record("outgoing", command, session_id=provider_session.session_id, generation=provider_session.generation)
        await original_send(provider_session, command)

    async def handle(provider_session, event):
        if event.get("type") != "session.output_audio.delta":
            recorder.record("incoming", event, session_id=provider_session.session_id, generation=provider_session.generation)
        return await original_handle(provider_session, event)

    def config(context):
        value = original_config(context)
        (root / "voice-prompt.txt").write_text(value["instructions"], encoding="utf-8")
        (root / "session-configuration.json").write_text(json.dumps(value, indent=2), encoding="utf-8")
        return value

    def trace(event_type, **kwargs):
        recorder.record("arbiter", {"type": event_type, "reason": kwargs.get("reason"), "detail": kwargs.get("detail")},
            session_id=kwargs.get("session_id"), generation=kwargs.get("generation"))
        return original_trace(event_type, **kwargs)

    with ExitStack() as instrumentation:
        for target, name, wrapper in [(openai_live_adapter, "_send", send),
                (openai_live_adapter, "connect", connect), (openai_live_adapter, "_handle_event", handle),
                (OpenAILiveAdapter, "_session_config", staticmethod(config)),
                (jarvis_coordinator, "_trace_response_gate", trace)]:
            instrumentation.enter_context(patch.object(target, name, wrapper))
        async with production_lifespan(application):
            registration = None if settings.task_pool_enabled else await worker_coordinator.activate(process_identity="selftest")
            if not settings.task_pool_enabled and registration is None:
                raise ValueError("Unable to start deterministic fixture worker")
            recorder.worker_id = registration.worker_id if registration else None
            heart = asyncio.create_task(recorder.heartbeat()) if registration else None
            recorder.record("configuration", {"type": "selftest.started",
                "live_model": settings.openai_live_model, "voice": settings.openai_live_voice,
                "candidate": application.state.acceptance_runtime["candidate"]})
            try:
                yield
            finally:
                recorder.stop.set()
                if heart:
                    await heart


app = FastAPI(lifespan=selftest_lifespan, middleware=production_app.user_middleware,
    exception_handlers=production_app.exception_handlers)


def require_selftest(request: Request):
    if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "Loopback self-test only")
    token = os.environ.get("JARVIS_EVAL_TOKEN", "")
    if not token or not secrets.compare_digest(request.headers.get("x-eval-token", ""), token):
        raise HTTPException(403, "Self-test runner authentication required")
    return request.app.state.selftest


@app.get("/__eval/state", dependencies=[Depends(require_selftest)])
async def state(request: Request):
    recorder = request.app.state.selftest
    async with session_scope() as db:
        tasks = list((await db.execute(select(Task).order_by(Task.created_at))).scalars().all())
        events = list((await db.execute(select(TaskEvent).order_by(TaskEvent.sequence))).scalars().all())
        presentations = list((await db.execute(select(TaskPresentation))).scalars().all())
        session_events = list((await db.execute(select(LiveSessionEvent).order_by(LiveSessionEvent.sequence))).scalars().all())
        outbox = list((await db.execute(select(NotificationOutbox))).scalars().all())
        sessions = list((await db.execute(select(LiveSession))).scalars().all())
        attempts = list((await db.execute(select(TaskAttempt))).scalars().all())
        observations = list((await db.execute(select(ConversationObservation))).scalars().all())
        return {"epoch_ms": time.time_ns() / 1e6,
            "observer": [row_values(o, ("id", "session_id", "status", "input_json", "result_json", "usage_json", "error")) for o in observations],
            "tasks": [row_values(t, ("id", "goal", "status", "created_at", "finished_at", "terminal_summary", "presentation_policy")) for t in tasks],
            "task_events": [row_values(e, ("id", "task_id", "event_type", "payload_json", "created_at", "sequence")) for e in events],
            "presentations": [row_values(p, ("id", "task_id", "source_task_event_id", "candidate_type", "required_facts_json", "state", "handled_by", "handled_at", "handled_event_id", "commentary_attempted_at", "reserved_session_id", "reserved_generation")) for p in presentations],
            "sessions": [row_values(s, ("id", "generation", "controller_id", "lifecycle_state", "created_at")) for s in sessions],
            "attempts": [row_values(a, ("id", "task_id", "execution_run_id", "started_at", "ended_at", "usage_summary_json")) for a in attempts],
            "session_events": [row_values(e, ("id", "session_id", "event_type", "payload_json", "created_at", "sequence")) for e in session_events],
            "notifications": [row_values(e, ("id", "task_id", "status")) for e in outbox],
            "provider_events": recorder.events}


@app.post("/__eval/action", dependencies=[Depends(require_selftest)])
async def action(request: Request):
    recorder = request.app.state.selftest
    value = await request.json()
    name, task_id = value["action"], value.get("task_id")
    if name == "finish":
        if settings.task_pool_enabled:
            raise HTTPException(409, "Real worker outcomes cannot be fabricated")
        await recorder.finish(task_id, value.get("status", "completed"), value.get("summary") or "The Hello World page is ready.")
    elif name == "question":
        await task_service.ask_question(task_id, question="Would you like a blue or a green heading?", idempotency_key=f"selftest-question:{task_id}")
    elif name == "sync":
        await task_presentation_service.sync_candidates()
    elif name in {"observer_stop", "observer_restart"}:
        from app.services.conversation_observer import conversation_observer
        await conversation_observer.stop()
        if name == "observer_restart":
            await conversation_observer.start()
    elif name != "marker":
        raise HTTPException(400, "Unknown fixture action")
    recorder.record("fixture", {"type": f"action.{name}", **value})
    return {"ok": True, "epoch_ms": time.time_ns() / 1e6}


# Self-test endpoints precede the production static-export catch-all.
app.router.routes.extend(production_app.router.routes)
