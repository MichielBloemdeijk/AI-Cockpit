from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.db.tables import JarvisLiveEvent, NotificationOutbox, SpeechDelivery, VoiceSessionEvent
from app.main import app
from app.services import task_notifications
from app.services.conversation_store import conversation_store
from app.services.jarvis_coordinator import StaleSessionError, jarvis_coordinator
from app.services.reminder_scheduler import reminder_scheduler
from app.services.task_notifications import route_pending_task_notifications
from app.services.task_presentations import task_presentation_service
from app.services.task_service import task_service
from app.services.worker_coordinator import worker_coordinator
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
        "timing_summary": {
            "estimator_source": "provider_audio_end_calibrated_text",
            "audio_end_ms": 725,
            "cutoff_char": len("I'll explain"),
        },
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
    async with session_scope() as db:
        delivery = (await db.execute(
            select(SpeechDelivery).where(
                SpeechDelivery.provider_response_id == "response-interrupted"
            )
        )).scalar_one()
        assert delivery.generated_transcript == "I'll explain the whole result now."
        assert delivery.heard_transcript == "I'll explain"
        assert delivery.timing_summary_json["audio_end_ms"] == 725
        assert delivery.timing_summary_json["estimator_source"] == (
            "provider_audio_end_calibrated_text"
        )
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
async def test_progress_is_quiet_by_default_and_opted_in_progress_coalesces() -> None:
    assert await worker_coordinator.activate(process_identity="progress-worker") is not None
    quiet = await task_service.dispatch(
        goal="Quiet progress task",
        idempotency_key="quiet-progress-task",
        origin_surface="test",
    )
    await task_service.publish_progress(
        quiet.task.id,
        summary="This should stay quiet",
        idempotency_key="quiet-progress",
    )

    followed = await task_service.dispatch(
        goal="Followed progress task",
        presentation_policy="follow_progress",
        idempotency_key="follow-progress-task",
        origin_surface="test",
    )
    await task_service.publish_progress(
        followed.task.id,
        summary="Drafting",
        idempotency_key="follow-progress-1",
    )
    await task_service.publish_progress(
        followed.task.id,
        summary="Reviewing",
        idempotency_key="follow-progress-2",
    )
    assert await task_presentation_service.sync_candidates() == 2

    quiet_detail = await task_service.get_task(quiet.task.id)
    followed_detail = await task_service.get_task(followed.task.id)
    assert quiet_detail is not None and quiet_detail.presentations == ()
    assert followed_detail is not None
    assert followed_detail.task.presentation_policy == "follow_progress"
    assert sorted(
        (item.candidate_type, item.state) for item in followed_detail.presentations
    ) == [("progress", "pending"), ("progress", "superseded")]
    pending = next(item for item in followed_detail.presentations if item.state == "pending")
    assert pending.required_facts_json is not None
    assert pending.required_facts_json["summary"] == "Reviewing"
    assert pending.required_facts_json["required_spoken_phrase"]


@pytest.mark.asyncio
async def test_fast_task_completion_projects_a_terminal_candidate() -> None:
    assert await worker_coordinator.activate(process_identity="fast-worker") is not None
    accepted = await jarvis_coordinator.accept_text_turn("Fast task", idempotency_key="fast-origin")
    task = await task_service.dispatch(
        goal="Finish immediately",
        origin_surface="jarvis",
        origin_conversation_id=accepted.conversation.id,
        origin_turn_id=accepted.message.id,
        idempotency_key="fast-task",
    )
    await task_service.publish_progress(
        task.task.id,
        summary="Almost done",
        idempotency_key="fast-progress",
    )
    await task_service.complete(task.task.id, summary="Already done", idempotency_key="fast-done")

    pending = await task_presentation_service.list_pending()
    candidate = next(item for item in pending if item.task_id == task.task.id)
    assert candidate.candidate_type == "terminal_result"
    assert candidate.required_facts_json is not None
    assert candidate.required_facts_json["summary"] == "Already done"
    detail = await task_service.get_task(task.task.id)
    assert detail is not None
    assert sorted((item.candidate_type, item.state) for item in detail.presentations) == [
        ("terminal_result", "pending"),
    ]


