"""Errors apps report from their UI, deduplicated per app version and message.

They are the evidence a repair starts from: `status` shows them to Jarvis and
`fix_app` passes them to the builder.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Any

from sqlalchemy import delete, select

from app.app_platform.lifecycle import get_active
from app.app_platform.store import AppError, apps_session, utc_now

KEPT_PER_APP = 50


async def record_error(slug: str, message: str, detail: str = "") -> None:
    app = await get_active(slug)
    message, detail = " ".join(message.split())[:1000] or "Unknown error", detail[:4000]
    fingerprint = sha256(f"{app.version}\n{message}".encode()).hexdigest()
    async with apps_session() as session:
        row = (await session.execute(select(AppError).where(AppError.app_slug == slug,
            AppError.fingerprint == fingerprint))).scalar_one_or_none()
        if row is not None:
            row.count += 1
            row.last_seen_at = utc_now()
            row.detail = detail or row.detail
            return
        session.add(AppError(app_slug=slug, version=app.version, fingerprint=fingerprint,
                             message=message, detail=detail))
        await session.flush()
        stale = select(AppError.id).where(AppError.app_slug == slug).order_by(
            AppError.last_seen_at.desc()).offset(KEPT_PER_APP)
        await session.execute(delete(AppError).where(AppError.id.in_(stale)))


async def recent_errors(slug: str, limit: int = 10) -> list[dict[str, Any]]:
    async with apps_session() as session:
        rows = (await session.execute(select(AppError).where(AppError.app_slug == slug)
            .order_by(AppError.last_seen_at.desc()).limit(limit))).scalars()
        return [{"version": row.version, "message": row.message, "detail": row.detail, "count": row.count,
                 "last_seen_at": row.last_seen_at.isoformat()} for row in rows]


async def clear_errors(slug: str) -> None:
    async with apps_session() as session:
        await session.execute(delete(AppError).where(AppError.app_slug == slug))
