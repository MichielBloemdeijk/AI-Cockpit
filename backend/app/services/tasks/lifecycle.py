"""Task, execution-run and attempt transitions.

This module owns how a task moves between statuses. Each operation writes the
task, its current execution run and the affected attempt together, so callers
never keep a task/run pair in step by hand. Every function runs inside the
caller's write session; callers take the task write lock with ``lock_task``
first and supply their own evidence (a control command, an observed process
exit, an elapsed wait).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.repositories.tasks import TaskRepository
from app.db.tables import (
    ResourceLease,
    Task,
    TaskAttempt,
    TaskContextRequest,
    TaskControlCommand,
    TaskExecutionRun,
    TaskSupervisorLease,
    TaskTurn,
    WorkerInstance,
)
from app.services.tasks.vocabulary import (
    IMMEDIATE_CONTROLS,
    RETRYABLE_TASK_STATUSES,
    STOPPING_CONTROLS,
    TASK_IMPORTANCE,
    TERMINAL_TASK_STATUSES,
    WAITING_TO_RUN_STATUSES,
    ControlAction,
    ControlState,
    RunStatus,
    TaskStatus,
)
from app.timestamps import utc_now

NO_WORKER_REASON = "No live worker is available; this task has not started."
CANCELLED_SUMMARY = "Task cancelled by user."


@dataclass(frozen=True)
class WorkerHealth:
    status: str
    worker_id: str | None
    heartbeat_at: datetime | None


# --- Lock, fence and lookups ------------------------------------------------

async def lock_task(session: AsyncSession, task_id: str) -> Task:
    """Take the task write lock (a no-op update) and return the task."""
    result = cast(CursorResult, await session.execute(
        update(Task).where(Task.id == task_id).values(status=Task.status)))
    if result.rowcount != 1:
        raise ValueError(f"Task not found: {task_id}")
    return (await session.execute(select(Task).where(Task.id == task_id).limit(1))).scalar_one()


async def assert_fence(
    session: AsyncSession,
    task: Task,
    *,
    attempt_id: str | None,
    execution_run_id: str | None,
    claim_token: str | None,
) -> None:
    """Reject a worker write unless it holds the current attempt's claim.

    Unfenced writes (no identity at all) come from the owner or the API and pass.
    """
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
    if supervisor is not None and attempt.supervisor_generation != supervisor.generation:
        raise ValueError("Stale task supervisor generation")


async def current_run(session: AsyncSession, task: Task) -> TaskExecutionRun | None:
    if task.current_run_id is None:
        return None
    return await session.get(TaskExecutionRun, task.current_run_id)


async def current_attempt(session: AsyncSession, task: Task) -> TaskAttempt | None:
    if task.current_attempt_id is None:
        return None
    return await session.get(TaskAttempt, task.current_attempt_id)


# --- Attempts ------------------------------------------------------------------

def inherited_checkpoint(attempt: TaskAttempt | None) -> dict | None:
    return dict(attempt.checkpoint_json or {}) if attempt is not None else None


async def create_attempt(
    session: AsyncSession,
    task: Task,
    run: TaskExecutionRun,
    *,
    parent: TaskAttempt | None = None,
    resume_reason: str | None = None,
    checkpoint: dict | None = None,
    recovery_metadata: dict | None = None,
    workspace_path: str | None = None,
) -> TaskAttempt:
    """Create the next attempt of ``run`` and make it current on task and run."""
    highest = (await session.execute(
        select(func.max(TaskAttempt.attempt_number)).where(TaskAttempt.task_id == task.id)
    )).scalar_one_or_none()
    attempt = TaskAttempt(
        id=str(uuid4()),
        task_id=task.id,
        execution_run_id=run.id,
        attempt_number=int(highest or 0) + 1,
        parent_attempt_id=parent.id if parent is not None else None,
        resume_reason=resume_reason,
        checkpoint_json=checkpoint,
        checkpoint_version=parent.checkpoint_version if parent is not None else 0,
        recovery_metadata_json=recovery_metadata,
        workspace_path=workspace_path,
    )
    session.add(attempt)
    await session.flush()
    task.current_attempt_id = attempt.id
    run.current_attempt_id = attempt.id
    return attempt


async def end_attempt(
    session: AsyncSession,
    attempt: TaskAttempt,
    *,
    reason: str,
    now: datetime,
    stop_evidence: dict | None = None,
    release_leases: bool = False,
) -> None:
    """End an attempt. Leases are released only with proof its writers stopped."""
    if attempt.ended_at is None:
        attempt.ended_at = now
        attempt.terminal_reason = reason
    if stop_evidence is not None:
        attempt.recovery_metadata_json = {
            **dict(attempt.recovery_metadata_json or {}), "stop_evidence": dict(stop_evidence),
        }
    if release_leases:
        await session.execute(delete(ResourceLease).where(ResourceLease.owner_attempt_id == attempt.id))


async def end_current_attempt(session: AsyncSession, task: Task, *, reason: str, now: datetime | None = None) -> None:
    attempt = await current_attempt(session, task)
    if attempt is not None:
        await end_attempt(session, attempt, reason=reason, now=now or utc_now())


# --- Status transitions ----------------------------------------------------------
# Each sets the task and its run together. ``run`` is None only for tasks
# without an execution run (a reminder repaired before it ran).

def requeue(task: Task, run: TaskExecutionRun | None, *, now: datetime, reason: str | None = None) -> None:
    """Queue the task's current run again, clearing any earlier outcome."""
    task.status = TaskStatus.queued
    task.status_reason = reason
    task.terminal_summary = None
    task.finished_at = None
    task.cancellation_requested_at = None
    task.queue_entered_at = now
    task.updated_at = now
    if run is not None:
        run.status = RunStatus.queued
        run.queue_entered_at = now
        run.finished_at = None


