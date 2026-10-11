"""Managed selection overlaps transcript grouping and uses one guarded tool path."""
import json

import pytest

from app.config import settings
from app.services.openai_live_adapter import OpenAILiveAdapter
from test_openai_live_adapter import FakeLiveSocket, wait_for_sideband


DELEGATION = {"type": "session.delegation.created", "offset_ms": 900,
    "delegation": {"id": "selection", "target": "responses", "response_id": "r"}}


async def connected(monkeypatch, events):
    monkeypatch.setattr(settings, "openai_api_key", "fixture-key")
    socket = FakeLiveSocket()
    async def creator(**kwargs):
        config = kwargs["session_config"]["delegation"]
        assert config["type"] == "responses"
        assert config["responses"]["model"] == settings.openai_live_responses_model
        assert config["responses"]["service_tier"] == "priority"
        assert all(tool["strict"] is False for tool in config["responses"]["tools"])
        return {"session": {"id": "provider"}, "transport": {"sdp": "answer"}}
    async def connector(*args, **kwargs):
        return socket
    async def event(session, generation, value):
        events.append(value)
    def forbidden():
        raise AssertionError("Managed selection attempted an application HTTP request")
    adapter = OpenAILiveAdapter(session_creator=creator, websocket_connector=connector, http_client_factory=forbidden)
    await adapter.connect(session_id="session", generation=1, context={}, on_event=event, sdp_offer="offer")
    await wait_for_sideband(adapter, ("session", 1))
    return adapter, socket


def envelope(inner):
    return {"type": "response.event", "delegation_id": "selection", "event": inner}


@pytest.mark.asyncio
async def test_attention_refresh_preserves_two_calls_from_segmented_user_request(monkeypatch):
    events = []
    adapter, socket = await connected(monkeypatch, events)
    try:
        context = {"attention_context": {"items": [
            {"summary": "Earlier Python task cancelled"}, {"summary": "Earlier notes outline completed"}],
            "total_pending": 2}}
        await adapter.create_response(session_id="session", generation=1, request_id="older-updates",
            response_kind="task_update", presentation_ids=(), facts=(), context=context)
        update = next(message for message in socket.sent if message["type"] == "session.update")
        assert update["session"]["delegation"]["responses"]["instructions"] == adapter._router_instructions(context)
        for text, start, end in [("Create two separate tasks. Compare Python lists and ", 3000, 6400),
                                 ("tuples. Outline a notes application.", 6800, 9600)]:
            await adapter.handle_event("session", 1, {"type": "session.input_transcript.delta",
                "delta": text, "start_ms": start, "end_ms": end})
        await adapter._flush_transcript(adapter._sessions[("session", 1)], "user")
        await adapter.handle_event("session", 1, {**DELEGATION, "offset_ms": 9800})
        await adapter.handle_event("session", 1, envelope({"type": "response.created", "response": {"id": "r"}}))
        for intent, goal in [("compare", "Compare Python lists and tuples"), ("notes", "Outline a notes application")]:
            await adapter.handle_event("session", 1, envelope({"type": "response.output_item.done", "response_id": "r",
                "item": {"type": "function_call", "call_id": intent, "name": "dispatch_task",
                    "arguments": json.dumps({"intent_id": intent, "goal": goal})}}))
        calls = [event for event in events if event.kind == "shell_call"]
        assert len(calls) == 2
        assert {event.payload["arguments"]["intent_id"] for event in calls} == {"compare", "notes"}
        assert all(event.payload["name"] == "dispatch_task" for event in calls)
    finally:
        await adapter.close(session_id="session", generation=1, reason="test", emit_event=False)


@pytest.mark.asyncio
async def test_managed_selection_starts_while_transcript_is_provisional(monkeypatch):
    events = []
    adapter, _ = await connected(monkeypatch, events)
    try:
        await adapter.handle_event("session", 1, {"type": "session.input_transcript.delta",
            "delta": "Create a notes app plan", "start_ms": 0, "end_ms": 1000})
        await adapter.handle_event("session", 1, DELEGATION)
        await adapter.handle_event("session", 1, envelope({"type": "response.created", "response": {"id": "r"}}))
        assert any(e.kind == "admission_stage" and e.payload["stage"] == "selector_response_started" for e in events)
        assert not any(e.kind == "user_transcript" for e in events)
        await adapter._flush_transcript(adapter._sessions[("session", 1)], "user")
        origin = next(e for e in events if e.kind == "delegation_origin_resolved")
        assert origin.payload["origin_item_id"] == "live-user:provider:1"
    finally:
        await adapter.close(session_id="session", generation=1, reason="test", emit_event=False)


@pytest.mark.asyncio
async def test_complete_stream_call_routes_before_terminal_and_continues_once(monkeypatch):
    events = []
    adapter, socket = await connected(monkeypatch, events)
    item = {"type": "function_call", "call_id": "call", "name": "dispatch_task",
        "arguments": '{"goal":"Draft a notes app plan","intent_id":"notes-plan"}'}
    try:
        await adapter.handle_event("session", 1, DELEGATION)
        await adapter.handle_event("session", 1, envelope({"type": "response.created", "response": {"id": "r"}}))
        await adapter.handle_event("session", 1, envelope({"type": "response.function_call_arguments.delta", "delta": '{"goal":'}))
        assert not any(e.kind == "shell_call" for e in events)
        done = envelope({"type": "response.output_item.done", "response_id": "r", "item": item})
        await adapter.handle_event("session", 1, done)
        await adapter.handle_event("session", 1, done)
        assert len([e for e in events if e.kind == "shell_call"]) == 1
        assert not adapter._sessions[("session", 1)].responses[("selection", "r")].terminal
        result = {"accepted": True, "task_id": "task", "admission_timing": {"private": True},
            "result": {"status": "queued", "admission_timing": {"private": True}}}
        await adapter.send_tool_result(session_id="session", generation=1, call_id="call", output=result)
        await adapter.send_tool_result(session_id="session", generation=1, call_id="call", output=result)
        assert not any(m["type"] == "response.create" for m in socket.sent)
        terminal = envelope({"type": "response.completed", "response": {"id": "r", "output": [],
            "service_tier": "priority", "usage": {"input_tokens": 10, "output_tokens": 5}}})
        await adapter.handle_event("session", 1, terminal)
        await adapter.handle_event("session", 1, terminal)
        results = [m for m in socket.sent if m["type"] == "response.item.create"]
        assert len(results) == 1
        assert "admission_timing" not in results[0]["item"]["output"]
        assert len([m for m in socket.sent if m["type"] == "response.create"]) == 1
        assert len([e for e in events if e.kind == "delegation_usage"]) == 1
    finally:
        await adapter.close(session_id="session", generation=1, reason="test", emit_event=False)


@pytest.mark.asyncio
async def test_terminal_output_cannot_substitute_for_a_complete_stream_item(monkeypatch):
    events = []
    adapter, _ = await connected(monkeypatch, events)
    try:
        await adapter.handle_event("session", 1, envelope({"type": "response.completed", "response": {"id": "r",
            "output": [{"type": "function_call", "call_id": "unobserved", "name": "dispatch_task",
                "arguments": '{"goal":"Draft a plan","intent_id":"plan"}'}]}}))
        assert not any(e.kind == "shell_call" for e in events)
    finally:
        await adapter.close(session_id="session", generation=1, reason="test", emit_event=False)
