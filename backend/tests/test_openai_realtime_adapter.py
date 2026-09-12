from __future__ import annotations

import asyncio
import json
import ssl
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


async def wait_until(predicate, *, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("Condition was not met in time")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_default_call_creator_uses_shared_http_client_and_multipart() -> None:
    requests: list[httpx.Request] = []
    clients_created = 0

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, text="answer", headers={"Location": "/v1/realtime/calls/test"})

    def create_client() -> httpx.AsyncClient:
        nonlocal clients_created
        clients_created += 1
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    adapter = OpenAIRealtimeAdapter(http_client_factory=create_client)

    response = await adapter._default_realtime_call_creator(
        api_key="test-key",
        sdp_offer="test-sdp",
        session_config={"type": "realtime", "model": "test-model"},
    )

    assert response.status_code == 201
    request = requests[0]
    assert request.headers["authorization"] == "Bearer test-key"
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    body = request.content.decode()
    assert 'name="sdp"' in body and "test-sdp" in body
    assert 'name="session"' in body and '"model":"test-model"' in body
    await adapter._hangup(api_key="test-key", call_id="rtc_test")
    assert clients_created == 1
    await adapter.shutdown()


@pytest.mark.asyncio
async def test_default_websocket_connector_uses_verified_certificate_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    expected_socket = object()

    async def connect(url: str, **kwargs: Any):
        captured["url"] = url
        captured.update(kwargs)
        return expected_socket

    monkeypatch.setattr("websockets.asyncio.client.connect", connect)

    result = await OpenAIRealtimeAdapter._default_websocket_connector(
        "wss://api.openai.com/v1/realtime?call_id=test",
        open_timeout=12.0,
    )

    assert result is expected_socket
    assert captured["url"].startswith("wss://api.openai.com/")
    assert captured["open_timeout"] == 12.0
    assert isinstance(captured["ssl"], ssl.SSLContext)
    assert captured["ssl"].check_hostname is True
    assert captured["ssl"].verify_mode == ssl.CERT_REQUIRED


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
                {"role": "user", "content": "Old question one"},
                {"role": "assistant", "content": "Old answer one"},
                {"role": "user", "content": "Old question two"},
                {"role": "assistant", "content": "Old answer two"},
                {"role": "user", "content": "Recent question"},
                {"role": "assistant", "content": "Recent answer"},
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
    await wait_until(lambda: bool(events))
    assert events[0][2].kind == "ready"
    assert events[0][2].payload["timing_ms"]["provider_call"] >= 0
    assert events[0][2].payload["timing_ms"]["sideband_connect"] >= 0
    assert events[0][2].payload["timing_ms"]["total_to_sideband_ready"] >= 0
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
        "type": "server_vad",
        "silence_duration_ms": 350,
        "threshold": 0.5,
        "prefix_padding_ms": 300,
        "create_response": False,
        "interrupt_response": False,
    }
    assert session_config["audio"]["input"]["transcription"] == {
        "model": "gpt-live-transcribe",
        "keywords": ["Jarvis", "queued", "reminder", "snooze"],
    }
    assert "multi-step or substantial analysis with dispatch_task" in session_config["instructions"]
    assert "has no permission to browse, edit files, run commands" in session_config["instructions"]
    assert "one to eight words" in session_config["instructions"]
    assert "Do not warm up" in session_config["instructions"]
    assert "Never use exclamation marks" in session_config["instructions"]
    assert "Remain silent until its result exists" in session_config["instructions"]
    assert "It hasn't started; the worker is offline." in session_config["instructions"]
    assert "Never add a preamble" in session_config["instructions"]
    assert "Begin the requested content immediately" in session_config["instructions"]
    assert "likely continuation" in session_config["instructions"]
    assert "continue until it is naturally complete" in session_config["instructions"]
    assert "not a customer-service chatbot" in session_config["instructions"]
    assert "taking as little of their time and attention as possible" in session_config["instructions"]
    assert "collected, even pace and low emotional intensity" in session_config["instructions"]
    assert "include_diagnostics=true" in session_config["instructions"]
    assert "Never read SQL" in session_config["instructions"]
    assert "task-1" not in session_config["instructions"]
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
        "set_task_update_policy",
        "set_jarvis_voice",
        "start_new_jarvis_log",
        "end_voice_session",
    }
    assert [message["item"]["role"] for message in socket.sent] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert socket.sent[0]["item"]["content"][0]["text"] == "Old question two"

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
    assert socket.sent[-2]["item"]["content"][0]["text"] == "Typed continuation"
    assert socket.sent[-1]["item"]["type"] == "function_call_output"
    assert not any(item["type"] == "response.create" for item in socket.sent)

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
async def test_openai_adapter_returns_sdp_while_sideband_connects() -> None:
    release_sideband = asyncio.Event()
    socket = FakeSideband()

    async def create_call(**kwargs: Any) -> httpx.Response:
        return httpx.Response(
            201,
            text="early-answer",
            headers={"Location": "/v1/realtime/calls/rtc_parallel"},
        )

    async def connect_socket(url: str, **kwargs: Any) -> FakeSideband:
        await release_sideband.wait()
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

    connection = await asyncio.wait_for(adapter.connect(
        session_id="parallel-session",
        generation=1,
        context={},
        on_event=on_event,
        sdp_offer="offer",
    ), timeout=0.1)

    assert connection.sdp_answer == "early-answer"
    assert not adapter.is_connected(session_id="parallel-session", generation=1)
    assert events == []
    release_sideband.set()
    await wait_until(lambda: bool(events))
    assert events[-1].kind == "ready"
    assert adapter.is_connected(session_id="parallel-session", generation=1)
    await adapter.shutdown()


