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
from app.services.task_service import task_service
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
        runtime: TaskAgentRuntime = task_agent_runtime,
    ) -> None:
        self.coordinator = coordinator
        self.runtime = runtime
        self.worker_id: str | None = None

    async def activate(self, *, process_identity: str | None = None) -> bool:
        identity = process_identity or f"{socket.gethostname()}:{os.getpid()}"
        registration = await self.coordinator.activate(process_identity=identity)
        if registration is None:
            return False
        self.worker_id = registration.worker_id
        return True

    async def run_once(self) -> WorkerRunResult:
        if self.worker_id is None:
            raise RuntimeError("Worker is not active")
        claim = await self.coordinator.claim_next(self.worker_id)
        if claim.task is None:
            await self.coordinator.heartbeat(self.worker_id, state="idle")
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

    async def _execute_claim(self, claim: ClaimedTask) -> WorkerRunResult:
        assert self.worker_id is not None
        context = AgentExecutionContext(
            execution_id=f"task:{claim.task_id}:attempt:{claim.attempt_number}",
            task_id=claim.task_id,
            attempt_id=claim.attempt_id,
            goal=claim.goal,
            workspace_path=claim.workspace_path,
            origin_conversation_id=claim.origin_conversation_id,
            targets=claim.targets,
            checkpoint=dict(claim.checkpoint or {}),
        )
        sink = TaskExecutionSink(context)
        execution = asyncio.create_task(
            self.runtime.execute(
                context,
                sink,
                cancellation_requested=lambda: task_service.cancellation_requested(claim.task_id),
            )
        )
        try:
            while not execution.done():
                done, _ = await asyncio.wait(
                    {execution},
                    timeout=settings.worker_heartbeat_interval_seconds,
                )
                if done:
                    break
                renewed = await self.coordinator.renew_claim(self.worker_id, claim.attempt_id)
                if not renewed:
                    execution.cancel()
                    await asyncio.gather(execution, return_exceptions=True)
                    return WorkerRunResult(task_id=claim.task_id, status="lost_claim")
                if await task_service.cancellation_requested(claim.task_id):
                    execution.cancel()
                    await asyncio.gather(execution, return_exceptions=True)
                    await sink.cancelled()
                    return WorkerRunResult(task_id=claim.task_id, status="cancelled")

            result = await execution
            return WorkerRunResult(task_id=claim.task_id, status=result.status)
        except TargetExpansionRequested as expansion:
            await self.coordinator.checkpoint_and_requeue(
                worker_id=self.worker_id,
                task_id=claim.task_id,
                attempt_id=claim.attempt_id,
                checkpoint=expansion.checkpoint,
                expanded_targets=expansion.targets,
                idempotency_key=f"runtime-expansion:{claim.attempt_id}",
            )
            return WorkerRunResult(task_id=claim.task_id, status="requeued")
        except asyncio.CancelledError:
            if await task_service.cancellation_requested(claim.task_id):
                await sink.cancelled()
                return WorkerRunResult(task_id=claim.task_id, status="cancelled")
            raise
        except Exception as exc:
            detail = await task_service.get_task(claim.task_id)
            if detail is not None and detail.task.status == "running":
                await sink.fail(str(exc))
            return WorkerRunResult(task_id=claim.task_id, status="failed")
        finally:
            await self.coordinator.release_claim(self.worker_id, claim.attempt_id)

task_worker = TaskWorker()
