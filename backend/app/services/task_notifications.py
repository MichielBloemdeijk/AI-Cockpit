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
from app.db.tables import NotificationOutbox, PushSubscription, Task, TaskEvent, TaskQuestion
TERMINAL_TASK_EVENTS = frozenset(
    {"task.completed", "task.failed", "task.interrupted", "task.cancelled"}
)
NOTIFIABLE_TASK_EVENTS = TERMINAL_TASK_EVENTS | {"task.requires_input", "task.reminder.due"}
_MAX_RETRY_DELAY_SECONDS = 30.0
_outbox_tasks: set[asyncio.Task[None]] = set()


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
    await session.execute(
        sqlite_insert(NotificationOutbox)
        .values(
            id=str(uuid4()),
            task_id=task.id,
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
        "summary": payload.get("summary") or "There is a new Jarvis update.",
        "url": outbox.deep_link,
    }


async def schedule_task_notification(outbox_id: str, *, delay_seconds: float = 0.0) -> None:
    if not settings.web_push_enabled or not settings.vapid_private_key:
        return
    async with session_scope() as session:
        subscription_id = (
            await session.execute(select(PushSubscription.id).limit(1))
        ).scalar_one_or_none()
    if subscription_id is None:
        return

    async def deliver() -> None:
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        await _send_outbox_by_id(outbox_id)

    task = asyncio.create_task(deliver(), name=f"task-push:{outbox_id}")
    _outbox_tasks.add(task)
    task.add_done_callback(_outbox_tasks.discard)


async def recover_pending_task_notifications() -> int:
    if not settings.web_push_enabled:
        return 0
    now = utc_now()
    async with session_scope() as session:
        await session.execute(
            update(NotificationOutbox)
            .where(NotificationOutbox.status == "sending")
            .values(status="queued", next_attempt_at=None)
        )
        rows = list(
            (
                await session.execute(
                    select(NotificationOutbox.id, NotificationOutbox.next_attempt_at)
                    .where(NotificationOutbox.status.in_(["queued", "failed"]))
                    .where(NotificationOutbox.attempts < settings.notification_retry_limit)
                    .order_by(NotificationOutbox.created_at.asc(), NotificationOutbox.id.asc())
                )
            ).all()
        )
    for outbox_id, next_attempt_at in rows:
        delay_seconds = 0.0
        if next_attempt_at is not None:
            if next_attempt_at.tzinfo is None:
                next_attempt_at = next_attempt_at.replace(tzinfo=timezone.utc)
            delay_seconds = max(0.0, (next_attempt_at - now).total_seconds())
        await schedule_task_notification(outbox_id, delay_seconds=delay_seconds)
    return len(rows)


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
        body = json.dumps(payload_for_task_notification(outbox), ensure_ascii=False)

    if not settings.vapid_private_key or not subscriptions:
        async with session_scope() as session:
            row = await session.get(NotificationOutbox, outbox_id)
            if row is not None and row.status == "sending":
                row.status = "queued"
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

    retry_delay: float | None = None
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
            row.status = "queued"
            row.next_attempt_at = None
            row.last_error = "No active Push subscriptions remain."

    if retry_delay is not None:
        await schedule_task_notification(outbox_id, delay_seconds=retry_delay)


async def acknowledge_task_notification(delivery_id: str, state: str) -> bool:
    if state not in {"opened", "spoken"}:
        raise ValueError("Delivery state must be opened or spoken")
    async with session_scope() as session:
        row = await session.get(NotificationOutbox, delivery_id)
        if row is None:
            return False
        if row.acknowledged_at is None:
            row.acknowledged_at = utc_now()
            row.status = "acknowledged"
            payload = dict(row.payload_json or {})
            payload["acknowledgement_state"] = state
            row.payload_json = payload
        return True


__all__ = [
    "NOTIFIABLE_TASK_EVENTS",
    "acknowledge_task_notification",
    "enqueue_task_notification",
    "payload_for_task_notification",
    "recover_pending_task_notifications",
    "schedule_task_notification",
]
