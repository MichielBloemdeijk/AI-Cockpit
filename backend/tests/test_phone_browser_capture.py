from datetime import datetime, timezone
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
import pytest

from app.main import app
from app.services.task_capture import task_capture_service


@pytest.mark.asyncio
async def test_unlink_releases_only_device_live_and_emits_targeted_event():
    from app.db.session import session_scope
    from app.db.tables import JarvisLiveEvent, LiveSession
    from sqlalchemy import select
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as owner:
        await owner.post("/api/auth/login", json={"password": ""})
        code = (await owner.post("/api/jarvis/devices/pairing-code")).json()["code"]
        paired = (await owner.post("/api/jarvis/devices/pair", json={"code": code, "label": "Unlink test"})).json()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver",
            headers={"Authorization": f"Bearer {paired['credential']}"}) as phone:
            live = (await phone.post("/api/jarvis/live/calls", json={"controller_id": paired["device_id"],
                "provider_adapter": "fake", "activation_context": {"source": "system", "activation_id": str(uuid4())}})).json()["session"]
            await owner.delete(f"/api/jarvis/devices/{paired['device_id']}")
            assert (await phone.get("/api/jarvis/state")).status_code == 401
            await owner.delete(f"/api/jarvis/devices/{paired['device_id']}")
            async with session_scope() as db:
                assert (await db.get(LiveSession, live["id"])).lifecycle_state == "superseded"
                events = list((await db.execute(select(JarvisLiveEvent).where(JarvisLiveEvent.event_type == "device.unlinked"))).scalars())
                assert len(events) == 1 and events[0].target_device_id == paired["device_id"]
                assert events[0].payload_json == {"device_id": paired["device_id"]}


@pytest.mark.asyncio
async def test_paired_browser_capture_uses_authenticated_device_and_native_upload_scope():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as owner, AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as native, AsyncClient(transport=transport, base_url="http://testserver") as browser:
        assert (await owner.post("/api/auth/login", json={"password": ""})).status_code == 200
        code = (await owner.post("/api/jarvis/devices/pairing-code")).json()["code"]
        paired = (await native.post("/api/jarvis/devices/pair", json={"code": code, "label": "Phone"})).json()
        native.headers["Authorization"] = f"Bearer {paired['credential']}"
        web_cookie = (await native.post("/api/jarvis/devices/web-session")).headers["set-cookie"]
        browser.headers["Cookie"] = web_cookie.split(";", 1)[0]
        activation = str(uuid4())
        created = await browser.post("/api/jarvis/live/calls", json={
            "controller_id": "jarvis-browser-random-tab", "provider_adapter": "fake",
            "activation_context": {"source": "manual", "activation_id": activation}})
        assert created.status_code == 200, created.text
        live = created.json()["session"]
        assert live["controller_id"] == paired["device_id"]
        events = (await native.get("/api/jarvis/live/replay")).json()["events"]
        event = next(e for e in events if e["event_type"] == "capture.activation_requested")
        payload = event["payload"]
        assert payload["session_id"] == live["id"] and payload["activation_id"] == activation
        assert payload["capture_transport"] == "phone_browser"
        report = {"activation_id": activation, "capture_mode": "activation", "status": "captured",
            "revision": 3, "captured_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": payload["expires_at"], "session_id": live["id"], "generation": live["generation"]}
        path = f"/api/jarvis/captures/{payload['capture_id']}/status"
        assert (await browser.put(path, json=report)).status_code == 401
        assert (await native.put(path, json=report)).status_code == 200
        fresh = await task_capture_service.request_fresh(session_id=live["id"], generation=live["generation"], request_identity="turn-1")
        repeated = await task_capture_service.request_fresh(session_id=live["id"], generation=live["generation"], request_identity="turn-1")
        assert fresh.capture_id == repeated.capture_id
        replay = (await native.get("/api/jarvis/live/replay")).json()["events"]
        fresh_events = [e for e in replay if e["event_type"] == "capture.fresh_requested"]
        assert len(fresh_events) == 1 and fresh_events[0]["payload"]["capture_transport"] == "phone_browser"
        assert fresh_events[0]["payload"]["capture_mode"] == "fresh"
        redacted = (await browser.get("/api/jarvis/live/replay")).json()["events"]
        assert all("capture_id" not in e["payload"] for e in redacted if e["event_type"].startswith("capture."))
        # Another authenticated phone cannot bind pixels into this phone's call.
        code = (await owner.post("/api/jarvis/devices/pairing-code")).json()["code"]
        async with AsyncClient(transport=transport, base_url="http://testserver") as other:
            paired_other = (await other.post("/api/jarvis/devices/pair", json={"code": code, "label": "Other"})).json()
            other.headers["Authorization"] = f"Bearer {paired_other['credential']}"
            assert (await other.put(path, json={**report, "revision": 4})).status_code == 409
        # Superseding voice calls fence old capture requests and late callbacks.
        newer = await browser.post("/api/jarvis/live/calls", json={
            "controller_id": paired_other["device_id"], "provider_adapter": "fake"})
        assert newer.status_code == 200 and newer.json()["session"]["controller_id"] == paired["device_id"]
        assert newer.json()["session"]["activation_id"]
        assert (await native.put(path, json={**report, "revision": 4})).status_code == 409
        with pytest.raises(ValueError, match="unavailable"):
            await task_capture_service.request_fresh(session_id=live["id"], generation=live["generation"], request_identity="late-turn")


@pytest.mark.asyncio
async def test_owner_browser_does_not_request_arbitrary_phone_capture():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as owner:
        await owner.post("/api/auth/login", json={"password": ""})
        created = await owner.post("/api/jarvis/live/calls", json={
            "controller_id": "desktop-browser", "provider_adapter": "fake"})
        assert created.status_code == 200
        events = (await owner.get("/api/jarvis/live/replay")).json()["events"]
        assert not any(e["event_type"] == "capture.activation_requested" for e in events)
