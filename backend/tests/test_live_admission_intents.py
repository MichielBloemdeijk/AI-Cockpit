import asyncio

import pytest
from sqlalchemy import select

from app.db.session import session_scope
from app.db.tables import Conversation, JarvisState, LiveSession, Task, TaskTurn
from app.services.jarvis_brain import RestrictedBrainGateway
from app.services.task_service import task_service


async def live_arguments():
    async with session_scope() as session:
        conversation = Conversation(title="Intent fixture")
        session.add(conversation)
        await session.flush()
        live = LiveSession(conversation_id=conversation.id, provider_adapter="fake", lifecycle_state="ready")
        session.add(live)
        await session.flush()
        return dict(session_id=live.id, generation=1, conversation_id=conversation.id,
            origin_turn_id=None, turn_scope="backend-turn-1")


@pytest.mark.asyncio
async def test_outcome_identity_survives_different_delivery_ids_and_gateway_instances():
    common = await live_arguments()
    arguments = {"goal": "Draft a test checklist", "intent_id": "checklist"}
    first, second = await asyncio.gather(
        RestrictedBrainGateway().execute(**common, tool_name="dispatch_task", arguments=arguments, call_id="call-a"),
        RestrictedBrainGateway().execute(**common, tool_name="dispatch_task", arguments=arguments, call_id="call-b"))
    assert first.output["task_id"] == second.output["task_id"]
    assert first.output["turn_id"] == second.output["turn_id"]
    for tool, changed in (("dispatch_task", {**arguments, "goal": "Changed goal"}),
        ("start_workflow", {"goal": arguments["goal"], "intent_id": "checklist", "definition": "screen_document@1"}),
        ("continue_task", {"content": "Expand", "intent_id": "checklist", "task_reference": "checklist"})):
        with pytest.raises(ValueError, match="conflict"):
            await RestrictedBrainGateway().execute(**common, tool_name=tool, arguments=changed, call_id="new-call")
    different = await RestrictedBrainGateway().execute(**common, tool_name="dispatch_task",
        arguments={"goal": "A separate requested outcome", "intent_id": "second"}, call_id="call-c")
    assert different.output["task_id"] != first.output["task_id"]
    async with session_scope() as session:
        assert len((await session.execute(select(Task))).scalars().all()) == 2


@pytest.mark.asyncio
async def test_crash_after_continuation_commit_recovers_one_original_run(monkeypatch):
    common = await live_arguments()
    original = await task_service.dispatch(goal="Original note", origin_surface="test", idempotency_key="original")
    await task_service.complete(original.task.id, summary="Done", idempotency_key="original-done")
    import app.services.live_admission_intents as receipts
    finish = receipts.finish_intent
    async def interrupted(*args, **kwargs):
        raise RuntimeError("Host stopped after commit")
    monkeypatch.setattr(receipts, "finish_intent", interrupted)
    request = {"task_id": original.task.id, "content": "Expand the note", "intent_id": "expand"}
    with pytest.raises(RuntimeError, match="Host stopped"):
        await RestrictedBrainGateway().execute(**common, tool_name="continue_task", arguments=request, call_id="before-crash")
    monkeypatch.setattr(receipts, "finish_intent", finish)
    result = await RestrictedBrainGateway().execute(**common, tool_name="continue_task", arguments=request, call_id="after-crash")
    assert result.output["accepted"] and result.output["replayed"]
    detail = await task_service.get_task(original.task.id)
    assert len(detail.runs) == 2 and result.output["execution_run_id"] == detail.runs[1].id
    async with session_scope() as session:
        assert len((await session.execute(select(TaskTurn).where(TaskTurn.task_id == original.task.id,
            TaskTurn.role == "user"))).scalars().all()) == 2


@pytest.mark.asyncio
async def test_concurrent_task_focus_preserves_every_reference_and_revision():
    from app.services.natural_task_resolver import natural_task_resolver
    tasks = [await task_service.dispatch(goal=f"Concurrent focus {i}", origin_surface="test",
        idempotency_key=f"focus-{i}") for i in range(8)]
    await asyncio.gather(*(natural_task_resolver.touch(item.task.id) for item in tasks))
    async with session_scope() as session:
        state = await session.get(JarvisState, "current")
        assert state.state_revision == 8
        assert set(state.recent_task_refs_json) == {item.task.id for item in tasks}
        assert state.focused_task_id == state.recent_task_refs_json[0]
