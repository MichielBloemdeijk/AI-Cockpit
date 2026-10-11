"""Durable unified Jarvis event replay and SSE support."""
from __future__ import annotations

from collections.abc import AsyncIterator
import json
from time import perf_counter_ns as monotonic_ns
from time import monotonic
from typing import Any

from anyio import CancelScope
from anyio.lowlevel import checkpoint_if_cancelled
from sqlalchemy import func, select

from app.db.session import read_session_scope
from app.db.tables import JarvisLiveEvent
from app.services.auth import SESSION_COOKIE_NAME, verify_session_token, authorization_scheme
from app.services.jarvis_devices import jarvis_device_service
from app.services.commit_signals import live_event_committed

RECOVERY_SCAN_SECONDS = .5
HEARTBEAT_SECONDS = 15.


class JarvisLiveService:
    @staticmethod
    def event_payload(row: JarvisLiveEvent, *, native_device: bool = False) -> dict[str, Any]:
        return {
            "id": row.id,
            "sequence": row.sequence,
            "event_type": row.event_type,
            "source_domain": row.source_domain,
            "source_id": row.source_id,
            "source_sequence": row.source_sequence,
            "payload": (
                {"targeted": True, "task_id": row.source_id}
                if not native_device and row.target_device_id is not None else row.payload_json
            ),
            "created_at": row.created_at.isoformat(),
        }

    async def latest_cursor(self) -> int:
        """Return the committed global cursor without replaying historical rows."""
        async with read_session_scope() as session:
            return int((await session.execute(
                select(func.max(JarvisLiveEvent.sequence))
            )).scalar_one_or_none() or 0)

    async def replay(self, *, after: int = 0, limit: int = 250) -> tuple[int, list[JarvisLiveEvent]]:
        async with read_session_scope() as session:
            latest = int((await session.execute(select(func.max(JarvisLiveEvent.sequence)))).scalar_one_or_none() or 0)
            rows = list((await session.execute(
                select(JarvisLiveEvent)
                .where(JarvisLiveEvent.sequence > max(0, int(after)))
                .order_by(JarvisLiveEvent.sequence.asc())
                .limit(max(1, min(int(limit), 1000)))
            )).scalars().all())
            return latest, rows

    async def stream(self, request, *, cursor: int = 0) -> AsyncIterator[dict[str, str]]:
        """Yield resumable SSE frames without holding a database transaction open."""
        current = max(0, int(cursor))
        cookie = request.cookies.get(SESSION_COOKIE_NAME)
        browser_owner = bool(cookie and verify_session_token(cookie))
        # Authentication updates last-seen in one local transaction. SSE's
        # disconnect cancel scope must not interrupt its driver cleanup.
        with CancelScope(shield=True):
            device = None if browser_owner else await jarvis_device_service.authenticate(request)
        await checkpoint_if_cancelled()
        viewer_device_id = None if device is None else device.id
        heartbeat_at = monotonic()
        while not await request.is_disconnected():
            # Snapshot before the read so a commit between the read and wait
            # cannot be missed, and every connected reader receives the signal.
            observed = live_event_committed.snapshot()
            _latest, rows = await self.replay(after=current, limit=250)
            if rows:
                for row in rows:
                    current = row.sequence
                    if row.target_device_id is not None and not browser_owner and row.target_device_id != viewer_device_id:
                        continue
                    data = self.event_payload(row, native_device=device is not None and authorization_scheme(request) == "bearer")
                    # Delivery metadata is created only in the SSE envelope.
                    # The persisted event timestamp precedes its transaction's
                    # commit; label that interval honestly instead of calling
                    # it pure network time.
                    if row.event_type == "brain.tool.result":
                        data["delivery_timing"] = {"clock": "server_monotonic",
                            "sse_emitted_ns": monotonic_ns(),
                            "result_record_started_ns": data["payload"].get("server_monotonic_ns")}
                    yield {
                        "id": str(row.sequence),
                        "event": "jarvis",
                        "data": json.dumps(data, ensure_ascii=False),
                    }
                heartbeat_at = monotonic()
                continue
            if monotonic() - heartbeat_at >= HEARTBEAT_SECONDS:
                heartbeat_at = monotonic()
                yield {"event": "heartbeat", "data": json.dumps({"cursor": current})}
            await live_event_committed.wait(observed, timeout=RECOVERY_SCAN_SECONDS)


jarvis_live_service = JarvisLiveService()
