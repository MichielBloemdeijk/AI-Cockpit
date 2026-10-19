"""Authoritative lifecycle for global tasks and their ordered trace."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from typing import Any
from uuid import uuid4
from time import perf_counter

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.admission_timing import timed
from app.db.repositories.tasks import TaskRepository
from app.db.session import session_scope
from app.db.tables import (
    ResourceLease,
    ResultPage,
    Task,
    TaskArtifact,
    TaskAttempt,
    TaskCaptureReport,
    TaskContextRequest,
    TaskControlCommand,
    TaskExecutionRun,
    TaskEvent,
    TaskNativeBinding,
    TaskOperation,
    TaskPresentation,
    TaskQuestion,
    TaskTarget,
    TaskSupervisorLease,
    TaskTurn,
    WorkflowRun,
    JarvisDevice,
    JarvisLiveEvent,
    LiveSession,
    WorkerInstance,
)
from app.services.task_update_policy import (
    CRITICAL_ONLY,
    SILENT,
    normalize_task_update_policy,
)
from app.services.task_workflows import resolve_workflow_config, validate_pinned_workflow, validate_workflow_result
from app.services.task_result_types import result_type
from app.services.task_capture import CaptureCollection
from app.services.orchestration_authority import DecisionAuthority, begin_action
from app.services.task_execution_preferences import normalize_agent_preferences
from app.services.task_results import allocate_document_result, fail_document_result, publish_document_result
from app.services.task_result_contract import resolve_result_request, publication_profile
from app.services.result_telemetry import observe_lifecycle, phase, completed, validated as observe_validation
from app.services.task_statuses import ACTIVE_TASK_STATUSES


TERMINAL_TASK_STATUSES = {"completed", "failed", "interrupted", "cancelled"}
RETRYABLE_TASK_STATUSES = {"failed", "interrupted", "cancelled"}
RUN_TERMINAL_STATUSES = {"completed", "failed", "interrupted", "cancelled"}
CONTROL_ACTIONS = {
    "set_importance",
    "yield",
    "suspend",
    "resume",
    "cancel",
    "restart_execution",
    "retry",
    "restart_from_beginning",
}
GENERIC_REMINDER_TEXT = "This is your reminder."


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def execution_attempt_outcome(detail, run_id: str, attempt_id: str) -> str | None:
    """Committed outcomes belong to the attempt, not a later current task run."""
    if detail is None:
        return None
    attempt = next((item for item in detail.attempts if item.id == attempt_id), None)
    run = next((item for item in detail.runs if item.id == run_id), None)
    if (attempt is None or run is None
            or attempt.execution_run_id != run_id or attempt.ended_at is None):
        return None
    if attempt.terminal_reason == "waiting_for_input" and any(
            question.asked_attempt_id == attempt_id and question.asking_run_id == run_id for question in detail.questions):
        return "waiting_for_input"
    if attempt.terminal_reason == "completed" and run.status == "completed" and run.result_reference:
        return "completed"
    if attempt.terminal_reason == "failed" and run.status == "failed":
        return "failed"
    return None


def workflow_attempt_outcome(detail, run_id: str, attempt_id: str) -> str | None:
    """Workflow stream draining uses the same committed-attempt boundary."""
    run = next((item for item in detail.runs if item.id == run_id), None) if detail else None
    return execution_attempt_outcome(detail, run_id, attempt_id) if run and run.execution_kind == "workflow" else None


def _fingerprint(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    task: Task
    queue_position: int | None


@dataclass(frozen=True, slots=True)
class TaskTurnAdmission:
    task: Task
    turn: TaskTurn
    run: TaskExecutionRun
    queue_position: int | None


@dataclass(frozen=True, slots=True)
class TaskControlSnapshot:
    task: Task
    command: TaskControlCommand
    queue_position: int | None


@dataclass(frozen=True, slots=True)
class WorkerHealth:
    status: str
    worker_id: str | None
    heartbeat_at: datetime | None


@dataclass(frozen=True, slots=True)
class TaskDetailSnapshot:
    task: Task
    queue_position: int | None
    attempts: tuple[TaskAttempt, ...]
    turns: tuple[TaskTurn, ...]
    runs: tuple[TaskExecutionRun, ...]
    controls: tuple[TaskControlCommand, ...]
    operations: tuple[TaskOperation, ...]
    events: tuple[TaskEvent, ...]
    questions: tuple[TaskQuestion, ...]
    artifacts: tuple[TaskArtifact, ...]
    targets: tuple[TaskTarget, ...]
    presentations: tuple[TaskPresentation, ...]
    worker_health: WorkerHealth


class TaskService:
    async def record_attempt_usage(self, task_id: str, *, attempt_id: str, execution_run_id: str,
                                   claim_token: str, usage: dict) -> None:
        # Audit-only update may arrive while a committed MCP terminal response drains.
        # It never reopens authority or changes a task/result/operation outcome.
        async with session_scope() as session:
            await self._lock_task(session, task_id)
            attempt = await session.get(TaskAttempt, attempt_id)
            if (attempt is None or attempt.task_id != task_id or attempt.execution_run_id != execution_run_id
                    or attempt.claim_token != claim_token):
                raise ValueError("Usage observation lacks original attempt authority")
            attempt.usage_summary_json = usage

    # Only deterministic in-process tests override this. There is no runtime
    # environment switch that can expose the unconstrained legacy executor as
    # a production workflow profile.
    workflow_fixture_enabled = False

    async def schedule_reminder(
        self,
        *,
        text: str | None,
        due_at: datetime,
        idempotency_key: str,
        origin_conversation_id: str | None = None,
        allow_duplicate: bool = False,
        timing_metadata: dict[str, Any] | None = None,
    ) -> TaskSnapshot:
        requested_text = " ".join(str(text or "").split()).strip()
        normalized = requested_text or GENERIC_REMINDER_TEXT
        due = due_at if due_at.tzinfo is not None else due_at.replace(tzinfo=timezone.utc)
        due = due.astimezone(timezone.utc)
        if due <= utc_now():
            raise ValueError("Reminder time must be in the future")
        async with session_scope() as session:
            if not allow_duplicate:
                existing_reminders = list((await session.execute(
                    select(Task)
                    .where(Task.origin_surface == "jarvis_reminder")
                    .where(Task.origin_conversation_id == origin_conversation_id)
                    .where(Task.status == "waiting_until")
                    .where(Task.resume_at == due)
                    .order_by(Task.creation_order.asc())
                )).scalars().all())
                normalized_key = normalized.casefold()
                for existing in existing_reminders:
                    delivery = dict(existing.scheduled_delivery_json or {})
                    existing_text = " ".join(str(delivery.get("text") or "").split()).strip()
                    if existing_text.casefold() == normalized_key:
                        return TaskSnapshot(task=existing, queue_position=None)
            repo = TaskRepository(session)
            task = await repo.dispatch_task(
                origin_surface="jarvis_reminder",
                goal=(f"Remind me: {normalized}" if requested_text else "Reminder"),
                idempotency_key=idempotency_key,
                origin_conversation_id=origin_conversation_id,
                importance="high",
                presentation_policy=CRITICAL_ONLY,
            )
            task = await self._lock_task(session, task.id)
            attempt = await self._ensure_attempt(session, task)
            await repo.append_event(
                task_id=task.id,
                attempt_id=attempt.id,
                event_type="task.queued",
                payload_json={"goal": task.goal, "status": "queued"},
                idempotency_key=f"task.dispatch:{idempotency_key}",
            )
            if task.status == "queued":
                task.status = "waiting_until"
                run = await self._current_run(session, task)
                if run is not None:
                    run.status = "waiting_until"
                task.waiting_until = due
                task.resume_at = due
                task.wait_reason = "reminder"
                task.scheduled_delivery_json = {
                    "kind": "reminder",
                    "text": normalized,
                    **({"timing": dict(timing_metadata)} if timing_metadata else {}),
                }
                task.scheduled_delivery_key = f"reminder:{idempotency_key}"
                await repo.append_event(
                    task_id=task.id,
                    attempt_id=attempt.id,
                    event_type="task.waiting_until",
                    payload_json={"status": "waiting_until", "resume_at": due.isoformat(), "reason": "reminder"},
                    idempotency_key=f"task.waiting:{idempotency_key}",
                )
                await self._end_current_attempt(session, task, reason="waiting_until")
            await session.flush()
            return TaskSnapshot(task=task, queue_position=None)

    async def wait_until(
        self,
        task_id: str,
        *,
        resume_at: datetime,
        reason: str,
        idempotency_key: str,
        attempt_id: str | None = None,
        execution_run_id: str | None = None,
        claim_token: str | None = None,
    ) -> TaskSnapshot:
        due = resume_at if resume_at.tzinfo is not None else resume_at.replace(tzinfo=timezone.utc)
        due = due.astimezone(timezone.utc)
        if due <= utc_now():
            raise ValueError("Task wait time must be in the future")
        normalized_reason = " ".join(reason.split()).strip() or "scheduled wait"
        async with session_scope() as session:
            repo = TaskRepository(session)
            task = await self._lock_task(session, task_id)
            await self._assert_fence(
                session, task, attempt_id=attempt_id,
                execution_run_id=execution_run_id, claim_token=claim_token,
            )
            event_key = f"task.wait-until:{task_id}:{idempotency_key}"
            if await self._event_by_key(session, event_key) is None:
                if task.status not in {"queued", "waiting_for_worker", "running"}:
                    raise ValueError(f"Task in {task.status} cannot wait until a scheduled time")
                task.status = "waiting_until"
                run = await self._current_run(session, task)
                if run is not None:
                    run.status = "waiting_until"
                task.waiting_until = due
                task.resume_at = due
                task.wait_reason = normalized_reason
                task.updated_at = utc_now()
                await repo.append_event(
                    task_id=task.id,
                    attempt_id=task.current_attempt_id,
                    event_type="task.waiting_until",
                    payload_json={
                        "status": "waiting_until",
                        "resume_at": due.isoformat(),
                        "reason": normalized_reason,
                    },
                    idempotency_key=event_key,
                )
                await self._end_current_attempt(session, task, reason="waiting_until")
            await session.flush()
            return TaskSnapshot(task=task, queue_position=None)

    @timed("task_dispatch")
    @observe_lifecycle("admission")
    async def dispatch(
        self,
        *,
        goal: str,
        idempotency_key: str,
        origin_surface: str,
        origin_conversation_id: str | None = None,
        origin_turn_id: str | None = None,
        importance: str = "normal",
        presentation_policy: str | None = None,
        execution_kind: str = "agent",
        execution_config: dict[str, Any] | None = None,
        targets: tuple[tuple[str, str, bool], ...] = (),
        capture_binding: CaptureCollection | None = None,
        result_request: dict | None = None,
        decision_authority: DecisionAuthority | None = None,
        notification_intent: str | None = None,
        notification_reason: str | None = None,
        notification_authority: dict | None = None,
    ) -> TaskSnapshot:
        normalized_goal = " ".join(goal.split()).strip()
        if not normalized_goal:
            raise ValueError("Task goal cannot be empty")
        normalized_kind = execution_kind.strip().casefold()
        if normalized_kind not in {"agent", "workflow"}:
            raise ValueError("Task execution kind must be agent or workflow")
        if normalized_kind == "workflow":
            if settings.task_executor == "codex":
                execution_config = resolve_workflow_config(execution_config)
            elif not self.workflow_fixture_enabled:
                raise ValueError("Workflow execution is not enabled until its constrained runtime is registered")
            pinned = (execution_config or {}).get("workflow") or {}
            if pinned.get("schema") == "workflow-pinned.v2" and pinned.get("required_initial_context") and capture_binding is None:
                raise ValueError("Workflow admission requires its declared initial context")
        if capture_binding is not None and normalized_kind != "workflow":
            raise ValueError("Screen context requires a workflow execution")
        if normalized_kind == "agent":
            execution_config = normalize_agent_preferences(execution_config)
        from app.services.task_notification_preferences import normalize, save
        normalized_policy, preference = normalize(presentation_policy, notification_intent, notification_reason)
        async with session_scope() as session:
            if result_request is not None:
                execution_config = dict(execution_config or {})
                execution_config["result"] = await resolve_result_request(session, result_request,
                    workflow=execution_config.get("workflow"))
            if decision_authority is not None and idempotency_key != decision_authority.idempotency_key:
                raise ValueError("Background idempotency identity is backend-owned")
            receipt = await begin_action(session, decision_authority, action="dispatch", arguments={
                "goal": normalized_goal, "kind": normalized_kind, "configuration": execution_config or {},
                "importance": importance, "policy": normalized_policy, "notification": preference, "targets": targets,
            })
            repo = TaskRepository(session)
            task = await repo.dispatch_task(
                origin_surface=origin_surface,
                goal=normalized_goal,
                idempotency_key=idempotency_key,
                origin_conversation_id=origin_conversation_id,
                origin_turn_id=origin_turn_id,
                importance=importance,
                presentation_policy=normalized_policy,
            )
            task = await self._lock_task(session, task.id)
            prior_dispatch = (await session.execute(select(TaskEvent).where(TaskEvent.idempotency_key == f"task.dispatch:{idempotency_key}"))).scalar_one_or_none()
            if prior_dispatch and (prior_dispatch.payload_json.get("notification_input") != preference
                    or prior_dispatch.payload_json.get("presentation_policy", CRITICAL_ONLY) != normalized_policy):
                raise ValueError("Admission replay conflicts with notification preference arguments")
            await save(session,task,preference,key=f"task.dispatch:{idempotency_key}:notification",authority=notification_authority)
            if receipt is not None:
                receipt.outcome_json = {**(receipt.outcome_json or {}), "task_id": task.id, "relation": "delegated_repair"}
            original_turn = (await session.execute(
                select(TaskTurn).where(
                    TaskTurn.task_id == task.id,
                    TaskTurn.request_identity == idempotency_key,
                ).limit(1)
            )).scalar_one_or_none()
            if original_turn is not None and original_turn.execution_run_id != task.current_run_id:
                expected = _fingerprint({
                    "content": normalized_goal,
                    "execution_kind": normalized_kind,
                    "configuration": execution_config or {},
                })
                if original_turn.input_fingerprint != expected:
                    raise ValueError("Idempotency key was already used with different task input")
                return TaskSnapshot(task=task, queue_position=await self._queue_position(session, task))
            if task.goal != normalized_goal or (
                task.current_run_id is not None and task.execution_kind != normalized_kind
            ):
                raise ValueError("Idempotency key was already used with different task input")
            task.execution_kind = normalized_kind
            await self._ensure_targets(session, task.id, targets)
            run_was_unbound = task.current_run_id is None
            run = await self._ensure_initial_run(
                session,
                task,
                request_identity=idempotency_key,
                execution_config=execution_config,
            )
            attempt = await self._ensure_attempt(session, task, execution_run=run)
            existing_requests = list((await session.execute(select(TaskContextRequest).where(
                TaskContextRequest.execution_run_id == run.id))).scalars().all())
            if capture_binding is not None:
                if existing_requests:
                    if (run.configuration_json or {}).get("screen_context") != capture_binding.manifest():
                        raise ValueError("Idempotency key was already bound to another capture collection")
                else:
                    if not run_was_unbound:
                        raise ValueError("Existing task cannot gain required context after admission")
                    await self._bind_required_capture(session, task, run, attempt, capture_binding, normalized_goal)
            elif existing_requests:
                raise ValueError("Idempotency key was already bound to required context")
            await repo.append_event(
                task_id=task.id,
                attempt_id=attempt.id,
                event_type="task.queued",
                payload_json={"goal": task.goal, "status": task.status,"notification_input":preference,"presentation_policy":normalized_policy},
                idempotency_key=f"task.dispatch:{idempotency_key}",
            )
            await self._assign_worker_eligibility(
                session,
                task,
                repo=repo,
                idempotency_key=f"task.waiting-for-worker:{task.id}",
            )
            await session.refresh(task)
            return TaskSnapshot(task=task, queue_position=await self._queue_position(session, task))

    @timed("capture_binding_and_result_allocation")
    async def _bind_required_capture(self, session, task, run, attempt, collection, normalized_goal):
        repo = TaskRepository(session)
        roles = [role for role, _ in collection.captures]
        if not roles or len(roles) != len(set(roles)) or set(roles) - {"invocation", "latest"}:
            raise ValueError("Capture collection roles are invalid")
        if (len(collection.missing_roles) != len(set(collection.missing_roles))
                or set(collection.missing_roles) - {"invocation", "latest"}
                or set(roles) & set(collection.missing_roles)
                or set(roles) | set(collection.missing_roles) != {"invocation", "latest"}):
            raise ValueError("Capture collection missing roles are invalid")
        identities = {(item.session_id, item.generation, item.activation_id, item.device_id)
            for _, item in collection.captures}
        if len(identities) != 1 or any(item.capture_mode != {"invocation": "activation", "latest": "fresh"}[role]
                for role, item in collection.captures):
            raise ValueError("Capture collection provenance is inconsistent")
        for _, capture_binding in collection.captures:
            capture = await session.get(TaskCaptureReport, capture_binding.capture_id)
            device = await session.get(JarvisDevice, capture_binding.device_id)
            live = await session.get(LiveSession, capture_binding.session_id)
            if (capture is None or capture.status != "captured"
                    or capture.revision < capture_binding.capture_revision
                    or capture.device_id != capture_binding.device_id
                    or capture.activation_id != capture_binding.activation_id
                    or capture.capture_mode != capture_binding.capture_mode
                    or capture.session_id not in {None, capture_binding.session_id}
                    or capture.generation not in {None, capture_binding.generation}
                    or capture.captured_at is None
                    or capture.captured_at.replace(tzinfo=timezone.utc) != capture_binding.captured_at
                    or capture.expires_at.replace(tzinfo=timezone.utc) != capture_binding.expires_at
                    or capture.expires_at.replace(tzinfo=timezone.utc) <= utc_now()):
                raise ValueError("Capture changed before workflow admission")
            if (device is None or device.revoked_at is not None or device.lock_state == "locked"
                    or live is None or live.controller_id != capture_binding.device_id
                    or live.activation_id != capture_binding.activation_id
                    or live.generation != capture_binding.generation
                    or live.lifecycle_state not in {"connecting", "ready"}):
                raise ValueError("Capture session or device changed before workflow admission")
        pinned = validate_pinned_workflow(dict(run.configuration_json or {}))
        if "task.context.images@2" not in pinned["capabilities"]:
            raise ValueError("Workflow is not authorized to consume screen context")
        workflow_run = WorkflowRun(
            task_id=task.id, execution_run_id=run.id,
            workflow_id=pinned["id"], workflow_version=pinned["version"],
            workflow_digest=pinned["digest"],
            normalized_inputs_json={"goal": normalized_goal}, stage="context_pending",
        )
        session.add(workflow_run)
        await session.flush()
        if (result_type(pinned["output_schema"]).app_slug is not None
                and (run.configuration_json or {}).get("result", {}).get("mode") != "answer"):
            page = await allocate_document_result(session, task_id=task.id, run_id=run.id,
                                                  schema=pinned["output_schema"])
            await repo.append_event(
                task_id=task.id, attempt_id=attempt.id if attempt else None, execution_run_id=run.id,
                event_type="task.result.allocated",
                payload_json={"result_url": f"/results/{page.public_id}"},
                idempotency_key=f"task.result.allocated:{run.id}",
            )
        run.configuration_json = {**dict(run.configuration_json or {}), "screen_context": collection.manifest()}
        unique = {item.capture_id: item for _, item in collection.captures}
        for capture_binding in unique.values():
            request = TaskContextRequest(
                task_id=task.id, execution_run_id=run.id,
                workflow_run_id=workflow_run.id, capture_id=capture_binding.capture_id,
                capture_revision=capture_binding.capture_revision,
                device_id=capture_binding.device_id,
                session_id=capture_binding.session_id,
                generation=capture_binding.generation,
                activation_id=capture_binding.activation_id,
                capture_mode=capture_binding.capture_mode,
                state="pending", expires_at=capture_binding.expires_at,
            )
            session.add(request)
            await session.flush()
            if task.current_run_id == run.id:
                task.status = "waiting_for_input"
                task.status_reason = "context_upload_pending"
                run.status = "waiting_for_input"
            session.add(JarvisLiveEvent(
                event_type="context.requested", source_domain="task",
                source_id=task.id, target_device_id=capture_binding.device_id,
                payload_json={"request_id": request.id, "task_id": task.id,
                              "execution_run_id": run.id,
                              "capture_id": capture_binding.capture_id,
                              "activation_id": capture_binding.activation_id,
                              "generation": capture_binding.generation},
                idempotency_key=f"context.requested:{request.id}",
            ))

    @timed("task_continuation")
    @observe_lifecycle("continuation")
    async def continue_task(
        self,
        task_id: str,
        *,
        content: str,
        idempotency_key: str,
        origin: str,
        execution_kind: str | None = None,
        execution_config: dict[str, Any] | None = None,
        result_request: dict | None = None,
        capture_binding: CaptureCollection | None = None,
        decision_authority: DecisionAuthority | None = None,
    ) -> TaskTurnAdmission:
        normalized = " ".join(content.split()).strip()
        if not normalized:
            raise ValueError("Task turn content cannot be empty")
        async with session_scope() as session:
            task = await self._lock_task(session, task_id)
            if decision_authority is not None and idempotency_key != decision_authority.idempotency_key:
                raise ValueError("Background idempotency identity is backend-owned")
            receipt = await begin_action(session, decision_authority, action="continue", task_id=task_id, arguments={
                "content": normalized, "kind": execution_kind, "configuration": execution_config or {},
                **({"result": result_request} if result_request is not None else {}),
            })
            existing = (await session.execute(select(TaskTurn).where(
                TaskTurn.task_id == task_id, TaskTurn.request_identity == idempotency_key,
            ).limit(1))).scalar_one_or_none()
            previous = await session.get(TaskExecutionRun, existing.execution_run_id) if existing else await self._current_run(session, task)
            kind = (execution_kind or (previous.execution_kind if existing and previous else task.execution_kind)).strip().casefold()
            if kind not in {"agent", "workflow"}:
                raise ValueError("Task execution kind must be agent or workflow")
            if kind == "agent":
                inherited = dict(previous.configuration_json or {}) if previous and previous.execution_kind == "agent" else {}
                inherited.pop("result", None)
                inherited.pop("resolved_tools", None)
                execution_config = normalize_agent_preferences(execution_config, inherited=inherited)
            if kind == "workflow":
                if settings.task_executor == "codex":
                    if execution_config:
                        execution_config = resolve_workflow_config({"definition": previous.configuration_json["definition"], **execution_config}, inherited=previous.configuration_json)
                    else:
                        previous_config = dict(previous.configuration_json or {}) if previous else {}
                        pinned = validate_pinned_workflow(previous_config)
                        identity = f"{pinned['id']}@{pinned['version']}:{pinned['digest']}"
                        if identity not in {item.strip() for item in settings.task_workflow_allowlist.split(",")}:
                            raise ValueError("Workflow version and digest are not enabled")
                        execution_config = previous_config
                        execution_config.pop("resolved_tools", None)
                elif not self.workflow_fixture_enabled:
                    raise ValueError("Workflow execution is not enabled until its constrained runtime is registered")
            execution_config = dict(execution_config or {})
            execution_config.pop("result", None)  # a new run needs fresh explicit intent
            if result_request is not None:
                if existing:
                    # Replay compares the original contract, not today's head.
                    original = (previous.configuration_json or {}).get("result")
                    from app.services.task_result_contract import ResultRequest
                    requested = ResultRequest.model_validate(result_request).model_dump(exclude_none=True)
                    if original is None or requested != {key: value for key, value in original.items()
                        if key in requested} or ("kind" in requested and requested["kind"] != original.get("kind")):
                        raise ValueError("Idempotency key was already used with different result input")
                    execution_config["result"] = original
                else:
                    execution_config["result"] = await resolve_result_request(session, result_request,
                        task_id=task_id, workflow=execution_config.get("workflow"))
            elif existing and (previous.configuration_json or {}).get("result"):
                raise ValueError("Idempotency replay omitted the original result request")
            elif kind == "workflow" and execution_config.get("workflow") and result_type(execution_config["workflow"]["output_schema"]).app_slug:
                pages = (await session.execute(select(ResultPage).where(ResultPage.task_id == task_id).limit(2))).scalars().all()
                if len(pages) > 1:
                    raise ValueError("Task has multiple results; select an explicit result intent")
                if pages and pages[0].result_kind != execution_config["workflow"]["output_schema"]:
                    raise ValueError("Follow-up result type differs from the existing result")
            fingerprint = _fingerprint(
                {"content": normalized, "execution_kind": kind, "configuration": execution_config or {},
                 **({"screen_context": capture_binding.manifest()} if capture_binding else {})}
            )
            if existing is not None:
                if existing.input_fingerprint != fingerprint:
                    raise ValueError("Idempotency key was already used with different turn input")
                run = await session.get(TaskExecutionRun, existing.execution_run_id)
                if run is None:
                    raise RuntimeError("Task turn has no execution run")
                return TaskTurnAdmission(
                    task=task,
                    turn=existing,
                    run=run,
                    queue_position=await self._queue_position(session, task),
                )

            next_sequence = int((await session.execute(
                select(func.max(TaskTurn.sequence)).where(TaskTurn.task_id == task_id)
            )).scalar_one_or_none() or 0) + 1
            next_ordinal = int((await session.execute(
                select(func.max(TaskExecutionRun.ordinal)).where(TaskExecutionRun.task_id == task_id)
            )).scalar_one_or_none() or 0) + 1
            turn = TaskTurn(
                id=str(uuid4()),
                task_id=task_id,
                sequence=next_sequence,
                role="user",
                content=normalized,
                origin=origin,
                request_identity=idempotency_key,
                input_fingerprint=fingerprint,
            )
            session.add(turn)
            await session.flush()
            activate = task.status in TERMINAL_TASK_STATUSES
            run = TaskExecutionRun(
                id=str(uuid4()),
                task_id=task_id,
                ordinal=next_ordinal,
                input_turn_id=turn.id,
                status="queued" if activate else "pending",
                execution_kind=kind,
                configuration_json=execution_config or {},
                workspace_generation=task.workspace_generation,
                queue_entered_at=utc_now(),
            )
            session.add(run)
            await session.flush()
            turn.execution_run_id = run.id
            if not activate and execution_config.get("result", {}).get("mode") == "new":
                page = await allocate_document_result(session, task_id=task.id, run_id=run.id,
                    schema=execution_config["result"]["kind"])
                await TaskRepository(session).append_event(task_id=task.id, execution_run_id=run.id,
                    event_type="task.result.allocated", payload_json={"result_url": f"/results/{page.public_id}"},
                    idempotency_key=f"task.result.allocated:{run.id}")
            if activate:
                now = utc_now()
                task.current_run_id = run.id
                task.execution_kind = kind
                task.goal = normalized
                task.goal_revision += 1
                task.status = "queued"
                task.status_reason = None
                task.terminal_summary = None
                task.finished_at = None
                task.cancellation_requested_at = None
                task.queue_entered_at = now
                task.updated_at = now
                attempt = await self._create_attempt(session, task, execution_run=run)
                await TaskRepository(session).append_event(
                    task_id=task.id,
                    attempt_id=attempt.id,
                    event_type="task.turn.admitted",
                    payload_json={
                        "turn_id": turn.id,
                        "run_id": run.id,
                        "ordinal": run.ordinal,
                        "status": "queued",
                    },
                    idempotency_key=f"task.turn:{task.id}:{idempotency_key}",
                )
                if capture_binding:
                    await self._bind_required_capture(session, task, run, attempt, capture_binding, normalized)
                await self._assign_worker_eligibility(
                    session,
                    task,
                    repo=TaskRepository(session),
                    idempotency_key=f"task.turn-waiting:{run.id}",
                )
            else:
                if capture_binding:
                    await self._bind_required_capture(session, task, run, None, capture_binding, normalized)
                await TaskRepository(session).append_event(
                    task_id=task.id,
                    attempt_id=task.current_attempt_id,
                    event_type="task.turn.pending",
                    payload_json={"turn_id": turn.id, "run_id": run.id, "ordinal": run.ordinal},
                    idempotency_key=f"task.turn:{task.id}:{idempotency_key}",
                )
                if receipt is not None:
                    from app.db.tables import OrchestrationDecision
                    from app.services.orchestration_attention import retain_attention
                    decision = await session.get(OrchestrationDecision, decision_authority.decision_id)
                    retained = await retain_attention(session, decision, idempotency_key)
                    receipt.outcome_json = {**receipt.outcome_json, "retained": retained}
            if receipt is not None:
                receipt.outcome_json = {**receipt.outcome_json, "run_id": run.id,
                                        "execution_preferences": run.configuration_json}
            await session.flush()
            return TaskTurnAdmission(
                task=task,
                turn=turn,
                run=run,
                queue_position=await self._queue_position(session, task),
            )

    async def control(
        self,
        task_id: str,
        *,
        action: str,
        idempotency_key: str,
        parameters: dict[str, Any] | None = None,
        requester: str = "user",
        reason: str | None = None,
        provider_call_id: str | None = None,
        expected_run_id: str | None = None,
        expected_control_revision: int | None = None,
        decision_authority: DecisionAuthority | None = None,
    ) -> TaskControlSnapshot:
        normalized_action = action.strip().casefold()
        if normalized_action not in CONTROL_ACTIONS:
            raise ValueError(f"Unsupported task control action: {action}")
        values = dict(parameters or {})
        async with session_scope() as session:
            task = await self._lock_task(session, task_id)
            if decision_authority is not None and idempotency_key != decision_authority.idempotency_key:
                raise ValueError("Background idempotency identity is backend-owned")
            await begin_action(session, decision_authority, action="control", task_id=task_id, arguments={
                "action": normalized_action, "parameters": values, "reason": reason,
            })
            existing = (await session.execute(
                select(TaskControlCommand).where(
                    TaskControlCommand.task_id == task_id,
                    TaskControlCommand.idempotency_key == idempotency_key,
                ).limit(1)
            )).scalar_one_or_none()
            if existing is not None:
                if existing.action != normalized_action or (existing.parameters_json or {}) != values:
                    raise ValueError("Idempotency key was already used with a different control command")
                return TaskControlSnapshot(
                    task=task,
                    command=existing,
                    queue_position=await self._queue_position(session, task),
                )
            if expected_run_id is not None and expected_run_id != task.current_run_id:
                raise ValueError("Task control targeted a stale execution run")
            if expected_control_revision is not None and expected_control_revision != task.control_revision:
                raise ValueError("Task control revision is stale")
            command = TaskControlCommand(
                id=str(uuid4()),
                task_id=task.id,
                execution_run_id=task.current_run_id,
                attempt_id=task.current_attempt_id,
                action=normalized_action,
                parameters_json=values,
                requester=requester,
                reason=reason,
                provider_call_id=provider_call_id,
                idempotency_key=idempotency_key,
            )
            session.add(command)
            task.control_revision += 1
            now = utc_now()
            run = await self._current_run(session, task)

            if normalized_action == "set_importance":
                importance = str(values.get("importance") or "").casefold()
                if importance not in {"low", "normal", "high"}:
                    raise ValueError("Task importance must be low, normal, or high")
                task.importance = importance
                if task.status in {"queued", "waiting_for_worker"}:
                    task.queue_entered_at = now
                    if run is not None:
                        run.queue_entered_at = now
                command.state = "confirmed"
                command.confirmed_at = now
            elif normalized_action == "resume":
                if task.status != "paused" or run is None:
                    raise ValueError(f"Task in {task.status} cannot be resumed")
                previous = await self._current_attempt(session, task)
                attempt = await self._create_attempt(
                    session,
                    task,
                    execution_run=run,
                    parent_attempt=previous,
                    resume_reason="resume",
                    checkpoint=dict(previous.checkpoint_json or {}) if previous is not None else None,
                )
                task.status = "queued"
                task.queue_entered_at = now
                task.finished_at = None
                run.status = "queued"
                run.queue_entered_at = now
                run.finished_at = None
                command.attempt_id = attempt.id
                command.state = "confirmed"
                command.confirmed_at = now
            elif normalized_action in {"retry", "restart_execution", "restart_from_beginning"} and task.status != "running":
                if normalized_action == "retry" and task.status not in RETRYABLE_TASK_STATUSES:
                    raise ValueError(f"Task in {task.status} cannot be retried")
                if run is None:
                    raise ValueError("Task has no current execution run")
                previous = await self._current_attempt(session, task)
                if normalized_action == "restart_from_beginning":
                    task.workspace_generation += 1
                    run.workspace_generation = task.workspace_generation
                    checkpoint = None
                else:
                    checkpoint = dict(previous.checkpoint_json or {}) if previous is not None else None
                attempt = await self._create_attempt(
                    session,
                    task,
                    execution_run=run,
                    parent_attempt=previous,
                    resume_reason=normalized_action,
                    checkpoint=checkpoint,
                )
                if (
                    normalized_action == "retry"
                    and previous is not None
                    and previous.terminal_reason == "resource_changed"
                    and bool(values.get("accept_resource_changes"))
                ):
                    if not reason:
                        raise ValueError("A reason is required to accept changed shared resources")
                    attempt.recovery_metadata_json = {"accept_resource_changes": True}
                task.status = "queued"
                task.queue_entered_at = now
                task.terminal_summary = None
                task.finished_at = None
                task.cancellation_requested_at = None
                run.status = "queued"
                run.queue_entered_at = now
                run.finished_at = None
                command.attempt_id = attempt.id
                command.state = "confirmed"
                command.confirmed_at = now
            elif normalized_action == "yield" and task.status in {"queued", "waiting_for_worker"}:
                task.queue_entered_at = now
                if run is not None:
                    run.queue_entered_at = now
                command.state = "confirmed"
                command.confirmed_at = now
            elif normalized_action == "suspend" and task.status in {
                "queued", "waiting_for_worker", "waiting_for_input", "waiting_until"
            }:
                task.status = "paused"
                task.status_reason = reason or "Task suspended."
                if run is not None:
                    run.status = "paused"
                command.state = "confirmed"
                command.confirmed_at = now
                await self._end_current_attempt(session, task, reason="suspended")
            elif normalized_action == "cancel" and task.status != "running":
                if task.status in TERMINAL_TASK_STATUSES:
                    raise ValueError(f"Task in {task.status} cannot be cancelled")
                task.status = "cancelled"
                task.cancellation_requested_at = now
                task.finished_at = now
                task.terminal_summary = "Task cancelled by user."
                if run is not None:
                    run.status = "cancelled"
                    run.finished_at = now
                    run.terminal_summary = task.terminal_summary
                    await fail_document_result(session, task_id=task.id, run_id=run.id,
                        reason=task.terminal_summary, cancelled=True)
                command.state = "confirmed"
                command.confirmed_at = now
                await self._end_current_attempt(session, task, reason="cancelled")
            elif task.status == "running" and normalized_action in {
                "yield", "suspend", "cancel", "restart_execution", "restart_from_beginning"
            }:
                immediate = bool(values.get("immediate", normalized_action in {
                    "cancel", "restart_execution", "restart_from_beginning"
                }))
                command.state = "stop_requested" if immediate else "requested"
                task.pending_control_id = command.id
                task.status_reason = f"{normalized_action} requested"
                if normalized_action == "cancel":
                    task.cancellation_requested_at = now
                if immediate:
                    attempt = await self._current_attempt(session, task)
                    if attempt is not None:
                        attempt.claim_token = str(uuid4())
            else:
                raise ValueError(f"Task in {task.status} cannot apply {normalized_action}")

            task.updated_at = now
            await TaskRepository(session).append_event(
                task_id=task.id,
                attempt_id=task.current_attempt_id,
                event_type="task.control.requested",
                payload_json={
                    "command_id": command.id,
                    "action": command.action,
                    "state": command.state,
                    "requester": requester,
                    "control_revision": task.control_revision,
                },
                idempotency_key=f"task.control:{task.id}:{idempotency_key}",
            )
            if normalized_action == "cancel" and task.status == "cancelled" and run is not None:
                await TaskRepository(session).append_event(
                    task_id=task.id,
                    attempt_id=task.current_attempt_id,
                    execution_run_id=run.id,
                    event_type="task.cancelled",
                    payload_json={"status": "cancelled", "summary": task.terminal_summary},
                    idempotency_key=f"task.cancelled:{task.id}:{run.id}",
                )
            await self._assign_worker_eligibility(
                session,
                task,
                repo=TaskRepository(session),
                idempotency_key=f"task.control-waiting:{command.id}",
            )
            await session.flush()
            return TaskControlSnapshot(
                task=task,
                command=command,
                queue_position=await self._queue_position(session, task),
            )

    async def pending_control(self, task_id: str, *, attempt_id: str) -> TaskControlCommand | None:
        async with session_scope() as session:
            task = await TaskRepository(session).get_task(task_id)
            if task is None or task.pending_control_id is None or task.current_attempt_id != attempt_id:
                return None
            return await session.get(TaskControlCommand, task.pending_control_id)

    async def acknowledge_control_boundary(
        self,
        task_id: str,
        *,
        command_id: str,
        attempt_id: str,
    ) -> None:
        """Escalate a cooperative stop at a safe runtime boundary to supervisor stop."""
        async with session_scope() as session:
            task = await self._lock_task(session, task_id)
            command = await session.get(TaskControlCommand, command_id)
            if (
                command is None
                or task.pending_control_id != command.id
                or command.attempt_id != attempt_id
            ):
                raise ValueError("Task control no longer targets this attempt")
            if command.state == "stop_requested":
                return
            if command.state != "requested":
                raise ValueError("Task control is not awaiting a runtime boundary")
            attempt = await session.get(TaskAttempt, attempt_id)
            if attempt is None or attempt.ended_at is not None:
                raise ValueError("Task attempt is not active")
            command.state = "stop_requested"
            attempt.claim_token = str(uuid4())
            await TaskRepository(session).append_event(
                task_id=task_id,
                attempt_id=attempt_id,
                execution_run_id=attempt.execution_run_id,
                event_type="task.control.acknowledged",
                payload_json={"command_id": command.id, "state": command.state},
                idempotency_key=f"task.control.acknowledged:{command.id}",
            )

    async def confirm_control(
        self,
        task_id: str,
        *,
        command_id: str,
        stopped_attempt_id: str,
        stop_evidence: dict[str, Any],
    ) -> TaskControlSnapshot:
        async with session_scope() as session:
            task = await self._lock_task(session, task_id)
            command = await session.get(TaskControlCommand, command_id)
            if command is None or command.task_id != task_id:
                raise ValueError("Task control command not found")
            if command.state == "confirmed":
                return TaskControlSnapshot(task, command, await self._queue_position(session, task))
            if task.pending_control_id != command.id or command.attempt_id != stopped_attempt_id:
                raise ValueError("Task control no longer targets the active attempt")
            attempt = await session.get(TaskAttempt, stopped_attempt_id)
            if attempt is None:
                raise ValueError("Task attempt not found")
            now = utc_now()
            attempt.ended_at = now
            attempt.terminal_reason = f"control:{command.action}"
            attempt.recovery_metadata_json = {
                **dict(attempt.recovery_metadata_json or {}),
                "stop_evidence": dict(stop_evidence),
            }
            await session.execute(
                delete(ResourceLease).where(ResourceLease.owner_attempt_id == stopped_attempt_id)
            )
            run = await self._current_run(session, task)
            if run is None:
                raise ValueError("Task has no current execution run")
            checkpoint = dict(attempt.checkpoint_json or {})
            if command.action == "yield":
                next_attempt = await self._create_attempt(
                    session, task, execution_run=run, parent_attempt=attempt,
                    resume_reason="yield", checkpoint=checkpoint,
                )
                task.status = "queued"
                task.queue_entered_at = now
                run.status = "queued"
                run.queue_entered_at = now
                command.detail = f"requeued as attempt {next_attempt.attempt_number}"
            elif command.action == "suspend":
                task.status = "paused"
                run.status = "paused"
                command.detail = "writers stopped; task paused"
            elif command.action == "cancel":
                task.status = "cancelled"
                task.finished_at = now
                task.terminal_summary = "Task cancelled by user."
                run.status = "cancelled"
                run.finished_at = now
                run.terminal_summary = task.terminal_summary
                await fail_document_result(session, task_id=task.id, run_id=run.id,
                    reason=task.terminal_summary, cancelled=True)
            elif command.action in {"restart_execution", "restart_from_beginning"}:
                if command.action == "restart_from_beginning":
                    task.workspace_generation += 1
                    run.workspace_generation = task.workspace_generation
                    checkpoint = {}
                next_attempt = await self._create_attempt(
                    session, task, execution_run=run, parent_attempt=attempt,
                    resume_reason=command.action, checkpoint=checkpoint,
                )
                task.status = "queued"
                task.queue_entered_at = now
                run.status = "queued"
                run.queue_entered_at = now
                command.detail = f"restarted as attempt {next_attempt.attempt_number}"
            else:
                raise ValueError(f"Control {command.action} does not require stop confirmation")
            task.pending_control_id = None
            task.status_reason = None
            command.state = "confirmed"
            command.confirmed_at = now
            await TaskRepository(session).append_event(
                task_id=task.id,
                attempt_id=stopped_attempt_id,
                event_type="task.control.confirmed",
                payload_json={
                    "command_id": command.id,
                    "action": command.action,
                    "state": "confirmed",
                    "stop_evidence": dict(stop_evidence),
                },
                idempotency_key=f"task.control.confirmed:{command.id}",
            )
            if command.action == "cancel":
                await TaskRepository(session).append_event(
                    task_id=task.id,
                    attempt_id=stopped_attempt_id,
                    execution_run_id=run.id,
                    event_type="task.cancelled",
                    payload_json={"status": "cancelled", "summary": task.terminal_summary},
                    idempotency_key=f"task.cancelled:{task.id}:{run.id}",
                )
            await session.flush()
            return TaskControlSnapshot(task, command, await self._queue_position(session, task))

    async def set_update_policy(self, task_id: str, policy: str | None, *, idempotency_key: str | None = None,
                                decision_authority: DecisionAuthority | None = None,
                                notification_intent: str | None = None, notification_reason: str | None = None,
                                notification_authority: dict | None = None) -> TaskSnapshot:
        from app.services.task_notification_preferences import normalize, save
        normalized_policy, preference = normalize(policy,notification_intent,notification_reason)
        async with session_scope() as session:
            if decision_authority and idempotency_key != decision_authority.idempotency_key:
                raise ValueError("Decision action key must be backend-derived")
            receipt = await begin_action(session, decision_authority, action="set_update_policy",
                                         arguments={"policy": normalized_policy,"notification":preference}, task_id=task_id)
            task = await self._lock_task(session, task_id)
            event_key = idempotency_key or f"task.update-policy:{task.id}:{normalized_policy}:{uuid4()}"
            existing = (await session.execute(select(TaskEvent).where(TaskEvent.idempotency_key == event_key))).scalar_one_or_none()
            if existing:
                if existing.task_id != task_id or existing.payload_json.get("presentation_policy") != normalized_policy or existing.payload_json.get("notification_input") != preference:
                    raise ValueError("Update-policy operation key conflicts with changed arguments")
                await save(session,task,preference,key=event_key+":notification",authority=notification_authority)
                return TaskSnapshot(task=task, queue_position=await self._queue_position(session, task))
            supplied_preference = preference
            if preference is None:
                from app.services.task_notification_preferences import current
                old = await current(session,task)
                if normalized_policy == "silent" and old["intent"] != "suppressed":
                    preference = {"intent":"suppressed","reason":"Task update policy set to silent"}
                elif normalized_policy != "silent" and old["intent"] == "suppressed":
                    preference = {"intent":"discretionary","reason":"Task update policy no longer silent"}
            task.presentation_policy = normalized_policy
            task.updated_at = utc_now()
            await save(session,task,preference,key=event_key+":notification",authority=notification_authority)
            await TaskRepository(session).append_event(
                task_id=task.id,
                attempt_id=task.current_attempt_id,
                event_type="task.update_policy.changed",
                payload_json={"presentation_policy": normalized_policy,"notification_input":supplied_preference},
                idempotency_key=event_key,
            )
            if receipt:
                from app.db.tables import OrchestrationDecision
                from app.services.orchestration_attention import retain_attention
                decision = await session.get(OrchestrationDecision, decision_authority.decision_id)
                retained = await retain_attention(session, decision, event_key)
                receipt.outcome_json = {**receipt.outcome_json, "presentation_policy": normalized_policy, "retained": retained}
            await session.flush()
            return TaskSnapshot(task=task, queue_position=await self._queue_position(session, task))

    async def list_tasks(self, *, statuses: set[str] | None = None) -> list[TaskSnapshot]:
        async with session_scope() as session:
            tasks = list(await TaskRepository(session).list_tasks(statuses=statuses))
            return [
                TaskSnapshot(task=task, queue_position=await self._queue_position(session, task))
                for task in tasks
            ]

    async def get_task(self, task_id: str) -> TaskDetailSnapshot | None:
        async with session_scope() as session:
            repo = TaskRepository(session)
            task = await repo.get_task(task_id)
            if task is None:
                return None
            attempts = tuple((await session.execute(
                select(TaskAttempt)
                .where(TaskAttempt.task_id == task_id)
                .order_by(TaskAttempt.attempt_number.asc())
            )).scalars().all())
            turns = tuple((await session.execute(
                select(TaskTurn)
                .where(TaskTurn.task_id == task_id)
                .order_by(TaskTurn.sequence.asc())
            )).scalars().all())
            runs = tuple((await session.execute(
                select(TaskExecutionRun)
                .where(TaskExecutionRun.task_id == task_id)
                .order_by(TaskExecutionRun.ordinal.asc())
            )).scalars().all())
            controls = tuple((await session.execute(
                select(TaskControlCommand)
                .where(TaskControlCommand.task_id == task_id)
                .order_by(TaskControlCommand.requested_at.asc(), TaskControlCommand.id.asc())
            )).scalars().all())
            operations = tuple((await session.execute(
                select(TaskOperation)
                .where(TaskOperation.task_id == task_id)
                .order_by(TaskOperation.started_at.asc(), TaskOperation.id.asc())
            )).scalars().all())
            events = tuple((await session.execute(
                select(TaskEvent)
                .where(TaskEvent.task_id == task_id)
                .order_by(TaskEvent.sequence.asc())
            )).scalars().all())
            questions = tuple((await session.execute(
                select(TaskQuestion)
                .where(TaskQuestion.task_id == task_id)
                .order_by(TaskQuestion.asked_at.asc(), TaskQuestion.id.asc())
            )).scalars().all())
            artifacts = tuple((await session.execute(
                select(TaskArtifact)
                .where(TaskArtifact.task_id == task_id)
                .order_by(TaskArtifact.created_at.asc(), TaskArtifact.id.asc())
            )).scalars().all())
            targets = tuple((await session.execute(
                select(TaskTarget)
                .where(TaskTarget.task_id == task_id)
                .order_by(TaskTarget.resource_type.asc(), TaskTarget.resource_key.asc())
            )).scalars().all())
            presentations = tuple((await session.execute(
                select(TaskPresentation)
                .outerjoin(
                    TaskEvent,
                    TaskPresentation.source_task_event_id == TaskEvent.id,
                )
                .where(TaskPresentation.task_id == task_id)
                .order_by(
                    TaskEvent.sequence.asc(),
                    TaskPresentation.created_at.asc(),
                    TaskPresentation.id.asc(),
                )
            )).scalars().all())
            return TaskDetailSnapshot(
                task=task,
                queue_position=await self._queue_position(session, task),
                attempts=attempts,
                turns=turns,
                runs=runs,
                controls=controls,
                operations=operations,
                events=events,
                questions=questions,
                artifacts=artifacts,
                targets=targets,
                presentations=presentations,
                worker_health=await self._worker_health(session),
            )

    async def replay_events(self, *, after_sequence: int = 0, limit: int = 250) -> tuple[int, list[TaskEvent]]:
        async with session_scope() as session:
            repo = TaskRepository(session)
            events = list(await repo.list_events_after(after_sequence=after_sequence, limit=limit))
            return await repo.get_latest_event_sequence(), events

    async def replay_task_events(
        self, task_id: str, *, after_sequence: int = 0, limit: int = 250
    ) -> tuple[int, list[TaskEvent]] | None:
        """Durable per-task cursor for mobile/desktop reconnection."""
        async with session_scope() as session:
            repo = TaskRepository(session)
            if await repo.get_task(task_id) is None:
                return None
            latest = int((await session.execute(
                select(func.max(TaskEvent.sequence)).where(TaskEvent.task_id == task_id)
            )).scalar_one_or_none() or 0)
            events = list((await session.execute(
                select(TaskEvent)
                .where(TaskEvent.task_id == task_id, TaskEvent.sequence > after_sequence)
                .order_by(TaskEvent.sequence.asc())
                .limit(limit)
            )).scalars().all())
            return latest, events

    async def record_event(
        self,
        task_id: str,
        *,
        event_type: str,
        payload: dict | None,
        idempotency_key: str,
        attempt_id: str | None = None,
        execution_run_id: str | None = None,
        claim_token: str | None = None,
        native_item_id: str | None = None,
    ) -> TaskEvent:
        async with session_scope() as session:
            task = await self._lock_task(session, task_id)
            await self._assert_fence(
                session, task, attempt_id=attempt_id,
                execution_run_id=execution_run_id, claim_token=claim_token,
            )
            return await TaskRepository(session).append_event(
                task_id=task.id,
                attempt_id=attempt_id or task.current_attempt_id,
                execution_run_id=execution_run_id or task.current_run_id,
                native_item_id=native_item_id,
                event_type=event_type,
                payload_json=payload,
                idempotency_key=idempotency_key,
            )

    async def record_native_observation(self, task_id: str, *, event_type: str, payload: dict,
            idempotency_key: str, attempt_id: str, execution_run_id: str, claim_token: str,
            native_item_id: str | None = None) -> TaskEvent:
        """Retain trailing native observations after publication; grants no writer authority."""
        if event_type not in {"agent.native.item", "agent.native.event", "agent.native.action.started", "agent.native.subagent.item"}:
            raise ValueError("Only native observations may outlive the active writer fence")
        from app.services.native_worker import observation_payload
        payload = observation_payload(payload)
        async with session_scope() as session:
            task = await self._lock_task(session, task_id)
            attempt = await session.get(TaskAttempt, attempt_id)
            if (attempt is None or attempt.task_id != task_id or attempt.execution_run_id != execution_run_id
                    or attempt.claim_token != claim_token):
                raise ValueError("Native observation lacks original attempt authority")
            return await TaskRepository(session).append_event(task_id=task.id, attempt_id=attempt_id,
                execution_run_id=execution_run_id, native_item_id=native_item_id,
                event_type=event_type, payload_json=payload, idempotency_key=idempotency_key)

    async def save_checkpoint(
        self,
        task_id: str,
        *,
        checkpoint: dict,
        attempt_id: str | None = None,
        execution_run_id: str | None = None,
        claim_token: str | None = None,
    ) -> None:
        async with session_scope() as session:
            task = await self._lock_task(session, task_id)
            await self._assert_fence(
                session, task, attempt_id=attempt_id,
                execution_run_id=execution_run_id, claim_token=claim_token,
            )
            attempt = await self._current_attempt(session, task)
            if attempt is None or attempt.ended_at is not None:
                raise ValueError("Task has no active attempt")
            attempt.checkpoint_json = checkpoint
            attempt.checkpoint_version += 1
            attempt.heartbeat_at = utc_now()
            await session.flush()

    async def cancellation_requested(self, task_id: str) -> bool:
        async with session_scope() as session:
            task = await TaskRepository(session).get_task(task_id)
            return bool(
                task is not None
                and task.status == "running"
                and (
                    task.cancellation_requested_at is not None
                    or task.pending_control_id is not None
                )
            )

    async def worker_health(self) -> WorkerHealth:
        async with session_scope() as session:
            return await self._worker_health(session)

    async def repair_invalid_reminder_tasks(self) -> int:
        """Quarantine reminder-like work incorrectly sent to the worker queue."""
        repaired = 0
        now = utc_now()
        async with session_scope() as session:
            repo = TaskRepository(session)
            tasks = list((await session.execute(
                select(Task)
                .where(Task.origin_surface != "jarvis_reminder")
                .where(Task.status.in_(sorted(ACTIVE_TASK_STATUSES)))
            )).scalars().all())
            for task in tasks:
                value = task.goal.casefold()
                reminder = re.search(r"\b(remind(?:er)?|alarm|herinner(?:ing|en)?)\b", value)
                timing = re.search(
                    r"\b(in|at|on|today|tomorrow|tonight|minute|minutes|hour|hours|"
                    r"over|om|vandaag|morgen|vanavond|minuut|minuten|uur)\b|\b\d{1,2}[:.]\d{2}\b",
                    value,
                )
                if reminder is None or timing is None:
                    continue
                task.status = "failed"
                task.status_reason = "Reminder request was routed to background work and was not scheduled."
                task.terminal_summary = task.status_reason
                task.finished_at = now
                task.updated_at = now
                await repo.append_event(
                    task_id=task.id,
                    attempt_id=task.current_attempt_id,
                    event_type="task.failed",
                    payload_json={
                        "status": "failed",
                        "reason": "misrouted_reminder",
                        "summary": task.terminal_summary,
                    },
                    idempotency_key=f"task.repair-misrouted-reminder:{task.id}",
                )
                await self._end_current_attempt(session, task, reason="misrouted_reminder")
                repaired += 1
        return repaired

    async def cancel(self, task_id: str, *, idempotency_key: str) -> TaskSnapshot:
        controlled = await self.control(
            task_id,
            action="cancel",
            idempotency_key=idempotency_key,
            parameters={"immediate": True},
        )
        return TaskSnapshot(task=controlled.task, queue_position=controlled.queue_position)

    async def retry(self, task_id: str, *, idempotency_key: str) -> TaskSnapshot:
        controlled = await self.control(
            task_id,
            action="retry",
            idempotency_key=idempotency_key,
        )
        return TaskSnapshot(task=controlled.task, queue_position=controlled.queue_position)

    async def ask_question(
        self,
        task_id: str,
        *,
        question: str,
        idempotency_key: str,
        attempt_id: str | None = None,
        execution_run_id: str | None = None,
        claim_token: str | None = None,
        permission: dict[str, Any] | None = None,
    ) -> TaskQuestion:
        normalized = " ".join(question.split()).strip()
        if not normalized:
            raise ValueError("Task question cannot be empty")
        async with session_scope() as session:
            repo = TaskRepository(session)
            task = await self._lock_task(session, task_id)
            event_key = f"task.question:{task_id}:{idempotency_key}"
            existing = await self._event_by_key(session, event_key)
            if existing is not None:
                question_id = str((existing.payload_json or {}).get("question_id") or "")
                saved = await session.get(TaskQuestion, question_id)
                if saved is not None:
                    if saved.question != normalized or saved.permission_json != permission:
                        raise ValueError("Question operation key conflicts with an earlier question")
                    if attempt_id is not None:
                        prior_attempt = await session.get(TaskAttempt, attempt_id)
                        if (prior_attempt is None or prior_attempt.task_id != task_id
                                or prior_attempt.claim_token != claim_token
                                or saved.asked_attempt_id != attempt_id or saved.asking_run_id != execution_run_id):
                            raise ValueError("Question replay lacks original authority")
                    return saved
            await self._assert_fence(
                session, task, attempt_id=attempt_id,
                execution_run_id=execution_run_id, claim_token=claim_token,
            )
            if task.status not in {"queued", "waiting_for_worker", "running"}:
                raise ValueError(f"Task in {task.status} cannot ask a question")
            question_row = TaskQuestion(
                id=str(uuid4()),
                task_id=task.id,
                asking_run_id=task.current_run_id,
                asked_attempt_id=task.current_attempt_id,
                question=normalized,
                state="asked",
                permission_json=permission,
            )
            session.add(question_row)
            await session.flush()
            event = await repo.append_event(
                task_id=task.id,
                attempt_id=task.current_attempt_id,
                event_type="task.requires_input",
                payload_json={"question_id": question_row.id, "question": normalized,
                    **({"permission": permission, "human_only": True} if permission else {})},
                idempotency_key=event_key,
            )
            question_row.source_task_event_id = event.id
            task.status = "waiting_for_input"
            task.updated_at = utc_now()
            run = await self._current_run(session, task)
            if run is not None:
                run.status = "waiting_for_input"
            await self._end_current_attempt(session, task, reason="waiting_for_input")
            await session.flush()
            saved_question = question_row
        return saved_question

    async def answer_question(
        self,
        task_id: str,
        question_id: str,
        *,
        answer: str,
        idempotency_key: str,
        decision_authority: DecisionAuthority | None = None,
    ) -> TaskSnapshot:
        normalized = " ".join(answer.split()).strip()
        if not normalized:
            raise ValueError("Task answer cannot be empty")
        async with session_scope() as session:
            repo = TaskRepository(session)
            task = await self._lock_task(session, task_id)
            if decision_authority is not None and idempotency_key != decision_authority.idempotency_key:
                raise ValueError("Background idempotency identity is backend-owned")
            await begin_action(session, decision_authority, action="answer", task_id=task_id, arguments={
                "question_id": question_id, "answer": normalized,
            })
            event_key = f"task.answer:{task_id}:{question_id}:{idempotency_key}"
            if await self._event_by_key(session, event_key) is None:
                question = await session.get(TaskQuestion, question_id)
                if question is None or question.task_id != task_id:
                    raise ValueError("Task question not found")
                if question.permission_json and decision_authority is not None:
                    raise ValueError("Permission requests require an explicit human decision")
                if question.permission_json and normalized.casefold() not in {"approve", "deny"}:
                    raise ValueError("Permission answer must be approve or deny")
                if question.state != "asked" or question.answered_at is not None:
                    raise ValueError("Task question has already been answered")
                if task.status != "waiting_for_input":
                    raise ValueError(f"Task in {task.status} is not waiting for input")
                attempt = await self._create_attempt(session, task)
                now = utc_now()
                question.answer = normalized
                question.state = "answered"
                question.answered_at = now
                question.answered_attempt_id = attempt.id
                task.control_revision += 1
                question.resolution_revision = task.control_revision
                task.status = "queued"
                task.queue_entered_at = now
                task.current_attempt_id = attempt.id
                task.updated_at = now
                run = await self._current_run(session, task)
                if run is not None:
                    run.status = "queued"
                    run.queue_entered_at = now
                next_sequence = int((await session.execute(
                    select(func.max(TaskTurn.sequence)).where(TaskTurn.task_id == task.id)
                )).scalar_one_or_none() or 0) + 1
                session.add(TaskTurn(
                    id=str(uuid4()),
                    task_id=task.id,
                    sequence=next_sequence,
                    role="user",
                    content=normalized,
                    origin="question_answer",
                    request_identity=idempotency_key,
                    input_fingerprint=_fingerprint({"answer": normalized, "question_id": question.id}),
                    execution_run_id=task.current_run_id,
                    question_id=question.id,
                ))
                await repo.append_event(
                    task_id=task.id,
                    attempt_id=attempt.id,
                    event_type="task.question.answered",
                    payload_json={
                        "question_id": question.id,
                        "answer": normalized,
                        "attempt_number": attempt.attempt_number,
                    },
                    idempotency_key=event_key,
                )
                await self._assign_worker_eligibility(
                    session,
                    task,
                    repo=repo,
                    idempotency_key=f"task.answer-waiting-for-worker:{event_key}",
                )
            await session.flush()
            return TaskSnapshot(task=task, queue_position=await self._queue_position(session, task))

    async def publish_progress(
        self,
        task_id: str,
        *,
        summary: str,
        idempotency_key: str,
        attempt_id: str | None = None,
        execution_run_id: str | None = None,
        claim_token: str | None = None,
    ) -> TaskSnapshot:
        normalized = " ".join(summary.split()).strip()
        if not normalized:
            raise ValueError("Task progress cannot be empty")
        async with session_scope() as session:
            repo = TaskRepository(session)
            task = await self._lock_task(session, task_id)
            await self._assert_fence(
                session, task, attempt_id=attempt_id,
                execution_run_id=execution_run_id, claim_token=claim_token,
            )
            event_key = f"task.progress:{task_id}:{idempotency_key}"
            if await self._event_by_key(session, event_key) is not None:
                return TaskSnapshot(task=task, queue_position=await self._queue_position(session, task))
            if task.status not in {"queued", "running"}:
                raise ValueError(f"Task in {task.status} cannot publish progress")
            task.status = "running"
            task.status_reason = None
            task.updated_at = utc_now()
            attempt = await self._current_attempt(session, task)
            if attempt is not None and attempt.started_at is None:
                attempt.started_at = utc_now()
            if attempt is not None:
                attempt.last_progress_at = utc_now()
                recovery = dict(attempt.recovery_metadata_json or {})
                if recovery.get("consecutive_crashes"):
                    recovery["consecutive_crashes"] = 0
                    attempt.recovery_metadata_json = recovery
            run = await self._current_run(session, task)
            if run is not None:
                run.status = "running"
                run.started_at = run.started_at or utc_now()
            await repo.append_event(
                task_id=task.id,
                attempt_id=task.current_attempt_id,
                event_type="task.progress",
                payload_json={"status": "running", "summary": normalized},
                idempotency_key=event_key,
            )
            return TaskSnapshot(task=task, queue_position=None)

    async def finish_cancellation(self, task_id: str, *, idempotency_key: str) -> TaskSnapshot:
        """Record that a running attempt observed a requested cancellation."""
        detail = await self.get_task(task_id)
        if detail is None:
            raise ValueError(f"Task not found: {task_id}")
        if detail.task.pending_control_id is not None and detail.task.current_attempt_id is not None:
            command = next(
                (item for item in detail.controls if item.id == detail.task.pending_control_id),
                None,
            )
            if command is not None and command.action == "cancel":
                confirmed = await self.confirm_control(
                    task_id,
                    command_id=command.id,
                    stopped_attempt_id=detail.task.current_attempt_id,
                    stop_evidence={"kind": "worker_observed", "idempotency_key": idempotency_key},
                )
                return TaskSnapshot(confirmed.task, confirmed.queue_position)
        async with session_scope() as session:
            repo = TaskRepository(session)
            task = await self._lock_task(session, task_id)
            event_key = f"task.cancelled:{task_id}:{idempotency_key}"
            if await self._event_by_key(session, event_key) is None:
                if task.status != "running" or task.cancellation_requested_at is None:
                    raise ValueError("Task does not have a running cancellation request")
                now = utc_now()
                task.status = "cancelled"
                task.finished_at = now
                task.updated_at = now
                task.terminal_summary = "Task cancelled by user."
                run = await self._current_run(session, task)
                if run is not None:
                    run.status = "cancelled"
                    run.finished_at = now
                    run.terminal_summary = task.terminal_summary
                await repo.append_event(
                    task_id=task.id,
                    attempt_id=task.current_attempt_id,
                    event_type="task.cancelled",
                    payload_json={"status": "cancelled", "summary": task.terminal_summary},
                    idempotency_key=event_key,
                )
                await self._end_current_attempt(session, task, reason="cancelled")
            await session.flush()
            snapshot = TaskSnapshot(task=task, queue_position=None)
        return snapshot

    @observe_lifecycle("publication")
    async def complete(
        self,
        task_id: str,
        *,
        summary: str,
        idempotency_key: str,
        status: str = "completed",
        final_text: str | None = None,
        workflow_result: dict[str, Any] | None = None,
        workflow_operation_key: str | None = None,
        workflow_base_revision: int | None = None,
        workflow_answer: bool = False,
        attempt_id: str | None = None,
        execution_run_id: str | None = None,
        claim_token: str | None = None,
    ) -> TaskSnapshot:
        if status not in {"completed", "failed", "interrupted"}:
            raise ValueError(f"Invalid terminal task status: {status}")
        normalized = " ".join(summary.split()).strip()
        async with session_scope() as session:
            repo = TaskRepository(session)
            task = await self._lock_task(session, task_id)
            producing_run = await session.get(TaskExecutionRun, execution_run_id or task.current_run_id) if (execution_run_id or task.current_run_id) else None
            if (producing_run is not None and producing_run.execution_kind == "agent"
                    and publication_profile(producing_run) is not None
                    and status == "completed" and not (attempt_id and execution_run_id and claim_token)):
                raise ValueError("Agent publication requires the claimed attempt and run fence")
            if (producing_run is not None and producing_run.task_id == task_id
                    and producing_run.execution_kind == "agent" and status == "completed"
                    and final_text is not None and publication_profile(producing_run) is not None):
                # General runtimes return Markdown; this adapter supplies the
                # generic Document shape, not a separate publication lifecycle.
                workflow_result = {"title": normalized[:200] or "Document", "body": final_text,
                    "source_event_sequences": []}
                workflow_operation_key = f"final:{sha256(idempotency_key.encode()).hexdigest()}"
                workflow_base_revision = (producing_run.configuration_json or {}).get("result", {}).get("expected_revision")
                final_text = None
            if workflow_answer and attempt_id is not None:
                prior_terminal = await self._event_by_key(session, f"task.terminal:{task_id}:{idempotency_key}")
                if prior_terminal is not None:
                    prior_attempt = await session.get(TaskAttempt, attempt_id)
                    if (prior_attempt is None or prior_attempt.task_id != task_id
                            or prior_attempt.claim_token != claim_token
                            or prior_terminal.attempt_id != attempt_id
                            or prior_terminal.execution_run_id != execution_run_id
                            or prior_terminal.payload_json != {"status": status, "summary": normalized}):
                        raise ValueError("Workflow finish replay conflicts or lacks original authority")
                    return TaskSnapshot(task=task, queue_position=None)
            if workflow_result is not None and workflow_operation_key and execution_run_id:
                # An MCP success response can be lost after the publication
                # transaction commits. Permit only an exact, read-only replay
                # from the same claimed attempt; the ended attempt cannot make
                # any further writes through the normal fence.
                prior = (await session.execute(select(TaskOperation).where(
                    TaskOperation.task_id == task_id,
                    TaskOperation.execution_run_id == execution_run_id,
                    TaskOperation.operation_key == f"{execution_run_id}:{workflow_operation_key}",
                    TaskOperation.operation_type == "task.result.publish@1",
                ).limit(1))).scalar_one_or_none()
                if prior is not None:
                    prior_run = await session.get(TaskExecutionRun, execution_run_id)
                    if (prior_run is None or prior_run.task_id != task_id
                            or prior_run.status != "completed" or prior.state != "succeeded"
                            or prior_run.result_reference != prior.result_reference
                            or prior.attempt_id != attempt_id):
                        raise ValueError("Workflow publication replay is inconsistent")
                    prior_attempt = await session.get(TaskAttempt, attempt_id)
                    if prior_attempt is None or prior_attempt.claim_token != claim_token:
                        raise ValueError("Workflow publication replay lacks original authority")
                    pinned = publication_profile(prior_run)
                    if pinned is None:
                        raise ValueError("This run has no publication authority")
                    phase("validation")
                    validated = validate_workflow_result(pinned["output_schema"], workflow_result)
                    publication_input = validated if workflow_base_revision is None else {
                        "content": validated, "base_revision": workflow_base_revision}
                    if prior.input_digest != _fingerprint(publication_input):
                        phase("validation", outcome="conflict")
                        raise ValueError("Workflow publication operation key conflicts with an earlier result")
                    phase("replay", outcome="replayed")
                    return TaskSnapshot(task=task, queue_position=None)
            await self._assert_fence(
                session, task, attempt_id=attempt_id,
                execution_run_id=execution_run_id, claim_token=claim_token,
            )
            run = await self._current_run(session, task)
            pinned = publication_profile(run) if run is not None else None
            validated_result: dict[str, Any] | None = None
            if status == "completed" and run is not None and run.execution_kind == "agent" and pinned is not None and workflow_result is None:
                raise ValueError("Publishing agent run must supply a Document result")
            if (status == "completed" and task.execution_kind == "workflow" and run is not None
                    and (run.configuration_json or {}).get("workflow") is not None
                    and workflow_result is None
                    and not (workflow_answer
                        and (run.configuration_json or {}).get("result", {}).get("mode") == "answer"
                        and "task.finish@1" in validate_pinned_workflow(dict(run.configuration_json or {}))["capabilities"])):
                raise ValueError("A production workflow must publish its typed result")
            if workflow_result is not None:
                phase("validation")
                validation_started = perf_counter()
                if status != "completed" or final_text is not None or run is None or pinned is None:
                    raise ValueError("Typed publication requires an authorized publishing run")
                validated_result = validate_workflow_result(pinned["output_schema"], workflow_result)
                if not isinstance(workflow_operation_key, str) or not (1 <= len(workflow_operation_key) <= 180):
                    raise ValueError("Workflow publication requires a stable operation key")
                if workflow_base_revision is not None and (type(workflow_base_revision) is not int or workflow_base_revision < 0):
                    raise ValueError("Expected base revision must be a nonnegative integer")
                if result_type(pinned["output_schema"]).app_slug is not None:
                    page = (await session.execute(select(ResultPage).where(
                        ResultPage.task_id == task_id, ResultPage.active_execution_run_id == run.id,
                    ))).scalar_one_or_none()
                    if page is None:
                        raise ValueError("Result page is not bound to this run")
                    if pinned["schema"] == "workflow-pinned.v2" and page.revision_number and workflow_base_revision is None:
                        raise ValueError("Updating a result requires an expected base revision")
                    if workflow_base_revision is not None and workflow_base_revision != page.revision_number:
                        phase("validation", outcome="conflict")
                        raise ValueError("Expected base revision is stale; inspect the current result")
                    request = (run.configuration_json or {}).get("result")
                    if request and request["mode"] == "update" and workflow_base_revision != request["expected_revision"]:
                        raise ValueError("Publication must use the admitted expected base revision")
                sources = validated_result["source_event_sequences"]
                if sources:
                    found = set((await session.execute(select(TaskEvent.sequence).where(
                        TaskEvent.task_id == task_id, TaskEvent.sequence.in_(sources),
                    ))).scalars().all())
                    if found != set(sources):
                        raise ValueError("Workflow result cites an event outside this task")
                from app.services.native_research import validate_research_evidence
                await validate_research_evidence(session, task_id, pinned, validated_result)
                from app.services.task_results import validate_attachments
                await validate_attachments(session, task_id, validated_result)
                observe_validation(validation_started)
            event_key = f"task.terminal:{task_id}:{idempotency_key}"
            if await self._event_by_key(session, event_key) is None:
                if task.status not in ACTIVE_TASK_STATUSES:
                    raise ValueError(f"Task in {task.status} cannot complete")
                now = utc_now()
                completed()
                task.status = status
                task.terminal_summary = normalized or None
                task.finished_at = now
                task.updated_at = now
                if run is not None:
                    run.status = status
                    run.terminal_summary = normalized or None
                    run.finished_at = now
                    run.updated_at = now
                if status == "completed" and (final_text is not None or validated_result is not None):
                    artifact = TaskArtifact(
                        id=str(uuid4()),
                        task_id=task.id,
                        attempt_id=task.current_attempt_id,
                        execution_run_id=task.current_run_id,
                        artifact_type="workflow_result" if validated_result is not None else "result",
                        mime_type="application/json" if validated_result is not None else "text/markdown",
                        title=validated_result["title"] if validated_result is not None else "Task result",
                        content_text=final_text,
                        content_json={"schema": pinned["output_schema"], "content": validated_result}
                        if validated_result is not None else None,
                    )
                    session.add(artifact)
                    await session.flush()
                    created_event = await repo.append_event(
                        task_id=task.id,
                        attempt_id=task.current_attempt_id,
                        execution_run_id=task.current_run_id,
                        event_type="task.artifact.created",
                        payload_json={
                            "artifact_id": artifact.id,
                            "artifact_type": "result",
                            "title": artifact.title,
                        },
                        idempotency_key=f"task.result:{task_id}:{idempotency_key}",
                    )
                    artifact.source_task_event_id = created_event.id
                    if run is not None:
                        run.result_reference = f"task-artifact:{artifact.id}"
                    if (validated_result is not None and result_type(pinned["output_schema"]).app_slug is not None
                            and run is not None):
                        await publish_document_result(
                            session, task_id=task.id, run_id=run.id,
                            attempt_id=task.current_attempt_id,
                            artifact=artifact, content=validated_result, schema=pinned["output_schema"],
                            publication_key=workflow_operation_key,
                        )
                    if validated_result is not None and run is not None:
                        operation = TaskOperation(
                            id=str(uuid4()), task_id=task.id, execution_run_id=run.id,
                            attempt_id=task.current_attempt_id,
                            operation_key=f"{run.id}:{workflow_operation_key}",
                            operation_type="task.result.publish@1", state="succeeded",
                            input_digest=_fingerprint(validated_result if workflow_base_revision is None else {
                                "content": validated_result, "base_revision": workflow_base_revision}),
                            intent_json={"schema": pinned["output_schema"]},
                            result_reference=run.result_reference,
                            result_json={"artifact_id": artifact.id},
                            completed_at=now,
                        )
                        session.add(operation)
                        await repo.append_event(
                            task_id=task.id, attempt_id=task.current_attempt_id,
                            execution_run_id=run.id,
                            event_type="task.operation.succeeded",
                            payload_json={"operation_id": operation.id,
                                          "operation_key": operation.operation_key,
                                          "operation_type": operation.operation_type,
                                          "result_reference": run.result_reference},
                            idempotency_key=f"task.operation:{operation.id}:succeeded",
                        )
                if status != "completed" and run is not None:
                    await fail_document_result(
                        session, task_id=task.id, run_id=run.id,
                        reason=normalized or status,
                    )
                elif workflow_answer and validated_result is None and run is not None:
                    page = (await session.execute(select(ResultPage).where(
                        ResultPage.task_id == task.id, ResultPage.active_execution_run_id == run.id,
                    ))).scalar_one_or_none()
                    if page is not None:
                        page.status = "ready" if page.current_revision_id else "failed"
                        page.warning = None if page.current_revision_id else normalized
                        from app.services.task_results import append_result_event
                        await append_result_event(session, page, "answer_completed", event_key=f"answer:{run.id}")
                terminal_event = await repo.append_event(
                    task_id=task.id,
                    attempt_id=task.current_attempt_id,
                    execution_run_id=None if run is None else run.id,
                    event_type=f"task.{status}",
                    payload_json={"status": status, "summary": normalized},
                    idempotency_key=event_key,
                )
                if validated_result is not None and normalize_task_update_policy(task.presentation_policy) != SILENT:
                    # Persist publication and delivery intent together. The
                    # attention projector subsequently deduplicates by event.
                    from app.services.task_notifications import enqueue_task_notification
                    await enqueue_task_notification(session, task=task, event=terminal_event)
                await self._end_current_attempt(session, task, reason=status)
                if status == "completed" and normalized:
                    next_sequence = int((await session.execute(
                        select(func.max(TaskTurn.sequence)).where(TaskTurn.task_id == task.id)
                    )).scalar_one_or_none() or 0) + 1
                    session.add(TaskTurn(
                        id=str(uuid4()),
                        task_id=task.id,
                        sequence=next_sequence,
                        role="assistant",
                        content=normalized,
                        origin="executor",
                        execution_run_id=task.current_run_id,
                    ))
                await self._promote_next_run(session, task, repo=repo)
            await session.flush()
            snapshot = TaskSnapshot(task=task, queue_position=None)
        return snapshot

    async def add_artifact(
        self,
        task_id: str,
        *,
        artifact_type: str,
        mime_type: str,
        idempotency_key: str,
        title: str | None = None,
        uri: str | None = None,
        content_text: str | None = None,
        content_json: dict | None = None,
        attempt_id: str | None = None,
        execution_run_id: str | None = None,
        claim_token: str | None = None,
    ) -> TaskArtifact:
        async with session_scope() as session:
            repo = TaskRepository(session)
            task = await self._lock_task(session, task_id)
            await self._assert_fence(
                session, task, attempt_id=attempt_id,
                execution_run_id=execution_run_id, claim_token=claim_token,
            )
            event_key = f"task.artifact:{task_id}:{idempotency_key}"
            existing = await self._event_by_key(session, event_key)
            if existing is not None:
                artifact_id = str((existing.payload_json or {}).get("artifact_id") or "")
                artifact = await session.get(TaskArtifact, artifact_id)
                if artifact is not None:
                    return artifact
            artifact = TaskArtifact(
                id=str(uuid4()),
                task_id=task.id,
                attempt_id=task.current_attempt_id,
                execution_run_id=task.current_run_id,
                artifact_type=artifact_type,
                mime_type=mime_type,
                title=title,
                uri=uri,
                content_text=content_text,
                content_json=content_json,
            )
            session.add(artifact)
            await session.flush()
            event = await repo.append_event(
                task_id=task.id,
                attempt_id=task.current_attempt_id,
                event_type="task.artifact.created",
                payload_json={"artifact_id": artifact.id, "artifact_type": artifact_type, "title": title},
                idempotency_key=event_key,
            )
            artifact.source_task_event_id = event.id
            await session.flush()
            return artifact

    async def start_operation(
        self,
        task_id: str,
        *,
        operation_key: str,
        operation_type: str,
        input_digest: str | None,
        intent: dict[str, Any] | None = None,
        attempt_id: str,
        execution_run_id: str,
        claim_token: str,
    ) -> TaskOperation:
        async with session_scope() as session:
            task = await self._lock_task(session, task_id)
            await self._assert_fence(
                session, task, attempt_id=attempt_id,
                execution_run_id=execution_run_id, claim_token=claim_token,
            )
            existing = (await session.execute(
                select(TaskOperation).where(
                    TaskOperation.task_id == task_id,
                    TaskOperation.operation_key == operation_key,
                ).limit(1)
            )).scalar_one_or_none()
            if existing is not None:
                if (existing.operation_type != operation_type or existing.input_digest != input_digest
                        or existing.intent_json != intent):
                    raise ValueError("Operation key was already used with different input")
                if existing.state == "started":
                    raise ValueError("Operation is already in flight; outcome must be reconciled")
                if existing.state != "succeeded":
                    raise ValueError("Operation has an uncertain or failed outcome and cannot be replayed")
                return existing
            if operation_type in {"broker.file_write", "broker.project_write", "broker.command", "connector.tool@1"}:
                unresolved = (await session.execute(
                    select(TaskOperation.id).where(
                        TaskOperation.task_id == task_id,
                        TaskOperation.state.in_(["started", "uncertain"]),
                    ).limit(1)
                )).scalar_one_or_none()
                if unresolved is not None:
                    raise ValueError(
                        "This task has an unresolved uncertain operation; no new mutation is allowed"
                    )
            operation = TaskOperation(
                id=str(uuid4()),
                task_id=task_id,
                execution_run_id=execution_run_id,
                attempt_id=attempt_id,
                operation_key=operation_key,
                operation_type=operation_type,
                input_digest=input_digest,
                intent_json=dict(intent) if intent is not None else None,
                state="started",
            )
            session.add(operation)
            attempt = await session.get(TaskAttempt, attempt_id)
            assert attempt is not None
            attempt.active_operation = operation.id
            attempt.operation_started_at = utc_now()
            await session.flush()
            return operation

    async def validate_execution_authority(
        self,
        task_id: str,
        *,
        attempt_id: str,
        execution_run_id: str,
        claim_token: str,
    ) -> None:
        async with session_scope() as session:
            task = await self._lock_task(session, task_id)
            await self._assert_fence(
                session, task, attempt_id=attempt_id,
                execution_run_id=execution_run_id, claim_token=claim_token,
            )

    async def finish_operation(
        self,
        task_id: str,
        operation_id: str,
        *,
        state: str,
        attempt_id: str,
        execution_run_id: str,
        claim_token: str,
        result_reference: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> TaskOperation:
        if state not in {"succeeded", "failed", "uncertain"}:
            raise ValueError("Operation state must be succeeded, failed, or uncertain")
        async with session_scope() as session:
            task = await self._lock_task(session, task_id)
            await self._assert_fence(
                session, task, attempt_id=attempt_id,
                execution_run_id=execution_run_id, claim_token=claim_token,
            )
            operation = await session.get(TaskOperation, operation_id)
            if (
                operation is None
                or operation.task_id != task_id
                or operation.execution_run_id != execution_run_id
                or operation.attempt_id != attempt_id
            ):
                raise ValueError("Task operation not found")
            if operation.state != "started":
                if operation.state != state:
                    raise ValueError("Task operation is already terminal")
                return operation
            operation.state = state
            operation.result_reference = result_reference
            operation.result_json = result
            operation.error = error
            operation.completed_at = utc_now()
            attempt = await session.get(TaskAttempt, attempt_id)
            assert attempt is not None
            attempt.active_operation = None
            attempt.operation_started_at = None
            attempt.operation_deadline = None
            await TaskRepository(session).append_event(
                task_id=task_id,
                attempt_id=attempt_id,
                execution_run_id=execution_run_id,
                event_type=f"task.operation.{state}",
                payload_json={
                    "operation_id": operation.id,
                    "operation_key": operation.operation_key,
                    "operation_type": operation.operation_type,
                    "result_reference": result_reference,
                },
                idempotency_key=f"task.operation:{operation.id}:{state}",
            )
            await session.flush()
            return operation

    async def reconcile_started_operations(self) -> int:
        """Mark operations left in-flight by a stopped writer as uncertain."""
        async with session_scope() as session:
            operations = list((await session.execute(
                select(TaskOperation).where(TaskOperation.state == "started")
            )).scalars().all())
            changed = 0
            for operation in operations:
                attempt = await session.get(TaskAttempt, operation.attempt_id) if operation.attempt_id else None
                if attempt is not None and attempt.ended_at is None:
                    continue
                operation.state = "uncertain"
                operation.error = "Writer stopped before a durable operation result was recorded."
                operation.completed_at = utc_now()
                changed += 1
            return changed

    async def latest_native_binding(self, task_id: str) -> TaskNativeBinding | None:
        async with session_scope() as session:
            return (await session.execute(
                select(TaskNativeBinding)
                .where(TaskNativeBinding.task_id == task_id)
                .order_by(TaskNativeBinding.created_at.desc(), TaskNativeBinding.generation.desc())
                .limit(1)
            )).scalar_one_or_none()

    async def bind_native_thread(
        self,
        task_id: str,
        *,
        execution_run_id: str,
        native_thread_id: str,
        profile_id: str,
        runtime_version: str,
        storage_reference: str,
        compatibility: dict[str, Any],
        attempt_id: str,
        claim_token: str,
        replacement_reason: str | None = None,
    ) -> TaskNativeBinding:
        async with session_scope() as session:
            task = await self._lock_task(session, task_id)
            await self._assert_fence(
                session, task, attempt_id=attempt_id,
                execution_run_id=execution_run_id, claim_token=claim_token,
            )
            if task.current_run_id != execution_run_id:
                raise ValueError("Cannot bind a stale task execution run")
            existing = (await session.execute(
                select(TaskNativeBinding)
                .where(
                    TaskNativeBinding.execution_run_id == execution_run_id,
                    TaskNativeBinding.native_thread_id == native_thread_id,
                )
                .limit(1)
            )).scalar_one_or_none()
            if existing is not None:
                return existing
            max_generation = int((await session.execute(
                select(func.max(TaskNativeBinding.generation)).where(
                    TaskNativeBinding.execution_run_id == execution_run_id
                )
            )).scalar_one_or_none() or 0)
            binding = TaskNativeBinding(
                id=str(uuid4()),
                task_id=task_id,
                execution_run_id=execution_run_id,
                generation=max_generation + 1,
                native_thread_id=native_thread_id,
                profile_id=profile_id,
                runtime_version=runtime_version,
                compatibility_json=dict(compatibility),
                storage_reference=storage_reference,
                replacement_reason=replacement_reason,
            )
            session.add(binding)
            await session.flush()
            return binding

    async def attach_runtime_reference(
        self,
        task_id: str,
        runtime_reference: str,
        *,
        attempt_id: str,
        execution_run_id: str,
        claim_token: str,
    ) -> None:
        """Link the current attempt to a temporary or durable execution runtime."""
        async with session_scope() as session:
            task = await self._lock_task(session, task_id)
            await self._assert_fence(
                session, task, attempt_id=attempt_id,
                execution_run_id=execution_run_id, claim_token=claim_token,
            )
            attempt = await self._current_attempt(session, task)
            if attempt is None:
                raise ValueError("Task has no current attempt")
            attempt.agent_runtime_reference = runtime_reference
            await session.flush()

    async def _lock_task(self, session: AsyncSession, task_id: str) -> Task:
        result = await session.execute(
            update(Task).where(Task.id == task_id).values(status=Task.status)
        )
        if result.rowcount != 1:
            raise ValueError(f"Task not found: {task_id}")
        task_result = await session.execute(select(Task).where(Task.id == task_id).limit(1))
        return task_result.scalar_one()

    async def _assert_fence(
        self,
        session: AsyncSession,
        task: Task,
        *,
        attempt_id: str | None,
        execution_run_id: str | None,
        claim_token: str | None,
    ) -> None:
        if attempt_id is None and execution_run_id is None and claim_token is None:
            return
        if not attempt_id or not execution_run_id or not claim_token:
            raise ValueError("Fenced task writes require run, attempt, and claim token")
        attempt = await session.get(TaskAttempt, attempt_id)
        if (
            attempt is None
            or task.current_run_id != execution_run_id
            or task.current_attempt_id != attempt_id
            or attempt.execution_run_id != execution_run_id
            or attempt.claim_token != claim_token
            or attempt.ended_at is not None
        ):
            raise ValueError("Stale task execution authority")
        supervisor = await session.get(TaskSupervisorLease, "task-pool")
        if (
            supervisor is not None
            and attempt.supervisor_generation != supervisor.generation
        ):
            raise ValueError("Stale task supervisor generation")

    async def _ensure_targets(
        self,
        session: AsyncSession,
        task_id: str,
        targets: tuple[tuple[str, str, bool], ...],
    ) -> None:
        normalized: dict[tuple[str, str], bool] = {}
        for resource_type, resource_key, mutable in targets:
            target_type = resource_type.strip()
            target_key = resource_key.strip()
            if not target_type or not target_key:
                raise ValueError("Task targets require a resource type and key")
            normalized[(target_type, target_key)] = bool(mutable)
        for (resource_type, resource_key), mutable in sorted(normalized.items()):
            await session.execute(
                sqlite_insert(TaskTarget)
                .values(
                    id=str(uuid4()),
                    task_id=task_id,
                    resource_type=resource_type,
                    resource_key=resource_key,
                    mutable=mutable,
                )
                .on_conflict_do_nothing(
                    index_elements=["task_id", "resource_type", "resource_key"]
                )
            )

    async def _ensure_initial_run(
        self,
        session: AsyncSession,
        task: Task,
        *,
        request_identity: str,
        execution_config: dict[str, Any] | None = None,
    ) -> TaskExecutionRun:
        if task.current_run_id is not None:
            existing = await session.get(TaskExecutionRun, task.current_run_id)
            if existing is not None:
                turn = await session.get(TaskTurn, existing.input_turn_id)
                expected = _fingerprint(
                    {
                        "content": task.goal,
                        "execution_kind": task.execution_kind,
                        "configuration": execution_config or {},
                    }
                )
                if turn is not None and turn.input_fingerprint not in {None, expected}:
                    raise ValueError("Idempotency key was already used with different task input")
                return existing

        turn = TaskTurn(
            id=str(uuid4()),
            task_id=task.id,
            sequence=1,
            role="user",
            content=task.goal,
            origin=task.origin_surface,
            request_identity=request_identity,
            input_fingerprint=_fingerprint(
                {
                    "content": task.goal,
                    "execution_kind": task.execution_kind,
                    "configuration": execution_config or {},
                }
            ),
        )
        session.add(turn)
        await session.flush()
        run = TaskExecutionRun(
            id=str(uuid4()),
            task_id=task.id,
            ordinal=1,
            input_turn_id=turn.id,
            status=task.status,
            execution_kind=task.execution_kind,
            configuration_json=execution_config or {},
            workspace_generation=task.workspace_generation,
            queue_entered_at=task.queue_entered_at or utc_now(),
        )
        session.add(run)
        await session.flush()
        turn.execution_run_id = run.id
        task.current_run_id = run.id
        return run

    async def _current_run(self, session: AsyncSession, task: Task) -> TaskExecutionRun | None:
        if task.current_run_id is None:
            return None
        return await session.get(TaskExecutionRun, task.current_run_id)

    async def _promote_next_run(
        self,
        session: AsyncSession,
        task: Task,
        *,
        repo: TaskRepository,
    ) -> TaskExecutionRun | None:
        next_run = (await session.execute(
            select(TaskExecutionRun)
            .where(TaskExecutionRun.task_id == task.id, TaskExecutionRun.status == "pending")
            .order_by(TaskExecutionRun.ordinal.asc())
            .limit(1)
        )).scalar_one_or_none()
        if next_run is None:
            return None
        turn = await session.get(TaskTurn, next_run.input_turn_id)
        if turn is None or not turn.content:
            raise RuntimeError("Pending task run has no input turn")
        now = utc_now()
        task.current_run_id = next_run.id
        task.execution_kind = next_run.execution_kind
        task.goal = turn.content
        task.goal_revision += 1
        task.status = "queued"
        task.status_reason = None
        task.terminal_summary = None
        task.finished_at = None
        task.cancellation_requested_at = None
        task.queue_entered_at = now
        task.updated_at = now
        context_request = (await session.execute(select(TaskContextRequest).where(
            TaskContextRequest.execution_run_id == next_run.id,
            TaskContextRequest.state != "ready").limit(1))).scalar_one_or_none()
        if context_request is not None and context_request.state != "ready":
            task.status = "waiting_for_input"
            task.status_reason = "context_upload_pending"
            next_run.status = "waiting_for_input"
        else:
            next_run.status = "queued"
        next_run.queue_entered_at = now
        attempt = await self._create_attempt(session, task, execution_run=next_run)
        await repo.append_event(
            task_id=task.id,
            attempt_id=attempt.id,
            event_type="task.run.promoted",
            payload_json={
                "run_id": next_run.id,
                "ordinal": next_run.ordinal,
                "turn_id": next_run.input_turn_id,
                "status": next_run.status,
            },
            idempotency_key=f"task.run.promoted:{next_run.id}",
        )
        await self._assign_worker_eligibility(
            session,
            task,
            repo=repo,
            idempotency_key=f"task.run.promoted.waiting:{next_run.id}",
        )
        return next_run

    async def _ensure_attempt(
        self,
        session: AsyncSession,
        task: Task,
        *,
        execution_run: TaskExecutionRun | None = None,
    ) -> TaskAttempt:
        run = execution_run or await self._current_run(session, task)
        if run is None:
            run = await self._ensure_initial_run(
                session,
                task,
                request_identity=task.idempotency_key,
            )
        result = await session.execute(
            select(TaskAttempt)
            .where(TaskAttempt.task_id == task.id, TaskAttempt.execution_run_id == run.id)
            .order_by(TaskAttempt.attempt_number.asc())
            .limit(1)
        )
        attempt = result.scalar_one_or_none()
        if attempt is None:
            attempt = await self._create_attempt(session, task, execution_run=run)
        if task.current_attempt_id != attempt.id:
            task.current_attempt_id = attempt.id
        run.current_attempt_id = attempt.id
        return attempt

    async def _create_attempt(
        self,
        session: AsyncSession,
        task: Task,
        *,
        execution_run: TaskExecutionRun | None = None,
        parent_attempt: TaskAttempt | None = None,
        resume_reason: str | None = None,
        checkpoint: dict | None = None,
    ) -> TaskAttempt:
        run = execution_run or await self._current_run(session, task)
        page = None
        if run is not None:
            pinned = publication_profile(run) if (run.configuration_json or {}).get("workflow") or (run.configuration_json or {}).get("result") else None
        else:
            pinned = None
        if pinned is not None:
            if result_type(pinned["output_schema"]).app_slug is not None:
                request = (run.configuration_json or {}).get("result")
                if request and request["mode"] == "new":
                    page = await allocate_document_result(session, task_id=task.id, run_id=run.id, schema=pinned["output_schema"])
                else:
                    query = select(ResultPage).where(ResultPage.task_id == task.id)
                    if request:
                        query = query.where(ResultPage.public_id == request["public_id"])
                    else:
                        # Retry the run's own allocation before considering the
                        # legacy implicit single-result association.
                        page = (await session.execute(query.where(ResultPage.active_execution_run_id == run.id))).scalar_one_or_none()
                        if page is not None:
                            query = query.where(ResultPage.id == page.id)
                    page = (await session.execute(query)).scalar_one_or_none()
                if page is None:
                    page = await allocate_document_result(session, task_id=task.id, run_id=run.id, schema=pinned["output_schema"])
                elif page.active_execution_run_id != run.id:
                    if page.result_kind != pinned["output_schema"]:
                        raise ValueError("Follow-up result type differs from the existing result")
                    from app.services.task_results import begin_result_update
                    await begin_result_update(session, page, run.id)
        if run is None:
            run = await self._ensure_initial_run(
                session,
                task,
                request_identity=task.idempotency_key,
            )
        result = await session.execute(
            select(func.max(TaskAttempt.attempt_number)).where(TaskAttempt.task_id == task.id)
        )
        attempt_number = int(result.scalar_one_or_none() or 0) + 1
        attempt = TaskAttempt(
            id=str(uuid4()),
            task_id=task.id,
            execution_run_id=run.id,
            attempt_number=attempt_number,
            parent_attempt_id=parent_attempt.id if parent_attempt is not None else None,
            resume_reason=resume_reason,
            checkpoint_json=checkpoint,
            checkpoint_version=(parent_attempt.checkpoint_version if parent_attempt is not None else 0),
        )
        session.add(attempt)
        await session.flush()
        task.current_attempt_id = attempt.id
        run.current_attempt_id = attempt.id
        if page is not None and page.initial_execution_run_id == run.id:
            await TaskRepository(session).append_event(task_id=task.id, attempt_id=attempt.id,
                execution_run_id=run.id, event_type="task.result.allocated",
                payload_json={"result_url": f"/results/{page.public_id}"},
                idempotency_key=f"task.result.allocated:{run.id}")
        return attempt

    async def _current_attempt(self, session: AsyncSession, task: Task) -> TaskAttempt | None:
        if task.current_attempt_id is None:
            return None
        return await session.get(TaskAttempt, task.current_attempt_id)

    async def _end_current_attempt(self, session: AsyncSession, task: Task, *, reason: str) -> None:
        attempt = await self._current_attempt(session, task)
        if attempt is not None and attempt.ended_at is None:
            attempt.ended_at = utc_now()
            attempt.terminal_reason = reason

    async def _event_by_key(self, session: AsyncSession, idempotency_key: str) -> TaskEvent | None:
        result = await session.execute(
            select(TaskEvent).where(TaskEvent.idempotency_key == idempotency_key).limit(1)
        )
        return result.scalar_one_or_none()

    async def _queue_position(self, session: AsyncSession, task: Task) -> int | None:
        if task.status != "queued":
            return None
        priority = {"high": 0, "normal": 1, "low": 2}
        queued = list((await session.execute(
            select(Task).where(Task.status == "queued")
        )).scalars().all())
        queued.sort(
            key=lambda item: (
                priority.get(item.importance, 1),
                item.queue_entered_at,
                item.creation_order,
                item.id,
            )
        )
        return next((index for index, item in enumerate(queued, start=1) if item.id == task.id), None)

    async def _worker_health(
        self,
        session: AsyncSession,
        *,
        now: datetime | None = None,
    ) -> WorkerHealth:
        result = await session.execute(
            select(WorkerInstance)
            .where(
                WorkerInstance.stopped_at.is_(None),
                WorkerInstance.state.in_(["starting", "idle", "busy"]),
            )
            .order_by(WorkerInstance.heartbeat_at.desc())
            .limit(1)
        )
        worker = result.scalar_one_or_none()
        if worker is None:
            return WorkerHealth(status="offline", worker_id=None, heartbeat_at=None)
        heartbeat = worker.heartbeat_at
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=timezone.utc)
        status = (
            "online"
            if heartbeat >= (now or utc_now()) - timedelta(seconds=settings.worker_stale_after_seconds)
            and worker.stopped_at is None
            else "offline"
        )
        return WorkerHealth(status=status, worker_id=worker.id, heartbeat_at=worker.heartbeat_at)

    async def _assign_worker_eligibility(
        self,
        session: AsyncSession,
        task: Task,
        *,
        repo: TaskRepository,
        idempotency_key: str,
    ) -> None:
        if task.status != "queued":
            return
        if (await self._worker_health(session)).status == "online":
            task.status_reason = None
            return
        task.status = "waiting_for_worker"
        task.status_reason = "No live worker is available; this task has not started."
        task.updated_at = utc_now()
        await repo.append_event(
            task_id=task.id,
            attempt_id=task.current_attempt_id,
            event_type="task.waiting_for_worker",
            payload_json={"status": task.status, "reason": task.status_reason},
            idempotency_key=idempotency_key,
        )


task_service = TaskService()
