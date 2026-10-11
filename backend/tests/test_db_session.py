from __future__ import annotations

import pytest
from sqlalchemy.pool import NullPool

from app.db.session import initialize_engine


@pytest.mark.asyncio
async def test_read_scope_detaches_scalar_rows_and_rejects_writes():
    from sqlalchemy import update
    from app.db.session import session_scope, read_session_scope
    from app.db.tables import Conversation
    async with session_scope() as session:
        row = Conversation(title="Read scope")
        session.add(row)
        await session.flush()
        identity = row.id
    async with read_session_scope() as session:
        read = await session.get(Conversation, identity)
    assert read.id == identity and read.title == "Read scope"
    with pytest.raises(RuntimeError, match="mutations"):
        async with read_session_scope() as session:
            await session.execute(update(Conversation).where(Conversation.id == identity).values(title="Forbidden"))
    with pytest.raises(RuntimeError, match="retain changes"):
        async with read_session_scope() as session:
            (await session.get(Conversation, identity)).title = "Forbidden"
    with pytest.raises(RuntimeError, match="flush changes"):
        async with read_session_scope() as session:
            session.add(Conversation(title="Forbidden"))
            await session.flush()
    with pytest.raises(RuntimeError, match="commit"):
        async with read_session_scope() as session:
            await session.get(Conversation, identity)
            await session.commit()
    async with read_session_scope() as session:
        assert (await session.get(Conversation, identity)).title == "Read scope"


@pytest.mark.asyncio
async def test_commit_trace_separates_flush_connection_setup_and_commit():
    from types import SimpleNamespace
    from app.services.admission_timing import observe_admission
    from app.db.session import session_scope
    from app.db.tables import Conversation
    @observe_admission
    async def write():
        async with session_scope() as session:
            session.add(Conversation(title="Commit stage fixture"))
        return SimpleNamespace(output={})
    trace = (await write()).output["admission_timing"]
    spans = {entry["stage"]: entry for entry in trace["spans"]}
    assert {"db_commit", "db_flush", "db_transaction_commit", "db_connection_open", "db_connection_setup"} <= spans.keys()
    assert spans["db_commit"]["start_ns"] <= spans["db_flush"]["start_ns"]
    assert spans["db_flush"]["end_ns"] <= spans["db_transaction_commit"]["start_ns"]
    assert spans["db_connection_open"]["start_ns"] <= spans["db_connection_setup"]["start_ns"]
    assert spans["db_connection_setup"]["end_ns"] <= spans["db_connection_open"]["end_ns"]


@pytest.mark.asyncio
async def test_initialize_engine_uses_null_pool_for_file_backed_sqlite():
    engine = initialize_engine()

    assert isinstance(engine.sync_engine.pool, NullPool)
    async with engine.connect() as connection:
        busy_timeout = await connection.exec_driver_sql("PRAGMA busy_timeout")
        assert busy_timeout.scalar_one() == 30_000


