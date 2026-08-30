from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db.session import session_scope
from app.db.tables import NotificationOutbox, TaskPresentation
from app.services.notifications import acknowledge_delivery
from app.services import task_notifications
from app.services.task_notifications import payload_for_task_notification
from app.services.task_service import task_service


async def _outbox() -> list[NotificationOutbox]:
    async with session_scope() as session:
        return list(
            (
                await session.execute(
                    select(NotificationOutbox).order_by(NotificationOutbox.created_at.asc())
                )
            ).scalars().all()
        )


@pytest.mark.asyncio
async def test_progress_does_not_push_and_blocking_question_enqueues_once() -> None:
    snapshot = await task_service.dispatch(
        goal="Prepare the launch report",
        idempotency_key="notification-question-task",
        origin_surface="test",
    )
    await task_service.publish_progress(
        snapshot.task.id,
        summary="Drafting the report",
        idempotency_key="ordinary-progress",
    )
    assert await _outbox() == []

    question = await task_service.ask_question(
        snapshot.task.id,
        question="Which launch date should I use?",
        idempotency_key="launch-date-question",
    )
    repeated = await task_service.ask_question(
        snapshot.task.id,
        question="Which launch date should I use?",
        idempotency_key="launch-date-question",
    )

    rows = await _outbox()
    assert repeated.id == question.id
    assert len(rows) == 1
    assert rows[0].event_kind == "task.requires_input"
    assert rows[0].question_id == question.id
    assert rows[0].deep_link == f"/tasks/{snapshot.task.id}?question={question.id}"
    assert payload_for_task_notification(rows[0])["url"] == rows[0].deep_link


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["completed", "failed", "interrupted"])
async def test_terminal_task_state_enqueues_once(terminal_status: str) -> None:
    snapshot = await task_service.dispatch(
        goal=f"Finish as {terminal_status}",
        idempotency_key=f"notification-{terminal_status}-task",
        origin_surface="test",
    )
    await task_service.complete(
        snapshot.task.id,
        status=terminal_status,
        summary=f"Task is {terminal_status}.",
        idempotency_key=f"terminal-{terminal_status}",
    )
    await task_service.complete(
        snapshot.task.id,
        status=terminal_status,
        summary=f"Task is {terminal_status}.",
        idempotency_key=f"terminal-{terminal_status}",
    )

    rows = await _outbox()
    assert len(rows) == 1
    assert rows[0].event_kind == f"task.{terminal_status}"
    assert rows[0].deep_link == f"/tasks/{snapshot.task.id}"


@pytest.mark.asyncio
async def test_cancelled_task_enqueues_once() -> None:
    snapshot = await task_service.dispatch(
        goal="Cancel this task",
        idempotency_key="notification-cancel-task",
        origin_surface="test",
    )
    await task_service.cancel(snapshot.task.id, idempotency_key="cancel-once")
    await task_service.cancel(snapshot.task.id, idempotency_key="cancel-once")

    rows = await _outbox()
    assert len(rows) == 1
    assert rows[0].event_kind == "task.cancelled"


@pytest.mark.asyncio
async def test_notification_acknowledgement_does_not_mark_speech_presented() -> None:
    snapshot = await task_service.dispatch(
        goal="Keep delivery truth separate",
        idempotency_key="notification-ack-task",
        origin_surface="test",
    )
    presentation = await task_service.enqueue_presentation(
        snapshot.task.id,
        candidate_type="terminal_result",
        coalescing_key=f"task:{snapshot.task.id}:terminal",
        idempotency_key="notification-ack-presentation",
    )
    await task_service.complete(
        snapshot.task.id,
        summary="Finished independently.",
        idempotency_key="notification-ack-complete",
    )
    row = (await _outbox())[0]

    assert await acknowledge_delivery(row.id, "opened") is True

    async with session_scope() as session:
        acknowledged = await session.get(NotificationOutbox, row.id)
        unchanged_presentation = await session.get(TaskPresentation, presentation.id)
    assert acknowledged is not None and acknowledged.acknowledged_at is not None
    assert acknowledged.status == "acknowledged"
    assert unchanged_presentation is not None
    assert unchanged_presentation.state == "pending"
    assert unchanged_presentation.presented_at is None


@pytest.mark.asyncio
async def test_recovery_reschedules_a_future_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = await task_service.dispatch(
        goal="Retry after a restart",
        idempotency_key="notification-recovery-task",
        origin_surface="test",
    )
    await task_service.complete(
        snapshot.task.id,
        summary="Finished before push delivery.",
        idempotency_key="notification-recovery-complete",
    )
    row = (await _outbox())[0]
    retry_at = task_notifications.utc_now() + timedelta(seconds=20)
    async with session_scope() as session:
        stored = await session.get(NotificationOutbox, row.id)
        assert stored is not None
        stored.status = "failed"
        stored.attempts = 1
        stored.next_attempt_at = retry_at

    scheduled: list[tuple[str, float]] = []

    async def capture_schedule(outbox_id: str, *, delay_seconds: float = 0.0) -> None:
        scheduled.append((outbox_id, delay_seconds))

    monkeypatch.setattr(task_notifications.settings, "web_push_enabled", True)
    monkeypatch.setattr(task_notifications, "schedule_task_notification", capture_schedule)

    assert await task_notifications.recover_pending_task_notifications() == 1
    assert scheduled[0][0] == row.id
    assert 0 < scheduled[0][1] <= 20