def test_response_context_is_bounded_and_dynamic_state_is_not_in_session_prompt() -> None:
    long_text = "Detailed context " * 100
    context = {
        "situation_revision": 123,
        "temporal": {
            "utc": "2026-09-05T10:00:00+00:00",
            "local": "2026-09-05T12:00:00+02:00",
            "time_zone": "Europe/Amsterdam",
        },
        "conversation_summary": long_text,
        "tasks": [
            {
                "task_id": f"active-{index}",
                "goal": long_text,
                "status": "running",
                "status_reason": long_text,
                "summary": long_text,
            }
            for index in range(50)
        ],
        "recent_terminal_tasks": [
            {
                "task_id": f"terminal-{index}",
                "goal": long_text,
                "status": "completed",
                "summary": long_text,
            }
            for index in range(50)
        ],
        "questions": [],
        "reminders": {"upcoming": [], "overdue": [], "recently_completed": []},
        "component_health": {
            "worker": {"status": "degraded", "detail": long_text},
        },
        "preferences": {"concision": "concise"},
    }

    compact = OpenAIRealtimeAdapter._compact_situation(context)
    serialized = json.dumps(compact, separators=(",", ":"), ensure_ascii=False)
    session_prompt = OpenAIRealtimeAdapter._session_config(context)["instructions"]
    response_prompt = OpenAIRealtimeAdapter._response_instructions(context, ())

    assert len(serialized) <= 7_800
    assert "active-0" not in session_prompt
    assert "active-0" in response_prompt
    assert "active-49" not in response_prompt
    assert compact["counts"]["active_tasks"] == 50


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
        context={"activation_id": "activation-events"},
        on_event=on_event,
        sdp_offer="offer",
    )
    await wait_until(lambda: adapter.is_connected(session_id="session-events", generation=1))
    await socket.feed({"type": "input_audio_buffer.speech_started", "event_id": "evt-1"})
    await socket.feed({"type": "input_audio_buffer.speech_stopped", "event_id": "evt-2"})
    await socket.feed(
        {
            "type": "input_audio_buffer.committed",
            "event_id": "evt-2-commit",
            "item_id": "item-user",
            "previous_item_id": "item-previous",
        }
    )
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
        "user_input_committed",
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
        "previous_item_id": "item-previous",
        "activation_id": "activation-events",
    }
    assert events[4].payload == {
        "item_id": "item-user",
        "delta": "Please prepare ",
        "transcript": "Please prepare ",
        "provisional": True,
    }
    assert events[5].payload["transcript"] == "Please prepare a report."
    assert events[5].payload["activation_id"] == "activation-events"
    assert events[5].payload["timing_ms"]["item_commit_to_transcript"] >= 0
    assert adapter._sessions[("session-events", 1)].activation_id is None
    assert events[7].payload["timing_ms"]["response_created_to_first_audio"] >= 0
    assert events[8].payload["delta"] == "I can "
    assert events[9].payload["transcript"] == "I can help with that."
    assert events[10].payload["arguments"] == {"goal": "Prepare a report"}
    await adapter.close(session_id="session-events", generation=1, reason="done")


@pytest.mark.asyncio
async def test_cancelled_response_without_transcript_emits_terminal_interruption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "test-secret-key")

    async def create_call(**kwargs: Any) -> httpx.Response:
        return httpx.Response(
            201,
            text="answer",
            headers={"Location": "/v1/realtime/calls/rtc_cancelled"},
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
        session_id="session-cancelled",
        generation=1,
        context={},
        on_event=on_event,
        sdp_offer="offer",
    )
    metadata = {
        "jarvis_request_id": "request-cancelled",
        "jarvis_purpose": "user_turn",
    }
    await socket.feed(
        {
            "type": "response.created",
            "event_id": "cancelled-created",
            "response": {"id": "response-cancelled", "metadata": metadata},
        }
    )
    await socket.feed(
        {
            "type": "response.done",
            "event_id": "cancelled-done",
            "response": {
                "id": "response-cancelled",
                "status": "cancelled",
                "metadata": metadata,
                "output": [],
            },
        }
    )

    terminal = next(event for event in events if event.kind == "assistant_interrupted")
    assert terminal.payload["transcript"] == ""
    assert terminal.payload["request_id"] == "request-cancelled"
    assert terminal.payload["response_status"] == "cancelled"
    await adapter.close(session_id="session-cancelled", generation=1, reason="done")


