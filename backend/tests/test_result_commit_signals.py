"""Result streams wake after commit while keeping replay and recovery authority."""
import asyncio
import json
from uuid import uuid4

import pytest

from app.api import results
from app.db.session import session_scope
from app.db.tables import ResultPage
from app.services.commit_signals import result_event_committed
from app.services.task_results import append_result_event
from test_result_platform import page_fixture
from test_commit_signals import Request


async def change(page_id):
    async with session_scope() as session:
        page = await session.get(ResultPage, page_id)
        await append_result_event(session, page, "action_state", event_key=f"fixture:{uuid4()}")


@pytest.mark.asyncio
@pytest.mark.parametrize("missed_hint", [False, True])
async def test_result_stream_broadcast_and_missed_hint_recovery(monkeypatch, missed_hint):
    page = await page_fixture()
    monkeypatch.setattr(results, "RESULT_RECOVERY_SCAN_SECONDS", .03 if missed_hint else 10.)
    if missed_hint:
        monkeypatch.setattr(result_event_committed, "notify", lambda: None)
    streams = [results.result_events(page.public_id, Request(), None) for _ in range(2)]
    tasks = []
    try:
        for stream in streams:
            assert (await anext(stream))["event"] == "snapshot"
            tasks.append(asyncio.create_task(anext(stream)))
        await asyncio.sleep(.025)
        await change(page.id)
        frames = await asyncio.wait_for(asyncio.gather(*tasks), 1)
        assert [frame["event"] for frame in frames] == ["result", "result"]
        assert [int(frame["id"]) for frame in frames] == [2, 2]
        replay = results.result_events(page.public_id, Request(), "1")
        try:
            await anext(replay)
            assert (await anext(replay))["id"] == "2"
        finally:
            await replay.aclose()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for stream in streams:
            await stream.aclose()


@pytest.mark.asyncio
async def test_result_signal_ignores_savepoint_release_and_rollback():
    page = await page_fixture()
    observed = result_event_committed.snapshot()
    with pytest.raises(RuntimeError):
        async with session_scope() as session:
            async with session.begin_nested():
                await append_result_event(session, await session.get(ResultPage, page.id), "action_state", event_key="nested")
            assert not observed.is_set()
            raise RuntimeError("outer rollback")
    assert not observed.is_set()
    await change(page.id)
    assert observed.is_set()


@pytest.mark.asyncio
async def test_result_commit_between_read_and_wait_is_not_lost(monkeypatch):
    page = await page_fixture()
    stream = results.result_events(page.public_id, Request(), None)
    await anext(stream)
    monkeypatch.setattr(results, "RESULT_RECOVERY_SCAN_SECONDS", 10.)
    wait = result_event_committed.wait
    inserted = False
    async def race(observed, *, timeout):
        nonlocal inserted
        if not inserted:
            inserted = True
            await change(page.id)
        await wait(observed, timeout=timeout)
    monkeypatch.setattr(result_event_committed, "wait", race)
    try:
        assert (await asyncio.wait_for(anext(stream), 1))["id"] == "2"
    finally:
        await stream.aclose()


@pytest.mark.asyncio
async def test_unrelated_page_does_not_leak_and_wait_is_cancellable(monkeypatch):
    first, other = await page_fixture(), await page_fixture()
    monkeypatch.setattr(results, "RESULT_RECOVERY_SCAN_SECONDS", 10.)
    stream = results.result_events(first.public_id, Request(), None)
    await anext(stream)
    pending = asyncio.create_task(anext(stream))
    try:
        await asyncio.sleep(.025)
        await change(other.id)
        await asyncio.sleep(.025)
        assert not pending.done()
        await change(first.id)
        frame = await asyncio.wait_for(pending, 1)
        assert frame["id"] == "2" and json.loads(frame["data"])["type"] == "action_state"
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(.01)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await stream.aclose()
