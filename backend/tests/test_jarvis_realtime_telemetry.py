from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.db.session import session_scope
from app.db.tables import JarvisRealtimeTraceEvent
from app.services.jarvis_realtime_telemetry import JarvisRealtimeTelemetryService


@pytest.mark.asyncio
async def test_realtime_telemetry_persists_a_batch_without_transcript_content() -> None:
    service = JarvisRealtimeTelemetryService()
    await service.start()
    assert service.record({
        "trace_id": "trace-1",
        "activation_id": "activation-1",
        "session_id": None,
        "generation": None,
        "request_id": "request-1",
        "provider_response_id": None,
        "source": "browser",
        "event_type": "wake.detected",
        "observed_at": datetime.now(timezone.utc),
        "monotonic_ms": 12.0,
        "duration_ms": None,
        "detail_json": {"attempt": 1},
    })
    await service.stop()

    async with session_scope() as session:
        rows = list((await session.execute(select(JarvisRealtimeTraceEvent))).scalars())
    assert len(rows) == 1
    assert rows[0].event_type == "wake.detected"
    assert rows[0].detail_json == {"attempt": 1}
