"""Async SQLAlchemy engine and session helpers."""
from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from contextlib import closing
import asyncio
import sqlite3
from collections.abc import AsyncIterator
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import time

from anyio import CancelScope
from anyio.lowlevel import checkpoint_if_cancelled
from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, StaticPool
from sqlalchemy.orm import Session

from app.config import settings
from app.services.admission_timing import span, record_interval, monotonic_ns
from app.services.commit_signals import live_event_committed, capture_state_committed, result_event_committed

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_observation_engine: AsyncEngine | None = None
_observation_factory: async_sessionmaker[AsyncSession] | None = None


def _note_committed_change(session, table):
    # Task/session event rows project into JarvisLiveEvent through DB hooks.
    if table in {"jarvis_live_events", "task_events", "live_session_events"}:
        session.info["live_event_changed"] = True
    if table in {"task_capture_reports", "jarvis_devices", "live_sessions"}:
        session.info["capture_state_changed"] = True
    if table == "result_page_events":
        session.info["result_event_changed"] = True


@event.listens_for(Session, "before_flush")
def _guard_read_scope(session, _context, _instances):
    if session.info.get("read_scope") and (session.new or session.dirty or session.deleted):
        raise RuntimeError("Read session cannot flush changes")


@event.listens_for(Session, "before_commit")
def _guard_read_commit(session):
    if session.info.get("read_scope"):
        raise RuntimeError("Read session cannot commit")


@event.listens_for(Session, "after_flush")
def _note_flushed_changes(session, _context):
    for row in (*session.new, *session.dirty, *session.deleted):
        _note_committed_change(session, getattr(row, "__tablename__", None))


@event.listens_for(Session, "do_orm_execute")
def _note_statement_changes(state):
    if state.is_insert or state.is_update or state.is_delete:
        if state.session.info.get("read_scope"):
            raise RuntimeError("Read session cannot execute mutations")
        _note_committed_change(state.session, getattr(getattr(state.statement, "table", None), "name", None))


@event.listens_for(Session, "after_commit")
def _wake_committed_readers(session):
    if session.in_nested_transaction():
        return  # Releasing a savepoint has not committed the outer writer.
    if session.info.pop("live_event_changed", False):
        live_event_committed.notify()
    if session.info.pop("capture_state_changed", False):
        capture_state_committed.notify()
    if session.info.pop("result_event_changed", False):
        result_event_committed.notify()


@event.listens_for(Session, "after_rollback")
def _discard_rolled_back_signals(session):
    if session.in_nested_transaction():
        return  # Preserve hints for changes in the surviving outer writer.
    session.info.pop("live_event_changed", None)
    session.info.pop("capture_state_changed", None)
    session.info.pop("result_event_changed", None)


def _engine_kwargs_for_url(database_url: str) -> dict[str, object]:
    if not database_url.startswith("sqlite"):
        return {}

    database = make_url(database_url).database
    if database in (None, "", ":memory:"):
        return {"poolclass": StaticPool}
    return {"poolclass": NullPool}


def initialize_engine(database_url: str | None = None) -> AsyncEngine:
    """Create the shared async engine once and reuse it across the app."""
    global _engine, _session_factory
    if _engine is not None:
        return _engine

    resolved_url = database_url or settings.database_url
    engine = _create_engine(resolved_url)
    _engine = engine
    _session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    return engine


def _create_engine(resolved_url: str, *, observation: bool = False) -> AsyncEngine:
    resolved_url = settings.resolve_database_url(resolved_url)
    kwargs = _engine_kwargs_for_url(resolved_url)
    if observation and resolved_url.startswith("sqlite"):
        # A diagnostic connection must have its short lock budget from birth,
        # including connection setup, before any SQL statement is submitted.
        kwargs["connect_args"] = {"timeout": .1}
    engine = create_async_engine(resolved_url, future=True, **kwargs)

    if resolved_url.startswith("sqlite"):
        @event.listens_for(engine.sync_engine, "do_connect")
        def _connection_start(_dialect, record, _args, _kwargs):
            record.info["connect_started_ns"] = monotonic_ns()

        @event.listens_for(engine.sync_engine, "connect")
        def _configure_sqlite(dbapi_connection, connection_record):
            with span("db_connection_setup"):
                cursor = dbapi_connection.cursor()
                try:
                    cursor.execute("PRAGMA foreign_keys=ON")
                    # Brokered actions retain crash-durable commits.
                    cursor.execute("PRAGMA synchronous=FULL")
                    cursor.execute(f"PRAGMA busy_timeout={100 if observation else 30000}")
                finally:
                    cursor.close()
            if started := connection_record.info.pop("connect_started_ns", None):
                record_interval("db_connection_open", started)

    return engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        initialize_engine()
    assert _session_factory is not None
    return _session_factory


def _get_observation_factory() -> async_sessionmaker[AsyncSession]:
    global _observation_engine, _observation_factory
    factory = get_session_factory()
    assert _engine is not None
    url = _engine.url.render_as_string(hide_password=False)
    # An independent engine would be a different in-memory database.
    if not url.startswith("sqlite") or make_url(url).database in (None, "", ":memory:"):
        return factory
    if _observation_factory is None:
        _observation_engine = _create_engine(url, observation=True)
        _observation_factory = async_sessionmaker(bind=_observation_engine, expire_on_commit=False)
    return _observation_factory


async def get_db_session() -> AsyncIterator[AsyncSession]:
    session_factory = get_session_factory()
    session = session_factory()
    try:
        await finish_database_operation(session.connection())
        yield session
    finally:
        with CancelScope(shield=True):
            await session.close()