def refresh_queue_position(task: Task, run: TaskExecutionRun | None, *, now: datetime) -> None:
    """Move a waiting task to the back of its importance band."""
    task.queue_entered_at = now
    if run is not None:
        run.queue_entered_at = now


def start(task: Task, run: TaskExecutionRun, *, now: datetime) -> None:
    task.status = TaskStatus.running
    task.status_reason = None
    task.updated_at = now
    run.status = RunStatus.running
    run.started_at = run.started_at or now


def pause(task: Task, run: TaskExecutionRun | None, *, now: datetime, reason: str | None = None) -> None:
    task.status = TaskStatus.paused
    task.status_reason = reason
    task.updated_at = now
    if run is not None:
        run.status = RunStatus.paused


def wait_for_input(task: Task, run: TaskExecutionRun | None, *, now: datetime, reason: str | None = None) -> None:
    task.status = TaskStatus.waiting_for_input
    task.status_reason = reason
    task.updated_at = now
    if run is not None:
        run.status = RunStatus.waiting_for_input


def wait_until(task: Task, run: TaskExecutionRun | None, *, due: datetime, reason: str, now: datetime) -> None:
    task.status = TaskStatus.waiting_until
    task.waiting_until = due
    task.resume_at = due
    task.wait_reason = reason
    task.updated_at = now
    if run is not None:
        run.status = RunStatus.waiting_until


def end_wait(task: Task) -> None:
    task.waiting_until = None
    task.resume_at = None
    task.wait_reason = None


def finish(
    task: Task,
    run: TaskExecutionRun | None,
    status: TaskStatus,
    *,
    summary: str | None,
    now: datetime,
    reason: str | None = None,
) -> None:
    """Record a terminal outcome on the task and its run."""
    if status not in TERMINAL_TASK_STATUSES:
        raise ValueError(f"Invalid terminal task status: {status}")
    task.status = TaskStatus(status)
    task.status_reason = reason
    task.terminal_summary = summary
    task.finished_at = now
    task.updated_at = now
    if run is not None:
        run.status = RunStatus(status)
        run.terminal_summary = summary
        run.finished_at = now


def fail_pending_run(run: TaskExecutionRun, *, summary: str, now: datetime) -> None:
    """Fail a follow-up run that never started; the task's current run is unaffected."""
    run.status = RunStatus.failed
    run.terminal_summary = summary
    run.finished_at = now


# --- Worker availability -------------------------------------------------------

