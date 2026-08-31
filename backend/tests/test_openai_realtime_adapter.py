from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.config import settings
from app.services.openai_realtime_adapter import (
    OpenAIRealtimeAdapter,
    OpenAIRealtimeConfigurationError,
)


class FakeSideband:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.incoming.put_nowait(json.dumps({"type": "session.created", "event_id": "evt-ready"}))

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        message = await self.incoming.get()
        if message is None:
            raise StopAsyncIteration
        return message

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self.incoming.put_nowait(None)

    async def feed(self, event: dict[str, Any]) -> None:
        await self.incoming.put(json.dumps(event))
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_openai_adapter_bootstraps_sideband_hydrates_and_returns_tool_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "test-secret-key")
    requests: list[httpx.Request] = []
    call_requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    async def create_call(**kwargs: Any) -> httpx.Response:
        call_requests.append(kwargs)
        return httpx.Response(
            201,
            text="v=0\r\na=answer",
            headers={"Location": "/v1/realtime/calls/rtc_test_call"},
        )

    socket = FakeSideband()
    websocket_calls: list[tuple[str, dict[str, Any]]] = []

    async def connect_socket(url: str, **kwargs: Any) -> FakeSideband:
        websocket_calls.append((url, kwargs))
        return socket

    adapter = OpenAIRealtimeAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.openai.com",
        ),
        websocket_connector=connect_socket,
        realtime_call_creator=create_call,
    )
    events = []

    async def on_event(session_id, generation, event):
        events.append((session_id, generation, event))

    connection = await adapter.connect(
        session_id="internal-session",
        generation=3,
        context={
            "dialogue": [
                {"role": "user", "content": "Earlier question"},
                {"role": "assistant", "content": "Earlier answer"},
            ],
            "tasks": [{"task_id": "task-1", "status": "running"}],
            "questions": [],
        },
        on_event=on_event,
        sdp_offer="v=0\r\na=offer\r\n",
    )

    assert connection.sdp_answer == "v=0\r\na=answer"
    assert events[0][2].kind == "ready"
    assert websocket_calls[0][0].endswith("?call_id=rtc_test_call")
    assert websocket_calls[0][1]["additional_headers"]["Authorization"] == "Bearer test-secret-key"
    assert call_requests[0]["api_key"] == "test-secret-key"
    assert call_requests[0]["sdp_offer"] == "v=0\r\na=offer\r\n"
    session_config = call_requests[0]["session_config"]
    assert session_config["model"] == "gpt-realtime-2.1"
    assert session_config["audio"]["output"]["voice"] == "marin"
    assert session_config["reasoning"]["effort"] == "low"
    assert session_config["audio"]["input"]["noise_reduction"]["type"] == "near_field"
    assert session_config["audio"]["input"]["turn_detection"] == {
        "type": "semantic_vad",
        "eagerness": "low",
        "create_response": True,
        "interrupt_response": True,
    }
    assert session_config["audio"]["input"]["transcription"]["model"] == "gpt-live-transcribe"
    assert "multi-step or substantial analysis with dispatch_task" in session_config["instructions"]
    assert "has no permission to browse, edit files, run commands" in session_config["instructions"]
    assert "two to eight words for simple acknowledgements" in session_config["instructions"]
    assert "Do not restate the request" in session_config["instructions"]
    assert {tool["name"] for tool in session_config["tools"]} == {
        "dispatch_task",
        "list_tasks",
        "get_task",
        "answer_task_question",
        "cancel_task",
        "create_reminder",
        "list_reminders",
        "snooze_reminder",
        "history_grep",
        "set_jarvis_voice",
        "start_new_jarvis_log",
        "end_voice_session",
    }
    assert [message["item"]["role"] for message in socket.sent[:2]] == ["user", "assistant"]

    await adapter.send_user_text(
        session_id="internal-session",
        generation=3,
        turn_id="turn-1",
        text="Typed continuation",
    )
    await adapter.send_tool_result(
        session_id="internal-session",
        generation=3,
        call_id="call-1",
        output={"ok": True, "result": {"task_id": "task-1"}},
    )
    assert socket.sent[-4]["item"]["content"][0]["text"] == "Typed continuation"
    assert socket.sent[-3]["type"] == "response.create"
    assert socket.sent[-2]["item"]["type"] == "function_call_output"
    assert socket.sent[-1]["type"] == "response.create"

    await adapter.close(
        session_id="internal-session",
        generation=3,
        reason="test_complete",
    )
    assert socket.closed is True
    assert requests[-1].url.path == "/v1/realtime/calls/rtc_test_call/hangup"
    assert events[-1][2].kind == "disconnected"
    assert not hasattr(connection, "call_id")


