from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import time
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.db.tables import (
    ConversationEvent,
    JarvisLiveEvent,
    NotificationOutbox,
    LiveSession,
    SpeechDelivery,
    LiveSessionEvent,
)
from app.main import app
from app.services import task_notifications
from app.services.conversation_store import conversation_store
from app.services.jarvis_coordinator import (
    StaleSessionError,
    _ResponseAttempt,
    _ResponseGate,
    jarvis_coordinator,
)
from app.services.jarvis_live import jarvis_live_service
from app.services.reminder_scheduler import reminder_scheduler
from app.services.task_notifications import route_pending_task_notifications
from app.services.task_presentations import task_presentation_service
from app.services.task_service import task_service
from app.services.worker_coordinator import worker_coordinator
from app.services.voice_session_adapter import VoiceProviderEvent
from app.services.voice_session_adapter import fake_voice_session_adapter


@pytest.mark.asyncio
async def test_provider_connection_failure_is_recorded_without_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accepted = await jarvis_coordinator.accept_text_turn(
        "Connection diagnostics setup",
        idempotency_key="connection-diagnostics-setup",
    )
    monkeypatch.setattr(settings, "openai_api_key", "secret-test-key")

    async def fail_connect(**_kwargs):
        raise RuntimeError("provider rejected secret-test-key with HTTP 429")

    monkeypatch.setattr(fake_voice_session_adapter, "connect", fail_connect)

    with pytest.raises(RuntimeError, match="HTTP 429"):
        await jarvis_coordinator.bootstrap_controller(
            conversation_id=accepted.conversation.id,
            controller_id="failing-browser",
            adapter_name="fake",
        )

    async with session_scope() as session:
        failed = (
            await session.execute(
                select(LiveSessionEvent)
                .where(LiveSessionEvent.event_type == "session.connection_failed")
                .order_by(LiveSessionEvent.created_at.desc())
                .limit(1)
            )
        ).scalar_one()

    assert failed.payload_json == {
        "adapter": "fake",
        "failure_type": "RuntimeError",
        "detail": "provider rejected [redacted] with HTTP 429",
    }


@pytest.mark.asyncio
async def test_superseded_provider_cleanup_does_not_block_replacement_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accepted = await jarvis_coordinator.accept_text_turn(
        "Connection setup",
        idempotency_key="nonblocking-cleanup-setup",
    )
    current = await jarvis_coordinator.get_active_session()
    assert current is not None
    async with session_scope() as session:
        realtime = await session.get(LiveSession, current.id)
        assert realtime is not None
        realtime.provider_adapter = "slow-test-adapter"

    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    class SlowCleanupAdapter:
        async def close(self, **_kwargs) -> None:
            cleanup_started.set()
            await release_cleanup.wait()

    slow_adapter = SlowCleanupAdapter()

    def resolve_adapter(name: str):
        return slow_adapter if name == "slow-test-adapter" else fake_voice_session_adapter

    monkeypatch.setattr(
        "app.services.jarvis_coordinator.get_voice_session_adapter",
        resolve_adapter,
    )
    try:
        replacement = await asyncio.wait_for(
            jarvis_coordinator.bootstrap_controller(
                conversation_id=accepted.conversation.id,
                controller_id="replacement-browser",
                adapter_name="fake",
            ),
            timeout=2.0,
        )
        assert replacement.session.lifecycle_state == "ready"
        await asyncio.wait_for(cleanup_started.wait(), timeout=1.0)
        assert jarvis_coordinator._provider_cleanup_tasks
    finally:
        release_cleanup.set()
        for _ in range(20):
            if not jarvis_coordinator._provider_cleanup_tasks:
                break
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_wake_response_waits_for_permissive_verification() -> None:
    accepted = await jarvis_coordinator.accept_text_turn("Wake setup", idempotency_key="wake-setup")
    bootstrap = await jarvis_coordinator.bootstrap_controller(
        conversation_id=accepted.conversation.id,
        controller_id="wake-browser",
        adapter_name="fake",
        activation_context={"source": "wake_word", "wake_score": 0.2},
    )
    realtime = bootstrap.session
    _, initial_events = await jarvis_coordinator.replay_session_events(realtime.id)
    ready_event = next(event for event in initial_events if event.event_type == "provider.ready")
    assert ready_event.payload_json["session_id"] == realtime.id
    assert ready_event.payload_json["generation"] == realtime.generation

    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(event_id="wake-speaking", kind="user_speaking"),
    )
    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(
            event_id="wake-committed",
            kind="user_input_committed",
            payload={"item_id": "wake-item"},
        ),
    )
    _, before = await jarvis_coordinator.replay_session_events(realtime.id)
    assert not any(
        event.event_type == "response.arbiter.started"
        and (event.payload_json or {}).get("reason") == "user_turn"
        for event in before
    )

    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(
            event_id="wake-transcript",
            kind="user_transcript",
            payload={"item_id": "wake-item", "transcript": "Jarvis, remind me in ten minutes"},
        ),
    )
    _, after = await jarvis_coordinator.replay_session_events(realtime.id)
    assert any(event.event_type == "wake.gate.accepted" for event in after)
    assert any(
        event.event_type == "response.arbiter.started"
        and (event.payload_json or {}).get("reason") == "user_turn"
        for event in after
    )
    arbiter_event = next(
        event
        for event in after
        if event.event_type == "response.arbiter.started"
        and (event.payload_json or {}).get("reason") == "user_turn"
    )
    assert arbiter_event.payload_json["timing_ms"]["gate_wait"] >= 0
    assert arbiter_event.payload_json["timing_ms"]["candidate_sync"] >= 0
    assert arbiter_event.payload_json["timing_ms"]["state_checks"] >= 0
    assert arbiter_event.payload_json["timing_ms"]["situation_projection"] >= 0
    assert arbiter_event.payload_json["timing_ms"]["presentation_reservation"] >= 0
    assert arbiter_event.payload_json["timing_ms"]["situation_snapshot"] >= 0
    assert arbiter_event.payload_json["timing_ms"]["provider_submit"] >= 0
    assert arbiter_event.payload_json["timing_ms"]["total_to_provider_submit"] >= 0


