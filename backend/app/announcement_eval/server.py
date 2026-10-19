"""Loopback-only evaluation host around the actual application.

Launch exclusively through announcement_eval.runner. All patches are process-local
and restored on exit. Normal app.main imports no experiment code or routes.
"""
from contextlib import asynccontextmanager, ExitStack
from contextvars import ContextVar
from datetime import datetime, timezone
import asyncio
import json
from hashlib import sha256
import os
from pathlib import Path
import secrets
import time
from unittest.mock import patch

from fastapi import Depends, FastAPI, HTTPException, Request
from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.db.tables import Task, TaskEvent, TaskAttempt, TaskPresentation, LiveSession, LiveSessionEvent, NotificationOutbox, OrchestrationDecision, OrchestrationMetric, ConversationObservation
from app.main import app as production_app, lifespan as production_lifespan
from app.services.jarvis_coordinator import jarvis_coordinator
from app.services.openai_live_adapter import OpenAILiveAdapter, openai_live_adapter
from app.services.task_presentations import TaskPresentationService, task_presentation_service
from app.services.task_service import task_service
from app.services.worker_coordinator import worker_coordinator
from app.announcement_eval.profiles import TERMINAL, PREFERENCE_PROFILES, append_commands, profile, profile_identity, revise_live_prompt, revise_backend_prompt
from app.announcement_eval.preferences import current_preference, save_preference, tool_definitions
from app.services.jarvis_brain import restricted_brain_gateway, BrainResult
from app.services import openai_live_adapter as adapter_module

ROOT = Path(__file__).resolve().parents[3]
ACTIVE_UPDATE = ContextVar("announcement_eval_update", default=False)
PREFERENCE_ACTION = ContextVar("announcement_eval_preference_action", default=None)


class AppendDelivery:
    """Experimental transport ordering; never decides whether Live should speak."""

    def __init__(self):
        self.pending = {}

    def receipt(self, session_id, generation, event):
        if event.get("type") != "session.thinking.appended":
            return
        key = (session_id, generation, event.get("client_event_id"))
        if key in self.pending:
            self.pending[key].set()

    async def send(self, provider, commands, send, record, *, barrier=False, timeout=5):
        key = (provider.session_id, provider.generation, commands[0].get("event_id"))
        if barrier:
            self.pending[key] = asyncio.Event()
        try:
            for index, command in enumerate(commands):
                if barrier and index == 1:
                    record("timing", {"type": "append.receipt_barrier.waiting", "client_event_id": key[2]},
                        session_id=key[0], generation=key[1])
                    try:
                        await asyncio.wait_for(self.pending[key].wait(), timeout)
                    except TimeoutError:
                        record("timing", {"type": "append.receipt_barrier.timeout", "client_event_id": key[2]},
                            session_id=key[0], generation=key[1])
                        raise
                record("outgoing", command, session_id=key[0], generation=key[1])
                await send(provider, command)
        finally:
            self.pending.pop(key, None)


def isolated_root(path: Path, database: Path) -> Path:
    path = path.resolve()
    base = (ROOT / "data" / "announcement-evaluation").resolve()
    if path == base or not path.is_relative_to(base) or not database.resolve().is_relative_to(path):
        raise ValueError("Evaluation requires its own project data directory and database")
    return path


def serial(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).timestamp() * 1000 if value.tzinfo is None else value.timestamp() * 1000
    return value


def row_values(row, names):
    return {key: serial(getattr(row, key)) for key in names}


class Experiment:
    def __init__(self, root, selected, timing):
        self.root, self.selected, self.timing = root, selected, timing
        self.events = []
        self.claims = {}
        self.completed = {}
        self.worker_id = None
        self.stop = asyncio.Event()
        self.jobs = set()

    def record(self, stream, event, **identity):
        # Never retain audio, transport SDP, credentials or encrypted reasoning here.
        from app.services.live_model_diagnostics import clean
        row = {"epoch_ms": time.time_ns() / 1e6, "monotonic_ns": time.perf_counter_ns(),
            "stream": stream, **identity, "event": clean(event)}
        def prompt_hashes(value, path=""):
            hashes = {}
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "instructions" and isinstance(item, str):
                        hashes[path + key] = sha256(item.encode()).hexdigest()
                    else:
                        hashes.update(prompt_hashes(item, path + key + "."))
            return hashes
        row["prompt_sha256"] = prompt_hashes(event)
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
            # Execution authority ends at completion. Redelivery resynchronizes
            # the durable projection rather than reusing a stale worker fence.
            await task_presentation_service.sync_candidates()
            return await task_service.get_task(task_id)
        claim = self.claims.get(task_id)
        if claim is None:
            claimed = await worker_coordinator.claim_next(self.worker_id)
            claim = claimed.task
            if claim is None or claim.task_id != task_id:
                raise ValueError("Fixture claim differs from requested task; task order must be explicit")
            self.claims[task_id] = claim
        kwargs = dict(summary=summary, status=status, idempotency_key=f"eval-finish:{task_id}",
            attempt_id=claim.attempt_id, execution_run_id=claim.execution_run_id, claim_token=claim.claim_token)
        if status == "completed":
            detail = await task_service.get_task(task_id)
            if "desk" in detail.task.goal.lower():
                kwargs["final_text"] = "# Desk checklist\n\n- Keep notebooks together.\n- Leave space for a lamp.\n- Clear a spot for tea.\n"
            else:
                kwargs["final_text"] = "# Hello World\n\nHello, World!\n"
        result = await task_service.complete(task_id, **kwargs)
        self.completed[task_id] = kwargs
        await worker_coordinator.release_claim(self.worker_id, claim.attempt_id, claim_token=claim.claim_token)
        self.record("fixture", {"type": "task.finished", "task_id": task_id, "status": status})
        return result


