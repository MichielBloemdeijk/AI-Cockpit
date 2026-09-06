"""Durable unified Jarvis event replay and SSE support."""
from __future__ import annotations

from collections.abc import AsyncIterator
import asyncio
import json

from sqlalchemy import func, select

from app.db.session import session_scope
from app.db.tables import JarvisLiveEvent


class JarvisLiveService:
    async def latest_cursor(self) -> int:
        """Return the committed global cursor without replaying historical rows."""
        async with session_scope() as session:
            return int((await session.execute(
                select(func.max(JarvisLiveEvent.sequence))
            )).scalar_one_or_none() or 0)

    async def replay(self, *, after: int = 0, limit: int = 250) -> tuple[int, list[JarvisLiveEvent]]:
        async with session_scope() as session:
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
        heartbeat = 0
        while not await request.is_disconnected():
            _latest, rows = await self.replay(after=current, limit=250)
            if rows:
                for row in rows:
                    current = row.sequence
                    yield {
                        "id": str(row.sequence),
                        "event": "jarvis",
                        "data": json.dumps(
                            {
                                "id": row.id,
                                "sequence": row.sequence,
                                "event_type": row.event_type,
                                "source_domain": row.source_domain,
                                "source_id": row.source_id,
                                "source_sequence": row.source_sequence,
                                "payload": row.payload_json,
                                "created_at": row.created_at.isoformat(),
                            },
                            ensure_ascii=False,
                        ),
                    }
                heartbeat = 0
                continue
            heartbeat += 1
            if heartbeat >= 30:
                heartbeat = 0
                yield {"event": "heartbeat", "data": json.dumps({"cursor": current})}
            await asyncio.sleep(0.5)


jarvis_live_service = JarvisLiveService()
