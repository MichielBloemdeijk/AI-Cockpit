"""Single-worker registration, strict FIFO claiming, and typed leases."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, func, select, update

from app.config import settings
from app.db.repositories.tasks import TaskRepository
from app.db.session import session_scope
from app.db.tables import (
    ResourceLease,
    Task,
    TaskAttempt,
    TaskEvent,
    TaskTarget,
    WorkerInstance,
)


ACTIVE_WORKER_STATES = {"starting", "idle", "busy"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class WorkerRegistration:
    worker_id: str
    took_over: tuple[str, ...]
    promoted: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ClaimedTask:
    task_id: str
    attempt_id: str
    attempt_number: int
    goal: str
    origin_surface: str
    origin_conversation_id: str | None
    origin_turn_id: str | None
    workspace_path: str
    checkpoint: dict | None
    runtime_reference: str | None
    targets: tuple[tuple[str, str, bool], ...]


@dataclass(frozen=True, slots=True)
class ClaimResult:
    task: ClaimedTask | None
    blocked_task_id: str | None = None


class WorkerCoordinator:
    async def activate(
        self,
        *,
        process_identity: str,
        worker_id: str | None = None,
        now: datetime | None = None,
    ) -> WorkerRegistration | None:
        timestamp = now or utc_now()
        candidate_id = worker_id or str(uuid4())
        cutoff = timestamp - timedelta(seconds=settings.worker_stale_after_seconds)
        async with session_scope() as session:
            # Inserting the candidate obtains SQLite's writer lock. Competing
            # registrations then observe the committed winner instead of both
            # making a decision from the same read snapshot.
            candidate = WorkerInstance(
                id=candidate_id,
                state="starting",
                process_identity=process_identity,
                heartbeat_at=timestamp,
                metadata_json={"protocol": 1},
            )
            session.add(candidate)
            await session.flush()

            active_result = await session.execute(
                select(WorkerInstance)
                .where(
                    WorkerInstance.id != candidate_id,
                    WorkerInstance.stopped_at.is_(None),
                    WorkerInstance.state.in_(sorted(ACTIVE_WORKER_STATES)),
                )
                .order_by(WorkerInstance.heartbeat_at.desc())
            )
            others = list(active_result.scalars().all())
            live = [worker for worker in others if _aware(worker.heartbeat_at) >= cutoff]
            if live:
                candidate.state = "rejected"
                candidate.stopped_at = timestamp
                return None

            took_over: list[str] = []
            for stale_worker in others:
                reconciled = await self._reconcile_stale_worker(
                    session,
                    stale_worker,
                    timestamp=timestamp,
                )
                took_over.extend(reconciled)
            candidate.state = "idle"
            candidate.heartbeat_at = timestamp
            promoted = await self._promote_waiting_tasks(
                session,
                timestamp=timestamp,
                worker_id=candidate_id,
            )
            registration = WorkerRegistration(
                worker_id=candidate_id,
                took_over=tuple(took_over),
                promoted=tuple(promoted),
            )
        return registration

    async def heartbeat(
        self,
        worker_id: str,
        *,
        state: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        timestamp = now or utc_now()
        values: dict[str, object] = {"heartbeat_at": timestamp}
        if state is not None:
            values["state"] = state
        async with session_scope() as session:
            result = await session.execute(
                update(WorkerInstance)
                .where(
                    WorkerInstance.id == worker_id,
                    WorkerInstance.stopped_at.is_(None),
                    WorkerInstance.state.in_(sorted(ACTIVE_WORKER_STATES)),
                )
                .values(**values)
            )
            return result.rowcount == 1

    async def stop(self, worker_id: str, *, now: datetime | None = None) -> None:
        timestamp = now or utc_now()
        async with session_scope() as session:
            await session.execute(
                update(WorkerInstance)
                .where(WorkerInstance.id == worker_id, WorkerInstance.stopped_at.is_(None))
                .values(state="stopped", stopped_at=timestamp, heartbeat_at=timestamp)
            )
        await self.reconcile_availability(now=timestamp)

    async def reconcile_availability(self, *, now: datetime | None = None) -> tuple[int, int]:
        """Reconcile stale workers and tasks waiting for executable capacity."""
        timestamp = now or utc_now()
        cutoff = timestamp - timedelta(seconds=settings.worker_stale_after_seconds)
        interrupted = 0
        changed = 0
        async with session_scope() as session:
            workers = list((await session.execute(
                select(WorkerInstance)
                .where(
                    WorkerInstance.stopped_at.is_(None),
                    WorkerInstance.state.in_(sorted(ACTIVE_WORKER_STATES)),
                )
                .order_by(WorkerInstance.heartbeat_at.desc())
            )).scalars().all())
            live = [worker for worker in workers if _aware(worker.heartbeat_at) >= cutoff]
            for worker in workers:
                if worker not in live:
                    interrupted += len(await self._reconcile_stale_worker(
                        session,
                        worker,
                        timestamp=timestamp,
                    ))
            if live:
                changed = len(await self._promote_waiting_tasks(
                    session,
                    timestamp=timestamp,
                    worker_id=live[0].id,
                ))
            else:
                changed = len(await self._hold_queued_tasks(session, timestamp=timestamp))
        return interrupted, changed

    async def claim_next(
        self,
        worker_id: str,
        *,
        now: datetime | None = None,
    ) -> ClaimResult:
        timestamp = now or utc_now()
        lease_expiry = timestamp + timedelta(seconds=settings.worker_lease_ttl_seconds)
        async with session_scope() as session:
            locked = await session.execute(
                update(WorkerInstance)
                .where(
                    WorkerInstance.id == worker_id,
                    WorkerInstance.stopped_at.is_(None),
                    WorkerInstance.state.in_(["idle", "starting"]),
                )
                .values(heartbeat_at=timestamp)
            )
            if locked.rowcount != 1:
                return ClaimResult(task=None)

            await self._promote_waiting_tasks(
                session,
                timestamp=timestamp,
                worker_id=worker_id,
            )

            await session.execute(delete(ResourceLease).where(ResourceLease.expires_at <= timestamp))
            task_result = await session.execute(
                select(Task)
                .where(Task.status == "queued")
                .order_by(Task.creation_order.asc(), Task.id.asc())
                .limit(1)
            )
            task = task_result.scalar_one_or_none()
            if task is None:
                await session.execute(
                    update(WorkerInstance).where(WorkerInstance.id == worker_id).values(state="idle")
                )
                return ClaimResult(task=None)

            attempt = await self._current_or_new_attempt(session, task)
            targets_result = await session.execute(
                select(TaskTarget)
                .where(TaskTarget.task_id == task.id)
                .order_by(TaskTarget.resource_type.asc(), TaskTarget.resource_key.asc())
            )
            targets = list(targets_result.scalars().all())
            mutable_keys = {
                (target.resource_type, target.resource_key)
                for target in targets
                if target.mutable
            }
            leases_result = await session.execute(
                select(ResourceLease).where(ResourceLease.expires_at > timestamp)
            )
            leases = list(leases_result.scalars().all())
            blocked = any(
                (lease.resource_type, lease.resource_key) in mutable_keys
                and lease.owner_attempt_id != attempt.id
                for lease in leases
            )
            if blocked:
                await session.execute(
                    update(WorkerInstance).where(WorkerInstance.id == worker_id).values(state="idle")
                )
                return ClaimResult(task=None, blocked_task_id=task.id)

            workspace = self._workspace_path(task.id, attempt.attempt_number)
            workspace.mkdir(parents=True, exist_ok=True)
            for resource_type, resource_key in sorted(mutable_keys):
                session.add(
                    ResourceLease(
                        id=str(uuid4()),
                        resource_type=resource_type,
                        resource_key=resource_key,
                        owner_attempt_id=attempt.id,
                        acquired_at=timestamp,
                        renewed_at=timestamp,
                        expires_at=lease_expiry,
                    )
                )

            attempt.worker_id = worker_id
            attempt.workspace_path = str(workspace)
            attempt.claimed_at = timestamp
            attempt.started_at = timestamp
            attempt.heartbeat_at = timestamp
            if not attempt.agent_runtime_reference:
                attempt.agent_runtime_reference = f"worker:{worker_id}"
            task.status = "running"
            task.status_reason = None
            task.updated_at = timestamp
            worker_result = await session.execute(
                select(WorkerInstance).where(WorkerInstance.id == worker_id).limit(1)
            )
            worker = worker_result.scalar_one()
            worker.state = "busy"
            worker.heartbeat_at = timestamp
            await TaskRepository(session).append_event(
                task_id=task.id,
                attempt_id=attempt.id,
                event_type="task.claimed",
                payload_json={
                    "status": "running",
                    "worker_id": worker_id,
                    "attempt_number": attempt.attempt_number,
                    "workspace_path": str(workspace),
                },
                idempotency_key=f"task.claimed:{attempt.id}",
            )
            return ClaimResult(
                task=ClaimedTask(
                    task_id=task.id,
                    attempt_id=attempt.id,
                    attempt_number=attempt.attempt_number,
                    goal=task.goal,
                    origin_surface=task.origin_surface,
                    origin_conversation_id=task.origin_conversation_id,
                    origin_turn_id=task.origin_turn_id,
                    workspace_path=str(workspace),
                    checkpoint=attempt.checkpoint_json,
                    runtime_reference=attempt.agent_runtime_reference,
                    targets=tuple(
                        (target.resource_type, target.resource_key, target.mutable)
                        for target in targets
                    ),
                )
            )

    async def renew_claim(
        self,
        worker_id: str,
        attempt_id: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        timestamp = now or utc_now()
        lease_expiry = timestamp + timedelta(seconds=settings.worker_lease_ttl_seconds)
        async with session_scope() as session:
            result = await session.execute(
                update(TaskAttempt)
                .where(
                    TaskAttempt.id == attempt_id,
                    TaskAttempt.worker_id == worker_id,
                    TaskAttempt.ended_at.is_(None),
                )
                .values(heartbeat_at=timestamp)
            )
            if result.rowcount != 1:
                return False
            await session.execute(
                update(ResourceLease)
                .where(ResourceLease.owner_attempt_id == attempt_id)
                .values(renewed_at=timestamp, expires_at=lease_expiry)
            )
            await session.execute(
                update(WorkerInstance)
                .where(WorkerInstance.id == worker_id, WorkerInstance.stopped_at.is_(None))
                .values(heartbeat_at=timestamp, state="busy")
            )
            return True

    async def release_claim(self, worker_id: str, attempt_id: str) -> None:
        async with session_scope() as session:
            await session.execute(delete(ResourceLease).where(ResourceLease.owner_attempt_id == attempt_id))
            await session.execute(
                update(WorkerInstance)
                .where(WorkerInstance.id == worker_id, WorkerInstance.stopped_at.is_(None))
                .values(state="idle", heartbeat_at=utc_now())
            )

    async def checkpoint_and_requeue(
        self,
        *,
        worker_id: str,
        task_id: str,
        attempt_id: str,
        checkpoint: dict,
        idempotency_key: str,
        expanded_targets: tuple[tuple[str, str, bool], ...] = (),
    ) -> str:
        async with session_scope() as session:
            event_key = f"task.requeue:{task_id}:{idempotency_key}"
            prior_event = (await session.execute(
                select(TaskEvent).where(TaskEvent.idempotency_key == event_key).limit(1)
            )).scalar_one_or_none()
            if prior_event is not None:
                prior_attempt_id = str((prior_event.payload_json or {}).get("attempt_id") or prior_event.attempt_id or "")
                if prior_attempt_id:
                    return prior_attempt_id
            task_result = await session.execute(
                update(Task).where(Task.id == task_id).values(status=Task.status)
            )
            if task_result.rowcount != 1:
                raise ValueError("Task not found")
            task = (await session.execute(select(Task).where(Task.id == task_id).limit(1))).scalar_one()
            existing = (await session.execute(
                select(TaskAttempt)
                .where(TaskAttempt.task_id == task_id)
                .order_by(TaskAttempt.attempt_number.desc())
                .limit(1)
            )).scalar_one()
            if existing.id != attempt_id or existing.worker_id != worker_id or task.status != "running":
                raise ValueError("Task attempt is no longer claimable")

            normalized_expansions = {
                (resource_type.strip(), resource_key.strip()): bool(mutable)
                for resource_type, resource_key, mutable in expanded_targets
            }
            for (normalized_type, normalized_key), mutable in sorted(normalized_expansions.items()):
                if not normalized_type or not normalized_key:
                    raise ValueError("Expanded targets require a resource type and key")
                found = (await session.execute(
                    select(TaskTarget).where(
                        TaskTarget.task_id == task_id,
                        TaskTarget.resource_type == normalized_type,
                        TaskTarget.resource_key == normalized_key,
                    ).limit(1)
                )).scalar_one_or_none()
                if found is None:
                    session.add(TaskTarget(
                        id=str(uuid4()),
                        task_id=task_id,
                        resource_type=normalized_type,
                        resource_key=normalized_key,
                        mutable=mutable,
                    ))

            timestamp = utc_now()
            existing.checkpoint_json = checkpoint
            existing.ended_at = timestamp
            existing.terminal_reason = "target_expansion" if expanded_targets else "checkpoint"
            await session.execute(delete(ResourceLease).where(ResourceLease.owner_attempt_id == attempt_id))
            next_attempt = TaskAttempt(
                id=str(uuid4()),
                task_id=task_id,
                attempt_number=existing.attempt_number + 1,
                checkpoint_json=checkpoint,
            )
            session.add(next_attempt)
            await session.flush()
            task.status = "queued"
            task.status_reason = None
            task.current_attempt_id = next_attempt.id
            task.updated_at = timestamp
            await TaskRepository(session).append_event(
                task_id=task_id,
                attempt_id=next_attempt.id,
                event_type="task.requeued",
                payload_json={
                    "status": "queued",
                    "attempt_number": next_attempt.attempt_number,
                    "reason": existing.terminal_reason,
                    "expanded_targets": [
                        {"resource_type": item[0], "resource_key": item[1], "mutable": mutable}
                        for item, mutable in sorted(normalized_expansions.items())
                    ],
                    "attempt_id": next_attempt.id,
                },
                idempotency_key=event_key,
            )
            await session.execute(
                update(WorkerInstance)
                .where(WorkerInstance.id == worker_id, WorkerInstance.stopped_at.is_(None))
                .values(state="idle", heartbeat_at=timestamp)
            )
            return next_attempt.id

    async def _current_or_new_attempt(self, session, task: Task) -> TaskAttempt:
        attempt = await session.get(TaskAttempt, task.current_attempt_id) if task.current_attempt_id else None
        if attempt is not None and attempt.ended_at is None and attempt.worker_id is None:
            return attempt
        max_number = (await session.execute(
            select(func.max(TaskAttempt.attempt_number)).where(TaskAttempt.task_id == task.id)
        )).scalar_one_or_none()
        attempt = TaskAttempt(
            id=str(uuid4()),
            task_id=task.id,
            attempt_number=int(max_number or 0) + 1,
        )
        session.add(attempt)
        await session.flush()
        task.current_attempt_id = attempt.id
        return attempt

    async def _reconcile_stale_worker(
        self,
        session,
        worker: WorkerInstance,
        *,
        timestamp: datetime,
    ) -> list[str]:
        attempts_result = await session.execute(
            select(TaskAttempt, Task)
            .join(Task, Task.current_attempt_id == TaskAttempt.id)
            .where(
                TaskAttempt.worker_id == worker.id,
                TaskAttempt.ended_at.is_(None),
                Task.status == "running",
            )
        )
        interrupted: list[str] = []
        repo = TaskRepository(session)
        for attempt, task in attempts_result.all():
            attempt.ended_at = timestamp
            attempt.terminal_reason = "worker_stale"
            task.status = "interrupted"
            task.finished_at = timestamp
            task.updated_at = timestamp
            task.terminal_summary = "Worker heartbeat was lost; the task was not retried."
            await session.execute(delete(ResourceLease).where(ResourceLease.owner_attempt_id == attempt.id))
            await repo.append_event(
                task_id=task.id,
                attempt_id=attempt.id,
                event_type="task.interrupted",
                payload_json={"status": "interrupted", "reason": "worker_stale", "worker_id": worker.id},
                idempotency_key=f"task.worker-stale:{attempt.id}",
            )
            interrupted.append(task.id)
        worker.state = "stale"
        worker.stopped_at = timestamp
        return interrupted

    @staticmethod
    async def _promote_waiting_tasks(
        session,
        *,
        timestamp: datetime,
        worker_id: str,
    ) -> list[str]:
        tasks = list((await session.execute(
            select(Task)
            .where(Task.status == "waiting_for_worker")
            .order_by(Task.creation_order.asc())
        )).scalars().all())
        repo = TaskRepository(session)
        promoted: list[str] = []
        for task in tasks:
            task.status = "queued"
            task.status_reason = None
            task.updated_at = timestamp
            await repo.append_event(
                task_id=task.id,
                attempt_id=task.current_attempt_id,
                event_type="task.worker_available",
                payload_json={"status": "queued", "worker_id": worker_id},
                idempotency_key=(
                    f"task.worker-available:{task.current_attempt_id or task.id}:{worker_id}"
                ),
            )
            promoted.append(task.id)
        return promoted

    @staticmethod
    async def _hold_queued_tasks(session, *, timestamp: datetime) -> list[str]:
        tasks = list((await session.execute(
            select(Task)
            .where(Task.status == "queued")
            .order_by(Task.creation_order.asc())
        )).scalars().all())
        repo = TaskRepository(session)
        held: list[str] = []
        for task in tasks:
            task.status = "waiting_for_worker"
            task.status_reason = "No live worker is available; this task has not started."
            task.updated_at = timestamp
            await repo.append_event(
                task_id=task.id,
                attempt_id=task.current_attempt_id,
                event_type="task.waiting_for_worker",
                payload_json={"status": task.status, "reason": task.status_reason},
                idempotency_key=(
                    f"task.worker-unavailable:{task.current_attempt_id or task.id}:"
                    f"{timestamp.isoformat()}"
                ),
            )
            held.append(task.id)
        return held

    def _workspace_path(self, task_id: str, attempt_number: int) -> Path:
        return settings.task_workspaces_path / task_id / f"attempt-{attempt_number}"


worker_coordinator = WorkerCoordinator()
