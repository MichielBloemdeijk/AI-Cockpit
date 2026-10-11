"""Generation-fenced worker pool, prioritized claiming, and directory leases."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import socket
from uuid import uuid4

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import settings
from app.db.repositories.tasks import TaskRepository
from app.db.session import session_scope
from app.db.tables import (
    ResourceLease,
    Task,
    TaskAttempt,
    TaskControlCommand,
    TaskExecutionRun,
    TaskEvent,
    TaskOperation,
    TaskPoolCommand,
    TaskTarget,
    TaskSupervisorLease,
    WorkerInstance,
)
from app.services.windows_job import process_creation_stamp, process_has_exited
from app.services.resource_state import directory_snapshot
from app.services.task_operation_recovery import confirmed_file_write


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
    execution_run_id: str
    claim_token: str
    execution_kind: str
    execution_config: dict
    workspace_generation: int
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


@dataclass(frozen=True, slots=True)
class PoolSnapshot:
    supervisor_generation: int | None
    supervisor_owner: str | None
    supervisor_heartbeat_at: datetime | None
    busy: int
    standby: int
    starting: int
    stale: int
    queue_depth: int
    waiting_for_worker: int

    @property
    def saturated(self) -> bool:
        return self.busy >= settings.worker_max_busy


def _canonical_resource(resource_type: str, resource_key: str) -> tuple[str, str]:
    normalized_type = resource_type.strip().casefold()
    normalized_key = resource_key.strip()
    if normalized_type not in {"directory", "path", "workspace", "repository", "generated_app"}:
        return normalized_type, normalized_key.casefold()
    candidate = Path(normalized_key)
    if not candidate.is_absolute():
        candidate = settings.backend_root.parent / candidate
    try:
        canonical = candidate.resolve(strict=False)
    except OSError:
        canonical = candidate.absolute()
    return "directory", str(canonical).rstrip("\\/").casefold()


def _resources_overlap(left: tuple[str, str], right: tuple[str, str]) -> bool:
    if left[0] != right[0]:
        return False
    if left[0] != "directory":
        return left[1] == right[1]
    try:
        left_path = Path(left[1])
        right_path = Path(right[1])
        return left_path == right_path or left_path in right_path.parents or right_path in left_path.parents
    except (OSError, ValueError):
        return left[1] == right[1]


def _supervisor_owner_has_exited(owner_identity: str) -> bool:
    """Only an exact same-host Windows process identity permits early takeover."""
    if os.name != "nt":
        return False
    parts = owner_identity.split(":", 3)
    if len(parts) != 4 or parts[0] != socket.gethostname():
        return False
    try:
        pid, stamp = int(parts[1]), int(parts[2])
    except ValueError:
        return False
    return pid > 0 and stamp > 0 and process_has_exited(pid, stamp) is True


class WorkerCoordinator:
    async def list_pool_workers(self) -> tuple[WorkerInstance, ...]:
        async with session_scope() as session:
            lease = await session.get(TaskSupervisorLease, "task-pool")
            if lease is None:
                return ()
            return tuple((await session.execute(
                select(WorkerInstance)
                .where(
                    WorkerInstance.supervisor_generation == lease.generation,
                    WorkerInstance.stopped_at.is_(None),
                )
                .order_by(WorkerInstance.started_at.asc(), WorkerInstance.id.asc())
            )).scalars().all())

    async def control_pool(
        self,
        *,
        action: str,
        idempotency_key: str,
        requester: str,
        reason: str,
        worker_id: str | None = None,
    ) -> TaskPoolCommand:
        if action not in {"repair_capacity", "replace_worker", "restart_pool"}:
            raise ValueError("Unsupported pool action")
        if not idempotency_key.strip() or not reason.strip():
            raise ValueError("Pool controls require an idempotency key and reason")
        if action == "replace_worker" and not worker_id:
            raise ValueError("Worker replacement requires a worker ID")
        if action != "replace_worker" and worker_id:
            raise ValueError("This pool action does not target a worker")
        timestamp = utc_now()
        async with session_scope() as session:
            await session.execute(
                sqlite_insert(TaskPoolCommand)
                .values(
                    id=str(uuid4()), idempotency_key=idempotency_key,
                    action=action, target_worker_id=worker_id,
                    requester=requester, reason=reason, state="requested",
                    requested_at=timestamp,
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
            )
            command = (await session.execute(
                select(TaskPoolCommand).where(TaskPoolCommand.idempotency_key == idempotency_key)
            )).scalar_one()
            if (
                command.action != action or command.target_worker_id != worker_id
                or command.requester != requester or command.reason != reason
            ):
                raise ValueError("Pool idempotency key was reused with different input")
            if command.affected_worker_ids_json is not None:
                return command
            lease = await session.get(TaskSupervisorLease, "task-pool")
            workers = list((await session.execute(
                select(WorkerInstance).where(
                    WorkerInstance.stopped_at.is_(None),
                    WorkerInstance.supervisor_generation == (lease.generation if lease else -1),
                )
            )).scalars().all())
            if action == "replace_worker":
                selected = next((item for item in workers if item.id == worker_id), None)
                if selected is None or selected.state not in ACTIVE_WORKER_STATES:
                    raise ValueError("Worker is not active in the current pool generation")
                affected = [selected]
            elif action == "restart_pool":
                affected = [item for item in workers if item.state in ACTIVE_WORKER_STATES]
            else:
                affected = []
            command.affected_worker_ids_json = [item.id for item in affected]
            for worker in affected:
                worker.state = "retiring"
            return command

    async def confirm_pool_commands(self) -> None:
        timestamp = utc_now()
        async with session_scope() as session:
            lease = await session.get(TaskSupervisorLease, "task-pool")
            if lease is None or _aware(lease.expires_at) <= timestamp:
                return
            workers = list((await session.execute(select(WorkerInstance))).scalars().all())
            current = [
                item for item in workers
                if item.supervisor_generation == lease.generation and item.stopped_at is None
            ]
            ready = any(item.state == "idle" for item in current)
            if not ready:
                return
            by_id = {item.id: item for item in workers}
            commands = list((await session.execute(
                select(TaskPoolCommand).where(TaskPoolCommand.state == "requested")
            )).scalars().all())
            for command in commands:
                affected = command.affected_worker_ids_json or []
                if all(by_id.get(worker_id) is not None and by_id[worker_id].stopped_at is not None
                       for worker_id in affected):
                    command.state = "confirmed"
                    command.confirmed_at = timestamp

    async def _snapshots_at_worker_exit(self, worker_id: str) -> dict[str, dict[str, dict]]:
        """Inspect shared targets while their leases still exclude other tasks.

        Hashing happens outside the SQLite write transaction. The caller has
        already observed the writer process tree exit; lease rows remain until
        the reconciliation transaction commits.
        """
        async with session_scope() as session:
            rows = list((await session.execute(
                select(TaskAttempt.id, TaskTarget.id, TaskTarget.resource_type, TaskTarget.resource_key)
                .join(ResourceLease, ResourceLease.owner_attempt_id == TaskAttempt.id)
                .join(TaskTarget, TaskTarget.task_id == TaskAttempt.task_id)
                .where(TaskAttempt.worker_id == worker_id, TaskTarget.mutable.is_(True))
                .distinct()
            )).all())
        snapshots: dict[str, dict[str, dict]] = {}
        for attempt_id, target_id, resource_type, resource_key in rows:
            canonical_type, _ = _canonical_resource(resource_type, resource_key)
            if canonical_type != "directory":
                snapshots.setdefault(attempt_id, {})[target_id] = {
                    "state": "unknown", "reason": "no_resource_observer"
                }
                continue
            path = Path(resource_key)
            if not path.is_absolute():
                path = settings.backend_root.parent / path
            snapshots.setdefault(attempt_id, {})[target_id] = await asyncio.to_thread(
                directory_snapshot, str(path.absolute())
            )
        return snapshots

    async def _file_write_proofs_at_worker_exit(self, worker_id: str) -> dict[str, dict[str, str]]:
        """Read private workspace state after exit, before the reconciliation write lock."""
        async with session_scope() as session:
            rows = list((await session.execute(
                select(TaskOperation.id, TaskOperation.intent_json, TaskAttempt.workspace_path)
                .join(TaskAttempt, TaskAttempt.active_operation == TaskOperation.id)
                .where(
                    TaskAttempt.worker_id == worker_id,
                    TaskAttempt.ended_at.is_(None),
                    TaskOperation.state == "started",
                    TaskOperation.operation_type == "broker.file_write",
                )
            )).all())
        proofs: dict[str, dict[str, str]] = {}
        for operation_id, intent, workspace_path in rows:
            proof = await asyncio.to_thread(confirmed_file_write, workspace_path, intent)
            if proof is not None:
                proofs[operation_id] = proof
        return proofs

    async def verify_reacquired_targets(
        self, claim: ClaimedTask
    ) -> tuple[bool, tuple[dict[str, str], ...]]:
        """Check released shared targets before starting the agent runtime.

        The new attempt already holds its leases, so another pool task cannot
        race this scan. An explicit retry may acknowledge a reported change;
        the changed target list is then passed to the runtime for inspection.
        """
        async with session_scope() as session:
            attempt = await session.get(TaskAttempt, claim.attempt_id)
            if attempt is None or attempt.claim_token != claim.claim_token:
                return False, ()
            previous = list((await session.execute(
                select(TaskAttempt)
                .where(
                    TaskAttempt.task_id == claim.task_id,
                    TaskAttempt.attempt_number < attempt.attempt_number,
                )
                .order_by(TaskAttempt.attempt_number.desc())
            )).scalars().all())
            targets = list((await session.execute(
                select(TaskTarget).where(TaskTarget.task_id == claim.task_id, TaskTarget.mutable.is_(True))
            )).scalars().all())
            accepted = bool((attempt.recovery_metadata_json or {}).get("accept_resource_changes"))
            prior_snapshots: dict[str, dict] = {}
            for older in previous:
                if older.terminal_reason == "resource_changed":
                    continue
                for target_id, snapshot in (older.recovery_metadata_json or {}).get(
                    "released_resource_snapshots", {}
                ).items():
                    prior_snapshots.setdefault(target_id, snapshot)
        changes: list[dict[str, str]] = []
        for target in targets:
            baseline = prior_snapshots.get(target.id)
            if baseline is None:
                continue
            resource_type, _ = _canonical_resource(target.resource_type, target.resource_key)
            if resource_type == "directory":
                path = Path(target.resource_key)
                if not path.is_absolute():
                    path = settings.backend_root.parent / path
                current = await asyncio.to_thread(directory_snapshot, str(path.absolute()))
            else:
                current = {"state": "unknown", "reason": "no_resource_observer"}
            if baseline != current or baseline.get("state") == "unknown":
                changes.append({
                    "resource_type": target.resource_type,
                    "resource_key": target.resource_key,
                    "baseline_state": str(baseline.get("state")),
                    "current_state": str(current.get("state")),
                })
        if not changes:
            return await self.renew_claim(
                attempt.worker_id or "", claim.attempt_id, claim_token=claim.claim_token
            ), ()
        unobservable = any(change["current_state"] == "unknown" for change in changes)
        permitted = accepted and not unobservable
        timestamp = utc_now()
        async with session_scope() as session:
            attempt = await session.get(TaskAttempt, claim.attempt_id)
            task = (await session.execute(
                select(Task).where(Task.id == claim.task_id).limit(1)
            )).scalar_one_or_none()
            if (
                attempt is None or task is None or attempt.claim_token != claim.claim_token
                or task.current_attempt_id != claim.attempt_id or task.status != "running"
            ):
                return False, ()
            metadata = dict(attempt.recovery_metadata_json or {})
            metadata["observed_resource_changes"] = changes
            attempt.recovery_metadata_json = metadata
            if not permitted:
                attempt.ended_at = timestamp
                attempt.terminal_reason = "resource_changed"
                task.status = "interrupted"
                task.finished_at = timestamp
                task.terminal_summary = (
                    "A shared target cannot be checked automatically before reacquisition."
                    if unobservable else
                    "A shared target changed while this task did not hold its lease."
                )
                run = await session.get(TaskExecutionRun, claim.execution_run_id)
                if run is not None:
                    run.status = "interrupted"
                    run.finished_at = timestamp
                    run.terminal_summary = task.terminal_summary
            task.updated_at = timestamp
            await TaskRepository(session).append_event(
                task_id=task.id,
                attempt_id=attempt.id,
                execution_run_id=claim.execution_run_id,
                event_type="task.resource.changed_acknowledged" if permitted else "task.recovery.needed",
                payload_json={
                    "reason": "shared_resource_unobservable" if unobservable else "shared_resource_changed",
                    "accepted": permitted,
                    "targets": changes,
                },
                idempotency_key=f"task.resource-recheck:{attempt.id}",
            )
        return permitted, tuple(changes)

    async def acquire_supervisor(
        self,
        owner_identity: str,
        *,
        now: datetime | None = None,
    ) -> int | None:
        timestamp = now or utc_now()
        expires_at = timestamp + timedelta(seconds=settings.supervisor_lease_ttl_seconds)
        async with session_scope() as session:
            await session.execute(
                sqlite_insert(TaskSupervisorLease)
                .values(
                    lease_key="task-pool",
                    generation=1,
                    owner_identity=owner_identity,
                    heartbeat_at=timestamp,
                    expires_at=expires_at,
                )
                .on_conflict_do_nothing(index_elements=["lease_key"])
            )
            lease = await session.get(TaskSupervisorLease, "task-pool")
            assert lease is not None
            if lease.owner_identity == owner_identity:
                lease.heartbeat_at = timestamp
                lease.expires_at = expires_at
                return lease.generation
            if _aware(lease.expires_at) > timestamp and not _supervisor_owner_has_exited(
                lease.owner_identity
            ):
                return None
            lease.generation += 1
            lease.owner_identity = owner_identity
            lease.heartbeat_at = timestamp
            lease.expires_at = expires_at
            return lease.generation

    async def heartbeat_supervisor(
        self,
        owner_identity: str,
        generation: int,
        *,
        now: datetime | None = None,
    ) -> bool:
        timestamp = now or utc_now()
        async with session_scope() as session:
            result = await session.execute(
                update(TaskSupervisorLease)
                .where(
                    TaskSupervisorLease.lease_key == "task-pool",
                    TaskSupervisorLease.owner_identity == owner_identity,
                    TaskSupervisorLease.generation == generation,
                )
                .values(
                    heartbeat_at=timestamp,
                    expires_at=timestamp + timedelta(seconds=settings.supervisor_lease_ttl_seconds),
                )
            )
            return result.rowcount == 1

    async def release_supervisor(self, owner_identity: str, generation: int) -> None:
        timestamp = utc_now()
        async with session_scope() as session:
            await session.execute(
                update(TaskSupervisorLease)
                .where(
                    TaskSupervisorLease.lease_key == "task-pool",
                    TaskSupervisorLease.owner_identity == owner_identity,
                    TaskSupervisorLease.generation == generation,
                )
                .values(expires_at=timestamp, heartbeat_at=timestamp)
            )

    async def pool_snapshot(self) -> PoolSnapshot:
        async with session_scope() as session:
            lease = await session.get(TaskSupervisorLease, "task-pool")
            workers = list((await session.execute(select(WorkerInstance))).scalars().all())
            queue_depth = int((await session.execute(
                select(func.count()).select_from(Task).where(Task.status == "queued")
            )).scalar_one())
            waiting = int((await session.execute(
                select(func.count()).select_from(Task).where(Task.status == "waiting_for_worker")
            )).scalar_one())
            live = [worker for worker in workers if worker.stopped_at is None]
            return PoolSnapshot(
                supervisor_generation=lease.generation if lease is not None else None,
                supervisor_owner=lease.owner_identity if lease is not None else None,
                supervisor_heartbeat_at=lease.heartbeat_at if lease is not None else None,
                busy=sum(
                    worker.state == "busy"
                    or (worker.state == "retiring" and worker.assignment_attempt_id is not None)
                    for worker in live
                ),
                standby=sum(worker.state == "idle" for worker in live),
                starting=sum(worker.state == "starting" for worker in live),
                stale=sum(worker.state == "stale" for worker in workers),
                queue_depth=queue_depth,
                waiting_for_worker=waiting,
            )

    async def worker_id_for_identity(self, process_identity: str) -> str | None:
        async with session_scope() as session:
            worker = (await session.execute(
                select(WorkerInstance)
                .where(WorkerInstance.process_identity == process_identity)
                .order_by(WorkerInstance.started_at.desc())
                .limit(1)
            )).scalar_one_or_none()
            return worker.id if worker is not None else None

    async def stop_requested_identities(self, generation: int) -> tuple[str, ...]:
        async with session_scope() as session:
            rows = (await session.execute(
                select(WorkerInstance.process_identity)
                .join(TaskAttempt, WorkerInstance.assignment_attempt_id == TaskAttempt.id)
                .join(Task, Task.current_attempt_id == TaskAttempt.id)
                .join(TaskControlCommand, Task.pending_control_id == TaskControlCommand.id)
                .where(
                    WorkerInstance.supervisor_generation == generation,
                    WorkerInstance.stopped_at.is_(None),
                    TaskControlCommand.state == "stop_requested",
                )
            )).scalars().all()
            stale = (await session.execute(
                select(WorkerInstance.process_identity).where(
                    WorkerInstance.supervisor_generation == generation,
                    WorkerInstance.state == "stale",
                )
            )).scalars().all()
            retiring = (await session.execute(
                select(WorkerInstance.process_identity).where(
                    WorkerInstance.supervisor_generation == generation,
                    WorkerInstance.state == "retiring",
                    WorkerInstance.stopped_at.is_(None),
                )
            )).scalars().all()
            return tuple(identity for identity in (*rows, *stale, *retiring) if identity)

    async def retire(self, worker_id: str) -> None:
        """Keep a completed child alive until its process tree is reaped."""
        async with session_scope() as session:
            await session.execute(
                update(WorkerInstance)
                .where(WorkerInstance.id == worker_id, WorkerInstance.stopped_at.is_(None))
                .values(state="retiring", heartbeat_at=utc_now())
            )

    async def activate(
        self,
        *,
        process_identity: str,
        worker_id: str | None = None,
        supervisor_generation: int = 1,
        supervised: bool = False,
        now: datetime | None = None,
    ) -> WorkerRegistration | None:
        timestamp = now or utc_now()
        candidate_id = worker_id or str(uuid4())
        cutoff = timestamp - timedelta(seconds=settings.worker_stale_after_seconds)
        async with session_scope() as session:
            supervisor = await session.get(TaskSupervisorLease, "task-pool")
            if supervisor is not None and supervisor.generation != supervisor_generation:
                return None
            # Inserting the candidate obtains SQLite's writer lock. Competing
            # registrations then observe the committed winner instead of both
            # making a decision from the same read snapshot.
            candidate = WorkerInstance(
                id=candidate_id,
                state="starting",
                process_identity=process_identity,
                supervisor_generation=supervisor_generation,
                verified_at=timestamp,
                heartbeat_at=timestamp,
                metadata_json={
                    "protocol": 2,
                    "pool": "homogeneous",
                    "supervised": supervised,
                    "pid": os.getpid(),
                    "process_creation_stamp": process_creation_stamp(os.getpid()),
                },
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
            live = [
                worker for worker in others
                if _aware(worker.heartbeat_at) >= cutoff
                and worker.supervisor_generation == supervisor_generation
            ]
            busy = [worker for worker in live if worker.state == "busy"]
            idle = [worker for worker in live if worker.state in {"idle", "starting"}]
            if (
                len(idle) >= settings.worker_standby_count
                or len(live) >= settings.worker_max_busy + settings.worker_standby_count
            ):
                candidate.state = "rejected"
                candidate.stopped_at = timestamp
                return None

            took_over: list[str] = []
            for stale_worker in (worker for worker in others if worker not in live):
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

    async def worker_exited(
        self,
        worker_id: str,
        *,
        process_identity: str,
        exit_code: int | None,
        now: datetime | None = None,
    ) -> tuple[str, ...]:
        """Reconcile a directly observed child exit after its process tree stopped."""
        timestamp = now or utc_now()
        recovered: list[str] = []
        snapshots = await self._snapshots_at_worker_exit(worker_id)
        file_write_proofs = await self._file_write_proofs_at_worker_exit(worker_id)
        async with session_scope() as session:
            worker = await session.get(WorkerInstance, worker_id)
            if worker is None or worker.process_identity != process_identity:
                return ()
            for attempt_id, target_snapshots in snapshots.items():
                exited_attempt = await session.get(TaskAttempt, attempt_id)
                if exited_attempt is not None and exited_attempt.worker_id == worker_id:
                    exited_attempt.recovery_metadata_json = {
                        **dict(exited_attempt.recovery_metadata_json or {}),
                        "released_resource_snapshots": target_snapshots,
                    }
            attempts = list((await session.execute(
                select(TaskAttempt, Task)
                .join(Task, Task.current_attempt_id == TaskAttempt.id)
                .where(
                    TaskAttempt.worker_id == worker_id,
                    TaskAttempt.ended_at.is_(None),
                    Task.status == "running",
                )
            )).all())
            repo = TaskRepository(session)
            for attempt, task in attempts:
                command = (
                    await session.get(TaskControlCommand, task.pending_control_id)
                    if task.pending_control_id else None
                )
                if command is not None and command.attempt_id == attempt.id:
                    attempt.ended_at = timestamp
                    attempt.terminal_reason = f"control:{command.action}"
                    attempt.recovery_metadata_json = {
                        **dict(attempt.recovery_metadata_json or {}),
                        "stop_evidence": {
                            "kind": "observed_process_tree_exit",
                            "worker_id": worker_id,
                            "exit_code": exit_code,
                        },
                    }
                    await session.execute(
                        delete(ResourceLease).where(ResourceLease.owner_attempt_id == attempt.id)
                    )
                    run = await session.get(TaskExecutionRun, attempt.execution_run_id)
                    if run is None:
                        raise RuntimeError("Controlled attempt has no execution run")
                    if command.action in {"yield", "restart_execution", "restart_from_beginning"}:
                        if command.action == "restart_from_beginning":
                            task.workspace_generation += 1
                            run.workspace_generation = task.workspace_generation
                            checkpoint = {}
                        else:
                            checkpoint = dict(attempt.checkpoint_json or {})
                        max_number = int((await session.execute(
                            select(func.max(TaskAttempt.attempt_number)).where(TaskAttempt.task_id == task.id)
                        )).scalar_one_or_none() or 0)
                        replacement = TaskAttempt(
                            id=str(uuid4()),
                            task_id=task.id,
                            execution_run_id=run.id,
                            attempt_number=max_number + 1,
                            parent_attempt_id=attempt.id,
                            resume_reason=command.action,
                            checkpoint_json=checkpoint,
                            checkpoint_version=attempt.checkpoint_version,
                        )
                        session.add(replacement)
                        await session.flush()
                        task.status = "queued"
                        task.current_attempt_id = replacement.id
                        task.queue_entered_at = timestamp
                        run.status = "queued"
                        run.current_attempt_id = replacement.id
                        run.queue_entered_at = timestamp
                    elif command.action == "suspend":
                        task.status = "paused"
                        run.status = "paused"
                    elif command.action == "cancel":
                        task.status = "cancelled"
                        task.finished_at = timestamp
                        task.terminal_summary = "Task cancelled by user."
                        run.status = "cancelled"
                        run.finished_at = timestamp
                        run.terminal_summary = task.terminal_summary
                    else:
                        raise RuntimeError(f"Unsupported stopped control: {command.action}")
                    task.pending_control_id = None
                    task.status_reason = None
                    task.updated_at = timestamp
                    command.state = "confirmed"
                    command.confirmed_at = timestamp
                    command.detail = "confirmed after the supervised process tree exited"
                    await repo.append_event(
                        task_id=task.id,
                        attempt_id=attempt.id,
                        execution_run_id=attempt.execution_run_id,
                        event_type="task.control.confirmed",
                        payload_json={
                            "command_id": command.id,
                            "action": command.action,
                            "state": "confirmed",
                            "stop_evidence": "observed_process_tree_exit",
                        },
                        idempotency_key=f"task.control.confirmed:{command.id}",
                    )
                    if command.action == "cancel":
                        await repo.append_event(
                            task_id=task.id,
                            attempt_id=attempt.id,
                            execution_run_id=run.id,
                            event_type="task.cancelled",
                            payload_json={"status": "cancelled", "summary": task.terminal_summary},
                            idempotency_key=f"task.cancelled:{task.id}:{run.id}",
                        )
                    continue
                operation = None
                if attempt.active_operation:
                    operation = await session.get(TaskOperation, attempt.active_operation)
                attempt.ended_at = timestamp
                attempt.terminal_reason = "worker_exit"
                metadata = dict(attempt.recovery_metadata_json or {})
                consecutive = int(metadata.get("consecutive_crashes", 0) or 0)
                if operation is not None and operation.state == "started":
                    proof = file_write_proofs.get(operation.id)
                    if proof is not None:
                        operation.state = "succeeded"
                        operation.result_json = proof
                        operation.error = None
                        await repo.append_event(
                            task_id=task.id,
                            attempt_id=attempt.id,
                            execution_run_id=attempt.execution_run_id,
                            event_type="task.operation.succeeded",
                            payload_json={
                                "operation_id": operation.id,
                                "operation_key": operation.operation_key,
                                "operation_type": operation.operation_type,
                                "result_reference": None,
                                "reconciled_after_exit": True,
                            },
                            idempotency_key=f"task.operation:{operation.id}:succeeded",
                        )
                    else:
                        operation.state = "uncertain"
                        operation.error = "Worker exited after the operation started without a durable result."
                    operation.completed_at = timestamp
                attempt.active_operation = None
                safe = bool(attempt.checkpoint_json) and (
                    operation is None or operation.state == "succeeded"
                ) and consecutive < 1
                run = await session.get(TaskExecutionRun, attempt.execution_run_id)
                if safe and run is not None:
                    max_number = int((await session.execute(
                        select(func.max(TaskAttempt.attempt_number)).where(TaskAttempt.task_id == task.id)
                    )).scalar_one_or_none() or 0)
                    replacement = TaskAttempt(
                        id=str(uuid4()),
                        task_id=task.id,
                        execution_run_id=run.id,
                        attempt_number=max_number + 1,
                        parent_attempt_id=attempt.id,
                        resume_reason="automatic_worker_exit_recovery",
                        checkpoint_json=dict(attempt.checkpoint_json or {}),
                        checkpoint_version=attempt.checkpoint_version,
                        recovery_metadata_json={
                            "consecutive_crashes": consecutive + 1,
                            "previous_exit_code": exit_code,
                        },
                        workspace_path=attempt.workspace_path,
                    )
                    session.add(replacement)
                    await session.flush()
                    await session.execute(
                        update(ResourceLease)
                        .where(ResourceLease.owner_attempt_id == attempt.id)
                        .values(
                            owner_attempt_id=replacement.id,
                            claim_token=None,
                            writer_identity=None,
                            release_evidence_json={
                                "kind": "observed_process_exit",
                                "worker_id": worker_id,
                                "exit_code": exit_code,
                            },
                        )
                    )
                    task.status = "queued"
                    task.status_reason = "Recovering from an observed worker exit."
                    task.current_attempt_id = replacement.id
                    task.queue_entered_at = timestamp
                    run.status = "queued"
                    run.current_attempt_id = replacement.id
                    run.queue_entered_at = timestamp
                    await repo.append_event(
                        task_id=task.id,
                        attempt_id=replacement.id,
                        execution_run_id=run.id,
                        event_type="task.recovery.queued",
                        payload_json={
                            "previous_attempt_id": attempt.id,
                            "attempt_id": replacement.id,
                            "reason": "observed_worker_exit",
                        },
                        idempotency_key=f"task.worker-exit-recovery:{attempt.id}",
                    )
                    recovered.append(task.id)
                else:
                    task.status = "interrupted"
                    task.finished_at = timestamp
                    task.terminal_summary = (
                        "Worker exited with an uncertain operation outcome."
                        if operation is not None else
                        "Worker exited before a safe recovery checkpoint was available."
                    )
                    if run is not None:
                        run.status = "interrupted"
                        run.finished_at = timestamp
                        run.terminal_summary = task.terminal_summary
                    await session.execute(
                        delete(ResourceLease).where(ResourceLease.owner_attempt_id == attempt.id)
                    )
                    await repo.append_event(
                        task_id=task.id,
                        attempt_id=attempt.id,
                        execution_run_id=attempt.execution_run_id,
                        event_type="task.recovery.needed",
                        payload_json={
                            "reason": "uncertain_operation" if operation is not None else "no_checkpoint",
                            "exit_code": exit_code,
                        },
                        idempotency_key=f"task.worker-exit-interrupted:{attempt.id}",
                    )
                task.updated_at = timestamp
            worker.state = "stopped"
            worker.stopped_at = timestamp
            worker.heartbeat_at = timestamp
            worker.assignment_attempt_id = None
            # A heartbeat-stale attempt may already have been marked
            # interrupted.  Its lease is still held until this exact child
            # process tree has been observed to exit.
            await session.execute(
                delete(ResourceLease).where(
                    ResourceLease.owner_attempt_id.in_(
                        select(TaskAttempt.id).where(TaskAttempt.worker_id == worker_id)
                    )
                )
            )
        from app.services.runtime_auth_routes import release_stopped_owner
        await release_stopped_owner(owner_kind="worker", owner_id=worker_id)
        return tuple(recovered)

    async def reconcile_availability(self, *, now: datetime | None = None) -> tuple[int, int]:
        """Reconcile stale workers and tasks waiting for executable capacity."""
        timestamp = now or utc_now()
        cutoff = timestamp - timedelta(seconds=settings.worker_stale_after_seconds)
        if os.name == "nt":
            async with session_scope() as discovery:
                supervisor = await discovery.get(TaskSupervisorLease, "task-pool")
                candidates = list((await discovery.execute(
                    select(WorkerInstance).where(
                        WorkerInstance.state.in_(["starting", "idle", "busy", "retiring", "stale"]),
                    )
                )).scalars().all())
            for candidate in candidates:
                old_generation = (
                    supervisor is not None
                    and candidate.supervisor_generation != supervisor.generation
                )
                if (
                    _aware(candidate.heartbeat_at) >= cutoff
                    and candidate.state not in {"stale", "retiring"}
                    and not old_generation
                ):
                    continue
                metadata = candidate.metadata_json or {}
                pid = metadata.get("pid")
                stamp = metadata.get("process_creation_stamp")
                if (
                    isinstance(pid, int) and isinstance(stamp, int)
                    and process_has_exited(pid, stamp) is True
                    and candidate.process_identity
                ):
                    await self.worker_exited(
                        candidate.id,
                        process_identity=candidate.process_identity,
                        exit_code=None,
                        now=timestamp,
                    )
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
        self, worker_id: str, *, now: datetime | None = None,
    ) -> ClaimResult:
        """In-process legacy/fixture boundary. Supervised children cannot claim."""
        async with session_scope() as session:
            worker = await session.get(WorkerInstance, worker_id)
            if worker is not None and (worker.metadata_json or {}).get("supervised"):
                raise ValueError("Supervised children execute supervisor assignments only")
        return await self._assign_next(worker_id, now=now)

    async def assign_next(
        self, worker_id: str, *, owner_identity: str, generation: int,
        now: datetime | None = None,
    ) -> ClaimResult:
        return await self._assign_next(
            worker_id, owner_identity=owner_identity, generation=generation, now=now,
        )

    async def take_assignment(self, worker_id: str) -> ClaimResult:
        """Acknowledge an already committed assignment, without touching the queue."""
        timestamp = utc_now()
        async with session_scope() as session:
            locked = await session.execute(update(WorkerInstance).where(
                WorkerInstance.id == worker_id, WorkerInstance.state == "busy",
                WorkerInstance.stopped_at.is_(None),
            ).values(heartbeat_at=timestamp))
            if locked.rowcount != 1:
                return ClaimResult(task=None)
            worker = await session.get(WorkerInstance, worker_id)
            supervisor = await session.get(TaskSupervisorLease, "task-pool")
            if (worker is None or supervisor is None
                    or supervisor.generation != worker.supervisor_generation
                    or _aware(supervisor.expires_at) <= timestamp):
                return ClaimResult(task=None)
            metadata = dict(worker.metadata_json or {})
            if metadata.get("assignment_taken"):
                return ClaimResult(task=None)
            attempt = await session.get(TaskAttempt, worker.assignment_attempt_id)
            if (attempt is None or attempt.ended_at is not None or not attempt.claim_token
                    or attempt.worker_id != worker.id):
                return ClaimResult(task=None)
            task = (await session.execute(select(Task).where(Task.id == attempt.task_id))).scalar_one_or_none()
            run = await session.get(TaskExecutionRun, attempt.execution_run_id)
            if (task is None or run is None or task.current_attempt_id != attempt.id
                    or task.status != "running" or task.current_run_id != run.id):
                return ClaimResult(task=None)
            targets = list((await session.execute(select(TaskTarget).where(
                TaskTarget.task_id == task.id,
            ))).scalars().all())
            metadata["assignment_taken"] = True
            worker.metadata_json = metadata
            return ClaimResult(task=ClaimedTask(
                task_id=task.id, attempt_id=attempt.id, attempt_number=attempt.attempt_number,
                execution_run_id=run.id, claim_token=attempt.claim_token,
                execution_kind=run.execution_kind, execution_config=dict(run.configuration_json or {}),
                workspace_generation=run.workspace_generation, goal=task.goal,
                origin_surface=task.origin_surface, origin_conversation_id=task.origin_conversation_id,
                origin_turn_id=task.origin_turn_id, workspace_path=attempt.workspace_path,
                checkpoint=attempt.checkpoint_json, runtime_reference=attempt.agent_runtime_reference,
                targets=tuple((t.resource_type, t.resource_key, t.mutable) for t in targets),
            ))

    async def _assign_next(
        self,
        worker_id: str,
        *,
        owner_identity: str | None = None,
        generation: int | None = None,
        now: datetime | None = None,
    ) -> ClaimResult:
        timestamp = now or utc_now()
        lease_expiry = timestamp + timedelta(seconds=settings.worker_lease_ttl_seconds)
        async with session_scope() as session:
            # Acquire the write lock BEFORE reading the generation. A competing
            # supervisor cannot revoke authority between the check and commit.
            await session.execute(update(WorkerInstance).where(
                WorkerInstance.id == worker_id,
            ).values(heartbeat_at=WorkerInstance.heartbeat_at))
            supervisor = await session.get(TaskSupervisorLease, "task-pool")
            if owner_identity is not None and (
                supervisor is None or supervisor.owner_identity != owner_identity
                or supervisor.generation != generation or _aware(supervisor.expires_at) <= timestamp
            ):
                raise ValueError("Supervisor assignment authority is stale")
            worker_generation = (await session.execute(
                select(WorkerInstance.supervisor_generation).where(WorkerInstance.id == worker_id)
            )).scalar_one_or_none()
            if supervisor is not None and worker_generation != supervisor.generation:
                return ClaimResult(task=None)
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

            # Expiration is a liveness signal, not proof that the writer and
            # its descendants have stopped.  Only observed exit or an
            # explicitly confirmed in-process boundary may free a lease.
            busy_count = int((await session.execute(
                select(func.count()).select_from(WorkerInstance).where(
                    WorkerInstance.stopped_at.is_(None),
                    (WorkerInstance.state == "busy")
                    | ((WorkerInstance.state == "retiring") & WorkerInstance.assignment_attempt_id.is_not(None)),
                )
            )).scalar_one())
            if busy_count >= settings.worker_max_busy:
                return ClaimResult(task=None)

            task_result = await session.execute(
                select(Task)
                .where(Task.status == "queued")
                .order_by(
                    case(
                        (Task.importance == "high", 0),
                        (Task.importance == "normal", 1),
                        else_=2,
                    ),
                    Task.queue_entered_at.asc(),
                    Task.creation_order.asc(),
                    Task.id.asc(),
                )
            )
            candidates = list(task_result.scalars().all())
            if not candidates:
                await session.execute(
                    update(WorkerInstance).where(WorkerInstance.id == worker_id).values(state="idle")
                )
                return ClaimResult(task=None)
            leases_result = await session.execute(select(ResourceLease))
            leases = list(leases_result.scalars().all())
            task: Task | None = None
            attempt: TaskAttempt | None = None
            targets: list[TaskTarget] = []
            mutable_keys: set[tuple[str, str]] = set()
            selected_workspace: Path | None = None
            blocked_task_id: str | None = None
            for candidate in candidates:
                candidate_attempt = await self._current_or_new_attempt(session, candidate)
                candidate_run = await session.get(TaskExecutionRun, candidate.current_run_id)
                if candidate_run is None or candidate_attempt.execution_run_id != candidate_run.id:
                    raise RuntimeError("Queued task has no compatible execution run")
                candidate_workspace = await self._workspace_for(session, candidate, candidate_run)
                candidate_targets = list((await session.execute(
                    select(TaskTarget)
                    .where(TaskTarget.task_id == candidate.id)
                    .order_by(TaskTarget.resource_type.asc(), TaskTarget.resource_key.asc())
                )).scalars().all())
                candidate_keys = {
                    (target.resource_type, target.resource_key)
                    for target in candidate_targets if target.mutable
                }
                candidate_keys.add(("directory", str(candidate_workspace)))
                canonical_candidates = {
                    _canonical_resource(resource_type, resource_key)
                    for resource_type, resource_key in candidate_keys
                }
                blocked = any(
                    lease.owner_attempt_id != candidate_attempt.id
                    and any(
                        _resources_overlap(
                            requested,
                            (
                                ("directory", lease.canonical_key)
                                if lease.canonical_key
                                else _canonical_resource(lease.resource_type, lease.resource_key)
                            ),
                        )
                        for requested in canonical_candidates
                    )
                    for lease in leases
                )
                if blocked:
                    blocked_task_id = blocked_task_id or candidate.id
                    continue
                task = candidate
                attempt = candidate_attempt
                targets = candidate_targets
                mutable_keys = candidate_keys
                selected_workspace = candidate_workspace
                break
            if task is None or attempt is None or selected_workspace is None:
                await session.execute(
                    update(WorkerInstance).where(WorkerInstance.id == worker_id).values(state="idle")
                )
                return ClaimResult(task=None, blocked_task_id=blocked_task_id)

            run = await session.get(TaskExecutionRun, task.current_run_id)
            if run is None or attempt.execution_run_id != run.id:
                raise RuntimeError("Queued task has no compatible execution run")
            worker = await session.get(WorkerInstance, worker_id)
            assert worker is not None
            claim_token = str(uuid4())
            workspace = selected_workspace
            workspace.mkdir(parents=True, exist_ok=True)
            for resource_type, resource_key in sorted(mutable_keys):
                _, canonical_key = _canonical_resource(resource_type, resource_key)
                retained = next(
                    (
                        lease for lease in leases
                        if lease.owner_attempt_id == attempt.id
                        and _resources_overlap(
                            _canonical_resource(resource_type, resource_key),
                            (
                                ("directory", lease.canonical_key)
                                if lease.canonical_key
                                else _canonical_resource(lease.resource_type, lease.resource_key)
                            ),
                        )
                    ),
                    None,
                )
                if retained is not None:
                    retained.claim_token = claim_token
                    retained.supervisor_generation = worker.supervisor_generation
                    retained.writer_identity = worker.process_identity
                    retained.renewed_at = timestamp
                    retained.expires_at = lease_expiry
                else:
                    session.add(ResourceLease(
                        id=str(uuid4()),
                        resource_type=resource_type,
                        resource_key=resource_key,
                        canonical_key=canonical_key,
                        task_id=task.id,
                        execution_run_id=run.id,
                        owner_attempt_id=attempt.id,
                        claim_token=claim_token,
                        supervisor_generation=worker.supervisor_generation,
                        writer_identity=worker.process_identity,
                        acquired_at=timestamp,
                        renewed_at=timestamp,
                        expires_at=lease_expiry,
                    ))

            attempt.worker_id = worker_id
            attempt.claim_token = claim_token
            attempt.supervisor_generation = worker.supervisor_generation
            attempt.workspace_path = str(workspace)
            attempt.claimed_at = timestamp
            attempt.started_at = timestamp
            attempt.heartbeat_at = timestamp
            if not attempt.agent_runtime_reference:
                attempt.agent_runtime_reference = f"worker:{worker_id}"
            task.status = "running"
            task.status_reason = None
            task.updated_at = timestamp
            run.status = "running"
            run.started_at = run.started_at or timestamp
            worker.state = "busy"
            worker.assignment_attempt_id = attempt.id
            worker.metadata_json = dict(worker.metadata_json or {}) | {"assignment_taken": False}
            worker.heartbeat_at = timestamp
            await TaskRepository(session).append_event(
                task_id=task.id,
                attempt_id=attempt.id,
                execution_run_id=run.id,
                event_type="task.claimed",
                payload_json={
                    "status": "running",
                    "worker_id": worker_id,
                    "attempt_number": attempt.attempt_number,
                    "workspace_path": str(workspace),
                    "supervisor_generation": worker.supervisor_generation,
                    "child_identity": worker.process_identity,
                    "run_id": run.id,
                    "claim_token": claim_token,
                },
                idempotency_key=f"task.claimed:{attempt.id}",
            )
            return ClaimResult(
                task=ClaimedTask(
                    task_id=task.id,
                    attempt_id=attempt.id,
                    attempt_number=attempt.attempt_number,
                    execution_run_id=run.id,
                    claim_token=claim_token,
                    execution_kind=run.execution_kind,
                    execution_config=dict(run.configuration_json or {}),
                    workspace_generation=run.workspace_generation,
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
        claim_token: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        timestamp = now or utc_now()
        lease_expiry = timestamp + timedelta(seconds=settings.worker_lease_ttl_seconds)
        async with session_scope() as session:
            supervisor = await session.get(TaskSupervisorLease, "task-pool")
            attempt_generation = (await session.execute(
                select(TaskAttempt.supervisor_generation).where(TaskAttempt.id == attempt_id)
            )).scalar_one_or_none()
            if supervisor is not None and attempt_generation != supervisor.generation:
                return False
            worker_update = await session.execute(
                update(WorkerInstance)
                .where(
                    WorkerInstance.id == worker_id,
                    WorkerInstance.stopped_at.is_(None),
                    WorkerInstance.state == "busy",
                )
                .values(heartbeat_at=timestamp)
            )
            if worker_update.rowcount != 1:
                return False
            statement = update(TaskAttempt).where(
                TaskAttempt.id == attempt_id,
                TaskAttempt.worker_id == worker_id,
                TaskAttempt.ended_at.is_(None),
            )
            if claim_token is not None:
                statement = statement.where(TaskAttempt.claim_token == claim_token)
            result = await session.execute(statement.values(heartbeat_at=timestamp))
            if result.rowcount != 1:
                return False
            await session.execute(
                update(ResourceLease)
                .where(ResourceLease.owner_attempt_id == attempt_id)
                .values(renewed_at=timestamp, expires_at=lease_expiry)
            )
            return True

    async def release_claim(
        self,
        worker_id: str,
        attempt_id: str,
        *,
        claim_token: str | None = None,
        writers_stopped: bool = True,
    ) -> None:
        snapshots = (
            await self._snapshots_at_worker_exit(worker_id) if writers_stopped else {}
        )
        async with session_scope() as session:
            attempt = await session.get(TaskAttempt, attempt_id)
            authority_matches = not (
                attempt is not None and claim_token is not None and attempt.claim_token != claim_token
            )
            if writers_stopped and authority_matches:
                if attempt is not None and attempt_id in snapshots:
                    attempt.recovery_metadata_json = {
                        **dict(attempt.recovery_metadata_json or {}),
                        "released_resource_snapshots": snapshots[attempt_id],
                    }
                await session.execute(delete(ResourceLease).where(ResourceLease.owner_attempt_id == attempt_id))
            await session.execute(
                update(WorkerInstance)
                .where(WorkerInstance.id == worker_id, WorkerInstance.stopped_at.is_(None))
                .values(
                    state="idle" if writers_stopped else "retiring",
                    heartbeat_at=utc_now(),
                    assignment_attempt_id=None if writers_stopped else attempt_id,
                )
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
        writers_stopped: bool = True,
    ) -> str:
        snapshots = (
            await self._snapshots_at_worker_exit(worker_id) if writers_stopped else {}
        )
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
            existing.checkpoint_version += 1
            existing.ended_at = timestamp
            existing.terminal_reason = "target_expansion" if expanded_targets else "checkpoint"
            if existing.id in snapshots:
                existing.recovery_metadata_json = {
                    **dict(existing.recovery_metadata_json or {}),
                    "released_resource_snapshots": snapshots[existing.id],
                }
            if writers_stopped:
                await session.execute(delete(ResourceLease).where(ResourceLease.owner_attempt_id == attempt_id))
            next_attempt = TaskAttempt(
                id=str(uuid4()),
                task_id=task_id,
                execution_run_id=existing.execution_run_id,
                attempt_number=existing.attempt_number + 1,
                parent_attempt_id=existing.id,
                resume_reason=existing.terminal_reason,
                checkpoint_json=checkpoint,
                checkpoint_version=existing.checkpoint_version,
            )
            session.add(next_attempt)
            await session.flush()
            task.status = "queued"
            task.status_reason = None
            task.current_attempt_id = next_attempt.id
            task.queue_entered_at = timestamp
            task.updated_at = timestamp
            run = await session.get(TaskExecutionRun, existing.execution_run_id)
            if run is not None:
                run.status = "queued"
                run.current_attempt_id = next_attempt.id
                run.queue_entered_at = timestamp
            await TaskRepository(session).append_event(
                task_id=task_id,
                attempt_id=next_attempt.id,
                execution_run_id=existing.execution_run_id,
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
                .values(state="idle" if writers_stopped else "retiring", heartbeat_at=timestamp)
            )
            return next_attempt.id

    async def _current_or_new_attempt(self, session, task: Task) -> TaskAttempt:
        attempt = await session.get(TaskAttempt, task.current_attempt_id) if task.current_attempt_id else None
        if (
            attempt is not None
            and attempt.execution_run_id == task.current_run_id
            and attempt.ended_at is None
            and attempt.worker_id is None
        ):
            return attempt
        max_number = (await session.execute(
            select(func.max(TaskAttempt.attempt_number)).where(TaskAttempt.task_id == task.id)
        )).scalar_one_or_none()
        attempt = TaskAttempt(
            id=str(uuid4()),
            task_id=task.id,
            execution_run_id=task.current_run_id,
            attempt_number=int(max_number or 0) + 1,
        )
        session.add(attempt)
        await session.flush()
        task.current_attempt_id = attempt.id
        run = await session.get(TaskExecutionRun, task.current_run_id)
        if run is not None:
            run.current_attempt_id = attempt.id
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
            run = await session.get(TaskExecutionRun, attempt.execution_run_id)
            if run is not None:
                run.status = "interrupted"
                run.finished_at = timestamp
                run.terminal_summary = task.terminal_summary
            await repo.append_event(
                task_id=task.id,
                attempt_id=attempt.id,
                execution_run_id=attempt.execution_run_id,
                event_type="task.interrupted",
                payload_json={
                    "status": "interrupted",
                    "reason": "worker_stale_writer_unconfirmed",
                    "worker_id": worker.id,
                    "resource_leases_retained": True,
                },
                idempotency_key=f"task.worker-stale:{attempt.id}",
            )
            interrupted.append(task.id)
        worker.state = "stale"
        worker.assignment_attempt_id = None
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

    def _workspace_path(self, task_id: str, workspace_generation: int) -> Path:
        # Keep the legacy directory spelling for generation one while making
        # the path stable across recovery attempts in that generation.
        return settings.task_workspaces_path / task_id / f"attempt-{workspace_generation}"

    async def _workspace_for(self, session, task: Task, run: TaskExecutionRun) -> Path:
        workspace = self._workspace_path(task.id, run.workspace_generation)
        if run.workspace_generation == 1:
            # Migration can retain a legacy/custom path for generation one.
            historical_path = (await session.execute(
                select(TaskAttempt.workspace_path)
                .where(
                    TaskAttempt.task_id == task.id,
                    TaskAttempt.workspace_path.is_not(None),
                )
                .order_by(TaskAttempt.attempt_number.asc())
                .limit(1)
            )).scalar_one_or_none()
            if historical_path:
                return Path(historical_path)
        return workspace


worker_coordinator = WorkerCoordinator()
