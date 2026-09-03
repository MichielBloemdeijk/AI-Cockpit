from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.session import session_scope
from app.db.tables import Task
from app.services.jarvis_daily import jarvis_daily_service
from app.services.jarvis_situation import jarvis_situation_projector
from app.services.reminder_scheduler import reminder_scheduler
from app.services.runtime_health import runtime_health_service
from app.services.task_service import task_service
from app.services.worker_coordinator import worker_coordinator


@pytest.mark.asyncio
async def test_offline_dispatch_waits_and_worker_recovery_promotes_it() -> None:
    task = await task_service.dispatch(
        goal="Prepare a concise brief",
        origin_surface="test",
        idempotency_key="wait-for-worker",
    )

    assert task.task.status == "waiting_for_worker"
    assert task.task.status_reason == "No live worker is available; this task has not started."

    worker = await worker_coordinator.activate(
        process_identity="recovery-worker",
        worker_id="recovery-worker",
    )
    assert worker is not None
    assert worker.promoted == (task.task.id,)
    promoted = await task_service.get_task(task.task.id)
    assert promoted is not None
    assert promoted.task.status == "queued"
    assert promoted.task.status_reason is None


@pytest.mark.asyncio
async def test_situation_snapshot_exposes_overdue_then_recently_completed_reminder() -> None:
    accepted = await jarvis_daily_service.accept_text_turn(
        "Set a reminder",
        idempotency_key="situation-log",
    )
    now = datetime.now(timezone.utc)
    reminder = await reminder_scheduler.create(
        text="Check the oven",
        due_at=now + timedelta(seconds=1),
        idempotency_key="situation-reminder",
        origin_conversation_id=accepted.conversation.id,
    )

    overdue = await jarvis_situation_projector.project(
        conversation_id=accepted.conversation.id,
        now=now + timedelta(seconds=2),
    )
    assert overdue.payload["reminders"]["overdue"][0]["task_id"] == reminder.task.id
    assert overdue.payload["reminders"]["overdue"][0]["overdue"] is True

    assert await reminder_scheduler.run_due(now=now + timedelta(seconds=2)) == 1
    completed = await jarvis_situation_projector.project(
        conversation_id=accepted.conversation.id,
        now=now + timedelta(seconds=2),
    )
    assert completed.revision > overdue.revision
    assert completed.payload["reminders"]["overdue"] == []
    assert completed.payload["reminders"]["recently_completed"][0]["task_id"] == reminder.task.id
    assert completed.payload["recent_terminal_tasks"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_situation_snapshot_exposes_durable_component_failures() -> None:
    accepted = await jarvis_daily_service.accept_text_turn(
        "Status",
        idempotency_key="health-log",
    )
    await runtime_health_service.healthy("reminder_scheduler", force=True)
    await runtime_health_service.failed("reminder_scheduler", "scheduler loop crashed")

    situation = await jarvis_situation_projector.project(
        conversation_id=accepted.conversation.id,
    )

    scheduler = situation.payload["component_health"]["reminder_scheduler"]
    assert scheduler["status"] == "degraded"
    assert scheduler["detail"] == "scheduler loop crashed"
    assert scheduler["failure_count"] == 1
    assert situation.payload["component_health"]["worker"]["status"] == "offline"


@pytest.mark.asyncio
async def test_misrouted_reminder_task_is_quarantined_with_audit_event() -> None:
    task = await task_service.dispatch(
        goal="Create a one-shot reminder at 22:21 local time",
        origin_surface="jarvis",
        idempotency_key="misrouted-reminder",
    )

    assert await task_service.repair_invalid_reminder_tasks() == 1
    detail = await task_service.get_task(task.task.id)
    assert detail is not None
    assert detail.task.status == "failed"
    assert detail.task.status_reason == "Reminder request was routed to background work and was not scheduled."
    assert detail.events[-1].event_type == "task.failed"
    assert detail.events[-1].payload_json["reason"] == "misrouted_reminder"


@pytest.mark.asyncio
async def test_scheduler_startup_immediately_processes_overdue_reminders() -> None:
    now = datetime.now(timezone.utc)
    reminder = await reminder_scheduler.create(
        text="Restart catch-up",
        due_at=now + timedelta(minutes=1),
        idempotency_key="startup-overdue",
    )
    async with session_scope() as session:
        task = (await session.execute(
            select(Task).where(Task.id == reminder.task.id)
        )).scalar_one()
        task.resume_at = now - timedelta(seconds=5)
        task.waiting_until = task.resume_at

    await reminder_scheduler.start()
    try:
        completed = await task_service.get_task(reminder.task.id)
        assert completed is not None
        assert completed.task.status == "completed"
        assert completed.events[-1].event_type == "task.reminder.due"
        assert completed.events[-1].payload_json["overdue"] is True
    finally:
        await reminder_scheduler.stop()
