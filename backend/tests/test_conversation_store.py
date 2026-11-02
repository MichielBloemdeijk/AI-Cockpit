from __future__ import annotations

import pytest

from app.db.session import initialize_engine
from app.services.conversations import store as conversation_store_module
from app.services.conversations.read_model import conversation_read_model
from app.services.conversations.store import conversation_store
from app.services.conversations.timeline import TIMELINE_ITEM_EVENT_TYPE, build_message_timeline_item


@pytest.mark.asyncio
async def test_timeline_projection_replaces_an_item_in_place():
    initialize_engine()

    conversation = await conversation_store.create_conversation(mode_hint="single")
    run = await conversation_store.start_run(conversation.id, kind="assistant")

    async def record(content: str):
        return await conversation_store.append_event(
            conversation.id,
            run_id=run.id,
            actor_kind="assistant",
            event_type=TIMELINE_ITEM_EVENT_TYPE,
            payload_json=build_message_timeline_item(message_id="m1", role="assistant", content=content),
        )

    first_event = await record("I heard: tomorow")
    # A corrected transcript re-records the same message.
    await record("I heard: tomorrow")

    events = await conversation_store.list_events_for_run(conversation.id, run.id)
    timeline_items = conversation_read_model._build_timeline_items(events)

    assert len(timeline_items) == 1
    assert timeline_items[0]["item_id"] == "assistant:m1"
    assert timeline_items[0]["content"] == "I heard: tomorrow"
    assert timeline_items[0]["sequence"] == first_event.sequence


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
    await conversation_store.end_run(run.id, "completed")

    messages = await conversation_store.list_messages(conversation.id)
    events = await conversation_store.list_events(conversation.id)
    artifacts = await conversation_store.list_artifacts(conversation.id)

    assert user_event.event_type == "conversation.user_message.accepted"
    assert assistant_event.event_type == "conversation.assistant.message.completed"
    assert [message.content for message in messages] == ["Persist this turn", "Stored reply"]
    assert {event.event_type for event in events} == {
        "conversation.user_message.accepted",
        "conversation.assistant.message.completed",
        "chat.timeline.item.committed",
    }
    timeline_items = [event.payload_json for event in events if event.event_type == "chat.timeline.item.committed"]
    assert [item["content"] for item in timeline_items] == ["Persist this turn", "Stored reply"]
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


