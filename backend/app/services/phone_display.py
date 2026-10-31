"""Bring a Cockpit page to the front of the phone that is talking to Jarvis.

The Live router asks for it with show_on_phone or open_app. The backend only chooses
the page and targets the session's controller device; the phone opens its own
Cockpit view on that path, or posts the result notification when it is locked.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.session import session_scope
from app.db.tables import JarvisDevice, JarvisLiveEvent, LiveSession, TaskResult
from app.services.task_results import present

OPEN_REQUEST_TTL = timedelta(seconds=30)


async def show(*, session_id: str, generation: int, request_identity: str, task_id: str, title: str) -> dict:
    async with session_scope() as session:
        result = (await session.execute(select(TaskResult).where(TaskResult.task_id == task_id)
            .order_by(TaskResult.updated_at.desc(), TaskResult.id.desc()).limit(1))).scalar_one_or_none()
    path = present(result)["url"] if result else f"/tasks/{task_id}"
    opened = await open_path(session_id=session_id, generation=generation, request_identity=request_identity,
                             path=path, title=title)
    return {"shown": "result" if result else "task", **opened}


async def open_path(*, session_id: str, generation: int, request_identity: str, path: str, title: str) -> dict:
    """Ask the session's controller phone to open a Cockpit path (a result, task or app)."""
    async with session_scope() as session:
        live = await session.get(LiveSession, session_id)
        if (live is None or live.generation != generation or live.controller_id is None
                or live.lifecycle_state not in {"connecting", "ready"}):
            raise ValueError("Showing something on the phone requires the active phone session")
        device = await session.get(JarvisDevice, live.controller_id)
        locked = device is not None and device.lock_state == "locked"
        key = f"display.open_requested:{session_id}:{request_identity}"
        # A replayed tool call must not switch the phone a second time.
        if (await session.execute(select(JarvisLiveEvent.id)
                .where(JarvisLiveEvent.idempotency_key == key))).scalar_one_or_none() is None:
            session.add(JarvisLiveEvent(
                event_type="display.open_requested", source_domain="live",
                source_id=session_id, target_device_id=live.controller_id,
                payload_json={"path": path, "title": title, "session_id": session_id, "generation": generation,
                              "expires_at": (datetime.now(timezone.utc) + OPEN_REQUEST_TTL).isoformat()},
                idempotency_key=key,
            ))
    return {"title": title,
            "delivered_as": "notification" if locked else "screen",
            "speak": ("The phone is locked, so it was sent as a notification to open after unlocking."
                      # The phone reports locking late; it posts the notification itself then.
                      if locked else "It is opening on the phone's screen, or as a notification if the phone "
                                     "is locked; say so briefly.")}
