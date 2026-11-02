"""Independent durable scheduler for task waits and one-shot reminders."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings
from app.db.repositories.tasks import TaskRepository
from app.db.session import session_scope
from app.db.tables import Task
from app.services.updates.presentations import task_presentation_service
from app.services.tasks.service import TaskSnapshot, task_service
from app.services.tasks import lifecycle
from app.services.tasks.vocabulary import TaskStatus
from app.services.jarvis.clock import DEFAULT_TIME_ZONE, resolve_reminder_time
from app.services.runtime_health import runtime_health_service
from app.timestamps import utc_now


class ReminderScheduler:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        try:
            await self.run_due()
            await runtime_health_service.healthy("reminder_scheduler", force=True)
        except Exception as exc:
            await runtime_health_service.failed("reminder_scheduler", exc)
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self.run_forever(self._stop), name="jarvis-reminder-scheduler")

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        self._stop = None
        await runtime_health_service.offline(
            "reminder_scheduler",
            detail="Reminder scheduler stopped.",
        )

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.run_due()
                await runtime_health_service.healthy("reminder_scheduler")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await runtime_health_service.failed("reminder_scheduler", exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.reminder_poll_interval_seconds)
            except TimeoutError:
                pass

    async def run_due(self, *, now: datetime | None = None, limit: int = 50) -> int:
        timestamp = now or utc_now()
        async with session_scope() as session:
            due_ids = list((await session.execute(
                select(Task.id)
                .where(Task.status == TaskStatus.waiting_until)
                .where(Task.resume_at.is_not(None), Task.resume_at <= timestamp)
                .order_by(Task.resume_at.asc(), Task.creation_order.asc())
                .limit(limit)
            )).scalars().all())
        processed = 0
        for task_id in due_ids:
            if await self._resume_one(task_id, timestamp=timestamp):
                processed += 1
        return processed

    async def _resume_one(self, task_id: str, *, timestamp: datetime) -> bool:
        async with session_scope() as session:
            # Take the task write lock before reading, like every task writer,
            # so a concurrent cancel or suspend is never overwritten.
            try:
                task = await lifecycle.lock_task(session, task_id)
            except ValueError:
                return False
            if task.status != TaskStatus.waiting_until or task.resume_at is None:
                return False
            due = task.resume_at if task.resume_at.tzinfo is not None else task.resume_at.replace(tzinfo=timezone.utc)
            if due > timestamp:
                return False
            repo = TaskRepository(session)
            run = await lifecycle.current_run(session, task)
            delivery = dict(task.scheduled_delivery_json or {})
            if delivery.get("kind") == "reminder":
                key = task.scheduled_delivery_key or f"reminder:{task.id}"
                lifecycle.finish(
                    task, run, TaskStatus.completed, summary=str(delivery.get("text") or task.goal), now=timestamp,
                )
                task.scheduled_delivered_at = timestamp
                await repo.append_event(
                    task_id=task.id,
                    event_type="task.reminder.due",
                    payload_json={
                        "status": "completed",
                        "summary": task.terminal_summary,
                        "due_at": due.isoformat(),
                        "overdue": timestamp > due,
                    },
                    idempotency_key=f"task.reminder.due:{key}",
                )
            else:
                if run is None:
                    raise RuntimeError("Waiting task has no execution run")
                previous = await lifecycle.current_attempt(session, task)
                attempt = await lifecycle.create_attempt(
                    session, task, run, parent=previous, resume_reason="wait_elapsed",
                    checkpoint=lifecycle.inherited_checkpoint(previous),
                )
                lifecycle.end_wait(task)
                lifecycle.requeue(task, run, now=timestamp)
                if (await lifecycle.worker_health(session, now=timestamp)).status != "online":
                    lifecycle.hold_for_worker(task, now=timestamp)
                await repo.append_event(
                    task_id=task.id,
                    attempt_id=attempt.id,
                    event_type="task.resumed",
                    payload_json={
                        "status": task.status,
                        "status_reason": task.status_reason,
                        "attempt_number": attempt.attempt_number,
                    },
                    idempotency_key=f"task.resumed:{task.id}:{due.isoformat()}",
                )
            await session.flush()
        await task_presentation_service.sync_candidates()
        return True

    async def create(
        self,
        *,
        text: str | None,
        timing: object,
        active_time_zone: str = DEFAULT_TIME_ZONE,
        now: datetime | None = None,
        idempotency_key: str,
        origin_conversation_id: str | None = None,
        allow_duplicate: bool = False,
    ) -> TaskSnapshot:
        resolved = resolve_reminder_time(
            timing=timing,
            active_time_zone=active_time_zone,
            now=now,
        )
        return await task_service.schedule_reminder(
            text=text,
            due_at=resolved.due_at,
            idempotency_key=idempotency_key,
            origin_conversation_id=origin_conversation_id,
            allow_duplicate=allow_duplicate,
            timing_metadata=resolved.timing,
        )

reminder_scheduler = ReminderScheduler()
