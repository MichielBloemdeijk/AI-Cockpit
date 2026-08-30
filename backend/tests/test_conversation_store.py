from __future__ import annotations

import pytest

from app.db.session import initialize_engine
from app.services import conversation_store as conversation_store_module
from app.services.conversation_read_model import conversation_read_model
from app.services.conversation_store import conversation_store
from app.services.conversation_timeline import build_tool_result_timeline_item
from app.services.task_delegation import delegate_background_task


def test_app_initialize_activity_lists_scaffolded_files():
    item = build_tool_result_timeline_item(
        run_id="run-1",
        step=1,
        tool_name="app_initialize",
        arguments={"title": "Hello World"},
        ok=True,
        output="Created generated app 'Hello World' at /apps/hello-world.",
        result_metadata={
            "app": {
                "title": "Hello World",
                "scaffolded_files": [
                    "frontend/generated-apps/hello-world/page.tsx",
                    "frontend/generated-apps/hello-world/styles.css",
                ],
            }
        },
    )

    assert item["content"] == "Initialized Hello World"
    assert item["code"] == (
        "Created files:\n"
        "frontend/generated-apps/hello-world/page.tsx\n"
        "frontend/generated-apps/hello-world/styles.css"
    )


@pytest.mark.asyncio
async def test_timeline_projection_replaces_activity_in_place():
    initialize_engine()

    conversation = await conversation_store.create_conversation(mode_hint="single")
    run = await conversation_store.start_run(conversation.id, kind="assistant")
    activity_id = f"tool:{run.id}:1:file_read"
    first_event = await conversation_store.append_timeline_item(
        conversation.id,
        run_id=run.id,
        item_payload={
            "item_id": activity_id,
            "role": "assistant",
            "kind": "tool_call",
            "content": "Reading lib/hooks.ts",
        },
    )
    await conversation_store.append_timeline_item(
        conversation.id,
        run_id=run.id,
        item_payload={
            "item_id": activity_id,
            "role": "assistant",
            "kind": "tool_result",
            "content": "Read lib/hooks.ts",
        },
    )

    events = await conversation_store.list_events_for_run(conversation.id, run.id)
    timeline_items = conversation_read_model._build_timeline_items(events)

    assert len(timeline_items) == 1
    assert timeline_items[0]["kind"] == "tool_result"
    assert timeline_items[0]["content"] == "Read lib/hooks.ts"
    assert timeline_items[0]["sequence"] == first_event.sequence


@pytest.mark.asyncio
async def test_delegated_task_projects_as_one_task_card_without_a_child_run():
    initialize_engine()

    conversation = await conversation_store.create_conversation(mode_hint="single")
    parent = await conversation_store.start_run(
        conversation.id,
        kind="assistant",
        metadata_json={"branch_key": "main"},
    )
    task, _ = await delegate_background_task(
        parent_run_id=parent.id,
        goal="Review the repository",
        idempotency_key="conversation-task-card",
    )

    events = await conversation_store.list_events(conversation.id, limit=5000)
    runs = await conversation_store.list_runs(conversation.id)
    timeline_items = conversation_read_model._build_timeline_items(events, runs)
    task_items = [item for item in timeline_items if item["kind"] == "task"]

    assert len(task_items) == 1
    assert task_items[0]["item_id"] == f"task:{task.task.id}"
    assert task_items[0]["metadata"]["task_id"] == task.task.id
    assert task_items[0]["metadata"]["task_run_id"] == task.task.id
    assert task_items[0]["status"] == "queued"
    assert [run.id for run in runs] == [parent.id]


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


@pytest.mark.asyncio
async def test_record_agent_tool_result_persists_full_success_lifecycle():
    initialize_engine()

    conversation = await conversation_store.create_conversation(mode_hint="single")
    run = await conversation_store.start_run(conversation.id, kind="assistant")

    record = await conversation_store.record_agent_tool_result(
        conversation.id,
        run_id=run.id,
        step=1,
        tool_name="file_read",
        arguments={"path": "frontend/lib/hooks.ts"},
        ok=True,
        output="hooks inspected",
        result_metadata={"path": "frontend/lib/hooks.ts"},
        artifact_type="agent.tool.file_read.output",
        artifact_content_text="full hooks content",
        artifact_content_json={"metadata": {"path": "frontend/lib/hooks.ts"}},
        run_metadata_json={"agent_status": "running", "active_step_index": 1},
    )

    events = await conversation_store.list_events_for_run(conversation.id, run.id)
    artifacts = await conversation_store.list_artifacts_for_run(conversation.id, run.id)
    stored_run = await conversation_store.get_run(run.id)

    assert [event.event_type for event in events] == ["agent.tool.called", "agent.tool.completed", "chat.timeline.item.committed"]
    assert events[0].payload_json == {
        "step": 1,
        "tool": "file_read",
        "arguments": {"path": "frontend/lib/hooks.ts"},
    }
    assert events[1].payload_json == {
        "step": 1,
        "tool": "file_read",
        "ok": True,
        "output": "hooks inspected",
        "artifact_id": record.artifact.id,
        "metadata": {"path": "frontend/lib/hooks.ts"},
    }
    assert events[2].payload_json["kind"] == "tool_result"
    assert events[2].payload_json["content"] == "Read lib/hooks.ts"
    assert events[2].payload_json["code"] == "hooks inspected"
    assert record.started_event.id == events[0].id
    assert record.completed_event.id == events[1].id
    assert record.artifact is not None
    assert artifacts[0].id == record.artifact.id
    assert artifacts[0].source_event_id == record.started_event.id
    assert stored_run is not None
    assert stored_run.metadata_json == {"agent_status": "running", "active_step_index": 1}


@pytest.mark.asyncio
async def test_record_agent_tool_result_persists_failure_without_artifact():
    initialize_engine()

    conversation = await conversation_store.create_conversation(mode_hint="single")
    run = await conversation_store.start_run(conversation.id, kind="assistant")

    record = await conversation_store.record_agent_tool_result(
        conversation.id,
        run_id=run.id,
        step=2,
        tool_name="file_write",
        arguments={"path": "frontend/lib/hooks.ts", "content": "x"},
        ok=False,
        error="permission denied",
    )

    events = await conversation_store.list_events_for_run(conversation.id, run.id)
    artifacts = await conversation_store.list_artifacts_for_run(conversation.id, run.id)

    assert [event.event_type for event in events] == ["agent.tool.called", "agent.tool.completed", "chat.timeline.item.committed"]
    assert events[1].payload_json == {
        "step": 2,
        "tool": "file_write",
        "ok": False,
        "error": "permission denied",
    }
    assert events[2].payload_json["kind"] == "tool_result"
    assert events[2].payload_json["content"] == "permission denied"
    assert record.artifact is None
    assert artifacts == []
