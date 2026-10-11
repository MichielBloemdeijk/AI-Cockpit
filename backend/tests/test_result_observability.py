"""Result timing, privacy and restart checks without models, browsers or devices."""
from time import perf_counter
from uuid import uuid4
import json

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import select, func

from app.config import settings
from app.db.session import session_scope, close_engine, initialize_engine
from app.db.tables import ResultMetric, ResultPageRevision, NotificationOutbox
from app.main import app
from app.services import result_telemetry
from app.services.task_service import task_service
from app.services.worker_coordinator import worker_coordinator
from test_task_result_contract import claim, page_for, finish


async def admitted():
    created = await task_service.dispatch(goal="PRIVATE goal must not enter metrics", origin_surface="test",
        idempotency_key=str(uuid4()), result_request={"mode": "new"})
    return created.task.id, await page_for(created.task.id)


@pytest.mark.asyncio
async def test_publication_commit_replay_conflict_validation_rollback_and_no_content(monkeypatch, caplog):
    caplog.set_level("INFO", logger=result_telemetry.__name__)
    worker = await worker_coordinator.activate(process_identity="result-observability")
    task_id, page = await admitted()
    fence = await claim(worker, task_id)
    with pytest.raises(ValueError):
        await task_service.complete(task_id, summary="PRIVATE title", final_text="", idempotency_key="invalid", **fence)
    from app.services import task_service as service_module
    real_publish = service_module.publish_document_result
    async def fail_transaction(*args, **kwargs):
        await real_publish(*args, **kwargs)
        raise RuntimeError("PRIVATE exception diagnostics")
    with monkeypatch.context() as patch:
        patch.setattr(service_module, "publish_document_result", fail_transaction)
        with pytest.raises(RuntimeError):
            await task_service.complete(task_id, summary="PRIVATE title", final_text="PRIVATE body", idempotency_key="rolled-back", **fence)
    async with session_scope() as session:
        assert (await session.execute(select(func.count()).select_from(ResultPageRevision))).scalar_one() == 0
        assert (await session.execute(select(func.count()).select_from(NotificationOutbox))).scalar_one() == 0
    await finish(worker, task_id, fence, "PRIVATE body", "committed")
    await task_service.complete(task_id, summary="Document title", final_text="PRIVATE body", idempotency_key="committed", **fence)
    with pytest.raises(ValueError, match="conflicts"):
        await task_service.complete(task_id, summary="Document title", final_text="PRIVATE different", idempotency_key="committed", **fence)
    assert await result_telemetry.observation_queue.drain()
    async with session_scope() as session:
        stats = await result_telemetry.snapshot(session, page)
        serialized = json.dumps(stats)
        assert "PRIVATE" not in serialized and fence["claim_token"] not in serialized
        assert stats["publication_outcomes"] == {"committed": 1, "replayed": 1, "conflict": 1, "validation_failed": 1, "rollback": 1}
        assert stats["counts"]["allocation"] == 1
        assert stats["counts"]["completion_to_notification_enqueue"] == 1
        assert all(row["attempt_id"] == fence["attempt_id"] for row in stats["recent"] if row["kind"] == "publication")
        committed = next(row for row in stats["recent"] if row["kind"] == "publication" and row["metadata"]["outcome"] == "committed")
        assert committed["metadata"]["validation_ms"] >= 0
        assert committed["metadata"]["transaction_ms"] >= 0
    logged = [json.loads(entry.message) for entry in caplog.records if entry.name == result_telemetry.__name__ and entry.message.startswith("{")]
    assert any(item["result_metric_kind"] == "publication" and item["result_outcome"] == "rollback" for item in logged)
    assert "PRIVATE" not in json.dumps(logged) and fence["claim_token"] not in json.dumps(logged)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        await http.post("/api/auth/login", json={"password": ""})
        endpoint = f"/api/results/{page.public_id}"
        body = {"view_id": str(uuid4()), "sequence": 0, "kind": "first_ready", "duration_ms": 45.0, "revision_number": 1}
        for _ in range(2):
            assert (await http.post(endpoint + "/observations", json=body)).status_code == 202
        assert await result_telemetry.observation_queue.drain()
        stats = (await http.get(endpoint + "/observability")).json()
        assert stats["counts"]["publication_to_ready_receipt"] == 1
        receipt = next(row for row in stats["recent"] if row["kind"] == "publication_to_ready_receipt")
        assert receipt["run_id"] == fence["execution_run_id"] and receipt["attempt_id"] == fence["attempt_id"]
        assert receipt["revision"] == 1 and receipt["duration_ms"] >= 0
        assert receipt["metadata"]["source"] == "server_receipt_proxy" and receipt["metadata"]["includes_network"] is True