@pytest.mark.asyncio
@pytest.mark.parametrize("scope_name", ["read_session_scope", "session_scope", "get_db_session"])
async def test_cancelled_scopes_release_connections_and_roll_back(scope_name, caplog):
    import asyncio
    from contextlib import asynccontextmanager
    import threading
    from anyio import CancelScope, sleep
    from sqlalchemy import event, select, func
    from app.db import session as database
    from app.db.tables import Conversation

    engine = initialize_engine()
    checked_out = set()
    event.listen(engine.sync_engine, "checkout", lambda conn, record, proxy: checked_out.add(id(record)))
    event.listen(engine.sync_engine, "checkin", lambda conn, record: checked_out.discard(id(record)))
    baseline_threads = {t.ident for t in threading.enumerate() if "_connection_worker_thread" in t.name}
    scope = getattr(database, scope_name)
    if scope_name == "get_db_session":
        scope = asynccontextmanager(scope)
    for index in range(100):
        with CancelScope() as cancellation:
            async with scope() as session:
                if scope_name == "read_session_scope":
                    await session.execute(select(func.count()).select_from(Conversation))
                else:
                    session.add(Conversation(title=f"Cancelled {index}"))
                    await session.flush()
                assert checked_out
                cancellation.cancel()
                await sleep(0)
        assert not checked_out, f"Connection leaked after cancellation {index}"
    async with database.read_session_scope() as session:
        assert (await session.execute(select(func.count()).select_from(Conversation))).scalar_one() == 0
    # aiosqlite.close queues thread shutdown; allow the final worker to exit.
    for _ in range(100):
        live = {t.ident for t in threading.enumerate() if "_connection_worker_thread" in t.name}
        if live <= baseline_threads:
            break
        await asyncio.sleep(.01)
    assert live <= baseline_threads
    assert not any("Exception terminating connection" in r.message or
                   "non-checked-in connection" in r.message for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope_name", ["session_scope", "get_db_session", "read_session_scope"])
async def test_cancelled_connection_setup_finishes_before_close(scope_name, monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from app.db import session as database

    started, release = asyncio.Event(), asyncio.Event()
    events = []

    class PendingConnection:
        info = {}
        async def connection(self):
            started.set()
            await release.wait()
            events.append("connected")
        async def rollback(self):
            events.append("rolled_back")
        async def close(self):
            assert events[0] == "connected"
            events.append("closed")

    monkeypatch.setattr(database, "get_session_factory", lambda: PendingConnection)
    scope = getattr(database, scope_name)
    if scope_name == "get_db_session":
        scope = asynccontextmanager(scope)
    async def open_scope():
        async with scope():
            pytest.fail("Cancelled connection must not yield a session")
    caller = asyncio.create_task(open_scope())
    await started.wait()
    caller.cancel()
    await asyncio.sleep(0)
    caller.cancel()
    await asyncio.sleep(0)
    assert not caller.done() and not events
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert events[-1] == "closed"


@pytest.mark.asyncio
async def test_new_read_connection_remains_responsive_during_active_writer():
    import asyncio
    from sqlalchemy import text
    from app.db.session import session_scope, read_session_scope
    from app.db.tables import Conversation

    async with session_scope() as writer:
        writer.add(Conversation(title="Uncommitted writer"))
        await writer.flush()
        async def read():
            async with read_session_scope() as reader:
                assert (await reader.execute(text("PRAGMA journal_mode"))).scalar_one() == "wal"
                assert (await reader.execute(text("SELECT count(*) FROM conversations"))).scalar_one() == 0
        await asyncio.wait_for(read(), timeout=2)


@pytest.mark.asyncio
async def test_read_cancelled_during_sql_releases_connection(caplog):
    import asyncio
    import threading
    import time
    from anyio import CancelScope
    from sqlalchemy import event, text
    from app.db.session import read_session_scope
    entered = threading.Event()
    engine = initialize_engine()
    checked_out = set()
    event.listen(engine.sync_engine, "checkout", lambda conn, record, proxy: checked_out.add(id(record)))
    event.listen(engine.sync_engine, "checkin", lambda conn, record: checked_out.discard(id(record)))
    def slow_read():
        entered.set()
        time.sleep(.03)
        return 1
    event.listen(engine.sync_engine, "connect", lambda conn, record: conn.create_function("slow_read", 0, slow_read))
    for _ in range(20):
        entered.clear()
        with CancelScope() as cancellation:
            async def cancel_during_query():
                while not entered.is_set():
                    await asyncio.sleep(.001)
                cancellation.cancel()
            canceller = asyncio.create_task(cancel_during_query())
            try:
                async with read_session_scope() as session:
                    await session.execute(text("SELECT slow_read()"))
            finally:
                with CancelScope(shield=True):
                    await canceller
        assert not checked_out
    assert not any("Exception terminating connection" in r.message or
                   "non-checked-in connection" in r.message for r in caplog.records)
