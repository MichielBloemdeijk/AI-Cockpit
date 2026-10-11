"""Unpaid personal linking, cookie scope, revocation and origin acceptance."""
import asyncio
from datetime import timedelta
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
import pytest
from sqlalchemy import update

from app.config import settings
from app.db.session import session_scope
from app.db.tables import JarvisPairingCode
from app.main import app
from app.services.auth import SESSION_COOKIE_NAME, create_device_session, create_session_token, require_auth, verify_session_token
from app.services.jarvis_devices import jarvis_device_service, require_paired_device, require_unlocked_device, utc_now
from starlette.requests import Request
from fastapi import HTTPException


def test_obsolete_password_setting_fails_closed_without_exposing_configuration():
    from app.config import Settings
    from pydantic import ValidationError
    with pytest.raises(ValidationError) as failure:
        Settings(_env_file=None, auth_password_hash="private-obsolete-value", openai_api_key="private-api-fixture")
    assert "AUTH_PASSWORD_HASH is obsolete" in str(failure.value)
    assert "private-obsolete-value" not in str(failure.value)
    assert "private-api-fixture" not in str(failure.value)


def cookie_request(token):
    return Request({"type": "http", "headers": [(b"cookie", f"{SESSION_COOKIE_NAME}={token}".encode())]})


@pytest.mark.asyncio
async def test_personal_bootstrap_password_and_foreign_origin(monkeypatch):
    monkeypatch.setattr(settings, "auth_password", SecretStr(""))
    monkeypatch.setattr(settings, "jarvis_access_mode", "personal")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        assert (await http.get("/api/auth/status")).status_code == 401
        assert (await http.get("/api/auth/config")).json() == {"password_required": False}
        assert (await http.post("/api/auth/login", json={"password": ""}, headers={"Origin": "https://attacker.example"})).status_code == 403
        assert (await http.post("/api/auth/login", json={"password": ""}, headers={"Origin": "http://["})).status_code == 403
        assert (await http.post("/api/auth/login", json={"password": ""})).status_code == 200
        assert (await http.get("/api/auth/status")).status_code == 200
        monkeypatch.setattr(settings, "auth_password", SecretStr("private-fixture"))
        assert (await http.get("/api/auth/config")).json()["password_required"] is True
        assert (await http.post("/api/auth/login", json={"password": ""})).status_code == 401
        assert (await http.post("/api/auth/login", json={"password": "private-fixture"})).status_code == 200


@pytest.mark.asyncio
async def test_pairing_is_atomic_one_use_and_expiring():
    code = await jarvis_device_service.create_pairing_code()
    attempts = await asyncio.gather(*(jarvis_device_service.pair(code=code.code, label="fixture") for _ in range(2)), return_exceptions=True)
    assert sum(isinstance(result, tuple) for result in attempts) == 1
    assert sum(isinstance(result, ValueError) for result in attempts) == 1
    code = await jarvis_device_service.create_pairing_code()
    async with session_scope() as session:
        await session.execute(update(JarvisPairingCode).values(expires_at=utc_now() - timedelta(seconds=1)))
    with pytest.raises(ValueError, match="expired"):
        await jarvis_device_service.pair(code=code.code, label="expired")


