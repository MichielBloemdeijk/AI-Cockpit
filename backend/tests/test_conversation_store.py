from __future__ import annotations

import pytest

from app.db.session import initialize_engine
from app.services import conversation_store as conversation_store_module
from app.services.conversation_store import conversation_store


@pytest.mark.asyncio
async def test_conversation_store_writes_messages_events_and_artifacts():
    initialize_engine()

    scheduled_titles: list[tuple[str, str]] = []

    def _schedule_title_generation(conversation_id: str, user_content: str) -> None:
        scheduled_titles.append((conversation_id, user_content))

    conversation_store_module.conversation_title_service.schedule_title_generation = _schedule_title_generation

    conversation = await conversation_store.create_conversation(mode_hint="single")
    run = await conversation_store.start_run(conversation.id, kind="assistant")
    user_event, _ = await conversation_store.append_user_message(
        conversation.id,
        run_id=run.id,
        content="Persist this turn",
    )
    assistant_event, _ = await conversation_store.complete_message(
        conversation.id,
        run_id=run.id,
        role="assistant",
        content="Stored reply",
        actor_kind="assistant",
        event_type="conversation.assistant.message.completed",
        author_label="test-model",
    )
    artifact = await conversation_store.attach_artifact(
        conversation.id,
        run_id=run.id,
        source_event_id=assistant_event.id,
        artifact_type="prompt.snapshot",
        mime_type="application/json",
        content_json={"messages": 2},
    )
    await conversation_store.mark_run_completed(run.id)

    messages = await conversation_store.list_messages(conversation.id)
    events = await conversation_store.list_events(conversation.id)
    artifacts = await conversation_store.list_artifacts(conversation.id)

    assert user_event.event_type == "conversation.user_message.accepted"
    assert assistant_event.event_type == "conversation.assistant.message.completed"
    assert [message.content for message in messages] == ["Persist this turn", "Stored reply"]
    assert {event.event_type for event in events} == {
        "conversation.user_message.accepted",
        "conversation.assistant.message.completed",
    }
    assert artifacts[0].id == artifact.id
    assert scheduled_titles == [(conversation.id, "Persist this turn")]


@pytest.mark.asyncio
async def test_branch_messages_keep_turn_order_across_multiple_turns():
    initialize_engine()

    conversation = await conversation_store.create_conversation(mode_hint="single")

    first_run = await conversation_store.start_run(conversation.id, kind="assistant")
    await conversation_store.append_user_message(
        conversation.id,
        run_id=first_run.id,
        content="Sup",
    )
    await conversation_store.complete_message(
        conversation.id,
        run_id=first_run.id,
        role="assistant",
        content="Hey! What's up?",
        actor_kind="assistant",
        event_type="conversation.assistant.message.completed",
        author_label="test-model",
    )

    second_run = await conversation_store.start_run(conversation.id, kind="assistant")
    await conversation_store.append_user_message(
        conversation.id,
        run_id=second_run.id,
        content="Not much",
    )
    await conversation_store.complete_message(
        conversation.id,
        run_id=second_run.id,
        role="assistant",
        content="Fair enough",
        actor_kind="assistant",
        event_type="conversation.assistant.message.completed",
        author_label="test-model",
    )

    messages = await conversation_store.list_messages_for_branch(
        conversation.id,
        branch_key="main",
        final_only=True,
    )

    assert [(message.role, message.content) for message in messages] == [
        ("user", "Sup"),
        ("assistant", "Hey! What's up?"),
        ("user", "Not much"),
        ("assistant", "Fair enough"),
    ]