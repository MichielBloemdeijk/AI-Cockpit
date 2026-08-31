"""Authoritative lifecycle for global tasks and their ordered trace."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.repositories.tasks import TaskRepository
from app.db.session import session_scope
from app.db.tables import (
    Task,
    TaskArtifact,
    TaskAttempt,
    TaskEvent,
    TaskPresentation,
    TaskQuestion,
    TaskTarget,
    WorkerInstance,
)
from app.services.task_notifications import enqueue_task_notification, schedule_task_notification


ACTIVE_TASK_STATUSES = {"queued", "running", "waiting_for_input", "waiting_until"}
TERMINAL_TASK_STATUSES = {"completed", "failed", "interrupted", "cancelled"}
RETRYABLE_TASK_STATUSES = {"failed", "interrupted", "cancelled"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    task: Task
    queue_position: int | None


@dataclass(frozen=True, slots=True)
class WorkerHealth:
    status: str
    worker_id: str | None
    heartbeat_at: datetime | None


@dataclass(frozen=True, slots=True)
class TaskDetailSnapshot:
    task: Task
    queue_position: int | None
    attempts: tuple[TaskAttempt, ...]
    events: tuple[TaskEvent, ...]
    questions: tuple[TaskQuestion, ...]
    artifacts: tuple[TaskArtifact, ...]
    targets: tuple[TaskTarget, ...]
    presentations: tuple[TaskPresentation, ...]
    worker_health: WorkerHealth


class TaskService:
    async def schedule_reminder(
        self,
        *,
        text: str,
        due_at: datetime,
        idempotency_key: str,
        origin_conversation_id: str | None = None,
    ) -> TaskSnapshot:
        normalized = " ".join(text.split()).strip()
        due = due_at if due_at.tzinfo is not None else due_at.replace(tzinfo=timezone.utc)
        due = due.astimezone(timezone.utc)
        if not normalized:
            raise ValueError("Reminder text cannot be empty")
        if due <= utc_now():
            raise ValueError("Reminder time must be in the future")
        async with session_scope() as session:
            repo = TaskRepository(session)
            task = await repo.dispatch_task(
                origin_surface="jarvis_reminder",
                goal=f"Remind me: {normalized}",
                idempotency_key=idempotency_key,
                origin_conversation_id=origin_conversation_id,
                importance="high",
                presentation_policy="natural",
            )
            task = await self._lock_task(session, task.id)
            attempt = await self._ensure_attempt(session, task)
            await repo.append_event(
                task_id=task.id,
                attempt_id=attempt.id,
                event_type="task.queued",
                payload_json={"goal": task.goal, "status": "queued"},
                idempotency_key=f"task.dispatch:{idempotency_key}",
            )
            if task.status == "queued":
                task.status = "waiting_until"
                task.waiting_until = due
                task.resume_at = due
                task.wait_reason = "reminder"
                task.scheduled_delivery_json = {"kind": "reminder", "text": normalized}
                task.scheduled_delivery_key = f"reminder:{idempotency_key}"
                await repo.append_event(
                    task_id=task.id,
                    attempt_id=attempt.id,
                    event_type="task.waiting_until",
                    payload_json={"status": "waiting_until", "resume_at": due.isoformat(), "reason": "reminder"},
                    idempotency_key=f"task.waiting:{idempotency_key}",
                )
                await self._end_current_attempt(session, task, reason="waiting_until")
            await session.flush()
            return TaskSnapshot(task=task, queue_position=None)

    async def wait_until(
        self,
        task_id: str,
        *,
        resume_at: datetime,
        reason: str,
        idempotency_key: str,
    ) -> TaskSnapshot:
        due = resume_at if resume_at.tzinfo is not None else resume_at.replace(tzinfo=timezone.utc)
        due = due.astimezone(timezone.utc)
        if due <= utc_now():
            raise ValueError("Task wait time must be in the future")
        normalized_reason = " ".join(reason.split()).strip() or "scheduled wait"
        async with session_scope() as session:
            repo = TaskRepository(session)
            task = await self._lock_task(session, task_id)
            event_key = f"task.wait-until:{task_id}:{idempotency_key}"
            if await self._event_by_key(session, event_key) is None:
                if task.status not in {"queued", "running"}:
                    raise ValueError(f"Task in {task.status} cannot wait until a scheduled time")
                task.status = "waiting_until"
                task.waiting_until = due
                task.resume_at = due
                task.wait_reason = normalized_reason
                task.updated_at = utc_now()
                await repo.append_event(
                    task_id=task.id,
                    attempt_id=task.current_attempt_id,
                    event_type="task.waiting_until",
                    payload_json={
                        "status": "waiting_until",
                        "resume_at": due.isoformat(),
                        "reason": normalized_reason,
                    },
                    idempotency_key=event_key,
                )
                await self._end_current_attempt(session, task, reason="waiting_until")
            await session.flush()
            return TaskSnapshot(task=task, queue_position=None)

    async def dispatch(
        self,
        *,
        goal: str,
        idempotency_key: str,
        origin_surface: str,
        origin_conversation_id: str | None = None,
        origin_turn_id: str | None = None,
        importance: str = "normal",
        presentation_policy: str = "natural",
        targets: tuple[tuple[str, str, bool], ...] = (),
    ) -> TaskSnapshot:
        normalized_goal = " ".join(goal.split()).strip()
        if not normalized_goal:
            raise ValueError("Task goal cannot be empty")
        async with session_scope() as session:
            repo = TaskRepository(session)
            task = await repo.dispatch_task(
                origin_surface=origin_surface,
                goal=normalized_goal,
                idempotency_key=idempotency_key,
                origin_conversation_id=origin_conversation_id,
                origin_turn_id=origin_turn_id,
                importance=importance,
                presentation_policy=presentation_policy,
            )
            task = await self._lock_task(session, task.id)
            await self._ensure_targets(session, task.id, targets)
            attempt = await self._ensure_attempt(session, task)
            await repo.append_event(
                task_id=task.id,
                attempt_id=attempt.id,
                event_type="task.queued",
                payload_json={"goal": task.goal, "status": task.status},
                idempotency_key=f"task.dispatch:{idempotency_key}",
            )
            await session.refresh(task)
            return TaskSnapshot(task=task, queue_position=await self._queue_position(session, task))

    async def list_tasks(self, *, statuses: set[str] | None = None) -> list[TaskSnapshot]:
        async with session_scope() as session:
            tasks = list(await TaskRepository(session).list_tasks(statuses=statuses))
            return [
                TaskSnapshot(task=task, queue_position=await self._queue_position(session, task))
                for task in tasks
            ]

    async def get_task(self, task_id: str) -> TaskDetailSnapshot | None:
        async with session_scope() as session:
            repo = TaskRepository(session)
            task = await repo.get_task(task_id)
            if task is None:
                return None
            attempts = tuple((await session.execute(
                select(TaskAttempt)
                .where(TaskAttempt.task_id == task_id)
                .order_by(TaskAttempt.attempt_number.asc())
            )).scalars().all())
            events = tuple((await session.execute(
                select(TaskEvent)
                .where(TaskEvent.task_id == task_id)
                .order_by(TaskEvent.sequence.asc())
            )).scalars().all())
            questions = tuple((await session.execute(
                select(TaskQuestion)
                .where(TaskQuestion.task_id == task_id)
                .order_by(TaskQuestion.asked_at.asc(), TaskQuestion.id.asc())
            )).scalars().all())
            artifacts = tuple((await session.execute(
                select(TaskArtifact)
                .where(TaskArtifact.task_id == task_id)
                .order_by(TaskArtifact.created_at.asc(), TaskArtifact.id.asc())
            )).scalars().all())
            targets = tuple((await session.execute(
                select(TaskTarget)
                .where(TaskTarget.task_id == task_id)
                .order_by(TaskTarget.resource_type.asc(), TaskTarget.resource_key.asc())
            )).scalars().all())
            presentations = tuple((await session.execute(
                select(TaskPresentation)
                .where(TaskPresentation.task_id == task_id)
                .order_by(TaskPresentation.created_at.asc(), TaskPresentation.id.asc())
            )).scalars().all())
            return TaskDetailSnapshot(
                task=task,
                queue_position=await self._queue_position(session, task),
                attempts=attempts,
                events=events,
                questions=questions,
                artifacts=artifacts,
                targets=targets,
                presentations=presentations,
                worker_health=await self._worker_health(session),
            )

    async def replay_events(self, *, after_sequence: int = 0, limit: int = 250) -> tuple[int, list[TaskEvent]]:
        async with session_scope() as session:
            repo = TaskRepository(session)
            events = list(await repo.list_events_after(after_sequence=after_sequence, limit=limit))
            return await repo.get_latest_event_sequence(), events

    async def record_event(
        self,
        task_id: str,
        *,
        event_type: str,
        payload: dict | None,
        idempotency_key: str,
        attempt_id: str | None = None,
    ) -> TaskEvent:
        async with session_scope() as session:
            task = await self._lock_task(session, task_id)
            return await TaskRepository(session).append_event(
                task_id=task.id,
                attempt_id=attempt_id or task.current_attempt_id,
                event_type=event_type,
                payload_json=payload,
                idempotency_key=idempotency_key,
            )

    async def save_checkpoint(self, task_id: str, *, checkpoint: dict) -> None:
        async with session_scope() as session:
            task = await self._lock_task(session, task_id)
            attempt = await self._current_attempt(session, task)
            if attempt is None or attempt.ended_at is not None:
                raise ValueError("Task has no active attempt")
            attempt.checkpoint_json = checkpoint
            attempt.heartbeat_at = utc_now()
            await session.flush()

    async def cancellation_requested(self, task_id: str) -> bool:
        async with session_scope() as session:
            task = await TaskRepository(session).get_task(task_id)
            return bool(
                task is not None
                and task.status == "running"
                and task.cancellation_requested_at is not None
            )

    async def worker_health(self) -> WorkerHealth:
        async with session_scope() as session:
            return await self._worker_health(session)

    async def cancel(self, task_id: str, *, idempotency_key: str) -> TaskSnapshot:
        outbox_id: str | None = None
        async with session_scope() as session:
            repo = TaskRepository(session)
            task = await self._lock_task(session, task_id)
            existing = await self._event_by_key(session, f"task.cancel:{task_id}:{idempotency_key}")
            if existing is None and task.status == "running":
                now = utc_now()
                task.cancellation_requested_at = now
                task.updated_at = now
                await repo.append_event(
                    task_id=task.id,
                    attempt_id=task.current_attempt_id,
                    event_type="task.cancellation_requested",
                    payload_json={"status": "running"},
                    idempotency_key=f"task.cancel:{task_id}:{idempotency_key}",
                )
            elif existing is None and task.status in {"queued", "waiting_for_input", "waiting_until"}:
                now = utc_now()
                task.status = "cancelled"
                task.cancellation_requested_at = now
                task.finished_at = now
                task.updated_at = now
                task.terminal_summary = "Task cancelled by user."
                task.waiting_until = None
                task.resume_at = None
                event = await repo.append_event(
                    task_id=task.id,
                    attempt_id=task.current_attempt_id,
                    event_type="task.cancelled",
                    payload_json={"status": "cancelled", "summary": task.terminal_summary},
                    idempotency_key=f"task.cancel:{task_id}:{idempotency_key}",
                )
                outbox_id = await enqueue_task_notification(session, task=task, event=event)
                await self._end_current_attempt(session, task, reason="cancelled")
            await session.flush()
            snapshot = TaskSnapshot(task=task, queue_position=await self._queue_position(session, task))
        if outbox_id is not None:
            await schedule_task_notification(outbox_id)
        return snapshot

    async def retry(self, task_id: str, *, idempotency_key: str) -> TaskSnapshot:
        async with session_scope() as session:
            repo = TaskRepository(session)
            task = await self._lock_task(session, task_id)
            event_key = f"task.retry:{task_id}:{idempotency_key}"
            existing = await self._event_by_key(session, event_key)
            if existing is None:
                if task.status not in RETRYABLE_TASK_STATUSES:
                    raise ValueError(f"Task in {task.status} cannot be retried")
                attempt = await self._create_attempt(session, task)
                task.status = "queued"
                task.current_attempt_id = attempt.id
                task.cancellation_requested_at = None
                task.terminal_summary = None
                task.finished_at = None
                task.updated_at = utc_now()
                await repo.append_event(
                    task_id=task.id,
                    attempt_id=attempt.id,
                    event_type="task.retried",
                    payload_json={"status": "queued", "attempt_number": attempt.attempt_number},
                    idempotency_key=event_key,
                )
            await session.flush()
            return TaskSnapshot(task=task, queue_position=await self._queue_position(session, task))

    async def ask_question(
        self,
        task_id: str,
        *,
        question: str,
        idempotency_key: str,
    ) -> TaskQuestion:
        normalized = " ".join(question.split()).strip()
        if not normalized:
            raise ValueError("Task question cannot be empty")
        outbox_id: str | None = None
        async with session_scope() as session:
            repo = TaskRepository(session)
            task = await self._lock_task(session, task_id)
            event_key = f"task.question:{task_id}:{idempotency_key}"
            existing = await self._event_by_key(session, event_key)
            if existing is not None:
                question_id = str((existing.payload_json or {}).get("question_id") or "")
                saved = await session.get(TaskQuestion, question_id)
                if saved is not None:
                    return saved
            if task.status not in {"queued", "running"}:
                raise ValueError(f"Task in {task.status} cannot ask a question")
            question_row = TaskQuestion(
                id=str(uuid4()),
                task_id=task.id,
                asked_attempt_id=task.current_attempt_id,
                question=normalized,
                state="asked",
            )
            session.add(question_row)
            await session.flush()
            event = await repo.append_event(
                task_id=task.id,
                attempt_id=task.current_attempt_id,
                event_type="task.requires_input",
                payload_json={"question_id": question_row.id, "question": normalized},
                idempotency_key=event_key,
            )
            question_row.source_task_event_id = event.id
            task.status = "waiting_for_input"
            task.updated_at = utc_now()
            await self._end_current_attempt(session, task, reason="waiting_for_input")
            outbox_id = await enqueue_task_notification(
                session,
                task=task,
                event=event,
                question=question_row,
            )
            await session.flush()
            saved_question = question_row
        if outbox_id is not None:
            await schedule_task_notification(outbox_id)
        return saved_question

    async def answer_question(
        self,
        task_id: str,
        question_id: str,
        *,
        answer: str,
        idempotency_key: str,
    ) -> TaskSnapshot:
        normalized = " ".join(answer.split()).strip()
        if not normalized:
            raise ValueError("Task answer cannot be empty")
        async with session_scope() as session:
            repo = TaskRepository(session)
            task = await self._lock_task(session, task_id)
            event_key = f"task.answer:{task_id}:{question_id}:{idempotency_key}"
            if await self._event_by_key(session, event_key) is None:
                question = await session.get(TaskQuestion, question_id)
                if question is None or question.task_id != task_id:
                    raise ValueError("Task question not found")
                if question.state != "asked" or question.answered_at is not None:
                    raise ValueError("Task question has already been answered")
                if task.status != "waiting_for_input":
                    raise ValueError(f"Task in {task.status} is not waiting for input")
                attempt = await self._create_attempt(session, task)
                now = utc_now()
                question.answer = normalized
                question.state = "answered"
                question.answered_at = now
                question.answered_attempt_id = attempt.id
                task.status = "queued"
                task.current_attempt_id = attempt.id
                task.updated_at = now
                await repo.append_event(
                    task_id=task.id,
                    attempt_id=attempt.id,
                    event_type="task.question.answered",
                    payload_json={
                        "question_id": question.id,
                        "answer": normalized,
                        "attempt_number": attempt.attempt_number,
                    },
                    idempotency_key=event_key,
                )
            await session.flush()
            return TaskSnapshot(task=task, queue_position=await self._queue_position(session, task))

    async def publish_progress(
        self,
        task_id: str,
        *,
        summary: str,
        idempotency_key: str,
    ) -> TaskSnapshot:
        normalized = " ".join(summary.split()).strip()
        if not normalized:
            raise ValueError("Task progress cannot be empty")
        async with session_scope() as session:
            repo = TaskRepository(session)
            task = await self._lock_task(session, task_id)
            event_key = f"task.progress:{task_id}:{idempotency_key}"
            if await self._event_by_key(session, event_key) is not None:
                return TaskSnapshot(task=task, queue_position=await self._queue_position(session, task))
            if task.status not in {"queued", "running"}:
                raise ValueError(f"Task in {task.status} cannot publish progress")
            task.status = "running"
            task.updated_at = utc_now()
            attempt = await self._current_attempt(session, task)
            if attempt is not None and attempt.started_at is None:
                attempt.started_at = utc_now()
            await repo.append_event(
                task_id=task.id,
                attempt_id=task.current_attempt_id,
                event_type="task.progress",
                payload_json={"status": "running", "summary": normalized},
                idempotency_key=event_key,
            )
            return TaskSnapshot(task=task, queue_position=None)

    async def finish_cancellation(self, task_id: str, *, idempotency_key: str) -> TaskSnapshot:
        """Record that a running attempt observed a requested cancellation."""
        outbox_id: str | None = None
        async with session_scope() as session:
            repo = TaskRepository(session)
            task = await self._lock_task(session, task_id)
            event_key = f"task.cancelled:{task_id}:{idempotency_key}"
            if await self._event_by_key(session, event_key) is None:
                if task.status != "running" or task.cancellation_requested_at is None:
                    raise ValueError("Task does not have a running cancellation request")
                now = utc_now()
                task.status = "cancelled"
                task.finished_at = now
                task.updated_at = now
                task.terminal_summary = "Task cancelled by user."
                event = await repo.append_event(
                    task_id=task.id,
                    attempt_id=task.current_attempt_id,
                    event_type="task.cancelled",
                    payload_json={"status": "cancelled", "summary": task.terminal_summary},
                    idempotency_key=event_key,
                )
                outbox_id = await enqueue_task_notification(session, task=task, event=event)
                await self._end_current_attempt(session, task, reason="cancelled")
            await session.flush()
            snapshot = TaskSnapshot(task=task, queue_position=None)
        if outbox_id is not None:
            await schedule_task_notification(outbox_id)
        return snapshot

    async def complete(
        self,
        task_id: str,
        *,
        summary: str,
        idempotency_key: str,
        status: str = "completed",
    ) -> TaskSnapshot:
        if status not in {"completed", "failed", "interrupted"}:
            raise ValueError(f"Invalid terminal task status: {status}")
        normalized = " ".join(summary.split()).strip()
        outbox_id: str | None = None
        async with session_scope() as session:
            repo = TaskRepository(session)
            task = await self._lock_task(session, task_id)
            event_key = f"task.terminal:{task_id}:{idempotency_key}"
            if await self._event_by_key(session, event_key) is None:
                if task.status not in ACTIVE_TASK_STATUSES:
                    raise ValueError(f"Task in {task.status} cannot complete")
                now = utc_now()
                task.status = status
                task.terminal_summary = normalized or None
                task.finished_at = now
                task.updated_at = now
                event = await repo.append_event(
                    task_id=task.id,
                    attempt_id=task.current_attempt_id,
                    event_type=f"task.{status}",
                    payload_json={"status": status, "summary": normalized},
                    idempotency_key=event_key,
                )
                outbox_id = await enqueue_task_notification(session, task=task, event=event)
                await self._end_current_attempt(session, task, reason=status)
            await session.flush()
            snapshot = TaskSnapshot(task=task, queue_position=None)
        if outbox_id is not None:
            await schedule_task_notification(outbox_id)
        return snapshot

    async def add_artifact(
        self,
        task_id: str,
        *,
        artifact_type: str,
        mime_type: str,
        idempotency_key: str,
        title: str | None = None,
        uri: str | None = None,
        content_text: str | None = None,
        content_json: dict | None = None,
    ) -> TaskArtifact:
        async with session_scope() as session:
            repo = TaskRepository(session)
            task = await self._lock_task(session, task_id)
            event_key = f"task.artifact:{task_id}:{idempotency_key}"
            existing = await self._event_by_key(session, event_key)
            if existing is not None:
                artifact_id = str((existing.payload_json or {}).get("artifact_id") or "")
                artifact = await session.get(TaskArtifact, artifact_id)
                if artifact is not None:
                    return artifact
            artifact = TaskArtifact(
                id=str(uuid4()),
                task_id=task.id,
                attempt_id=task.current_attempt_id,
                artifact_type=artifact_type,
                mime_type=mime_type,
                title=title,
                uri=uri,
                content_text=content_text,
                content_json=content_json,
            )
            session.add(artifact)
            await session.flush()
            event = await repo.append_event(
                task_id=task.id,
                attempt_id=task.current_attempt_id,
                event_type="task.artifact.created",
                payload_json={"artifact_id": artifact.id, "artifact_type": artifact_type, "title": title},
                idempotency_key=event_key,
            )
            artifact.source_task_event_id = event.id
            await session.flush()
            return artifact

    async def enqueue_presentation(
        self,
        task_id: str,
        *,
        candidate_type: str,
        coalescing_key: str,
        idempotency_key: str,
        importance: str = "normal",
        source_task_event_id: str | None = None,
    ) -> TaskPresentation:
        async with session_scope() as session:
            await self._lock_task(session, task_id)
            await session.execute(
                sqlite_insert(TaskPresentation)
                .values(
                    id=str(uuid4()),
                    task_id=task_id,
                    candidate_type=candidate_type,
                    coalescing_key=coalescing_key,
                    importance=importance,
                    state="pending",
                    source_task_event_id=source_task_event_id,
                    idempotency_key=idempotency_key,
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
            )
            result = await session.execute(
                select(TaskPresentation)
                .where(TaskPresentation.idempotency_key == idempotency_key)
                .limit(1)
            )
            return result.scalar_one()

    async def attach_runtime_reference(self, task_id: str, runtime_reference: str) -> None:
        """Link the current attempt to a temporary or durable execution runtime."""
        async with session_scope() as session:
            task = await self._lock_task(session, task_id)
            attempt = await self._current_attempt(session, task)
            if attempt is None:
                raise ValueError("Task has no current attempt")
            attempt.agent_runtime_reference = runtime_reference
            await session.flush()

    async def _lock_task(self, session: AsyncSession, task_id: str) -> Task:
        result = await session.execute(
            update(Task).where(Task.id == task_id).values(status=Task.status)
        )
        if result.rowcount != 1:
            raise ValueError(f"Task not found: {task_id}")
        task_result = await session.execute(select(Task).where(Task.id == task_id).limit(1))
        return task_result.scalar_one()

    async def _ensure_targets(
        self,
        session: AsyncSession,
        task_id: str,
        targets: tuple[tuple[str, str, bool], ...],
    ) -> None:
        normalized: dict[tuple[str, str], bool] = {}
        for resource_type, resource_key, mutable in targets:
            target_type = resource_type.strip()
            target_key = resource_key.strip()
            if not target_type or not target_key:
                raise ValueError("Task targets require a resource type and key")
            normalized[(target_type, target_key)] = bool(mutable)
        for (resource_type, resource_key), mutable in sorted(normalized.items()):
            await session.execute(
                sqlite_insert(TaskTarget)
                .values(
                    id=str(uuid4()),
                    task_id=task_id,
                    resource_type=resource_type,
                    resource_key=resource_key,
                    mutable=mutable,
                )
                .on_conflict_do_nothing(
                    index_elements=["task_id", "resource_type", "resource_key"]
                )
            )

    async def _ensure_attempt(self, session: AsyncSession, task: Task) -> TaskAttempt:
        result = await session.execute(
            select(TaskAttempt)
            .where(TaskAttempt.task_id == task.id)
            .order_by(TaskAttempt.attempt_number.asc())
            .limit(1)
        )
        attempt = result.scalar_one_or_none()
        if attempt is None:
            attempt = await self._create_attempt(session, task)
        if task.current_attempt_id != attempt.id:
            task.current_attempt_id = attempt.id
        return attempt

    async def _create_attempt(self, session: AsyncSession, task: Task) -> TaskAttempt:
        result = await session.execute(
            select(func.max(TaskAttempt.attempt_number)).where(TaskAttempt.task_id == task.id)
        )
        attempt_number = int(result.scalar_one_or_none() or 0) + 1
        attempt = TaskAttempt(
            id=str(uuid4()),
            task_id=task.id,
            attempt_number=attempt_number,
        )
        session.add(attempt)
        await session.flush()
        task.current_attempt_id = attempt.id
        return attempt

    async def _current_attempt(self, session: AsyncSession, task: Task) -> TaskAttempt | None:
        if task.current_attempt_id is None:
            return None
        return await session.get(TaskAttempt, task.current_attempt_id)

    async def _end_current_attempt(self, session: AsyncSession, task: Task, *, reason: str) -> None:
        attempt = await self._current_attempt(session, task)
        if attempt is not None and attempt.ended_at is None:
            attempt.ended_at = utc_now()
            attempt.terminal_reason = reason

    async def _event_by_key(self, session: AsyncSession, idempotency_key: str) -> TaskEvent | None:
        result = await session.execute(
            select(TaskEvent).where(TaskEvent.idempotency_key == idempotency_key).limit(1)
        )
        return result.scalar_one_or_none()

    async def _queue_position(self, session: AsyncSession, task: Task) -> int | None:
        if task.status != "queued":
            return None
        result = await session.execute(
            select(func.count())
            .select_from(Task)
            .where(Task.status == "queued", Task.creation_order <= task.creation_order)
        )
        return int(result.scalar_one())

    async def _worker_health(self, session: AsyncSession) -> WorkerHealth:
        result = await session.execute(
            select(WorkerInstance)
            .where(
                WorkerInstance.stopped_at.is_(None),
                WorkerInstance.state.in_(["starting", "idle", "busy"]),
            )
            .order_by(WorkerInstance.heartbeat_at.desc())
            .limit(1)
        )
        worker = result.scalar_one_or_none()
        if worker is None:
            return WorkerHealth(status="offline", worker_id=None, heartbeat_at=None)
        heartbeat = worker.heartbeat_at
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=timezone.utc)
        status = (
            "online"
            if heartbeat >= utc_now() - timedelta(seconds=settings.worker_stale_after_seconds)
            and worker.stopped_at is None
            else "offline"
        )
        return WorkerHealth(status=status, worker_id=worker.id, heartbeat_at=worker.heartbeat_at)


task_service = TaskService()
