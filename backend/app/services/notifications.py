"""Authenticated Web Push subscription and delivery service."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import case, select, update

from app.config import settings
from app.db.repositories.conversations import ConversationRepository
from app.db.session import session_scope
from app.db.tables import ConversationEvent, NotificationDelivery, PushSubscription

_MAX_RETRY_DELAY_SECONDS = 30.0
_delivery_tasks: set[asyncio.Task[None]] = set()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sanitize_summary(value: str, limit: int = 120) -> str:
    text = re.sub(r"[`*_>#\[\](){}]", "", str(value or ""))
    text = re.sub(r"https?://\S+", "", text)
    text = " ".join(text.replace("\r", " ").replace("\n", " ").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def should_notify(event_type: str, payload: dict[str, Any]) -> bool:
    if event_type == "notification.test" or payload.get("test_notification"):
        return True
    policy = str(payload.get("notify_policy") or "important").lower()
    if policy == "none":
        return False
    # Required input is actionable even when the user requested completion-only
    # notifications. Keep this check ahead of that policy's terminal-only return.
    if event_type == "task.requires_input":
        return True
    terminal = event_type in {
        "task.completed",
        "task.failed",
        "task.cancelled",
        "task.interrupted",
        "task.interruptions_summarized",
    }
    importance = str(payload.get("importance") or "normal").lower()
    if policy == "all":
        return True
    if policy == "completion":
        return terminal
    if terminal:
        return event_type in {"task.failed", "task.interrupted"} or importance in {"high", "urgent"} or bool(payload.get("requested_notification"))
    if event_type == "task.progress":
        return importance in {"high", "urgent"}
    return False


def payload_for_event(event: ConversationEvent, delivery_id: str) -> dict[str, Any]:
    payload = event.payload_json if isinstance(event.payload_json, dict) else {}
    summary = sanitize_summary(str(payload.get("speech_text") or payload.get("summary") or "Task update"))
    title = sanitize_summary(str(payload.get("title") or "Jarvis update"), limit=80)
    return {
        "event_id": event.id,
        "delivery_id": delivery_id,
        "task_title": title,
        "importance": str(payload.get("importance") or "normal"),
        "summary": summary,
        "url": f"/voice?update={event.id}&delivery={delivery_id}",
    }


async def get_public_key() -> str:
    return settings.vapid_public_key if settings.web_push_enabled else ""


async def upsert_subscription(endpoint: str, p256dh: str, auth: str, user_agent: str | None = None) -> dict[str, Any]:
    if not endpoint or not p256dh or not auth:
        raise ValueError("A complete push subscription is required")
    async with session_scope() as session:
        existing = (await session.execute(select(PushSubscription).where(PushSubscription.endpoint == endpoint))).scalar_one_or_none()
        if existing is None:
            existing = PushSubscription(id=str(uuid4()), endpoint=endpoint, p256dh=p256dh, auth=auth, user_agent=user_agent)
            session.add(existing)
        else:
            existing.p256dh = p256dh
            existing.auth = auth
            existing.user_agent = user_agent
            existing.updated_at = utc_now()
        await session.flush()
        return {"id": existing.id, "endpoint": existing.endpoint}


async def delete_subscription(subscription_id: str) -> bool:
    async with session_scope() as session:
        subscription = await session.get(PushSubscription, subscription_id)
        if subscription is None:
            return False
        await session.delete(subscription)
        return True


async def _send(subscription: PushSubscription, delivery: NotificationDelivery, event: ConversationEvent) -> None:
    if not settings.web_push_enabled or not settings.vapid_private_key:
        async with session_scope() as session:
            row = await session.get(NotificationDelivery, delivery.id)
            if row is not None and row.status == "sending":
                row.status = "queued"
        return
    try:
        from pywebpush import webpush

        body = json.dumps(payload_for_event(event, delivery.id), ensure_ascii=False)
        await asyncio.to_thread(
            webpush,
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
            },
            data=body,
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": settings.vapid_subject},
        )
        async with session_scope() as session:
            row = await session.get(NotificationDelivery, delivery.id)
            if row is not None:
                row.attempts += 1
                row.sent_at = utc_now()
                if row.status not in {"opened", "spoken"}:
                    row.status = "sent"
    except Exception as exc:
        async with session_scope() as session:
            row = await session.get(NotificationDelivery, delivery.id)
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None) or getattr(exc, "status_code", None)
            if status_code in {404, 410}:
                stale_subscription = await session.get(PushSubscription, subscription.id)
                if stale_subscription is not None:
                    await session.delete(stale_subscription)
            elif row is not None:
                row.status = "failed"
                row.attempts += 1
                row.last_error = str(exc)[:1000]


async def notify_event(event_id: str) -> int:
    if not settings.web_push_enabled:
        return 0
    async with session_scope() as session:
        event = await session.get(ConversationEvent, event_id)
        if event is None:
            return 0
        payload = event.payload_json if isinstance(event.payload_json, dict) else {}
        if not should_notify(event.event_type, payload):
            return 0
        subscriptions = list((await session.execute(select(PushSubscription))).scalars().all())
        delivery_ids: list[str] = []
        for subscription in subscriptions:
            existing = (
                await session.execute(
                    select(NotificationDelivery)
                    .where(NotificationDelivery.subscription_id == subscription.id)
                    .where(NotificationDelivery.event_id == event.id)
                )
            ).scalar_one_or_none()
            if existing is None:
                delivery = NotificationDelivery(id=str(uuid4()), subscription_id=subscription.id, event_id=event.id, status="queued")
                session.add(delivery)
                await session.flush()
                delivery_ids.append(delivery.id)
            elif existing.status in {"queued", "failed"} and existing.attempts < settings.notification_retry_limit:
                delivery_ids.append(existing.id)
    # session_scope commits on exit.  Do not start a sender until every outbox
    # row it may claim is visible to a separate database connection.
    for delivery_id in delivery_ids:
        _schedule_delivery(delivery_id)
    return len(delivery_ids)


async def recover_pending_deliveries() -> int:
    """Rebuild a durable outbox gap, then retry queued/failed deliveries."""
    if not settings.web_push_enabled:
        return 0
    async with session_scope() as session:
        # A process crash can leave an in-flight row in `sending`; no sender
        # survives a restart, so return those rows to the claimable outbox.
        await session.execute(
            update(NotificationDelivery)
            .where(NotificationDelivery.status == "sending")
            .values(status="queued")
        )
        subscriptions = list((await session.execute(select(PushSubscription))).scalars().all())
        recoverable_event_types = {
            "notification.test",
            "task.completed",
            "task.failed",
            "task.cancelled",
            "task.interrupted",
            "task.interruptions_summarized",
            "task.progress",
            "task.requires_input",
        }
        for subscription in subscriptions:
            existing_event_ids = set(
                (
                    await session.execute(
                        select(NotificationDelivery.event_id).where(NotificationDelivery.subscription_id == subscription.id)
                    )
                ).scalars().all()
            )
            candidate_events = list(
                (
                    await session.execute(
                        select(ConversationEvent)
                        .where(ConversationEvent.event_type.in_(sorted(recoverable_event_types)))
                    )
                ).scalars().all()
            )
            for event in candidate_events:
                payload = event.payload_json if isinstance(event.payload_json, dict) else {}
                event_created = event.created_at.replace(tzinfo=None)
                subscription_created = subscription.created_at.replace(tzinfo=None)
                if event_created < subscription_created:
                    continue
                if event.id in existing_event_ids or not should_notify(event.event_type, payload):
                    continue
                session.add(
                    NotificationDelivery(
                        id=str(uuid4()),
                        subscription_id=subscription.id,
                        event_id=event.id,
                        status="queued",
                    )
                )
                existing_event_ids.add(event.id)
        await session.flush()
        rows = list(
            (
                await session.execute(
                    select(NotificationDelivery)
                    .where(NotificationDelivery.status.in_(["queued", "failed"]))
                    .where(NotificationDelivery.attempts < settings.notification_retry_limit)
                )
            ).scalars().all()
        )
        work: list[str] = []
        for delivery in rows:
            event = await session.get(ConversationEvent, delivery.event_id)
            subscription = await session.get(PushSubscription, delivery.subscription_id)
            if event is not None and subscription is not None:
                work.append(delivery.id)
    for delivery_id in work:
        _schedule_delivery(delivery_id, name_prefix="push-recover")
    return len(work)


def _retry_delay_seconds(attempts: int) -> float:
    return min(_MAX_RETRY_DELAY_SECONDS, float(2 ** max(1, attempts)))


def _schedule_delivery(delivery_id: str, *, delay_seconds: float = 0.0, name_prefix: str = "push") -> None:
    async def deliver() -> None:
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        await _send_by_id(delivery_id)

    task = asyncio.create_task(deliver(), name=f"{name_prefix}:{delivery_id}")
    # asyncio keeps only weak references to tasks. Retain live outbox workers
    # so a delayed retry cannot disappear before it claims the durable row.
    _delivery_tasks.add(task)
    task.add_done_callback(_delivery_tasks.discard)


async def _send_by_id(delivery_id: str) -> None:
    async with session_scope() as session:
        claimed = await session.execute(
            update(NotificationDelivery)
            .where(NotificationDelivery.id == delivery_id)
            .where(NotificationDelivery.status.in_(["queued", "failed"]))
            .where(NotificationDelivery.attempts < settings.notification_retry_limit)
            .values(status="sending")
        )
        if not claimed.rowcount:
            return
        delivery = await session.get(NotificationDelivery, delivery_id)
        if delivery is None:
            return
        event = await session.get(ConversationEvent, delivery.event_id)
        subscription = await session.get(PushSubscription, delivery.subscription_id)
        if delivery is None or event is None or subscription is None:
            return
    await _send(subscription, delivery, event)
    # Failed transient deliveries remain durable and receive bounded live
    # retries.  A restart still reconstructs this work from the same outbox.
    async with session_scope() as session:
        failed = await session.get(NotificationDelivery, delivery_id)
        retry_attempt = (
            failed.attempts
            if failed is not None
            and failed.status == "failed"
            and failed.attempts < settings.notification_retry_limit
            else None
        )
    if retry_attempt is not None:
        _schedule_delivery(
            delivery_id,
            delay_seconds=_retry_delay_seconds(retry_attempt),
            name_prefix="push-retry",
        )


async def acknowledge_delivery(delivery_id: str, state: str) -> bool:
    normalized = str(state or "").lower()
    if normalized not in {"opened", "spoken"}:
        raise ValueError("Delivery state must be opened or spoken")
    async with session_scope() as session:
        acknowledged_at = utc_now()
        if normalized == "opened":
            transition = await session.execute(
                update(NotificationDelivery)
                .where(NotificationDelivery.id == delivery_id)
                .where(NotificationDelivery.opened_at.is_(None))
                .values(
                    opened_at=acknowledged_at,
                    status=case(
                        (NotificationDelivery.status == "spoken", "spoken"),
                        else_="opened",
                    ),
                )
            )
        else:
            transition = await session.execute(
                update(NotificationDelivery)
                .where(NotificationDelivery.id == delivery_id)
                .where(NotificationDelivery.spoken_at.is_(None))
                .values(spoken_at=acknowledged_at, status="spoken")
            )
        # Claim the acknowledgement marker with the conditional UPDATE before
        # reading related rows. This avoids SQLite read-then-write lock upgrade
        # races when the service worker and click navigation acknowledge at once.
        delivery = await session.get(NotificationDelivery, delivery_id)
        if delivery is None:
            return False
        event = await session.get(ConversationEvent, delivery.event_id)
        if transition.rowcount and event is not None:
            repo = ConversationRepository(session)
            await repo.append_event(
                conversation_id=event.conversation_id,
                run_id=event.run_id,
                actor_kind="system",
                event_type=f"notification.delivery.{normalized}",
                payload_json={"delivery_id": delivery_id, "state": normalized},
            )
    return True
