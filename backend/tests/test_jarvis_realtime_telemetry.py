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
        "client_sequence": 7,
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
    assert rows[0].client_sequence == 7
    assert rows[0].detail_json == {"attempt": 1}


def test_realtime_telemetry_acceptance_is_a_prefix_when_queue_is_full() -> None:
    service = JarvisRealtimeTelemetryService(queue_size=2)
    events = [
        {
            "trace_id": "trace-prefix",
            "source": "browser",
            "event_type": f"event-{index}",
            "observed_at": datetime.now(timezone.utc),
        }
        for index in range(3)
    ]

    assert service.record_many(events) == 2
    assert [service._queue.get_nowait()["event_type"] for _ in range(2)] == [
        "event-0",
        "event-1",
    ]


@pytest.mark.asyncio
async def test_realtime_telemetry_keeps_only_the_newest_rows_at_the_hard_cap() -> None:
    service = JarvisRealtimeTelemetryService(max_rows=3)
    await service.start()
    for index in range(5):
        assert service.record({
            "trace_id": "trace-cap",
            "client_sequence": index + 1,
            "session_id": None,
            "generation": None,
            "request_id": None,
            "provider_response_id": None,
            "source": "browser",
            "event_type": "turn.result",
            "observed_at": datetime.now(timezone.utc),
            "monotonic_ms": float(index),
            "duration_ms": None,
            "detail_json": None,
        })
    await service.stop()
    await service._prune()

    async with session_scope() as session:
        rows = list((await session.execute(
            select(JarvisRealtimeTraceEvent)
            .where(JarvisRealtimeTraceEvent.trace_id == "trace-cap")
            .order_by(JarvisRealtimeTraceEvent.sequence)
        )).scalars())
    assert [row.client_sequence for row in rows] == [3, 4, 5]
