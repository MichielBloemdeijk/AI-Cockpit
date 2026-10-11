from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.repositories.tasks import TaskRepository
from app.db.session import session_scope
from app.main import app
from app.services.conversation_store import conversation_store
from app.services.jarvis_daily import jarvis_daily_service, operational_day_key


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        (datetime(2026, 3, 29, 1, 59, 59, tzinfo=timezone.utc), "2026-03-28"),
        (datetime(2026, 3, 29, 2, 0, 0, tzinfo=timezone.utc), "2026-03-29"),
        (datetime(2026, 10, 25, 2, 59, 59, tzinfo=timezone.utc), "2026-10-24"),
        (datetime(2026, 10, 25, 3, 0, 0, tzinfo=timezone.utc), "2026-10-25"),
    ],
)
def test_operational_day_uses_amsterdam_0400_across_dst(timestamp: datetime, expected: str) -> None:
    assert operational_day_key(timestamp).isoformat() == expected


@pytest.mark.asyncio
async def test_page_load_does_not_create_an_empty_log() -> None:
    state = await jarvis_daily_service.get_state(
        now=datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    )

    assert state.current_log is None
    assert state.state_revision == 0
    assert await conversation_store.list_conversations(conversation_kind="jarvis_daily") == []


@pytest.mark.asyncio
async def test_explicit_fresh_start_creates_the_first_log_on_a_new_database() -> None:
    fresh = await jarvis_daily_service.start_fresh_log(
        requested_at=datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc),
        idempotency_key="first-fresh-log",
    )

    assert fresh.title == "Jarvis — 2026-08-27"
    assert fresh.jarvis_day_sequence == 1
    assert fresh.updated_at is not None
    state = await jarvis_daily_service.get_state(
        now=datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    )
    assert state.current_log is not None
    assert state.current_log.id == fresh.id


@pytest.mark.asyncio
async def test_first_text_turn_lazily_creates_daily_log_with_truth_metadata() -> None:
    accepted = await jarvis_daily_service.accept_text_turn(
        "Jarvis, status check",
        accepted_at=datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc),
        idempotency_key="first-turn",
    )

    assert accepted.conversation.conversation_kind == "jarvis_daily"
    assert accepted.conversation.lifecycle_status == "open"
    assert accepted.conversation.jarvis_day_key.isoformat() == "2026-08-27"
    assert accepted.conversation.jarvis_day_sequence == 1
    assert accepted.conversation.title == "Jarvis — 2026-08-27"
    assert accepted.message.modality == "text"
    assert accepted.message.execution_mode == "jarvis_shell"
    assert accepted.message.communication_state == "completed"

    conversations = await conversation_store.list_conversations(conversation_kind="jarvis_daily")
    assert [conversation.id for conversation in conversations] == [accepted.conversation.id]


@pytest.mark.asyncio
async def test_competing_first_turns_create_one_current_log_and_unique_turns() -> None:
    timestamp = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)

    accepted = await asyncio.gather(
        *(
            jarvis_daily_service.accept_text_turn(
                f"Turn {index}",
                accepted_at=timestamp,
                idempotency_key=f"competing-{index}",
            )
            for index in range(8)
        )
    )

    assert len({item.conversation.id for item in accepted}) == 1
    assert len({item.message.id for item in accepted}) == 8
    conversations = await conversation_store.list_conversations(conversation_kind="jarvis_daily")
    assert len(conversations) == 1


@pytest.mark.asyncio
async def test_active_exchange_defers_rollover_until_next_idle_activation() -> None:
    first = await jarvis_daily_service.accept_text_turn(
        "Before boundary",
        accepted_at=datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc),
        idempotency_key="before-boundary",
    )
    crossing = await jarvis_daily_service.accept_text_turn(
        "Same exchange after boundary",
        accepted_at=datetime(2026, 8, 27, 2, 30, tzinfo=timezone.utc),
        idempotency_key="crossing-boundary",
        exchange_active=True,
    )
    after_idle = await jarvis_daily_service.accept_text_turn(
        "Next idle activation",
        accepted_at=datetime(2026, 8, 27, 2, 31, tzinfo=timezone.utc),
        idempotency_key="after-idle",
        exchange_active=False,
    )

    assert crossing.conversation.id == first.conversation.id
    assert after_idle.conversation.id != first.conversation.id
    old_log = await conversation_store.get_conversation(first.conversation.id)
    assert old_log is not None
    assert old_log.lifecycle_status == "closed"
    assert old_log.closed_at is not None
    assert after_idle.conversation.jarvis_day_key.isoformat() == "2026-08-27"
    assert after_idle.conversation.jarvis_day_sequence == 1


