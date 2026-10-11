import json
import importlib.util
from pathlib import Path

import pytest

from app.config import settings
from app.services import live_model_diagnostics as module
from app.services.openai_live_adapter import OpenAILiveAdapter
from test_openai_live_adapter import FakeLiveSocket, wait_for_sideband
from test_live_managed_delegation import connected, DELEGATION


def configure(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "ROOT", tmp_path)
    folder = tmp_path / "data/provider-outputs"
    monkeypatch.setattr(settings, "openai_live_diagnostics_dir", str(folder))
    monkeypatch.setattr(settings, "openai_live_reasoning_summary", True)
    return folder


@pytest.mark.asyncio
async def test_recorder_bounds_content_excludes_credentials_and_flushes_once(tmp_path, monkeypatch):
    folder = configure(tmp_path, monkeypatch)
    trace = module.LiveModelDiagnostics(folder, session_id="s", generation=1, configuration={})
    trace.record("selector", {"type": "response.completed", "response": {"id": "r", "output": [{
        "type": "reasoning", "summary": [{"type": "summary_text", "text": "A provider summary"}],
        "encrypted_content": "opaque-secret"}], "client_secret": "secret", "instructions": "private input"}})
    trace.record("voice", {"type": "session.output_audio.delta", "audio": "large-media"})
    assert not folder.exists()
    assert len(trace.events) == 1
    monkeypatch.setattr(module, "MAX_EVENTS", 1)
    trace.record("selector", {"type": "response.output_text.delta", "delta": "overflow"})
    await trace.flush("test")
    first = trace.path.read_bytes()
    await trace.flush("again")
    assert trace.path.read_bytes() == first
    parsed = json.loads(first)
    assert parsed["capture_health"]["dropped"] == 1
    assert not parsed["capture_health"]["complete_capture"]
    assert "A provider summary" in first.decode()
    assert all(value not in first.decode() for value in ("opaque-secret", "private input", "large-media", '"secret"'))
    with pytest.raises(ValueError, match="scoped"):
        module.LiveModelDiagnostics(tmp_path, session_id="s", generation=1, configuration={})


@pytest.mark.asyncio
async def test_managed_records_complete_provider_outputs_without_db_events(tmp_path, monkeypatch):
    folder = configure(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "openai_api_key", "fixture-key")
    socket, events = FakeLiveSocket(), []
    async def creator(**kwargs):
        assert kwargs["session_config"]["delegation"]["responses"]["reasoning"]["summary"] == "auto"
        return {"session": {"id": "provider"}, "transport": {"sdp": "answer"}}
    async def connector(*args, **kwargs):
        return socket
    async def event(session, generation, value):
        events.append(value)
    adapter = OpenAILiveAdapter(session_creator=creator, websocket_connector=connector)
    await adapter.connect(session_id="s", generation=1, context={}, on_event=event, sdp_offer="offer")
    await wait_for_sideband(adapter, ("s", 1))
    await adapter.handle_event("s", 1, {"type": "session.output_transcript.delta", "delta": "I will create it.", "start_ms": 0, "end_ms": 100})
    for inner in [{"type": "response.created", "response": {"id": "r"}},
        {"type": "response.reasoning_summary_text.delta", "delta": "A short task needs one call."},
        {"type": "response.function_call_arguments.delta", "delta": '{"goal":'},
        {"type": "response.completed", "response": {"id": "r", "status": "completed", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "Done."}]}],
            "usage": {"output_tokens": 20, "output_tokens_details": {"reasoning_tokens": 4}}}}]:
        await adapter.handle_event("s", 1, {"type": "response.event", "delegation_id": "d", "event": inner})
    assert not folder.exists()
    assert not any(row.kind.startswith("diagnostic") for row in events)
    await adapter.close(session_id="s", generation=1, reason="test", emit_event=False)
    trace = json.loads(next(folder.glob("*.json")).read_text())
    assert trace["capture_health"]["complete_capture"]
    assert any(row["event"]["type"] == "response.function_call_arguments.delta" and row["response_id"] == "r" for row in trace["events"])
    assert any(row["event"]["type"] == "session.output_transcript.delta" for row in trace["events"])
    assert any(row["event"]["type"] == "response.completed" and row["event"]["response"]["output"] for row in trace["events"])


