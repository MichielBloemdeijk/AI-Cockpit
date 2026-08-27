from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.conversation_store import conversation_store
from app.services.task_service import task_service


@pytest.mark.asyncio
async def test_dispatch_is_idempotent_under_contention() -> None:
    snapshots = await asyncio.gather(
        *(
            task_service.dispatch(
                goal="Inspect the repository",
                origin_surface="test",
                idempotency_key="same-dispatch",
                targets=(("repository", "AI-Cockpit", True),),
            )
            for _ in range(6)
        )
    )

    assert len({snapshot.task.id for snapshot in snapshots}) == 1
    detail = await task_service.get_task(snapshots[0].task.id)
    assert detail is not None
    assert len(detail.attempts) == 1
    assert [event.event_type for event in detail.events] == ["task.queued"]
    assert [(target.resource_type, target.resource_key) for target in detail.targets] == [
        ("repository", "AI-Cockpit")
    ]


@pytest.mark.asyncio
async def test_global_event_cursor_is_ordered_replayable_and_deduplicated() -> None:
    first = await task_service.dispatch(
        goal="First",
        origin_surface="test",
        idempotency_key="cursor-first",
    )
    second = await task_service.dispatch(
        goal="Second",
        origin_surface="test",
        idempotency_key="cursor-second",
    )
    await task_service.publish_progress(
        first.task.id,
        summary="Working",
        idempotency_key="progress-once",
    )
    await task_service.publish_progress(
        first.task.id,
        summary="Working",
        idempotency_key="progress-once",
    )

    latest, events = await task_service.replay_events(after_sequence=0)
    assert [event.sequence for event in events] == sorted(event.sequence for event in events)
    assert len({event.sequence for event in events}) == len(events)
    assert latest == events[-1].sequence
    assert sum(event.event_type == "task.progress" for event in events) == 1

    _, replay = await task_service.replay_events(after_sequence=events[0].sequence)
    assert [event.id for event in replay] == [event.id for event in events[1:]]
    assert {event.task_id for event in events} == {first.task.id, second.task.id}


@pytest.mark.asyncio
async def test_question_answer_is_once_and_creates_new_attempt() -> None:
    created = await task_service.dispatch(
        goal="Need a choice",
        origin_surface="test",
        idempotency_key="question-task",
    )
    question = await task_service.ask_question(
        created.task.id,
        question="Which branch?",
        idempotency_key="ask-branch",
    )
    answered = await task_service.answer_question(
        created.task.id,
        question.id,
        answer="main",
        idempotency_key="answer-branch",
    )
    retried_request = await task_service.answer_question(
        created.task.id,
        question.id,
        answer="main",
        idempotency_key="answer-branch",
    )

    assert answered.task.id == retried_request.task.id == created.task.id
    assert answered.task.status == "queued"
    detail = await task_service.get_task(created.task.id)
    assert detail is not None
    assert [attempt.attempt_number for attempt in detail.attempts] == [1, 2]
    assert detail.questions[0].answer == "main"
    assert detail.questions[0].answered_attempt_id == detail.attempts[1].id
    with pytest.raises(ValueError, match="already been answered"):
        await task_service.answer_question(
            created.task.id,
            question.id,
            answer="develop",
            idempotency_key="answer-again",
        )


@pytest.mark.asyncio
async def test_cancellation_state_machine_and_explicit_retry() -> None:
    queued = await task_service.dispatch(
        goal="Cancel before claim",
        origin_surface="test",
        idempotency_key="queued-cancel-task",
    )
    cancelled = await task_service.cancel(queued.task.id, idempotency_key="cancel-queued")
    assert cancelled.task.status == "cancelled"
    again = await task_service.cancel(queued.task.id, idempotency_key="cancel-queued")
    assert again.task.status == "cancelled"

    retried = await task_service.retry(queued.task.id, idempotency_key="retry-cancelled")
    assert retried.task.id == queued.task.id
    assert retried.task.status == "queued"
    detail = await task_service.get_task(queued.task.id)
    assert detail is not None
    assert [attempt.attempt_number for attempt in detail.attempts] == [1, 2]

    running = await task_service.dispatch(
        goal="Cancel while running",
        origin_surface="test",
        idempotency_key="running-cancel-task",
    )
    await task_service.publish_progress(
        running.task.id,
        summary="Started",
        idempotency_key="running-started",
    )
    requested = await task_service.cancel(running.task.id, idempotency_key="request-running-cancel")
    assert requested.task.status == "running"
    assert requested.task.cancellation_requested_at is not None
    finished = await task_service.finish_cancellation(
        running.task.id,
        idempotency_key="worker-observed-cancel",
    )
    assert finished.task.status == "cancelled"


@pytest.mark.asyncio
async def test_task_trace_is_exposed_without_polluting_conversation_history() -> None:
    conversation = await conversation_store.create_conversation(title="Keep history clean")
    created = await task_service.dispatch(
        goal="Produce a report",
        origin_surface="standard_chat",
        origin_conversation_id=conversation.id,
        idempotency_key="trace-task",
        targets=(("repository", "AI-Cockpit", False),),
    )
    await task_service.publish_progress(
        created.task.id,
        summary="Reviewed the code",
        idempotency_key="trace-progress",
    )
    artifact = await task_service.add_artifact(
        created.task.id,
        artifact_type="result",
        mime_type="text/markdown",
        title="Review report",
        content_text="Everything is traceable.",
        idempotency_key="trace-artifact",
    )
    await task_service.enqueue_presentation(
        created.task.id,
        candidate_type="terminal_result",
        coalescing_key=f"task:{created.task.id}:result",
        source_task_event_id=artifact.source_task_event_id,
        idempotency_key="trace-presentation",
    )
    await task_service.complete(
        created.task.id,
        summary="Report complete",
        idempotency_key="trace-complete",
    )

    assert await conversation_store.list_messages(conversation.id) == []

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": "dev"})
        listing = await client.get("/api/tasks")
        detail = await client.get(f"/api/tasks/{created.task.id}")
        replay = await client.get("/api/tasks/events", params={"after": 0})
        worker = await client.get("/api/tasks/worker")

    assert listing.status_code == 200
    assert listing.json()[0]["id"] == created.task.id
    assert detail.status_code == 200
    body = detail.json()
    assert body["terminal_summary"] == "Report complete"
    assert body["worker_health"]["status"] == "offline"
    assert body["attempts"][0]["terminal_reason"] == "completed"
    assert body["artifacts"][0]["content_text"] == "Everything is traceable."
    assert body["targets"][0]["resource_key"] == "AI-Cockpit"
    assert body["presentations"][0]["candidate_type"] == "terminal_result"
    assert replay.status_code == 200
    assert replay.json()["latest_sequence"] == body["events"][-1]["sequence"]
    assert worker.status_code == 200
    assert worker.json()["status"] == "offline"
