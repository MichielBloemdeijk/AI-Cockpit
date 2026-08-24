from __future__ import annotations

import asyncio

import pytest

from app.services.conversation_store import conversation_store
from app.services.run_lifecycle import run_lifecycle
from app.services.run_supervisor import RunSupervisor


@pytest.mark.asyncio
async def test_concurrent_background_cancellation_has_one_terminal_projection() -> None:
    conversation = await conversation_store.create_conversation(mode_hint="voice")
    run = await conversation_store.start_run(
        conversation.id,
        kind="assistant",
        status="queued",
        metadata_json={
            "execution_mode": "background",
            "branch_key": "main",
            "goal": "Wait for a slow operation",
            "title": "Slow operation",
            "notify_policy": "important",
        },
    )

    results = await asyncio.gather(run_lifecycle.cancel(run.id), run_lifecycle.cancel(run.id))

    assert sum(result.changed for result in results) == 1
    refreshed = await conversation_store.get_run(run.id)
    assert refreshed is not None
    assert refreshed.status == "cancelled"
    events = await conversation_store.list_events_for_run(conversation.id, run.id)
    terminal_events = [event for event in events if event.event_type == "task.cancelled"]
    assert len(terminal_events) == 1
    messages = await conversation_store.list_messages(conversation.id, final_only=True)
    terminal_messages = [message for message in messages if message.source_event_id == terminal_events[0].id]
    assert len(terminal_messages) == 1
    assert terminal_messages[0].content == "The background task was cancelled."


@pytest.mark.asyncio
async def test_supervisor_cancels_a_queued_run_durably_before_runner_starts_work() -> None:
    conversation = await conversation_store.create_conversation(mode_hint="voice")
    run = await conversation_store.start_run(
        conversation.id,
        kind="assistant",
        status="queued",
        metadata_json={"execution_mode": "background", "branch_key": "main", "goal": "Queued work"},
    )
    entered = asyncio.Event()

    class FakeRunner:
        async def continue_run(self, run_id: str, *, cancel_event: asyncio.Event) -> None:
            entered.set()
            await cancel_event.wait()

    supervisor = RunSupervisor()
    task = supervisor.start(run.id, runner=FakeRunner())
    await entered.wait()

    assert await supervisor.cancel(run.id) is True
    await task

    refreshed = await conversation_store.get_run(run.id)
    assert refreshed is not None
    assert refreshed.status == "cancelled"
    events = await conversation_store.list_events_for_run(conversation.id, run.id)
    assert [event.event_type for event in events].count("task.cancelled") == 1


@pytest.mark.asyncio
async def test_voice_cancellation_is_spoken_once_when_control_paths_race() -> None:
    conversation = await conversation_store.create_conversation(mode_hint="voice")
    run = await conversation_store.start_run(
        conversation.id,
        kind="assistant",
        status="running",
        metadata_json={"execution_mode": "voice_interactive", "voice_response": True, "branch_key": "main"},
    )

    first, second = await asyncio.gather(run_lifecycle.cancel(run.id), run_lifecycle.cancel(run.id))

    assert {first.changed, second.changed} == {False, True}
    events = await conversation_store.list_events_for_run(conversation.id, run.id)
    cancellations = [event for event in events if event.event_type == "run.cancelled"]
    assert len(cancellations) == 1
    assert cancellations[0].payload_json is not None
    assert cancellations[0].payload_json["voice_delivery"]["speak"] is True


@pytest.mark.asyncio
async def test_restart_interrupts_paused_background_run_without_resuming_it() -> None:
    conversation = await conversation_store.create_conversation(mode_hint="voice")
    run = await conversation_store.start_run(
        conversation.id,
        kind="assistant",
        status="paused",
        metadata_json={"execution_mode": "background", "branch_key": "main", "goal": "Await an answer"},
    )

    assert await conversation_store.mark_running_runs_interrupted() == 1

    refreshed = await conversation_store.get_run(run.id)
    assert refreshed is not None
    assert refreshed.status == "interrupted"
    events = await conversation_store.list_events_for_run(conversation.id, run.id)
    interrupted = [event for event in events if event.event_type == "task.interrupted"]
    assert len(interrupted) == 1
    assert interrupted[0].payload_json is not None
    assert interrupted[0].payload_json["voice_delivery"]["speak"] is True
    messages = await conversation_store.list_messages(conversation.id, final_only=True)
    assert any(message.source_event_id == interrupted[0].id for message in messages)


@pytest.mark.asyncio
async def test_question_answer_cannot_resurrect_a_cancelled_paused_run() -> None:
    conversation = await conversation_store.create_conversation(mode_hint="voice")
    metadata = {
        "execution_mode": "background",
        "branch_key": "main",
        "pending_question": {"id": "question-1", "question": "Which environment?"},
    }
    run = await conversation_store.start_run(
        conversation.id,
        kind="assistant",
        status="paused",
        metadata_json=metadata,
    )
    assert (await run_lifecycle.cancel(run.id)).changed is True

    answered = await conversation_store.answer_paused_run(
        conversation.id,
        run_id=run.id,
        answer="production",
        question_id="question-1",
        metadata_json={**metadata, "pending_question": None},
        branch_key="main",
    )

    assert answered is None
    refreshed = await conversation_store.get_run(run.id)
    assert refreshed is not None
    assert refreshed.status == "cancelled"
    events = await conversation_store.list_events_for_run(conversation.id, run.id)
    assert not any(event.event_type == "agent.question.answered" for event in events)
