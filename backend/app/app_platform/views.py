"""Which record each app is showing, reported by the app host and the SDK.

Only the latest view per app is kept. Jarvis uses the most recent one to resolve
references such as "this recipe" or "change the page I'm looking at".
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.app_platform.lifecycle import get_active
from app.app_platform.records import RecordError, _check_key
from app.app_platform.store import AppView, InstalledApp, apps_session, utc_now


async def record_view(slug: str, collection: str, key: str) -> None:
    app = await get_active(slug)
    if collection not in app.collections:
        raise RecordError(f"{slug} has no collection {collection}")
    _check_key(key)
    async with apps_session() as session:
        view = await session.get(AppView, slug)
        if view is None:
            session.add(AppView(app_slug=slug, collection=collection, record_key=key))
        else:
            view.collection, view.record_key, view.viewed_at = collection, key, utc_now()


async def last_view(since: datetime) -> dict[str, Any] | None:
    """The most recent record view across apps at or after `since`."""
    async with apps_session() as session:
        row = (await session.execute(select(AppView, InstalledApp.title)
            .join(InstalledApp, InstalledApp.slug == AppView.app_slug)
            .where(AppView.viewed_at >= since).order_by(AppView.viewed_at.desc()).limit(1))).first()
    if row is None:
        return None
    view, title = row
    return {"app": view.app_slug, "app_title": title, "collection": view.collection, "key": view.record_key,
            "viewed_at": view.viewed_at}
