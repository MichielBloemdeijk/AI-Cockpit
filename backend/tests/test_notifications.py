from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.notifications import (
    acknowledge_delivery,
    delete_subscription,
    upsert_subscription,
)


@pytest.mark.asyncio
async def test_subscription_upsert_is_idempotent_and_deletable() -> None:
    first = await upsert_subscription("https://push.test/device", "key-1", "auth-1", "browser-1")
    second = await upsert_subscription("https://push.test/device", "key-2", "auth-2", "browser-2")

    assert second == first
    assert await delete_subscription(first["id"]) is True
    assert await delete_subscription(first["id"]) is False


@pytest.mark.asyncio
async def test_acknowledgement_accepts_only_task_outbox_deliveries() -> None:
    assert await acknowledge_delivery("missing", "opened") is False
    with pytest.raises(ValueError, match="opened, spoken, or snoozed"):
        await acknowledge_delivery("missing", "dismissed")


@pytest.mark.asyncio
async def test_retired_voice_and_legacy_notification_test_apis_are_absent() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": "dev"})
        assert (await client.get("/api/voice/health")).status_code == 404
        assert (await client.post("/api/notifications/test")).status_code == 404
        assert (await client.get("/api/notifications/vapid-public-key")).status_code == 200
