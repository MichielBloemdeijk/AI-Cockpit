"""Diagnostic batching, bounded loss, lifecycle and authority separation."""
import asyncio
from contextvars import ContextVar
from time import perf_counter

import pytest
from sqlalchemy import select

from app.db.session import session_scope, close_engine, initialize_engine
from app.config import settings
from app.db.tables import ResultMetric
from app.services.observation_queue import ObservationQueue
from app.services import result_telemetry
from app.services.task_service import task_service
from app.services.worker_coordinator import worker_coordinator
from test_task_result_contract import claim, finish, page_for


@pytest.mark.asyncio
async def test_batches_copy_metadata_and_clear_request_context():
    request = ContextVar("private_request", default=None)
    batches = []
    async def writer(jobs):
        assert request.get() is None
        batches.append(jobs)
    queue = ObservationQueue(writer, batch_size=3, batch_delay=1)
    token = request.set("PRIVATE")
    job = {"values": {"outcome": "original"}}
    try:
        assert queue.submit(job)
        job["values"]["outcome"] = "changed"
        assert queue.submit({"kind": "second"})
        assert queue.submit({"kind": "third"})
        assert await queue.drain()
        assert batches == [[{"values": {"outcome": "original"}}, {"kind": "second"}, {"kind": "third"}]]
        assert queue.status()["completed"] == 3
        assert queue.status()["batches"] == 1
        assert queue.status()["queue_wait"]["sample_count"] == 3
    finally:
        request.reset(token)
        await queue.stop()