@pytest.mark.asyncio
async def test_openai_adapter_normalizes_current_realtime_event_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "test-secret-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    async def create_call(**kwargs: Any) -> httpx.Response:
        return httpx.Response(201, text="answer", headers={"Location": "/v1/realtime/calls/rtc_events"})

    socket = FakeSideband()

    async def connect_socket(url: str, **kwargs: Any) -> FakeSideband:
        return socket

    adapter = OpenAIRealtimeAdapter(
        http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        websocket_connector=connect_socket,
        realtime_call_creator=create_call,
    )
    events = []

    async def on_event(session_id, generation, event):
        events.append(event)

    await adapter.connect(
        session_id="session-events",
        generation=1,
        context={},
        on_event=on_event,
        sdp_offer="offer",
    )
    await socket.feed({"type": "input_audio_buffer.speech_started", "event_id": "evt-1"})
    await socket.feed({"type": "input_audio_buffer.speech_stopped", "event_id": "evt-2"})
    await socket.feed(
        {
            "type": "conversation.item.input_audio_transcription.delta",
            "event_id": "evt-2a",
            "item_id": "item-user",
            "delta": "Please prepare ",
        }
    )
    await socket.feed(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "event_id": "evt-3",
            "item_id": "item-user",
            "transcript": "Please prepare a report.",
        }
    )
    await socket.feed(
        {"type": "response.created", "event_id": "evt-4", "response": {"id": "resp-1"}}
    )
    await socket.feed(
        {
            "type": "response.output_audio.delta",
            "event_id": "evt-5",
            "response_id": "resp-1",
            "delta": "ignored-audio",
        }
    )
    await socket.feed(
        {
            "type": "response.output_audio_transcript.delta",
            "event_id": "evt-6",
            "response_id": "resp-1",
            "item_id": "item-assistant",
            "delta": "I can ",
        }
    )
    await socket.feed(
        {
            "type": "response.output_audio_transcript.done",
            "event_id": "evt-7",
            "response_id": "resp-1",
            "item_id": "item-assistant",
            "transcript": "I can help with that.",
        }
    )
    await socket.feed(
        {
            "type": "response.done",
            "event_id": "evt-7a",
            "response": {
                "id": "resp-1",
                "status": "completed",
                "output": [
                    {
                        "id": "item-assistant",
                        "type": "message",
                        "content": [
                            {"type": "audio", "transcript": "I can help with that."}
                        ],
                    }
                ],
            },
        }
    )
    await socket.feed(
        {
            "type": "output_audio_buffer.stopped",
            "event_id": "evt-7b",
            "response_id": "resp-1",
        }
    )
    await socket.feed(
        {
            "type": "response.done",
            "event_id": "evt-8",
            "response": {
                "id": "resp-tool",
                "status": "completed",
                "output": [
                    {
                        "id": "item-tool",
                        "type": "function_call",
                        "call_id": "call-tool",
                        "name": "dispatch_task",
                        "arguments": '{"goal":"Prepare a report"}',
                    }
                ],
            },
        }
    )
    await asyncio.sleep(0)

    assert [event.kind for event in events] == [
        "ready",
        "user_speaking",
        "user_stopped",
        "user_transcript_delta",
        "user_transcript",
        "assistant_generating",
        "assistant_speaking",
        "assistant_transcript_delta",
        "assistant_response",
        "shell_call",
    ]
    assert events[3].payload == {
        "item_id": "item-user",
        "delta": "Please prepare ",
        "transcript": "Please prepare ",
        "provisional": True,
    }
    assert events[4].payload["transcript"] == "Please prepare a report."
    assert events[7].payload["delta"] == "I can "
    assert events[8].payload["transcript"] == "I can help with that."
    assert events[9].payload["arguments"] == {"goal": "Prepare a report"}
    await adapter.close(session_id="session-events", generation=1, reason="done")


