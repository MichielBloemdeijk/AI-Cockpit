"""Bounded durable input and original evidence, scoped to one task issue."""
from __future__ import annotations

from hashlib import sha256
import json

from sqlalchemy import or_, select

from app.db.tables import JarvisPreference, MemoryItem, OrchestrationAction, OrchestrationDecision, Task, TaskArtifact, TaskEvent, TaskTurn


async def instructions(session) -> dict:
    rows = list((await session.execute(select(MemoryItem).where(
        MemoryItem.status == "approved", MemoryItem.deleted_at.is_(None),
        or_(MemoryItem.scope == "preference", MemoryItem.kind.in_(["preference", "instruction", "standing_instruction"])),
    ).order_by(MemoryItem.reviewed_at.desc(), MemoryItem.id))).scalars())
    preference = await session.get(JarvisPreference, "current")
    return {
        "standing_instructions": [{"id": row.id, "title": row.title[:200], "content": row.content[:800],
                                   "content_sha256": sha256(row.content.encode()).hexdigest(),
                                   "truncated": len(row.content) > 800} for row in rows[:8]],
        "standing_instruction_count": len(rows),
        "standing_instruction_catalog_digest": sha256(json.dumps([
            {"id": row.id, "title": row.title, "content": row.content} for row in rows
        ], ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        "preferences": {"concision": preference.concision, "time_zone": preference.time_zone} if preference else {},
    }


async def prior_actions(session, task_id: str) -> list[dict]:
    rows = (await session.execute(select(OrchestrationAction).join(OrchestrationDecision).where(
        OrchestrationDecision.task_id == task_id,
    ).order_by(OrchestrationAction.created_at.desc(), OrchestrationAction.id).limit(6))).scalars()
    return [{"id": row.id, "decision_id": row.decision_id, "operation_key": row.operation_key,
             "action": row.action, "outcome_excerpt": json.dumps(row.outcome_json, ensure_ascii=False)[:700]}
            for row in rows]


def bound_context(context: dict) -> dict:
    """12k-token target, hard serialized UTF-8 ceiling; originals stay retrievable."""
    context["context_budget"] = {"target_tokens": 12000, "max_utf8_bytes": 48000, "trimmed_sections": []}
    # Prefer current request/question/instructions to older secondary evidence.
    for section in ("events", "turns", "prior_actions", "artifacts"):
        while len(json.dumps(context, ensure_ascii=False).encode()) > 48000 and context.get(section):
            context[section].pop(0)
            if section not in context["context_budget"]["trimmed_sections"]:
                context["context_budget"]["trimmed_sections"].append(section)
    # Very large Unicode fields can exceed a byte budget despite character limits.
    while len(json.dumps(context, ensure_ascii=False).encode()) > 48000:
        context["task"]["goal"] = context["task"]["goal"][:len(context["task"]["goal"]) // 2]
        if "goal" not in context["context_budget"]["trimmed_sections"]:
            context["context_budget"]["trimmed_sections"].append("goal")
        if not context["task"]["goal"]:
            # All other sections have fixed bounded shapes. Fail closed if a
            # future extension accidentally introduces unbounded model input.
            raise ValueError("Decision context exceeds its bounded input contract")
    return context


async def inspect_evidence(session, task_id: str, arguments: dict) -> dict | None:
    for field in ("after_sequence", "event_sequence", "turn_sequence", "byte_offset"):
        value = arguments.get(field, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{field} must be a nonnegative integer")
    selectors = [key for key in ("event_sequence", "turn_sequence", "artifact_id", "instruction_id", "instructions_after", "task_goal") if key in arguments]
    if len(selectors) > 1:
        raise ValueError("Select one original evidence item at a time")
    if not selectors:
        if "byte_offset" in arguments:
            raise ValueError("A byte offset requires an evidence selector")
        return None
    selector = selectors[0]
    value = arguments[selector]
    if selector == "task_goal":
        if value is not True:
            raise ValueError("task_goal selector must be true")
        row = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        evidence = {"kind": "task_goal", "content": row.goal} if row else None
    elif selector in {"instruction_id", "instructions_after"}:
        if not isinstance(value, str) or len(value) > 36 or (selector == "instruction_id" and not value):
            raise ValueError("Instruction selector must be a bounded string")
        query = select(MemoryItem).where(
            MemoryItem.status == "approved", MemoryItem.deleted_at.is_(None),
            or_(MemoryItem.scope == "preference", MemoryItem.kind.in_(["preference", "instruction", "standing_instruction"])),
        )
        if selector == "instructions_after":
            if "byte_offset" in arguments:
                raise ValueError("Instruction listing uses its ID cursor, not a byte offset")
            rows = list((await session.execute(query.where(MemoryItem.id > value).order_by(MemoryItem.id).limit(9))).scalars())
            return {"instructions": [{"id": row.id, "title": row.title[:200], "content": row.content[:800],
                                      "truncated": len(row.content) > 800} for row in rows[:8]],
                    "next_cursor": rows[7].id if len(rows) > 8 else None, "has_more": len(rows) > 8}
        row = (await session.execute(query.where(MemoryItem.id == value))).scalar_one_or_none()
        if row is None:
            raise ValueError("Instruction is not an approved standing instruction")
        evidence = {"kind": "standing_instruction", "id": row.id, "title": row.title, "content": row.content}
    elif selector == "artifact_id":
        if not isinstance(value, str) or not value or len(value) > 36:
            raise ValueError("artifact_id must be a bounded nonempty string")
        row = (await session.execute(select(TaskArtifact).where(TaskArtifact.task_id == task_id, TaskArtifact.id == value))).scalar_one_or_none()
        evidence = {"kind": "artifact", "id": row.id, "content_text": row.content_text,
                    "content_json": row.content_json, "uri": row.uri} if row else None
    elif selector == "turn_sequence":
        row = (await session.execute(select(TaskTurn).where(TaskTurn.task_id == task_id, TaskTurn.sequence == value))).scalar_one_or_none()
        evidence = {"kind": "turn", "sequence": row.sequence, "role": row.role, "content": row.content,
                    "artifact_reference": row.artifact_reference} if row else None
    else:
        row = (await session.execute(select(TaskEvent).where(TaskEvent.task_id == task_id, TaskEvent.sequence == value))).scalar_one_or_none()
        evidence = {"kind": "event", "sequence": row.sequence, "type": row.event_type,
                    "payload": row.payload_json} if row else None
    if evidence is None:
        raise ValueError("Evidence does not belong to this task")
    # ASCII escaping makes any byte offset safe, even inside Unicode text.
    encoded = json.dumps(evidence, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    offset = arguments.get("byte_offset", 0)
    if offset > len(encoded):
        raise ValueError("byte_offset exceeds evidence payload")
    end = min(offset + 8000, len(encoded))
    return {"evidence_json_chunk": encoded[offset:end].decode("ascii"), "byte_offset": offset,
            "next_byte_offset": end, "total_bytes": len(encoded), "sha256": sha256(encoded).hexdigest()}
