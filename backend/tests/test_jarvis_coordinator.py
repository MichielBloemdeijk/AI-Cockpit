from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.session import session_scope
from app.db.tables import TaskEvent
from app.main import app
from app.services.conversation_store import conversation_store
from app.services.jarvis_coordinator import StaleSessionError, jarvis_coordinator
from app.services.task_presentations import task_presentation_service
from app.services.task_service import task_service
from app.services.voice_session_adapter import VoiceProviderEvent


@pytest.mark.asyncio
async def test_fake_provider_direct_response_and_delegation_acknowledgement() -> None:
    first = await jarvis_coordinator.accept_text_turn("Status check", idempotency_key="direct")
    session = await jarvis_coordinator.get_active_session()
    assert session is not None
    assert session.provider_adapter == "fake"
    assert session.lifecycle_state == "ready"
    assert session.floor_state == "listening"

    messages = await conversation_store.list_messages(first.conversation.id, final_only=True)
    assert [(item.role, item.content) for item in messages] == [
        ("user", "Status check"),
        ("assistant", "I heard: Status check"),
    ]

    delegated = await jarvis_coordinator.accept_text_turn(
        "delegate: prepare the launch brief",
        idempotency_key="delegate",
    )
    retried = await jarvis_coordinator.accept_text_turn(
        "delegate: prepare the launch brief",
        idempotency_key="delegate",
    )
    assert retried.message.id == delegated.message.id
    tasks = await task_service.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].task.goal == "prepare the launch brief"
    assert tasks[0].task.origin_turn_id == delegated.message.id
    messages = await conversation_store.list_messages(first.conversation.id, final_only=True)
    assert messages[-1].content == "I've started that in the background."
    assert sum(item.content == "I've started that in the background." for item in messages) == 1
    _, events = await jarvis_coordinator.replay_session_events(session.id)
    assert "brain.tool.result" in [event.event_type for event in events]
    await jarvis_coordinator.inject_fake_event(
        session_id=session.id,
        generation=session.generation,
        event_id="forbidden-shell-call",
        kind="shell_call",
        payload={"call_id": "forbidden", "name": "file_write", "arguments": {"path": "nope"}},
    )
    _, restricted_events = await jarvis_coordinator.replay_session_events(session.id)
    restricted = next(
        event
        for event in restricted_events
        if event.event_type == "brain.tool.result"
        and (event.payload_json or {}).get("call_id") == "forbidden"
    )
    assert restricted.payload_json is not None
    assert restricted.payload_json["ok"] is False
    assert "not allowed" in restricted.payload_json["output"]["error"]


@pytest.mark.asyncio
async def test_interruption_duplicate_and_reordered_delivery_truth() -> None:
    accepted = await jarvis_coordinator.accept_text_turn("Begin", idempotency_key="begin")
    session = await jarvis_coordinator.get_active_session()
    assert session is not None
    payload = {
        "response_id": "response-interrupted",
        "item_id": "item-interrupted",
        "transcript": "I'll explain the whole result now.",
        "heard_transcript": "I'll explain",
    }
    interrupted = VoiceProviderEvent(
        event_id="interrupted-once",
        kind="assistant_interrupted",
        payload=payload,
    )
    await jarvis_coordinator.handle_provider_event(session.id, session.generation, interrupted)
    await jarvis_coordinator.handle_provider_event(session.id, session.generation, interrupted)
    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(
            event_id="late-speaking",
            kind="assistant_speaking",
            payload={"response_id": "response-interrupted"},
        ),
    )
    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(
            event_id="natural-continuation",
            kind="assistant_response",
            payload={
                "response_id": "response-continuation",
                "item_id": "item-continuation",
                "transcript": "The useful part is that the task is complete.",
            },
        ),
    )

    messages = await conversation_store.list_messages(accepted.conversation.id, final_only=True)
    assistant_tail = [item for item in messages if item.role == "assistant"][-2:]
    assert [(item.content, item.communication_state) for item in assistant_tail] == [
        ("I'll explain", "interrupted"),
        ("The useful part is that the task is complete.", "completed"),
    ]
    _, events = await jarvis_coordinator.replay_session_events(session.id)
    assert sum(event.idempotency_key == "provider:interrupted-once" for event in events) == 1
    assert events[-2].event_type == "provider.event.ignored"
    context = await jarvis_coordinator.context(session.id)
    assert context is not None
    dialogue = [item["content"] for item in context["dialogue"]]
    assert "I'll explain" in dialogue
    assert "I'll explain the whole result now." not in dialogue


