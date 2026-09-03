"""Durable Web Push outbox for first-class task facts."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import session_scope
from app.db.tables import (
    JarvisLiveEvent,
    NotificationOutbox,
    PushSubscription,
    Task,
    TaskEvent,
    TaskPresentation,
    TaskQuestion,
)
TERMINAL_TASK_EVENTS = frozenset(
    {"task.completed", "task.failed", "task.interrupted", "task.cancelled"}
)
NOTIFIABLE_TASK_EVENTS = TERMINAL_TASK_EVENTS | {
    "task.progress",
    "task.requires_input",
    "task.reminder.due",
}
_MAX_RETRY_DELAY_SECONDS = 30.0
_outbox_tasks: set[asyncio.Task[None]] = set()
_scheduled_outbox_ids: set[str] = set()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sanitize_summary(value: str, limit: int = 120) -> str:
    text = re.sub(r"[`*_>#\[\](){}]", "", str(value or ""))
    text = re.sub(r"https?://\S+", "", text)
    text = " ".join(text.replace("\r", " ").replace("\n", " ").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _summary(task: Task, event: TaskEvent, question: TaskQuestion | None) -> str:
    payload = event.payload_json if isinstance(event.payload_json, dict) else {}
    if question is not None:
        return sanitize_summary(question.question)
    return sanitize_summary(
        str(payload.get("summary") or task.terminal_summary or f"Task {task.status}.")
    )


async def enqueue_task_notification(
    session: AsyncSession,
    *,
    task: Task,
    event: TaskEvent,
    question: TaskQuestion | None = None,
) -> str | None:
    """Enqueue one notification intent in the same transaction as its task fact."""
    if event.event_type not in NOTIFIABLE_TASK_EVENTS:
        return None
    if event.event_type == "task.requires_input" and question is None:
        raise ValueError("Blocking-question notifications require a question")
    question_id = None if question is None else question.id
    deep_link = f"/tasks/{task.id}"
    if question_id:
        deep_link += f"?question={question_id}"
    payload = {
        "task_event_id": event.id,
        "task_id": task.id,
        "question_id": question_id,
        "task_title": sanitize_summary(task.goal, limit=80) or "Jarvis task",
        "summary": _summary(task, event, question),
        "importance": task.importance,
        "event_kind": event.event_type,
    }
    dedupe_key = f"task-event:{event.id}"
    outbox_id = str(uuid4())
    await session.execute(
        sqlite_insert(NotificationOutbox)
        .values(
            id=outbox_id,
            task_id=task.id,
            task_event_id=event.id,
            question_id=question_id,
            event_kind=event.event_type,
            dedupe_key=dedupe_key,
            payload_json=payload,
            deep_link=deep_link,
            status="queued",
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )
    result = await session.execute(
        select(NotificationOutbox.id)
        .where(NotificationOutbox.dedupe_key == dedupe_key)
        .limit(1)
    )
    return result.scalar_one()


def payload_for_task_notification(outbox: NotificationOutbox) -> dict[str, Any]:
    payload = dict(outbox.payload_json or {})
    return {
        "event_id": payload.get("task_event_id"),
        "delivery_id": outbox.id,
        "task_id": outbox.task_id,
        "question_id": outbox.question_id,
        "task_title": payload.get("task_title") or "Jarvis update",
        "importance": payload.get("importance") or "normal",
        "event_kind": outbox.event_kind,
        "summary": payload.get("summary") or "There is a new Jarvis update.",
        "url": outbox.deep_link,
    }


async def schedule_task_notification(outbox_id: str, *, delay_seconds: float = 0.0) -> None:
    if outbox_id in _scheduled_outbox_ids:
        return
    _scheduled_outbox_ids.add(outbox_id)

    async def deliver() -> None:
        try:
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
            await _send_outbox_by_id(outbox_id)
        finally:
            _scheduled_outbox_ids.discard(outbox_id)

    task = asyncio.create_task(deliver(), name=f"task-push:{outbox_id}")
    _outbox_tasks.add(task)
    task.add_done_callback(_outbox_tasks.discard)


async def recover_pending_task_notifications() -> int:
    async with session_scope() as session:
        await session.execute(
            update(NotificationOutbox)
            .where(NotificationOutbox.status == "sending")
            .values(status="queued", next_attempt_at=None)
        )
        rows = list((await session.execute(
            select(NotificationOutbox)
            .where(NotificationOutbox.status.in_(["queued", "failed"]))
            .where(NotificationOutbox.attempts < settings.notification_retry_limit)
        )).scalars().all())
        for row in rows:
            row.next_attempt_at = None
    await route_pending_task_notifications(voice_connected=False)
    return len(rows)


async def route_pending_task_notifications(
    *,
    voice_connected: bool,
    voice_floor_held: bool = False,
) -> int:
    """Hold during legitimate speech, then apply the voice-delivery grace."""
    now = utc_now()
    due_ids: list[str] = []
    async with session_scope() as session:
        rows = list((await session.execute(
            select(NotificationOutbox)
            .where(NotificationOutbox.status.in_(["queued", "failed"]))
            .where(NotificationOutbox.acknowledged_at.is_(None))
            .where(NotificationOutbox.attempts < settings.notification_retry_limit)
            .order_by(NotificationOutbox.created_at.asc(), NotificationOutbox.id.asc())
        )).scalars().all())
        for row in rows:
            if voice_connected and voice_floor_held:
                row.next_attempt_at = None
                continue
            if voice_connected:
                if row.next_attempt_at is None:
                    row.next_attempt_at = now + timedelta(
                        seconds=max(0.0, settings.jarvis_notification_grace_seconds)
                    )
            elif row.status == "queued" or row.next_attempt_at is None:
                row.next_attempt_at = now
            next_attempt = row.next_attempt_at or now
            if next_attempt.tzinfo is None:
                next_attempt = next_attempt.replace(tzinfo=timezone.utc)
            if next_attempt <= now:
                due_ids.append(row.id)
    for outbox_id in due_ids:
        await schedule_task_notification(outbox_id)
    return len(due_ids)


async def _send_outbox_by_id(outbox_id: str) -> None:
    now = utc_now()
    async with session_scope() as session:
        claimed = await session.execute(
            update(NotificationOutbox)
            .where(NotificationOutbox.id == outbox_id)
            .where(NotificationOutbox.status.in_(["queued", "failed"]))
            .where(NotificationOutbox.attempts < settings.notification_retry_limit)
            .where(
                or_(
                    NotificationOutbox.next_attempt_at.is_(None),
                    NotificationOutbox.next_attempt_at <= now,
                )
            )
            .values(status="sending", next_attempt_at=None)
        )
        if not claimed.rowcount:
            return
        outbox = await session.get(NotificationOutbox, outbox_id)
        subscriptions = list((await session.execute(select(PushSubscription))).scalars().all())
        if outbox is None:
            return
        notification_payload = payload_for_task_notification(outbox)
        await session.execute(
            sqlite_insert(JarvisLiveEvent)
            .values(
                id=str(uuid4()),
                event_type="notification.available",
                source_domain="notification",
                source_id=outbox.id,
                source_sequence=None,
                payload_json=notification_payload,
                idempotency_key=f"notification:{outbox.id}",
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
        )
        body = json.dumps(notification_payload, ensure_ascii=False)

    if (
        not settings.web_push_enabled
        or not settings.vapid_private_key
        or not subscriptions
    ):
        async with session_scope() as session:
            row = await session.get(NotificationOutbox, outbox_id)
            if row is not None and row.status == "sending":
                row.status = "delivered"
                row.delivered_at = utc_now()
                row.last_error = None
        return

    successes = 0
    transient_errors: list[str] = []
    stale_ids: list[str] = []
    from pywebpush import webpush

    for subscription in subscriptions:
        try:
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
            successes += 1
        except Exception as exc:
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None) or getattr(exc, "status_code", None)
            if status_code in {404, 410}:
                stale_ids.append(subscription.id)
            else:
                transient_errors.append(str(exc)[:1000])

    async with session_scope() as session:
        for subscription_id in stale_ids:
            stale = await session.get(PushSubscription, subscription_id)
            if stale is not None:
                await session.delete(stale)
        row = await session.get(NotificationOutbox, outbox_id)
        if row is None or row.status != "sending":
            return
        row.attempts += 1
        if successes:
            row.status = "delivered"
            row.delivered_at = utc_now()
            row.last_error = None if not transient_errors else "; ".join(transient_errors)[:1000]
        elif transient_errors:
            row.status = "failed"
            retry_delay = min(_MAX_RETRY_DELAY_SECONDS, float(2 ** max(1, row.attempts)))
            row.next_attempt_at = utc_now() + timedelta(seconds=retry_delay)
            row.last_error = "; ".join(transient_errors)[:1000]
        else:
            row.status = "delivered"
            row.delivered_at = utc_now()
            row.last_error = None


async def acknowledge_task_notification(delivery_id: str, state: str) -> bool:
    if state not in {"opened", "spoken", "snoozed"}:
        raise ValueError("Delivery state must be opened, spoken, or snoozed")
    async with session_scope() as session:
        row = await session.get(NotificationOutbox, delivery_id)
        if row is None:
            return False
        if row.acknowledged_at is None:
            row.acknowledged_at = utc_now()
            row.status = "acknowledged"
            row.acknowledgement_state = state
            payload = dict(row.payload_json or {})
            payload["acknowledgement_state"] = state
            row.payload_json = payload
            if row.task_event_id:
                candidates = list((await session.execute(
                    select(TaskPresentation)
                    .where(TaskPresentation.source_task_event_id == row.task_event_id)
                    .where(TaskPresentation.state.in_(["pending", "reserved"]))
                )).scalars().all())
                for candidate in candidates:
                    candidate.state = "acknowledged"
                    candidate.reserved_session_id = None
                    candidate.reserved_generation = None
                    candidate.reserved_at = None
                    candidate.defer_until = None
        return True


__all__ = [
    "NOTIFIABLE_TASK_EVENTS",
    "acknowledge_task_notification",
    "enqueue_task_notification",
    "payload_for_task_notification",
    "recover_pending_task_notifications",
    "route_pending_task_notifications",
    "schedule_task_notification",
]