@pytest.mark.asyncio
async def test_wake_response_accepts_transcript_before_audio_commit() -> None:
    accepted = await jarvis_coordinator.accept_text_turn(
        "Wake setup",
        idempotency_key="wake-transcript-first-setup",
    )
    bootstrap = await jarvis_coordinator.bootstrap_controller(
        conversation_id=accepted.conversation.id,
        controller_id="wake-browser-transcript-first",
        adapter_name="fake",
        activation_context={"source": "wake_word", "wake_score": 0.2},
    )
    realtime = bootstrap.session

    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(
            event_id="wake-transcript-first",
            kind="user_transcript",
            payload={"item_id": "wake-item", "transcript": "Jarvis, what is on my schedule"},
        ),
    )
    _, before = await jarvis_coordinator.replay_session_events(realtime.id)
    assert not any(event.event_type == "response.arbiter.started" for event in before)

    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(
            event_id="wake-commit-second",
            kind="user_input_committed",
            payload={"item_id": "wake-item"},
        ),
    )
    _, after = await jarvis_coordinator.replay_session_events(realtime.id)
    assert any(
        event.event_type == "response.arbiter.started"
        and (event.payload_json or {}).get("reason") == "user_turn"
        for event in after
    )


@pytest.mark.asyncio
async def test_normalized_non_wake_audio_commit_queues_a_follow_up_response() -> None:
    accepted = await jarvis_coordinator.accept_text_turn(
        "Start",
        idempotency_key="non-wake-commit-setup",
    )
    realtime = await jarvis_coordinator.get_active_session()
    assert realtime is not None

    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(
            event_id="non-wake-commit",
            kind="user_input_committed",
            payload={"item_id": "later-item"},
        ),
    )
    _, events = await jarvis_coordinator.replay_session_events(realtime.id)
    committed_index = next(
        index
        for index, event in enumerate(events)
        if event.event_type == "provider.user_input_committed"
    )
    assert any(
        event.event_type == "response.arbiter.started"
        for event in events[committed_index + 1:]
    )


@pytest.mark.asyncio
async def test_wake_verification_rejects_clear_incidental_mention() -> None:
    accepted = await jarvis_coordinator.accept_text_turn("Wake setup", idempotency_key="wake-incidental")
    bootstrap = await jarvis_coordinator.bootstrap_controller(
        conversation_id=accepted.conversation.id,
        controller_id="wake-browser",
        adapter_name="fake",
        activation_context={"source": "wake_word", "wake_score": 0.95},
    )
    realtime = bootstrap.session

    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(event_id="incidental-stopped", kind="user_stopped"),
    )
    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(
            event_id="incidental-transcript",
            kind="user_transcript",
            payload={"item_id": "incidental-item", "transcript": "I was talking about Jarvis"},
        ),
    )
    await asyncio.sleep(0.7)
    _, events = await jarvis_coordinator.replay_session_events(realtime.id)
    assert any(event.event_type == "wake.gate.rejected" for event in events)
    assert not any(
        event.event_type == "response.arbiter.started"
        and (event.payload_json or {}).get("reason") == "user_turn"
        for event in events
    )


