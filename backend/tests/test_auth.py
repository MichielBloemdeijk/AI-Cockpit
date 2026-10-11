"""Password-backed authentication; blank development hashes cannot prove login."""
from pydantic import SecretStr
from httpx import ASGITransport, AsyncClient
import pytest

from app.config import settings
from app.main import app
from app.services.auth import verify_password


def test_optional_password_and_explicit_mode(monkeypatch):
    monkeypatch.setattr(settings, "auth_password", SecretStr("fixture-\u00e9"))
    assert verify_password("fixture-\u00e9")
    assert not verify_password("wrong")
    monkeypatch.setattr(settings, "auth_password", SecretStr(""))
    monkeypatch.setattr(settings, "jarvis_access_mode", "personal")
    assert verify_password("")
    assert not verify_password("random")
    monkeypatch.setattr(settings, "jarvis_access_mode", "password")
    assert not verify_password("")


@pytest.mark.asyncio
async def test_real_login_cookie_private_data_and_logout(monkeypatch):
    monkeypatch.setattr(settings, "auth_password", SecretStr("fixture"))
    monkeypatch.setattr(settings, "session_cookie_secure", False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        assert (await http.get("/api/auth/status")).status_code == 401
        assert (await http.post("/api/auth/login", json={"password": "wrong"})).status_code == 401
        assert not http.cookies
        response = await http.post("/api/auth/login", json={"password": "fixture"})
        assert response.status_code == 200
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "SameSite=lax" in response.headers["set-cookie"]
        assert (await http.get("/api/auth/status")).status_code == 200
        assert (await http.post("/api/auth/logout")).status_code == 200
        assert (await http.get("/api/auth/status")).status_code == 401
        assert (await http.get("/api/results/future-id")).status_code == 401


@pytest.mark.asyncio
async def test_device_auth_waits_for_writer_and_observes_committed_device_state():
    import asyncio
    from starlette.requests import Request
    from sqlalchemy import update
    from app.db.session import session_scope
    from app.db.tables import JarvisDevice
    from app.services.jarvis_devices import jarvis_device_service
    code = await jarvis_device_service.create_pairing_code()
    device, credential = await jarvis_device_service.pair(code=code.code, label="before")
    request = Request({"type": "http", "headers": [(b"authorization", f"Bearer {credential}".encode())]})
    # Authentication must wait for this competing writer rather than selecting
    # an old WAL snapshot and failing when its heartbeat upgrades the snapshot.
    async with session_scope() as writer:
        await writer.execute(update(JarvisDevice).where(JarvisDevice.id == device.id).values(label="after"))
        authentication = asyncio.create_task(jarvis_device_service.authenticate(request))
        await asyncio.sleep(.1)
    authenticated = await asyncio.wait_for(authentication, 3)
    assert authenticated.label == "after"
    assert authenticated.last_seen_at is not None
    async with session_scope() as writer:
        from datetime import datetime, timezone
        await writer.execute(update(JarvisDevice).where(JarvisDevice.id == device.id).values(revoked_at=datetime.now(timezone.utc)))
    assert await jarvis_device_service.authenticate(request) is None
