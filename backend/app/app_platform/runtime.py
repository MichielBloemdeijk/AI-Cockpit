"""The app runtime: scheduled and event functions, and automatic repairs.

One loop in the API process. Each tick it runs the functions that queued
record and file changes trigger, the scheduled functions that are due, and
(when enabled) starts a repair for an app that reported new errors. A tick
waits for its runs, which happen in parallel within the function host's limit.

Repairs are started through a callback the core registers, so this package
does not depend on the task system. Each app gets at most one automatic repair
a day, and only for errors of its active version that arrived after the
previous repair started.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from sqlalchemy import delete, select

from app.app_platform.cron import CronError, parse
from app.app_platform.errors import record_error
from app.app_platform.events import MAX_DEPTH, subscribers
from app.app_platform.function_host import run_function
from app.app_platform.lifecycle import ActiveApp, AppNotFound, get_active, list_apps
from app.app_platform.store import AppError, AppEvent, AppRepair, AppSchedule, apps_session
from app.config import settings

logger = logging.getLogger(__name__)
TICK_SECONDS = 2.0
EVENTS_PER_TICK = 20
SCHEDULE_CHECK_SECONDS = 15.0
REPAIR_CHECK_SECONDS = 60.0
REPAIR_INTERVAL = timedelta(hours=24)
# (app, recent errors of its active version) -> the repair task's id, or None when it could not start
RepairStarter = Callable[[ActiveApp, list[dict[str, Any]]], Awaitable[str | None]]


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


async def run_events(limit: int = EVENTS_PER_TICK) -> int:
    """Run the functions the oldest queued changes trigger; returns how many changes were handled."""
    async with apps_session() as session:
        events = list((await session.execute(select(AppEvent).order_by(AppEvent.id).limit(limit))).scalars())
    by_app: dict[str, list[AppEvent]] = {}
    for event in events:
        by_app.setdefault(event.app_slug, []).append(event)

    async def handle(slug: str, queued: list[AppEvent]) -> None:
        # One app's changes run in the order they happened (a put, then its delete);
        # different apps run side by side.
        for event in queued:
            try:
                app = await get_active(slug)
            except AppNotFound:
                return
            names = subscribers(app.manifest, event.payload_json)
            if event.depth >= MAX_DEPTH:
                await record_error(slug, f"Stopped a chain of {MAX_DEPTH} event-triggered changes "
                                   f"({', '.join(names)}); a function probably triggers itself")
                continue
            for name in names:
                await run_function(slug, name, event.payload_json, trigger="event", depth=event.depth)

    await asyncio.gather(*(handle(slug, queued) for slug, queued in by_app.items()))
    if events:
        async with apps_session() as session:
            await session.execute(delete(AppEvent).where(AppEvent.id.in_([event.id for event in events])))
    return len(events)


async def run_schedules(now: datetime | None = None) -> list[str]:
    """Run every scheduled function that is due; returns "slug.function" for each one started."""
    now = now or datetime.now(timezone.utc)
    due: list[tuple[str, str, datetime]] = []
    for app in await list_apps():
        scheduled = {name: (spec.get("trigger") or {}).get("schedule")
                     for name, spec in (app.manifest.get("functions") or {}).items()}
        scheduled = {name: cron for name, cron in scheduled.items() if cron}
        async with apps_session() as session:
            rows = {row.function: row for row in (await session.execute(
                select(AppSchedule).where(AppSchedule.app_slug == app.slug))).scalars()}
            for name, row in rows.items():
                if name not in scheduled:
                    await session.delete(row)
            for name, cron in scheduled.items():
                try:
                    schedule = parse(cron)
                except CronError:
                    continue  # validated on install; a later parser change must not stop the loop
                row = rows.get(name)
                if row is None or row.cron != cron:
                    # A new or changed schedule starts counting now; it does not run immediately.
                    if row is None:
                        session.add(AppSchedule(app_slug=app.slug, function=name, cron=cron,
                                                next_run_at=schedule.next_after(now)))
                    else:
                        row.cron, row.next_run_at = cron, schedule.next_after(now)
                elif _aware(row.next_run_at) <= now:
                    # Missed runs (Cockpit was off) collapse into one. The next time is
                    # committed before running, so a crash cannot repeat a run.
                    due.append((app.slug, name, _aware(row.next_run_at)))
                    row.next_run_at = schedule.next_after(now)
    await asyncio.gather(*(run_function(slug, name, {"event": "schedule", "scheduled_for": when.isoformat()},
                                        trigger="schedule") for slug, name, when in due))
    return [f"{slug}.{name}" for slug, name, _ in due]


async def start_repairs(starter: RepairStarter, now: datetime | None = None) -> list[str]:
    """Start a repair for each app with new errors in its active version; returns their slugs."""
    from app.app_platform.errors import recent_errors

    now = now or datetime.now(timezone.utc)
    started = []
    for app in await list_apps():
        async with apps_session() as session:
            repair = await session.get(AppRepair, app.slug)
            if repair is not None and now - _aware(repair.started_at) < REPAIR_INTERVAL:
                continue
            since = _aware(repair.started_at) if repair else None
            newest = (await session.execute(select(AppError.last_seen_at).where(
                AppError.app_slug == app.slug, AppError.version == app.version)
                .order_by(AppError.last_seen_at.desc()).limit(1))).scalar_one_or_none()
        if newest is None or (since is not None and _aware(newest) <= since):
            continue
        errors = [error for error in await recent_errors(app.slug) if error["version"] == app.version]
        try:
            task_id = await starter(app, errors)
        except Exception:
            logger.exception("Could not start a repair of %s", app.slug)
            task_id = None
        async with apps_session() as session:
            repair = await session.get(AppRepair, app.slug)
            if repair is None:
                session.add(AppRepair(app_slug=app.slug, task_id=task_id or "", started_at=now))
            else:
                repair.task_id, repair.started_at = task_id or "", now
        if task_id:
            started.append(app.slug)
    return started


class AppRuntime:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None
        self.repair: RepairStarter | None = None

    async def start(self, repair: RepairStarter | None = None) -> None:
        if self._task is not None and not self._task.done():
            return
        self.repair = repair
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(self._stop), name="app-platform-runtime")

    async def stop(self) -> None:
        if self._task is None or self._stop is None:
            return
        self._stop.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = self._stop = None

    async def _run(self, stop: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()
        next_schedules = next_repairs = loop.time()
        while not stop.is_set():
            try:
                while await run_events():
                    pass
                if loop.time() >= next_schedules:
                    next_schedules = loop.time() + SCHEDULE_CHECK_SECONDS
                    await run_schedules()
                if self.repair is not None and settings.app_auto_repair and loop.time() >= next_repairs:
                    next_repairs = loop.time() + REPAIR_CHECK_SECONDS
                    await start_repairs(self.repair)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("App runtime tick failed")
            try:
                await asyncio.wait_for(stop.wait(), TICK_SECONDS)
            except TimeoutError:
                pass


app_runtime = AppRuntime()