@pytest.mark.asyncio
async def test_controller_supersession_observer_and_stale_generation_rejection() -> None:
    accepted = await jarvis_coordinator.accept_text_turn("Connect", idempotency_key="connect")
    old = await jarvis_coordinator.get_active_session()
    assert old is not None
    replacement = await jarvis_coordinator.open_controller(
        conversation_id=accepted.conversation.id,
        controller_id="browser-b",
    )
    observer = await jarvis_coordinator.observe()

    assert observer is not None and observer.id == replacement.id
    assert replacement.generation == old.generation + 1
    old_record = await jarvis_coordinator.get_session(old.id)
    assert old_record is not None and old_record.lifecycle_state == "superseded"
    with pytest.raises(StaleSessionError, match="stale"):
        await jarvis_coordinator.handle_provider_event(
            old.id,
            old.generation,
            VoiceProviderEvent(event_id="stale-callback", kind="user_speaking"),
        )

    reconnect = await jarvis_coordinator.open_controller(
        conversation_id=accepted.conversation.id,
        controller_id="browser-b-reconnect",
    )
    assert reconnect.generation == replacement.generation + 1


@pytest.mark.asyncio
async def test_presentations_coalesce_defer_while_user_speaks_and_present_once() -> None:
    accepted = await jarvis_coordinator.accept_text_turn("Track a task", idempotency_key="track")
    session = await jarvis_coordinator.get_active_session()
    assert session is not None
    task = await task_service.dispatch(
        goal="Build the report",
        origin_surface="jarvis",
        origin_conversation_id=accepted.conversation.id,
        origin_turn_id=accepted.message.id,
        idempotency_key="presentation-task",
    )
    await task_service.publish_progress(task.task.id, summary="Drafting", idempotency_key="progress-1")
    await task_service.publish_progress(task.task.id, summary="Reviewing", idempotency_key="progress-2")
    _, projected_events = await task_service.replay_events(after_sequence=0)
    assert [item.event_type for item in projected_events] == ["task.queued", "task.progress", "task.progress"]
    assert task.task.presentation_policy == "natural"
    async with session_scope() as db:
        matched = list((await db.execute(select(TaskEvent).where(TaskEvent.event_type.in_(["task.progress"])))).scalars().all())
        assert len(matched) == 2
        assert {item.task_id for item in matched} == {task.task.id}
    assert await task_presentation_service.sync_candidates() == 2
    projected = await task_service.get_task(task.task.id)
    assert projected is not None
    assert sorted((item.candidate_type, item.state) for item in projected.presentations) == [
        ("progress", "pending"),
        ("progress", "superseded"),
    ]
    pending_progress = next(item for item in projected.presentations if item.state == "pending")
    assert pending_progress.required_facts_json is not None
    assert pending_progress.required_facts_json["summary"] == "Reviewing"

    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(event_id="user-floor", kind="user_speaking"),
    )
    assert await jarvis_coordinator.reserve_presentations(
        session_id=session.id,
        generation=session.generation,
    ) is None

    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(event_id="user-floor-ended", kind="user_stopped"),
    )
    reservation = await jarvis_coordinator.reserve_presentations(
        session_id=session.id,
        generation=session.generation,
    )
    assert reservation is not None
    assert [fact["summary"] for fact in reservation.facts] == ["Reviewing"]

    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(
            event_id="presentation-interrupted",
            kind="assistant_interrupted",
            payload={
                "response_id": "presentation-response-1",
                "item_id": "presentation-item-1",
                "transcript": "The report is being reviewed.",
                "heard_transcript": "The report",
                "presentation_ids": list(reservation.presentation_ids),
                "facts_heard": False,
            },
        ),
    )
    retry = await jarvis_coordinator.reserve_presentations(
        session_id=session.id,
        generation=session.generation,
    )
    assert retry is not None and retry.presentation_ids == reservation.presentation_ids
    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(
            event_id="presentation-complete",
            kind="assistant_response",
            payload={
                "response_id": "presentation-response-2",
                "item_id": "presentation-item-2",
                "transcript": "The report is being reviewed.",
                "presentation_ids": list(retry.presentation_ids),
            },
        ),
    )
    detail = await task_service.get_task(task.task.id)
    assert detail is not None
    progress = [item for item in detail.presentations if item.candidate_type == "progress"]
    assert sorted(item.state for item in progress) == ["presented", "superseded"]

    await task_service.complete(
        task.task.id,
        summary="Report complete",
        idempotency_key="slow-task-complete",
    )
    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(event_id="completion-during-user-floor", kind="user_speaking"),
    )
    assert await jarvis_coordinator.reserve_presentations(
        session_id=session.id,
        generation=session.generation,
    ) is None
    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(event_id="completion-after-user-floor", kind="user_stopped"),
    )
    terminal = await jarvis_coordinator.reserve_presentations(
        session_id=session.id,
        generation=session.generation,
    )
    assert terminal is not None
    assert terminal.facts[0]["summary"] == "Report complete"


