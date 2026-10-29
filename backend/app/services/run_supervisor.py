"""Application-lifetime ownership for conversation runs.

HTTP/SSE observers may disconnect at any time. The supervisor keeps the
underlying run task alive and lets a later observer attach to its durable
conversation events instead of cancelling work with the request.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class ConversationRunner(Protocol):
    async def continue_run(self, run_id: str, *, cancel_event: asyncio.Event,
                           stream_callback: Any | None = None) -> None: ...


class RunSupervisor:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}

    def start(
        self,
        run_id: str,
        *,
        cancel_event: asyncio.Event | None = None,
        runner: ConversationRunner,
        stream_callback: Any | None = None,
    ) -> asyncio.Task[None]:
        existing = self._tasks.get(run_id)
        if existing is not None and not existing.done():
            return existing

        effective_cancel_event = cancel_event or self._cancel_events.get(run_id) or asyncio.Event()
        self._cancel_events[run_id] = effective_cancel_event
        task = asyncio.create_task(
            runner.continue_run(run_id, cancel_event=effective_cancel_event, stream_callback=stream_callback),
            name=f"conversation-run:{run_id}",
        )
        self._tasks[run_id] = task

        def _on_done(completed: asyncio.Task[None]) -> None:
            if self._tasks.get(run_id) is completed:
                self._tasks.pop(run_id, None)
                self._cancel_events.pop(run_id, None)
            if completed.cancelled():
                return
            failure = completed.exception()
            if failure is not None:
                logger.error("Supervised run %s failed", run_id, exc_info=failure)

        task.add_done_callback(_on_done)
        return task

    def get(self, run_id: str) -> asyncio.Task[None] | None:
        task = self._tasks.get(run_id)
        return task if task is not None and not task.done() else None

    async def stop(self) -> None:
        tasks = [task for task in self._tasks.values() if not task.done()]
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._cancel_events.clear()


run_supervisor = RunSupervisor()
