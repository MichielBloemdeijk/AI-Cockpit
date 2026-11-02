from __future__ import annotations

from contextlib import closing
from datetime import timedelta
import sqlite3

import pytest

from app.config import settings
from app.db.repositories.conversations import utc_now
from app.services.tasks import reminder_scheduler as scheduler_module
from app.services.tasks.service import task_service


@pytest.mark.asyncio
async def test_cancel_during_reminder_resume_is_never_overwritten(monkeypatch) -> None:
    due = utc_now() + timedelta(seconds=30)
    snapshot = await task_service.schedule_reminder(text="race", due_at=due, idempotency_key="reminder-race")
    task_id = snapshot.task.id
    concurrent: list[object] = []

    class InterleavingRepository(scheduler_module.TaskRepository):
        def __init__(self, session):
            # Runs after the scheduler has read the task and before its writes.
            # Another process (a worker, a second API connection) cancels it.
            if not concurrent:
                try:
                    with closing(sqlite3.connect(settings.database_path, timeout=0.5)) as other, other:
                        concurrent.append(other.execute(
                            "UPDATE tasks SET status='cancelled' WHERE id=? AND status='waiting_until'",
                            (task_id,),
                        ).rowcount)
                except sqlite3.OperationalError as exc:
                    concurrent.append(str(exc))
            super().__init__(session)

    monkeypatch.setattr(scheduler_module, "TaskRepository", InterleavingRepository)
    assert await scheduler_module.reminder_scheduler._resume_one(task_id, timestamp=due + timedelta(seconds=1))

    detail = await task_service.get_task(task_id)
    assert detail is not None
    if concurrent == [1]:
        assert detail.task.status == "cancelled", "a committed cancel was overwritten"
    # The scheduler holds the task lock between its read and its write.
    assert concurrent == ["database is locked"]
    assert detail.task.status == "completed"


@pytest.mark.asyncio
async def test_resume_skips_a_reminder_cancelled_first() -> None:
    due = utc_now() + timedelta(seconds=30)
    snapshot = await task_service.schedule_reminder(text="gone", due_at=due, idempotency_key="reminder-cancelled")
    await task_service.control(snapshot.task.id, action="cancel", idempotency_key="reminder-cancelled-cancel")
    assert not await scheduler_module.reminder_scheduler._resume_one(
        snapshot.task.id, timestamp=due + timedelta(seconds=1))
    detail = await task_service.get_task(snapshot.task.id)
    assert detail is not None and detail.task.status == "cancelled"