@pytest.mark.asyncio
async def test_wake_gate_timeout_rejects_when_transcript_is_late(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.jarvis_coordinator._WAKE_GATE_TIMEOUT_SECONDS", 0.01)
    accepted = await jarvis_coordinator.accept_text_turn("Wake setup", idempotency_key="wake-grace")
    bootstrap = await jarvis_coordinator.bootstrap_controller(
        conversation_id=accepted.conversation.id,
        controller_id="wake-browser",
        adapter_name="fake",
        activation_context={"source": "wake_word", "wake_score": 0.19},
    )
    realtime = bootstrap.session

    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(event_id="late-transcript-stop", kind="user_stopped"),
    )
    await asyncio.sleep(0.2)
    _, events = await jarvis_coordinator.replay_session_events(realtime.id)
    assert any(event.event_type == "wake.gate.timed_out" for event in events)
    assert not any(
        event.event_type == "response.arbiter.started"
        and (event.payload_json or {}).get("reason") == "user_turn"
        for event in events
    )


@pytest.mark.asyncio
async def test_late_wake_transcript_does_not_strand_or_duplicate_first_response() -> None:
    accepted = await jarvis_coordinator.accept_text_turn(
        "Wake setup",
        idempotency_key="wake-late-transcript-regression",
    )
    bootstrap = await jarvis_coordinator.bootstrap_controller(
        conversation_id=accepted.conversation.id,
        controller_id="wake-late-transcript-browser",
        adapter_name="fake",
        activation_context={"source": "wake_word", "wake_score": 0.2},
    )
    realtime = bootstrap.session

    for event in (
        VoiceProviderEvent(event_id="late-first-speaking", kind="user_speaking"),
        VoiceProviderEvent(
            event_id="late-first-commit",
            kind="user_input_committed",
            payload={"item_id": "late-first-wake-item"},
        ),
        VoiceProviderEvent(event_id="late-first-stop", kind="user_stopped"),
        VoiceProviderEvent(
            event_id="late-first-delta",
            kind="user_transcript_delta",
            payload={
                "item_id": "late-first-user-item",
                "delta": "Jarvis",
                "transcript": "Jarvis",
            },
        ),
    ):
        await jarvis_coordinator.handle_provider_event(
            realtime.id,
            realtime.generation,
            event,
        )

    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(
            event_id="late-first-transcript",
            kind="user_transcript",
            payload={
                "item_id": "late-first-user-item",
                "transcript": "Jarvis, are you there?",
            },
        ),
    )

    _, events = await jarvis_coordinator.replay_session_events(realtime.id)
    user_responses = [
        event
        for event in events
        if event.event_type == "response.arbiter.started"
        and (event.payload_json or {}).get("reason") == "user_turn"
    ]
    assistant_responses = [
        event for event in events if event.event_type == "provider.assistant_response"
    ]
    assert len(user_responses) == 1
    assert len(assistant_responses) == 1