@asynccontextmanager
async def eval_lifespan(application):
    root = isolated_root(Path(os.environ["JARVIS_EVAL_ROOT"]), settings.database_path)
    selected = profile(os.environ["JARVIS_EVAL_PROFILE"])
    timing = os.environ.get("JARVIS_EVAL_TIMING", "listening")
    if (settings.openai_live_model, settings.openai_live_voice, settings.openai_live_responses_model,
        settings.openai_live_reasoning_effort) != ("gpt-live-1", "marin", "gpt-5.6-luna", "low"):
        raise ValueError("Experiment model settings differ from frozen comparison")
    if settings.task_pool_enabled and not os.environ.get("JARVIS_EVAL_REAL_WORKER"):
        raise ValueError("Deterministic experiments require an isolated fixture worker")
    experiment = Experiment(root, selected, timing)
    append_delivery = AppendDelivery()
    application.state.announcement_eval = experiment
    original_send = openai_live_adapter._send
    original_connect = openai_live_adapter.connect
    original_handle = openai_live_adapter._handle_event
    original_config = OpenAILiveAdapter._session_config
    original_router = OpenAILiveAdapter._router_instructions
    original_requires = TaskPresentationService._requires_commentary
    original_policy_allows = TaskPresentationService._policy_allows_candidate
    original_queue = jarvis_coordinator._queue_controlled_response
    original_floor = jarvis_coordinator._floor_state
    original_gateway = restricted_brain_gateway.execute
    original_catalog = adapter_module.get_jarvis_tool_definitions
    original_set_policy = task_service.set_update_policy
    original_trace = jarvis_coordinator._trace_response_gate

    async def connect(**kwargs):
        experiment.record("requests", {"type": "provider.connect.request"},
            session_id=kwargs["session_id"], generation=kwargs["generation"])
        try:
            return await original_connect(**kwargs)
        except Exception as error:
            experiment.record("requests", {"type": "provider.connect.failed", "error": str(error)},
                session_id=kwargs["session_id"], generation=kwargs["generation"])
            raise

    async def send(provider_session, command):
        preference = None
        if selected.name in PREFERENCE_PROFILES and str(command.get("event_id", "")).startswith("jarvis-live-update:"):
            content = command.get("content", "")
            try:
                fact = json.loads(content[content.index("{"):])
            except (ValueError, TypeError):
                fact = {}
            if fact.get("event_type") in TERMINAL:
                preference = await current_preference(fact["task_id"])
        commands = append_commands(command, selected, preference)
        await append_delivery.send(provider_session, commands, original_send, experiment.record,
            barrier=selected.name == "live_thinking_receipt_instruction" and len(commands) == 2)

    async def gateway(**kwargs):
        result = await original_gateway(**kwargs)
        if selected.name in PREFERENCE_PROFILES and kwargs["tool_name"] in {"dispatch_task","start_workflow","set_task_update_policy"} and result.output.get("task_id"):
            preference = await current_preference(result.output["task_id"])
            experiment.record("preferences", {"type":"notification.preference.saved","task_id":result.output["task_id"],**preference})
        return result

    async def set_policy(task_id, policy, **kwargs):
        if key := PREFERENCE_ACTION.get():
            kwargs["idempotency_key"] = key
        return await original_set_policy(task_id, policy, **kwargs)

    async def handle(provider_session, event):
        append_delivery.receipt(provider_session.session_id, provider_session.generation, event)
        if event.get("type") != "session.output_audio.delta":
            experiment.record("incoming", event, session_id=provider_session.session_id, generation=provider_session.generation)
        inner = event.get("event", {}) if event.get("type") == "response.event" else {}
        tier = inner.get("response", {}).get("service_tier")
        if tier is not None and tier != "priority":
            experiment.record("incoming", {"type": "error", "error": "Experiment backend left the fixed priority tier"},
                session_id=provider_session.session_id, generation=provider_session.generation)
            raise ValueError("Experiment backend left the fixed priority tier")
        if selected.name.startswith("live_") and event.get("type") == "session.instructions.appended":
            # An instruction-only outcome has the same context-submission
            # contract as factual appends. This is a transport receipt, never
            # a claim that the user heard an announcement.
            client_event_id = str(event.get("client_event_id") or "")
            pending = provider_session.pending_commentary.pop(client_event_id, None)
            if pending is not None:
                request_id, presentation_ids, transcript = pending
                openai_live_adapter._mark_meaningful_activity(provider_session)
                await openai_live_adapter._emit(provider_session, "assistant_update_submitted", {
                    "request_id": request_id, "presentation_ids": list(presentation_ids),
                    "response_kind": "task_update", "submitted_transcript": transcript,
                    "submission_ack": "context_appended", "append_type": event["type"],
                }, event_id=f"live-instruction-ack:{client_event_id}")
            return
        return await original_handle(provider_session, event)

    def config(context):
        value = original_config(context)
        backend = value["delegation"]["responses"]
        if value["audio"]["output"]["voice"] != "marin" or backend["service_tier"] != "priority" or backend["parallel_tool_calls"] is not False:
            raise ValueError("Experiment voice/tier/tool settings differ from frozen comparison")
        value["instructions"] = revise_live_prompt(value["instructions"], selected, context)
        (root / "voice-prompt.txt").write_text(value["instructions"], encoding="utf-8")
        (root / "session-configuration.json").write_text(json.dumps(value, indent=2), encoding="utf-8")
        return value

    def requires(candidate):
        if candidate.required_facts_json.get("event_type") in TERMINAL and selected.channel is not None:
            return candidate.commentary_attempted_at is None and selected.channel == "commentary"
        return original_requires(candidate)

    def policy_allows(candidate_type, policy):
        # Exercise silence after delivery while retaining the admitted task
        # preference. The normal projector still owns candidate creation.
        if selected.channel and candidate_type == "terminal_result":
            return True
        return original_policy_allows(candidate_type, policy)

    async def queue(*args, **kwargs):
        reason = kwargs.get("reason") or (args[2] if len(args) > 2 else None)
        token = ACTIVE_UPDATE.set(reason == "task_update")
        try:
            return await original_queue(*args, **kwargs)
        finally:
            ACTIVE_UPDATE.reset(token)

    async def floor(*args, **kwargs):
        actual = await original_floor(*args, **kwargs)
        if timing == "immediate" and ACTIVE_UPDATE.get() and actual in {"assistant_speaking", "user_speaking", "thinking"}:
            experiment.record("timing", {"type": "floor.context_delivery", "actual_floor": actual})
            return "listening"  # Only this update's eligibility; durable floor is unchanged.
        return actual

    def trace(event_type, **kwargs):
        experiment.record("arbiter", {"type": event_type, "reason": kwargs.get("reason"), "detail": kwargs.get("detail")},
            session_id=kwargs.get("session_id"), generation=kwargs.get("generation"))
        return original_trace(event_type, **kwargs)

    with ExitStack() as instrumentation:
        for target, name, wrapper in [(openai_live_adapter,"_send",send),
                (openai_live_adapter,"connect",connect),(openai_live_adapter,"_handle_event",handle),
                (OpenAILiveAdapter,"_session_config",staticmethod(config)),
                (jarvis_coordinator,"_trace_response_gate",trace)]:
            instrumentation.enter_context(patch.object(target,name,wrapper))
        # Adoption observes the production behavior directly. The wrappers above
        # only record commands/configuration/events and return them unchanged.
        if selected.name != "production_A":
            for target, name, wrapper in [(restricted_brain_gateway,"execute",gateway),
                    (task_service,"set_update_policy",set_policy),
                    (adapter_module,"get_jarvis_tool_definitions",lambda provider: tool_definitions(original_catalog(provider)) if selected.name in PREFERENCE_PROFILES else original_catalog(provider)),
                    (OpenAILiveAdapter,"_router_instructions",staticmethod(lambda c: revise_backend_prompt(original_router(c),selected))),
                    (TaskPresentationService,"_requires_commentary",staticmethod(requires)),
                    (TaskPresentationService,"_policy_allows_candidate",staticmethod(policy_allows)),
                    (jarvis_coordinator,"_queue_controlled_response",queue),(jarvis_coordinator,"_floor_state",floor)]:
                instrumentation.enter_context(patch.object(target,name,wrapper))
        async with production_lifespan(application):
            registration = None if settings.task_pool_enabled else await worker_coordinator.activate(process_identity="announcement-eval")
            if not settings.task_pool_enabled and registration is None:
                raise ValueError("Unable to start deterministic fixture worker")
            experiment.worker_id = registration.worker_id if registration else None
            heart = asyncio.create_task(experiment.heartbeat()) if registration else None
            experiment.record("configuration", {"type": "experiment.started", **profile_identity(selected, timing),
                "candidate": application.state.acceptance_runtime["candidate"]})
            try:
                yield
            finally:
                experiment.stop.set()
                for job in experiment.jobs:
                    job.cancel()
                await asyncio.gather(*experiment.jobs, return_exceptions=True)
                if heart:
                    await heart