@pytest.mark.asyncio
async def test_pending_update_merges_into_the_next_user_response() -> None:
    accepted = await jarvis_coordinator.accept_text_turn("Track updates", idempotency_key="merge-origin")
    session = await jarvis_coordinator.get_active_session()
    assert session is not None
    task = await task_service.dispatch(
        goal="Prepare the merged report",
        origin_surface="jarvis",
        origin_conversation_id=accepted.conversation.id,
        origin_turn_id=accepted.message.id,
        idempotency_key="merge-task",
    )
    await task_service.complete(
        task.task.id,
        summary="The merged report is complete.",
        idempotency_key="merge-complete",
    )
    await task_presentation_service.sync_candidates()

    await jarvis_coordinator.accept_text_turn(
        "What changed?",
        idempotency_key="merge-user-turn",
    )
    detail = await task_service.get_task(task.task.id)
    assert detail is not None
    candidate = next(item for item in detail.presentations if item.candidate_type == "terminal_result")
    assert candidate.state == "presented"
    assert candidate.required_facts_json is not None
    phrase = candidate.required_facts_json["required_spoken_phrase"]
    messages = await conversation_store.list_messages(accepted.conversation.id, final_only=True)
    assert messages[-2].content == "What changed?"
    assert messages[-1].content == f"I heard: What changed? {phrase}"


@pytest.mark.asyncio
async def test_update_arriving_after_response_start_waits_for_completion() -> None:
    accepted = await jarvis_coordinator.accept_text_turn("Start a response", idempotency_key="late-origin")
    session = await jarvis_coordinator.get_active_session()
    assert session is not None
    gate = jarvis_coordinator._response_gates[(session.id, session.generation)]
    gate.inflight = True
    task = await task_service.dispatch(
        goal="Late update",
        origin_surface="jarvis",
        origin_conversation_id=accepted.conversation.id,
        idempotency_key="late-task",
    )
    await task_service.complete(
        task.task.id,
        summary="Late update complete.",
        idempotency_key="late-complete",
    )
    await task_presentation_service.sync_candidates()
    await jarvis_coordinator._queue_controlled_response(
        session.id,
        session.generation,
        "task_update",
    )
    waiting = await task_service.get_task(task.task.id)
    assert waiting is not None and waiting.presentations[0].state == "pending"

    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(
            event_id="late-current-response-complete",
            kind="assistant_response",
            payload={
                "response_id": "late-current-response",
                "item_id": "late-current-item",
                "transcript": "The current response is done.",
            },
        ),
    )
    delivered = await task_service.get_task(task.task.id)
    assert delivered is not None and delivered.presentations[0].state == "presented"


@pytest.mark.asyncio
async def test_interrupted_update_is_requeued_until_required_phrase_is_heard() -> None:
    accepted = await jarvis_coordinator.accept_text_turn("Track delivery", idempotency_key="retry-origin")
    session = await jarvis_coordinator.get_active_session()
    assert session is not None
    task = await task_service.dispatch(
        goal="Test interrupted delivery",
        origin_surface="jarvis",
        origin_conversation_id=accepted.conversation.id,
        origin_turn_id=accepted.message.id,
        idempotency_key="retry-task",
    )
    await task_service.complete(
        task.task.id,
        summary="The retry task is complete.",
        idempotency_key="retry-complete",
    )
    reservation = await task_presentation_service.reserve(
        session_id=session.id,
        generation=session.generation,
        user_owns_floor=False,
    )
    assert reservation is not None
    phrase = reservation.facts[0]["required_spoken_phrase"]
    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(
            event_id="retry-interrupted",
            kind="assistant_interrupted",
            payload={
                "response_id": "retry-response-1",
                "item_id": "retry-item-1",
                "transcript": phrase,
                "heard_transcript": "The retry task",
                "presentation_ids": list(reservation.presentation_ids),
            },
        ),
    )
    retry = await task_presentation_service.reserve(
        session_id=session.id,
        generation=session.generation,
        user_owns_floor=False,
    )
    assert retry is not None and retry.presentation_ids == reservation.presentation_ids
    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(
            event_id="retry-completed",
            kind="assistant_response",
            payload={
                "response_id": "retry-response-2",
                "item_id": "retry-item-2",
                "transcript": phrase,
                "presentation_ids": list(retry.presentation_ids),
            },
        ),
    )
    detail = await task_service.get_task(task.task.id)
    assert detail is not None
    candidate = next(item for item in detail.presentations if item.candidate_type == "terminal_result")
    assert candidate.state == "presented"
    assert candidate.retry_count == 1


