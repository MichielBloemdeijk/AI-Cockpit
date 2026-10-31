"""The app platform's own SQLite database (`apps.db`).

It is separate from the core database so app writes never wait on task writes
and a misbehaving app can't damage core data. The schema is owned by the models
below and created on start; there is no migration chain to drift from it.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool

from app.config import settings


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AppsBase(DeclarativeBase):
    pass


class InstalledApp(AppsBase):
    __tablename__ = "installed_apps"

    slug: Mapped[str] = mapped_column(String(48), primary_key=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    active_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class AppVersion(AppsBase):
    __tablename__ = "app_versions"

    slug: Mapped[str] = mapped_column(String(48), ForeignKey("installed_apps.slug", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The validated manifest with each collection's schema embedded under "schema_json".
    manifest_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    package_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class AppRecord(AppsBase):
    __tablename__ = "app_records"
    __table_args__ = (UniqueConstraint("app_slug", "collection", "record_key", name="uq_app_records_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    app_slug: Mapped[str] = mapped_column(String(48), ForeignKey("installed_apps.slug", ondelete="CASCADE"), nullable=False)
    collection: Mapped[str] = mapped_column(String(48), nullable=False)
    record_key: Mapped[str] = mapped_column(String(128), nullable=False)
    value_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class AppError(AppsBase):
    """A UI or runtime error an app reported, kept so Jarvis can see and repair it."""
    __tablename__ = "app_errors"
    __table_args__ = (UniqueConstraint("app_slug", "fingerprint", name="uq_app_errors_fingerprint"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    app_slug: Mapped[str] = mapped_column(String(48), ForeignKey("installed_apps.slug", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    detail: Mapped[str] = mapped_column(String(4000), nullable=False, default="")
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


_engine: AsyncEngine | None = None
_engine_url: str | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def _get_factory() -> async_sessionmaker[AsyncSession]:
    global _engine, _engine_url, _factory
    url = settings.resolved_apps_database_url
    if _factory is not None and _engine_url == url:
        return _factory
    engine = create_async_engine(url, poolclass=NullPool)

    @event.listens_for(engine.sync_engine, "connect")
    def _configure(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
        finally:
            cursor.close()

    _engine, _engine_url = engine, url
    _factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    return _factory


@asynccontextmanager
async def apps_session() -> AsyncIterator[AsyncSession]:
    session = _get_factory()()
    try:
        yield session
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    finally:
        await session.close()


async def ensure_apps_database() -> None:
    url = make_url(settings.resolved_apps_database_url)
    if url.database not in (None, "", ":memory:"):
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    _get_factory()
    assert _engine is not None
    async with _engine.begin() as connection:
        await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        await connection.run_sync(AppsBase.metadata.create_all)


async def close_apps_engine() -> None:
    global _engine, _engine_url, _factory
    if _engine is not None:
        await _engine.dispose()
    _engine = _engine_url = _factory = None