@pytest.mark.asyncio
async def test_web_session_stays_device_scoped_locked_revocable_and_refreshable(monkeypatch):
    monkeypatch.setattr(settings, "session_cookie_secure", False)
    code = await jarvis_device_service.create_pairing_code()
    device, credential = await jarvis_device_service.pair(code=code.code, label="linked-fixture")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        bearer = {"Authorization": f"Bearer {credential}"}
        assert (await http.post("/api/jarvis/devices/web-session", headers=bearer)).status_code == 200
        token = http.cookies.get(SESSION_COOKIE_NAME)
        assert not verify_session_token(token)
        assert (await http.get("/api/auth/status")).status_code == 200
        assert (await http.get("/api/jarvis/devices")).status_code == 401
        assert (await http.get("/api/auth/status")).json() == {
            "authenticated": True, "scope": "device",
            "device": {"id": device.id, "label": "linked-fixture", "paired": True},
        }
        from app.db.tables import JarvisLiveEvent
        async with session_scope() as session:
            session.add(JarvisLiveEvent(id=device.id, event_type="context.requested", source_domain="tasks",
                source_id=device.id, source_sequence=1, target_device_id=device.id,
                payload_json={"upload_token": "native-only-fixture", "capture_id": "capture"},
                idempotency_key=f"personal-boundary:{device.id}"))
        browser_events = (await http.get("/api/jarvis/live/replay")).json()["events"]
        assert browser_events[0]["payload"] == {"targeted": True, "task_id": device.id}
        native_events = (await http.get("/api/jarvis/live/replay", headers=bearer)).json()["events"]
        assert native_events[0]["payload"]["upload_token"] == "native-only-fixture"
        assert (await http.post("/api/jarvis/devices/pairing-code")).status_code == 401
        assert (await http.get("/api/tasks")).status_code == 200
        assert (await http.post("/api/tasks/pool/control", json={"action": "stop"})).status_code == 403
        assert (await http.post("/api/auth/login", json={"password": ""})).status_code == 403
        request = cookie_request(token)
        with pytest.raises(HTTPException):
            await require_paired_device(request)
        assert (await http.patch(f"/api/jarvis/devices/{device.id}", headers=bearer, json={"lock_state": "locked"})).status_code == 200
        with pytest.raises(HTTPException) as locked:
            await require_unlocked_device(request)
        assert locked.value.status_code == 423
        assert (await http.post(f"/api/tasks/{device.id}/turns", json={"content": "test", "idempotency_key": "locked"})).status_code == 423
        # Forged owner cookie must not bypass lock checks with a valid bearer.
        forged = Request({"type": "http", "headers": [(b"cookie", b"cockpit_session=forged"), (b"authorization", bearer["Authorization"].encode())]})
        with pytest.raises(HTTPException):
            await require_unlocked_device(forged)
        http.cookies.clear()
        from itsdangerous import TimestampSigner, URLSafeTimedSerializer
        from app.services.auth import SESSION_MAX_AGE
        import time
        class OldSigner(TimestampSigner):
            def get_timestamp(self): return int(time.time()) - SESSION_MAX_AGE - 60
        expired = URLSafeTimedSerializer(settings.session_secret_key, signer=OldSigner).dumps({"device_id": device.id})
        http.cookies.set(SESSION_COOKIE_NAME, expired)
        assert (await http.get("/api/auth/status")).status_code == 401
        assert (await http.post("/api/jarvis/devices/web-session", headers=bearer)).status_code == 200
        owner = create_session_token()
        assert (await http.delete(f"/api/jarvis/devices/{device.id}", headers={"Cookie": f"{SESSION_COOKIE_NAME}={owner}"})).status_code == 200
        assert (await http.get("/api/auth/status")).status_code == 401
        assert (await http.post("/api/jarvis/devices/web-session", headers=bearer)).status_code == 401


@pytest.mark.asyncio
async def test_link_uses_configured_origin_and_contains_only_short_lived_code(monkeypatch):
    monkeypatch.setattr(settings, "jarvis_public_url", "https://fixture.tail.ts.net")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        http.cookies.set(SESSION_COOKIE_NAME, create_session_token())
        response = await http.post("/api/jarvis/devices/pairing-code", headers={"X-Forwarded-Host": "attacker.example"})
        assert response.status_code == 200
        link = response.json()
        assert link["server_url"] == "https://fixture.tail.ts.net"
        assert link["link"].startswith(link["server_url"] + "/link-phone#")
        assert "<svg" in link["qr_svg"]
        assert "credential" not in link["link"] and "attacker" not in link["link"]
        assert response.headers["cache-control"] == "private, no-store"
