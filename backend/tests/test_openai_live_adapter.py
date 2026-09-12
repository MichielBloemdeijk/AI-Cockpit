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
        "type": "session.input_transcript.delta", "delta": "list ", "start_ms": 10, "end_ms": 20,
    })
    await adapter.handle_event("s", 1, {
        "type": "session.input_transcript.delta", "delta": "tasks", "start_ms": 20, "end_ms": 30,
    })
    await adapter.handle_event("s", 1, {
        "type": "response.event", "delegation_id": "delegation-a", "start_ms": 40,
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
    shell_events = [event for event in events if event.kind == "shell_call"]
    assert all(event.payload["origin_item_id"] is None for event in shell_events)
    assert len({event.payload["origin_correlation_id"] for event in shell_events}) == 1
    await adapter._flush_transcript(adapter._sessions[("s", 1)], "user")
    transcript = next(event for event in events if event.kind == "user_transcript")
    assert transcript.payload["transcript"] == "list tasks"
    assert transcript.payload["start_ms"] == 10
    assert transcript.payload["end_ms"] == 30
    resolved = next(event for event in events if event.kind == "delegation_origin_resolved")
    assert resolved.payload["origin_item_id"] == transcript.payload["item_id"]

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
    await adapter.handle_event("s", 3, {
        "type": "session.input_transcript.delta", "delta": "hello", "start_ms": 100, "end_ms": 300,
    })
    await adapter.handle_event("s", 3, {
        "type": "session.output_transcript.delta", "delta": "hi", "start_ms": 310, "end_ms": 400,
    })
    await adapter.handle_event("s", 3, {"type": "session.closed", "usage": {"duration": 8}})
    assert [event.kind for event in events] == [
        "user_transcript_delta", "assistant_transcript_delta", "user_transcript",
        "assistant_transcript", "usage", "disconnected"
    ]
    assert events[2].payload["fragments"] == [
        {"delta": "hello", "start_ms": 100, "end_ms": 300}
    ]
    assert events[3].payload["transcript"] == "hi"
    await adapter.close(session_id="s", generation=3, reason="test", emit_event=False)


@pytest.mark.asyncio
async def test_verified_task_update_uses_live_commentary_content(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    socket = FakeLiveSocket()

    async def create(**_: Any) -> dict[str, Any]:
        return {"session": {"id": "live_3"}, "transport": {"sdp": "answer"}}

    async def connect(*_: Any, **__: Any) -> FakeLiveSocket:
        return socket

    events: list[VoiceProviderEvent] = []

    async def on_event(_: str, __: int, event: VoiceProviderEvent) -> None:
        events.append(event)

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
    assert events[-1].kind == "assistant_update_submitted"
    assert events[-1].payload["submission_ack"] == "context_appended"
    assert "transcript" not in events[-1].payload
    await adapter.close(session_id="s", generation=4, reason="test", emit_event=False)


@pytest.mark.asyncio
async def test_silent_sideband_events_do_not_extend_idle_activity(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    socket = FakeLiveSocket()

    async def create(**_: Any) -> dict[str, Any]:
        return {"session": {"id": "live_idle"}, "transport": {"sdp": "answer"}}

    async def connect(*_: Any, **__: Any) -> FakeLiveSocket:
        return socket

    async def on_event(_: str, __: int, ___: VoiceProviderEvent) -> None:
        return None

    adapter = OpenAILiveAdapter(session_creator=create, websocket_connector=connect)
    await adapter.connect(session_id="s", generation=5, context={}, on_event=on_event, sdp_offer="offer")
    provider_session = adapter._sessions[("s", 5)]
    provider_session.last_activity_monotonic -= 10
    before = provider_session.last_activity_monotonic
    await adapter.handle_event("s", 5, {"type": "session.input_audio.activity", "level": 0})
    assert provider_session.last_activity_monotonic == before
    await adapter.handle_event("s", 5, {
        "type": "session.input_transcript.delta", "delta": "meaningful", "start_ms": 1, "end_ms": 2,
    })
    assert provider_session.last_activity_monotonic > before
    await adapter.close(session_id="s", generation=5, reason="test", emit_event=False)


@pytest.mark.asyncio
async def test_late_out_of_order_transcripts_match_multiple_delegation_timelines(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    socket = FakeLiveSocket()
    events: list[VoiceProviderEvent] = []

    async def create(**_: Any) -> dict[str, Any]:
        return {"session": {"id": "live_match"}, "transport": {"sdp": "answer"}}

    async def connect(*_: Any, **__: Any) -> FakeLiveSocket:
        return socket

    async def on_event(_: str, __: int, event: VoiceProviderEvent) -> None:
        events.append(event)

    adapter = OpenAILiveAdapter(session_creator=create, websocket_connector=connect)
    await adapter.connect(session_id="s", generation=6, context={}, on_event=on_event, sdp_offer="offer")
    for delegation_id, response_id, call_id, start_ms in (
        ("delegation-1", "response-1", "call-1", 500),
        ("delegation-2", "response-2", "call-2", 1_700),
    ):
        await adapter.handle_event("s", 6, {
            "type": "response.event", "delegation_id": delegation_id, "start_ms": start_ms,
            "event": {"type": "response.created", "response": {"id": response_id}},
        })
        await adapter.handle_event("s", 6, {
            "type": "response.event", "delegation_id": delegation_id, "start_ms": start_ms + 10,
            "event": {
                "type": "response.output_item.done", "response_id": response_id,
                "item": {"type": "function_call", "call_id": call_id, "name": "list_tasks", "arguments": "{}"},
            },
        })
    assert all(
        event.payload["origin_item_id"] is None
        for event in events if event.kind == "shell_call"
    )

    # Network arrival order is intentionally the reverse of timeline order.
    await adapter.handle_event("s", 6, {
        "type": "session.input_transcript.delta", "delta": "second", "start_ms": 1_300, "end_ms": 1_500,
    })
    await adapter.handle_event("s", 6, {
        "type": "session.input_transcript.delta", "delta": "first", "start_ms": 100, "end_ms": 400,
    })
    await adapter._flush_transcript(adapter._sessions[("s", 6)], "user")

    transcripts = {
        event.payload["transcript"]: event.payload["item_id"]
        for event in events if event.kind == "user_transcript"
    }
    resolutions = {
        event.payload["delegation_id"]: event.payload["origin_item_id"]
        for event in events if event.kind == "delegation_origin_resolved"
    }
    assert resolutions == {
        "delegation-1": transcripts["first"],
        "delegation-2": transcripts["second"],
    }
    await adapter.close(session_id="s", generation=6, reason="test", emit_event=False)


@pytest.mark.asyncio
async def test_ambiguous_timeline_match_stays_unattributed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    socket = FakeLiveSocket()
    events: list[VoiceProviderEvent] = []

    async def create(**_: Any) -> dict[str, Any]:
        return {"session": {"id": "live_ambiguous"}, "transport": {"sdp": "answer"}}

    async def connect(*_: Any, **__: Any) -> FakeLiveSocket:
        return socket

    async def on_event(_: str, __: int, event: VoiceProviderEvent) -> None:
        events.append(event)

    adapter = OpenAILiveAdapter(session_creator=create, websocket_connector=connect)
    await adapter.connect(session_id="s", generation=7, context={}, on_event=on_event, sdp_offer="offer")
    await adapter.handle_event("s", 7, {
        "type": "response.event", "delegation_id": "ambiguous", "start_ms": 2_000,
        "event": {"type": "response.created", "response": {"id": "response"}},
    })
    await adapter.handle_event("s", 7, {
        "type": "session.input_transcript.delta", "delta": "before", "start_ms": 0, "end_ms": 1_000,
    })
    await adapter.handle_event("s", 7, {
        "type": "session.input_transcript.delta", "delta": "overlap", "start_ms": 1_800, "end_ms": 3_000,
    })
    await adapter._flush_transcript(adapter._sessions[("s", 7)], "user")
    assert not any(event.kind == "delegation_origin_resolved" for event in events)
    await adapter.close(session_id="s", generation=7, reason="test", emit_event=False)


@pytest.mark.asyncio
async def test_response_completion_does_not_end_assistant_transcript(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    socket = FakeLiveSocket()
    events: list[VoiceProviderEvent] = []

    async def create(**_: Any) -> dict[str, Any]:
        return {"session": {"id": "live_assistant"}, "transport": {"sdp": "answer"}}

    async def connect(*_: Any, **__: Any) -> FakeLiveSocket:
        return socket

    async def on_event(_: str, __: int, event: VoiceProviderEvent) -> None:
        events.append(event)

    adapter = OpenAILiveAdapter(session_creator=create, websocket_connector=connect)
    await adapter.connect(session_id="s", generation=8, context={}, on_event=on_event, sdp_offer="offer")
    await adapter.handle_event("s", 8, {
        "type": "session.output_transcript.delta", "delta": "Still speaking", "start_ms": 100, "end_ms": 300,
    })
    await adapter.handle_event("s", 8, {
        "type": "response.event", "delegation_id": "d", "start_ms": 150,
        "event": {"type": "response.completed", "response": {"id": "r"}},
    })
    assert not any(event.kind == "assistant_transcript" for event in events)
    await adapter.handle_event("s", 8, {
        "type": "session.output_transcript.delta", "delta": " after completion", "start_ms": 300, "end_ms": 500,
    })
    await adapter.handle_event("s", 8, {"type": "session.closed"})
    segment = next(event for event in events if event.kind == "assistant_transcript")
    assert segment.payload["transcript"] == "Still speaking after completion"
    await adapter.close(session_id="s", generation=8, reason="test", emit_event=False)
