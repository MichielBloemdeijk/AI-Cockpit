"""Independent durable feedback intake and single-flight background decisions.

No inference is permitted inside a DB transaction. Runtime implementations
receive issue-specific context and backend-bound tools, never worker leases.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import os
import socket
from time import monotonic
from typing import Awaitable, Callable, Protocol
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import settings
from app.db.repositories.tasks import TaskRepository
from app.db.session import session_scope
from app.db.tables import (
    JarvisLiveEvent, OrchestrationAction, OrchestrationDecision, TaskArtifact,
    Task, TaskEvent, TaskExecutionRun, TaskPresentation, TaskProjectionCheckpoint, TaskQuestion, TaskSupervisorLease, TaskTurn,
)
from app.services.orchestration_authority import (
    DecisionAuthority, StaleDecision, assert_current, begin_action, digest, observed_state,
)
from app.services.runtime_health import runtime_health_service
from app.services.windows_job import process_creation_stamp

logger = logging.getLogger(__name__)
CURSOR_KEY = "background_feedback_v1"
LEASE_KEY = "background-orchestration"
BATCH_SIZE = 100


def now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class DecisionClaim:
    id: str
    generation: int
    owner: str
    context: dict


class DecisionRuntime(Protocol):
    async def decide(self, claim: DecisionClaim, tools: "DecisionTools") -> dict: ...
    async def stop(self) -> None: ...


class RuntimeUnavailable(RuntimeError):
    pass


def classify_error(exc: Exception) -> tuple[str, float | None]:
    # Null due_at means availability-change/manual retry, NOT an immediate
    # polling retry. Generic/uncertain transport failures also wait for repair.
    value = str(exc).casefold()
    if any(term in value for term in ("insufficient_quota", "billing", "credit", "spend limit")):
        return "billing_exhausted", None
    if "rate_limit" in value or "429" in value:
        return "rate_limited", max(30.0, float(getattr(exc, "retry_after", 60.0)))
    if isinstance(exc, RuntimeUnavailable):
        return "runtime_unavailable", None
    if isinstance(exc, StaleDecision):
        return "stale_decision", None
    return "runtime_uncertain", None


class DecisionTools:
    """Model arguments cannot choose task/run/revision/claim identities."""

    def __init__(self, claim: DecisionClaim) -> None:
        self.claim = claim

    async def call(self, name: str, arguments: dict) -> dict:
        from app.services.orchestration_telemetry import record
        started, outcome, replay = monotonic(), "committed", False
        if name != "inspect":
            async with session_scope() as session:
                replay = (await session.execute(select(OrchestrationAction.id).where(
                    OrchestrationAction.decision_id == self.claim.id,
                    OrchestrationAction.operation_key == str(arguments.get("operation_key") or ""),
                ).limit(1))).first() is not None
        try:
            result = await self._call(name, arguments)
            outcome = "idempotent_replay" if replay else "committed"
            return result
        except StaleDecision:
            outcome = "stale_rejected"
            raise
        except ValueError:
            outcome = "validation_rejected"
            raise
        except Exception:
            outcome = "action_error"
            raise
        finally:
            if name != "inspect":
                try:
                    async with session_scope() as session:
                        await record(session, key=f"action:{uuid4()}", kind="action_attempt",
                            decision_id=self.claim.id, generation=self.claim.generation,
                            task_id=self.claim.context["task"]["id"], duration_ms=(monotonic() - started) * 1000,
                            values={"action": name if name in {"answer", "control", "continue", "delegate", "retain", "presentation"} else "undeclared",
                                    "outcome": outcome})
                except Exception:
                    logger.exception("Unable to record background action metadata")

    async def _call(self, name: str, arguments: dict) -> dict:
        from app.services.task_service import task_service
        allowed = {
            "inspect": {"after_sequence", "event_sequence", "turn_sequence", "artifact_id", "byte_offset", "instruction_id", "instructions_after", "task_goal"},
            "answer": {"answer", "reason", "operation_key"},
            "control": {"action", "importance", "reason", "operation_key"},
            "continue": {"content", "reason", "operation_key", "execution_preferences"},
            "delegate": {"goal", "reason", "operation_key", "execution_preferences"},
            "retain": {"reason", "operation_key"},
            "presentation": {"policy", "reason", "operation_key"},
        }
        if name not in allowed or set(arguments) - allowed[name]:
            raise ValueError("Undeclared orchestration capability or authority argument")
        task_id = self.claim.context["task"]["id"]
        if name == "inspect":
            from app.services.orchestration_context import inspect_evidence
            async with session_scope() as session:
                row = await session.get(OrchestrationDecision, self.claim.id)
                if (row is None or row.status != "running" or row.claim_owner != self.claim.owner
                        or row.claim_generation != self.claim.generation):
                    raise StaleDecision("Background inspection claim is stale")
                evidence = await inspect_evidence(session, task_id, arguments)
            if evidence is not None:
                return evidence
            detail = await task_service.get_task(task_id)
            if detail is None:
                raise ValueError("Task no longer exists")
            after = max(0, int(arguments.get("after_sequence", 0)))
            replay = await task_service.replay_task_events(task_id, after_sequence=after, limit=20)
            events = replay[1] if replay else []
            return {"task_id": task_id, "status": detail.task.status,
                    "events": [{"sequence": e.sequence, "type": e.event_type,
                                "payload": str(e.payload_json or {})[:1500]} for e in events]}
        operation = str(arguments.get("operation_key") or "")
        reason = str(arguments.get("reason") or "").strip()
        if not operation or len(operation) > 128 or not reason or len(reason) > 2000:
            raise ValueError("Mutation requires a bounded stable operation key and reason")
        authority = DecisionAuthority(self.claim.id, self.claim.generation, self.claim.owner, operation, reason)
        key = authority.idempotency_key
        if name == "answer":
            question_id = self.claim.context["observed"]["question_id"]
            if not question_id:
                raise ValueError("This decision has no exact worker question")
            result = await task_service.answer_question(
                task_id, question_id, answer=str(arguments.get("answer") or ""),
                idempotency_key=key, decision_authority=authority,
            )
        elif name == "control":
            action = str(arguments.get("action") or "")
            if action not in {"retry", "set_importance", "suspend", "yield"}:
                raise ValueError("Background cannot override user pause/cancel or expand permissions")
            result = await task_service.control(
                task_id, action=action, parameters={"importance": arguments["importance"]}
                if action == "set_importance" and "importance" in arguments else {},
                requester="orchestration", reason=reason, idempotency_key=key, decision_authority=authority,
            )
        elif name == "continue":
            result = await task_service.continue_task(
                task_id, content=str(arguments.get("content") or ""), origin="orchestration",
                execution_config=arguments.get("execution_preferences"),
                idempotency_key=key, decision_authority=authority,
            )
        elif name == "delegate":
            result = await task_service.dispatch(
                goal=str(arguments.get("goal") or ""), origin_surface="orchestration",
                execution_config=arguments.get("execution_preferences"),
                idempotency_key=key, decision_authority=authority,
            )
        elif name == "presentation":
            result = await task_service.set_update_policy(
                task_id, str(arguments.get("policy") or ""),
                idempotency_key=key, decision_authority=authority,
            )
        else:
            from app.services.orchestration_attention import retain_attention
            async with session_scope() as session:
                receipt = await begin_action(session, authority, action="retain",
                                             arguments={"reason": reason}, task_id=task_id)
                decision = await session.get(OrchestrationDecision, self.claim.id)
                retained = await retain_attention(session, decision, authority.idempotency_key)
                receipt.outcome_json = {**receipt.outcome_json, "task_id": task_id, "retained": retained}
                await self._audit(session, task_id, operation, name, reason)
                return dict(receipt.outcome_json)
        # The receipt already committed atomically with the shared mutation.
        # This extra explanation is an idempotent audit, not action authority.
        async with session_scope() as session:
            await self._audit(session, task_id, operation, name, reason)
        return {"task_id": result.task.id, "status": result.task.status}

    async def _audit(self, session, task_id: str, operation: str, action: str, reason: str) -> None:
        await TaskRepository(session).append_event(
            task_id=task_id, event_type="task.orchestration.action",
            payload_json={"decision_id": self.claim.id, "action": action, "reason": reason},
            causality_json={"decision_id": self.claim.id},
            idempotency_key=f"orchestration-audit:{self.claim.id}:{operation}",
        )


class TaskOrchestrationService:
    def __init__(self, runtime: DecisionRuntime | None = None,
                 owner_has_exited: Callable[[str], bool] | None = None,
                 native_host_has_exited: Callable[[dict], bool] | None = None) -> None:
        from app.services.worker_coordinator import _supervisor_owner_has_exited
        from app.services.orchestration_recovery import host_has_exited
        self.runtime = runtime
        self.owner_has_exited = owner_has_exited or _supervisor_owner_has_exited
        self.native_host_has_exited = native_host_has_exited or host_has_exited
        self.owner = f"{socket.gethostname()}:{os.getpid()}:{process_creation_stamp(os.getpid()) or 0}:{uuid4()}"
        self.generation: int | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._feedback_wake = asyncio.Event()
        self._last_batch_full = False
        self._loops: list[asyncio.Task] = []

    def wake_feedback(self) -> None:
        self._feedback_wake.set()

    async def intake(self, *, before_commit: Callable[[], Awaitable[None]] | None = None) -> int:
        """One <=100-event transaction: projection + decisions + unified cursor."""
        from app.services.task_presentations import task_presentation_service
        from app.services.orchestration_telemetry import age_ms, record
        started = monotonic()
        async with session_scope() as session:
            await session.execute(sqlite_insert(TaskProjectionCheckpoint).values(
                projection_key=CURSOR_KEY, last_sequence=0,
            ).on_conflict_do_nothing(index_elements=["projection_key"]))
            await session.execute(update(TaskProjectionCheckpoint).where(
                TaskProjectionCheckpoint.projection_key == CURSOR_KEY,
            ).values(last_sequence=TaskProjectionCheckpoint.last_sequence))
            checkpoint = await session.get(TaskProjectionCheckpoint, CURSOR_KEY)
            live = list((await session.execute(select(JarvisLiveEvent).where(
                JarvisLiveEvent.sequence > checkpoint.last_sequence,
            ).order_by(JarvisLiveEvent.sequence).limit(BATCH_SIZE))).scalars().all())
            self._last_batch_full = len(live) == BATCH_SIZE
            if not live:
                return 0
            sources = {row.source_id: row for row in live if row.source_domain == "task" and row.source_sequence}
            events = list((await session.execute(select(TaskEvent).where(
                TaskEvent.id.in_(list(sources)),
            ).order_by(TaskEvent.sequence))).scalars().all()) if sources else []
            before_ids = set((await session.execute(select(TaskPresentation.id).where(
                TaskPresentation.source_task_event_id.in_(list(sources)),
            ))).scalars()) if sources else set()
            created = await task_presentation_service._project_batch(session, events)
            await session.flush()
            candidates = (await session.execute(select(TaskPresentation).where(
                TaskPresentation.source_task_event_id.in_(list(sources)),
            ))).scalars() if sources else []
            events_by_id = {event.id: event for event in events}
            for candidate in candidates:
                if (candidate.id not in before_ids and candidate.state == "pending"
                        and events_by_id[candidate.source_task_event_id].event_type not in
                        {"task.requires_input", "task.failed", "task.interrupted"}):
                    source = events_by_id[candidate.source_task_event_id]
                    await record(session, key=f"forward:{candidate.id}", kind="factual_forwarding",
                        task_id=source.task_id, source_sequence=sources[source.id].sequence,
                        duration_ms=age_ms(source.created_at),
                        values={"presentation_id": candidate.id, "source_event_id": source.id,
                                "run_id": source.execution_run_id, "candidate_type": candidate.candidate_type,
                                "latency_clock": "source_database_wall_clock"})
            for event in events:
                if event.idempotency_key.startswith(("orchestration:", "orchestration-audit:")):
                    continue
                if event.event_type in {"task.requires_input", "task.failed", "task.interrupted",
                                       "task.turn.pending", "task.turn.admitted", "task.question.answered",
                                       "task.recovery.needed"}:
                    await self._queue_issue(session, event, sources[event.id].sequence)
                if event.event_type in {"task.completed", "task.failed", "task.interrupted"}:
                    await self._queue_repair_result(session, event, sources[event.id].sequence)
            checkpoint.last_sequence = live[-1].sequence
            checkpoint.updated_at = now()
            await record(session, key=f"feedback:{live[0].sequence}:{live[-1].sequence}", kind="feedback_batch",
                         source_sequence=live[-1].sequence, duration_ms=(monotonic() - started) * 1000,
                         values={"event_count": len(live), "task_event_count": len(events),
                                 "presentations_created": created, "first_sequence": live[0].sequence,
                                 "last_sequence": live[-1].sequence})
            if before_commit:
                await before_commit()
        self._wake.set()
        return created

    async def _queue_issue(self, session, event: TaskEvent, source_sequence: int,
                           repair_event: TaskEvent | None = None) -> None:
        task = (await session.execute(select(Task).where(Task.id == event.task_id))).scalar_one_or_none()
        if task is None or task.status in {"paused", "cancelled"} or task.pending_control_id:
            return
        if task.origin_surface == "orchestration" and task.status in {"failed", "interrupted"} and repair_event is None:
            # A failed delegated repair reports back to its originating issue;
            # it cannot recursively originate a new repair-of-repair chain.
            return
        question = (await session.execute(select(TaskQuestion).where(
            TaskQuestion.task_id == task.id, TaskQuestion.asking_run_id == task.current_run_id,
            TaskQuestion.state == "asked",
        ).order_by(TaskQuestion.asked_at.desc()).limit(1))).scalar_one_or_none()
        kind = "question" if question else "failure" if task.status in {"failed", "interrupted"} else None
        if kind is None:
            return
        state = await observed_state(session, task, question)
        issue = digest({"task": task.id, "run": task.current_run_id, "goal": task.goal_revision,
                        "user": state["user_sequence"], "question": question.id if question else None,
                        "failure": task.terminal_summary if not question else None,
                        "instructions": state["instructions_digest"], "presentation_policy": state["presentation_policy"],
                        "repair": repair_event.id if repair_event else None})
        existing = (await session.execute(select(OrchestrationDecision).where(
            OrchestrationDecision.issue_key == issue,
        ))).scalar_one_or_none()
        if existing:
            ids = list(existing.source_event_ids_json)
            if event.id not in ids and len(ids) < BATCH_SIZE:
                existing.source_event_ids_json = ids + [event.id]
            return
        context = await self._context(session, task, question, event, state, repair_event)
        session.add(OrchestrationDecision(
            id=str(uuid4()), issue_key=issue, task_id=task.id, execution_run_id=task.current_run_id,
            question_id=question.id if question else None, trigger_kind=kind,
            source_event_ids_json=list(dict.fromkeys([event.id, question.source_task_event_id] if question else [event.id])),
            source_sequence=source_sequence, observed_json=state, status="pending", due_at=now(),
            claim_generation=0, context_json=context, context_digest=digest(context),
            model=settings.task_orchestration_model, effort=settings.task_orchestration_effort,
            auth_profile=settings.task_orchestration_profile,
        ))

    async def _queue_repair_result(self, session, event: TaskEvent, source_sequence: int) -> None:
        # Relationship lives in the creation receipt, not an inferred title.
        actions = list((await session.execute(select(OrchestrationAction).where(
            OrchestrationAction.action == "dispatch",
            OrchestrationAction.outcome_json["task_id"].as_string() == event.task_id,
        ))).scalars().all())
        for action in actions:
            if (action.outcome_json or {}).get("task_id") != event.task_id:
                continue
            parent = await session.get(OrchestrationDecision, action.decision_id)
            source = (await session.execute(select(TaskEvent).where(
                TaskEvent.id.in_(parent.source_event_ids_json),
            ).order_by(TaskEvent.sequence).limit(1))).scalar_one_or_none()
            if source:
                await self._queue_issue(session, source, source_sequence, repair_event=event)

    async def _context(self, session, task, question, event, state, repair_event) -> dict:
        from app.services.orchestration_context import bound_context, instructions, prior_actions
        from app.services.task_execution_preferences import permitted_models
        from app.services.jarvis_situation import jarvis_situation_projector
        current_run = await session.get(TaskExecutionRun, task.current_run_id) if task.current_run_id else None
        turns = list((await session.execute(select(TaskTurn).where(TaskTurn.task_id == task.id)
                                           .order_by(TaskTurn.sequence.desc()).limit(6))).scalars().all())
        events = list((await session.execute(select(TaskEvent).where(TaskEvent.task_id == task.id)
                                            .order_by(TaskEvent.sequence.desc()).limit(8))).scalars().all())
        artifacts = (await session.execute(select(TaskArtifact).where(TaskArtifact.task_id == task.id)
                                           .order_by(TaskArtifact.created_at.desc(), TaskArtifact.id).limit(6))).scalars()
        return bound_context({
            "task": {"id": task.id, "goal": task.goal[:12000], "status": task.status,
                     "goal_truncated": len(task.goal) > 12000,
                     "failure": (task.terminal_summary or "")[:3000], "importance": task.importance},
            "observed": state, "trigger": {"id": event.id, "type": event.event_type},
            "execution_preferences": {
                "current_run": {key: value for key, value in (current_run.configuration_json or {}).items()
                                if key in {"model", "reasoning_effort"}} if current_run else {},
                "preferences_supported": settings.task_executor == "codex",
                "permitted_models": list(permitted_models()), "permitted_efforts": ["low", "medium", "high"],
                "scope": "New agent runs only; does not change existing run, permissions or authentication",
            },
            "question": question.question[:4000] if question else None,
            "turns": [{"sequence": row.sequence, "role": row.role, "origin": row.origin,
                       "content": (row.content or "")[:700], "truncated": len(row.content or "") > 700} for row in reversed(turns)],
            "events": [{"sequence": row.sequence, "type": row.event_type,
                        "payload": str(row.payload_json or {})[:600], "truncated": len(str(row.payload_json or {})) > 600} for row in reversed(events)],
            **await instructions(session),
            "situation": await jarvis_situation_projector.background_in_session(session),
            "prior_actions": await prior_actions(session, task.id),
            "artifacts": [{"id": row.id, "type": row.artifact_type, "title": (row.title or "")[:200]} for row in artifacts],
            "repair_result": {"event_id": repair_event.id, "task_id": repair_event.task_id, "type": repair_event.event_type,
                              "payload": str(repair_event.payload_json or {})[:3000]} if repair_event else None,
        })

    async def acquire(self) -> bool:
        """Expiry alone never proves the old host/process tree has stopped."""
        async with session_scope() as session:
            await session.execute(sqlite_insert(TaskSupervisorLease).values(
                lease_key=LEASE_KEY, generation=0, owner_identity="released",
                heartbeat_at=now(), expires_at=now(),
            ).on_conflict_do_nothing(index_elements=["lease_key"]))
            await session.execute(update(TaskSupervisorLease).where(TaskSupervisorLease.lease_key == LEASE_KEY)
                                  .values(heartbeat_at=TaskSupervisorLease.heartbeat_at))
            lease = await session.get(TaskSupervisorLease, LEASE_KEY)
            if lease.owner_identity not in {"released", self.owner} and not self.owner_has_exited(lease.owner_identity):
                return False
            if lease.owner_identity != self.owner:
                lease.generation += 1
            lease.owner_identity = self.owner
            lease.heartbeat_at = now()
            lease.expires_at = now() + timedelta(seconds=30)
            self.generation = lease.generation
            # The old supervisor is verified gone; its contained native host
            # cannot commit through the fenced generation. Reuse receipts.
            unfinished = list((await session.execute(select(OrchestrationDecision).where(
                OrchestrationDecision.status == "running",
            ))).scalars().all())
            for row in unfinished:
                receipt = (await session.execute(select(OrchestrationAction.id).where(
                    OrchestrationAction.decision_id == row.id,
                ).limit(1))).first()
                uncertain_native = bool(row.native_binding_json) and not receipt
                row.status = "deferred" if uncertain_native else "pending"
                row.claim_owner = None
                row.due_at = None if uncertain_native else now()
                row.last_error = "runtime_uncertain" if uncertain_native else "owner_restarted"
        return True

    async def recover(self, decision_id: str, **kwargs) -> dict:
        from app.services.orchestration_recovery import recover
        return await recover(self, decision_id, **kwargs)

    async def inspect_deferred(self) -> None:
        # Inspect each binding once automatically. Unknown outcomes stay held;
        # new availability does not silently authorize another paid turn.
        async with session_scope() as session:
            rows = list((await session.execute(select(OrchestrationDecision).where(
                OrchestrationDecision.status == "deferred",
                OrchestrationDecision.native_binding_json.is_not(None),
                OrchestrationDecision.outcome_json["recovery"]["binding_digest"].as_string().is_(None),
            ).order_by(OrchestrationDecision.source_sequence).limit(100))).scalars().all())
        for row in rows:
            if (row.outcome_json or {}).get("recovery", {}).get("binding_digest") != digest(row.native_binding_json or {}):
                await self.recover(row.id, automatic=True)
                return

    async def claim(self) -> DecisionClaim | None:
        if self.generation is None:
            return None
        async with session_scope() as session:
            lock = await session.execute(update(TaskSupervisorLease).where(
                TaskSupervisorLease.lease_key == LEASE_KEY, TaskSupervisorLease.owner_identity == self.owner,
                TaskSupervisorLease.generation == self.generation,
            ).values(heartbeat_at=now(), expires_at=now() + timedelta(seconds=30)))
            if lock.rowcount != 1:
                raise StaleDecision("Background service generation is stale")
            if (await session.execute(select(OrchestrationDecision.id).where(
                OrchestrationDecision.status == "running",
            ).limit(1))).first():
                return None
            row = (await session.execute(select(OrchestrationDecision).where(
                OrchestrationDecision.status.in_(["pending", "deferred"]),
                OrchestrationDecision.due_at.is_not(None), OrchestrationDecision.due_at <= now(),
            ).order_by(OrchestrationDecision.due_at, OrchestrationDecision.source_sequence).limit(1))).scalar_one_or_none()
            if row is None:
                return None
            receipts = list((await session.execute(select(OrchestrationAction).where(
                OrchestrationAction.decision_id == row.id,
            ))).scalars().all())
            if receipts:
                # A task mutation committed but the host's completion did not.
                # Complete from the atomic receipt; do not call the model again.
                row.status = "completed"
                row.outcome_json = {"reconciled_actions": [r.id for r in receipts]}
                row.completed_at = now()
                return None
            if row.native_binding_json:
                # Even a provider retry delay cannot authorize a duplicate
                # turn whose native completion has not been reconciled.
                row.status, row.due_at = "deferred", None
                return None
            task = (await session.execute(select(Task).where(Task.id == row.task_id))).scalar_one_or_none()
            # Queued context may refresh instructions; once claimed they are
            # immutable and part of every action's optimistic preconditions.
            question = await session.get(TaskQuestion, row.question_id) if row.question_id else None
            if task is not None:
                current = await observed_state(session, task, question)
                row.observed_json = {**row.observed_json, "instructions_digest": current["instructions_digest"]}
            try:
                await assert_current(session, row, task)
            except StaleDecision:
                row.status = "obsolete"
                row.last_error = "stale_decision"
                return None
            source = (await session.execute(select(TaskEvent).where(
                TaskEvent.id.in_(row.source_event_ids_json), TaskEvent.task_id == row.task_id,
            ).order_by(TaskEvent.sequence).limit(1))).scalar_one()
            repair_id = (row.context_json or {}).get("repair_result", {}).get("event_id") if (row.context_json or {}).get("repair_result") else None
            repair_event = (await session.execute(select(TaskEvent).where(TaskEvent.id == repair_id))).scalar_one_or_none() if repair_id else None
            row.context_json = await self._context(session, task, question, source, row.observed_json, repair_event)
            row.context_digest = digest(row.context_json)
            if row.outcome_json and "recovery" in row.outcome_json:
                # Its prior inspection belongs to the audited old binding,
                # not the new host that this fresh claim will start.
                row.outcome_json = {key: value for key, value in row.outcome_json.items() if key != "recovery"}
            row.status = "running"
            row.claim_owner = self.owner
            row.claim_generation += 1
            row.claimed_at = now()
            from app.services.orchestration_telemetry import age_ms, record
            await record(session, key=f"queue:{row.id}:{row.claim_generation}", kind="decision_queue",
                decision_id=row.id, task_id=row.task_id, generation=row.claim_generation,
                source_sequence=row.source_sequence, duration_ms=age_ms(row.created_at),
                values={"wait_clock": "decision_created_wall_clock", "model": row.model, "effort": row.effort})
            return DecisionClaim(row.id, row.claim_generation, self.owner, dict(row.context_json))

    async def run_one(self) -> bool:
        claim = await self.claim()
        if claim is None:
            return False
        status, error, delay, outcome = "completed", None, None, None
        started = monotonic()
        try:
            if self.runtime is None:
                raise RuntimeUnavailable("No permitted background model route is enabled")
            outcome = await self.runtime.decide(claim, DecisionTools(claim))
            async with session_scope() as session:
                action = (await session.execute(select(OrchestrationAction.id).where(
                    OrchestrationAction.decision_id == claim.id,
                ).limit(1))).first()
            if not action:
                # A text-only response is not resolution or a user answer.
                raise RuntimeUnavailable("Runtime returned without a durable action")
        except Exception as exc:
            error, delay = classify_error(exc)
            status = "obsolete" if error == "stale_decision" else "deferred"
            outcome = {"error": error, "detail": str(exc)[:1500], "native_result": outcome}
            async with session_scope() as session:
                receipts = list((await session.execute(select(OrchestrationAction.id).where(
                    OrchestrationAction.decision_id == claim.id,
                ))).scalars().all())
            if receipts:
                # Lost native completion after a durable effect is not an
                # invitation to rerun that effect or call the model again.
                status = "completed"
                outcome["reconciled_actions"] = receipts
                delay = None
            logger.warning("Decision %s stopped: %s", claim.id, error)
        async with session_scope() as session:
            result = await session.execute(update(OrchestrationDecision).where(
                OrchestrationDecision.id == claim.id, OrchestrationDecision.status == "running",
                OrchestrationDecision.claim_owner == claim.owner,
                OrchestrationDecision.claim_generation == claim.generation,
            ).values(status=status, last_error=error, outcome_json=outcome,
                     decision_reason=str((outcome or {}).get("reason") or "")[:2000] or None,
                     due_at=now() + timedelta(seconds=delay) if delay else None,
                     completed_at=now() if status in {"completed", "obsolete"} else None))
            if result.rowcount == 1:
                from app.services.orchestration_telemetry import record
                await record(session, key=f"runtime:{claim.id}:{claim.generation}", kind="runtime_invocation",
                    decision_id=claim.id, generation=claim.generation, task_id=claim.context["task"]["id"],
                    duration_ms=(monotonic() - started) * 1000,
                    values={"status": status, "error": error, "route_configured": self.runtime is not None,
                            "timing_scope": "runtime_and_tools_excluding_queue"})
            if result.rowcount == 1 and status == "obsolete":
                row = await session.get(OrchestrationDecision, claim.id)
                source = (await session.execute(select(TaskEvent).where(
                    TaskEvent.id.in_(row.source_event_ids_json), TaskEvent.task_id == row.task_id,
                ).order_by(TaskEvent.sequence).limit(1))).scalar_one_or_none()
                if source:
                    await self._queue_issue(session, source, row.source_sequence)
        return True

    async def start(self) -> None:
        if self._loops:
            return
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._feedback_wake = asyncio.Event()
        if self.runtime is None and settings.task_orchestration_live_enabled:
            from app.services.codex_orchestration_runtime import CodexOrchestrationRuntime
            self.runtime = CodexOrchestrationRuntime(permitted=True)
        self._loops = [asyncio.create_task(self._intake_loop()), asyncio.create_task(self._decision_loop())]

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._feedback_wake.set()
        if self.runtime:
            await self.runtime.stop()
        # Let short in-flight DB transactions close before disposing the
        # engine. Cancelling during aiosqlite commit can leave its connection
        # thread trying to report into an already-closed event loop.
        try:
            await asyncio.wait_for(asyncio.gather(*self._loops, return_exceptions=True), 5.0)
        except TimeoutError:
            for loop in self._loops:
                loop.cancel()
            await asyncio.gather(*self._loops, return_exceptions=True)
        self._loops.clear()
        async with session_scope() as session:
            await session.execute(update(TaskSupervisorLease).where(
                TaskSupervisorLease.lease_key == LEASE_KEY, TaskSupervisorLease.owner_identity == self.owner,
            ).values(owner_identity="released", expires_at=now()))
        self.generation = None

    async def _intake_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._feedback_wake.clear()
                await self.intake()
                await runtime_health_service.healthy("background_feedback", detail="Code-only unified event intake")
                if self.generation is not None:
                    # Liveness runs independently of a deliberately blocked
                    # model. A claim expiry remains non-authoritative.
                    async with session_scope() as session:
                        await session.execute(update(TaskSupervisorLease).where(
                            TaskSupervisorLease.lease_key == LEASE_KEY,
                            TaskSupervisorLease.owner_identity == self.owner,
                            TaskSupervisorLease.generation == self.generation,
                        ).values(heartbeat_at=now(), expires_at=now() + timedelta(seconds=30)))
                    await runtime_health_service.healthy("background_decisions", detail="Independent single-flight decision host")
                if self._last_batch_full:
                    await asyncio.sleep(0)
                    continue
            except Exception as exc:
                await runtime_health_service.failed("background_feedback", exc)
            try:
                await asyncio.wait_for(self._feedback_wake.wait(), 1.0)
            except TimeoutError:
                pass

    async def _decision_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.generation is None and not await self.acquire():
                    await asyncio.wait_for(self._stop.wait(), 1.0)
                    continue
                self._wake.clear()
                if await self.run_one():
                    continue
                await self.inspect_deferred()
                await runtime_health_service.healthy("background_decisions", detail="Waiting for meaningful task issues")
                await asyncio.wait_for(self._wake.wait(), 1.0)
            except TimeoutError:
                pass
            except Exception as exc:
                await runtime_health_service.failed("background_decisions", exc)
                try:
                    await asyncio.wait_for(self._stop.wait(), 1.0)
                except TimeoutError:
                    pass


task_orchestration_service = TaskOrchestrationService()