@pytest.mark.asyncio
async def test_contradictory_reminder_speech_is_requeued_and_corrected() -> None:
    accepted = await jarvis_coordinator.accept_text_turn(
        "Set a reminder",
        idempotency_key="contradiction-origin",
    )
    session = await jarvis_coordinator.get_active_session()
    assert session is not None
    due = datetime.now(timezone.utc) + timedelta(seconds=1)
    reminder = await reminder_scheduler.create(
        text="check the oven",
        due_at=due,
        idempotency_key="contradiction-reminder",
        origin_conversation_id=accepted.conversation.id,
    )
    assert await reminder_scheduler.run_due(now=due + timedelta(seconds=1)) == 1
    reservation = await task_presentation_service.reserve(
        session_id=session.id,
        generation=session.generation,
        user_owns_floor=False,
    )
    assert reservation is not None
    phrase = reservation.facts[0]["required_spoken_phrase"]

    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(
            event_id="contradictory-reminder-delivery",
            kind="assistant_response",
            payload={
                "response_id": "contradictory-reminder-response",
                "item_id": "contradictory-reminder-item",
                "transcript": f"{phrase} It's queued.",
                "presentation_ids": list(reservation.presentation_ids),
                "response_kind": "user_turn",
            },
        ),
    )

    rejected = await task_service.get_task(reminder.task.id)
    assert rejected is not None
    candidate = rejected.presentations[0]
    assert candidate.state == "pending"
    assert candidate.last_error == "contradictory_spoken_delivery"
    async with session_scope() as db:
        outbox = (await db.execute(
            select(NotificationOutbox).where(NotificationOutbox.task_id == reminder.task.id)
        )).scalar_one()
        assert outbox.acknowledged_at is None

    correction = await task_presentation_service.reserve(
        session_id=session.id,
        generation=session.generation,
        user_owns_floor=False,
    )
    assert correction is not None
    assert correction.facts[0]["delivery"] == {"correction_required": True}
    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(
            event_id="corrected-reminder-delivery",
            kind="assistant_response",
            payload={
                "response_id": "corrected-reminder-response",
                "item_id": "corrected-reminder-item",
                "transcript": phrase,
                "presentation_ids": list(correction.presentation_ids),
                "response_kind": "task_update",
            },
        ),
    )
    corrected = await task_service.get_task(reminder.task.id)
    assert corrected is not None and corrected.presentations[0].state == "presented"
    async with session_scope() as db:
        outbox = (await db.execute(
            select(NotificationOutbox).where(NotificationOutbox.task_id == reminder.task.id)
        )).scalar_one()
        assert outbox.status == "acknowledged"
        assert outbox.acknowledgement_state == "spoken"


@pytest.mark.asyncio
async def test_interruption_does_not_release_the_floor_while_user_is_speaking() -> None:
    accepted = await jarvis_coordinator.accept_text_turn("Start", idempotency_key="floor-origin")
    session = await jarvis_coordinator.get_active_session()
    assert session is not None
    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(event_id="floor-user-started", kind="user_speaking"),
    )
    task = await task_service.dispatch(
        goal="Finish during interruption",
        origin_surface="jarvis",
        origin_conversation_id=accepted.conversation.id,
        idempotency_key="floor-task",
    )
    await task_service.complete(
        task.task.id,
        summary="Finished safely.",
        idempotency_key="floor-task-complete",
    )
    await task_presentation_service.sync_candidates()
    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(
            event_id="floor-assistant-interrupted",
            kind="assistant_interrupted",
            payload={
                "response_id": "floor-response",
                "item_id": "floor-item",
                "transcript": "I was interrupted.",
                "heard_transcript": "I was",
            },
        ),
    )

    current = await jarvis_coordinator.get_session(session.id)
    assert current is not None and current.floor_state == "user_speaking"
    await jarvis_coordinator._queue_controlled_response(
        session.id,
        session.generation,
        "task_update",
    )
    waiting = await task_service.get_task(task.task.id)
    assert waiting is not None and waiting.presentations[0].state == "pending"

    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(event_id="floor-user-stopped", kind="user_stopped"),
    )
    delivered = await task_service.get_task(task.task.id)
    assert delivered is not None and delivered.presentations[0].state == "presented"


@pytest.mark.asyncio
async def test_stale_busy_floor_releases_notification_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accepted = await jarvis_coordinator.accept_text_turn(
        "Start",
        idempotency_key="stale-floor-origin",
    )
    session = await jarvis_coordinator.get_active_session()
    assert session is not None
    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(event_id="stale-floor-user-started", kind="user_speaking"),
    )
    task = await task_service.dispatch(
        goal="Finish while the voice floor stalls",
        origin_surface="jarvis",
        origin_conversation_id=accepted.conversation.id,
        idempotency_key="stale-floor-task",
    )
    await task_service.complete(
        task.task.id,
        summary="The stalled-floor result is ready.",
        idempotency_key="stale-floor-complete",
    )
    await task_presentation_service.sync_candidates()
    async with session_scope() as db:
        floor_event = (await db.execute(
            select(VoiceSessionEvent)
            .where(VoiceSessionEvent.session_id == session.id)
            .where(VoiceSessionEvent.event_type == "provider.user_speaking")
        )).scalar_one()
        floor_event.created_at = datetime.now(timezone.utc) - timedelta(seconds=31)
        outbox = (await db.execute(
            select(NotificationOutbox).where(NotificationOutbox.task_id == task.task.id)
        )).scalar_one()
        outbox_id = outbox.id

    scheduled: list[str] = []

    async def capture_schedule(outbox_id: str, *, delay_seconds: float = 0.0) -> None:
        scheduled.append(outbox_id)

    monkeypatch.setattr(settings, "jarvis_update_poll_seconds", 0.01)
    monkeypatch.setattr(settings, "jarvis_notification_floor_stale_seconds", 30.0)
    monkeypatch.setattr(task_notifications, "schedule_task_notification", capture_schedule)
    await jarvis_coordinator.start()
    try:
        await asyncio.sleep(0.08)
    finally:
        await jarvis_coordinator.stop()

    assert outbox_id in scheduled
    detail = await task_service.get_task(task.task.id)
    assert detail is not None and detail.presentations[0].state == "pending"