app = FastAPI(lifespan=eval_lifespan, middleware=production_app.user_middleware,
    exception_handlers=production_app.exception_handlers)


def require_eval(request: Request):
    if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "Loopback evaluation only")
    token = os.environ.get("JARVIS_EVAL_TOKEN", "")
    if not token or not secrets.compare_digest(request.headers.get("x-eval-token", ""), token):
        raise HTTPException(403, "Evaluation runner authentication required")
    return request.app.state.announcement_eval


@app.get("/__eval/state", dependencies=[Depends(require_eval)])
async def state(request: Request):
    experiment = request.app.state.announcement_eval
    async with session_scope() as db:
        tasks = list((await db.execute(select(Task).order_by(Task.created_at))).scalars().all())
        events = list((await db.execute(select(TaskEvent).order_by(TaskEvent.sequence))).scalars().all())
        presentations = list((await db.execute(select(TaskPresentation))).scalars().all())
        session_events = list((await db.execute(select(LiveSessionEvent).order_by(LiveSessionEvent.sequence))).scalars().all())
        outbox = list((await db.execute(select(NotificationOutbox))).scalars().all())
        sessions = list((await db.execute(select(LiveSession))).scalars().all())
        attempts = list((await db.execute(select(TaskAttempt))).scalars().all())
        decisions = list((await db.execute(select(OrchestrationDecision))).scalars().all())
        metrics = list((await db.execute(select(OrchestrationMetric))).scalars().all())
        observations = list((await db.execute(select(ConversationObservation))).scalars().all())
        return {"epoch_ms": time.time_ns() / 1e6, "profile": profile_identity(experiment.selected, experiment.timing),
            "observer": [row_values(o,("id","session_id","status","input_json","result_json","usage_json","error")) for o in observations],
            "tasks": [row_values(t, ("id", "goal", "status", "created_at", "finished_at", "terminal_summary", "presentation_policy")) for t in tasks],
            "task_events": [row_values(e, ("id", "task_id", "event_type", "payload_json", "created_at", "sequence")) for e in events],
            "presentations": [row_values(p, ("id", "task_id", "source_task_event_id", "candidate_type", "required_facts_json", "state", "handled_by", "handled_at", "handled_event_id", "commentary_attempted_at", "reserved_session_id", "reserved_generation")) for p in presentations],
            "sessions": [row_values(s, ("id", "generation", "controller_id", "lifecycle_state", "created_at")) for s in sessions],
            "attempts": [row_values(a, ("id", "task_id", "execution_run_id", "started_at", "ended_at", "usage_summary_json")) for a in attempts],
            "background_decisions": [row_values(d, ("id", "task_id", "status", "claim_generation", "model", "effort", "last_error")) for d in decisions],
            "background_metrics": [row_values(m, ("id", "kind", "decision_id", "claim_generation", "values_json", "created_at")) for m in metrics],
            "session_events": [row_values(e, ("id", "session_id", "event_type", "payload_json", "created_at", "sequence")) for e in session_events],
            "notifications": [row_values(e, ("id", "task_id", "status")) for e in outbox],
            "provider_events": experiment.events}


@app.post("/__eval/action", dependencies=[Depends(require_eval)])
async def action(request: Request):
    experiment = request.app.state.announcement_eval
    value = await request.json()
    name, task_id = value["action"], value.get("task_id")
    if name == "finish":
        if settings.task_pool_enabled:
            raise HTTPException(409, "Real worker outcomes cannot be fabricated")
        await experiment.finish(task_id, value.get("status", "completed"), value.get("summary", "The Hello World page is ready."))
    elif name == "question":
        await task_service.ask_question(task_id, question="Would you like a blue or a green heading?", idempotency_key=f"eval-question:{task_id}")
    elif name == "sync":
        await task_presentation_service.sync_candidates()
    elif name in {"observer_stop", "observer_restart"}:
        from app.services.conversation_observer import conversation_observer
        await conversation_observer.stop()
        if name == "observer_restart": await conversation_observer.start()
    elif name == "marker":
        pass
    else:
        raise HTTPException(400, "Unknown fixture action")
    experiment.record("fixture", {"type": f"action.{name}", **value})
    return {"ok": True, "epoch_ms": time.time_ns() / 1e6}


# Evaluation endpoints precede the production static-export catch-all.
app.router.routes.extend(production_app.router.routes)
