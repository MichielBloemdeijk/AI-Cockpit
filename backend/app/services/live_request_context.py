"""Refresh a delegated request before admission, without interpreting speech."""
import asyncio
from hashlib import sha256
import json

from sqlalchemy import select

from app.db.session import session_scope
from app.db.tables import LiveAdmissionIntent, LiveSession, LiveSessionEvent


async def admission_reserved(session_id, generation, turn_scope, intent_id):
    identity = sha256(json.dumps([session_id, generation, turn_scope, intent_id]).encode()).hexdigest()
    async with session_scope() as db:
        return await db.get(LiveAdmissionIntent, identity) is not None


async def advanced_context(session_id, generation, offset_ms, correlation_id, *, quiet=.8, bound=2.5):
    if not isinstance(offset_ms, int):
        return None
    clock = asyncio.get_running_loop().time
    deadline = clock() + bound
    seen, changed_at = None, clock()
    while True:
        async with session_scope() as db:
            live = await db.get(LiveSession, session_id)
            if live is None or live.generation != generation or live.lifecycle_state not in {"ready", "connecting"}:
                raise ValueError("Request context requires current session authority")
            rows = list(reversed((await db.execute(select(LiveSessionEvent).where(
                LiveSessionEvent.session_id == session_id,
                LiveSessionEvent.event_type.in_(["provider.user_transcript_delta", "brain.tool.result"]))
                .order_by(LiveSessionEvent.sequence.desc()).limit(128))).scalars().all()))
            watermark = max((e.payload_json.get("output", {}).get("request_context_watermark", 0)
                for e in rows if e.event_type == "brain.tool.result"
                and e.payload_json.get("generation") == generation
                and e.payload_json.get("origin_correlation_id") == correlation_id), default=0)
            speech = [e for e in rows if e.event_type == "provider.user_transcript_delta"
                and e.sequence > watermark and e.payload_json.get("generation") == generation
                and not e.payload_json.get("transcript_redacted") and e.payload_json.get("delta")
                and isinstance(e.payload_json.get("start_ms"), (int, float))
                and e.payload_json["start_ms"] > offset_ms]
            latest = next((e for e in reversed(rows) if e.event_type == "provider.user_transcript_delta"
                and e.payload_json.get("generation") == generation), None)
            if latest and latest.id != seen:
                seen, changed_at = latest.id, clock()
            age = clock() - changed_at if latest else quiet
        if age >= quiet or asyncio.get_running_loop().time() >= deadline:
            if not speech:
                return None
            return {"status": "request_context_advanced", "applied": False,
                "request_context_watermark": max(e.sequence for e in speech),
                "later_user_context": {"turn_completion": "unknown", "fragments": [
                    {"fragment_id": e.id, "text": e.payload_json["delta"],
                     "start_ms": e.payload_json.get("start_ms"), "end_ms": e.payload_json.get("end_ms")}
                    for e in speech]},
                "instruction": "No task was created. Reconsider the entire request with this newer speech, then use the normal admission tool with the same intent_id."}
        await asyncio.sleep(min(.1, max(0, deadline - asyncio.get_running_loop().time())))
