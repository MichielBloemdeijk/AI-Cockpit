"""Authenticated Web Push subscription boundary for durable task notifications."""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.db.tables import PushSubscription
from app.services.task_notifications import (
    acknowledge_task_notification,
    recover_pending_task_notifications,
    utc_now,
)


async def get_public_key() -> str:
    return settings.vapid_public_key if settings.web_push_enabled else ""


async def upsert_subscription(
    endpoint: str,
    p256dh: str,
    auth: str,
    user_agent: str | None = None,
) -> dict[str, Any]:
    if not endpoint or not p256dh or not auth:
        raise ValueError("A complete push subscription is required")
    async with session_scope() as session:
        existing = (
            await session.execute(
                select(PushSubscription).where(PushSubscription.endpoint == endpoint)
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = PushSubscription(
                id=str(uuid4()),
                endpoint=endpoint,
                p256dh=p256dh,
                auth=auth,
                user_agent=user_agent,
            )
            session.add(existing)
        else:
            existing.p256dh = p256dh
            existing.auth = auth
            existing.user_agent = user_agent
            existing.updated_at = utc_now()
        await session.flush()
        saved = {"id": existing.id, "endpoint": existing.endpoint}
    await recover_pending_task_notifications()
    return saved


async def delete_subscription(subscription_id: str) -> bool:
    async with session_scope() as session:
        subscription = await session.get(PushSubscription, subscription_id)
        if subscription is None:
            return False
        await session.delete(subscription)
        return True


async def acknowledge_delivery(delivery_id: str, state: str) -> bool:
    normalized = str(state or "").lower()
    if normalized not in {"opened", "spoken", "snoozed"}:
        raise ValueError("Delivery state must be opened, spoken, or snoozed")
    return await acknowledge_task_notification(delivery_id, normalized)


__all__ = [
    "acknowledge_delivery",
    "delete_subscription",
    "get_public_key",
    "upsert_subscription",
]