@pytest.mark.asyncio
async def test_managed_captures_stream_items_when_terminal_output_is_empty(tmp_path, monkeypatch):
    folder = configure(tmp_path, monkeypatch)
    adapter, _ = await connected(monkeypatch, [])
    await adapter.handle_event("session", 1, DELEGATION)
    output = {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "A short reply."}]}
    for inner in [{"type": "response.created", "response": {"id": "r"}},
        {"type": "response.output_text.delta", "response_id": "r", "delta": "A short reply."},
        {"type": "response.output_item.done", "response_id": "r", "item": output},
        {"type": "response.completed", "response": {"id": "r", "status": "completed", "output": [],
            "service_tier": "priority", "usage": {"input_tokens": 100, "output_tokens": 30,
                "output_tokens_details": {"reasoning_tokens": 5}}}}]:
        await adapter.handle_event("session", 1, {"type": "response.event", "delegation_id": "selection", "event": inner})
    await adapter.close(session_id="session", generation=1, reason="test", emit_event=False)
    trace = json.loads(next(folder.glob("*.json")).read_text())
    assert trace["configuration"]["delegation_mode"] == "responses"
    final = next(row["event"]["response"] for row in trace["events"] if row["event"]["type"] == "response.completed")
    assert final["usage"]["output_tokens_details"]["reasoning_tokens"] == 5
    assert final["output"] == []
    assert any(row["event"].get("item") == output for row in trace["events"])


def test_summary_is_not_requested_in_normal_mode(monkeypatch):
    monkeypatch.setattr(settings, "openai_live_reasoning_summary", False)
    assert "summary" not in OpenAILiveAdapter._router_configuration({})["reasoning"]


@pytest.mark.asyncio
async def test_terminal_checkpoint_survives_rejected_coordinator_event(tmp_path, monkeypatch):
    folder = configure(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "openai_api_key", "fixture-key")
    socket = FakeLiveSocket()
    async def creator(**kwargs):
        return {"session": {"id": "provider"}, "transport": {"sdp": "answer"}}
    async def connector(*args, **kwargs): return socket
    async def event(session, generation, value):
        if value.kind in {"disconnected", "provider_error"}: raise ValueError("Stale session")
    adapter = OpenAILiveAdapter(session_creator=creator, websocket_connector=connector)
    await adapter.connect(session_id="s", generation=1, context={}, on_event=event, sdp_offer="offer")
    await wait_for_sideband(adapter, ("s", 1))
    await adapter.handle_event("s", 1, {"type": "session.output_transcript.delta", "delta": "Visible output", "start_ms": 0, "end_ms": 100})
    with pytest.raises(ValueError, match="Stale"):
        await adapter.handle_event("s", 1, {"type": "session.closed", "usage": {"seconds": 3}})
    path = next(folder.glob("*.json"))
    document = json.loads(path.read_text())
    assert not document["final_adapter_flush"]
    assert any(row["event"]["type"] == "session.closed" for row in document["events"])
    await adapter._report_transport_error(adapter._sessions[("s", 1)], {"reason": "sideband_closed"})
    assert "selector.transport.closed" in path.read_text()
    await adapter.close(session_id="s", generation=1, reason="test", emit_event=False)
    assert json.loads(path.read_text())["final_adapter_flush"]


@pytest.mark.parametrize("empty_terminal_output", [False, True])
def test_output_analysis_distinguishes_reasoning_item_from_completed_arguments(empty_terminal_output):
    path = Path(__file__).resolve().parents[2] / "scripts/summarize-model-outputs.py"
    spec = importlib.util.spec_from_file_location("model_output_analysis", path)
    analyzer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(analyzer)
    def row(ms, kind, **fields):
        return {"observed_ns": ms * 1_000_000, "stream": "selector",
            "response_id": "r", "delegation_id": "d", "event": {"type": kind, **fields}}
    events = [row(0, "selector.request.started"), row(10, "selector.http.headers"),
        row(20, "response.created"),
        row(30, "response.output_item.done", item={"id": "reason", "type": "reasoning"}),
        row(40, "response.function_call_arguments.delta", item_id="call", delta='{"goal":'),
        row(60, "response.function_call_arguments.delta", item_id="call", delta='"test"}'),
        row(65, "response.output_item.done", item={"id": "call", "type": "function_call", "name": "dispatch_task", "arguments": '{"goal":"test"}'}),
        row(70, "response.completed", response={"status": "completed", "output": [] if empty_terminal_output else [{
            "id": "call", "type": "function_call", "name": "dispatch_task", "arguments": '{"goal":"test"}'}],
            "usage": {"output_tokens": 12, "output_tokens_details": {"reasoning_tokens": 3}}})]
    result = analyzer.analyze({"events": events, "session_id": "s", "capture_health": {"complete_capture": True},
        "configuration": {"requested_tools": [{"name": "dispatch_task", "required": ["goal"]}]}})["responses"][0]
    assert result["timings_ms"]["http_start_to_first_arguments_delta"] == 40
    assert result["timings_ms"]["first_to_last_argument_delta"] == 20
    assert result["timings_ms"]["last_argument_delta_to_item_done"] == 5
    assert result["non_reasoning_output_tokens"] == 9
    assert result["calls"][0]["arguments"] == {"goal": "test"}
