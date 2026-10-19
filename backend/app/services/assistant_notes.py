"""Jarvis's working notes: one readable view of what the user is waiting for.

A human assistant keeps a mental list: what the person is waiting to hear, what
they promised, what they already said and how it landed. Live receives this as
plain-language context instead of scattered JSON facts. The notes are facts, not
a script: Live decides what to say and when.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

from sqlalchemy import select

from app.db.tables import AssistantPromise, LiveSession, Task, TaskPresentation
from app.services.live_task_updates import TERMINAL_WORDS
from app.services.orchestration_visibility import live_context_visible, visible_attention

RECENTLY_TOLD = timedelta(hours=12)
RECENTLY_TOLD_LIMIT = 5
FOLLOW_UP_WINDOW = timedelta(hours=1)


def utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def parse_time(value) -> datetime | None:
    if isinstance(value, datetime):
        return utc(value)
    try:
        return utc(datetime.fromisoformat(str(value)))
    except (TypeError, ValueError):
        return None


def ago(moment: datetime | None, now: datetime) -> str:
    if moment is None:
        return ""
    minutes = int((now - moment).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 90:
        return f"{minutes} min ago"
    return f"{minutes // 60} h ago"


def clean(value, limit: int = 200) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


async def recent_session_ids(db, session_id: str) -> list[str]:
    """The current session and the one before it: promises live this long."""
    current = await db.get(LiveSession, session_id)
    rows = (await db.execute(select(LiveSession.id).where(LiveSession.created_at <= current.created_at)
        .order_by(LiveSession.created_at.desc()).limit(2))).scalars().all() if current else []
    return list(rows) or [session_id]


async def expire_promises(db, session_id: str) -> None:
    keep = await recent_session_ids(db, session_id)
    for promise in (await db.execute(select(AssistantPromise).where(AssistantPromise.status == "open",
            AssistantPromise.session_id.not_in(keep)))).scalars().all():
        promise.status, promise.resolution = "dropped", "Not kept before the next conversation ended"
        promise.resolved_at = datetime.now(timezone.utc)


async def promise_signature(db) -> str:
    rows = (await db.execute(select(AssistantPromise.id, AssistantPromise.status)
        .order_by(AssistantPromise.id))).all()
    return sha256(json.dumps([list(r) for r in rows]).encode()).hexdigest()[:16]


def waiting_line(row: TaskPresentation, now: datetime, session_start: datetime | None, task_status: str | None = None) -> str:
    facts = row.required_facts_json or {}
    goal = clean(facts.get("goal"), 90)
    if row.candidate_type == "question":
        return f'- The task "{goal}" asks them: "{clean(facts.get("question"))}" (waiting for their answer).'
    if row.candidate_type == "reminder":
        return f'- Reminder due: {clean(facts.get("summary") or goal)}.'
    if row.candidate_type == "progress":
        return f'- Progress on "{goal}": {clean(facts.get("summary"))}.'
    finished_at = parse_time(facts.get("completed_at"))
    word = TERMINAL_WORDS.get(str(facts.get("event_type")), "finished")
    line = f'- "{goal}" {word} {ago(finished_at, now)}: {clean(facts.get("summary"))}'
    if session_start and finished_at and finished_at < session_start:
        line += " (while you were not talking)"
    intent = ((facts.get("notification") or {}).get("intent")) or "discretionary"
    line += {"requested": " They asked to be told.",
        "suppressed": " They asked you not to announce it; answer if they ask.",
        "discretionary": " No notification request; mention it if it is useful to them."}[intent]
    assessment = facts.get("communication_assessment") or {}
    if assessment.get("classification") == "possibly_unheard" and intent != "suppressed":
        line += (" You already said it, but they may not have heard it (they talked over it or asked to hear it later)."
            " If it is still relevant, re-offer it briefly, once, at a natural opening.")
    elif assessment.get("classification") == "partial":
        line += " You started telling them but did not finish."
    if facts.get("event_type") in {"task.failed", "task.interrupted"} and task_status in {"queued", "running", "claimed"}:
        line += " A retry is already running; when you tell them about the failure, mention briefly that you are retrying."
    return line


def open_question_line(row: TaskPresentation) -> str:
    facts = row.required_facts_json or {}
    return (f'- The task "{clean(facts.get("goal"), 90)}" needs a decision: "{clean(facts.get("question"))}". '
        "Background is checking whether it can decide this itself; if the user brings it up, ask them.")


async def render(db, *, session_id: str, now: datetime | None = None) -> dict:
    """Plain-language notes plus a signature that changes only when the facts change."""
    now = now or datetime.now(timezone.utc)
    await expire_promises(db, session_id)
    session = await db.get(LiveSession, session_id)
    session_start = utc(session.created_at) if session else None
    pending = list((await db.execute(select(TaskPresentation).where(TaskPresentation.state == "pending")
        .where(live_context_visible()).order_by(TaskPresentation.created_at))).scalars().all())
    told = list((await db.execute(select(TaskPresentation).where(TaskPresentation.state == "handled",
            TaskPresentation.candidate_type == "terminal_result", TaskPresentation.handled_at >= now - RECENTLY_TOLD)
        .order_by(TaskPresentation.handled_at.desc()).limit(RECENTLY_TOLD_LIMIT))).scalars().all())
    promises = list((await db.execute(select(AssistantPromise).where(AssistantPromise.status == "open",
        AssistantPromise.session_id.in_(await recent_session_ids(db, session_id))).order_by(AssistantPromise.created_at))).scalars().all())
    statuses = dict((await db.execute(select(Task.id, Task.status).where(
        Task.id.in_([row.task_id for row in pending])))).all()) if pending else {}
    # Questions the background orchestration has not yet released to the user (it may answer them itself).
    checking = list((await db.execute(select(TaskPresentation).where(TaskPresentation.state == "pending",
        TaskPresentation.candidate_type == "question", ~visible_attention()))).scalars().all())
    lines = ["JARVIS NOTES - your own memory of what the user is waiting for. Facts, not a script."]
    lines.append("Waiting for the user:")
    lines += ([waiting_line(row, now, session_start, statuses.get(row.task_id)) for row in pending]
        + [open_question_line(row) for row in checking]) or ["- Nothing."]
    follow_ups = list((await db.execute(select(Task).where(Task.origin_surface == "orchestration",
        Task.status.in_(["queued", "waiting_for_worker", "running"]), Task.created_at >= now - FOLLOW_UP_WINDOW)
        .order_by(Task.created_at))).scalars().all())
    if follow_ups:
        lines.append("Started in the background on the user's behalf (mention briefly when relevant, e.g. after a failure):")
        lines += [f'- "{clean(t.goal, 120)}" ({t.status})' for t in follow_ups]
    if promises:
        lines.append("You promised:")
        lines += [f'- "{clean(p.text)}" ({ago(utc(p.created_at), now)}). Keep it when the moment comes.' for p in promises]
    if told:
        lines.append("Already told them (no need to repeat unless asked):")
        lines += [f'- {ago(utc(row.handled_at), now)}: "{clean((row.required_facts_json or {}).get("goal"), 90)}" - '
            f'{clean((row.required_facts_json or {}).get("summary"))}' for row in told]
    signature = sha256(json.dumps({
        "pending": [(r.id, r.source_task_event_id, ((r.required_facts_json or {}).get("notification") or {}).get("intent"),
            ((r.required_facts_json or {}).get("communication_assessment") or {}).get("classification")) for r in pending],
        "promises": [(p.id, p.status) for p in promises], "told": [r.id for r in told],
        "checking": [r.id for r in checking], "statuses": sorted(statuses.items()),
        "follow_ups": [(t.id, t.status) for t in follow_ups]}).encode()).hexdigest()[:16]
    return {"text": "\n".join(lines), "signature": signature}
