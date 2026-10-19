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
@pytest.mark.parametrize("termination", ["closed", "transport", "stale_closed"])
async def test_terminal_sideband_retires_session_and_idle_watcher(monkeypatch, termination):
    from app.services.jarvis_coordinator import StaleSessionError
    monkeypatch.setattr(settings, "openai_api_key", "fixture-key")
    socket, events = FakeLiveSocket(), []
    async def create(**kwargs):
        return {"session": {"id": "terminal"}, "transport": {"sdp": "answer"}}
    async def connect(*args, **kwargs):
        return socket
    async def on_event(session, generation, event):
        events.append(event)
        if termination == "stale_closed" and event.kind in {"disconnected", "provider_error"}:
            raise StaleSessionError("Live session generation is stale")
    adapter = OpenAILiveAdapter(session_creator=create, websocket_connector=connect)
    await adapter.connect(session_id="terminal", generation=1, context={}, on_event=on_event, sdp_offer="offer")
    await wait_for_sideband(adapter, ("terminal", 1))
    session = adapter._sessions[("terminal", 1)]
    tool = asyncio.create_task(asyncio.Event().wait())
    session.tool_tasks.add(tool)
    if termination == "transport":
        await socket.incoming.put(None)
    else:
        await socket.incoming.put(json.dumps({"type": "session.closed"}))
    await asyncio.wait_for(session.receiver_task, timeout=1)
    assert not adapter._sessions and socket.closed
    assert session.idle_task.cancelled() and tool.cancelled()
    if termination == "closed":
        assert [event.kind for event in events] == ["ready", "disconnected"]
    else:
        assert any(event.kind == "provider_error" for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("flush_path", ["gap", "close"])
async def test_rejected_transcript_does_not_strand_old_session_work(monkeypatch, flush_path):
    from app.services.jarvis_coordinator import StaleSessionError
    monkeypatch.setattr(settings, "openai_api_key", "fixture-key")
    monkeypatch.setattr("app.services.openai_live_adapter._TRANSCRIPT_GAP_SECONDS", .01)
    socket, events = FakeLiveSocket(), []
    async def create(**kwargs):
        return {"session": {"id": "old"}, "transport": {"sdp": "answer"}}
    async def connect(*args, **kwargs):
        return socket
    async def on_event(session, generation, event):
        events.append(event)
        if event.kind in {"user_transcript", "provider_error"}:
            raise StaleSessionError("Live session generation is stale")
    adapter = OpenAILiveAdapter(session_creator=create, websocket_connector=connect)
    await adapter.connect(session_id="old", generation=1, context={}, on_event=on_event, sdp_offer="offer")
    await wait_for_sideband(adapter, ("old", 1))
    session = adapter._sessions[("old", 1)]
    tool = asyncio.create_task(asyncio.Event().wait())
    session.tool_tasks.add(tool)
    await adapter.handle_event("old", 1, {"type": "session.input_transcript.delta",
        "delta": "Unfinished old speech", "start_ms": 0, "end_ms": 100})
    flush = session.user_transcript_flush_task
    if flush_path == "gap":
        await asyncio.wait_for(flush, timeout=1)
        assert any(event.kind == "provider_error" and event.payload["stage"] == "transcript_flush" for event in events)
        await adapter.close(session_id="old", generation=1, reason="superseded", emit_event=False)
    else:
        with pytest.raises(StaleSessionError):
            await adapter.close(session_id="old", generation=1, reason="superseded", emit_event=False)
    assert socket.closed and tool.cancelled() and session.receiver_task.done()
    assert session.idle_task.done() and flush.done()
    assert not adapter._sessions


@pytest.mark.asyncio
async def test_close_waits_for_durable_disconnect_after_provider_ack(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "fixture-key")
    socket, events = FakeLiveSocket(), []
    async def create(**kwargs):
        return {"session": {"id": "closing"}, "transport": {"sdp": "answer"}}
    async def connect(*args, **kwargs):
        return socket
    async def on_event(session, generation, event):
        if event.kind == "usage":
            await asyncio.sleep(.05)  # Coordinator persistence yields to close().
        events.append(event)
    adapter = OpenAILiveAdapter(session_creator=create, websocket_connector=connect)
    await adapter.connect(session_id="closing", generation=1, context={}, on_event=on_event, sdp_offer="offer")
    await wait_for_sideband(adapter, ("closing", 1))
    await adapter.handle_event("closing", 1, {"type": "session.started"})
    await adapter.close(session_id="closing", generation=1, reason="explicit_sleep")
    disconnected = [event for event in events if event.kind == "disconnected"]
    assert len(disconnected) == 1 and disconnected[0].payload["reason"] == "session_closed"
    assert any(event.kind == "usage" for event in events)
    assert socket.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("socket_exception", [False, True])
async def test_provider_closed_socket_does_not_report_a_second_error(monkeypatch, socket_exception):
    monkeypatch.setattr(settings, "openai_api_key", "fixture-key")
    class RemoteSocket(FakeLiveSocket):
        async def __anext__(self):
            value = await self.incoming.get()
            if value is None:
                if socket_exception:
                    raise RuntimeError("Transport closed after session.closed")
                raise StopAsyncIteration
            return value
    socket, events = RemoteSocket(), []
    async def create(**kwargs):
        return {"session": {"id": "provider"}, "transport": {"sdp": "answer"}}
    async def connect(*args, **kwargs): return socket
    async def on_event(session, generation, event): events.append(event)
    adapter = OpenAILiveAdapter(session_creator=create, websocket_connector=connect)
    await adapter.connect(session_id="s", generation=1, context={}, on_event=on_event, sdp_offer="offer")
    await wait_for_sideband(adapter, ("s", 1))
    session = adapter._sessions[("s", 1)]
    await socket.incoming.put(json.dumps({"type": "session.closed", "usage": {"seconds": 3}}))
    await socket.incoming.put(None)
    await asyncio.wait_for(session.receiver_task, timeout=1)
    assert [event.kind for event in events].count("disconnected") == 1
    assert [event.kind for event in events].count("usage") == 1
    assert not any(event.kind == "provider_error" for event in events)
    assert session.closed.is_set() and not adapter._sessions
    await adapter.close(session_id="s", generation=1, reason="test", emit_event=False)


@pytest.mark.asyncio
async def test_cancellation_before_sideband_start_reports_disconnect(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "fixture-key")
    events = []
    async def create(**kwargs):
        return {"session": {"id": "pending"}, "transport": {"sdp": "answer"}}
    async def connect(*args, **kwargs):
        await asyncio.Event().wait()
    async def on_event(session, generation, event):
        events.append(event)
    adapter = OpenAILiveAdapter(session_creator=create, websocket_connector=connect)
    await adapter.connect(session_id="pending", generation=1, context={}, on_event=on_event, sdp_offer="offer")
    await adapter.close(session_id="pending", generation=1, reason="cancel_startup")
    assert [event.payload["reason"] for event in events if event.kind == "disconnected"] == ["cancel_startup"]
    assert not adapter._pending_connections and not adapter._sessions


@pytest.mark.asyncio
async def test_priority_rejection_falls_back_once_and_stream_item_routes_once(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    socket, events = FakeLiveSocket(), []
    async def create(**kwargs):
        assert kwargs["session_config"]["delegation"]["responses"]["service_tier"] == "priority"
        return {"session": {"id": "priority"}, "transport": {"sdp": "answer"}}
    async def connect(*args, **kwargs):
        return socket
    async def on_event(session, generation, event):
        events.append(event)
    adapter = OpenAILiveAdapter(session_creator=create, websocket_connector=connect)
    await adapter.connect(session_id="priority", generation=1, context={}, on_event=on_event, sdp_offer="offer")
    await wait_for_sideband(adapter, ("priority", 1))
    rejected = {"type": "response.event", "delegation_id": "one", "event": {"type": "response.failed",
        "response": {"id": "response-one", "error": {"code": "unsupported_service_tier"}}}}
    await adapter.handle_event("priority", 1, rejected)
    await adapter.handle_event("priority", 1, rejected)
    assert len([event for event in events if event.kind == "delegation_tier_fallback"]) == 1
    assert len([message for message in socket.sent if message["type"] == "session.update"]) == 1
    item = {"type": "response.event", "delegation_id": "two", "event": {"type": "response.output_item.done",
        "response_id": "response-two", "item": {"type": "function_call", "call_id": "completed",
            "name": "list_tasks", "arguments": "{}"}}}
    await adapter.handle_event("priority", 1, item)
    await adapter.handle_event("priority", 1, item)
    completed = {"type": "response.event", "delegation_id": "two", "event": {"type": "response.completed", "response": {
        "id": "response-two", "service_tier": "default", "usage": {"input_tokens": 4, "output_tokens": 2}, "output": []}}}
    await adapter.handle_event("priority", 1, completed)
    await adapter.handle_event("priority", 1, completed)
    assert len([event for event in events if event.kind == "shell_call"]) == 1
    usage = next(event.payload for event in events if event.kind == "delegation_usage" and event.payload["response_id"] == "response-two")
    assert usage["actual_tier"] == "default" and usage["fallback"]
    assert usage["usage"]["input_tokens"] == 4 and usage["cost_status"] == "unavailable"
    await adapter.close(session_id="priority", generation=1, reason="test", emit_event=False)


async def wait_for_sideband(adapter: OpenAILiveAdapter, key: tuple[str, int]) -> None:
    for _ in range(20):
        if key in adapter._sessions:
            return
        await asyncio.sleep(0)
    raise AssertionError("Live sideband did not attach")


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments, valid", [
    ({"goal": "Draft a plan", "intent_id": "plan", "notification_intent": "discretionary", "notification_reason": "No update requested"}, True),
    ({"goal": "Draft a plan"}, False),
    ({"intent_id": "plan"}, False),
    ({"goal": "Draft a plan", "intent_id": "plan", "importance": "invalid"}, False),
    ({"goal": "Draft a plan", "intent_id": "plan", "unknown": True}, False),
])
async def test_managed_optional_arguments_preserve_local_validation(monkeypatch, arguments, valid):
    from jsonschema.exceptions import ValidationError
    monkeypatch.setattr(settings, "openai_api_key", "fixture-key")
    socket, events = FakeLiveSocket(), []
    async def create(**kwargs):
        return {"session": {"id": "provider"}, "transport": {"sdp": "answer"}}
    async def connect(*args, **kwargs): return socket
    async def on_event(session, generation, event): events.append(event)
    adapter = OpenAILiveAdapter(session_creator=create, websocket_connector=connect)
    await adapter.connect(session_id="s", generation=1, context={}, on_event=on_event, sdp_offer="offer")
    await wait_for_sideband(adapter, ("s", 1))
    event = {"type": "response.event", "delegation_id": "d", "event": {
        "type": "response.output_item.done", "response_id": "r", "item": {
            "type": "function_call", "call_id": "call", "name": "dispatch_task", "arguments": json.dumps(arguments)}}}
    try:
        if valid:
            await adapter.handle_event("s", 1, event)
            # Admission routes off the sideband receive loop.
            for _ in range(100):
                if any(row.kind == "shell_call" for row in events):
                    break
                await asyncio.sleep(0.01)
            assert next(row for row in events if row.kind == "shell_call").payload["arguments"] == arguments
        else:
            with pytest.raises(ValidationError):
                await adapter.handle_event("s", 1, event)
            assert not any(row.kind == "shell_call" for row in events)
    finally:
        await adapter.close(session_id="s", generation=1, reason="test", emit_event=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("code, expected_requests", [("unsupported_service_tier", 2),
    ("service_tier_not_supported", 2), ("invalid_api_key", 1), ("invalid_request", 1)])
async def test_creation_retries_only_explicit_priority_rejection_and_remembers_fallback(monkeypatch, code, expected_requests):
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    requests, events = [], []
    socket = FakeLiveSocket()
    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(400, json={"error": {"code": code}})
        return httpx.Response(201, json={"session": {"id": "live-fallback"}, "transport": {"sdp": "answer"}})
    async def connect(*args, **kwargs):
        return socket
    async def on_event(session, generation, event):
        events.append(event)
    adapter = OpenAILiveAdapter(websocket_connector=connect,
        http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    if expected_requests == 1:
        with pytest.raises(RuntimeError, match="HTTP 400"):
            await adapter.connect(session_id="s", generation=1, context={}, on_event=on_event, sdp_offer="offer")
    else:
        await adapter.connect(session_id="s", generation=1, context={}, on_event=on_event, sdp_offer="offer")
        await wait_for_sideband(adapter, ("s", 1))
        assert requests[0]["session"]["delegation"]["responses"]["service_tier"] == "priority"
        assert requests[1]["session"]["delegation"]["responses"]["service_tier"] == "default"
        await adapter.handle_event("s", 1, {"type": "error", "error": {"code": code}})
        assert len([event for event in events if event.kind == "delegation_tier_fallback"]) == 1
        assert not [message for message in socket.sent if message["type"] == "session.update"]
        await adapter.close(session_id="s", generation=1, reason="test", emit_event=False)
    assert len(requests) == expected_requests


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
async def test_screen_workflow_preflight_does_not_block_live_sideband(monkeypatch) -> None:
    from app.config import settings
    from app.services.task_workflows import load_workflow
    definition = load_workflow("screen_document@1")
    monkeypatch.setattr(settings, "task_workflow_allowlist", f"screen_document@1:{definition.digest}")
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    socket = FakeLiveSocket()
    started = asyncio.Event()
    release = asyncio.Event()
    events: list[VoiceProviderEvent] = []

    async def create(**_: Any) -> dict[str, Any]:
        return {"session": {"id": "live-screen"}, "transport": {"sdp": "answer"}}

    async def connect(*_: Any, **__: Any) -> FakeLiveSocket:
        return socket

    async def on_event(_: str, __: int, event: VoiceProviderEvent) -> None:
        if event.kind == "shell_call":
            started.set()
            await release.wait()
        events.append(event)

    adapter = OpenAILiveAdapter(session_creator=create, websocket_connector=connect)
    await adapter.connect(session_id="screen", generation=1, context={}, on_event=on_event, sdp_offer="offer")
    await wait_for_sideband(adapter, ("screen", 1))
    await adapter.handle_event("screen", 1, {
        "type": "response.event", "delegation_id": "delegation-screen",
        "event": {"type": "response.output_item.done", "response_id": "response-screen",
                  "item": {"type": "function_call", "call_id": "call-screen",
                           "name": "start_workflow", "arguments": json.dumps({"goal": "Describe this screen", "definition": "screen_document@1", "intent_id": "screen", "notification_intent": "discretionary", "notification_reason": "No update requested"})}},
    })
    await asyncio.wait_for(started.wait(), timeout=1)
    await asyncio.wait_for(adapter.handle_event("screen", 1, {
        "type": "session.input_transcript.delta", "delta": "still listening",
    }), timeout=0.2)
    assert any(event.kind == "user_transcript_delta" for event in events)
    release.set()
    await adapter.close(session_id="screen", generation=1, reason="test", emit_event=False)


@pytest.mark.asyncio
async def test_attached_sideband_is_ready_without_primary_started_replay(monkeypatch) -> None:
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
    await wait_for_sideband(adapter, ("local", 2))
    assert connection.sdp_answer == "answer-sdp"
    assert connection.provider_session_id == "live_opaque/id"
    assert created_args["sdp_offer"] == "offer-sdp"
    config = created_args["session_config"]
    assert config["model"] == "gpt-live-1"
    assert config["audio"]["output"]["voice"] == "marin"
    assert config["delegation"]["responses"]["model"] == "gpt-5.6-luna"
    assert config["delegation"]["responses"]["reasoning"] == {"effort": "low"}
    assert config["delegation"]["responses"]["parallel_tool_calls"] is False
    assert all(tool["strict"] is False for tool in config["delegation"]["responses"]["tools"])
    dispatch = next(tool for tool in config["delegation"]["responses"]["tools"] if tool["name"] == "dispatch_task")
    assert dispatch["parameters"]["required"] == ["goal", "intent_id", "notification_intent", "notification_reason"]
    assert socket_args["url"].endswith("/live_opaque%2Fid/attach")
    assert [event.kind for event in events] == ["ready"]
    assert events[0].payload["readiness_source"] == "authenticated_sideband_attachment"

    await adapter.handle_event("local", 2, {"type": "session.started", "event_id": "started"})
    assert [event.kind for event in events] == ["ready"]
    await adapter.close(session_id="local", generation=2, reason="test", emit_event=False)
    assert socket.closed


@pytest.mark.asyncio
async def test_connect_returns_sdp_while_live_sideband_attaches(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    socket = FakeLiveSocket()
    attachment_started = asyncio.Event()
    release_attachment = asyncio.Event()
    events: list[VoiceProviderEvent] = []

    async def create(**_: Any) -> dict[str, Any]:
        return {"session": {"id": "live_overlap"}, "transport": {"sdp": "answer"}}

    async def connect(*_: Any, **__: Any) -> FakeLiveSocket:
        attachment_started.set()
        await release_attachment.wait()
        return socket

    async def on_event(_: str, __: int, event: VoiceProviderEvent) -> None:
        events.append(event)

    adapter = OpenAILiveAdapter(session_creator=create, websocket_connector=connect)
    connection = await asyncio.wait_for(
        adapter.connect(session_id="s", generation=10, context={}, on_event=on_event, sdp_offer="offer"),
        timeout=0.1,
    )
    assert connection.sdp_answer == "answer"
    await asyncio.wait_for(attachment_started.wait(), timeout=0.1)
    assert ("s", 10) in adapter._pending_connections
    assert ("s", 10) not in adapter._sessions
    assert events == []

    release_attachment.set()
    await wait_for_sideband(adapter, ("s", 10))
    assert [event.kind for event in events] == ["ready"]
    await adapter.handle_event("s", 10, {"type": "session.started"})
    assert events[-1].kind == "ready"
    assert events[-1].payload["timing_ms"]["sideband_connect"] >= 0
    await adapter.close(session_id="s", generation=10, reason="test", emit_event=False)


@pytest.mark.asyncio
async def test_sideband_setup_failure_is_reported_after_sdp_return(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    events: list[VoiceProviderEvent] = []

    async def create(**_: Any) -> dict[str, Any]:
        return {"session": {"id": "live_failure"}, "transport": {"sdp": "answer"}}

    async def connect(*_: Any, **__: Any) -> FakeLiveSocket:
        raise RuntimeError("attach failed")

    async def on_event(_: str, __: int, event: VoiceProviderEvent) -> None:
        events.append(event)

    adapter = OpenAILiveAdapter(session_creator=create, websocket_connector=connect)
    connection = await adapter.connect(
        session_id="s", generation=11, context={}, on_event=on_event, sdp_offer="offer",
    )
    assert connection.sdp_answer == "answer"
    for _ in range(20):
        if events:
            break
        await asyncio.sleep(0)
    assert events[-1].kind == "provider_error"
    assert events[-1].payload == {"reason": "RuntimeError", "stage": "sideband_setup"}
    assert ("s", 11) not in adapter._pending_connections
    assert ("s", 11) not in adapter._sessions


@pytest.mark.asyncio
async def test_rejected_attachment_ready_retires_socket_and_watchers(monkeypatch):
    from app.services.jarvis_coordinator import StaleSessionError
    monkeypatch.setattr(settings, "openai_api_key", "fixture-key")
    socket, observed = FakeLiveSocket(), []
    key = ("superseded", 4)
    session = None
    async def create(**kwargs):
        return {"session": {"id": "provider"}, "transport": {"sdp": "answer"}}
    async def connect(*args, **kwargs):
        return socket
    async def on_event(session_id, generation, event):
        nonlocal session
        if event.kind == "ready":
            session = adapter._sessions[key]
            raise StaleSessionError("Superseded during attachment")
        observed.append(event)
    adapter = OpenAILiveAdapter(session_creator=create, websocket_connector=connect)
    await adapter.connect(session_id=key[0], generation=key[1], context={},
        on_event=on_event, sdp_offer="offer")
    pending = adapter._pending_connections[key]
    await asyncio.wait_for(pending.task, timeout=3)
    assert socket.closed and session is not None
    assert session.receiver_task.done() and session.idle_task.done()
    assert not adapter._sessions and not adapter._pending_connections
    assert not any(event.kind == "ready" for event in observed)
    assert any(event.kind == "provider_error" for event in observed)


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
        "type": "session.delegation.created",
        "event_id": "event-delegation-a",
        "offset_ms": 40,
        "delegation": {
            "id": "item_delegation_a",
            "type": "delegation",
            "target": "responses",
            "response_id": "resp-1",
        },
    })
    await adapter.handle_event("s", 1, {
        "type": "response.event",
        "event_id": "event-response-created-1",
        "delegation_id": "item_delegation_a",
        "event": {"type": "response.created", "response": {"id": "resp-1"}},
    })
    for call_id in ("call-1", "call-2"):
        event = {
            "type": "response.event",
            "event_id": f"event-{call_id}",
            "delegation_id": "item_delegation_a",
            "event": {
                "type": "response.output_item.done", "response_id": "resp-1",
                "item": {"type": "function_call", "call_id": call_id, "name": "list_tasks", "arguments": "{}"},
            },
        }
        await adapter.handle_event("s", 1, event)
        await adapter.handle_event("s", 1, event)
    assert [event.kind for event in events].count("shell_call") == 2
    assert {event.payload["delegation_id"] for event in events if event.kind == "shell_call"} == {"item_delegation_a"}
    shell_events = [event for event in events if event.kind == "shell_call"]
    assert all(event.payload["origin_item_id"] == "live-user:live_1:1" for event in shell_events)
    assert len({event.payload["origin_correlation_id"] for event in shell_events}) == 1
    assert all(
        event.payload["origin_timeline"]
        == {"offset_ms": 40, "start_ms": 40, "end_ms": None}
        for event in shell_events
    )
    await adapter._flush_transcript(adapter._sessions[("s", 1)], "user")
    transcript = next(event for event in events if event.kind == "user_transcript")
    assert transcript.payload["transcript"] == "list tasks"
    assert transcript.payload["start_ms"] == 10
    assert transcript.payload["end_ms"] == 30
    resolved = next(event for event in events if event.kind == "delegation_origin_resolved")
    assert resolved.payload["origin_item_id"] == transcript.payload["item_id"]
    assert resolved.payload["delegation_timeline"] == {
        "offset_ms": 40,
        "start_ms": 40,
        "end_ms": None,
    }

    await adapter.send_tool_result(session_id="s", generation=1, call_id="call-1", output={"ok": True})
    await adapter.send_tool_result(session_id="s", generation=1, call_id="call-1", output={"ok": True})
    assert [item["type"] for item in socket.sent].count("response.item.create") == 1
    assert [item["type"] for item in socket.sent].count("response.create") == 0

    await adapter.handle_event("s", 1, {
        "type": "response.event",
        "event_id": "event-response-completed-1",
        "delegation_id": "item_delegation_a",
        "event": {"type": "response.completed", "response": {"id": "resp-1"}},
    })
    await adapter.send_tool_result(session_id="s", generation=1, call_id="call-2", output={"ok": True})
    await adapter.send_tool_result(session_id="s", generation=1, call_id="call-2", output={"ok": True})
    assert [item["type"] for item in socket.sent].count("response.item.create") == 2
    assert [item["type"] for item in socket.sent].count("response.create") == 1

    # The response created by the continuation keeps the original delegation
    # offset and repaired transcript origin even though no new delegation event arrives.
    await adapter.handle_event("s", 1, {
        "type": "response.event",
        "event_id": "event-response-created-2",
        "delegation_id": "item_delegation_a",
        "event": {"type": "response.created", "response": {"id": "resp-2"}},
    })
    await adapter.handle_event("s", 1, {
        "type": "response.event",
        "event_id": "event-call-3",
        "delegation_id": "item_delegation_a",
        "event": {
            "type": "response.output_item.done",
            "response_id": "resp-2",
            "item": {
                "type": "function_call",
                "call_id": "call-3",
                "name": "list_tasks",
                "arguments": "{}",
            },
        },
    })
    continuation = [event for event in events if event.kind == "shell_call"][-1]
    assert continuation.payload["response_id"] == "resp-2"
    assert continuation.payload["origin_item_id"] == transcript.payload["item_id"]
    assert continuation.payload["origin_correlation_id"] == shell_events[0].payload["origin_correlation_id"]
    assert continuation.payload["origin_timeline"] == {
        "offset_ms": 40,
        "start_ms": 40,
        "end_ms": None,
    }
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
        "ready", "user_transcript_delta", "assistant_transcript_delta", "user_transcript",
        "assistant_transcript", "usage", "disconnected"
    ]
    assert events[3].payload["fragments"] == [
        {"delta": "hello", "start_ms": 100, "end_ms": 300}
    ]
    assert events[4].payload["transcript"] == "hi"
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

    notes = {"text": "JARVIS NOTES\nWaiting for the user:\n- The report.", "signature": "n1"}
    adapter = OpenAILiveAdapter(session_creator=create, websocket_connector=connect)
    await adapter.connect(session_id="s", generation=4, context={"assistant_notes": notes}, on_event=on_event, sdp_offer="offer")
    await wait_for_sideband(adapter, ("s", 4))
    await adapter.create_response(
        session_id="s",
        generation=4,
        request_id="update-1",
        response_kind="task_update",
        presentation_ids=(),
        facts=({"summary": "The report is ready.", "commentary": True},),
        context={"attention_context": {"items": [{"summary": "The report is ready."}], "total_pending": 1},
            "assistant_notes": notes},
    )
    delegated_update = socket.sent[-2]
    assert delegated_update["type"] == "session.update"
    assert delegated_update["session"]["delegation"]["type"] == "responses"
    instructions = delegated_update["session"]["delegation"]["responses"]["instructions"]
    assert instructions.count("CURRENT_ATTENTION_CHECKLIST=") == 1
    assert "maintained by the conversation observer" in instructions
    commentary = socket.sent[-1]
    assert commentary["type"] == "session.commentary.append"
    assert commentary["delegation_id"] is None
    assert "The report is ready." in commentary["content"]
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
async def test_empty_attention_refresh_does_not_inject_live_context(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    socket = FakeLiveSocket()

    async def create(**_: Any) -> dict[str, Any]:
        return {"session": {"id": "live_empty"}, "transport": {"sdp": "answer"}}

    async def connect(*_: Any, **__: Any) -> FakeLiveSocket:
        return socket

    events: list[VoiceProviderEvent] = []

    async def on_event(_: str, __: int, event: VoiceProviderEvent) -> None:
        events.append(event)

    notes = {"text": "JARVIS NOTES\nWaiting for the user:\n- Nothing.", "signature": "n1"}
    adapter = OpenAILiveAdapter(session_creator=create, websocket_connector=connect)
    await adapter.connect(session_id="s", generation=4, context={"assistant_notes": notes}, on_event=on_event, sdp_offer="offer")
    await wait_for_sideband(adapter, ("s", 4))
    await adapter.create_response(
        session_id="s", generation=4, request_id="empty-1",
        response_kind="task_update", presentation_ids=(), facts=(),
        context={"attention_context": {"items": [], "total_pending": 0, "deltas": []}, "assistant_notes": notes},
    )
    assert [item["type"] for item in socket.sent] == ["session.update"]
    assert not adapter._sessions[("s", 4)].pending_commentary
    submitted = events[-1]
    assert submitted.kind == "assistant_update_submitted"
    assert submitted.payload["submission_ack"] == "no_context_to_append"
    assert submitted.payload["presentation_ids"] == []
    assert "submitted_transcript" not in submitted.payload
    # An empty checklist after a handled update must still refresh the notes.
    # A tombstone is a real change, unlike an empty startup update.
    await adapter.create_response(
        session_id="s", generation=4, request_id="handled-1",
        response_kind="task_update", presentation_ids=(), facts=(),
        context={"attention_context": {"items": [], "total_pending": 0,
            "deltas": [{"presentation_id": "old-update", "state": "handled"}]}, "assistant_notes": notes},
    )
    assert socket.sent[-1]["type"] == "session.thinking.append"
    assert notes["text"] in socket.sent[-1]["content"]
    assert "old-update" not in socket.sent[-1]["content"]
    assert "jarvis-live-update:handled-1:0" in adapter._sessions[("s", 4)].pending_commentary
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
    await wait_for_sideband(adapter, ("s", 5))
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
    for delegation_id, response_id, call_id, offset_ms in (
        ("item_delegation_1", "response-1", "call-1", 500),
        ("item_delegation_2", "response-2", "call-2", 1_700),
    ):
        await adapter.handle_event("s", 6, {
            "type": "session.delegation.created",
            "event_id": f"event-{delegation_id}",
            "offset_ms": offset_ms,
            "delegation": {
                "id": delegation_id,
                "type": "delegation",
                "target": "responses",
                "response_id": response_id,
            },
        })
        await adapter.handle_event("s", 6, {
            "type": "response.event",
            "event_id": f"event-response-created-{response_id}",
            "delegation_id": delegation_id,
            "event": {"type": "response.created", "response": {"id": response_id}},
        })
        await adapter.handle_event("s", 6, {
            "type": "response.event",
            "event_id": f"event-{call_id}",
            "delegation_id": delegation_id,
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
        "item_delegation_1": transcripts["first"],
        "item_delegation_2": transcripts["second"],
    }
    resolution_offsets = {
        event.payload["delegation_id"]: event.payload["delegation_timeline"]["offset_ms"]
        for event in events if event.kind == "delegation_origin_resolved"
    }
    assert resolution_offsets == {
        "item_delegation_1": 500,
        "item_delegation_2": 1_700,
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
        "type": "session.delegation.created",
        "event_id": "event-ambiguous",
        "offset_ms": 2_000,
        "delegation": {
            "id": "item_ambiguous",
            "type": "delegation",
            "target": "responses",
            "response_id": "response",
        },
    })
    await adapter.handle_event("s", 7, {
        "type": "response.event",
        "event_id": "event-response-ambiguous",
        "delegation_id": "item_ambiguous",
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
        "type": "session.delegation.created",
        "event_id": "event-assistant",
        "offset_ms": 150,
        "delegation": {
            "id": "item_assistant",
            "type": "delegation",
            "target": "responses",
            "response_id": "r",
        },
    })
    await adapter.handle_event("s", 8, {
        "type": "response.event",
        "event_id": "event-response-assistant",
        "delegation_id": "item_assistant",
        "event": {"type": "response.completed", "response": {"id": "r"}},
    })
    assert not any(event.kind == "assistant_transcript" for event in events)
    assert not any(event.kind == "assistant_response" for event in events)
    await adapter.handle_event("s", 8, {
        "type": "session.output_transcript.delta", "delta": " after completion", "start_ms": 300, "end_ms": 500,
    })
    await adapter.handle_event("s", 8, {"type": "session.closed"})
    segment = next(event for event in events if event.kind == "assistant_transcript")
    assert segment.payload["transcript"] == "Still speaking after completion"
    await adapter.close(session_id="s", generation=8, reason="test", emit_event=False)