@pytest.mark.asyncio
async def test_observation_failure_cannot_fail_committed_publication(monkeypatch):
    worker = await worker_coordinator.activate(process_identity="telemetry-unavailable")
    task_id, page = await admitted()
    fence = await claim(worker, task_id)
    async def unavailable(*args, **kwargs):
        raise RuntimeError("PRIVATE metrics unavailable")
    monkeypatch.setattr(result_telemetry, "record", unavailable)
    await finish(worker, task_id, fence)
    assert await result_telemetry.observation_queue.drain()
    assert result_telemetry.observation_queue.status()["failed"] > 0
    committed = await page_for(task_id)
    assert committed.status == "ready" and committed.revision_number == 1
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        await http.post("/api/auth/login", json={"password": ""})
        endpoint = f"/api/results/{page.public_id}"
        receipt = await http.post(endpoint + "/observations", json={"view_id": str(uuid4()), "sequence": 0,
            "kind": "first_ready", "duration_ms": 20.0, "revision_number": 1})
        assert receipt.status_code == 202
        assert (await http.get(endpoint)).json()["content"]["body"]


@pytest.mark.asyncio
async def test_authenticated_bounded_deduplicated_client_metadata_and_restart(monkeypatch):
    task_id, page = await admitted()
    endpoint = f"/api/results/{page.public_id}"
    payload = {"view_id": str(uuid4()), "sequence": 0, "kind": "first_snapshot", "duration_ms": 25.0, "revision_number": 0}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        assert (await http.get(endpoint + "/observability")).status_code == 401
        assert (await http.post(endpoint + "/observations", json=payload)).status_code == 401
        await http.post("/api/auth/login", json={"password": ""})
        for seq in [0, 0, 1]:
            assert (await http.post(endpoint + "/observations", json=payload | {"sequence": seq})).status_code == 202
        for bad in [{"content": "PRIVATE"}, {"kind": "arbitrary"}, {"sequence": 1001}, {"duration_ms": -1}, {"duration_ms": True}]:
            assert (await http.post(endpoint + "/observations", json=payload | bad)).status_code == 422
        assert (await http.post(endpoint + "/observations", json=payload | {"revision_number": 1})).status_code == 409
        assert (await http.post(endpoint + "/actions/set_saved", json={"saved": True, "expected_revision": 0})).status_code == 200
        before = (await http.get(endpoint)).json()
        assert await result_telemetry.observation_queue.drain()
        metrics = (await http.get(endpoint + "/observability")).json()
        metrics.pop("observation_queue")
        assert metrics["counts"]["client_first_snapshot"] == 1 and metrics["counts"]["allocation_to_view_receipt"] == 1
        assert "PRIVATE" not in json.dumps(metrics)
        await close_engine()
        initialize_engine(settings.database_url)
        monkeypatch.setattr(settings, "task_research_enabled", False)
        monkeypatch.setattr(settings, "task_workflow_allowlist", "")
        assert (await http.get(endpoint)).json() == before
        assert await result_telemetry.observation_queue.drain()
        restored = (await http.get(endpoint + "/observability")).json()
        restored.pop("observation_queue")
        assert restored == metrics
        assert before["actions"][0]["value"]["saved"] is True


@pytest.mark.asyncio
async def test_twenty_actual_admissions_allocation_samples_and_idempotency():
    timings = []
    for _ in range(20):
        key = str(uuid4())
        started = perf_counter()
        created = await task_service.dispatch(goal="Timing fixture", origin_surface="test",
            idempotency_key=key, result_request={"mode": "new"})
        timings.append((perf_counter() - started) * 1000)
        await task_service.dispatch(goal="Timing fixture", origin_surface="test",
            idempotency_key=key, result_request={"mode": "new"})
    assert await result_telemetry.observation_queue.drain()
    async with session_scope() as session:
        rows = list((await session.execute(select(ResultMetric).where(ResultMetric.kind == "allocation"))).scalars())
        assert len(rows) == 20
        assert all(0 <= row.duration_ms < 2000 for row in rows)
        assert max(timings) < 2000
        print(json.dumps({"samples": 20, "admission_including_observation_max_ms": round(max(timings), 2),
            "admission_p95_ms": round(sorted(timings)[18], 2), "allocation_max_ms": round(max(row.duration_ms for row in rows), 2)}))


