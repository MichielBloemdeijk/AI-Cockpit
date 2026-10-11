"""Committed wakeups, lost-hint recovery and per-turn capture readiness."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy.dialects.sqlite import insert

from app.db.session import session_scope
from app.db.tables import Conversation, JarvisDevice, JarvisLiveEvent, LiveSession
from app.services.auth import SESSION_COOKIE_NAME, create_session_token
from app.services.commit_signals import CommitSignal, live_event_committed, capture_state_committed
from app.services.jarvis_live import jarvis_live_service
from app.services.task_capture import task_capture_service


def event_row():
    return JarvisLiveEvent(event_type="test.committed", source_domain="task", source_id="fixture",
        idempotency_key=str(uuid4()), payload_json={"test": True})


class Request:
    def __init__(self):
        self.cookies = {SESSION_COOKIE_NAME: create_session_token()}
    async def is_disconnected(self):
        return False


@pytest.mark.asyncio
async def test_repeated_sse_disconnects_release_lookup_connections(monkeypatch, caplog):
    import threading
    from sqlalchemy import event
    from sse_starlette.sse import EventSourceResponse
    from app.db.session import initialize_engine
    engine = initialize_engine()
    active = set()
    event.listen(engine.sync_engine, "checkout", lambda conn, record, proxy: active.add(id(record)))
    event.listen(engine.sync_engine, "checkin", lambda conn, record: active.discard(id(record)))
    baseline = {t.ident for t in threading.enumerate() if "_connection_worker_thread" in t.name}
    scope = {"type": "http", "method": "GET", "path": "/api/jarvis/live", "headers": []}
    async def disconnect():
        await asyncio.sleep(.001)
        return {"type": "http.disconnect"}
    async def send(message):
        pass
    for _ in range(20):
        await asyncio.gather(*(EventSourceResponse(jarvis_live_service.stream(Request()), ping=None)(
            scope, disconnect, send) for _ in range(5)))
        assert not active
    for _ in range(100):
        remaining = {t.ident for t in threading.enumerate() if "_connection_worker_thread" in t.name}
        if remaining <= baseline:
            break
        await asyncio.sleep(.01)
    assert remaining <= baseline
    assert not any("Exception terminating connection" in r.message or
                   "non-checked-in connection" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_broadcast_and_read_to_wait_race_do_not_lose_wakeups():
    signal = CommitSignal()
    observed = signal.snapshot()
    first = asyncio.create_task(signal.wait(observed, timeout=10))
    second = asyncio.create_task(signal.wait(observed, timeout=10))
    signal.notify()
    await asyncio.wait_for(asyncio.gather(first, second), 1)
    # A listener that starts waiting after the commit also sees the old event.
    await asyncio.wait_for(signal.wait(observed, timeout=10), 1)
    assert signal.snapshot() is not observed
    pending = asyncio.create_task(signal.wait(signal.snapshot(), timeout=10))
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending


@pytest.mark.asyncio
async def test_only_outer_commit_wakes_and_rollback_does_not():
    observed = live_event_committed.snapshot()
    async with session_scope() as session:
        async with session.begin_nested():
            session.add(event_row())
            await session.flush()
            assert not observed.is_set()
        assert not observed.is_set()
    assert observed.is_set()
    observed = live_event_committed.snapshot()
    with pytest.raises(RuntimeError):
        async with session_scope() as session:
            session.add(event_row())
            await session.flush()
            raise RuntimeError("rollback")
    assert not observed.is_set()
    async with session_scope() as session:
        row = event_row()
        await session.execute(insert(JarvisLiveEvent).values(event_type=row.event_type,
            source_domain=row.source_domain, source_id=row.source_id, idempotency_key=row.idempotency_key))
        assert not observed.is_set()
    assert observed.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("missed_hint", [False, True])
async def test_stream_broadcast_replay_and_missed_hint_recovery(monkeypatch, missed_hint):
    monkeypatch.setattr("app.services.jarvis_live.RECOVERY_SCAN_SECONDS", .05 if missed_hint else 10.)
    if missed_hint:
        monkeypatch.setattr(live_event_committed, "notify", lambda: None)
    streams = [jarvis_live_service.stream(Request()) for _ in range(2)]
    pending = [asyncio.create_task(anext(stream)) for stream in streams]
    await asyncio.sleep(.025)
    async with session_scope() as session:
        row = event_row()
        session.add(row)
    try:
        frames = await asyncio.wait_for(asyncio.gather(*pending), 1)
        assert [json.loads(frame["data"])["id"] for frame in frames] == [row.id, row.id]
        replay = jarvis_live_service.stream(Request(), cursor=row.sequence-1)
        try:
            assert json.loads((await anext(replay))["data"])["id"] == row.id
        finally:
            await replay.aclose()
    finally:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for stream in streams:
            await stream.aclose()


@pytest.mark.asyncio
async def test_commit_between_stream_read_and_wait_is_not_lost(monkeypatch):
    monkeypatch.setattr("app.services.jarvis_live.RECOVERY_SCAN_SECONDS", 10.)
    replay = jarvis_live_service.replay
    inserted = False
    async def race(**kwargs):
        nonlocal inserted
        result = await replay(**kwargs)
        if not inserted:
            inserted = True
            async with session_scope() as session:
                session.add(event_row())
        return result
    monkeypatch.setattr(jarvis_live_service, "replay", race)
    stream = jarvis_live_service.stream(Request())
    try:
        assert (await asyncio.wait_for(anext(stream), 1))["event"] == "jarvis"
    finally:
        await stream.aclose()


async def capture_fixture():
    async with session_scope() as session:
        conversation = Conversation(title="Capture signal")
        device = JarvisDevice(label="Capture", credential_hash=sha256(str(uuid4()).encode()).hexdigest(), lock_state="unlocked")
        session.add_all([conversation, device])
        await session.flush()
        live = LiveSession(conversation_id=conversation.id, controller_id=device.id, activation_id=str(uuid4()),
            provider_adapter="fake", lifecycle_state="ready")
        session.add(live)
        await session.flush()
    return dict(device_id=device.id, session_id=live.id, generation=1, activation_id=live.activation_id)


@pytest.mark.asyncio
async def test_required_readiness_is_triggered_and_optional_role_is_checked_at_freeze(monkeypatch):
    options = await capture_fixture()
    invocation_id = str(uuid4())
    expiry = datetime.now(timezone.utc)+timedelta(seconds=80)
    await task_capture_service.report(**options, capture_id=invocation_id, capture_mode="activation", status="pending", revision=1, expires_at=expiry)
    fresh = await task_capture_service.request_fresh(session_id=options["session_id"], generation=1, request_identity="turn")
    wait = capture_state_committed.wait
    waiting = asyncio.Event()
    async def long_recovery(observed, *, timeout):
        waiting.set()
        await wait(observed, timeout=10.)
    monkeypatch.setattr(capture_state_committed, "wait", long_recovery)
    pending = asyncio.create_task(task_capture_service.preflight_collection(**options, request_identity="turn"))
    await asyncio.wait_for(waiting.wait(), 1)
    for identity, mode, expires in ((fresh.capture_id, "fresh", fresh.expires_at.replace(tzinfo=timezone.utc)),
        (invocation_id, "activation", expiry)):
        await task_capture_service.report(**options, capture_id=identity, capture_mode=mode, status="captured",
            revision=2, captured_at=datetime.now(timezone.utc), expires_at=expires)
    collection = await asyncio.wait_for(pending, 1)
    assert {role for role, _ in collection.captures} == {"invocation", "latest"}
    assert not collection.missing_roles