@pytest.mark.asyncio
async def test_wake_boundaries_request_once_then_normal_turns_continue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accepted = await jarvis_coordinator.accept_text_turn(
        "Wake setup",
        idempotency_key="wake-one-shot-regression",
    )
    create_calls: list[dict] = []

    async def capture_response(**kwargs) -> None:
        create_calls.append(kwargs)

    monkeypatch.setattr(fake_voice_session_adapter, "create_response", capture_response)
    bootstrap = await jarvis_coordinator.bootstrap_controller(
        conversation_id=accepted.conversation.id,
        controller_id="wake-one-shot-browser",
        adapter_name="fake",
        activation_context={"source": "wake_word", "wake_score": 0.2},
    )
    realtime = bootstrap.session

    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(
            event_id="one-shot-transcript-first",
            kind="user_transcript",
            payload={"item_id": "one-shot-item", "transcript": "Jarvis, are you there?"},
        ),
    )
    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(
            event_id="one-shot-commit",
            kind="user_input_committed",
            payload={"item_id": "one-shot-item"},
        ),
    )
    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(event_id="one-shot-stop", kind="user_stopped"),
    )
    assert len(create_calls) == 1

    first_request = create_calls[0]["request_id"]
    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(
            event_id="one-shot-generating",
            kind="assistant_generating",
            payload={
                "request_id": first_request,
                "response_id": "one-shot-response",
                "response_kind": "user_turn",
            },
        ),
    )
    assert (realtime.id, realtime.generation) in jarvis_coordinator._wake_activations
    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(event_id="one-shot-late-stop", kind="user_stopped"),
    )
    assert len(create_calls) == 1
    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(
            event_id="one-shot-interrupted",
            kind="assistant_interrupted",
            payload={
                "request_id": first_request,
                "response_id": "one-shot-response",
                "item_id": "one-shot-assistant-item",
                "response_kind": "user_turn",
                "transcript": "An unfinished ordinary response.",
                "heard_transcript": "",
                "interrupted": True,
            },
        ),
    )
    assert len(create_calls) == 1

    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(event_id="normal-follow-up-speaking", kind="user_speaking"),
    )
    assert (realtime.id, realtime.generation) not in jarvis_coordinator._wake_activations
    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(event_id="normal-follow-up-stop", kind="user_stopped"),
    )
    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(
            event_id="normal-follow-up-commit",
            kind="user_input_committed",
            payload={"item_id": "normal-follow-up-item"},
        ),
    )
    assert len(create_calls) == 2
    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(
            event_id="normal-follow-up-response",
            kind="assistant_response",
            payload={
                "request_id": create_calls[1]["request_id"],
                "response_id": "normal-follow-up-response",
                "item_id": "normal-follow-up-assistant-item",
                "response_kind": "user_turn",
                "transcript": "Yes.",
            },
        ),
    )


@pytest.mark.asyncio
async def test_deferred_wake_response_retries_only_after_real_speech_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accepted = await jarvis_coordinator.accept_text_turn(
        "Wake setup",
        idempotency_key="wake-deferred-retry-regression",
    )
    create_calls: list[dict] = []

    async def capture_response(**kwargs) -> None:
        create_calls.append(kwargs)

    monkeypatch.setattr(fake_voice_session_adapter, "create_response", capture_response)
    bootstrap = await jarvis_coordinator.bootstrap_controller(
        conversation_id=accepted.conversation.id,
        controller_id="wake-deferred-retry-browser",
        adapter_name="fake",
        activation_context={"source": "wake_word", "wake_score": 0.2},
    )
    realtime = bootstrap.session

    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(
            event_id="deferred-wake-commit",
            kind="user_input_committed",
            payload={"item_id": "deferred-wake-item"},
        ),
    )
    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(event_id="deferred-wake-stop", kind="user_stopped"),
    )
    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(event_id="continued-speech-start", kind="user_speaking"),
    )
    await asyncio.sleep(0.7)
    assert create_calls == []

    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(
            event_id="deferred-wake-transcript",
            kind="user_transcript",
            payload={"item_id": "deferred-wake-item", "transcript": "Jarvis, tell me"},
        ),
    )
    assert create_calls == []

    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(event_id="continued-speech-stop", kind="user_stopped"),
    )
    assert len(create_calls) == 1
    await jarvis_coordinator.handle_provider_event(
        realtime.id,
        realtime.generation,
        VoiceProviderEvent(
            event_id="continued-speech-response",
            kind="assistant_response",
            payload={
                "request_id": create_calls[0]["request_id"],
                "response_id": "continued-speech-response",
                "item_id": "continued-speech-assistant-item",
                "response_kind": "user_turn",
                "transcript": "Done.",
            },
        ),
    )


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
        ("assistant", "I understand."),
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
    assert messages[-1].content == "It hasn't started; the worker is offline."
    assert sum(item.content == "It hasn't started; the worker is offline." for item in messages) == 1
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
async def test_live_tool_call_uses_correlated_transcript_turn_not_older_history() -> None:
    accepted = await jarvis_coordinator.accept_text_turn(
        "An older unrelated request",
        idempotency_key="live-origin-old",
    )
    session = await jarvis_coordinator.get_active_session()
    assert session is not None
    await jarvis_coordinator.inject_fake_event(
        session_id=session.id,
        generation=session.generation,
        event_id="live-transcript-current",
        kind="user_transcript",
        payload={
            "item_id": "live-user:current",
            "transcript": "Prepare the correlated launch note",
            "start_ms": 100,
            "end_ms": 900,
        },
    )
    messages = await conversation_store.list_messages(accepted.conversation.id, final_only=True)
    current_turn = next(item for item in messages if item.content == "Prepare the correlated launch note")
    await jarvis_coordinator.inject_fake_event(
        session_id=session.id,
        generation=session.generation,
        event_id="live-origin-current",
        kind="delegation_origin_resolved",
        payload={
            "origin_correlation_id": "live-delegation:current",
            "origin_item_id": "live-user:current",
        },
    )
    await jarvis_coordinator.inject_fake_event(
        session_id=session.id,
        generation=session.generation,
        event_id="live-shell-current",
        kind="shell_call",
        payload={
            "call_id": "live-call-current",
            "name": "dispatch_task",
            "origin_item_id": "live-user:current",
            "origin_correlation_id": "live-delegation:current",
            "arguments": {"goal": "Prepare the correlated launch note", "intent_id": "launch-note"},
        },
    )
    tasks = await task_service.list_tasks()
    created = next(item.task for item in tasks if item.task.goal == "Prepare the correlated launch note")
    assert created.origin_turn_id == current_turn.id
    assert created.origin_turn_id != accepted.message.id


