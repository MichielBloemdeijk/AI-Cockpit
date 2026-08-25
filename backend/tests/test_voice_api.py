from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from types import SimpleNamespace

from app.main import app
import app.api.voice as voice_api_module
from app.services.conversation_store import conversation_store


@pytest.mark.asyncio
async def test_voice_conversation_and_notification_routes_are_authenticated_and_durable() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.get("/api/voice/conversation")).status_code == 401
        await client.post("/api/auth/login", json={"password": "dev"})

        conversation = await client.get("/api/voice/conversation")
        assert conversation.status_code == 200
        payload = conversation.json()
        assert payload["is_primary_voice"] is True

        health = await client.get("/api/voice/health")
        assert health.status_code == 200
        assert set(health.json()) == {"enabled", "wake_word", "llm", "stt", "tts"}

        vapid = await client.get("/api/notifications/vapid-public-key")
        assert vapid.status_code == 200
        assert set(vapid.json()) == {"enabled", "public_key"}

        test_event = await client.post("/api/notifications/test")
        assert test_event.status_code == 200
        assert test_event.json()["event_id"]


@pytest.mark.asyncio
async def test_wake_word_acknowledgement_is_persisted_once_in_the_primary_voice_conversation() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": "dev"})
        conversation = (await client.get("/api/voice/conversation")).json()
        response = await client.post("/api/voice/acknowledgements")

    assert response.status_code == 200
    message = response.json()
    assert message["role"] == "assistant"
    assert message["author_label"] == "Jarvis"
    assert message["content"] == "Yes?"
    assert message["source_event_id"]

    messages = await conversation_store.list_messages(conversation["conversation_id"], final_only=True)
    events = await conversation_store.list_events(conversation["conversation_id"])
    assert [item.content for item in messages] == ["Yes?"]
    acknowledgement = next(item for item in events if item.event_type == "voice.acknowledgement")
    assert acknowledgement.id == message["source_event_id"]
    assert acknowledgement.payload_json == {"content": "Yes?", "speech_text": "Yes?"}


@pytest.mark.asyncio
async def test_voice_conversation_cursor_uses_unbounded_max_sequence_query(monkeypatch) -> None:
    conversation = SimpleNamespace(id="conversation", title="Jarvis", is_primary_voice=True)
    calls: list[str] = []

    async def fake_primary():
        return conversation

    async def fake_max_sequence(conversation_id: str) -> int:
        calls.append(conversation_id)
        return 6001

    async def bounded_list_should_not_run(*args, **kwargs):
        raise AssertionError("voice cursor must not load a bounded event list")

    monkeypatch.setattr(voice_api_module, "get_or_create_primary", fake_primary)
    monkeypatch.setattr(voice_api_module.conversation_store, "get_latest_event_sequence", fake_max_sequence)
    monkeypatch.setattr(voice_api_module.conversation_store, "list_events", bounded_list_should_not_run)

    response = await voice_api_module.voice_conversation()

    assert response.latest_sequence == 6001
    assert calls == ["conversation"]


@pytest.mark.asyncio
async def test_voice_state_captures_replay_cursor_before_projection_reads(monkeypatch) -> None:
    conversation = SimpleNamespace(id="conversation", title="Jarvis", is_primary_voice=True)
    order: list[str] = []

    async def fake_primary():
        return conversation

    async def fake_max_sequence(_conversation_id: str) -> int:
        order.append("cursor")
        return 41

    async def fake_tasks(_conversation_id: str, *, include_finished: bool):
        assert include_finished is False
        order.append("tasks")
        return []

    async def fake_runs(_conversation_id: str):
        order.append("runs")
        return []

    monkeypatch.setattr(voice_api_module, "get_or_create_primary", fake_primary)
    monkeypatch.setattr(voice_api_module, "list_tasks", fake_tasks)
    monkeypatch.setattr(voice_api_module.conversation_store, "get_latest_event_sequence", fake_max_sequence)
    monkeypatch.setattr(voice_api_module.conversation_store, "list_runs", fake_runs)

    response = await voice_api_module.voice_state()

    assert response.latest_sequence == 41
    assert response.conversation.latest_sequence == 41
    assert order == ["cursor", "tasks", "runs"]