@pytest.mark.asyncio
async def test_recap_reserves_urgent_updates_first_and_leaves_the_rest_pending() -> None:
    accepted = await jarvis_coordinator.accept_text_turn("Prioritize updates", idempotency_key="priority-origin")
    session = await jarvis_coordinator.get_active_session()
    assert session is not None
    completed = await task_service.dispatch(
        goal="Routine result",
        origin_surface="test",
        idempotency_key="priority-complete-task",
    )
    await task_service.complete(
        completed.task.id,
        summary="Routine result complete.",
        idempotency_key="priority-complete",
    )
    failed = await task_service.dispatch(
        goal="Failed result",
        origin_surface="test",
        idempotency_key="priority-failed-task",
    )
    await task_service.complete(
        failed.task.id,
        status="failed",
        summary="Failed result stopped.",
        idempotency_key="priority-failed",
    )
    question_task = await task_service.dispatch(
        goal="Blocking question",
        origin_surface="test",
        idempotency_key="priority-question-task",
    )
    await task_service.ask_question(
        question_task.task.id,
        question="Which option?",
        idempotency_key="priority-question",
    )
    now = datetime.now(timezone.utc)
    await reminder_scheduler.create(
        text=None,
        due_at=now + timedelta(seconds=1),
        idempotency_key="priority-reminder",
        origin_conversation_id=accepted.conversation.id,
    )
    await reminder_scheduler.run_due(now=now + timedelta(seconds=2))

    reservation = await task_presentation_service.reserve(
        session_id=session.id,
        generation=session.generation,
        user_owns_floor=False,
        limit=3,
    )
    assert reservation is not None and reservation.total_pending == 4
    assert [fact["event_type"] for fact in reservation.facts] == [
        "task.reminder.due",
        "task.requires_input",
        "task.failed",
    ]
    remaining = await task_presentation_service.list_pending()
    assert [item.required_facts_json["event_type"] for item in remaining] == ["task.completed"]


@pytest.mark.asyncio
async def test_duplicate_reminder_is_spoken_once_without_fallback_notification() -> None:
    accepted = await jarvis_coordinator.accept_text_turn("Set a reminder", idempotency_key="reminder-origin")
    due = datetime.now(timezone.utc) + timedelta(seconds=1)
    reminder = await reminder_scheduler.create(
        text=None,
        due_at=due,
        idempotency_key="reminder-provider-call-1",
        origin_conversation_id=accepted.conversation.id,
    )
    duplicate = await reminder_scheduler.create(
        text="  ",
        due_at=due,
        idempotency_key="reminder-provider-call-2",
        origin_conversation_id=accepted.conversation.id,
    )
    assert duplicate.task.id == reminder.task.id
    assert await reminder_scheduler.run_due(now=due + timedelta(seconds=1)) == 1

    await jarvis_coordinator.accept_text_turn("Anything due?", idempotency_key="reminder-follow-up")
    detail = await task_service.get_task(reminder.task.id)
    assert detail is not None and len(detail.presentations) == 1
    presentation = detail.presentations[0]
    assert presentation.state == "presented"
    assert presentation.required_facts_json["required_spoken_phrase"] == "This is your reminder."
    await route_pending_task_notifications(voice_connected=False)
    await asyncio.sleep(0.05)
    async with session_scope() as db:
        outbox = list((await db.execute(
            select(NotificationOutbox).where(NotificationOutbox.task_id == reminder.task.id)
        )).scalars().all())
        live_notifications = list((await db.execute(
            select(JarvisLiveEvent).where(JarvisLiveEvent.event_type == "notification.available")
        )).scalars().all())
    assert len(outbox) == 1
    assert outbox[0].status == "acknowledged"
    assert outbox[0].acknowledgement_state == "spoken"
    assert live_notifications == []


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
    assert answered.task.status == "waiting_for_worker"
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
