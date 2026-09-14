"""Ordered session-event writes and consistent Jarvis domain snapshots."""
from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.tables import (
    Conversation,
    ConversationEvent,
    LiveSession,
    Task,
    TaskEvent,
    LiveSessionEvent,
)


@dataclass(frozen=True)
class DomainSnapshot:
    conversation: Conversation
    tasks: tuple[Task, ...]
    live_session: LiveSession | None
    conversation_cursor: int
    task_cursor: int
    session_cursor: int


@dataclass(frozen=True)
class SessionEventAppendResult:
    event: LiveSessionEvent
    created: bool


class JarvisDomainRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append_session_event(
        self,
        *,
        session_id: str,
        event_type: str,
        payload_json: dict | None = None,
        idempotency_key: str | None = None,
    ) -> LiveSessionEvent:
        return (
            await self.append_session_event_once(
                session_id=session_id,
                event_type=event_type,
                payload_json=payload_json,
                idempotency_key=idempotency_key,
            )
        ).event

    async def append_session_event_once(
        self,
        *,
        session_id: str,
        event_type: str,
        payload_json: dict | None = None,
        idempotency_key: str | None = None,
    ) -> SessionEventAppendResult:
        # A no-op update acquires the same writer lock used for the counter.
        # Checking the idempotency key while holding that lock prevents a
        # concurrent retry from consuming a sequence or creating a gap.
        lock_statement = (
            update(LiveSession)
            .where(LiveSession.id == session_id)
            .values(last_event_sequence=LiveSession.last_event_sequence)
        )
        lock_result = await self.session.execute(lock_statement)
        if lock_result.rowcount != 1:
            raise ValueError(f"Live session not found: {session_id}")

        if idempotency_key is not None:
            existing_result = await self.session.execute(
                select(LiveSessionEvent)
                .where(LiveSessionEvent.session_id == session_id)
                .where(LiveSessionEvent.idempotency_key == idempotency_key)
                .limit(1)
            )
            existing = existing_result.scalar_one_or_none()
            if existing is not None:
                return SessionEventAppendResult(event=existing, created=False)

        counter_statement = (
            update(LiveSession)
            .where(LiveSession.id == session_id)
            .values(last_event_sequence=LiveSession.last_event_sequence + 1)
        )
        await self.session.execute(counter_statement)
        sequence_result = await self.session.execute(
            select(LiveSession.last_event_sequence).where(LiveSession.id == session_id)
        )
        event = LiveSessionEvent(
            id=str(uuid4()),
            session_id=session_id,
            sequence=int(sequence_result.scalar_one()),
            event_type=event_type,
            payload_json=payload_json,
            idempotency_key=idempotency_key,
        )
        self.session.add(event)
        await self.session.flush()
        return SessionEventAppendResult(event=event, created=True)

    async def read_snapshot(
        self,
        *,
        conversation_id: str,
        session_id: str | None = None,
    ) -> DomainSnapshot:
        """Read projections and their committed cursors in one DB snapshot."""
        conversation = await self.session.get(Conversation, conversation_id)
        if conversation is None:
            raise ValueError(f"Conversation not found: {conversation_id}")

        task_result = await self.session.execute(
            select(Task)
            .where(Task.status.in_([
                "queued", "waiting_for_worker", "running", "waiting_for_input", "waiting_until"
            ]))
            .order_by(Task.creation_order.asc())
        )
        tasks = tuple(task_result.scalars().all())

        conversation_cursor_result = await self.session.execute(
            select(func.max(ConversationEvent.sequence)).where(
                ConversationEvent.conversation_id == conversation_id
            )
        )
        task_cursor_result = await self.session.execute(select(func.max(TaskEvent.sequence)))

        live_session = None
        session_cursor = 0
        if session_id is not None:
            live_session = await self.session.get(LiveSession, session_id)
            if live_session is None or live_session.conversation_id != conversation_id:
                raise ValueError(f"Live session not found for conversation: {session_id}")
            session_cursor_result = await self.session.execute(
                select(func.max(LiveSessionEvent.sequence)).where(
                    LiveSessionEvent.session_id == session_id
                )
            )
            session_cursor = int(session_cursor_result.scalar_one_or_none() or 0)

        return DomainSnapshot(
            conversation=conversation,
            tasks=tasks,
            live_session=live_session,
            conversation_cursor=int(conversation_cursor_result.scalar_one_or_none() or 0),
            task_cursor=int(task_cursor_result.scalar_one_or_none() or 0),
            session_cursor=session_cursor,
        )
