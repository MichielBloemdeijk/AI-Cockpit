"""Isolated, durable preference prototype authored only by normal model tools.

No transcript parsing or fixture-authored preference decisions. Production does
not import this module. Task events retain preference revisions and provenance.
"""
from copy import deepcopy
from sqlalchemy import select

from app.db.session import session_scope
from app.db.tables import Task, TaskEvent, LiveSession
from app.db.repositories.tasks import TaskRepository

INTENTS = frozenset({"requested", "discretionary", "suppressed"})
EVENT = "task.notification_intent.changed"


def tool_definitions(tools):
    tools = deepcopy(tools)
    for tool in tools:
        if tool["name"] not in {"dispatch_task", "start_workflow", "set_task_update_policy"}:
            continue
        props = tool["parameters"]["properties"]
        props["notification_intent"] = {"type": "string", "enum": sorted(INTENTS),
            "description": "Latest completion notification preference. Explicit notify/promise=requested, explicit silence=suppressed, otherwise discretionary."}
        props["notification_reason"] = {"type": "string", "maxLength": 160,
            "description": "Brief faithful user request or assistant promise that established the preference."}
        tool["description"] += " Record notification preferences; later changes must be saved even if admission already occurred."
    return tools


async def save_preference(task_id, intent, reason, *, session_id, generation, call_id):
    if intent not in INTENTS:
        raise ValueError("Invalid notification intent")
    reason = " ".join(str(reason or "").split())
    if not reason or len(reason) > 160:
        raise ValueError("Notification intent needs a bounded reason")
    async with session_scope() as db:
        live = await db.get(LiveSession, session_id)
        if live is None or live.generation != generation or live.lifecycle_state not in {"connecting", "ready"}:
            raise ValueError("Notification preference requires current session authority")
        task = (await db.execute(select(Task).where(Task.id == task_id).with_for_update())).scalar_one()
        key = f"announcement-pref:{session_id}:{generation}:{call_id}"
        existing = (await db.execute(select(TaskEvent).where(TaskEvent.idempotency_key == key))).scalar_one_or_none()
        if existing:
            if existing.task_id != task_id or existing.payload_json["intent"] != intent or existing.payload_json["reason"] != reason:
                raise ValueError("Preference operation conflicts with its recorded arguments")
            return existing.payload_json
        latest = (await db.execute(select(TaskEvent).where(TaskEvent.task_id == task_id, TaskEvent.event_type == EVENT)
            .order_by(TaskEvent.sequence.desc()).limit(1))).scalar_one_or_none()
        value = {"intent": intent, "reason": reason, "revision": latest.payload_json["revision"] + 1 if latest else 1,
            "session_id": session_id, "generation": generation, "call_id": call_id}
        await TaskRepository(db).append_event(task_id=task.id, attempt_id=task.current_attempt_id,
            event_type=EVENT, payload_json=value, idempotency_key=key)
        return value


async def current_preference(task_id):
    async with session_scope() as db:
        task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()
        event = (await db.execute(select(TaskEvent).where(TaskEvent.task_id == task_id, TaskEvent.event_type == EVENT)
            .order_by(TaskEvent.sequence.desc()).limit(1))).scalar_one_or_none()
        if event:
            value = event.payload_json
            return {"intent": value["intent"], "reason": value["reason"][:100], "revision": value["revision"], "recorded": True}
        # Silent is already an explicit durable suppression. Default policy
        # conveys no request for completion speech, regardless of importance.
        return {"intent": "suppressed" if task.presentation_policy == "silent" else "discretionary",
            "reason": "Stored silent policy" if task.presentation_policy == "silent" else "No notification preference recorded",
            "revision": 0, "recorded": False}