@pytest.mark.asyncio
async def test_openai_adapter_uses_out_of_band_timing_and_tracks_presented_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "test-secret-key")

    async def create_call(**kwargs: Any) -> httpx.Response:
        return httpx.Response(
            201,
            text="answer",
            headers={"Location": "/v1/realtime/calls/rtc_presentations"},
        )

    socket = FakeSideband()

    async def connect_socket(url: str, **kwargs: Any) -> FakeSideband:
        return socket

    adapter = OpenAIRealtimeAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200))
        ),
        websocket_connector=connect_socket,
        realtime_call_creator=create_call,
    )
    events = []

    async def on_event(session_id, generation, event):
        events.append(event)

    await adapter.connect(
        session_id="session-presentations",
        generation=1,
        context={"tasks": [], "questions": []},
        on_event=on_event,
        sdp_offer="offer",
    )
    facts = ({"schema": "jarvis.task_update.v1", "summary": "The report is complete."},)
    judgement_task = asyncio.create_task(
        adapter.judge_presentation_timing(
            session_id="session-presentations",
            generation=1,
            facts=facts,
        )
    )
    await asyncio.sleep(0)
    timing_request = socket.sent[-1]
    assert timing_request["response"]["conversation"] == "none"
    assert timing_request["response"]["output_modalities"] == ["text"]
    metadata = timing_request["response"]["metadata"]
    await socket.feed(
        {
            "type": "response.created",
            "event_id": "timing-created",
            "response": {"id": "timing-response", "metadata": metadata},
        }
    )
    await socket.feed(
        {
            "type": "response.done",
            "event_id": "timing-done",
            "response": {
                "id": "timing-response",
                "status": "completed",
                "output": [
                    {
                        "id": "timing-item",
                        "type": "message",
                        "content": [{"type": "output_text", "text": "PRESENT"}],
                    }
                ],
            },
        }
    )
    assert await judgement_task == "PRESENT"
    assert [event.kind for event in events] == ["ready"]

    await adapter.present_task_updates(
        session_id="session-presentations",
        generation=1,
        presentation_ids=("presentation-1",),
        facts=facts,
    )
    presentation_request = socket.sent[-1]
    presentation_metadata = presentation_request["response"]["metadata"]
    assert presentation_request["response"]["output_modalities"] == ["audio"]
    await socket.feed(
        {
            "type": "response.created",
            "event_id": "presentation-created",
            "response": {"id": "presentation-response", "metadata": presentation_metadata},
        }
    )
    await socket.feed(
        {
            "type": "response.done",
            "event_id": "presentation-done",
            "response": {
                "id": "presentation-response",
                "status": "completed",
                "output": [
                    {
                        "id": "presentation-item",
                        "type": "message",
                        "content": [
                            {"type": "audio", "transcript": "The report is complete."}
                        ],
                    }
                ],
            },
        }
    )
    delivery = next(event for event in events if event.kind == "assistant_response")
    assert delivery.payload["presentation_ids"] == ["presentation-1"]
    assert delivery.payload["transcript"] == "The report is complete."
    await adapter.close(session_id="session-presentations", generation=1, reason="done")


@pytest.mark.asyncio
async def test_openai_adapter_waits_for_provider_playout_cutoff_before_finalizing_interruption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "test-secret-key")

    async def create_call(**kwargs: Any) -> httpx.Response:
        return httpx.Response(201, text="answer", headers={"Location": "/v1/realtime/calls/rtc_cutoff"})

    socket = FakeSideband()

    async def connect_socket(url: str, **kwargs: Any) -> FakeSideband:
        return socket

    adapter = OpenAIRealtimeAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200))
        ),
        websocket_connector=connect_socket,
        realtime_call_creator=create_call,
    )
    events = []

    async def on_event(session_id, generation, event):
        events.append(event)

    await adapter.connect(
        session_id="session-cutoff",
        generation=1,
        context={},
        on_event=on_event,
        sdp_offer="offer",
    )
    await socket.feed(
        {"type": "response.created", "event_id": "cut-1", "response": {"id": "resp-cut"}}
    )
    await socket.feed(
        {
            "type": "response.output_item.added",
            "event_id": "cut-2",
            "response_id": "resp-cut",
            "item": {"id": "item-cut", "type": "message"},
        }
    )
    await socket.feed(
        {
            "type": "output_audio_buffer.started",
            "event_id": "cut-3",
            "response_id": "resp-cut",
        }
    )
    await socket.feed(
        {
            "type": "response.output_audio_transcript.done",
            "event_id": "cut-4",
            "response_id": "resp-cut",
            "item_id": "item-cut",
            "transcript": "I can explain the first result. The second result was not heard.",
        }
    )
    assert "assistant_response" not in [event.kind for event in events]
    await socket.feed(
        {
            "type": "output_audio_buffer.cleared",
            "event_id": "cut-5",
            "response_id": "resp-cut",
        }
    )
    await socket.feed(
        {
            "type": "output_audio_buffer.stopped",
            "event_id": "cut-5b",
            "response_id": "resp-cut",
        }
    )
    assert "assistant_response" not in [event.kind for event in events]
    await socket.feed(
        {
            "type": "response.done",
            "event_id": "cut-6",
            "response": {
                "id": "resp-cut",
                "status": "cancelled",
                "output": [
                    {
                        "id": "item-cut",
                        "type": "message",
                        "content": [
                            {
                                "type": "audio",
                                "transcript": "I can explain the first result. The second result was not heard.",
                            }
                        ],
                    }
                ],
            },
        }
    )
    assert "assistant_interrupted" not in [event.kind for event in events]
    await socket.feed(
        {
            "type": "conversation.item.truncated",
            "event_id": "cut-7",
            "item_id": "item-cut",
            "content_index": 0,
            "audio_end_ms": 1_200,
        }
    )
    await socket.feed(
        {
            "type": "output_audio_buffer.stopped",
            "event_id": "cut-8",
            "response_id": "resp-cut",
        }
    )
    await asyncio.sleep(0.8)

    deliveries = [event for event in events if event.kind == "assistant_interrupted"]
    assert len(deliveries) == 1
    assert deliveries[0].payload["transcript"].endswith("not heard.")
    assert deliveries[0].payload["heard_transcript"] == "I can explain"
    assert deliveries[0].payload["timing_summary"] == {
        "estimator_source": "provider_audio_end_calibrated_text",
        "audio_end_ms": 1200,
        "generated_audio_ms": None,
        "playback_duration_ms": 0,
        "transcript_margin_ms": 250,
        "cutoff_char": len("I can explain"),
        "duration_scale": 1.0,
    }
    await adapter.close(session_id="session-cutoff", generation=1, reason="done")


