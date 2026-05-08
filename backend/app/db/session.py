"""Async SQLAlchemy engine and session helpers."""
from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path
from tempfile import NamedTemporaryFile

from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def initialize_engine(database_url: str | None = None) -> AsyncEngine:
    """Create the shared async engine once and reuse it across the app."""
    global _engine, _session_factory
    if _engine is not None:
        return _engine

    resolved_url = database_url or settings.database_url
    engine = create_async_engine(resolved_url, future=True)

    if resolved_url.startswith("sqlite"):
        @event.listens_for(engine.sync_engine, "connect")
        def _configure_sqlite(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
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

    command.upgrade(config, "head")