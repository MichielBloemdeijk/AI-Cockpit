"""Scoped storage credentials cannot become a Cockpit/paired-device session."""
import asyncio

from httpx import ASGITransport, AsyncClient
from itsdangerous import SignatureExpired
import pytest
from sqlalchemy import select, update

from app.api import private
from app.db.session import session_scope
from app.db.tables import CockpitApp
from app.main import app
from app.services import app_capabilities
from app.services.app_catalog import write_data
from app.services.app_registry import app_registry_service
from app.services.auth import verify_session_token


@pytest.mark.asyncio
async def test_scoped_namespace_methods_cookie_override_tampering_and_revocation(monkeypatch):
    private._write_windows.clear()
    scoped_app = await app_registry_service.create_app(title="Scoped", slug="scoped")
    await app_registry_service.create_app(title="Other", slug="other")
    mint = f"/api/apps/{scoped_app.id}/storage-capability"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as owner:
        await owner.post("/api/auth/login", json={"password": ""})
        credential = (await owner.post(mint, json={})).json()
        assert credential["methods"] == ["GET", "PUT"] and credential["namespace"] == "state"
        assert not verify_session_token(credential["credential"])
        headers = {"Authorization": f"AppCapability {credential['credential']}"}
        path = "/api/app-catalog/scoped/data/state/settings"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers=headers) as scoped:
            assert (await scoped.get(path)).json() == {"value": None, "revision": 0}
            responses = await asyncio.gather(*[scoped.put(path, json={"value": {"theme": "dark"}, "expected_revision": 0}) for _ in range(2)])
            assert sorted(response.status_code for response in responses) == [200, 409]
            assert (await scoped.get(path)).json()["revision"] == 1
            # Encoded compound record keys match the scoped JS client contract.
            compound = path.replace("settings", "a%2Fb")
            assert (await scoped.put(compound, json={"value": {"nested": True}, "expected_revision": 0})).status_code == 200
            assert (await scoped.get(compound)).json()["value"] == {"nested": True}
            for forbidden in [path.replace("scoped", "other")]:
                assert (await scoped.get(forbidden)).status_code == 403
            assert (await scoped.get(path.replace("/state/", "/saved/"))).status_code == 403
            assert (await scoped.put(path, json={"value": {}, "expected_revision": 1, "result_public_id": "foreign"})).status_code == 422
            # Capabilities never unlock unrelated APIs, even with a valid cookie.
            for forbidden in ["/api/tasks", "/api/app-catalog", "/api/app-platform/apps", "/api/auth/status"]:
                assert (await owner.get(forbidden, headers=headers)).status_code == 401
            assert (await owner.post(mint, json={}, headers=headers)).status_code == 401
            assert (await owner.get(path, headers={"Authorization": "AppCapability"})).status_code == 401
            for variant in [" AppCapability invalid", "AppCapability\tinvalid", "appcapability   invalid"]:
                assert (await owner.get("/api/tasks", headers={"Authorization": variant})).status_code == 401
                assert (await owner.get(path, headers={"Authorization": variant})).status_code == 401
            assert (await scoped.get(path, headers={"Authorization": headers["Authorization"] + "tamper"})).status_code == 401
            readonly = (await owner.post(mint, json={"read_only": True})).json()
            ro_headers = {"Authorization": f"AppCapability {readonly['credential']}"}
            assert (await scoped.get(path, headers=ro_headers)).status_code == 200
            assert (await scoped.put(path, headers=ro_headers, json={"value": {}, "expected_revision": 1})).status_code == 403
            # Bumping the capability revision revokes every outstanding grant.
            async with session_scope() as session:
                await session.execute(update(CockpitApp).where(CockpitApp.slug == "scoped")
                    .values(capability_revision=CockpitApp.capability_revision + 1))
            assert (await scoped.get(path)).status_code == 403
            # Revocation does not delete committed state or affect owner sessions.
            assert (await owner.get(path)).json()["value"] == {"theme": "dark"}
            fresh = (await owner.post(mint, json={})).json()
            assert (await scoped.get(path, headers={"Authorization": f"AppCapability {fresh['credential']}"})).status_code == 200


@pytest.mark.asyncio
async def test_expired_capability_and_in_transaction_generation_check(monkeypatch):
    await app_registry_service.create_app(title="Scoped", slug="scoped")
    async with session_scope() as session:
        app_row = (await session.execute(select(CockpitApp).where(CockpitApp.slug == "scoped"))).scalar_one()
        grant = app_capabilities.StorageGrant(app_row.id, app_row.capability_revision)
        token = app_capabilities.issue_capability(app_row)["credential"]
        await session.execute(update(CockpitApp).where(CockpitApp.id == app_row.id).values(capability_revision=1))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as conflict:
        async with session_scope() as session:
            await write_data(session, slug="scoped", namespace="state", key="fresh", value={}, expected_revision=0, grant=grant)
    assert conflict.value.status_code == 403
    def expired(*args, **kwargs):
        raise SignatureExpired("expired")
    monkeypatch.setattr(app_capabilities, "serializer", lambda: type("Expired", (), {"loads": expired})())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        assert (await http.get("/api/app-catalog/scoped/data/state/fresh", headers={"Authorization": f"AppCapability {token}"})).status_code == 401
