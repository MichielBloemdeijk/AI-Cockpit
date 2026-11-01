"""Record and file changes that start apps' event functions.

A change is queued in `app_events` inside the transaction that makes it, once
for every app with a function subscribed to it: the app that owns the data
(collection "recipe") and apps that watch it through a grant ("recipes.recipe").
The app runtime drains the queue (`runtime.py`). Each event-triggered run's
own writes are one level deeper, and chains stop at MAX_DEPTH so functions
cannot trigger each other forever.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from sqlalchemy import select

from app.app_platform.store import AppEvent, AppVersion, InstalledApp

MAX_DEPTH = 3
# Set by the function host while it serves a run's calls.
event_depth: ContextVar[int] = ContextVar("app_event_depth", default=0)


def subscribers(manifest: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    """The app's functions this change (as the app names it) triggers."""
    names = []
    for name, spec in (manifest.get("functions") or {}).items():
        trigger = spec.get("trigger") or {}
        if trigger.get("event") != payload["event"]:
            continue
        if payload["event"] == "record_changed" and trigger.get("collection") != payload["collection"]:
            continue
        if payload["event"] == "file_changed" and not payload["name"].startswith(trigger.get("prefix") or ""):
            continue
        names.append(name)
    return names


async def queue_change(session, app, payload: dict[str, Any]) -> None:
    depth = event_depth.get()
    if subscribers(app.manifest, payload):
        session.add(AppEvent(app_slug=app.slug, payload_json=payload, depth=depth))
    if payload["event"] != "record_changed":
        return
    # Apps watching this collection through a grant see it by its qualified name.
    shared = {**payload, "collection": f"{app.slug}.{payload['collection']}", "app": app.slug}
    watchers = (await session.execute(select(InstalledApp.slug, AppVersion.manifest_json).join(AppVersion, (
        AppVersion.slug == InstalledApp.slug) & (AppVersion.version == InstalledApp.active_version))
        .where(InstalledApp.slug != app.slug))).all()
    for slug, manifest in watchers:
        grants = manifest.get("grants") or {}
        if shared["collection"] in {*(grants.get("read") or []), *(grants.get("write") or [])} \
                and subscribers(manifest, shared):
            session.add(AppEvent(app_slug=slug, payload_json=shared, depth=depth))