async def finish_database_operation(awaitable):
    """Finish driver work before propagating cancellation and closing its handle."""
    operation = asyncio.create_task(awaitable)
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        with CancelScope(shield=True):
            while not operation.done():
                try:
                    await asyncio.shield(operation)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
        if not operation.cancelled():
            operation.exception()
        raise


@asynccontextmanager
async def session_scope(*, observation: bool = False) -> AsyncIterator[AsyncSession]:
    session_factory = _get_observation_factory() if observation else get_session_factory()
    with span("db_session"):
        session = session_factory()
        try:
            try:
                # A cancelled connect hook otherwise abandons the driver before
                # the session owns the connection and can release its file.
                await finish_database_operation(session.connection())
                yield session
                with span("db_commit"):
                    with span("db_flush"):
                        await session.flush()
                    with span("db_transaction_commit"):
                        await session.commit()
                if settings.task_orchestration_enabled and session.info.pop("task_feedback_committed", False):
                    # In-process hint only after commit; other writers/processes
                    # are caught by the durable cursor's one-second safety scan.
                    from app.services.task_orchestration import task_orchestration_service
                    task_orchestration_service.wake_feedback()
            except BaseException:
                # SSE disconnects use AnyIO level cancellation, which cancels
                # every await until the scope exits. Cleanup must finish even
                # when the request or worker is cancelled.
                with CancelScope(shield=True):
                    await session.rollback()
                raise
        finally:
            with CancelScope(shield=True):
                await session.close()


def observation_session_scope():
    """Diagnostic writes use short-budget connections to the same durable DB."""
    return session_scope(observation=True)


@asynccontextmanager
async def read_session_scope() -> AsyncIterator[AsyncSession]:
    """Lookup-only scope: detach loaded rows and close without committing.

    Guards reject ORM mutations and flushed changes. This is an application
    contract, not a sandbox for arbitrary SQL supplied by untrusted callers.
    """
    session_factory = get_session_factory()
    with span("db_session"):
        session = session_factory()
        session.info["read_scope"] = True
        # These scopes contain local lookups only. Let an in-flight lookup and
        # connection release finish before delivering AnyIO level cancellation;
        # the driver otherwise gets repeatedly cancelled during invalidation.
        with CancelScope(shield=True):
            try:
                await finish_database_operation(session.connection())
                yield session
                if session.new or session.dirty or session.deleted:
                    raise RuntimeError("Read session cannot retain changes")
            finally:
                # Detach loaded scalars and release the read transaction.
                with span("db_read_close"):
                    await session.close()
        await checkpoint_if_cancelled()


async def close_engine() -> None:
    global _engine, _session_factory, _observation_engine, _observation_factory
    # All API/worker/MCP/test entry points finish diagnostics before DB cleanup.
    from app.services.result_telemetry import observation_queue
    await observation_queue.stop()
    if _observation_engine is not None:
        await _observation_engine.dispose()
    _observation_engine = None
    _observation_factory = None
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def ensure_database_ready() -> None:
    db_path = settings.database_path
    if db_path is None:
        return
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(_prepare_sqlite_journal, settings.database_url, Path(db_path))


def _prepare_sqlite_journal(database_url: str, database_path: Path) -> None:
    with _sqlite_migration_lock(database_url):
        with closing(sqlite3.connect(database_path, timeout=30)) as connection:
            # WAL is persistent database state. Setting it on every connection
            # can contend with an existing writer, even for a read-only lookup.
            if connection.execute("PRAGMA journal_mode").fetchone()[0].casefold() != "wal":
                connection.execute("PRAGMA journal_mode=WAL").fetchall()


@contextmanager
def _sqlite_migration_lock(database_url: str, *, timeout_seconds: float = 60.0):
    """Serialize Alembic across local processes without leaving a stale lock."""
    url = make_url(settings.resolve_database_url(database_url))
    database = url.database
    if not url.drivername.startswith("sqlite") or database in (None, "", ":memory:"):
        yield
        return

    database_path = Path(database).resolve()
    lock_path = database_path.with_name(f"{database_path.name}.migration.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    with lock_path.open("a+b") as lock_file:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()

        while True:
            try:
                lock_file.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Timed out waiting for database migration lock: {lock_path}"
                    ) from exc
                time.sleep(0.1)

        try:
            yield
        finally:
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def run_migrations() -> None:
    alembic_config_path = settings.alembic_config_path
    config = Config(str(alembic_config_path))
    config.set_main_option("script_location", str(settings.backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.resolved_database_url.replace("+aiosqlite", ""))

    # Alembic warns if the config lacks path_separator. Inject a temporary one at runtime.
    with alembic_config_path.open("r", encoding="utf-8") as handle:
        config_text = handle.read()
    if "path_separator" not in config_text:
        config_text = config_text.replace("[alembic]\n", "[alembic]\npath_separator = os\n", 1)
        with NamedTemporaryFile("w", encoding="utf-8", suffix=".ini", delete=False) as temp_file:
            temp_file.write(config_text)
            temp_config_path = temp_file.name
        config = Config(temp_config_path)
        config.set_main_option("script_location", str(settings.backend_root / "alembic"))
        config.set_main_option("sqlalchemy.url", settings.resolved_database_url.replace("+aiosqlite", ""))

    with _sqlite_migration_lock(settings.database_url):
        # In-process startup already configured logging. Alembic's CLI config
        # must not replace API/worker handlers or their application log levels.
        config.attributes["configure_logger"] = False
        command.upgrade(config, "head")
    if settings.database_path is not None:
        _prepare_sqlite_journal(settings.database_url, settings.database_path)
