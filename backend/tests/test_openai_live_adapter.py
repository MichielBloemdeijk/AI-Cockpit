from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
import httpx

from app.config import settings
from app.services.openai_live_adapter import OpenAILiveAdapter
from app.services.voice_session_adapter import VoiceProviderEvent


class FakeLiveSocket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        value = await self.incoming.get()
        if value is None:
            raise StopAsyncIteration
        return value

    async def send(self, message: str) -> None:
        payload = json.loads(message)
        self.sent.append(payload)
        if payload.get("type") == "session.close":
            await self.incoming.put(json.dumps({"type": "session.closed", "usage": {"duration": 3.5}}))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        await self.incoming.put(None)


@pytest.mark.asyncio
async def test_default_creator_posts_documented_session_and_transport(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    socket = FakeLiveSocket()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201,
            json={"session": {"id": "live_http"}, "transport": {"type": "webrtc", "sdp": "answer"}},
        )

    async def connect(*_: Any, **__: Any) -> FakeLiveSocket:
        return socket

    async def on_event(_: str, __: int, ___: VoiceProviderEvent) -> None:
        return None

    adapter = OpenAILiveAdapter(
        websocket_connector=connect,
        http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await adapter.connect(session_id="s", generation=9, context={}, on_event=on_event, sdp_offer="offer")
    assert requests[0].url.path == "/v1/live/sessions"
    body = json.loads(requests[0].content)
    assert body["transport"] == {"type": "webrtc", "sdp": "offer"}
    assert body["session"]["delegation"]["type"] == "responses"
    assert requests[0].headers["Authorization"] == "Bearer test-key"
    await adapter.close(session_id="s", generation=9, reason="test", emit_event=False)


@pytest.mark.asyncio
async def test_connect_uses_live_session_shape_and_waits_for_real_started_event(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    socket = FakeLiveSocket()
    created_args: dict[str, Any] = {}
    socket_args: dict[str, Any] = {}
    events: list[VoiceProviderEvent] = []

    async def create(**kwargs: Any) -> dict[str, Any]:
        created_args.update(kwargs)
        return {
            "session": {"id": "live_opaque/id"},
            "transport": {"type": "webrtc", "sdp": "answer-sdp"},
        }

    async def connect(url: str, **kwargs: Any) -> FakeLiveSocket:
        socket_args.update({"url": url, **kwargs})
        return socket

    async def on_event(_: str, __: int, event: VoiceProviderEvent) -> None:
        events.append(event)

    adapter = OpenAILiveAdapter(session_creator=create, websocket_connector=connect)
    connection = await adapter.connect(
        session_id="local",
        generation=2,
        context={"activation_id": "activation-1"},
        on_event=on_event,
        sdp_offer="offer-sdp",
    )
    assert connection.sdp_answer == "answer-sdp"
    assert connection.provider_session_id == "live_opaque/id"
    assert created_args["sdp_offer"] == "offer-sdp"
    config = created_args["session_config"]
    assert config["model"] == "gpt-live-1"
    assert config["audio"]["output"]["voice"] == "marin"
    assert config["delegation"]["responses"]["model"] == "gpt-5.6-luna"
    assert config["delegation"]["responses"]["reasoning"] == {"effort": "low"}
    assert config["delegation"]["responses"]["parallel_tool_calls"] is False
    assert socket_args["url"].endswith("/live_opaque%2Fid/attach")
    assert events == []

    await adapter.handle_event("local", 2, {"type": "session.started", "event_id": "started"})
    assert [event.kind for event in events] == ["ready"]
    await adapter.close(session_id="local", generation=2, reason="test", emit_event=False)
    assert socket.closed


@pytest.mark.asyncio
async def test_nested_responses_calls_are_idempotent_and_continue_once(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    socket = FakeLiveSocket()
    events: list[VoiceProviderEvent] = []

    async def create(**_: Any) -> dict[str, Any]:
        return {"session": {"id": "live_1"}, "transport": {"sdp": "answer"}}

    async def connect(*_: Any, **__: Any) -> FakeLiveSocket:
        return socket

    async def on_event(_: str, __: int, event: VoiceProviderEvent) -> None:
        events.append(event)

    adapter = OpenAILiveAdapter(session_creator=create, websocket_connector=connect)
    await adapter.connect(session_id="s", generation=1, context={}, on_event=on_event, sdp_offer="offer")
    await adapter.handle_event("s", 1, {
        "type": "response.event", "delegation_id": "delegation-a",
        "event": {"type": "response.created", "response": {"id": "resp-1"}},
    })
    for call_id in ("call-1", "call-2"):
        event = {
            "type": "response.event", "delegation_id": "delegation-a",
            "event": {
                "type": "response.output_item.done", "response_id": "resp-1",
                "item": {"type": "function_call", "call_id": call_id, "name": "list_tasks", "arguments": "{}"},
            },
        }
        await adapter.handle_event("s", 1, event)
        await adapter.handle_event("s", 1, event)
    assert [event.kind for event in events].count("shell_call") == 2
    assert {event.payload["delegation_id"] for event in events if event.kind == "shell_call"} == {"delegation-a"}

    await adapter.send_tool_result(session_id="s", generation=1, call_id="call-1", output={"ok": True})
    await adapter.send_tool_result(session_id="s", generation=1, call_id="call-1", output={"ok": True})
    assert [item["type"] for item in socket.sent].count("response.item.create") == 1
    assert [item["type"] for item in socket.sent].count("response.create") == 0

    await adapter.handle_event("s", 1, {
        "type": "response.event", "delegation_id": "delegation-a",
        "event": {"type": "response.completed", "response": {"id": "resp-1"}},
    })
    await adapter.send_tool_result(session_id="s", generation=1, call_id="call-2", output={"ok": True})
    await adapter.send_tool_result(session_id="s", generation=1, call_id="call-2", output={"ok": True})
    assert [item["type"] for item in socket.sent].count("response.item.create") == 2
    assert [item["type"] for item in socket.sent].count("response.create") == 1
    await adapter.close(session_id="s", generation=1, reason="test", emit_event=False)


@pytest.mark.asyncio
async def test_live_transcripts_and_final_usage_are_normalized(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    socket = FakeLiveSocket()
    events: list[VoiceProviderEvent] = []

    async def create(**_: Any) -> dict[str, Any]:
        return {"session": {"id": "live_2"}, "transport": {"sdp": "answer"}}

    async def connect(*_: Any, **__: Any) -> FakeLiveSocket:
        return socket

    async def on_event(_: str, __: int, event: VoiceProviderEvent) -> None:
        events.append(event)

    adapter = OpenAILiveAdapter(session_creator=create, websocket_connector=connect)
    await adapter.connect(session_id="s", generation=3, context={}, on_event=on_event, sdp_offer="offer")
    await adapter.handle_event("s", 3, {"type": "session.input_transcript.delta", "delta": "hello"})
    await adapter.handle_event("s", 3, {"type": "session.output_transcript.delta", "delta": "hi"})
    await adapter.handle_event("s", 3, {"type": "session.closed", "usage": {"duration": 8}})
    assert [event.kind for event in events] == [
        "user_transcript_delta", "assistant_transcript_delta", "usage", "disconnected"
    ]
    await adapter.close(session_id="s", generation=3, reason="test", emit_event=False)


@pytest.mark.asyncio
async def test_verified_task_update_uses_live_commentary_content(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    socket = FakeLiveSocket()

    async def create(**_: Any) -> dict[str, Any]:
        return {"session": {"id": "live_3"}, "transport": {"sdp": "answer"}}

    async def connect(*_: Any, **__: Any) -> FakeLiveSocket:
        return socket

    async def on_event(_: str, __: int, ___: VoiceProviderEvent) -> None:
        return None

    adapter = OpenAILiveAdapter(session_creator=create, websocket_connector=connect)
    await adapter.connect(session_id="s", generation=4, context={}, on_event=on_event, sdp_offer="offer")
    await adapter.create_response(
        session_id="s",
        generation=4,
        request_id="update-1",
        response_kind="task_update",
        presentation_ids=(),
        facts=({"required_spoken_phrase": "The report is ready."},),
        context={},
    )
    commentary = socket.sent[-1]
    assert commentary["type"] == "session.commentary.append"
    assert commentary["delegation_id"] is None
    assert commentary["content"].endswith("The report is ready.")
    assert "commentary" not in commentary
    await adapter.handle_event("s", 4, {
        "type": "session.commentary.appended",
        "client_event_id": commentary["event_id"],
    })
    await adapter.close(session_id="s", generation=4, reason="test", emit_event=False)
