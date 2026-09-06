"""Non-blocking, metadata-only Realtime trace persistence."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from sqlalchemy import delete, insert, select

from app.db.session import session_scope
from app.db.tables import JarvisRealtimeTraceEvent


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


logger = logging.getLogger(__name__)


class JarvisRealtimeTelemetryService:
    def __init__(
        self,
        *,
        queue_size: int = 5_000,
        retention_days: int = 7,
        max_rows: int = 50_000,
    ) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_size)
        self._retention_days = retention_days
        self._max_rows = max_rows
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(self._stop), name="jarvis-realtime-telemetry")

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        self._stop = None

    def record(self, event: dict[str, Any]) -> bool:
        try:
            normalized = dict(event)
            normalized.setdefault("client_sequence", None)
            self._queue.put_nowait(normalized)
            return True
        except asyncio.QueueFull:
            return False

    def record_many(self, events: list[dict[str, Any]]) -> int:
        return sum(1 for event in events if self.record(event))

    async def _run(self, stop: asyncio.Event) -> None:
        next_cleanup = utc_now()
        while not stop.is_set() or not self._queue.empty():
            batch: list[dict[str, Any]] = []
            try:
                first = await asyncio.wait_for(self._queue.get(), timeout=0.5)
                batch.append(first)
            except TimeoutError:
                pass
            while len(batch) < 100:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            if batch:
                try:
                    async with session_scope() as session:
                        await session.execute(insert(JarvisRealtimeTraceEvent), batch)
                except Exception:
                    logger.exception("Unable to persist Jarvis Realtime telemetry batch")
                finally:
                    for _ in batch:
                        self._queue.task_done()
            if utc_now() >= next_cleanup:
                try:
                    await self._prune()
                except Exception:
                    logger.exception("Unable to prune Jarvis Realtime telemetry")
                next_cleanup = utc_now() + timedelta(hours=24)

    async def _prune(self) -> None:
        async with session_scope() as session:
            await session.execute(
                delete(JarvisRealtimeTraceEvent).where(
                    JarvisRealtimeTraceEvent.created_at
                    < utc_now() - timedelta(days=self._retention_days)
                )
            )
            if self._max_rows <= 0:
                await session.execute(delete(JarvisRealtimeTraceEvent))
                return
            cutoff = await session.scalar(
                select(JarvisRealtimeTraceEvent.sequence)
                .order_by(JarvisRealtimeTraceEvent.sequence.desc())
                .offset(self._max_rows - 1)
                .limit(1)
            )
            if cutoff is not None:
                await session.execute(
                    delete(JarvisRealtimeTraceEvent).where(
                        JarvisRealtimeTraceEvent.sequence < cutoff
                    )
                )


jarvis_realtime_telemetry = JarvisRealtimeTelemetryService()
