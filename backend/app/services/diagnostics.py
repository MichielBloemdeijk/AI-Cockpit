"""Read-only fault diagnosis: turn scattered health signals into findings with proposed fixes.

Each finding names a symptom, the likely component, evidence and a proposed fix
that a coding agent (or the user) can act on. Nothing here repairs anything.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.db.tables import (AssistantPromise, ConversationObservation, NotificationOutbox, RuntimeComponentHealth,
    TaskPresentation)

STALE_HEARTBEAT = timedelta(minutes=2)
UNDELIVERED_REQUEST = timedelta(minutes=15)
STUCK_OUTBOX = timedelta(minutes=10)


def _utc(value):
    return None if value is None else (value if value.tzinfo else value.replace(tzinfo=timezone.utc))


def _finding(severity, component, symptom, evidence, fix):
    return {"severity": severity, "component": component, "symptom": symptom, "evidence": evidence, "proposed_fix": fix}


async def diagnose(db, *, now: datetime | None = None, observer=None, live: bool = True) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    findings: list[dict] = []

    for row in (await db.execute(select(RuntimeComponentHealth))).scalars().all():
        heartbeat = _utc(row.heartbeat_at)
        if row.status == "failed":
            findings.append(_finding("high", row.component, f"Runtime component {row.component} reports failure",
                {"detail": row.detail, "failures": row.failure_count, "last_failure_at": str(row.last_failure_at)},
                "Read the component's detail and backend log around last_failure_at; fix the cause and restart the backend."))
        elif live and heartbeat and now - heartbeat > STALE_HEARTBEAT and row.status not in {"stopped", "disabled"}:
            findings.append(_finding("medium", row.component, f"Runtime component {row.component} stopped sending heartbeats",
                {"status": row.status, "heartbeat_at": str(row.heartbeat_at)},
                "Check whether its background task crashed (backend log) or the process is hung; restart the backend."))

    if observer is not None and observer.failure:
        findings.append(_finding("high", "conversation_observer", "The conversation bookkeeper stopped",
            {"failure": observer.failure},
            "Pending updates stay pending until restart. Check provider access (API key, quota) and the failed "
            "conversation_observations rows, then restart the backend."))
    failed_jobs = (await db.execute(select(ConversationObservation).where(ConversationObservation.status == "failed",
        ConversationObservation.created_at >= now - timedelta(days=1)).order_by(ConversationObservation.created_at.desc())
        .limit(5))).scalars().all()
    if failed_jobs:
        findings.append(_finding("medium", "conversation_observer", f"{len(failed_jobs)} bookkeeping assessment(s) failed in the last day",
            {"errors": [j.error for j in failed_jobs]},
            "Invalid model answers point at the observer prompt/schema (conversation_observer.py); HTTP errors at "
            "provider access. Re-check with the L1 replay (python -m app.selftest replay)."))

    pending = (await db.execute(select(TaskPresentation).where(TaskPresentation.state == "pending",
        TaskPresentation.candidate_type == "terminal_result"))).scalars().all()
    for row in pending:
        facts = row.required_facts_json or {}
        if ((facts.get("notification") or {}).get("intent")) != "requested":
            continue
        age = now - _utc(row.created_at)
        if age < UNDELIVERED_REQUEST:
            continue
        assessment = (facts.get("communication_assessment") or {}).get("classification")
        findings.append(_finding("high" if assessment != "possibly_unheard" else "medium", "natural_updates",
            f'Requested update for "{facts.get("goal")}" is still undelivered after {int(age.total_seconds() // 60)} min',
            {"presentation_id": row.id, "assessment": assessment, "commentary_attempted_at": str(row.commentary_attempted_at)},
            "If no assessment exists, Jarvis never spoke it: check the Live session events for the commentary append "
            "and the notes. If assessed but not handled, check the observer validation reason (playback evidence). "
            "Reproduce with python -m app.selftest run --suite core --case requested_idle --live."))

    outbox = (await db.execute(select(NotificationOutbox).where(NotificationOutbox.status.in_(["queued", "failed"]))
        )).scalars().all()
    stuck = [o for o in outbox if now - _utc(o.created_at) > STUCK_OUTBOX]
    if stuck:
        findings.append(_finding("medium", "away_notifications", f"{len(stuck)} away notification(s) are stuck",
            {"statuses": [o.status for o in stuck], "last_errors": [o.last_error for o in stuck if o.last_error][:3]},
            "Check Web Push configuration (VAPID keys, WEB_PUSH_ENABLED) and the device subscription; failed rows "
            "retry with backoff up to the retry limit."))

    try:
        dropped = await db.scalar(select(func.count()).select_from(AssistantPromise).where(AssistantPromise.status == "dropped",
            AssistantPromise.resolved_at >= now - timedelta(days=7)))
    except OperationalError:  # databases recorded before promise tracking
        dropped = 0
    if dropped:
        findings.append(_finding("low", "natural_updates", f"Jarvis did not keep {dropped} promise(s) this week",
            {"dropped_promises": dropped},
            "Review the promise texts and their conversations; if Jarvis keeps over-promising, tighten the Live update "
            "policy (live_task_updates.py) and add an adaptive scenario reproducing it."))
    return findings
