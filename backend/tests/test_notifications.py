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
    with pytest.raises(ValueError, match="Delivery state must be opened"):
        await acknowledge_delivery("missing", "dismissed")


@pytest.mark.asyncio
async def test_retired_voice_and_legacy_notification_test_apis_are_absent() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": ""})
        assert (await client.get("/api/voice/health")).status_code == 404
        assert (await client.post("/api/notifications/test")).status_code == 404
        assert (await client.get("/api/notifications/vapid-public-key")).status_code == 200


@pytest.mark.asyncio
async def test_paired_device_can_ack_open_but_cannot_manage_push_and_revocation_fails_closed() -> None:
    from sqlalchemy import select
    from app.db.session import session_scope
    from app.db.tables import JarvisDevice, NotificationOutbox
    from app.db.repositories.conversations import utc_now
    from app.services.task_service import task_service
    from app.services.worker_coordinator import worker_coordinator
    from test_task_result_contract import claim, finish
    from app.services.result_telemetry import snapshot as metric_snapshot
    from test_task_result_contract import page_for
    worker = await worker_coordinator.activate(process_identity="native-open-ack")
    task = await task_service.dispatch(goal="Native ack fixture", origin_surface="test", idempotency_key="native-ack", result_request={"mode": "new"})
    await finish(worker, task.task.id, await claim(worker, task.task.id))
    page = await page_for(task.task.id)
    async with session_scope() as session:
        delivery_id = (await session.execute(select(NotificationOutbox).where(NotificationOutbox.task_id == task.task.id))).scalar_one().id
    endpoint = f"/api/notifications/deliveries/{delivery_id}/ack"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as admin, AsyncClient(transport=transport, base_url="http://testserver") as device:
        assert (await device.post(endpoint, json={"state": "opened"})).status_code == 401
        await admin.post("/api/auth/login", json={"password": ""})
        code = (await admin.post("/api/jarvis/devices/pairing-code")).json()["code"]
        paired = (await device.post("/api/jarvis/devices/pair", json={"code": code, "label": "Native ack fixture"})).json()
        device.headers["Authorization"] = "Bearer " + paired["credential"]
        assert not device.cookies
        for _ in range(2):
            assert (await device.post(endpoint, json={"state": "opened"})).status_code == 200
        assert (await device.post(endpoint, json={"state": "dismissed"})).status_code == 400
        assert (await device.post("/api/notifications/deliveries/missing/ack", json={"state": "opened"})).status_code == 404
        assert (await device.get("/api/notifications/vapid-public-key")).status_code == 401
        assert (await device.post("/api/notifications/subscriptions", json={"endpoint": "https://push.test/device", "keys": {"p256dh": "key", "auth": "auth"}})).status_code == 401
        assert (await device.delete("/api/notifications/subscriptions/missing")).status_code == 401
        # Scoped app capabilities must fail even alongside a valid owner cookie.
        assert (await admin.post(endpoint, json={"state": "opened"}, headers={"Authorization": "AppCapability invalid"})).status_code == 401
        assert (await admin.post(endpoint, json={"state": "opened"})).status_code == 200
        from app.services.result_telemetry import observation_queue
        assert await observation_queue.drain()
        async with session_scope() as session:
            metrics = await metric_snapshot(session, page)
            assert metrics["counts"]["notification_opened"] == 1
            (await session.get(JarvisDevice, paired["device_id"])).revoked_at = utc_now()
        assert (await device.post(endpoint, json={"state": "opened"})).status_code == 401
