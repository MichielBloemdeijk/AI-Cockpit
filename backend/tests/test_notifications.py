from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.db.tables import NotificationDelivery
from app.services.conversation_store import conversation_store
import app.services.notifications as notifications_module
from app.services.notifications import acknowledge_delivery, notify_event, payload_for_event, recover_pending_deliveries, sanitize_summary, should_notify, upsert_subscription


def test_notification_summary_is_plain_text_and_bounded() -> None:
    summary = sanitize_summary("**Finished**\nhttps://example.test/a " + "x " * 100)

    assert "**" not in summary
    assert "https://" not in summary
    assert len(summary) <= 120


def test_notification_policy_only_alerts_requested_or_material_updates() -> None:
    assert should_notify("task.failed", {"notify_policy": "important"})
    assert should_notify("task.completed", {"notify_policy": "important", "importance": "high"})
    assert not should_notify("task.progress", {"notify_policy": "important", "requested_notification": True})
    assert should_notify("task.progress", {"notify_policy": "all", "requested_notification": True})
    assert not should_notify("task.progress", {"notify_policy": "important", "importance": "normal"})
    assert not should_notify("task.completed", {"notify_policy": "none", "importance": "urgent"})
    assert should_notify("task.interruptions_summarized", {"notify_policy": "important", "importance": "urgent"})
    assert should_notify("notification.test", {})


def test_required_input_is_not_suppressed_by_completion_policy() -> None:
    assert should_notify("task.requires_input", {"notify_policy": "completion"})
    assert not should_notify("task.requires_input", {"notify_policy": "none"})
    assert not should_notify("agent.question.asked", {"notify_policy": "completion"})
    assert not should_notify("task.progress", {"notify_policy": "completion", "importance": "urgent"})


@pytest.mark.asyncio
async def test_notification_payload_targets_jarvis_workspace() -> None:
    conversation = await conversation_store.create_conversation(mode_hint="voice")
    event = await conversation_store.append_event(
        conversation.id,
        run_id=None,
        actor_kind="assistant",
        event_type="task.completed",
        payload_json={"summary": "Done"},
    )

    assert payload_for_event(event, "delivery-1")["url"] == (
        f"/jarvis?update={event.id}&delivery=delivery-1"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        (
            "task.completed",
            {
                "summary": "Requested task complete",
                "importance": "normal",
                "notify_policy": "completion",
                "requested_notification": True,
            },
        ),
        (
            "task.interruptions_summarized",
            {
                "summary": "AI-Cockpit restarted before two background tasks completed.",
                "importance": "urgent",
                "notify_policy": "important",
                "requested_notification": True,
            },
        ),
    ],
)
async def test_restart_recovers_eligible_event_missing_its_delivery_outbox_row(monkeypatch, event_type, payload) -> None:
    original_enabled = settings.web_push_enabled
    settings.web_push_enabled = True
    send_started = asyncio.Event()

    async def fake_send_by_id(delivery_id: str) -> None:
        send_started.set()

    monkeypatch.setattr(notifications_module, "_send_by_id", fake_send_by_id)
    try:
        subscription = await upsert_subscription("https://push.example/sub", "key", "auth")
        conversation = await conversation_store.create_conversation(mode_hint="voice")
        run = await conversation_store.start_run(conversation.id, kind="task", status="completed", metadata_json={})
        event = await conversation_store.append_event(
            conversation.id,
            run_id=run.id,
            actor_kind="assistant",
            event_type=event_type,
            payload_json=payload,
        )

        assert await recover_pending_deliveries() == 1
        await asyncio.wait_for(send_started.wait(), timeout=1)

        async with session_scope() as session:
            deliveries = list(
                (
                    await session.execute(
                        select(NotificationDelivery)
                        .where(NotificationDelivery.subscription_id == subscription["id"])
                        .where(NotificationDelivery.event_id == event.id)
                    )
                ).scalars().all()
            )
        assert len(deliveries) == 1
        assert deliveries[0].status == "queued"
    finally:
        settings.web_push_enabled = original_enabled


@pytest.mark.asyncio
async def test_concurrent_delivery_senders_claim_outbox_row_once(monkeypatch) -> None:
    subscription = await upsert_subscription("https://push.example/claim", "key", "auth")
    conversation = await conversation_store.create_conversation(mode_hint="voice")
    run = await conversation_store.start_run(conversation.id, kind="task", status="completed", metadata_json={})
    event = await conversation_store.append_event(
        conversation.id,
        run_id=run.id,
        actor_kind="assistant",
        event_type="task.completed",
        payload_json={"notify_policy": "completion", "summary": "Done"},
    )
    delivery = NotificationDelivery(subscription_id=subscription["id"], event_id=event.id, status="queued")
    async with session_scope() as session:
        session.add(delivery)
        await session.flush()
        delivery_id = delivery.id
    sends: list[str] = []

    async def fake_send(subscription_row, delivery_row, event_row):
        sends.append(delivery_row.id)

    monkeypatch.setattr(notifications_module, "_send", fake_send)
    await asyncio.gather(
        notifications_module._send_by_id(delivery_id),
        notifications_module._send_by_id(delivery_id),
    )

    assert sends == [delivery_id]