async def worker_health(session: AsyncSession, *, now: datetime | None = None) -> WorkerHealth:
    worker = (await session.execute(
        select(WorkerInstance)
        .where(WorkerInstance.stopped_at.is_(None), WorkerInstance.state.in_(["starting", "idle", "busy"]))
        .order_by(WorkerInstance.heartbeat_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if worker is None:
        return WorkerHealth(status="offline", worker_id=None, heartbeat_at=None)
    heartbeat = worker.heartbeat_at
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
    fresh = heartbeat >= (now or utc_now()) - timedelta(seconds=settings.worker_stale_after_seconds)
    return WorkerHealth(status="online" if fresh else "offline", worker_id=worker.id, heartbeat_at=worker.heartbeat_at)


def hold_for_worker(task: Task, *, now: datetime) -> None:
    """A queued task waits for a worker; its run stays queued."""
    task.status = TaskStatus.waiting_for_worker
    task.status_reason = NO_WORKER_REASON
    task.updated_at = now


def release_for_worker(task: Task, *, now: datetime) -> None:
    task.status = TaskStatus.queued
    task.status_reason = None
    task.updated_at = now


async def assign_worker_eligibility(
    session: AsyncSession,
    task: Task,
    *,
    idempotency_key: str,
    now: datetime | None = None,
) -> None:
    """Hold a newly queued task when no live worker can start it, and say so."""
    if task.status != TaskStatus.queued:
        return
    if (await worker_health(session)).status == "online":
        task.status_reason = None
        return
    hold_for_worker(task, now=now or utc_now())
    await TaskRepository(session).append_event(
        task_id=task.id,
        attempt_id=task.current_attempt_id,
        event_type="task.waiting_for_worker",
        payload_json={"status": task.status, "reason": task.status_reason},
        idempotency_key=idempotency_key,
    )


# --- Runs ---------------------------------------------------------------------------

async def activate_run(
    session: AsyncSession,
    task: Task,
    run: TaskExecutionRun,
    *,
    goal: str,
    now: datetime,
) -> TaskAttempt:
    """Make ``run`` the task's current run, queued with a fresh first attempt."""
    task.current_run_id = run.id
    task.execution_kind = run.execution_kind
    task.goal = goal
    task.goal_revision += 1
    requeue(task, run, now=now)
    return await create_attempt(session, task, run)


async def promote_next_run(session: AsyncSession, task: Task) -> TaskExecutionRun | None:
    """After a run ends, start the oldest follow-up turn waiting behind it."""
    next_run = (await session.execute(
        select(TaskExecutionRun)
        .where(TaskExecutionRun.task_id == task.id, TaskExecutionRun.status == RunStatus.pending)
        .order_by(TaskExecutionRun.ordinal.asc())
        .limit(1)
    )).scalar_one_or_none()
    if next_run is None:
        return None
    turn = await session.get(TaskTurn, next_run.input_turn_id)
    if turn is None or not turn.content:
        raise RuntimeError("Pending task run has no input turn")
    now = utc_now()
    attempt = await activate_run(session, task, next_run, goal=turn.content, now=now)
    context_pending = (await session.execute(select(TaskContextRequest.id).where(
        TaskContextRequest.execution_run_id == next_run.id,
        TaskContextRequest.state != "ready").limit(1))).scalar_one_or_none()
    if context_pending is not None:
        wait_for_input(task, next_run, now=now, reason="context_upload_pending")
    await TaskRepository(session).append_event(
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
    await assign_worker_eligibility(session, task, idempotency_key=f"task.run.promoted.waiting:{next_run.id}")
    return next_run


# --- Controls -------------------------------------------------------------------

async def record_cancelled(session: AsyncSession, task: Task, run: TaskExecutionRun, *, attempt_id: str | None) -> None:
    await TaskRepository(session).append_event(
        task_id=task.id,
        attempt_id=attempt_id,
        execution_run_id=run.id,
        event_type="task.cancelled",
        payload_json={"status": TaskStatus.cancelled, "summary": task.terminal_summary},
        idempotency_key=f"task.cancelled:{task.id}:{run.id}",
    )


def _confirm(command: TaskControlCommand, *, now: datetime) -> None:
    command.state = ControlState.confirmed
    command.confirmed_at = now


async def apply_control(
    session: AsyncSession,
    task: Task,
    command: TaskControlCommand,
    *,
    parameters: dict[str, Any],
    reason: str | None,
    now: datetime,
) -> None:
    """Apply a new control command to a locked task.

    Controls on a task that is not running take effect at once. A stopping
    control on a running task is only recorded here: it is confirmed by
    ``confirm_stop`` once the attempt's writers have stopped.
    """
    action = ControlAction(command.action)
    status = task.status
    run = await current_run(session, task)

    if action is ControlAction.set_importance:
        importance = str(parameters.get("importance") or "").casefold()
        if importance not in TASK_IMPORTANCE:
            raise ValueError("Task importance must be low, normal, or high")
        task.importance = importance
        if status in WAITING_TO_RUN_STATUSES:
            refresh_queue_position(task, run, now=now)
        _confirm(command, now=now)
    elif action is ControlAction.resume:
        if status != TaskStatus.paused or run is None:
            raise ValueError(f"Task in {status} cannot be resumed")
        previous = await current_attempt(session, task)
        attempt = await create_attempt(
            session, task, run, parent=previous, resume_reason=action, checkpoint=inherited_checkpoint(previous),
        )
        requeue(task, run, now=now)
        command.attempt_id = attempt.id
        _confirm(command, now=now)
    elif action in {ControlAction.retry, ControlAction.restart_execution, ControlAction.restart_from_beginning} \
            and status != TaskStatus.running:
        if action is ControlAction.retry and status not in RETRYABLE_TASK_STATUSES:
            raise ValueError(f"Task in {status} cannot be retried")
        if run is None:
            raise ValueError("Task has no current execution run")
        previous = await current_attempt(session, task)
        if action is ControlAction.restart_from_beginning:
            task.workspace_generation += 1
            run.workspace_generation = task.workspace_generation
            checkpoint = None
        else:
            checkpoint = inherited_checkpoint(previous)
        attempt = await create_attempt(
            session, task, run, parent=previous, resume_reason=action, checkpoint=checkpoint,
        )
        if (
            action is ControlAction.retry
            and previous is not None
            and previous.terminal_reason == "resource_changed"
            and bool(parameters.get("accept_resource_changes"))
        ):
            if not reason:
                raise ValueError("A reason is required to accept changed shared resources")
            attempt.recovery_metadata_json = {"accept_resource_changes": True}
        requeue(task, run, now=now)
        command.attempt_id = attempt.id
        _confirm(command, now=now)
    elif action is ControlAction.yield_ and status in WAITING_TO_RUN_STATUSES:
        refresh_queue_position(task, run, now=now)
        _confirm(command, now=now)
    elif action is ControlAction.suspend and status in WAITING_TO_RUN_STATUSES | {
        TaskStatus.waiting_for_input, TaskStatus.waiting_until,
    }:
        pause(task, run, now=now, reason=reason or "Task suspended.")
        await end_current_attempt(session, task, reason="suspended", now=now)
        _confirm(command, now=now)
    elif action is ControlAction.cancel and status != TaskStatus.running:
        if status in TERMINAL_TASK_STATUSES:
            raise ValueError(f"Task in {status} cannot be cancelled")
        finish(task, run, TaskStatus.cancelled, summary=CANCELLED_SUMMARY, now=now)
        task.cancellation_requested_at = now
        await end_current_attempt(session, task, reason="cancelled", now=now)
        _confirm(command, now=now)
    elif status == TaskStatus.running and action in STOPPING_CONTROLS:
        immediate = bool(parameters.get("immediate", action in IMMEDIATE_CONTROLS))
        command.state = ControlState.stop_requested if immediate else ControlState.requested
        task.pending_control_id = command.id
        task.status_reason = f"{action} requested"
        if action is ControlAction.cancel:
            task.cancellation_requested_at = now
        if immediate:
            # A new claim token fences the running writers out at once.
            attempt = await current_attempt(session, task)
            if attempt is not None:
                attempt.claim_token = str(uuid4())
    else:
        raise ValueError(f"Task in {status} cannot apply {action}")
    task.updated_at = now


async def confirm_stop(
    session: AsyncSession,
    task: Task,
    command: TaskControlCommand,
    attempt: TaskAttempt,
    *,
    evidence: dict[str, Any],
    now: datetime,
    detail: str | None = None,
) -> None:
    """Finish a stopping control once ``evidence`` proves the attempt's writers stopped."""
    action = ControlAction(command.action)
    if action not in STOPPING_CONTROLS:
        raise ValueError(f"Control {action} does not require stop confirmation")
    run = await session.get(TaskExecutionRun, attempt.execution_run_id)
    if run is None:
        raise ValueError("Task has no current execution run")
    await end_attempt(
        session, attempt, reason=f"control:{action}", now=now, stop_evidence=evidence, release_leases=True,
    )
    if action is ControlAction.suspend:
        pause(task, run, now=now)
        command.detail = detail or "writers stopped; task paused"
    elif action is ControlAction.cancel:
        finish(task, run, TaskStatus.cancelled, summary=CANCELLED_SUMMARY, now=now)
        command.detail = detail
    else:
        checkpoint = inherited_checkpoint(attempt)
        if action is ControlAction.restart_from_beginning:
            task.workspace_generation += 1
            run.workspace_generation = task.workspace_generation
            checkpoint = {}
        next_attempt = await create_attempt(
            session, task, run, parent=attempt, resume_reason=action, checkpoint=checkpoint,
        )
        requeue(task, run, now=now)
        verb = "requeued" if action is ControlAction.yield_ else "restarted"
        command.detail = detail or f"{verb} as attempt {next_attempt.attempt_number}"
    task.pending_control_id = None
    _confirm(command, now=now)
    await TaskRepository(session).append_event(
        task_id=task.id,
        attempt_id=attempt.id,
        execution_run_id=run.id,
        event_type="task.control.confirmed",
        payload_json={
            "command_id": command.id,
            "action": command.action,
            "state": ControlState.confirmed,
            "stop_evidence": dict(evidence),
        },
        idempotency_key=f"task.control.confirmed:{command.id}",
    )
    if action is ControlAction.cancel:
        await record_cancelled(session, task, run, attempt_id=attempt.id)
