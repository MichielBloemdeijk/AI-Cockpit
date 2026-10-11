"""Repositories for the global ordered task domain."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.conversations import utc_now
from app.db.tables import Task, TaskEvent, TaskStatus
from app.services.task_update_policy import CRITICAL_ONLY


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def dispatch_task(
        self,
        *,
        origin_surface: str,
        goal: str,
        idempotency_key: str,
        origin_conversation_id: str | None = None,
        origin_turn_id: str | None = None,
        importance: str = "normal",
        presentation_policy: str = CRITICAL_ONLY,
    ) -> Task:
        """Create a task once and return the original task on retries."""
        task_id = str(uuid4())
        statement = (
            sqlite_insert(Task)
            .values(
                id=task_id,
                origin_surface=origin_surface,
                origin_conversation_id=origin_conversation_id,
                origin_turn_id=origin_turn_id,
                goal=goal,
                status=TaskStatus.queued.value,
                idempotency_key=idempotency_key,
                importance=importance,
                presentation_policy=presentation_policy,
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
        )
        await self.session.execute(statement)
        result = await self.session.execute(
            select(Task).where(Task.idempotency_key == idempotency_key).limit(1)
        )
        return result.scalar_one()

    async def get_task(self, task_id: str) -> Task | None:
        result = await self.session.execute(select(Task).where(Task.id == task_id).limit(1))
        return result.scalar_one_or_none()

    async def list_tasks(self, *, statuses: set[str] | None = None) -> Sequence[Task]:
        statement = select(Task)
        if statuses:
            statement = statement.where(Task.status.in_(sorted(statuses)))
        statement = statement.order_by(Task.creation_order.asc())
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def append_event(
        self,
        *,
        task_id: str,
        event_type: str,
        idempotency_key: str,
        attempt_id: str | None = None,
        execution_run_id: str | None = None,
        native_item_id: str | None = None,
        payload_json: dict | None = None,
        causality_json: dict | None = None,
    ) -> TaskEvent:
        """Append once to the global task stream.

        ``task_events.sequence`` is an SQLite INTEGER PRIMARY KEY. Its value is
        allocated transactionally, while the conflict target guarantees that
        a provider or browser retry returns the already-committed event.
        """
        event_id = str(uuid4())
        statement = (
            sqlite_insert(TaskEvent)
            .values(
                id=event_id,
                task_id=task_id,
                attempt_id=attempt_id,
                execution_run_id=execution_run_id,
                native_item_id=native_item_id,
                event_type=event_type,
                payload_json=payload_json,
                causality_json=causality_json,
                idempotency_key=idempotency_key,
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
        )
        await self.session.execute(statement)
        result = await self.session.execute(
            select(TaskEvent).where(TaskEvent.idempotency_key == idempotency_key).limit(1)
        )
        self.session.info["task_feedback_committed"] = True
        event = result.scalar_one()
        from app.services.task_results import project_task_event
        await project_task_event(self.session, event)
        return event

    async def transition_with_event(
        self,
        *,
        task_id: str,
        expected_statuses: set[str],
        status: str,
        event_type: str,
        idempotency_key: str,
        attempt_id: str | None = None,
        payload_json: dict | None = None,
        terminal_summary: str | None = None,
        finished_at: datetime | None = None,
    ) -> TaskEvent:
        existing_result = await self.session.execute(
            select(TaskEvent).where(TaskEvent.idempotency_key == idempotency_key).limit(1)
        )
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            return existing

        values: dict[str, object] = {"status": status, "updated_at": utc_now()}
        if terminal_summary is not None:
            values["terminal_summary"] = terminal_summary
        if finished_at is not None:
            values["finished_at"] = finished_at
        statement = (
            update(Task)
            .where(Task.id == task_id)
            .where(Task.status.in_(sorted(expected_statuses)))
            .values(**values)
        )
        result = await self.session.execute(statement)
        if result.rowcount != 1:
            raise ValueError(f"Task {task_id} is not in an expected state")
        return await self.append_event(
            task_id=task_id,
            attempt_id=attempt_id,
            event_type=event_type,
            payload_json=payload_json,
            idempotency_key=idempotency_key,
        )

    async def get_latest_event_sequence(self) -> int:
        result = await self.session.execute(select(func.max(TaskEvent.sequence)))
        return int(result.scalar_one_or_none() or 0)

    async def list_events_after(self, *, after_sequence: int = 0, limit: int = 250) -> Sequence[TaskEvent]:
        statement = (
            select(TaskEvent)
            .where(TaskEvent.sequence > max(0, int(after_sequence)))
            .order_by(TaskEvent.sequence.asc())
            .limit(max(1, min(int(limit), 1000)))
        )
        result = await self.session.execute(statement)
        return result.scalars().all()