@pytest.mark.asyncio
async def test_saturation_does_not_wait_and_failures_do_not_stop_consumer(caplog):
    entered, release = asyncio.Event(), asyncio.Event()
    async def writer(jobs):
        entered.set()
        await release.wait()
        if jobs[0] == "failed":
            raise RuntimeError("PRIVATE failure")
    queue = ObservationQueue(writer, capacity=1, batch_size=1)
    try:
        assert queue.submit("failed")
        await entered.wait()
        assert queue.submit("next")
        assert queue.submit("overflow") is False
        assert queue.status()["pending"] == 2  # capacity plus one bounded active batch
        release.set()
        assert await queue.drain()
        assert queue.status()["failed"] == 1
        assert queue.status()["dropped"] == 1
        assert queue.status()["completed"] == 1
        assert "PRIVATE" not in caplog.text
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_shutdown_drains_and_timeout_cancels_writer_with_cleanup(caplog):
    persisted = []
    async def writer(jobs):
        persisted.extend(jobs)
    queue = ObservationQueue(writer, batch_delay=10)
    queue.submit("shutdown")
    await queue.stop(timeout=.5)
    assert persisted == ["shutdown"]
    entered, cleaned = asyncio.Event(), asyncio.Event()
    async def blocked(jobs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()
    queue = ObservationQueue(blocked, batch_size=1)
    queue.submit("in-flight")
    await entered.wait()
    queue.submit("queued")
    started = perf_counter()
    await queue.stop(timeout=.02)
    assert perf_counter()-started < .5
    assert cleaned.is_set()
    assert "drain_timeout" in caplog.text and "dropped=2" in caplog.text


@pytest.mark.asyncio
async def test_cancelled_drain_does_not_cancel_persistence():
    entered, release = asyncio.Event(), asyncio.Event()
    async def writer(jobs):
        entered.set()
        await release.wait()
    queue = ObservationQueue(writer, batch_size=1)
    try:
        queue.submit("observation")
        await entered.wait()
        waiter = asyncio.create_task(queue.drain())
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        release.set()
        assert await queue.drain()
        assert queue.status()["completed"] == 1
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_admission_returns_while_metric_writer_is_blocked_and_shutdown_persists(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    real_writer = result_telemetry.observation_queue.writer
    async def blocked(jobs):
        entered.set()
        await release.wait()
        await real_writer(jobs)
    monkeypatch.setattr(result_telemetry.observation_queue, "writer", blocked)
    created = await asyncio.wait_for(task_service.dispatch(goal="Queue acceptance", origin_surface="test",
        idempotency_key="queue-admission", result_request={"mode": "new"}), .8)
    await entered.wait()
    page = await page_for(created.task.id)
    async with session_scope() as session:
        assert not list((await session.execute(select(ResultMetric))).scalars())
    release.set()
    await close_engine()  # All entry points drain diagnostics before disposing SQLite.
    initialize_engine(settings.database_url)
    async with session_scope() as session:
        rows = list((await session.execute(select(ResultMetric).where(ResultMetric.result_page_id == page.id))).scalars())
        assert {row.kind for row in rows} == {"allocation", "admission_to_allocation"}


@pytest.mark.asyncio
async def test_delayed_batch_keeps_allocation_and_publication_run_revision(monkeypatch):
    # Hold all diagnostic work while two different runs complete.
    release = asyncio.Event()
    real_writer = result_telemetry.observation_queue.writer
    async def blocked(jobs):
        await release.wait()
        await real_writer(jobs)
    monkeypatch.setattr(result_telemetry.observation_queue, "writer", blocked)
    worker = await worker_coordinator.activate(process_identity="delayed-diagnostics")
    created = await task_service.dispatch(goal="Original", origin_surface="test", idempotency_key="first",
        result_request={"mode": "new"})
    task_id = created.task.id
    first = await claim(worker, task_id)
    with pytest.raises(ValueError):
        await task_service.complete(task_id, summary="Invalid", final_text="", idempotency_key="invalid", **first)
    await finish(worker, task_id, first, "First body", "first-publish")
    page = await page_for(task_id)
    await task_service.continue_task(task_id, content="Second", origin="test", idempotency_key="second",
        result_request={"mode": "update", "public_id": page.public_id, "expected_revision": 1})
    second = await claim(worker, task_id)
    await finish(worker, task_id, second, "Second body", "second-publish")
    release.set()
    assert await result_telemetry.observation_queue.drain()
    async with session_scope() as session:
        rows = list((await session.execute(select(ResultMetric).where(ResultMetric.result_page_id == page.id))).scalars())
    allocation = next(row for row in rows if row.kind == "allocation")
    assert allocation.execution_run_id == first["execution_run_id"] and allocation.revision_number == 0
    publications = {row.execution_run_id: row.revision_number for row in rows
        if row.kind == "publication" and row.values_json["outcome"] == "committed"}
    assert publications == {first["execution_run_id"]: 1, second["execution_run_id"]: 2}
    failed = next(row for row in rows if row.kind == "publication" and row.values_json["outcome"] == "validation_failed")
    assert failed.execution_run_id == first["execution_run_id"] and failed.revision_number is None


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_error", ["busy", "deadline"])
async def test_one_busy_retry_is_idempotent_and_other_failures_are_not_retried(monkeypatch, retry_error):
    import sqlite3
    from sqlalchemy.exc import OperationalError
    attempts = []
    real_write = result_telemetry._write_batch_once
    async def busy(jobs):
        attempts.append(True)
        if len(attempts) == 1:
            if retry_error == "deadline":
                await real_write(jobs)
                raise TimeoutError()
            error = sqlite3.OperationalError("PRIVATE statement")
            error.sqlite_errorcode = sqlite3.SQLITE_BUSY
            raise OperationalError("PRIVATE SQL", {}, error)
        await real_write(jobs)
    monkeypatch.setattr(result_telemetry, "_write_batch_once", busy)
    job = dict(type="metric", page_id=None, metric=dict(kind="retry_fixture", key="retry-key", duration_ms=1))
    await result_telemetry._write_batch([job])
    await result_telemetry._write_batch([job])
    assert len(attempts) == 3
    async with session_scope() as session:
        assert len(list((await session.execute(select(ResultMetric).where(ResultMetric.metric_key == "retry-key"))).scalars())) == 1
    attempts.clear()
    async def invalid(jobs):
        attempts.append(True)
        raise ValueError("PRIVATE invalid")
    monkeypatch.setattr(result_telemetry, "_write_batch_once", invalid)
    with pytest.raises(ValueError):
        await result_telemetry._write_batch([job])
    assert len(attempts) == 1
