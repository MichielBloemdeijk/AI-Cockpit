"""Non-AI process supervisor for the homogeneous task-worker pool."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import os
import socket
import sys
from uuid import uuid4

from app.config import settings
from app.services.worker_coordinator import WorkerCoordinator, worker_coordinator
from app.services.windows_job import process_creation_stamp


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SupervisedChild:
    identity: str
    process: asyncio.subprocess.Process


class SupervisorLeaseBusy(RuntimeError):
    """Another API process currently owns the pool."""


class TaskPoolSupervisor:
    def __init__(self, *, coordinator: WorkerCoordinator = worker_coordinator) -> None:
        self.coordinator = coordinator
        stamp = process_creation_stamp(os.getpid())
        self.owner_identity = f"{socket.gethostname()}:{os.getpid()}:{stamp or 0}:{uuid4()}"
        self.generation: int | None = None
        self.children: dict[str, SupervisedChild] = {}
        self._terminating: set[str] = set()

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        stop = stop_event or asyncio.Event()
        generation = await self.coordinator.acquire_supervisor(self.owner_identity)
        if generation is None:
            raise SupervisorLeaseBusy("Another healthy task-pool supervisor owns the lease")
        self.generation = generation
        try:
            while not stop.is_set():
                await self._reap_children()
                if not await self.coordinator.heartbeat_supervisor(self.owner_identity, generation):
                    raise RuntimeError("Task-pool supervisor lost its generation lease")
                requested = await self.coordinator.stop_requested_identities(generation)
                for identity in requested:
                    child = self.children.get(identity)
                    if child is not None and identity not in self._terminating:
                        self._terminating.add(identity)
                        await self._terminate_process_tree(child)
                await self.coordinator.reconcile_availability()
                for child in self.children.values():
                    if child.process.returncode is not None or child.identity in self._terminating:
                        continue
                    worker_id = await self.coordinator.worker_id_for_identity(child.identity)
                    if worker_id is not None:
                        await self.coordinator.assign_next(
                            worker_id, owner_identity=self.owner_identity, generation=generation,
                        )
                unregistered_child = False
                for child in self.children.values():
                    if (
                        child.process.returncode is None
                        and await self.coordinator.worker_id_for_identity(child.identity) is None
                    ):
                        unregistered_child = True
                        break
                # Read occupancy AFTER registration checks. A child registering
                # between these awaits must not disappear from both the
                # unregistered guard and an older occupancy snapshot.
                snapshot = await self.coordinator.pool_snapshot()
                if (
                    snapshot.busy < settings.worker_max_busy
                    and snapshot.standby + snapshot.starting < settings.worker_standby_count
                    and not unregistered_child
                ):
                    await self._spawn_child(generation)
                await self.coordinator.confirm_pool_commands()
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_interval_seconds)
                except TimeoutError:
                    pass
        finally:
            await self._stop_children()
            await self.coordinator.release_supervisor(self.owner_identity, generation)

    async def _spawn_child(self, generation: int) -> None:
        identity = f"{socket.gethostname()}:{uuid4()}"
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "app.worker",
            "--child",
            "--generation",
            str(generation),
            "--identity",
            identity,
            "--parent-pid",
            str(os.getpid()),
            creationflags=creationflags,
        )
        self.children[identity] = SupervisedChild(identity=identity, process=process)
        logger.info("Started task worker child %s (pid=%s)", identity, process.pid)

    async def _reap_children(self) -> None:
        completed = [child for child in self.children.values() if child.process.returncode is not None]
        for child in completed:
            await self._reconcile_child(child)

    async def _reconcile_child(self, child: SupervisedChild) -> None:
        self.children.pop(child.identity, None)
        self._terminating.discard(child.identity)
        return_code = await child.process.wait()
        worker_id = await self.coordinator.worker_id_for_identity(child.identity)
        if worker_id is not None:
            await self.coordinator.worker_exited(
                worker_id,
                process_identity=child.identity,
                exit_code=return_code,
            )
        logger.info("Task worker child %s exited with %s", child.identity, return_code)

    async def _stop_children(self) -> None:
        children = list(self.children.values())
        for child in children:
            if child.process.returncode is None:
                await self._terminate_process_tree(child)
        if children:
            await asyncio.gather(*(child.process.wait() for child in children), return_exceptions=True)
        for child in children:
            worker_id = await self.coordinator.worker_id_for_identity(child.identity)
            if worker_id is not None:
                await self.coordinator.worker_exited(
                    worker_id,
                    process_identity=child.identity,
                    exit_code=child.process.returncode,
                )
        self.children.clear()
        self._terminating.clear()

    async def _terminate_process_tree(self, child: SupervisedChild) -> None:
        if child.process.returncode is not None:
            return
        if os.name == "nt" and child.process.pid is not None:
            creationflags = getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0)
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(child.process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=creationflags,
            )
            await killer.wait()
        else:
            child.process.terminate()


task_pool_supervisor = TaskPoolSupervisor()


class TaskPoolManager:
    """Keep one elected supervisor running while this API process is alive.

    Every API process may run a manager. The database generation lease elects
    the single supervisor; a loser retries after the current lease expires.
    This also repairs an unexpectedly exited local supervisor without making
    task admission wait for process startup.
    """

    def __init__(self, *, retry_seconds: float = 1.0) -> None:
        self.retry_seconds = retry_seconds
        self._stop = asyncio.Event()
        self._running: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()

    async def start(self) -> None:
        if self._running is not None and not self._running.done():
            return
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._running = asyncio.create_task(self._serve(), name="task-pool-manager")

    async def stop(self) -> None:
        if self._running is None:
            return
        self._stop.set()
        self._wake.set()
        await self._running
        self._running = None

    def repair(self) -> None:
        """Wake a local manager; the normal loop also repairs automatically."""
        self._wake.set()

    async def _serve(self) -> None:
        while not self._stop.is_set():
            supervisor = TaskPoolSupervisor()
            try:
                await supervisor.run(self._stop)
            except SupervisorLeaseBusy:
                pass
            except Exception:
                if not self._stop.is_set():
                    logger.exception("Task-pool supervisor unavailable; retrying")
            if self._stop.is_set():
                break
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.retry_seconds)
            except TimeoutError:
                pass
            self._wake.clear()


task_pool_manager = TaskPoolManager()
