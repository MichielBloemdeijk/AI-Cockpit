"""Atomic, authoritative situation projection supplied to every Jarvis response."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update

from app.config import settings
from app.db.session import session_scope
from app.db.tables import (
    Conversation,
    ConversationEvent,
    ConversationMessage,
    JarvisPreference,
    JarvisState,
    NotificationOutbox,
    RealtimeSession,
    SpeechDelivery,
    Task,
    TaskEvent,
    TaskPresentation,
    TaskQuestion,
    WorkerInstance,
)
from app.services.jarvis_time import temporal_frame
from app.services.runtime_health import runtime_health_service
from app.services.task_error_messages import public_failure_summary
from app.services.task_service import ACTIVE_TASK_STATUSES


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class JarvisSituationSnapshot:
    revision: int
    payload: dict[str, Any]


class JarvisSituationProjector:
    async def project(
        self,
        *,
        conversation_id: str,
        session_id: str | None = None,
        now: datetime | None = None,
        advance_revision: bool = True,
    ) -> JarvisSituationSnapshot:
        async with session_scope() as session:
            return await self.project_in_session(
                session,
                conversation_id=conversation_id,
                session_id=session_id,
                now=now,
                advance_revision=advance_revision,
            )

    async def project_in_session(
        self,
        session,
        *,
        conversation_id: str,
        session_id: str | None = None,
        now: datetime | None = None,
        advance_revision: bool = True,
    ) -> JarvisSituationSnapshot:
        timestamp = (now or utc_now()).astimezone(timezone.utc)
        conversation = await session.get(Conversation, conversation_id)
        state = await session.get(JarvisState, "current")
        if conversation is None or state is None:
            raise ValueError("Jarvis situation requires the current conversation")
        if advance_revision:
            await session.execute(
                update(JarvisState)
                .where(JarvisState.id == "current")
                .values(situation_revision=JarvisState.situation_revision + 1)
            )
            await session.refresh(state)

        preferences = await session.get(JarvisPreference, "current")
        realtime = await session.get(RealtimeSession, session_id) if session_id else None
        active_time_zone = (
            realtime.client_time_zone
            if realtime is not None and realtime.client_time_zone
            else ("Europe/Amsterdam" if preferences is None else preferences.time_zone)
        )

        active_tasks = list((await session.execute(
            select(Task)
            .where(Task.status.in_(sorted(ACTIVE_TASK_STATUSES)))
            .order_by(Task.creation_order.asc())
        )).scalars().all())
        terminal_tasks = list((await session.execute(
            select(Task)
            .where(Task.status.in_(["completed", "failed", "interrupted", "cancelled"]))
            .order_by(Task.finished_at.desc(), Task.creation_order.desc())
            .limit(12)
        )).scalars().all())
        reminder_rows = list((await session.execute(
            select(Task)
            .where(Task.scheduled_delivery_json.is_not(None))
            .order_by(Task.creation_order.desc())
            .limit(30)
        )).scalars().all())
        reminders = [
            task for task in reminder_rows
            if (task.scheduled_delivery_json or {}).get("kind") == "reminder"
        ]

        active_ids = [task.id for task in active_tasks]
        questions = []
        if active_ids:
            questions = list((await session.execute(
                select(TaskQuestion)
                .where(TaskQuestion.task_id.in_(active_ids))
                .where(TaskQuestion.state == "asked")
                .order_by(TaskQuestion.asked_at.asc())
            )).scalars().all())

        pending_presentations = list((await session.execute(
            select(TaskPresentation)
            .where(TaskPresentation.state.in_(["pending", "reserved"]))
            .order_by(TaskPresentation.created_at.asc(), TaskPresentation.id.asc())
        )).scalars().all())
        delivered_presentations = list((await session.execute(
            select(TaskPresentation)
            .where(TaskPresentation.state.in_(["presented", "acknowledged"]))
            .order_by(TaskPresentation.presented_at.desc(), TaskPresentation.created_at.desc())
            .limit(10)
        )).scalars().all())
        deliveries = list((await session.execute(
            select(SpeechDelivery)
            .order_by(SpeechDelivery.created_at.desc())
            .limit(12)
        )).scalars().all())
        notifications = list((await session.execute(
            select(NotificationOutbox)
            .order_by(NotificationOutbox.created_at.desc())
            .limit(12)
        )).scalars().all())

        messages = list((await session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .where(ConversationMessage.communication_state.in_(["completed", "interrupted"]))
            .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
            .limit(48)
        )).scalars().all())
        messages.reverse()
        dialogue: list[dict[str, str]] = []
        remaining = 6_000
        for message in reversed(messages[-6:]):
            content = message.content[-min(len(message.content), remaining, 1_500):]
            dialogue.append({
                "role": message.role,
                "content": content,
                "communication_state": message.communication_state,
            })
            remaining -= len(content)
            if remaining <= 0:
                break
        dialogue.reverse()
        summary_lines = [
            f"{'User' if message.role == 'user' else 'Jarvis'}: "
            f"{' '.join(message.content.split())[:240]}"
            for message in messages[:-6]
            if message.role in {"user", "assistant"} and message.content.strip()
        ]
        if summary_lines and advance_revision:
            state.conversation_summary = "\n".join(summary_lines)[-4_000:]

        worker = (await session.execute(
            select(WorkerInstance)
            .where(
                WorkerInstance.stopped_at.is_(None),
                WorkerInstance.state.in_(["starting", "idle", "busy"]),
            )
            .order_by(WorkerInstance.heartbeat_at.desc())
            .limit(1)
        )).scalar_one_or_none()
        worker_online = bool(
            worker is not None
            and _aware(worker.heartbeat_at) is not None
            and _aware(worker.heartbeat_at) >= timestamp - timedelta(seconds=settings.worker_stale_after_seconds)
        )
        component_health = await runtime_health_service.snapshot_in_session(session, now=timestamp)
        component_health["worker"] = {
            "status": "healthy" if worker_online else "offline",
            "detail": None if worker_online else "No live task worker is available.",
            "worker_id": None if worker is None else worker.id,
            "heartbeat_at": None if worker is None else _aware(worker.heartbeat_at).isoformat(),
        }

        conversation_cursor = int((await session.execute(
            select(func.max(ConversationEvent.sequence))
            .where(ConversationEvent.conversation_id == conversation_id)
        )).scalar_one_or_none() or 0)
        task_cursor = int((await session.execute(
            select(func.max(TaskEvent.sequence))
        )).scalar_one_or_none() or 0)

        def task_value(task: Task) -> dict[str, Any]:
            due = _aware(task.resume_at)
            summary = task.terminal_summary
            diagnostics_available = False
            if task.status in {"failed", "interrupted"} and summary:
                summary = public_failure_summary(summary)
                diagnostics_available = True
            return {
                "task_id": task.id,
                "goal": task.goal,
                "status": task.status,
                "status_reason": task.status_reason,
                "summary": summary,
                "diagnostics_available": diagnostics_available,
                "importance": task.importance,
                "due_at": None if due is None else due.isoformat(),
                "overdue": bool(task.status == "waiting_until" and due is not None and due <= timestamp),
                "created_at": _aware(task.created_at).isoformat(),
                "finished_at": None if task.finished_at is None else _aware(task.finished_at).isoformat(),
            }

        def presentation_value(item: TaskPresentation) -> dict[str, Any]:
            return {
                "presentation_id": item.id,
                "task_id": item.task_id,
                "type": item.candidate_type,
                "state": item.state,
                "facts": item.required_facts_json,
                "presented_at": None if item.presented_at is None else _aware(item.presented_at).isoformat(),
            }

        upcoming = [task_value(task) for task in reminders if task.status == "waiting_until" and not task_value(task)["overdue"]]
        overdue = [task_value(task) for task in reminders if task.status == "waiting_until" and task_value(task)["overdue"]]
        recent_reminders = [task_value(task) for task in reminders if task.status == "completed"][:20]
        payload = {
            "schema": "jarvis.situation.v1",
            "situation_revision": state.situation_revision,
            "conversation_id": conversation_id,
            "temporal": temporal_frame(time_zone=active_time_zone, now=timestamp),
            "conversation_summary": state.conversation_summary,
            "dialogue": dialogue,
            "tasks": [task_value(task) for task in active_tasks],
            "recent_terminal_tasks": [task_value(task) for task in terminal_tasks],
            "reminders": {
                "upcoming": upcoming,
                "overdue": overdue,
                "recently_completed": recent_reminders,
            },
            "questions": [
                {
                    "task_id": item.task_id,
                    "question_id": item.id,
                    "question": item.question,
                    "asked_at": _aware(item.asked_at).isoformat(),
                }
                for item in questions
            ],
            "presentations": [presentation_value(item) for item in pending_presentations],
            "recently_delivered_presentations": [
                presentation_value(item) for item in delivered_presentations
            ],
            "speech_deliveries": [
                {
                    "delivery_id": item.id,
                    "state": item.state,
                    "heard_transcript": item.heard_transcript,
                    "presentation_id": item.presentation_id,
                    "completed_at": None if item.completed_at is None else _aware(item.completed_at).isoformat(),
                }
                for item in deliveries
            ],
            "notification_deliveries": [
                {
                    "delivery_id": item.id,
                    "task_id": item.task_id,
                    "task_event_id": item.task_event_id,
                    "status": item.status,
                    "acknowledgement_state": item.acknowledgement_state,
                    "acknowledged_at": None if item.acknowledged_at is None else _aware(item.acknowledged_at).isoformat(),
                }
                for item in notifications
            ],
            "focus": {
                "focused_task_id": state.focused_task_id,
                "recent_task_refs": list(state.recent_task_refs_json or []),
            },
            "component_health": component_health,
            "cursors": {
                "conversation": conversation_cursor,
                "task": task_cursor,
                "session": 0 if realtime is None else realtime.last_event_sequence,
            },
            "preferences": {
                "voice": "marin" if preferences is None else preferences.voice,
                "concision": "concise" if preferences is None else preferences.concision,
                "wake_behavior": "follow_up" if preferences is None else preferences.wake_behavior,
                "time_zone": active_time_zone,
            },
        }
        return JarvisSituationSnapshot(revision=state.situation_revision, payload=payload)


jarvis_situation_projector = JarvisSituationProjector()