@pytest.mark.asyncio
async def test_live_commentary_submission_is_not_persisted_as_heard_delivery() -> None:
    await jarvis_coordinator.accept_text_turn(
        "Keep the update pending",
        idempotency_key="live-submission-not-delivery",
    )
    session = await jarvis_coordinator.get_active_session()
    assert session is not None
    async with session_scope() as db:
        before = len(list((await db.execute(
            select(SpeechDelivery).where(SpeechDelivery.live_session_id == session.id)
        )).scalars().all()))
    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(
            event_id="live-commentary-submitted",
            kind="assistant_update_submitted",
            payload={
                "request_id": "request-1",
                "presentation_ids": ["presentation-1"],
                "submitted_transcript": "The report is ready.",
                "submission_ack": "context_appended",
            },
        ),
    )
    async with session_scope() as db:
        deliveries = list((await db.execute(
            select(SpeechDelivery).where(SpeechDelivery.live_session_id == session.id)
        )).scalars().all())
    assert len(deliveries) == before
    refreshed = await jarvis_coordinator.get_session(session.id)
    assert refreshed is not None and refreshed.floor_state == "listening"


@pytest.mark.asyncio
async def test_provider_usage_does_not_take_the_voice_floor() -> None:
    await jarvis_coordinator.accept_text_turn(
        "Keep the floor idle",
        idempotency_key="provider-usage-neutral-floor",
    )
    session = await jarvis_coordinator.get_active_session()
    assert session is not None
    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(
            event_id="provider-usage-neutral-floor",
            kind="usage",
            payload={"usage": {"seconds": 1.0}},
        ),
    )
    refreshed = await jarvis_coordinator.get_session(session.id)
    assert refreshed is not None and refreshed.floor_state == "listening"


@pytest.mark.asyncio
async def test_live_tool_executes_unattributed_then_repairs_from_delegation_match() -> None:
    accepted = await jarvis_coordinator.accept_text_turn(
        "An older request that must not be reused",
        idempotency_key="live-late-origin-old",
    )
    session = await jarvis_coordinator.get_active_session()
    assert session is not None
    correlation_id = "live-delegation:provider:late"
    await jarvis_coordinator.inject_fake_event(
        session_id=session.id,
        generation=session.generation,
        event_id="live-shell-before-transcript",
        kind="shell_call",
        payload={
            "call_id": "live-call-before-transcript",
            "name": "dispatch_task",
            "origin_correlation_id": correlation_id,
            "origin_timeline": {"start_ms": 900, "end_ms": 950},
            "arguments": {"goal": "Prepare the late-correlated brief", "intent_id": "launch-brief"},
        },
    )
    tasks = await task_service.list_tasks()
    created = next(item.task for item in tasks if item.task.goal == "Prepare the late-correlated brief")
    assert created.origin_turn_id is None
    assert created.origin_turn_id != accepted.message.id

    await jarvis_coordinator.inject_fake_event(
        session_id=session.id,
        generation=session.generation,
        event_id="live-late-transcript",
        kind="user_transcript",
        payload={
            "item_id": "live-user:late",
            "transcript": "Prepare the late-correlated brief",
            "start_ms": 100,
            "end_ms": 850,
        },
    )
    messages = await conversation_store.list_messages(accepted.conversation.id, final_only=True)
    current_turn = next(item for item in messages if item.content == "Prepare the late-correlated brief")
    await jarvis_coordinator.inject_fake_event(
        session_id=session.id,
        generation=session.generation,
        event_id="live-late-origin-resolution",
        kind="delegation_origin_resolved",
        payload={
            "origin_correlation_id": correlation_id,
            "origin_item_id": "live-user:late",
            "delegation_timeline": {"start_ms": 900, "end_ms": 950},
            "transcript_timeline": {"start_ms": 100, "end_ms": 850},
        },
    )
    tasks = await task_service.list_tasks()
    repaired = next(item.task for item in tasks if item.task.id == created.id)
    assert repaired.origin_turn_id == current_turn.id


