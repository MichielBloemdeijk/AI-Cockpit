"""Durable health heartbeats and watchdogs for the Jarvis runtime."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import settings
from app.db.session import session_scope
from app.db.tables import RuntimeComponentHealth


EXPECTED_COMPONENTS = (
    "reminder_scheduler",
    "notification_router",
    "response_arbiter",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class RuntimeHealthService:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None
        self._last_healthy_write: dict[str, float] = {}

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        await self.mark_stale_components()
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(self._stop), name="jarvis-runtime-watchdog")

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        self._stop = None
        self._last_healthy_write.clear()

    async def healthy(self, component: str, *, detail: str | None = None, force: bool = False) -> None:
        monotonic = time.monotonic()
        if not force and monotonic - self._last_healthy_write.get(component, 0.0) < 1.0:
            return
        self._last_healthy_write[component] = monotonic
        timestamp = utc_now()
        async with session_scope() as session:
            await session.execute(
                sqlite_insert(RuntimeComponentHealth)
                .values(
                    component=component,
                    status="healthy",
                    detail=detail,
                    heartbeat_at=timestamp,
                    last_success_at=timestamp,
                    failure_count=0,
                )
                .on_conflict_do_update(
                    index_elements=["component"],
                    set_={
                        "status": "healthy",
                        "detail": detail,
                        "heartbeat_at": timestamp,
                        "last_success_at": timestamp,
                    },
                )
            )

    async def failed(self, component: str, error: BaseException | str) -> None:
        timestamp = utc_now()
        detail = " ".join(str(error).split()).strip()[:500] or "Unknown component failure"
        async with session_scope() as session:
            await session.execute(
                sqlite_insert(RuntimeComponentHealth)
                .values(
                    component=component,
                    status="degraded",
                    detail=detail,
                    heartbeat_at=timestamp,
                    last_failure_at=timestamp,
                    failure_count=1,
                )
                .on_conflict_do_update(
                    index_elements=["component"],
                    set_={
                        "status": "degraded",
                        "detail": detail,
                        "heartbeat_at": timestamp,
                        "last_failure_at": timestamp,
                        "failure_count": RuntimeComponentHealth.failure_count + 1,
                    },
                )
            )

    async def offline(self, component: str, *, detail: str) -> None:
        timestamp = utc_now()
        async with session_scope() as session:
            row = await session.get(RuntimeComponentHealth, component)
            if row is None:
                session.add(RuntimeComponentHealth(
                    component=component,
                    status="offline",
                    detail=detail,
                    heartbeat_at=timestamp,
                ))
            elif row.status != "offline" or row.detail != detail:
                row.status = "offline"
                row.detail = detail
                row.updated_at = timestamp

    async def mark_stale_components(self, *, now: datetime | None = None) -> int:
        timestamp = now or utc_now()
        cutoff = timestamp - timedelta(seconds=settings.jarvis_component_stale_after_seconds)
        changed = 0
        async with session_scope() as session:
            rows = list((await session.execute(select(RuntimeComponentHealth))).scalars().all())
            by_name = {row.component: row for row in rows}
            for component in EXPECTED_COMPONENTS:
                row = by_name.get(component)
                if row is None:
                    session.add(RuntimeComponentHealth(
                        component=component,
                        status="starting",
                        detail="Awaiting first heartbeat.",
                        heartbeat_at=timestamp,
                    ))
                    changed += 1
                elif row.status != "offline" and _aware(row.heartbeat_at) < cutoff:
                    row.status = "offline"
                    row.detail = "Heartbeat is stale."
                    row.updated_at = timestamp
                    changed += 1
        return changed

    @staticmethod
    async def snapshot_in_session(session, *, now: datetime | None = None) -> dict[str, dict[str, Any]]:
        timestamp = now or utc_now()
        cutoff = timestamp - timedelta(seconds=settings.jarvis_component_stale_after_seconds)
        rows = list((await session.execute(
            select(RuntimeComponentHealth).order_by(RuntimeComponentHealth.component.asc())
        )).scalars().all())
        values: dict[str, dict[str, Any]] = {}
        for row in rows:
            status = row.status
            if status in {"healthy", "starting"} and _aware(row.heartbeat_at) < cutoff:
                status = "offline"
            values[row.component] = {
                "status": status,
                "detail": row.detail,
                "heartbeat_at": _aware(row.heartbeat_at).isoformat(),
                "last_success_at": None if row.last_success_at is None else _aware(row.last_success_at).isoformat(),
                "last_failure_at": None if row.last_failure_at is None else _aware(row.last_failure_at).isoformat(),
                "failure_count": row.failure_count,
            }
        return values

    async def _run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                from app.services.worker_coordinator import worker_coordinator

                await worker_coordinator.reconcile_availability()
                await self.mark_stale_components()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.failed("runtime_watchdog", exc)
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=max(0.2, settings.jarvis_watchdog_poll_seconds),
                )
            except TimeoutError:
                pass


runtime_health_service = RuntimeHealthService()
