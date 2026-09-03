from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db.session import session_scope
from app.db.tables import JarvisLiveEvent, NotificationOutbox, TaskPresentation
from app.services.notifications import acknowledge_delivery
from app.services import task_notifications
from app.services.task_notifications import payload_for_task_notification
from app.services.task_presentations import task_presentation_service
from app.services.task_service import task_service
from app.services.worker_coordinator import worker_coordinator


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
    assert await worker_coordinator.activate(process_identity="notification-worker") is not None
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
    await task_presentation_service.sync_candidates()
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
    await task_presentation_service.sync_candidates()

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
    await task_presentation_service.sync_candidates()
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
    await task_presentation_service.sync_candidates()

    rows = await _outbox()
    assert len(rows) == 1
    assert rows[0].event_kind == "task.cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("ack_state", ["opened", "snoozed"])
async def test_notification_acknowledgement_suppresses_pending_voice_recap(
    ack_state: str,
) -> None:
    snapshot = await task_service.dispatch(
        goal="Keep delivery truth separate",
        idempotency_key="notification-ack-task",
        origin_surface="test",
    )
    await task_service.complete(
        snapshot.task.id,
        summary="Finished independently.",
        idempotency_key="notification-ack-complete",
    )
    await task_presentation_service.sync_candidates()
    row = (await _outbox())[0]
    async with session_scope() as session:
        presentation = (
            await session.execute(
                select(TaskPresentation).where(
                    TaskPresentation.source_task_event_id == row.task_event_id
                )
            )
        ).scalar_one()

    assert await acknowledge_delivery(row.id, ack_state) is True

    async with session_scope() as session:
        acknowledged = await session.get(NotificationOutbox, row.id)
        acknowledged_presentation = await session.get(TaskPresentation, presentation.id)
    assert acknowledged is not None and acknowledged.acknowledged_at is not None
    assert acknowledged.status == "acknowledged"
    assert acknowledged.acknowledgement_state == ack_state
    assert acknowledged_presentation is not None
    assert acknowledged_presentation.state == "acknowledged"
    assert acknowledged_presentation.presented_at is None


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
    await task_presentation_service.sync_candidates()
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

    monkeypatch.setattr(task_notifications, "schedule_task_notification", capture_schedule)

    assert await task_notifications.recover_pending_task_notifications() == 1
    assert scheduled[0][0] == row.id
    assert scheduled[0][1] == 0


@pytest.mark.asyncio
async def test_inactive_delivery_publishes_resumable_notification_event() -> None:
    snapshot = await task_service.dispatch(
        goal="Publish a device update",
        idempotency_key="notification-live-task",
        origin_surface="test",
    )
    await task_service.complete(
        snapshot.task.id,
        summary="Device update ready.",
        idempotency_key="notification-live-complete",
    )
    await task_presentation_service.sync_candidates()
    row = (await _outbox())[0]

    assert await task_notifications.route_pending_task_notifications(voice_connected=False) == 1
    await asyncio.sleep(0.05)
    async with session_scope() as session:
        delivered = await session.get(NotificationOutbox, row.id)
        live_event = (
            await session.execute(
                select(JarvisLiveEvent).where(
                    JarvisLiveEvent.idempotency_key == f"notification:{row.id}"
                )
            )
        ).scalar_one()
    assert delivered is not None and delivered.status == "delivered"
    assert live_event.event_type == "notification.available"
    assert live_event.payload_json["delivery_id"] == row.id
    assert live_event.payload_json["task_id"] == snapshot.task.id
    assert live_event.payload_json["url"] == f"/tasks/{snapshot.task.id}"


@pytest.mark.asyncio
async def test_connected_voice_holds_fallback_until_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = await task_service.dispatch(
        goal="Hold fallback notification",
        idempotency_key="notification-grace-task",
        origin_surface="test",
    )
    await task_service.complete(
        snapshot.task.id,
        summary="Fallback is ready.",
        idempotency_key="notification-grace-complete",
    )
    await task_presentation_service.sync_candidates()
    row = (await _outbox())[0]
    scheduled: list[tuple[str, float]] = []

    async def capture_schedule(outbox_id: str, *, delay_seconds: float = 0.0) -> None:
        scheduled.append((outbox_id, delay_seconds))

    monkeypatch.setattr(task_notifications.settings, "jarvis_notification_grace_seconds", 8.0)
    monkeypatch.setattr(task_notifications, "schedule_task_notification", capture_schedule)
    assert await task_notifications.route_pending_task_notifications(voice_connected=True) == 0
    assert scheduled == []
    assert await task_notifications.route_pending_task_notifications(voice_connected=False) == 1
    assert scheduled == [(row.id, 0.0)]


@pytest.mark.asyncio
async def test_retry_supersedes_an_unheard_terminal_update_deterministically() -> None:
    snapshot = await task_service.dispatch(
        goal="Retry a failed task",
        idempotency_key="notification-retry-task",
        origin_surface="test",
    )
    await task_service.complete(
        snapshot.task.id,
        status="failed",
        summary="First attempt failed.",
        idempotency_key="notification-retry-failed",
    )
    await task_presentation_service.sync_candidates()
    await task_service.retry(snapshot.task.id, idempotency_key="notification-retry-action")
    await task_service.complete(
        snapshot.task.id,
        summary="Second attempt completed.",
        idempotency_key="notification-retry-completed",
    )
    await task_presentation_service.sync_candidates()
    await task_presentation_service.sync_candidates()

    detail = await task_service.get_task(snapshot.task.id)
    rows = await _outbox()
    assert detail is not None
    assert [(item.state, item.required_facts_json["event_type"]) for item in detail.presentations] == [
        ("superseded", "task.failed"),
        ("pending", "task.completed"),
    ]
    assert [(row.event_kind, row.status) for row in rows] == [
        ("task.failed", "superseded"),
        ("task.completed", "queued"),
    ]