@pytest.mark.asyncio
async def test_live_assistant_transcript_is_history_not_delivery_evidence() -> None:
    accepted = await jarvis_coordinator.accept_text_turn(
        "Tell me the generated answer",
        idempotency_key="live-assistant-history",
    )
    session = await jarvis_coordinator.get_active_session()
    assert session is not None
    task = await task_service.dispatch(
        goal="Keep generated speech separate from delivery",
        origin_surface="jarvis",
        origin_conversation_id=accepted.conversation.id,
        origin_turn_id=accepted.message.id,
        idempotency_key="live-assistant-history-task",
    )
    await task_service.complete(
        task.task.id,
        summary="The generated-speech check is complete.",
        idempotency_key="live-assistant-history-task-complete",
    )
    reservation = await task_presentation_service.reserve(
        session_id=session.id,
        generation=session.generation,
        user_owns_floor=False,
    )
    assert reservation is not None
    async with session_scope() as db:
        before = len(list((await db.execute(
            select(SpeechDelivery).where(SpeechDelivery.live_session_id == session.id)
        )).scalars().all()))

    await jarvis_coordinator.inject_fake_event(
        session_id=session.id,
        generation=session.generation,
        event_id="live-assistant-segment",
        kind="assistant_transcript",
        payload={
            "item_id": "live-assistant:segment-1",
            "transcript": "This text was generated, but playback is unverified.",
            "start_ms": 1_000,
            "end_ms": 1_800,
            "fragments": [
                {
                    "delta": "This text was generated, but playback is unverified.",
                    "start_ms": 1_000,
                    "end_ms": 1_800,
                }
            ],
        },
    )

    messages = await conversation_store.list_messages(accepted.conversation.id, final_only=True)
    generated = next(
        item for item in messages
        if item.content == "This text was generated, but playback is unverified."
    )
    assert generated.role == "assistant"
    assert generated.communication_state == "generated"
    async with session_scope() as db:
        deliveries = list((await db.execute(
            select(SpeechDelivery).where(SpeechDelivery.live_session_id == session.id)
        )).scalars().all())
        persisted = (await db.execute(
            select(ConversationEvent).where(ConversationEvent.id == generated.source_event_id)
        )).scalar_one()
    assert len(deliveries) == before
    assert persisted.payload_json["playback_verified"] is False
    detail = await task_service.get_task(task.task.id)
    assert detail is not None
    presentation = next(
        item for item in detail.presentations if item.id in reservation.presentation_ids
    )
    assert presentation.state == "pending"


