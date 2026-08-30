from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services.agent_tools.tasks import _cancel, _get, _list
from app.services.conversation_store import conversation_store
from app.services.task_service import task_service


@pytest.mark.asyncio
async def test_task_tools_only_expose_tasks_owned_by_the_conversation() -> None:
    conversation = await conversation_store.create_conversation(title="Task owner")
    other_conversation = await conversation_store.create_conversation(title="Other task owner")
    owned = await task_service.dispatch(
        goal="Review this conversation workspace",
        origin_surface="test",
        origin_conversation_id=conversation.id,
        idempotency_key="owned-agent-tool-task",
    )
    await task_service.dispatch(
        goal="Unrelated task",
        origin_surface="test",
        origin_conversation_id=other_conversation.id,
        idempotency_key="unrelated-agent-tool-task",
    )
    context = SimpleNamespace(conversation_id=conversation.id, run_id="run-1")

    listed = await _list(context, {})
    payload = json.loads(listed.output)
    assert [item["task_id"] for item in payload] == [owned.task.id]
    assert payload[0]["goal"] == "Review this conversation workspace"

    fetched = await _get(context, {"task_id": owned.task.id})
    assert json.loads(fetched.output)["task_id"] == owned.task.id

    with pytest.raises(ValueError, match="Task not found"):
        await _get(context, {"task_id": "missing-or-unowned"})


@pytest.mark.asyncio
async def test_cancel_task_tool_updates_first_class_task_directly() -> None:
    conversation = await conversation_store.create_conversation(title="Task owner")
    owned = await task_service.dispatch(
        goal="Cancel me",
        origin_surface="test",
        origin_conversation_id=conversation.id,
        idempotency_key="cancel-agent-tool-task",
    )
    context = SimpleNamespace(conversation_id=conversation.id, run_id="run-1")

    cancelled = await _cancel(context, {"task_id": owned.task.id})

    assert json.loads(cancelled.output)["status"] == "cancelled"
    detail = await task_service.get_task(owned.task.id)
    assert detail is not None and detail.task.status == "cancelled"
