"""Authenticated Web Push endpoints for away-task delivery."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.config import settings
from app.services.auth import require_auth
from app.services.jarvis.devices import require_jarvis_auth
from app.services.updates.push_subscriptions import (
    acknowledge_delivery,
    get_public_key,
    upsert_subscription,
)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=1)
    auth: str = Field(min_length=1)


class PushSubscriptionRequest(BaseModel):
    endpoint: str = Field(min_length=1, max_length=4096)
    keys: PushKeys
    user_agent: str | None = Field(default=None, max_length=512)


class DeliveryAckRequest(BaseModel):
    state: str


@router.get("/vapid-public-key", dependencies=[Depends(require_auth)])
async def vapid_public_key() -> dict[str, object]:
    return {"enabled": bool(settings.web_push_enabled and settings.vapid_public_key), "public_key": await get_public_key()}


@router.post("/subscriptions", dependencies=[Depends(require_auth)])
async def create_subscription(request: PushSubscriptionRequest) -> dict[str, str]:
    try:
        return await upsert_subscription(request.endpoint, request.keys.p256dh, request.keys.auth, request.user_agent)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/deliveries/{delivery_id}/ack", dependencies=[Depends(require_jarvis_auth)])
async def ack_delivery(delivery_id: str, request: DeliveryAckRequest) -> dict[str, bool]:
    try:
        found = await acknowledge_delivery(delivery_id, request.state)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery not found")
    return {"ok": True}
