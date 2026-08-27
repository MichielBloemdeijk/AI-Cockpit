from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, inspect, select

from app.config import Settings
from app.db.repositories.conversations import ConversationRepository
from app.db.repositories.jarvis import JarvisDomainRepository
from app.db.repositories.tasks import TaskRepository
from app.db.session import get_session_factory, session_scope
from app.db.tables import (
    ConversationEvent,
    RealtimeSession,
    Task,
    TaskEvent,
    VoiceSessionEvent,
)


REQUIRED_JARVIS_TABLES = {
    "jarvis_state",
    "speech_deliveries",
    "realtime_sessions",
    "voice_session_events",
    "tasks",
    "task_attempts",
    "task_events",
    "task_questions",
    "task_artifacts",
    "task_targets",
    "resource_leases",
    "task_presentations",
    "worker_instances",
    "notification_outbox",
}


@pytest.mark.asyncio
async def test_blank_database_has_jarvis_schema_and_locked_constraints() -> None:
    session_factory = get_session_factory()
    engine = session_factory.kw["bind"]

    async with engine.connect() as connection:
        contract = await connection.run_sync(
            lambda sync_connection: {
                "tables": set(inspect(sync_connection).get_table_names()),
                "conversation_columns": {
                    column["name"] for column in inspect(sync_connection).get_columns("conversations")
                },
                "message_columns": {
                    column["name"]
                    for column in inspect(sync_connection).get_columns("conversation_messages")
                },
                "conversation_checks": {
                    constraint["name"]
                    for constraint in inspect(sync_connection).get_check_constraints("conversations")
                },
                "conversation_uniques": {
                    constraint["name"]
                    for constraint in inspect(sync_connection).get_unique_constraints("conversations")
                },
            }
        )

    assert REQUIRED_JARVIS_TABLES <= contract["tables"]
    assert {
        "conversation_kind",
        "lifecycle_status",
        "closed_at",
        "jarvis_day_key",
        "jarvis_day_sequence",
        "last_event_sequence",
    } <= contract["conversation_columns"]
    assert {
        "modality",
        "execution_mode",
        "communication_state",
        "source_task_id",
        "source_task_event_id",
        "presented_in_turn_id",
    } <= contract["message_columns"]
    assert "ck_conversations_kind_day_fields" in contract["conversation_checks"]
    assert "ck_conversations_lifecycle_closed_at" in contract["conversation_checks"]
    assert "uq_conversations_jarvis_day_sequence" in contract["conversation_uniques"]


@pytest.mark.asyncio
async def test_concurrent_writers_allocate_unique_ordered_domain_sequences() -> None:
    async with session_scope() as session:
        conversation = await ConversationRepository(session).create_conversation(title="Sequence test")
        session.add(
            RealtimeSession(
                id="realtime-sequence-test",
                conversation_id=conversation.id,
                generation=1,
                provider_adapter="fake",
                lifecycle_state="ready",
            )
        )
        task = await TaskRepository(session).dispatch_task(
            origin_surface="jarvis",
            goal="Test ordered writes",
            idempotency_key="dispatch:sequence-test",
        )
        conversation_id = conversation.id
        task_id = task.id

    async def append_conversation_event(index: int) -> int:
        async with session_scope() as session:
            event = await ConversationRepository(session).append_event(
                conversation_id=conversation_id,
                run_id=None,
                actor_kind="system",
                event_type="test.event",
                idempotency_key=f"conversation:{index}",
            )
            return event.sequence

    async def append_task_event(index: int) -> int:
        async with session_scope() as session:
            event = await TaskRepository(session).append_event(
                task_id=task_id,
                event_type="test.event",
                idempotency_key=f"task:{index}",
            )
            return event.sequence

    async def append_session_event(index: int) -> int:
        async with session_scope() as session:
            event = await JarvisDomainRepository(session).append_session_event(
                session_id="realtime-sequence-test",
                event_type="test.event",
                idempotency_key=f"session:{index}",
            )
            return event.sequence

    conversation_sequences, task_sequences, session_sequences = await asyncio.gather(
        asyncio.gather(*(append_conversation_event(index) for index in range(12))),
        asyncio.gather(*(append_task_event(index) for index in range(12))),
        asyncio.gather(*(append_session_event(index) for index in range(12))),
    )

    assert sorted(conversation_sequences) == list(range(1, 13))
    assert len(set(task_sequences)) == 12
    assert sorted(session_sequences) == list(range(1, 13))


