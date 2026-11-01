"""Record and file changes that start an app's event functions.

A change is queued in `app_events` inside the transaction that makes it, and
only when one of the app's functions subscribes to it. The app runtime drains
the queue (`runtime.py`). Each event-triggered run's own writes are one level
deeper, and chains stop at MAX_DEPTH so two functions cannot trigger each other
forever.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from app.app_platform.store import AppEvent

MAX_DEPTH = 3
# Set by the function host while it serves a run's calls.
event_depth: ContextVar[int] = ContextVar("app_event_depth", default=0)


def subscribers(manifest: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    """The app's functions this change triggers."""
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
    if subscribers(app.manifest, payload):
        session.add(AppEvent(app_slug=app.slug, payload_json=payload, depth=event_depth.get()))
