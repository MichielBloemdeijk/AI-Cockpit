from __future__ import annotations

import asyncio
import pytest
from sqlalchemy import select

import app.services.jarvis_coordinator as coordinator_module
from app.db.session import session_scope
from app.db.tables import LiveSessionEvent
from app.services.conversation_store import conversation_store
from app.services.jarvis_coordinator import jarvis_coordinator
from app.services.voice_session_adapter import VoiceProviderEvent, fake_voice_session_adapter


@pytest.mark.asyncio
async def test_pending_wake_gate_blocks_response_until_direct_transcript() -> None:
    accepted = await jarvis_coordinator.accept_text_turn(
        "Gate setup",
        idempotency_key="gate-blocking-setup",
    )
    bootstrap = await jarvis_coordinator.bootstrap_controller(
        conversation_id=accepted.conversation.id,
        controller_id="gate-browser",
        adapter_name="fake",
        activation_context={"source": "wake_word", "wake_score": 0.99},
    )
    session = bootstrap.session

    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(event_id="gate-stop", kind="user_stopped"),
    )
    await jarvis_coordinator._queue_controlled_response(
        session.id,
        session.generation,
        "user_turn",
    )
    _, blocked_events = await jarvis_coordinator.replay_session_events(session.id)
    assert not any(event.event_type == "response.arbiter.started" for event in blocked_events)

    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(
            event_id="gate-transcript",
            kind="user_transcript",
            payload={"item_id": "gate-item", "transcript": "Hey Jarvis, are you there?"},
        ),
    )
    _, accepted_events = await jarvis_coordinator.replay_session_events(session.id)
    assert any(event.event_type == "wake.gate.accepted" for event in accepted_events)
    assert any(event.event_type == "response.arbiter.started" for event in accepted_events)


@pytest.mark.asyncio
async def test_strong_score_gate_acceptance_emits_privacy_safe_diagnostics() -> None:
    accepted = await jarvis_coordinator.accept_text_turn(
        "Strong score setup",
        idempotency_key="strong-score-setup",
    )
    bootstrap = await jarvis_coordinator.bootstrap_controller(
        conversation_id=accepted.conversation.id,
        controller_id="strong-score-browser",
        adapter_name="fake",
        activation_context={"source": "wake_word", "wake_score": 0.928},
    )
    session = bootstrap.session
    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(event_id="strong-stop", kind="user_stopped"),
    )
    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(
            event_id="strong-transcript",
            kind="user_transcript",
            payload={"item_id": "strong-item", "transcript": "Remind me in ten minutes"},
        ),
    )
    _, events = await jarvis_coordinator.replay_session_events(session.id)
    gate_event = next(event for event in events if event.event_type == "wake.gate.accepted")
    assert gate_event.payload_json is not None
    assert gate_event.payload_json["reason"] == "strong_wake_candidate"
    assert gate_event.payload_json["wake_term_present"] is False
    assert gate_event.payload_json["prefix_classification"] == "missing_prefix"
    assert gate_event.payload_json["transcript_word_count"] == 5
    assert "transcript" not in gate_event.payload_json


@pytest.mark.asyncio
async def test_wake_gate_timeout_rejects_and_discards_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(coordinator_module, "_WAKE_GATE_TIMEOUT_SECONDS", 0.01)
    accepted = await jarvis_coordinator.accept_text_turn(
        "Timeout setup",
        idempotency_key="gate-timeout-setup",
    )
    bootstrap = await jarvis_coordinator.bootstrap_controller(
        conversation_id=accepted.conversation.id,
        controller_id="timeout-browser",
        adapter_name="fake",
        activation_context={"source": "wake_word", "wake_score": 0.99},
    )
    session = bootstrap.session
    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(
            event_id="timeout-commit",
            kind="user_input_committed",
            payload={"item_id": "timeout-item"},
        ),
    )
    await asyncio.sleep(0.2)
    _, events = await jarvis_coordinator.replay_session_events(session.id)
    assert any(event.event_type == "wake.gate.timed_out" for event in events)
    assert not any(event.event_type == "response.arbiter.started" for event in events)
    assert any(
        item_ids == ("timeout-item",)
        for _session_id, _generation, item_ids in fake_voice_session_adapter.discarded_candidate_items
    )


@pytest.mark.asyncio
async def test_rejected_candidate_transcript_is_redacted_and_not_in_conversation() -> None:
    accepted = await jarvis_coordinator.accept_text_turn(
        "Reject setup",
        idempotency_key="gate-reject-setup",
    )
    bootstrap = await jarvis_coordinator.bootstrap_controller(
        conversation_id=accepted.conversation.id,
        controller_id="reject-browser",
        adapter_name="fake",
        activation_context={"source": "wake_word", "wake_score": 0.99},
    )
    session = bootstrap.session
    await jarvis_coordinator.handle_provider_event(
        session.id,
        session.generation,
        VoiceProviderEvent(
            event_id="reject-transcript",
            kind="user_transcript",
            payload={
                "item_id": "reject-item",
                "transcript": "I was talking about Jarvis yesterday",
            },
        ),
    )
    messages = await conversation_store.list_messages(accepted.conversation.id, final_only=True)
    assert all("talking about Jarvis" not in item.content for item in messages)
    async with session_scope() as db_session:
        event = (
            await db_session.execute(
                select(LiveSessionEvent)
                .where(LiveSessionEvent.session_id == session.id)
                .where(LiveSessionEvent.event_type == "provider.user_transcript")
            )
        ).scalar_one()
        assert event.payload_json is not None
        assert event.payload_json.get("transcript_redacted") is True
        assert "transcript" not in event.payload_json