@pytest.mark.asyncio
async def test_native_live_transcript_retries_question_deferred_by_busy_floor(monkeypatch):
    accepted = await jarvis_coordinator.accept_text_turn("Begin", idempotency_key="native-floor")
    live = await jarvis_coordinator.get_active_session()
    task = await task_service.dispatch(goal="Recipe awaiting a choice", origin_surface="test",
        idempotency_key="native-floor-task", result_request={"mode": "answer"})
    question = await task_service.ask_question(task.task.id, question="Vegetarian option?", idempotency_key="native-floor-question")
    await task_presentation_service.sync_candidates()
    requests = []
    async def capture_request(**kwargs):
        requests.append(kwargs)
    monkeypatch.setattr(fake_voice_session_adapter, "create_response", capture_request)
    await jarvis_coordinator.handle_provider_event(live.id, live.generation,
        VoiceProviderEvent(event_id="native-floor-thinking", kind="assistant_generating"))
    await jarvis_coordinator._queue_controlled_response(live.id, live.generation, "task_update")
    assert not requests
    await jarvis_coordinator.handle_provider_event(live.id, live.generation,
        VoiceProviderEvent(event_id="native-floor-release", kind="assistant_transcript",
            payload={"item_id": "native-segment", "transcript": "Your recipe is admitted.", "start_ms": 100, "end_ms": 600}))
    assert len(requests) == 1
    assert any(fact.get("task_id") == task.task.id and fact.get("question") == question.question
        for fact in requests[0]["facts"])
    detail = await task_service.get_task(task.task.id)
    assert detail.questions[0].state == "asked"
    messages = await conversation_store.list_messages(accepted.conversation.id, final_only=True)
    assert next(message for message in messages if message.content == "Your recipe is admitted.").communication_state == "generated"


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
    assert "required_spoken_phrase" not in pending.required_facts_json


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

    await task_presentation_service.sync_candidates()
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
    assert candidate.state == "pending"
    assert candidate.required_facts_json is not None
    messages = await conversation_store.list_messages(accepted.conversation.id, final_only=True)
    assert messages[-2].content == "What changed?"
    assert messages[-1].role == "assistant"


@pytest.mark.asyncio
async def test_update_arriving_after_response_start_waits_for_completion() -> None:
    accepted = await jarvis_coordinator.accept_text_turn("Start a response", idempotency_key="late-origin")
    session = await jarvis_coordinator.get_active_session()
    assert session is not None
    gate = jarvis_coordinator._response_gates[(session.id, session.generation)]
    now = time.monotonic()
    gate.attempt = _ResponseAttempt(
        request_id="late-active-request",
        reason="user_turn",
        presentation_ids=(),
        state="speaking",
        started_monotonic=now,
        last_activity_monotonic=now,
        provider_response_id="late-current-response",
    )
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
    assert delivered is not None and delivered.presentations[0].state == "pending"


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
    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(
            event_id="retry-interrupted",
            kind="assistant_interrupted",
            payload={
                "response_id": "retry-response-1",
                "item_id": "retry-item-1",
                "transcript": "The retry task is complete.",
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
                "transcript": "The retry task is complete.",
                "presentation_ids": list(retry.presentation_ids),
            },
        ),
    )
    detail = await task_service.get_task(task.task.id)
    assert detail is not None
    candidate = next(item for item in detail.presentations if item.candidate_type == "terminal_result")
    assert candidate.state == "pending"
    assert candidate.retry_count == 0


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
    await jarvis_coordinator.handle_provider_event(session.id, session.generation,
        VoiceProviderEvent(event_id="floor-native-segment", kind="assistant_transcript",
            payload={"item_id": "floor-native", "transcript": "The earlier speech ended.", "start_ms": 100, "end_ms": 600}))
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
    assert delivered is not None and delivered.presentations[0].state == "pending"


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
            select(LiveSessionEvent)
            .where(LiveSessionEvent.session_id == session.id)
            .where(LiveSessionEvent.event_type == "provider.user_speaking")
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
    assert [fact["event_type"] for fact in reservation.facts] == ["task.reminder.due"]
    remaining = await task_presentation_service.list_pending()
    assert {
        item.required_facts_json["event_type"] for item in remaining
    } == {"task.completed", "task.requires_input", "task.failed", "task.reminder.due"}


