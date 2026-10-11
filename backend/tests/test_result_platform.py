"""M5 generic platform contracts. No model, emulator or human interaction."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import func, select

from app.api import results
from app.db.repositories.tasks import TaskRepository
from app.db.session import session_scope
from app.db.tables import AppDataRecord, CockpitApp, ResultPage, ResultPageEvent, ResultPageRevision, TaskArtifact, NotificationOutbox
from app.main import app
from app.services import app_catalog
from app.services.app_registry import app_registry_service
from app.services.task_results import allocate_result, fail_document_result, publish_result, result_snapshot
from app.services.task_service import task_service


@pytest.fixture(autouse=True)
def reset_rate_limit(monkeypatch):
    monkeypatch.setattr(results, "_write_windows", OrderedDict())


@pytest.mark.asyncio
async def test_sse_disconnect_finishes_short_database_session_cleanup(monkeypatch):
    import anyio
    cleaned = []
    @asynccontextmanager
    async def pending_cleanup():
        try:
            yield object()
        finally:
            await anyio.sleep(0)  # cancellation checkpoint during DB close
            cleaned.append(True)
    monkeypatch.setattr(results, "read_session_scope", pending_cleanup)
    with anyio.CancelScope() as disconnected:
        async with results.stream_read_session():
            disconnected.cancel()
    assert cleaned == [True]


@asynccontextmanager
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as instance:
        assert (await instance.post("/api/auth/login", json={"password": ""})).status_code == 200
        yield instance


async def page_fixture(schema="document.v1", title=None):
    created = await task_service.dispatch(goal="M5 deterministic fixture", origin_surface="test", idempotency_key=str(uuid4()))
    async with session_scope() as session:
        page = await allocate_result(session, task_id=created.task.id, run_id=created.task.current_run_id, schema=schema)
        assert await allocate_result(session, task_id=created.task.id, run_id=created.task.current_run_id, schema=schema) is page
        if title:
            page.title, page.summary = title, "A compact searchable summary"
        return SimpleNamespace(id=page.id, public_id=page.public_id, task_id=page.task_id, run_id=page.active_execution_run_id)


@pytest.mark.asyncio
async def test_catalog_generated_compatibility_and_collections():
    generated = await app_registry_service.create_app(title="Existing custom app", slug="documents")
    documents = [await page_fixture(title=title) for title in ["Apple", "Banana", "Cherry", "Literal % sign"]]
    await page_fixture("recipe.v1", "Other app")
    async with client() as http:
        catalog = (await http.get("/api/app-catalog")).json()
        assert {row["slug"] for row in catalog if row["kind"] == "collection"} == {"documents", "recipes"}
        custom = next(row for row in catalog if row["generated_app_id"] == generated.id)
        assert custom["slug"] != "documents" and custom["route_path"] == generated.route_path
        assert (await http.get("/api/apps/slug/documents")).json()["id"] == generated.id
        first = (await http.get("/api/app-catalog/documents/results", params={"order": "title", "limit": 2})).json()
        assert [row["title"] for row in first["items"]] == ["Apple", "Banana"]
        assert all("content" not in row for row in first["items"])
        second = (await http.get("/api/app-catalog/documents/results", params={"order": "title", "limit": 2, "cursor": first["next_cursor"]})).json()
        assert [row["title"] for row in second["items"]] == ["Cherry", "Literal % sign"] and second["next_cursor"] is None
        assert len((await http.get("/api/app-catalog/documents/results", params={"search": "%"})).json()["items"]) == 1
        assert (await http.get("/api/app-catalog/recipes/results", params={"order": "title", "cursor": first["next_cursor"]})).status_code == 400
        assert (await http.get("/api/app-catalog/documents/results", params={"cursor": "garbage"})).status_code == 400
        assert (await http.get(f"/api/app-catalog/{custom['slug']}/results")).status_code == 404
        await app_registry_service.update_app(generated.id, title="Changed title")
        assert (await http.get(f"/api/app-catalog/{custom['slug']}")).json()["title"] == "Changed title"


@pytest.mark.asyncio
@pytest.mark.parametrize("schema,slug", [("document.v1", "documents"), ("recipe.v1", "recipes")])
async def test_saved_reload_cas_and_isolation_without_worker_or_content(schema, slug):
    page = await page_fixture(schema)
    other = await page_fixture(schema)
    async with client() as http:
        snapshot = await http.get(f"/api/results/{page.public_id}")
        assert snapshot.json()["actions"] == [{"id": "set_saved", "value": {"saved": False}, "revision": 0}]
        original_content_revision = snapshot.json()["revision_number"]
        url = f"/api/results/{page.public_id}/actions/set_saved"
        responses = await asyncio.gather(*[http.post(url, json={"saved": True, "expected_revision": 0}) for _ in range(2)])
        assert sorted(response.status_code for response in responses) == [200, 409]
        refreshed = (await http.get(f"/api/results/{page.public_id}")).json()
        assert refreshed["actions"][0]["value"] == {"saved": True}
        assert refreshed["revision_number"] == original_content_revision and refreshed["content"] is None
        assert (await http.get(f"/api/results/{other.public_id}")).json()["actions"][0]["value"] == {"saved": False}
        data_url = f"/api/app-catalog/{slug}/data/saved/{page.public_id}"
        assert (await http.get(data_url)).json() == {"value": {"saved": True}, "revision": 1}
        assert (await http.put(data_url, json={"value": {"saved": False}, "expected_revision": 1})).json()["revision"] == 2
        foreign_slug = "recipes" if slug == "documents" else "documents"
        assert (await http.get(f"/api/app-catalog/{foreign_slug}/data/saved/{page.public_id}")).status_code == 404
        assert (await http.post(url, json={"saved": "true", "expected_revision": 2})).status_code == 422
        assert (await http.post(url.replace("set_saved", "delete"), json={"saved": True, "expected_revision": 2})).status_code == 404
        assert (await http.put(data_url, json={"value": {"saved": True, "body": "override"}, "expected_revision": 2})).status_code == 422
        assert (await http.get(data_url.replace("/saved/", "/content/"))).status_code == 404
    async with session_scope() as session:
        assert (await session.execute(select(func.count()).select_from(ResultPageRevision))).scalar_one() == 0
        rows = list((await session.execute(select(AppDataRecord))).scalars())
        assert len(rows) == 1 and rows[0].scope_key == page.id


@pytest.mark.asyncio
async def test_storage_deterministic_bytes_quota_concurrent_writes_and_rollback(monkeypatch):
    generated = await app_registry_service.create_app(title="Storage test", slug="storage-test")
    async with client() as http:
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
        assert (await http.put(prefix + "invalid-scope", json={"value": {}, "expected_revision": 0, "result_public_id": "foreign"})).status_code == 404
        missing = (await http.get(prefix + "large")).json()
        assert missing == {"value": None, "revision": 0}
        assert (await http.put(prefix + "fresh", json={"value": {}, "expected_revision": True})).status_code == 422


@pytest.mark.asyncio
async def test_safe_durable_events_replay_live_terminal_reset_and_atomic_rollback():
    page = await page_fixture()
    request = SimpleNamespace(is_disconnected=lambda: asyncio.sleep(0, result=False))
    stream = results.result_events(page.public_id, request, None)
    first = await anext(stream)
    assert first["event"] == "snapshot" and json.loads(first["data"])["status"] == "building"
    async with session_scope() as session:
        await TaskRepository(session).append_event(task_id=page.task_id, execution_run_id=page.run_id,
            event_type="task.progress", payload_json={"summary": "secret API key and speculative narration"}, idempotency_key="safe-progress")
        artifact = TaskArtifact(id=str(uuid4()), task_id=page.task_id, execution_run_id=page.run_id,
            artifact_type="workflow_result", mime_type="application/json", title="Published")
        session.add(artifact)
        await session.flush()
        published = await publish_result(session, task_id=page.task_id, run_id=page.run_id, attempt_id=None,
            artifact=artifact, content={"title": "Published", "summary": "Simple", "body": "Useful", "source_event_sequences": []}, publication_key="publish")
        assert published.first_published_at and published.last_published_at
        head_revision = published.current_revision_id
        await fail_document_result(session, task_id=page.task_id, run_id=page.run_id, reason="late failure")
        assert published.current_revision_id == head_revision and published.warning is None
    live_events = [await asyncio.wait_for(anext(stream), 2) for _ in range(2)]
    await stream.aclose()
    assert [item["event"] for item in live_events] == ["result", "result"]
    assert json.loads(live_events[-1]["data"])["status"] == "ready"
    assert "secret" not in json.dumps(live_events)
    replay = results.result_events(page.public_id, request, "0")
    initial = await anext(replay)
    assert not json.loads(initial["data"])["cursor_reset"]
    event_ids = [int((await anext(replay))["id"]) for _ in range(3)]
    assert event_ids == [1, 2, 3]
    await replay.aclose()
    for cursor in ["-1", "99999", "invalid", "01"]:
        reset = results.result_events(page.public_id, request, cursor)
        assert json.loads((await anext(reset))["data"])["cursor_reset"]
        await reset.aclose()
    async with session_scope() as session:
        assert (await session.execute(select(func.count()).select_from(ResultPageRevision))).scalar_one() == 1
        revision = await session.get(ResultPageRevision, head_revision)
        assert revision.schema_id == "document.v1" and revision.base_revision_number == 0 and revision.publication_key.endswith(":publish")
    with pytest.raises(RuntimeError):
        async with session_scope() as session:
            await app_catalog.write_data(session, slug="documents", namespace="saved", key=page.public_id,
                value={"saved": True}, expected_revision=0)
            raise RuntimeError("Injected transaction failure")
    async with session_scope() as session:
        assert (await session.execute(select(func.count()).select_from(AppDataRecord))).scalar_one() == 0


@pytest.mark.asyncio
async def test_unrelated_task_followup_and_late_event_do_not_change_older_result():
    page = await page_fixture()
    await task_service.cancel(page.task_id, idempotency_key="initial-cancel")
    async with session_scope() as session:
        original = await session.get(ResultPage, page.id)
        original_revision = original.state_revision
    await task_service.continue_task(page.task_id, content="An unrelated agent task", idempotency_key="followup", origin="test", execution_kind="agent")
    async with session_scope() as session:
        before = await result_snapshot(session, await session.get(ResultPage, page.id))
        assert before["status"] == "cancelled" and before["task_status"] == "cancelled"
        assert before["active_execution_run_id"] == page.run_id
        # A current-run event must not project into an earlier result.
        detail = await task_service.get_task(page.task_id)
        await TaskRepository(session).append_event(task_id=page.task_id, execution_run_id=detail.task.current_run_id,
            event_type="task.failed", payload_json={"summary": "Private diagnostics"}, idempotency_key="unrelated-event")
        assert (await session.get(ResultPage, page.id)).state_revision == original_revision


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "cancelled", "completed"])
async def test_notification_without_a_published_revision_opens_task(status):
    from app.services.task_presentations import task_presentation_service
    page = await page_fixture()
    if status == "cancelled":
        await task_service.cancel(page.task_id, idempotency_key="no-result-cancel")
    else:
        await task_service.complete(page.task_id, status=status, summary="No typed publication",
            idempotency_key="no-result-terminal")
    await task_presentation_service.sync_candidates()
    async with session_scope() as session:
        delivery = (await session.execute(select(NotificationOutbox).where(NotificationOutbox.task_id == page.task_id))).scalar_one()
        assert delivery.deep_link == f"/tasks/{page.task_id}"


@pytest.mark.asyncio
async def test_auth_unknown_ids_headers_and_write_rate_limit():
    page = await page_fixture()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        for path in ["/api/app-catalog", "/api/app-catalog/documents", f"/api/results/{page.public_id}", f"/api/results/{page.public_id}/events"]:
            response = await http.get(path)
            assert response.status_code == 401
            assert response.headers["cache-control"] == "private, no-store"
        await http.post("/api/auth/login", json={"password": ""})
        assert (await http.get("/api/results/unknown/events")).status_code == 404
        assert (await http.get("/api/app-catalog/unknown")).status_code == 404
        response = await http.get(f"/api/results/{page.public_id}")
        for name, value in results.PRIVATE_HEADERS.items():
            assert response.headers[name] == value
        url = f"/api/results/{page.public_id}/actions/set_saved"
        for _ in range(120):
            await http.post(url, json={"saved": True, "expected_revision": 999})
        response = await http.post(url, json={"saved": True, "expected_revision": 0})
        assert response.status_code == 429 and response.headers["retry-after"] == "60"


@pytest.mark.asyncio
@pytest.mark.parametrize("definition", ["recipe_from_screen@1", "research_screen@1"])
async def test_real_publication_transaction_rolls_back_result_events_and_saved_is_content_independent(monkeypatch, definition):
    from test_m4_workflows import admit, content, researched
    from app.jarvis_tasks.task_mcp import _call
    from app.services import task_results
    args, _ = await admit(monkeypatch, definition)
    _, source = await researched(args)
    payload = content(args, source) | {"operation_key": "m5-publish"}
    async with session_scope() as session:
        page = (await session.execute(select(ResultPage).where(ResultPage.task_id == args.task_id))).scalar_one()
        public_id, state_revision = page.public_id, page.state_revision
    original = task_results.append_result_event
    async def fail_on_publication(session, page, event_type, **kwargs):
        if event_type == "published":
            raise RuntimeError("Injected result event failure")
        return await original(session, page, event_type, **kwargs)
    monkeypatch.setattr(task_results, "append_result_event", fail_on_publication)
    with pytest.raises(RuntimeError, match="Injected"):
        await _call("result_publish", payload, args)
    async with session_scope() as session:
        page = (await session.execute(select(ResultPage).where(ResultPage.public_id == public_id))).scalar_one()
        assert page.revision_number == 0 and page.state_revision == state_revision
        assert (await session.execute(select(func.count()).select_from(ResultPageRevision))).scalar_one() == 0
        assert (await session.execute(select(func.count()).select_from(TaskArtifact).where(TaskArtifact.artifact_type == "workflow_result"))).scalar_one() == 0
    assert (await task_service.get_task(args.task_id)).task.status == "running"
    monkeypatch.setattr(task_results, "append_result_event", original)
    await _call("result_publish", payload, args)
    assert await _call("result_publish", payload, args)
    async with client() as http:
        before = (await http.get(f"/api/results/{public_id}")).json()
        assert before["revision_number"] == 1
        assert (await http.post(f"/api/results/{public_id}/actions/set_saved", json={"saved": True, "expected_revision": 0})).status_code == 200
        after = (await http.get(f"/api/results/{public_id}")).json()
        assert after["revision_number"] == 1 and after["content"] == before["content"]
        assert after["state_revision"] > before["state_revision"]
    async with session_scope() as session:
        events = list((await session.execute(select(ResultPageEvent).where(ResultPageEvent.result_page_id == page.id).order_by(ResultPageEvent.sequence))).scalars())
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        assert len([event for event in events if event.event_type == "published"]) == 1


@pytest.mark.asyncio
async def test_production_shell_is_data_free_and_csp_protected(monkeypatch, tmp_path):
    from app import main
    from app.services.result_static import result_shell_response
    # A missing build reports an actionable error, not the legacy static 404.
    monkeypatch.setattr(main, "_result_export", tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        assert (await http.get("/results/not-prebuilt")).status_code == 503
        assert (await http.get("/api/results/not-prebuilt")).status_code == 401
    # Actual shell build/browser behavior is exercised by test_result_browser.
    export = Path(__file__).resolve().parents[2] / "frontend/result-export/out"
    if export.exists():
        response = result_shell_response(export)
        assert response.headers["cache-control"] == "private, no-store"
        assert "script-src 'self' 'sha256-" in response.headers["content-security-policy"]
        assert "'unsafe-inline'" not in response.headers["content-security-policy"].split("script-src")[1].split(";")[0]
        assert b"source_event_sequences" not in response.body


@pytest.mark.asyncio
async def test_companion_routes_are_allowlisted_data_free_and_private_apis_stay_authenticated(monkeypatch, tmp_path):
    from app import main
    from app.services.result_static import SHELL_FILES
    monkeypatch.setattr(main, "_result_export", tmp_path)
    monkeypatch.setattr(main, "_frontend_out", tmp_path / "missing-legacy")
    paths = {"/results/future-id": "result", "/tasks/future-task": "task",
             "/login": "login", "/tasks": "tasks", "/workspace/apps": "apps",
             "/collections/documents": "documents", "/collections/recipes": "recipes"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
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
        assert (await http.get("/collections/unknown")).status_code == 404
        assert (await http.get("/api/results/future-id")).status_code == 401
        assert (await http.get("/api/tasks/future-task")).status_code == 401
        assert (await http.get("/api/app-catalog/documents/results")).status_code == 401


@pytest.mark.asyncio
async def test_fixed_legacy_login_survives_missing_companion_build(monkeypatch, tmp_path):
    from app import main
    monkeypatch.setattr(main, "_result_export", tmp_path / "missing-companion")
    monkeypatch.setattr(main, "_frontend_out", tmp_path)
    (tmp_path / "login.html").write_text("<html>Legacy login</html>", encoding="utf-8")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        response = await http.get("/login")
        assert response.status_code == 200 and "Legacy login" in response.text
        assert (await http.get("/results/future-id")).status_code == 503
