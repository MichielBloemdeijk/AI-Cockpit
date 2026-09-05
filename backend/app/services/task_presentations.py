"""Deterministic task-fact coalescing and generation-bound presentation."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from uuid import uuid4

from sqlalchemy import func, select, update

from app.db.session import session_scope
from app.db.tables import (
    NotificationOutbox,
    RealtimeSession,
    Task,
    TaskEvent,
    TaskPresentation,
    TaskProjectionCheckpoint,
    TaskQuestion,
)
from app.services.task_notifications import enqueue_task_notification
from app.services.task_error_messages import failure_category, public_failure_summary
from app.services.runtime_health import runtime_health_service
from app.services.task_service import GENERIC_REMINDER_TEXT
from app.services.task_update_policy import FOLLOW_PROGRESS, SILENT, normalize_task_update_policy


PRESENTABLE_EVENT_TYPES = {
    "task.progress": "progress",
    "task.requires_input": "question",
    "task.completed": "terminal_result",
    "task.failed": "terminal_result",
    "task.interrupted": "terminal_result",
    "task.cancelled": "terminal_result",
    "task.reminder.due": "reminder",
}
UNFINISHED_PRESENTATION_STATES = {"pending", "reserved"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class PresentationReservation:
    presentation_ids: tuple[str, ...]
    facts: tuple[dict, ...]
    total_pending: int


class TaskPresentationService:
    _PROJECTION_KEY = "task_attention"
    _BATCH_SIZE = 250

    def __init__(self) -> None:
        self._projection_lock = asyncio.Lock()
        self._projection_detail = "Awaiting the first projection pass."

    async def recover_reservations(self) -> int:
        recovered = 0
        async with session_scope() as session:
            candidates = list((await session.execute(
                select(TaskPresentation).where(TaskPresentation.state == "reserved")
            )).scalars().all())
            for candidate in candidates:
                candidate.state = "pending"
                candidate.reserved_session_id = None
                candidate.reserved_generation = None
                candidate.reserved_at = None
                candidate.defer_until = None
                candidate.retry_count += 1
                candidate.last_error = "process_restarted"
                recovered += 1
        return recovered

    async def sync_candidates(self) -> int:
        try:
            created = await self._sync_candidates()
            await runtime_health_service.healthy(
                "task_projector",
                detail=self._projection_detail,
            )
            return created
        except Exception as exc:
            await runtime_health_service.failed("task_projector", exc)
            raise

    async def _sync_candidates(self) -> int:
        """Incrementally project every committed task event exactly once."""
        created = 0
        async with self._projection_lock:
            target_sequence: int | None = None
            while True:
                async with session_scope() as session:
                    checkpoint = await session.get(TaskProjectionCheckpoint, self._PROJECTION_KEY)
                    if checkpoint is None:
                        checkpoint = TaskProjectionCheckpoint(
                            projection_key=self._PROJECTION_KEY,
                            last_sequence=0,
                        )
                        session.add(checkpoint)
                    if target_sequence is None:
                        target_sequence = int((await session.execute(
                            select(func.max(TaskEvent.sequence))
                        )).scalar_one_or_none() or 0)
                    if checkpoint.last_sequence >= target_sequence:
                        self._projection_detail = (
                            f"Caught up through task event {checkpoint.last_sequence}."
                        )
                        return created
                    events = list((await session.execute(
                        select(TaskEvent)
                        .where(TaskEvent.sequence > checkpoint.last_sequence)
                        .where(TaskEvent.sequence <= target_sequence)
                        .order_by(TaskEvent.sequence.asc())
                        .limit(self._BATCH_SIZE)
                    )).scalars().all())
                    if not events:
                        checkpoint.last_sequence = target_sequence
                        checkpoint.updated_at = utc_now()
                        self._projection_detail = (
                            f"Caught up through task event {target_sequence}."
                        )
                        return created
                    created += await self._project_batch(session, events)
                    checkpoint.last_sequence = events[-1].sequence
                    checkpoint.updated_at = utc_now()

    async def _project_batch(self, session, events: list[TaskEvent]) -> int:
        relevant = [
            event for event in events
            if event.event_type in PRESENTABLE_EVENT_TYPES
            or event.event_type == "task.question.answered"
        ]
        if not relevant:
            return 0
        task_ids = {event.task_id for event in relevant}
        tasks = {
            task.id: task
            for task in (await session.execute(select(Task).where(Task.id.in_(task_ids)))).scalars()
        }
        presentations = list((await session.execute(
            select(TaskPresentation).where(TaskPresentation.task_id.in_(task_ids))
        )).scalars().all())
        by_event = {
            item.source_task_event_id: item
            for item in presentations
            if item.source_task_event_id
        }
        by_task: dict[str, list[TaskPresentation]] = {}
        for item in presentations:
            by_task.setdefault(item.task_id, []).append(item)

        question_ids = {
            str((event.payload_json or {}).get("question_id") or "")
            for event in relevant
            if event.event_type == "task.requires_input"
        }
        questions = {
            item.id: item
            for item in (
                (await session.execute(select(TaskQuestion).where(TaskQuestion.id.in_(question_ids)))).scalars()
                if question_ids else []
            )
        }
        notification_event_ids = {
            item.source_task_event_id for item in presentations if item.source_task_event_id
        } | {event.id for event in relevant}
        notifications = list((await session.execute(
            select(NotificationOutbox).where(NotificationOutbox.task_event_id.in_(notification_event_ids))
        )).scalars().all()) if notification_event_ids else []
        notifications_by_event: dict[str, list[NotificationOutbox]] = {}
        for item in notifications:
            if item.task_event_id:
                notifications_by_event.setdefault(item.task_event_id, []).append(item)

        async def supersede_notification(event_id: str | None) -> None:
            if not event_id:
                return
            for row in notifications_by_event.get(event_id, []):
                if row.status in {"queued", "failed", "sending"}:
                    row.status = "superseded"
                    row.next_attempt_at = None
            await session.execute(
                update(NotificationOutbox)
                .where(NotificationOutbox.task_event_id == event_id)
                .where(NotificationOutbox.status.in_(["queued", "failed", "sending"]))
                .values(status="superseded", next_attempt_at=None)
            )

        async def supersede(candidate: TaskPresentation) -> None:
            candidate.state = "superseded"
            candidate.reserved_session_id = None
            candidate.reserved_generation = None
            candidate.reserved_at = None
            await supersede_notification(candidate.source_task_event_id)

        created = 0
        for event in relevant:
            if event.event_type == "task.question.answered":
                question_id = str((event.payload_json or {}).get("question_id") or "")
                for candidate in presentations:
                    if (
                        candidate.candidate_type == "question"
                        and candidate.state in UNFINISHED_PRESENTATION_STATES
                        and str((candidate.required_facts_json or {}).get("question_id") or "") == question_id
                    ):
                        await supersede(candidate)
                continue

            task = tasks.get(event.task_id)
            if task is None:
                continue
            candidate_type = PRESENTABLE_EVENT_TYPES[event.event_type]
            existing = by_event.get(event.id)
            policy = normalize_task_update_policy(task.presentation_policy)
            if task.presentation_policy != policy:
                task.presentation_policy = policy
            if policy == SILENT or (candidate_type == "progress" and policy != FOLLOW_PROGRESS):
                if existing is not None and existing.state in UNFINISHED_PRESENTATION_STATES:
                    await supersede(existing)
                continue

            facts = self._facts(event, task)
            question = questions.get(str((event.payload_json or {}).get("question_id") or ""))
            if existing is not None:
                existing.required_facts_json = facts
                existing.importance = task.importance
                await enqueue_task_notification(session, task=task, event=event, question=question)
                continue

            task_presentations = by_task.setdefault(task.id, [])
            if candidate_type == "terminal_result":
                for candidate in task_presentations:
                    if (
                        candidate.candidate_type in {"progress", "terminal_result"}
                        and candidate.state in UNFINISHED_PRESENTATION_STATES
                    ):
                        await supersede(candidate)
            elif candidate_type == "progress":
                if any(
                    candidate.candidate_type == "terminal_result"
                    and candidate.state in {"pending", "reserved", "presented"}
                    for candidate in task_presentations
                ):
                    continue
                for candidate in task_presentations:
                    if candidate.candidate_type == "progress" and candidate.state in UNFINISHED_PRESENTATION_STATES:
                        await supersede(candidate)

            candidate = TaskPresentation(
                id=str(uuid4()),
                task_id=task.id,
                candidate_type=candidate_type,
                coalescing_key=f"task:{task.id}:{candidate_type}",
                importance=task.importance,
                state="pending",
                source_task_event_id=event.id,
                required_facts_json=facts,
                idempotency_key=f"task-event-presentation:{event.id}",
            )
            session.add(candidate)
            presentations.append(candidate)
            task_presentations.append(candidate)
            by_event[event.id] = candidate
            await enqueue_task_notification(session, task=task, event=event, question=question)
            created += 1
        return created

    async def list_pending(self) -> tuple[TaskPresentation, ...]:
        async with session_scope() as session:
            result = await session.execute(
                select(TaskPresentation)
                .where(TaskPresentation.state == "pending")
                .order_by(
                    TaskPresentation.created_at.asc(),
                    TaskPresentation.id.asc(),
                )
            )
            return tuple(sorted(result.scalars().all(), key=self._delivery_priority))

    async def reserve(
        self,
        *,
        session_id: str,
        generation: int,
        user_owns_floor: bool,
        limit: int = 3,
    ) -> PresentationReservation | None:
        await self.sync_candidates()
        async with session_scope() as session:
            return await self.reserve_in_session(
                session,
                session_id=session_id,
                generation=generation,
                user_owns_floor=user_owns_floor,
                limit=limit,
            )

    async def reserve_in_session(
        self,
        session,
        *,
        session_id: str,
        generation: int,
        user_owns_floor: bool,
        limit: int = 3,
    ) -> PresentationReservation | None:
        """Reserve pending facts in the same transaction as a situation snapshot."""
        if user_owns_floor:
            return None
        realtime = await session.get(RealtimeSession, session_id)
        if (
            realtime is None
            or realtime.generation != generation
            or realtime.lifecycle_state != "ready"
        ):
            raise ValueError("Realtime session generation is stale")
        result = await session.execute(
            select(TaskPresentation)
            .where(TaskPresentation.state == "pending")
            .order_by(
                TaskPresentation.created_at.asc(),
                TaskPresentation.id.asc(),
            )
        )
        pending = sorted(result.scalars().all(), key=self._delivery_priority)
        candidates = pending[:limit]
        if not candidates:
            return None
        now = utc_now()
        facts: list[dict] = []
        for candidate in candidates:
            candidate.state = "reserved"
            candidate.reserved_session_id = session_id
            candidate.reserved_generation = generation
            candidate.reserved_at = now
            candidate.updated_at = now
            fact = dict(candidate.required_facts_json or {})
            if candidate.last_error == "contradictory_spoken_delivery":
                fact["delivery"] = {"correction_required": True}
            facts.append(fact)
        return PresentationReservation(
            presentation_ids=tuple(item.id for item in candidates),
            facts=tuple(facts),
            total_pending=len(pending),
        )

    async def mark_presented(
        self,
        *,
        presentation_ids: tuple[str, ...],
        session_id: str,
        generation: int,
        turn_id: str | None,
        delivery_id: str | None,
        allow_superseded: bool = False,
    ) -> None:
        now = utc_now()
        async with session_scope() as session:
            for presentation_id in presentation_ids:
                candidate = await session.get(TaskPresentation, presentation_id)
                if candidate is None:
                    raise ValueError(f"Task presentation not found: {presentation_id}")
                if allow_superseded and candidate.state == "superseded":
                    continue
                if (
                    candidate.state != "reserved"
                    or candidate.reserved_session_id != session_id
                    or candidate.reserved_generation != generation
                ):
                    raise ValueError("Task presentation reservation is stale")
                candidate.state = "presented"
                candidate.presented_turn_id = turn_id
                candidate.presented_delivery_id = delivery_id
                candidate.presented_at = now
                candidate.updated_at = now

    async def settle_delivery(
        self,
        *,
        presentation_ids: tuple[str, ...],
        session_id: str,
        generation: int,
        turn_id: str | None,
        delivery_id: str | None,
        heard_transcript: str,
        response_kind: str | None = None,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Acknowledge only complete, non-contradictory speech of required facts."""
        now = utc_now()
        normalized_heard = self._normalize_spoken_text(heard_transcript)
        delivered: list[str] = []
        requeued: list[str] = []
        async with session_scope() as session:
            for presentation_id in presentation_ids:
                candidate = await session.get(TaskPresentation, presentation_id)
                if candidate is None or candidate.state in {"superseded", "acknowledged"}:
                    continue
                if (
                    candidate.state != "reserved"
                    or candidate.reserved_session_id != session_id
                    or candidate.reserved_generation != generation
                ):
                    continue
                phrase = str(
                    (candidate.required_facts_json or {}).get("required_spoken_phrase") or ""
                ).strip()
                normalized_phrase = self._normalize_spoken_text(phrase)
                delivery_error = self._delivery_error(
                    facts=dict(candidate.required_facts_json or {}),
                    normalized_heard=normalized_heard,
                    normalized_phrase=normalized_phrase,
                    require_exact=(
                        response_kind == "task_update"
                        and len(presentation_ids) == 1
                        and candidate.candidate_type == "reminder"
                    ),
                )
                if delivery_error is None:
                    candidate.state = "presented"
                    candidate.presented_turn_id = turn_id
                    candidate.presented_delivery_id = delivery_id
                    candidate.presented_at = now
                    candidate.last_error = None
                    delivered.append(candidate.id)
                    if candidate.source_task_event_id:
                        rows = list((await session.execute(
                            select(NotificationOutbox).where(
                                NotificationOutbox.task_event_id == candidate.source_task_event_id
                            )
                        )).scalars().all())
                        for row in rows:
                            if row.acknowledged_at is None:
                                row.acknowledged_at = now
                                row.acknowledgement_state = "spoken"
                                row.status = "acknowledged"
                else:
                    candidate.state = "pending"
                    candidate.retry_count += 1
                    candidate.last_error = delivery_error
                    requeued.append(candidate.id)
                candidate.reserved_session_id = None
                candidate.reserved_generation = None
                candidate.reserved_at = None
                candidate.defer_until = None
                candidate.updated_at = now
        return tuple(delivered), tuple(requeued)

    async def requeue(
        self,
        *,
        presentation_ids: tuple[str, ...],
        session_id: str,
        generation: int,
        error: str | None = None,
        allow_superseded: bool = False,
    ) -> None:
        now = utc_now()
        async with session_scope() as session:
            for presentation_id in presentation_ids:
                candidate = await session.get(TaskPresentation, presentation_id)
                if candidate is None:
                    continue
                if allow_superseded and candidate.state == "superseded":
                    continue
                if (
                    candidate.state != "reserved"
                    or candidate.reserved_session_id != session_id
                    or candidate.reserved_generation != generation
                ):
                    raise ValueError("Task presentation reservation is stale")
                candidate.state = "pending"
                candidate.reserved_session_id = None
                candidate.reserved_generation = None
                candidate.reserved_at = None
                candidate.retry_count += 1
                candidate.defer_until = None
                candidate.last_error = error
                candidate.updated_at = now

    async def release_session_reservations(
        self,
        *,
        session_id: str,
        generation: int,
        error: str,
    ) -> int:
        """Return orphaned reservations to the retry queue after session loss."""
        now = utc_now()
        released = 0
        async with session_scope() as session:
            candidates = list((await session.execute(
                select(TaskPresentation)
                .where(TaskPresentation.state == "reserved")
                .where(TaskPresentation.reserved_session_id == session_id)
                .where(TaskPresentation.reserved_generation == generation)
            )).scalars().all())
            for candidate in candidates:
                candidate.state = "pending"
                candidate.reserved_session_id = None
                candidate.reserved_generation = None
                candidate.reserved_at = None
                candidate.defer_until = now
                candidate.retry_count += 1
                candidate.last_error = error
                candidate.updated_at = now
                released += 1
        return released

    @staticmethod
    async def _supersede_progress(session, task_id: str) -> None:
        result = await session.execute(
            select(TaskPresentation)
            .where(TaskPresentation.task_id == task_id)
            .where(TaskPresentation.candidate_type == "progress")
            .where(TaskPresentation.state.in_(sorted(UNFINISHED_PRESENTATION_STATES)))
        )
        for candidate in result.scalars().all():
            candidate.state = "superseded"
            candidate.reserved_session_id = None
            candidate.reserved_generation = None
            if candidate.source_task_event_id:
                await TaskPresentationService._supersede_notification(
                    session, candidate.source_task_event_id
                )

    @staticmethod
    async def _supersede_answered_questions(session, events: list[TaskEvent]) -> None:
        answered_ids = {
            str((event.payload_json or {}).get("question_id") or "")
            for event in events
        }
        candidates = list((await session.execute(
            select(TaskPresentation)
            .where(TaskPresentation.candidate_type == "question")
            .where(TaskPresentation.state.in_(sorted(UNFINISHED_PRESENTATION_STATES)))
        )).scalars().all())
        for candidate in candidates:
            question_id = str((candidate.required_facts_json or {}).get("question_id") or "")
            if question_id in answered_ids:
                candidate.state = "superseded"
                candidate.reserved_session_id = None
                candidate.reserved_generation = None
                if candidate.source_task_event_id:
                    await TaskPresentationService._supersede_notification(
                        session, candidate.source_task_event_id
                    )

    @staticmethod
    async def _supersede_notification(session, task_event_id: str) -> None:
        rows = list((await session.execute(
            select(NotificationOutbox)
            .where(NotificationOutbox.task_event_id == task_event_id)
            .where(NotificationOutbox.status.in_(["queued", "failed", "sending"]))
        )).scalars().all())
        for row in rows:
            row.status = "superseded"
            row.next_attempt_at = None

    @staticmethod
    async def _ensure_notification(session, task: Task, event: TaskEvent) -> None:
        question = None
        if event.event_type == "task.requires_input":
            question_id = str((event.payload_json or {}).get("question_id") or "")
            question = await session.get(TaskQuestion, question_id) if question_id else None
            if question is None:
                return
        await enqueue_task_notification(session, task=task, event=event, question=question)

    @staticmethod
    def _facts(event: TaskEvent, task: Task) -> dict:
        payload = dict(event.payload_json or {})
        if event.event_type in {"task.failed", "task.interrupted"}:
            raw_failure = str(payload.get("summary") or task.terminal_summary or "")
            payload["summary"] = public_failure_summary(raw_failure)
            for key in ("error", "exception", "traceback", "stack", "stderr"):
                payload.pop(key, None)
            payload["diagnostics_available"] = bool(raw_failure)
        candidate_type = PRESENTABLE_EVENT_TYPES[event.event_type]
        kind = {
            "progress": "progress",
            "question": "question",
            "terminal_result": "result",
            "reminder": "reminder",
        }[candidate_type]
        spoken_phrase = TaskPresentationService._spoken_phrase(event, task, payload)
        coalescing_key = f"task:{task.id}:{candidate_type}"
        return {
            "task_id": task.id,
            "goal": task.goal,
            "event_type": event.event_type,
            **payload,
            "schema": "jarvis.task_update.v1",
            "kind": kind,
            "required_spoken_phrase": spoken_phrase,
            "task": {
                "id": task.id,
                "goal": task.goal,
                "status": task.status,
                "importance": task.importance,
            },
            "fact": {
                "event_type": event.event_type,
                "data": payload,
            },
            "source": {
                "domain": "task",
                "task_id": task.id,
                "task_event_id": event.id,
                "sequence": event.sequence,
            },
            "presentation": {
                "candidate_type": candidate_type,
                "coalescing_key": coalescing_key,
                "importance": task.importance,
            },
        }

    @staticmethod
    def _spoken_phrase(event: TaskEvent, task: Task, payload: dict) -> str:
        goal = " ".join(str(task.goal or "Task").split()).strip() or "Task"
        if goal.lower().startswith("remind me:"):
            goal = goal.split(":", 1)[1].strip() or "Reminder"
        goal = goal[:96].rstrip()
        summary = " ".join(
            str(payload.get("summary") or task.terminal_summary or "").split()
        ).strip()
        if event.event_type == "task.reminder.due":
            if not summary or summary == GENERIC_REMINDER_TEXT:
                return GENERIC_REMINDER_TEXT
            return f"Reminder: {summary}"[:240].rstrip()
        if event.event_type == "task.requires_input":
            question = " ".join(str(payload.get("question") or "").split()).strip()
            return f"{goal} needs your input: {question}"[:240].rstrip()
        if event.event_type == "task.completed":
            detail = summary or "It finished successfully."
            return f"{goal} is complete: {detail}"[:240].rstrip()
        if event.event_type in {"task.failed", "task.interrupted"}:
            category = failure_category(
                str((event.payload_json or {}).get("summary") or task.terminal_summary or "")
            )
            if category:
                short_goal = goal[:56].rstrip()
                if len(goal) > 56:
                    short_goal = short_goal.rstrip(" ,;:-") + "…"
                return f"{short_goal} failed because of {category}."[:240].rstrip()
            detail = summary or "It could not finish."
            return f"{goal} failed: {detail}"[:240].rstrip()
        if event.event_type == "task.cancelled":
            return f"{goal} was cancelled."
        detail = summary or "Progress is available."
        return f"{goal}: {detail}"[:240].rstrip()

    @staticmethod
    def _normalize_spoken_text(value: str) -> str:
        return " ".join(re.sub(r"[^\w]+", " ", str(value or "").casefold()).split())

    @staticmethod
    def _delivery_error(
        *,
        facts: dict,
        normalized_heard: str,
        normalized_phrase: str,
        require_exact: bool,
    ) -> str | None:
        if not normalized_phrase or normalized_phrase not in normalized_heard:
            return "required_spoken_phrase_not_heard"
        if require_exact and normalized_heard != normalized_phrase:
            return "proactive_reminder_not_exact"

        remainder = normalized_heard.replace(normalized_phrase, " ", 1)
        event_type = str(facts.get("event_type") or "")
        contradictory_phrases = {
            "task.reminder.due": (
                "queued", "scheduled", "will remind", "not due", "still waiting",
            ),
            "task.completed": (
                "still queued", "still running", "not complete", "not completed",
                "not finished", "has not started", "hasn t started", "is waiting",
            ),
            "task.failed": (
                "completed successfully", "is complete", "succeeded", "still running",
                "still queued",
            ),
            "task.interrupted": (
                "completed successfully", "is complete", "still running", "still queued",
            ),
            "task.cancelled": (
                "still running", "still queued", "will continue", "not cancelled",
            ),
            "task.requires_input": (
                "no input needed", "does not need your input", "doesn t need your input",
            ),
        }.get(event_type, ())
        padded_remainder = f" {remainder} "
        if any(f" {phrase} " in padded_remainder for phrase in contradictory_phrases):
            return "contradictory_spoken_delivery"
        return None

    @staticmethod
    def _delivery_priority(candidate: TaskPresentation) -> tuple[int, datetime, str]:
        event_type = str((candidate.required_facts_json or {}).get("event_type") or "")
        rank = {
            "task.reminder.due": 0,
            "task.requires_input": 1,
            "task.failed": 2,
            "task.interrupted": 2,
            "task.cancelled": 2,
            "task.completed": 3,
            "task.progress": 4,
        }.get(event_type, 5)
        return rank, candidate.created_at, candidate.id


task_presentation_service = TaskPresentationService()