@pytest.mark.asyncio
async def test_fast_task_completion_projects_a_terminal_candidate() -> None:
    accepted = await jarvis_coordinator.accept_text_turn("Fast task", idempotency_key="fast-origin")
    task = await task_service.dispatch(
        goal="Finish immediately",
        origin_surface="jarvis",
        origin_conversation_id=accepted.conversation.id,
        origin_turn_id=accepted.message.id,
        idempotency_key="fast-task",
    )
    await task_service.complete(task.task.id, summary="Already done", idempotency_key="fast-done")

    pending = await task_presentation_service.list_pending()
    candidate = next(item for item in pending if item.task_id == task.task.id)
    assert candidate.candidate_type == "terminal_result"
    assert candidate.required_facts_json is not None
    assert candidate.required_facts_json["summary"] == "Already done"


@pytest.mark.asyncio
async def test_question_answer_and_fresh_log_keep_task_identity() -> None:
    accepted = await jarvis_coordinator.accept_text_turn("Need a decision", idempotency_key="question-origin")
    session = await jarvis_coordinator.get_active_session()
    assert session is not None
    task = await task_service.dispatch(
        goal="Wait for a branch",
        origin_surface="jarvis",
        origin_conversation_id=accepted.conversation.id,
        origin_turn_id=accepted.message.id,
        idempotency_key="question-task",
    )
    question = await task_service.ask_question(
        task.task.id,
        question="Which branch?",
        idempotency_key="question",
    )
    await jarvis_coordinator.inject_fake_event(
        session_id=session.id,
        generation=session.generation,
        event_id="answer-shell-call",
        kind="shell_call",
        payload={
            "call_id": "answer-call",
            "name": "answer_task_question",
            "arguments": {
                "task_id": task.task.id,
                "question_id": question.id,
                "answer": "main",
            },
        },
    )
    answered = await task_service.get_task(task.task.id)
    assert answered is not None
    assert answered.task.status == "queued"
    assert answered.questions[0].answer == "main"

    fresh = await jarvis_coordinator.start_fresh_log(idempotency_key="fresh-after-question")
    assert fresh.id != accepted.conversation.id
    assert fresh.jarvis_day_sequence == 2
    durable_task = await task_service.get_task(task.task.id)
    assert durable_task is not None
    assert durable_task.task.origin_conversation_id == accepted.conversation.id
    old_session = await jarvis_coordinator.get_session(session.id)
    assert old_session is not None and old_session.lifecycle_state == "superseded"


@pytest.mark.asyncio
async def test_live_workspace_api_exposes_independent_domain_cursors_and_lab() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": "dev"})
        accepted = await client.post(
            "/api/jarvis/turns/text",
            json={"content": "API command", "idempotency_key": "api-command"},
        )
        assert accepted.status_code == 202
        state = await client.get("/api/jarvis/state")
        assert state.status_code == 200
        body = state.json()
        assert body["current_log"]["messages"][-1]["content"] == "I heard: API command"
        assert body["session"]["provider_adapter"] == "fake"
        assert set(body["cursors"]) == {"conversation", "task", "session"}

        conversation_events = await client.get(
            f"/api/jarvis/logs/{body['current_log']['id']}/events",
            params={"after": 0},
        )
        session_events = await client.get(
            f"/api/jarvis/sessions/{body['session']['id']}/events",
            params={"after": 0},
        )
        task_events = await client.get("/api/tasks/events", params={"after": 0})
        assert conversation_events.json()["latest_sequence"] == body["cursors"]["conversation"]
        assert session_events.json()["latest_sequence"] == body["cursors"]["session"]
        assert task_events.json()["latest_sequence"] == body["cursors"]["task"]

        observer = await client.post(
            "/api/jarvis/realtime/calls",
            json={"controller_id": "observer-tab", "role": "observer"},
        )
        assert observer.status_code == 200
        assert observer.json()["session"]["id"] == body["session"]["id"]

        event_id = str(uuid4())
        injected = await client.post(
            f"/api/jarvis/fake/sessions/{body['session']['id']}/events",
            json={
                "generation": body["session"]["generation"],
                "event_id": event_id,
                "kind": "user_speaking",
                "payload": {},
            },
        )
        assert injected.status_code == 202
        refreshed = await client.get("/api/jarvis/state")
        assert refreshed.json()["session"]["floor_state"] == "user_speaking"

        transcript = await client.post(
            f"/api/jarvis/fake/sessions/{body['session']['id']}/events",
            json={
                "generation": body["session"]["generation"],
                "event_id": str(uuid4()),
                "kind": "user_transcript",
                "payload": {"item_id": "provider-user-1", "transcript": "A live voice turn"},
            },
        )
        assert transcript.status_code == 202
        with_transcript = (await client.get("/api/jarvis/state")).json()
        assert with_transcript["current_log"]["messages"][-1]["content"] == "A live voice turn"
        assert with_transcript["current_log"]["messages"][-1]["modality"] == "voice"
