"""Deterministic task-fact coalescing and generation-bound presentation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import or_, select

from app.db.session import session_scope
from app.db.tables import RealtimeSession, Task, TaskEvent, TaskPresentation


PRESENTABLE_EVENT_TYPES = {
    "task.progress": "progress",
    "task.requires_input": "question",
    "task.completed": "terminal_result",
    "task.failed": "terminal_result",
    "task.interrupted": "terminal_result",
    "task.cancelled": "terminal_result",
    "task.reminder.due": "reminder",
}
UNFINISHED_PRESENTATION_STATES = {"pending", "reserved", "deferred"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class PresentationReservation:
    presentation_ids: tuple[str, ...]
    facts: tuple[dict, ...]


class TaskPresentationService:
    async def sync_candidates(self) -> int:
        """Project presentation candidates from durable task events.

        This projection can be safely rebuilt after a process restart. Existing
        worker-created terminal candidates are enriched instead of duplicated.
        """
        created = 0
        async with session_scope() as session:
            events = list((await session.execute(
                select(TaskEvent)
                .where(TaskEvent.event_type.in_([*sorted(PRESENTABLE_EVENT_TYPES), "task.question.answered"]))
                .order_by(TaskEvent.sequence.asc())
            )).scalars().all())
            for event in events:
                if event.event_type == "task.question.answered":
                    continue
                candidate_type = PRESENTABLE_EVENT_TYPES[event.event_type]
                existing = (await session.execute(
                    select(TaskPresentation)
                    .where(TaskPresentation.source_task_event_id == event.id)
                    .limit(1)
                )).scalar_one_or_none()
                if existing is not None:
                    continue
                task = (await session.execute(
                    select(Task).where(Task.id == event.task_id).limit(1)
                )).scalar_one_or_none()
                if task is None or task.presentation_policy == "never":
                    continue

                facts = self._facts(event, task)
                if candidate_type == "terminal_result":
                    await self._supersede_progress(session, task.id)
                    existing_terminal = (await session.execute(
                        select(TaskPresentation)
                        .where(TaskPresentation.task_id == task.id)
                        .where(TaskPresentation.candidate_type == "terminal_result")
                        .where(TaskPresentation.state.in_(sorted(UNFINISHED_PRESENTATION_STATES)))
                        .order_by(TaskPresentation.created_at.desc())
                        .limit(1)
                    )).scalar_one_or_none()
                    if existing_terminal is not None:
                        existing_terminal.source_task_event_id = event.id
                        existing_terminal.required_facts_json = facts
                        existing_terminal.importance = task.importance
                        continue
                elif candidate_type == "progress":
                    terminal = (await session.execute(
                        select(TaskPresentation.id)
                        .where(TaskPresentation.task_id == task.id)
                        .where(TaskPresentation.candidate_type == "terminal_result")
                        .where(TaskPresentation.state.in_(["pending", "reserved", "presented", "deferred"]))
                        .limit(1)
                    )).scalar_one_or_none()
                    if terminal is not None:
                        continue
                    await self._supersede_progress(session, task.id)

                session.add(
                    TaskPresentation(
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
                )
                created += 1

            answered_events = [event for event in events if event.event_type == "task.question.answered"]
            if answered_events:
                await self._fold_answered_questions(session, answered_events)
        return created

    async def list_pending(self) -> tuple[TaskPresentation, ...]:
        await self.sync_candidates()
        now = utc_now()
        async with session_scope() as session:
            result = await session.execute(
                select(TaskPresentation)
                .where(TaskPresentation.state.in_(["pending", "deferred"]))
                .where(or_(TaskPresentation.defer_until.is_(None), TaskPresentation.defer_until <= now))
                .order_by(
                    TaskPresentation.created_at.asc(),
                    TaskPresentation.id.asc(),
                )
            )
            return tuple(result.scalars().all())

    async def reserve(
        self,
        *,
        session_id: str,
        generation: int,
        user_owns_floor: bool,
        limit: int = 3,
    ) -> PresentationReservation | None:
        await self.sync_candidates()
        if user_owns_floor:
            return None
        now = utc_now()
        async with session_scope() as session:
            realtime = await session.get(RealtimeSession, session_id)
            if (
                realtime is None
                or realtime.generation != generation
                or realtime.lifecycle_state != "ready"
            ):
                raise ValueError("Realtime session generation is stale")
            result = await session.execute(
                select(TaskPresentation)
                .where(TaskPresentation.state.in_(["pending", "deferred"]))
                .where(or_(TaskPresentation.defer_until.is_(None), TaskPresentation.defer_until <= now))
                .order_by(
                    TaskPresentation.created_at.asc(),
                    TaskPresentation.id.asc(),
                )
                .limit(limit)
            )
            candidates = list(result.scalars().all())
            if not candidates:
                return None
            for candidate in candidates:
                candidate.state = "reserved"
                candidate.reserved_session_id = session_id
                candidate.reserved_generation = generation
                candidate.reserved_at = now
                candidate.updated_at = now
            return PresentationReservation(
                presentation_ids=tuple(item.id for item in candidates),
                facts=tuple(dict(item.required_facts_json or {}) for item in candidates),
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
                if allow_superseded and candidate.state in {"folded", "superseded"}:
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

    async def defer(
        self,
        *,
        presentation_ids: tuple[str, ...],
        session_id: str,
        generation: int,
        error: str | None = None,
        retry_after_seconds: float = 0,
        allow_superseded: bool = False,
    ) -> None:
        now = utc_now()
        async with session_scope() as session:
            for presentation_id in presentation_ids:
                candidate = await session.get(TaskPresentation, presentation_id)
                if candidate is None:
                    continue
                if allow_superseded and candidate.state in {"folded", "superseded"}:
                    continue
                if (
                    candidate.state != "reserved"
                    or candidate.reserved_session_id != session_id
                    or candidate.reserved_generation != generation
                ):
                    raise ValueError("Task presentation reservation is stale")
                candidate.state = "deferred"
                candidate.reserved_session_id = None
                candidate.reserved_generation = None
                candidate.reserved_at = None
                candidate.retry_count += 1
                candidate.defer_until = (
                    now + timedelta(seconds=max(0, retry_after_seconds))
                    if retry_after_seconds > 0
                    else None
                )
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
                candidate.state = "deferred"
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

    @staticmethod
    async def _fold_answered_questions(session, events: list[TaskEvent]) -> None:
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
                candidate.state = "folded"
                candidate.reserved_session_id = None
                candidate.reserved_generation = None

    @staticmethod
    def _facts(event: TaskEvent, task: Task) -> dict:
        payload = dict(event.payload_json or {})
        candidate_type = PRESENTABLE_EVENT_TYPES[event.event_type]
        kind = {
            "progress": "progress",
            "question": "question",
            "terminal_result": "result",
        }[candidate_type]
        coalescing_key = f"task:{task.id}:{candidate_type}"
        return {
            "task_id": task.id,
            "goal": task.goal,
            "event_type": event.event_type,
            **payload,
            "schema": "jarvis.task_update.v1",
            "kind": kind,
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


task_presentation_service = TaskPresentationService()
