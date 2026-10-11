"""Backend-scoped outcome deduplication, independent of provider call delivery IDs."""
from hashlib import sha256
from datetime import datetime, timezone
import json

from sqlalchemy.dialects.sqlite import insert
from sqlalchemy import update

from app.db.session import session_scope
from app.db.tables import LiveAdmissionIntent, LiveSession


from app.services.admission_timing import timed


@timed("intent_reservation")
async def reserve_intent(*, session_id, generation, turn_scope, intent_id, tool_name, arguments):
    if not isinstance(intent_id, str) or not 1 <= len(intent_id.strip()) <= 128:
        raise ValueError("Admission requires a per-outcome intent_id")
    if not isinstance(turn_scope, str) or not 1 <= len(turn_scope) <= 255:
        raise ValueError("Admission requires a backend-owned user-turn scope")
    identity = sha256(json.dumps([session_id, generation, turn_scope, intent_id]).encode()).hexdigest()
    digest = sha256(json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    async with session_scope() as session:
        await session.execute(insert(LiveAdmissionIntent).values(id=identity, session_id=session_id,
            generation=generation, turn_scope=turn_scope, intent_id=intent_id, tool_name=tool_name,
            input_digest=digest, created_at=datetime.now(timezone.utc)).on_conflict_do_nothing(index_elements=["id"]))
        live = await session.get(LiveSession, session_id)
        if live is None or live.generation != generation or live.lifecycle_state not in {"connecting", "ready"}:
            raise ValueError("Live admission authority is stale")
        receipt = await session.get(LiveAdmissionIntent, identity)
        if receipt.tool_name != tool_name or receipt.input_digest != digest:
            raise ValueError("Intent idempotency conflict: another route or different arguments")
        return receipt


@timed("intent_completion")
async def finish_intent(identity, *, output=None, error=None):
    async with session_scope() as session:
        await session.execute(update(LiveAdmissionIntent).where(LiveAdmissionIntent.id == identity,
            LiveAdmissionIntent.output_json.is_(None), LiveAdmissionIntent.error.is_(None)).values(output_json=output, error=error))


@timed("target_pinning")
async def pin_target(identity, task_id):
    async with session_scope() as session:
        await session.execute(update(LiveAdmissionIntent).where(LiveAdmissionIntent.id == identity,
            LiveAdmissionIntent.target_task_id.is_(None)).values(target_task_id=task_id))
        receipt = await session.get(LiveAdmissionIntent, identity)
        return receipt.target_task_id
