"""Authenticated Web Push endpoints for away-task delivery."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.config import settings
from app.services.auth import require_auth
from app.services.conversation_store import conversation_store
from app.services.notifications import acknowledge_delivery, delete_subscription, get_public_key, notify_event, upsert_subscription
from app.services.voice_conversation import get_or_create_primary

router = APIRouter(prefix="/api/notifications", tags=["notifications"], dependencies=[Depends(require_auth)])


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=1)
    auth: str = Field(min_length=1)


class PushSubscriptionRequest(BaseModel):
    endpoint: str = Field(min_length=1, max_length=4096)
    keys: PushKeys
    user_agent: str | None = Field(default=None, max_length=512)


class DeliveryAckRequest(BaseModel):
    state: str


@router.get("/vapid-public-key")
async def vapid_public_key() -> dict[str, object]:
    return {"enabled": bool(settings.web_push_enabled and settings.vapid_public_key), "public_key": await get_public_key()}


@router.post("/subscriptions")
async def create_subscription(request: PushSubscriptionRequest) -> dict[str, str]:
    try:
        return await upsert_subscription(request.endpoint, request.keys.p256dh, request.keys.auth, request.user_agent)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete("/subscriptions/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_subscription(subscription_id: str) -> None:
    await delete_subscription(subscription_id)


@router.post("/deliveries/{delivery_id}/ack")
async def ack_delivery(delivery_id: str, request: DeliveryAckRequest) -> dict[str, bool]:
    try:
        found = await acknowledge_delivery(delivery_id, request.state)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery not found")
    return {"ok": True}


@router.post("/test")
async def test_notification() -> dict[str, object]:
    conversation = await get_or_create_primary()
    event = await conversation_store.append_event(
        conversation.id,
        run_id=None,
        actor_kind="system",
        event_type="notification.test",
        payload_json={
            "title": "Jarvis notification test",
            "summary": "This is a Jarvis notification test.",
            "speech_text": "This is a Jarvis notification test.",
            "importance": "high",
            "notify_policy": "completion",
            "requested_notification": True,
            "test_notification": True,
        },
    )
    deliveries = await notify_event(event.id)
    return {
        "enabled": bool(settings.web_push_enabled),
        "configured": bool(settings.vapid_private_key and settings.vapid_public_key),
        "event_id": event.id,
        "deliveries": deliveries,
    }