@pytest.mark.asyncio
async def test_sideband_failure_hangs_up_partial_call_and_key_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "")
    adapter = OpenAIRealtimeAdapter()

    async def on_event(session_id, generation, event):
        raise AssertionError("No event expected")

    with pytest.raises(OpenAIRealtimeConfigurationError, match="OPENAI_API_KEY"):
        await adapter.connect(
            session_id="missing-key",
            generation=1,
            context={},
            on_event=on_event,
            sdp_offer="offer",
        )

    monkeypatch.setattr(settings, "openai_api_key", "test-secret-key")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    async def create_call(**kwargs: Any) -> httpx.Response:
        return httpx.Response(201, text="answer", headers={"Location": "/v1/realtime/calls/rtc_partial"})

    async def fail_socket(url: str, **kwargs: Any):
        raise OSError("sideband unavailable")

    failing = OpenAIRealtimeAdapter(
        http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        websocket_connector=fail_socket,
        realtime_call_creator=create_call,
    )
    with pytest.raises(OSError, match="sideband unavailable"):
        await failing.connect(
            session_id="partial",
            generation=1,
            context={},
            on_event=on_event,
            sdp_offer="offer",
        )
    assert [request.url.path for request in requests] == ["/v1/realtime/calls/rtc_partial/hangup"]


@pytest.mark.asyncio
async def test_openai_call_api_returns_only_internal_session_and_sdp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "test-secret-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    async def create_call(**kwargs: Any) -> httpx.Response:
        return httpx.Response(
            201,
            text="safe-sdp-answer",
            headers={"Location": "/v1/realtime/calls/rtc_private_provider_id"},
        )

    socket = FakeSideband()

    async def connect_socket(url: str, **kwargs: Any) -> FakeSideband:
        return socket

    adapter = OpenAIRealtimeAdapter(
        http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        websocket_connector=connect_socket,
        realtime_call_creator=create_call,
    )
    monkeypatch.setattr(
        "app.services.openai_realtime_adapter.openai_realtime_adapter",
        adapter,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": "dev"})
        accepted = await client.post(
            "/api/jarvis/turns/text",
            json={"content": "Create the live log", "idempotency_key": "live-log"},
        )
        assert accepted.status_code == 202
        response = await client.post(
            "/api/jarvis/realtime/calls",
            json={
                "controller_id": "browser-live",
                "role": "controller",
                "provider_adapter": "openai",
                "sdp_offer": "safe-sdp-offer",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["sdp_answer"] == "safe-sdp-answer"
        assert body["rotate_after_seconds"] == 55 * 60
        assert body["session"]["provider_adapter"] == "openai"
        serialized = json.dumps(body)
        assert "test-secret-key" not in serialized
        assert "rtc_private_provider_id" not in serialized
        assert set(body) == {"role", "session", "sdp_answer", "rotate_after_seconds"}

        closed = await client.delete(
            f"/api/jarvis/realtime/sessions/{body['session']['id']}",
            params={"generation": body["session"]["generation"]},
        )
        assert closed.status_code == 204