@pytest.mark.asyncio
async def test_explicit_fresh_start_acknowledges_old_log_and_is_idempotent() -> None:
    original = await jarvis_daily_service.accept_text_turn(
        "Keep this in the first log",
        accepted_at=datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc),
        idempotency_key="original",
    )
    fresh = await jarvis_daily_service.start_fresh_log(
        requested_at=datetime(2026, 8, 27, 12, 5, tzinfo=timezone.utc),
        idempotency_key="fresh-request",
    )
    retried = await jarvis_daily_service.start_fresh_log(
        requested_at=datetime(2026, 8, 27, 12, 5, tzinfo=timezone.utc),
        idempotency_key="fresh-request",
    )

    assert retried.id == fresh.id
    assert fresh.jarvis_day_sequence == 2
    assert fresh.title == "Jarvis — 2026-08-27 (2)"
    old_log = await conversation_store.get_conversation(original.conversation.id)
    assert old_log is not None and old_log.lifecycle_status == "closed"
    old_messages = await conversation_store.list_messages(original.conversation.id, final_only=True)
    assert old_messages[-1].content == "Starting a fresh Jarvis log."
    with pytest.raises(ValueError, match="Closed Jarvis log is read-only"):
        await conversation_store.append_event(
            original.conversation.id,
            run_id=None,
            actor_kind="system",
            event_type="must.reject",
        )
    with pytest.raises(ValueError, match="Closed Jarvis log is read-only"):
        await conversation_store.create_branch(
            original.conversation.id,
            parent_branch_key="main",
            branched_from_message_id=original.message.id,
        )

    # An empty new current log stays out of main history until its first turn.
    visible = await conversation_store.list_conversations(conversation_kind="jarvis_daily")
    assert [item.id for item in visible] == [original.conversation.id]
    await jarvis_daily_service.accept_text_turn(
        "First turn in sequence two",
        accepted_at=datetime(2026, 8, 27, 12, 6, tzinfo=timezone.utc),
        idempotency_key="sequence-two-first",
    )
    visible = await conversation_store.list_conversations(conversation_kind="jarvis_daily")
    assert {item.id for item in visible} == {original.conversation.id, fresh.id}


@pytest.mark.asyncio
async def test_task_identity_survives_daily_rollover() -> None:
    origin = await jarvis_daily_service.accept_text_turn(
        "Delegate a task",
        accepted_at=datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc),
        idempotency_key="task-origin-turn",
    )
    async with session_scope() as session:
        task = await TaskRepository(session).dispatch_task(
            origin_surface="jarvis",
            origin_conversation_id=origin.conversation.id,
            origin_turn_id=origin.message.id,
            goal="Survive rollover",
            idempotency_key="task-survives-rollover",
        )
        task_id = task.id

    replacement = await jarvis_daily_service.accept_text_turn(
        "New operational day",
        accepted_at=datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc),
        idempotency_key="new-day-turn",
    )

    async with session_scope() as session:
        persisted = await TaskRepository(session).get_task(task_id)
    assert persisted is not None
    assert persisted.origin_conversation_id == origin.conversation.id
    assert replacement.conversation.id != origin.conversation.id


@pytest.mark.asyncio
async def test_history_filters_and_closed_log_mutation_guards() -> None:
    original = await jarvis_daily_service.accept_text_turn(
        "Archive-visible turn",
        accepted_at=datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc),
        idempotency_key="history-original",
    )
    fresh = await jarvis_daily_service.start_fresh_log(
        requested_at=datetime(2026, 8, 27, 12, 5, tzinfo=timezone.utc),
        idempotency_key="history-fresh",
    )
    standard = await conversation_store.create_conversation(title="Standard chat")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": ""})

        jarvis_history = await client.get("/api/conversations/", params={"kind": "jarvis"})
        assert jarvis_history.status_code == 200
        assert [item["id"] for item in jarvis_history.json()] == [original.conversation.id]
        assert jarvis_history.json()[0]["is_read_only"] is True
        assert jarvis_history.json()[0]["lifecycle_status"] == "closed"

        standard_history = await client.get("/api/conversations/", params={"kind": "standard"})
        assert standard_history.status_code == 200
        assert standard.id in {item["id"] for item in standard_history.json()}

        turn = await client.post(
            f"/api/conversations/{original.conversation.id}/messages",
            json={"content": "Must reject"},
        )
        resend = await client.post(
            f"/api/conversations/{original.conversation.id}/branches/resend",
            json={
                "source_message_id": original.message.id,
                "content": "Must reject",
                "parent_branch_key": "main",
            },
        )
        agent = await client.post(
            "/api/chat/",
            json={
                "conversation_id": original.conversation.id,
                "messages": [{"role": "user", "content": "Must reject"}],
                "stream": False,
            },
        )
        assert turn.status_code == 409
        assert resend.status_code == 409
        assert agent.status_code == 409

        current_state = await client.get("/api/jarvis/state")
        assert current_state.status_code == 200
        assert current_state.json()["current_log"]["id"] == fresh.id

        archived = await client.post(f"/api/conversations/{original.conversation.id}/archive")
        assert archived.status_code == 200
        assert archived.json()["lifecycle_status"] == "closed"
        default_history = await client.get("/api/conversations/", params={"kind": "jarvis"})
        assert default_history.json() == []
        hidden_history = await client.get(
            "/api/conversations/",
            params={"kind": "jarvis", "include_archived": True},
        )
        assert [item["id"] for item in hidden_history.json()] == [original.conversation.id]
