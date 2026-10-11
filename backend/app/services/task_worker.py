"""Worker loop that owns first-class task execution outside FastAPI."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import os
import socket

from sqlalchemy.exc import OperationalError

from app.config import settings
from app.services.agent_execution import (
    AgentExecutionContext,
    TargetExpansionRequested,
    TaskAgentRuntime,
    TaskExecutionSink,
    task_agent_runtime,
)
from app.services.task_service import task_service, execution_attempt_outcome
from app.services.worker_coordinator import ClaimedTask, WorkerCoordinator, worker_coordinator


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WorkerRunResult:
    task_id: str | None
    status: str
    blocked_task_id: str | None = None


class TaskWorker:
    def __init__(
        self,
        *,
        coordinator: WorkerCoordinator = worker_coordinator,
        runtime: TaskAgentRuntime | None = None,
        externally_supervised: bool = False,
    ) -> None:
        self.coordinator = coordinator
        if runtime is not None:
            self.runtime = runtime
        elif settings.task_executor == "codex":
            from app.services.codex_task_runtime import codex_task_runtime

            self.runtime = codex_task_runtime  # type: ignore[assignment]
        else:
            self.runtime = task_agent_runtime
        self.worker_id: str | None = None
        self.externally_supervised = externally_supervised
        self._release_writers_stopped = True

    async def activate(
        self,
        *,
        process_identity: str | None = None,
        supervisor_generation: int = 1,
    ) -> bool:
        identity = process_identity or f"{socket.gethostname()}:{os.getpid()}"
        registration = await self.coordinator.activate(
            process_identity=identity,
            supervisor_generation=supervisor_generation,
            supervised=self.externally_supervised,
        )
        if registration is None:
            return False
        self.worker_id = registration.worker_id
        return True

    async def run_once(self) -> WorkerRunResult:
        if self.worker_id is None:
            raise RuntimeError("Worker is not active")
        claim = (await self.coordinator.take_assignment(self.worker_id)
                 if self.externally_supervised else await self.coordinator.claim_next(self.worker_id))
        if claim.task is None:
            await self.coordinator.heartbeat(self.worker_id)
            return WorkerRunResult(
                task_id=None,
                status="blocked" if claim.blocked_task_id else "idle",
                blocked_task_id=claim.blocked_task_id,
            )
        return await self._execute_claim(claim.task)

    async def run_forever(self, stop_event: asyncio.Event | None = None) -> None:
        if self.worker_id is None and not await self.activate():
            raise RuntimeError("Another healthy task worker is already active")
        assert self.worker_id is not None
        stop = stop_event or asyncio.Event()
        try:
            while not stop.is_set():
                try:
                    result = await self.run_once()
                except OperationalError as exc:
                    if "database is locked" not in str(exc).casefold():
                        raise
                    logger.warning("Task worker is waiting for the SQLite write lock")
                    try:
                        await asyncio.wait_for(
                            stop.wait(),
                            timeout=settings.worker_poll_interval_seconds,
                        )
                    except TimeoutError:
                        pass
                    continue
                if result.status in {"idle", "blocked"}:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_interval_seconds)
                    except TimeoutError:
                        pass
        finally:
            await self.coordinator.stop(self.worker_id)

    async def run_child(self, stop_event: asyncio.Event | None = None) -> WorkerRunResult:
        """Remain as the warm standby until one assignment finishes, then exit."""
        if self.worker_id is None:
            raise RuntimeError("Worker is not active")
        stop = stop_event or asyncio.Event()
        try:
            while not stop.is_set():
                result = await self.run_once()
                if result.task_id is not None:
                    if self.externally_supervised:
                        await self.coordinator.retire(self.worker_id)
                        # The supervisor kills this entire process tree.  A
                        # normal child exit cannot attest to surviving tool
                        # subprocesses on Windows.
                        await asyncio.Event().wait()
                    return result
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_interval_seconds)
                except TimeoutError:
                    pass
            return WorkerRunResult(task_id=None, status="stopped")
        finally:
            await self.coordinator.stop(self.worker_id)

    async def _execute_claim(self, claim: ClaimedTask) -> WorkerRunResult:
        assert self.worker_id is not None
        self._release_writers_stopped = True
        permitted, resource_changes = await self.coordinator.verify_reacquired_targets(claim)
        if not permitted:
            await self.coordinator.release_claim(
                self.worker_id,
                claim.attempt_id,
                claim_token=claim.claim_token,
                writers_stopped=not self.externally_supervised,
            )
            return WorkerRunResult(task_id=claim.task_id, status="resource_changed")
        checkpoint = dict(claim.checkpoint or {})
        if resource_changes:
            checkpoint["resource_changes_to_inspect"] = list(resource_changes)
        context = AgentExecutionContext(
            execution_id=f"task:{claim.task_id}:attempt:{claim.attempt_number}",
            task_id=claim.task_id,
            attempt_id=claim.attempt_id,
            execution_run_id=claim.execution_run_id,
            claim_token=claim.claim_token,
            execution_kind=claim.execution_kind,
            execution_config=claim.execution_config,
            goal=claim.goal,
            workspace_path=claim.workspace_path,
            origin_conversation_id=claim.origin_conversation_id,
            targets=claim.targets,
            checkpoint=checkpoint,
        )
        sink = TaskExecutionSink(context)

        async def execute_with_context():
            from app.services.task_context import portable_context

            if not getattr(self.runtime, "manages_context", False):
                await portable_context(
                    claim.task_id, run_id=claim.execution_run_id,
                    attempt_id=claim.attempt_id, claim_token=claim.claim_token,
                )
            return await self.runtime.execute(
                context,
                sink,
                cancellation_requested=lambda: task_service.cancellation_requested(claim.task_id),
            )

        execution = asyncio.create_task(
            execute_with_context()
        )
        try:
            while not execution.done():
                done, _ = await asyncio.wait(
                    {execution},
                    timeout=settings.worker_heartbeat_interval_seconds,
                )
                if done:
                    break
                renewed = await self.coordinator.renew_claim(
                    self.worker_id,
                    claim.attempt_id,
                    claim_token=claim.claim_token,
                )
                if not renewed:
                    # Publication or an answer may commit before the runtime
                    # finishes draining/closing. A closed committed attempt
                    # cannot renew; that is completion, not a lost claim.
                    detail = await task_service.get_task(claim.task_id)
                    outcome = execution_attempt_outcome(detail, claim.execution_run_id, claim.attempt_id)
                    if outcome is not None:
                        try:
                            await asyncio.wait_for(execution, timeout=15)
                        except TimeoutError:
                            execution.cancel()
                            await asyncio.gather(execution, return_exceptions=True)
                            logger.warning("Codex turn did not drain after task publication: %s", claim.task_id)
                        except Exception:
                            logger.exception("Task stream failed after durable result publication: %s", claim.task_id)
                        return WorkerRunResult(task_id=claim.task_id, status=outcome)
                    command = await task_service.pending_control(
                        claim.task_id,
                        attempt_id=claim.attempt_id,
                    )
                    if command is not None and command.state == "stop_requested":
                        if self.externally_supervised:
                            self._release_writers_stopped = False
                            await asyncio.Event().wait()
                        execution.cancel()
                        await asyncio.gather(execution, return_exceptions=True)
                        return await self._confirm_control(claim, command.id, command.action)
                    execution.cancel()
                    await asyncio.gather(execution, return_exceptions=True)
                    return WorkerRunResult(task_id=claim.task_id, status="lost_claim")
                command = await task_service.pending_control(
                    claim.task_id,
                    attempt_id=claim.attempt_id,
                )
                if command is not None and command.state == "stop_requested":
                    if self.externally_supervised:
                        self._release_writers_stopped = False
                        await asyncio.Event().wait()
                    execution.cancel()
                    await asyncio.gather(execution, return_exceptions=True)
                    return await self._confirm_control(claim, command.id, command.action)

            result = await execution
            if result.status == "control_requested":
                command = await task_service.pending_control(
                    claim.task_id,
                    attempt_id=claim.attempt_id,
                )
                if command is not None:
                    return await self._confirm_control(claim, command.id, command.action)
            return WorkerRunResult(task_id=claim.task_id, status=result.status)
        except TargetExpansionRequested as expansion:
            await self.coordinator.checkpoint_and_requeue(
                worker_id=self.worker_id,
                task_id=claim.task_id,
                attempt_id=claim.attempt_id,
                checkpoint=expansion.checkpoint,
                expanded_targets=expansion.targets,
                idempotency_key=f"runtime-expansion:{claim.attempt_id}",
                writers_stopped=not self.externally_supervised,
            )
            return WorkerRunResult(task_id=claim.task_id, status="requeued")
        except asyncio.CancelledError:
            command = await task_service.pending_control(claim.task_id, attempt_id=claim.attempt_id)
            if command is not None:
                return await self._confirm_control(claim, command.id, command.action)
            raise
        except Exception as exc:
            detail = await task_service.get_task(claim.task_id)
            outcome = execution_attempt_outcome(detail, claim.execution_run_id, claim.attempt_id)
            if outcome is not None:
                logger.warning("Runtime cleanup failed after durable task outcome: %s", claim.task_id)
                return WorkerRunResult(task_id=claim.task_id, status=outcome)
            if detail is not None and detail.task.status == "running":
                await sink.fail(str(exc))
            return WorkerRunResult(task_id=claim.task_id, status="failed")
        finally:
            await self.coordinator.release_claim(
                self.worker_id,
                claim.attempt_id,
                claim_token=claim.claim_token,
                writers_stopped=(
                    self._release_writers_stopped and not self.externally_supervised
                ),
            )

    async def _confirm_control(
        self,
        claim: ClaimedTask,
        command_id: str,
        action: str,
    ) -> WorkerRunResult:
        if self.externally_supervised:
            await task_service.acknowledge_control_boundary(
                claim.task_id,
                command_id=command_id,
                attempt_id=claim.attempt_id,
            )
            # The child cannot attest that its own descendants are gone.  Keep
            # the assignment and leases until the supervisor observes exit.
            self._release_writers_stopped = False
            await asyncio.Event().wait()
        await task_service.confirm_control(
            claim.task_id,
            command_id=command_id,
            stopped_attempt_id=claim.attempt_id,
            stop_evidence={
                "kind": "worker_execution_stopped",
                "worker_id": self.worker_id,
                "writer_boundary": "in_process_runtime",
            },
        )
        status = {
            "cancel": "cancelled",
            "suspend": "paused",
            "yield": "requeued",
            "restart_execution": "requeued",
            "restart_from_beginning": "requeued",
        }.get(action, "controlled")
        return WorkerRunResult(task_id=claim.task_id, status=status)

task_worker = TaskWorker()
