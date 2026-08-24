"""Application-lifetime ownership for agent runs.

HTTP/SSE observers may disconnect at any time. The supervisor keeps the
underlying agent task alive and lets a later observer attach to its durable
conversation events instead of cancelling work with the request.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any

logger = logging.getLogger(__name__)


class RunSupervisor:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}

    def start(
        self,
        run_id: str,
        *,
        cancel_event: asyncio.Event | None = None,
        stream_callback: Any | None = None,
        runner: Any | None = None,
    ) -> asyncio.Task[None]:
        existing = self._tasks.get(run_id)
        if existing is not None and not existing.done():
            return existing

        kwargs: dict[str, Any] = {}
        effective_cancel_event = cancel_event or self._cancel_events.get(run_id) or asyncio.Event()
        self._cancel_events[run_id] = effective_cancel_event
        kwargs["cancel_event"] = effective_cancel_event
        if stream_callback is not None:
            kwargs["stream_callback"] = stream_callback
        if runner is None:
            from app.services.agent_runner import agent_runner as runner

        parameters = inspect.signature(runner.continue_run).parameters
        supported_kwargs = {name: value for name, value in kwargs.items() if name in parameters}
        task = asyncio.create_task(runner.continue_run(run_id, **supported_kwargs), name=f"agent-run:{run_id}")
        self._tasks[run_id] = task

        def _on_done(completed: asyncio.Task[None]) -> None:
            if self._tasks.get(run_id) is completed:
                self._tasks.pop(run_id, None)
                self._cancel_events.pop(run_id, None)
            if completed.cancelled():
                return
            try:
                completed.exception()
            except Exception:
                logger.exception("Unable to inspect supervised run %s", run_id)

        task.add_done_callback(_on_done)
        return task

    def get(self, run_id: str) -> asyncio.Task[None] | None:
        task = self._tasks.get(run_id)
        return task if task is not None and not task.done() else None

    async def cancel(self, run_id: str) -> bool:
        event = self._cancel_events.get(run_id)
        task = self.get(run_id)
        if event is None and task is None:
            return False
        if event is None:
            event = asyncio.Event()
            self._cancel_events[run_id] = event
        event.set()
        try:
            from app.services.run_lifecycle import run_lifecycle

            await run_lifecycle.cancel(run_id)
        except Exception:
            logger.exception("Unable to cancel supervised run %s", run_id)
        return True

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