@pytest.mark.asyncio
async def test_notification_transport_and_open_receipt_are_correlated_not_device_display(monkeypatch):
    from app.services.task_notifications import _send_outbox_by_id, acknowledge_task_notification
    from app.services.task_presentations import task_presentation_service
    worker = await worker_coordinator.activate(process_identity="observed-notification")
    task_id, page = await admitted()
    original = await claim(worker, task_id)
    await finish(worker, task_id, original)
    await task_presentation_service.sync_candidates()
    async with session_scope() as session:
        outbox = (await session.execute(select(NotificationOutbox).where(NotificationOutbox.task_id == task_id))).scalar_one()
        delivery_id = outbox.id
    monkeypatch.setattr(settings, "web_push_enabled", False)
    await _send_outbox_by_id(delivery_id)
    await task_service.continue_task(task_id, content="Update before opening old notification", origin="test", idempotency_key="notify-update",
        result_request={"mode": "update", "public_id": page.public_id, "expected_revision": 1})
    await finish(worker, task_id, await claim(worker, task_id), "Updated body", "notify-revision2")
    assert await acknowledge_task_notification(delivery_id, "opened")
    assert await acknowledge_task_notification(delivery_id, "opened")
    assert await result_telemetry.observation_queue.drain()
    async with session_scope() as session:
        stats = await result_telemetry.snapshot(session, page)
        assert stats["counts"]["notification_transport"] == 1 and stats["counts"]["notification_opened"] == 1
        for row in stats["recent"]:
            if row["kind"] in {"notification_transport", "notification_opened"}:
                assert row["metadata"]["delivery_id"] == delivery_id
                assert row["metadata"]["android_display_verified"] is False
                assert row["run_id"] == original["execution_run_id"] and row["revision"] == 1


@pytest.mark.asyncio
async def test_observation_disconnect_finishes_short_cleanup(monkeypatch):
    import anyio
    from contextlib import asynccontextmanager
    cleaned = []
    class Session:
        async def execute(self, *_):
            await anyio.sleep(0)
    @asynccontextmanager
    async def pending_cleanup():
        try:
            yield Session()
        finally:
            await anyio.sleep(0)
            cleaned.append(True)
    monkeypatch.setattr(result_telemetry, "session_scope", pending_cleanup)
    with anyio.CancelScope() as disconnected:
        async with result_telemetry.observation_session():
            disconnected.cancel()
    assert cleaned == [True]


@pytest.mark.asyncio
async def test_observation_query_deadline_does_not_cancel_connection_cleanup(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    cleaned = []
    class Session:
        async def execute(self, *_):
            pass
    @asynccontextmanager
    async def slow_close():
        try:
            yield Session()
        finally:
            await asyncio.sleep(.1)
            cleaned.append(True)
    monkeypatch.setattr(result_telemetry, "session_scope", slow_close)
    monkeypatch.setattr(result_telemetry, "OBSERVATION_QUERY_SECONDS", .05)
    async with result_telemetry.observation_session():
        pass
    assert cleaned == [True]
    with pytest.raises(TimeoutError):
        async with result_telemetry.observation_session():
            await asyncio.sleep(.15)
    assert cleaned == [True, True]


@pytest.mark.asyncio
async def test_observation_write_contention_is_bounded_without_stealing_writer():
    from sqlalchemy import update
    from app.db.tables import Task
    task_id, page = await admitted()
    assert await result_telemetry.observation_queue.drain()
    async with session_scope() as writer:
        assert (await writer.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none() is not None
        changed = await writer.execute(update(Task).where(Task.id == task_id).values(goal="Writer still owns its task"))
        assert changed.rowcount == 1
        started = perf_counter()
        await result_telemetry.observe(page.id, kind="contention_fixture", key="contention:test", duration_ms=1)
        assert await result_telemetry.observation_queue.drain()
        assert (perf_counter() - started) < .6
    async with session_scope() as session:
        assert (await session.execute(select(Task).where(Task.id == task_id))).scalar_one().goal == "Writer still owns its task"
        assert (await session.execute(select(ResultMetric).where(ResultMetric.kind == "contention_fixture"))).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_observation_deadline_finishes_sql_before_connection_cleanup(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    entered, release = asyncio.Event(), asyncio.Event()
    finished = []
    class Session:
        async def execute(self, *_):
            entered.set()
            await release.wait()
            finished.append("sql")
    @asynccontextmanager
    async def connection():
        try:
            yield Session()
        finally:
            finished.append("close")
    monkeypatch.setattr(result_telemetry, "session_scope", connection)
    monkeypatch.setattr(result_telemetry, "OBSERVATION_QUERY_SECONDS", .005)
    async def write():
        with pytest.raises(TimeoutError):
            async with result_telemetry.observation_session() as session:
                await session.execute("pending driver operation")
    operation = asyncio.create_task(write())
    await entered.wait()
    await asyncio.sleep(.02)
    assert finished == [] and not operation.done()
    release.set()
    await operation
    assert finished == ["sql", "close"]


@pytest.mark.asyncio
async def test_observation_connection_has_short_budget_before_first_statement():
    from sqlalchemy import text
    from app.db.session import observation_session_scope
    async with observation_session_scope() as diagnostic:
        assert (await diagnostic.execute(text("PRAGMA busy_timeout"))).scalar_one() == 100
    async with session_scope() as authority:
        assert (await authority.execute(text("PRAGMA busy_timeout"))).scalar_one() == 30000
