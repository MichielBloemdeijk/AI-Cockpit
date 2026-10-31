"""Companion shells, notification links and the old generated-app state. No model or browser."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import json

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import func, select

from app.api import private
from app.db.session import session_scope
from app.db.tables import AppDataRecord, CockpitApp, NotificationOutbox
from app.main import app
from app.services import app_catalog
from app.services.app_registry import app_registry_service
from app.services.task_service import task_service


@pytest.fixture(autouse=True)
def reset_rate_limit(monkeypatch):
    monkeypatch.setattr(private, "_write_windows", OrderedDict())


def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest.mark.asyncio
async def test_storage_deterministic_bytes_quota_concurrent_writes_and_rollback(monkeypatch):
    await app_registry_service.create_app(title="Storage test", slug="storage-test")
    async with client() as http:
        await http.post("/api/auth/login", json={"password": ""})
        prefix = "/api/app-catalog/storage-test/data/state/"
        value = {"text": "é" * 20}
        serialized_size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
        monkeypatch.setattr(app_catalog, "APP_QUOTA", serialized_size * 2)
        replies = await asyncio.gather(*[http.put(prefix + str(index), json={"value": value, "expected_revision": 0}) for index in range(3)])
        assert sorted(response.status_code for response in replies) == [200, 200, 413]
        async with session_scope() as session:
            catalog = (await session.execute(select(CockpitApp).where(CockpitApp.slug == "storage-test"))).scalar_one()
            assert catalog.storage_bytes == serialized_size * 2
            assert catalog.storage_bytes == (await session.execute(select(func.sum(AppDataRecord.size_bytes)).where(AppDataRecord.cockpit_app_id == catalog.id))).scalar_one()
        monkeypatch.setattr(app_catalog, "APP_QUOTA", 10 * 1024 * 1024)
        assert (await http.put(prefix + "large", json={"value": {"x": "a" * 65536}, "expected_revision": 0})).status_code == 413
        assert (await http.get(prefix + "large")).json() == {"value": None, "revision": 0}
        assert (await http.put(prefix + "fresh", json={"value": {}, "expected_revision": True})).status_code == 422
        assert (await http.get("/api/app-catalog/storage-test/data/saved/x")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "cancelled", "completed"])
async def test_notification_without_a_published_result_opens_task(status):
    from app.services.task_presentations import task_presentation_service
    created = await task_service.dispatch(goal="Check the backups", origin_surface="test", idempotency_key="no-result")
    task_id = created.task.id
    if status == "cancelled":
        await task_service.cancel(task_id, idempotency_key="no-result-cancel")
    else:
        await task_service.complete(task_id, status=status, summary="No typed publication", idempotency_key="no-result-terminal")
    await task_presentation_service.sync_candidates()
    async with session_scope() as session:
        delivery = (await session.execute(select(NotificationOutbox).where(NotificationOutbox.task_id == task_id))).scalar_one()
        assert delivery.deep_link == f"/tasks/{task_id}"


@pytest.mark.asyncio
async def test_companion_routes_are_allowlisted_data_free_and_private_apis_stay_authenticated(monkeypatch, tmp_path):
    from app import main
    from app.services.result_static import SHELL_FILES
    monkeypatch.setattr(main, "_result_export", tmp_path)
    monkeypatch.setattr(main, "_frontend_out", tmp_path / "missing-legacy")
    paths = {"/tasks/future-task": "task", "/login": "login", "/tasks": "tasks", "/workspace/apps": "apps"}
    async with client() as http:
        for route, page in paths.items():
            assert (await http.get(route)).status_code == 503
            filename = tmp_path / SHELL_FILES[page]
            filename.parent.mkdir(parents=True, exist_ok=True)
            filename.write_text(f"<html><script>/* {page} boot */</script></html>", encoding="utf-8")
            response = await http.get(route)
            assert response.status_code == 200
            assert f"/* {page} boot */" in response.text
            assert response.headers["cache-control"] == "private, no-store"
            assert "script-src 'self' 'sha256-" in response.headers["content-security-policy"]
            assert "noindex" in response.headers["x-robots-tag"]
        assert (await http.get("/api/tasks/future-task")).status_code == 401
        assert (await http.get("/api/app-platform/apps")).status_code == 401


@pytest.mark.asyncio
async def test_fixed_legacy_login_survives_missing_companion_build(monkeypatch, tmp_path):
    from app import main
    monkeypatch.setattr(main, "_result_export", tmp_path / "missing-companion")
    monkeypatch.setattr(main, "_frontend_out", tmp_path)
    (tmp_path / "login.html").write_text("<html>Legacy login</html>", encoding="utf-8")
    async with client() as http:
        response = await http.get("/login")
        assert response.status_code == 200 and "Legacy login" in response.text
        assert (await http.get("/tasks/future-task")).status_code == 503