@pytest.mark.asyncio
async def test_task_dispatch_and_event_append_are_idempotent() -> None:
    async def dispatch() -> tuple[str, int]:
        async with session_scope() as session:
            task = await TaskRepository(session).dispatch_task(
                origin_surface="jarvis",
                goal="Only dispatch once",
                idempotency_key="dispatch:duplicate",
            )
            return task.id, task.creation_order

    dispatch_results = await asyncio.gather(*(dispatch() for _ in range(8)))
    assert len(set(dispatch_results)) == 1
    task_id = dispatch_results[0][0]

    async def append() -> tuple[str, int]:
        async with session_scope() as session:
            event = await TaskRepository(session).append_event(
                task_id=task_id,
                event_type="task.queued",
                idempotency_key="event:duplicate",
            )
            return event.id, event.sequence

    event_results = await asyncio.gather(*(append() for _ in range(8)))
    assert len(set(event_results)) == 1

    async with session_scope() as session:
        task_count = await session.scalar(select(func.count()).select_from(Task))
        event_count = await session.scalar(select(func.count()).select_from(TaskEvent))
    assert task_count == 1
    assert event_count == 1


@pytest.mark.asyncio
async def test_snapshot_cursors_only_reference_committed_rows() -> None:
    async with session_scope() as session:
        conversation = await ConversationRepository(session).create_conversation(title="Snapshot test")
        conversation_event = await ConversationRepository(session).append_event(
            conversation_id=conversation.id,
            run_id=None,
            actor_kind="system",
            event_type="conversation.accepted",
            idempotency_key="snapshot:conversation",
        )
        task = await TaskRepository(session).dispatch_task(
            origin_surface="jarvis",
            origin_conversation_id=conversation.id,
            goal="Snapshot task",
            idempotency_key="snapshot:task",
        )
        task_event = await TaskRepository(session).append_event(
            task_id=task.id,
            event_type="task.queued",
            idempotency_key="snapshot:task-event",
        )
        realtime_session = RealtimeSession(
            id="snapshot-session",
            conversation_id=conversation.id,
            generation=1,
            provider_adapter="fake",
            lifecycle_state="ready",
        )
        session.add(realtime_session)
        await session.flush()
        session_event = await JarvisDomainRepository(session).append_session_event(
            session_id=realtime_session.id,
            event_type="ready",
            idempotency_key="snapshot:session-event",
        )
        conversation_id = conversation.id

    async with session_scope() as session:
        snapshot = await JarvisDomainRepository(session).read_snapshot(
            conversation_id=conversation_id,
            session_id="snapshot-session",
        )
        assert await session.scalar(
            select(func.count()).select_from(ConversationEvent).where(
                ConversationEvent.sequence == snapshot.conversation_cursor,
                ConversationEvent.conversation_id == conversation_id,
            )
        ) == 1
        assert await session.scalar(
            select(func.count()).select_from(TaskEvent).where(TaskEvent.sequence == snapshot.task_cursor)
        ) == 1
        assert await session.scalar(
            select(func.count()).select_from(VoiceSessionEvent).where(
                VoiceSessionEvent.sequence == snapshot.session_cursor,
                VoiceSessionEvent.session_id == "snapshot-session",
            )
        ) == 1

    assert snapshot.conversation_cursor == conversation_event.sequence
    assert snapshot.task_cursor == task_event.sequence
    assert snapshot.session_cursor == session_event.sequence


def test_default_runtime_uses_fresh_jarvis_database() -> None:
    assert Settings.model_fields["database_url"].default == (
        "sqlite+aiosqlite:///./data/ai_cockpit_jarvis.db"
    )
