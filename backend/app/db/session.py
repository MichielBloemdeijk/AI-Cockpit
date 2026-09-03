"""Async SQLAlchemy engine and session helpers."""
from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from collections.abc import AsyncIterator
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import time

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

from app.config import settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


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
    engine = create_async_engine(resolved_url, future=True, **_engine_kwargs_for_url(resolved_url))

    if resolved_url.startswith("sqlite"):
        @event.listens_for(engine.sync_engine, "connect")
        def _configure_sqlite(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    _engine = engine
    _session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    return engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        initialize_engine()
    assert _session_factory is not None
    return _session_factory


async def get_db_session() -> AsyncIterator[AsyncSession]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def ensure_database_ready() -> None:
    db_path = settings.database_path
    if db_path is None:
        return
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def _sqlite_migration_lock(database_url: str, *, timeout_seconds: float = 60.0):
    """Serialize Alembic across local processes without leaving a stale lock."""
    url = make_url(database_url)
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
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("+aiosqlite", ""))

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
        config.set_main_option("sqlalchemy.url", settings.database_url.replace("+aiosqlite", ""))

    with _sqlite_migration_lock(settings.database_url):
        command.upgrade(config, "head")