@pytest.mark.asyncio
async def test_duplicate_reminder_stays_pending_without_model_report() -> None:
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
    assert presentation.state == "pending"
    assert "required_spoken_phrase" not in presentation.required_facts_json
    await route_pending_task_notifications(voice_connected=False)
    async def delivered_notification():
        while True:
            async with session_scope() as db:
                outbox = list((await db.execute(
                    select(NotificationOutbox).where(NotificationOutbox.task_id == reminder.task.id)
                )).scalars().all())
                live_notifications = list((await db.execute(
                    select(JarvisLiveEvent).where(JarvisLiveEvent.event_type == "notification.available")
                )).scalars().all())
            if outbox and outbox[0].status == "delivered":
                return outbox, live_notifications
            await asyncio.sleep(0.01)

    # Observe the committed transport outcome, not a machine-specific 50 ms
    # sleep. Slow SDK/emulator load must not turn delivery into a false failure.
    outbox, live_notifications = await asyncio.wait_for(delivered_notification(), 5)
    assert len(outbox) == 1
    assert outbox[0].status == "delivered"
    assert len(live_notifications) == 1


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
        await client.post("/api/auth/login", json={"password": ""})
        accepted = await client.post(
            "/api/jarvis/turns/text",
            json={"content": "API command", "idempotency_key": "api-command"},
        )
        assert accepted.status_code == 202
        state = await client.get("/api/jarvis/state")
        assert state.status_code == 200
        body = state.json()
        assert body["current_log"]["messages"][-1]["content"] == "I understand."
        assert body["session"]["provider_adapter"] == "fake"
        assert set(body["cursors"]) == {"conversation", "task", "session"}
        assert body["live_event_cursor"] == await jarvis_live_service.latest_cursor()
        situation_revision = body["situation_revision"]
        repeated_state = await client.get("/api/jarvis/state")
        assert repeated_state.status_code == 200
        assert repeated_state.json()["situation_revision"] == situation_revision

        conversation_events = await client.get(
            f"/api/jarvis/logs/{body['current_log']['id']}/events",
            params={"after": 0},
        )
        session_events = await client.get(
                f"/api/jarvis/live/sessions/{body['session']['id']}/events",
            params={"after": 0},
        )
        task_events = await client.get("/api/tasks/events", params={"after": 0})
        assert conversation_events.json()["latest_sequence"] == body["cursors"]["conversation"]
        assert session_events.json()["latest_sequence"] == body["cursors"]["session"]
        assert task_events.json()["latest_sequence"] == body["cursors"]["task"]

        observer = await client.post(
            "/api/jarvis/live/calls",
            json={"controller_id": "observer-tab", "role": "observer"},
        )
        assert observer.status_code == 200
        assert observer.json()["session"]["id"] == body["session"]["id"]
        assert observer.json()["request_id"]
        server_received_at = datetime.fromisoformat(observer.json()["server_received_at"])
        server_responded_at = datetime.fromisoformat(observer.json()["server_responded_at"])
        assert server_responded_at >= server_received_at

        telemetry = await client.post(
            "/api/jarvis/live/telemetry",
            json={
                "events": [{
                    "trace_id": "browser-trace-1",
                    "activation_id": "activation-1",
                    "source": "browser",
                    "event_type": "wake.detected",
                    "observed_at": "2026-09-05T00:00:00Z",
                    "monotonic_ms": 42.0,
                    "detail": {"attempt": 1},
                }],
            },
        )
        assert telemetry.status_code == 202
        assert telemetry.json() == {"accepted": 1}

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


@pytest.mark.asyncio
async def test_barge_in_requires_the_observed_response_identity() -> None:
    key = ("barge-session", 7)
    now = time.monotonic()
    gate = _ResponseGate()
    gate.attempt = _ResponseAttempt(
        request_id="request-1",
        reason="user_turn",
        presentation_ids=(),
        state="speaking",
        started_monotonic=now,
        last_activity_monotonic=now,
        provider_response_id="response-current",
    )
    jarvis_coordinator._response_gates[key] = gate
    try:
        stale = await jarvis_coordinator._capture_barge_in_intent(
            session_id=key[0],
            generation=key[1],
            speech_event_id="speech-stale",
            speech_item_id="item-2",
            source="browser",
            expected_request_id="request-1",
            expected_response_id="response-stale",
        )
        assert stale is None

        current = await jarvis_coordinator._capture_barge_in_intent(
            session_id=key[0],
            generation=key[1],
            speech_event_id="speech-current",
            speech_item_id="item-2",
            source="browser",
            expected_request_id="request-1",
            expected_response_id="response-current",
        )
        assert current is not None
        assert current.response_id_at_onset == "response-current"
    finally:
        jarvis_coordinator._response_gates.pop(key, None)
        jarvis_coordinator._barge_in_keys = {
            item for item in jarvis_coordinator._barge_in_keys if item[:2] != key
        }


def test_activation_identity_is_active_until_session_close_then_tombstoned() -> None:
    activation_id = "long-lived-activation"
    key = ("long-lived-session", 3)
    jarvis_coordinator._activation_index[activation_id] = (*key, float("inf"))
    try:
        jarvis_coordinator._prune_activation_index()
        assert jarvis_coordinator._activation_index[activation_id][2] == float("inf")

        jarvis_coordinator._tombstone_activation_ids(*key)
        expires_at = jarvis_coordinator._activation_index[activation_id][2]
        assert time.monotonic() < expires_at < float("inf")
    finally:
        jarvis_coordinator._activation_index.pop(activation_id, None)