@pytest.mark.asyncio
async def test_openai_adapter_creates_one_controlled_response_with_fresh_facts(
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
    await wait_until(lambda: adapter.is_connected(session_id="session-presentations", generation=1))
    facts = (
        {
            "schema": "jarvis.task_update.v1",
            "summary": "The report is complete.",
            "required_spoken_phrase": "The report is complete.",
        },
    )
    await adapter.create_response(
        session_id="session-presentations",
        generation=1,
        request_id="request-presentations",
        response_kind="user_turn",
        presentation_ids=("presentation-1",),
        facts=facts,
        context={
            "tasks": [{"task_id": "task-1", "status": "completed"}],
            "questions": [],
            "pending_update_count": 5,
        },
    )
    presentation_request = socket.sent[-1]
    presentation_metadata = presentation_request["response"]["metadata"]
    assert presentation_request["response"]["output_modalities"] == ["audio"]
    assert "max_output_tokens" not in presentation_request["response"]
    assert presentation_metadata["jarvis_purpose"] == "user_turn"
    assert json.loads(presentation_metadata["jarvis_presentation_ids"]) == ["presentation-1"]
    assert "The report is complete." in presentation_request["response"]["instructions"]
    assert "completed" in presentation_request["response"]["instructions"]
    assert "there are 5 updates" in presentation_request["response"]["instructions"]
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
    generating = next(event for event in events if event.kind == "assistant_generating")
    assert generating.payload["timing_ms"]["response_create_sent_to_created"] >= 0
    assert delivery.payload["presentation_ids"] == ["presentation-1"]
    assert delivery.payload["transcript"] == "The report is complete."
    assert delivery.payload["response_kind"] == "user_turn"

    reminder_facts = (
        {
            "schema": "jarvis.task_update.v1",
            "event_type": "task.reminder.due",
            "required_spoken_phrase": "Reminder: take a break.",
        },
    )
    await adapter.create_response(
        session_id="session-presentations",
        generation=1,
        request_id="request-reminder",
        response_kind="task_update",
        presentation_ids=("reminder-presentation",),
        facts=reminder_facts,
        context={
            "dialogue": [{"role": "assistant", "content": "Stale queued confirmation"}],
            "conversation_summary": "Stale history must not leak.",
            "pending_update_count": 1,
        },
    )
    proactive = socket.sent[-1]["response"]
    assert proactive["conversation"] == "none"
    assert proactive["tools"] == []
    assert proactive["tool_choice"] == "none"
    assert "max_output_tokens" not in proactive
    assert proactive["input"][0]["role"] == "user"
    proactive_input = proactive["input"][0]["content"][0]["text"]
    assert "Reminder: take a break." in proactive_input
    assert "Stale queued confirmation" not in proactive_input
    assert "Stale history must not leak" not in proactive["instructions"]
    assert "Say exactly" in proactive["instructions"]
    assert "Do not call tools" in proactive["instructions"]
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
    timing_summary = deliveries[0].payload["timing_summary"]
    assert 0 <= timing_summary.pop("playback_duration_ms") <= 50
    assert timing_summary == {
        "estimator_source": "provider_audio_end_calibrated_text",
        "audio_end_ms": 1200,
        "generated_audio_ms": None,
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

    events = []

    async def on_event(session_id, generation, event):
        events.append(event)

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
    connection = await failing.connect(
        session_id="partial",
        generation=1,
        context={},
        on_event=on_event,
        sdp_offer="offer",
    )
    assert connection.sdp_answer == "answer"
    await wait_until(lambda: bool(events))
    assert events[-1].kind == "provider_error"
    assert events[-1].payload == {"reason": "OSError", "stage": "sideband_setup"}
    assert [request.url.path for request in requests] == ["/v1/realtime/calls/rtc_partial/hangup"]
    await failing.shutdown()


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
        assert set(body) == {
            "role",
            "session",
            "sdp_answer",
            "rotate_after_seconds",
            "request_id",
            "server_received_at",
            "server_responded_at",
            "audio_policy",
            "provider_capabilities",
        }
        assert body["provider_capabilities"] == {
            "continuous_conversation": False,
            "application_controlled_turns": True,
            "supports_candidate_audio_append": True,
            "supports_manual_response": True,
            "supports_sideband": True,
        }
        assert body["audio_policy"] == {
            "configuration_id": "server_vad:s350:t0.5:p300",
            "turn_mode": "server_vad",
            "commit_owner": "provider",
            "silence_duration_ms": 350,
            "vad_threshold": 0.5,
            "prefix_padding_ms": 300,
            "semantic_eagerness": None,
            "create_response": False,
            "interrupt_response": False,
        }

        closed = await client.delete(
            f"/api/jarvis/realtime/sessions/{body['session']['id']}",
            params={"generation": body["session"]["generation"]},
        )
        assert closed.status_code == 204
