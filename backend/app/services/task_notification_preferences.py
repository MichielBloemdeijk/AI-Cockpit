"""Model-authored preferences, atomically stored in the task's event history."""
from sqlalchemy import select, update
from app.db.tables import TaskEvent, LiveSession, LiveSessionEvent, JarvisDevice, NotificationOutbox, TaskPresentation
from app.db.repositories.tasks import TaskRepository
from app.services.task_update_policy import normalize_task_update_policy

INTENTS = frozenset({"requested", "discretionary", "suppressed"})
EVENT = "task.notification_intent.changed"


def normalize(policy, intent=None, reason=None):
    if intent is None:
        if reason is not None: raise ValueError("notification_reason requires notification_intent")
        return normalize_task_update_policy(policy or "critical_only"), None
    if intent not in INTENTS: raise ValueError("Invalid notification_intent")
    reason = " ".join(str(reason or "").split())
    if not reason or len(reason) > 160: raise ValueError("notification_reason must contain 1–160 characters")
    selected = normalize_task_update_policy(policy or ("silent" if intent == "suppressed" else "critical_only"))
    if (intent == "suppressed") != (selected == "silent"):
        raise ValueError("Notification intent conflicts with presentation_policy")
    return selected, {"intent": intent, "reason": reason}


async def current(session, task):
    event = (await session.execute(select(TaskEvent).where(TaskEvent.task_id == task.id, TaskEvent.event_type == EVENT)
        .order_by(TaskEvent.sequence.desc()).limit(1))).scalar_one_or_none()
    if event:
        p = event.payload_json
        return {"intent":p["intent"],"reason":p["reason"][:100],"revision":p["revision"],"recorded":True}
    return {"intent":"suppressed" if task.presentation_policy == "silent" else "discretionary",
        "reason":"Stored silent policy" if task.presentation_policy == "silent" else "No notification preference recorded",
        "revision":0,"recorded":False}


async def save(session, task, preference, *, key, authority=None):
    if authority:
        live = await session.get(LiveSession, authority["session_id"])
        if live is None or live.generation != authority["generation"] or live.lifecycle_state not in {"connecting","ready"}:
            raise ValueError("Notification preference requires current session authority")
    if preference is None: return await current(session, task)
    existing = (await session.execute(select(TaskEvent).where(TaskEvent.idempotency_key == key))).scalar_one_or_none()
    if existing:
        if existing.task_id != task.id or any(existing.payload_json[k] != preference[k] for k in ("intent","reason")):
            raise ValueError("Notification preference replay conflicts with recorded arguments")
        return await current(session,task)
    previous = await current(session,task)
    if authority and "expected_revision" in authority:
        device = await session.get(JarvisDevice, live.controller_id) if live.controller_id else None
        if device and device.lock_state == "locked":
            raise ValueError("Preference review is unavailable while the device is locked")
        if authority["expected_revision"] != previous["revision"]:
            raise ValueError("Preference review was superseded")
        newer = (await session.execute(select(LiveSessionEvent).where(
            LiveSessionEvent.session_id == live.id, LiveSessionEvent.event_type == "provider.user_transcript_delta",
            LiveSessionEvent.sequence > authority["source_sequence"]))).scalars().all()
        if any(e.payload_json.get("generation") == live.generation for e in newer):
            raise ValueError("Preference review has newer user context")
    value = {**preference,"revision":previous["revision"]+1,**(authority or {})}
    await TaskRepository(session).append_event(task_id=task.id,attempt_id=task.current_attempt_id,
        event_type=EVENT,payload_json=value,idempotency_key=key)
    projected = {"intent":value["intent"],"reason":value["reason"][:100],"revision":value["revision"],"recorded":True}
    rows = (await session.execute(select(TaskPresentation).where(TaskPresentation.task_id == task.id,
        TaskPresentation.state == "pending",TaskPresentation.candidate_type == "terminal_result"))).scalars().all()
    for row in rows: row.required_facts_json = {**(row.required_facts_json or {}),"notification":projected}
    if preference["intent"] == "suppressed":
        await session.execute(update(NotificationOutbox).where(NotificationOutbox.task_id == task.id,
            NotificationOutbox.status.in_(["queued","failed"])).values(status="superseded",next_attempt_at=None))
    return projected
