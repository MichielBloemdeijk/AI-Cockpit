from __future__ import annotations

import asyncio

import pytest

from app.services.conversation_store import conversation_store
from app.services.run_lifecycle import run_lifecycle
from app.services.run_supervisor import RunSupervisor


@pytest.mark.asyncio
async def test_concurrent_run_cancellation_has_one_terminal_event() -> None:
    conversation = await conversation_store.create_conversation(mode_hint="single")
    run = await conversation_store.start_run(
        conversation.id,
        kind="assistant",
        status="queued",
        metadata_json={"branch_key": "main", "goal": "Wait for a slow operation"},
    )

    results = await asyncio.gather(run_lifecycle.cancel(run.id), run_lifecycle.cancel(run.id))

    assert sum(result.changed for result in results) == 1
    refreshed = await conversation_store.get_run(run.id)
    assert refreshed is not None and refreshed.status == "cancelled"
    events = await conversation_store.list_events_for_run(conversation.id, run.id)
    assert [event.event_type for event in events].count("run.cancelled") == 1
    assert await conversation_store.list_messages(conversation.id, final_only=True) == []


@pytest.mark.asyncio
async def test_supervisor_cancels_a_queued_run_durably_before_runner_starts_work() -> None:
    conversation = await conversation_store.create_conversation(mode_hint="single")
    run = await conversation_store.start_run(
        conversation.id,
        kind="assistant",
        status="queued",
        metadata_json={"branch_key": "main", "goal": "Queued work"},
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
    assert refreshed is not None and refreshed.status == "cancelled"
    events = await conversation_store.list_events_for_run(conversation.id, run.id)
    assert [event.event_type for event in events].count("run.cancelled") == 1


@pytest.mark.asyncio
async def test_restart_interrupts_unfinished_conversation_runs_without_messages() -> None:
    conversation = await conversation_store.create_conversation(mode_hint="single")
    runs = [
        await conversation_store.start_run(
            conversation.id,
            kind="assistant",
            status=status,
            metadata_json={"branch_key": "main", "goal": f"Run {index}"},
        )
        for index, status in enumerate(("queued", "running", "paused"), start=1)
    ]

    assert await conversation_store.mark_running_runs_interrupted() == 3

    events = await conversation_store.list_events(conversation.id, limit=200)
    interrupted = [event for event in events if event.event_type == "run.interrupted"]
    assert {event.run_id for event in interrupted} == {run.id for run in runs}
    assert all((event.payload_json or {}).get("status") == "interrupted" for event in interrupted)
    assert await conversation_store.list_messages(conversation.id, final_only=True) == []


@pytest.mark.asyncio
async def test_question_answer_cannot_resurrect_a_cancelled_paused_run() -> None:
    conversation = await conversation_store.create_conversation(mode_hint="single")
    metadata = {
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
    assert refreshed is not None and refreshed.status == "cancelled"
    events = await conversation_store.list_events_for_run(conversation.id, run.id)
    assert not any(event.event_type == "agent.question.answered" for event in events)