@pytest.mark.asyncio
async def test_notify_schedules_sender_only_after_delivery_row_commits(monkeypatch) -> None:
    original_enabled = settings.web_push_enabled
    settings.web_push_enabled = True
    scheduled: list[str] = []
    monkeypatch.setattr(notifications_module, "_schedule_delivery", lambda delivery_id, **_kwargs: scheduled.append(delivery_id))
    try:
        subscription = await upsert_subscription("https://push.example/commit", "key", "auth")
        conversation = await conversation_store.create_conversation(mode_hint="voice")
        run = await conversation_store.start_run(conversation.id, kind="task", status="completed", metadata_json={})
        event = await conversation_store.append_event(
            conversation.id,
            run_id=run.id,
            actor_kind="assistant",
            event_type="task.completed",
            payload_json={"notify_policy": "completion", "summary": "Done"},
        )

        assert await notify_event(event.id) == 1
        assert len(scheduled) == 1
        async with session_scope() as session:
            row = await session.get(NotificationDelivery, scheduled[0])
            assert row is not None
            assert row.subscription_id == subscription["id"]
            assert row.status == "queued"
    finally:
        settings.web_push_enabled = original_enabled


@pytest.mark.asyncio
async def test_transient_failure_schedules_bounded_live_retry(monkeypatch) -> None:
    original_limit = settings.notification_retry_limit
    settings.notification_retry_limit = 3
    subscription = await upsert_subscription("https://push.example/retry", "key", "auth")
    conversation = await conversation_store.create_conversation(mode_hint="voice")
    run = await conversation_store.start_run(conversation.id, kind="task", status="completed", metadata_json={})
    event = await conversation_store.append_event(
        conversation.id,
        run_id=run.id,
        actor_kind="assistant",
        event_type="task.completed",
        payload_json={"notify_policy": "completion", "summary": "Done"},
    )
    delivery = NotificationDelivery(subscription_id=subscription["id"], event_id=event.id, status="queued")
    async with session_scope() as session:
        session.add(delivery)
        await session.flush()
        delivery_id = delivery.id
    scheduled: list[tuple[str, float, str]] = []

    async def fail_transiently(_subscription, delivery_row, _event) -> None:
        async with session_scope() as session:
            row = await session.get(NotificationDelivery, delivery_row.id)
            assert row is not None
            row.status = "failed"
            row.attempts += 1

    def capture_schedule(target_id: str, *, delay_seconds: float = 0.0, name_prefix: str = "push") -> None:
        scheduled.append((target_id, delay_seconds, name_prefix))

    monkeypatch.setattr(notifications_module, "_send", fail_transiently)
    monkeypatch.setattr(notifications_module, "_schedule_delivery", capture_schedule)
    try:
        await notifications_module._send_by_id(delivery_id)
        assert scheduled == [(delivery_id, 2.0, "push-retry")]
        assert scheduled[0][1] <= 30.0
    finally:
        settings.notification_retry_limit = original_limit


@pytest.mark.asyncio
async def test_opened_ack_cannot_downgrade_a_spoken_delivery() -> None:
    subscription = await upsert_subscription("https://push.example/monotonic", "key", "auth")
    conversation = await conversation_store.create_conversation(mode_hint="voice")
    event = await conversation_store.append_event(
        conversation.id,
        run_id=None,
        actor_kind="assistant",
        event_type="task.completed",
        payload_json={"summary": "Done"},
    )
    delivery = NotificationDelivery(subscription_id=subscription["id"], event_id=event.id, status="sent")
    async with session_scope() as session:
        session.add(delivery)
        await session.flush()
        delivery_id = delivery.id

    assert await acknowledge_delivery(delivery_id, "spoken") is True
    assert await acknowledge_delivery(delivery_id, "opened") is True

    async with session_scope() as session:
        row = await session.get(NotificationDelivery, delivery_id)
        assert row is not None
        assert row.status == "spoken"
        assert row.spoken_at is not None
        assert row.opened_at is not None
    events = await conversation_store.list_events(conversation.id, limit=100)
    assert [item.event_type for item in events].count("notification.delivery.spoken") == 1
    assert [item.event_type for item in events].count("notification.delivery.opened") == 1


@pytest.mark.asyncio
async def test_concurrent_duplicate_acknowledgements_append_one_event() -> None:
    subscription = await upsert_subscription("https://push.example/ack-race", "key", "auth")
    conversation = await conversation_store.create_conversation(mode_hint="voice")
    event = await conversation_store.append_event(
        conversation.id,
        run_id=None,
        actor_kind="assistant",
        event_type="task.completed",
        payload_json={"summary": "Done"},
    )
    delivery = NotificationDelivery(subscription_id=subscription["id"], event_id=event.id, status="sent")
    async with session_scope() as session:
        session.add(delivery)
        await session.flush()
        delivery_id = delivery.id

    assert await asyncio.gather(
        acknowledge_delivery(delivery_id, "opened"),
        acknowledge_delivery(delivery_id, "opened"),
    ) == [True, True]

    events = await conversation_store.list_events(conversation.id, limit=100)
    assert [item.event_type for item in events].count("notification.delivery.opened") == 1
