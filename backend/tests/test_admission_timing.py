"""No-provider admission profiling and trace correctness on real durable SQLite."""
import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import socket
from statistics import median
from time import perf_counter_ns as monotonic_ns
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config import settings
from app.db.session import session_scope
from app.db.tables import Conversation, JarvisDevice, LiveSession
from app.services.jarvis.admission_timing import observe_admission, span
from app.services.tasks.capture import task_capture_service


def summary(values):
    ordered = sorted(values)
    return {"count": len(values), "median_ms": median(ordered),
        "p95_ms": ordered[math.ceil(len(ordered)*.95)-1], "max_ms": max(ordered)}


@pytest.mark.asyncio
async def test_spans_isolate_overlapping_turns_and_do_not_suppress_failure():
    @observe_admission
    async def execute(stage):
        with span(stage):
            await asyncio.sleep(.005)
        return SimpleNamespace(output={"accepted": True})
    first, second = await asyncio.gather(execute("first"), execute("second"))
    for output, stage in ((first.output, "first"), (second.output, "second")):
        timing = output["admission_timing"]
        assert [item["stage"] for item in timing["spans"]] == [stage]
        assert timing["gateway_started_ns"] <= timing["spans"][0]["start_ns"]
        assert timing["spans"][0]["end_ns"] <= timing["gateway_finished_ns"]
    with pytest.raises(ValueError, match="failure"):
        with span("failure"):
            raise ValueError("failure")


@pytest.mark.asyncio
@pytest.mark.diagnostic
async def test_profile_fixture_routing(monkeypatch):
    await profile_fixture_routing(monkeypatch)


@pytest.mark.asyncio
@pytest.mark.diagnostic
async def test_profile_full_offline_delivery(monkeypatch):
    await profile_fixture_routing(monkeypatch, with_delivery=True)


async def profile_fixture_routing(monkeypatch, *, with_delivery=False):
    destination = os.environ.get("JARVIS_OFFLINE_TIMING_REPORT")
    if not destination:
        pytest.skip("Set JARVIS_OFFLINE_TIMING_REPORT to run the offline benchmark")
    from sqlalchemy import select
    from app.db.tables import LiveSessionEvent
    from app.services.jarvis.coordinator import jarvis_coordinator
    from app.services.jarvis.openai_live_adapter import OpenAILiveAdapter
    from test_openai_live_adapter import FakeLiveSocket, wait_for_sideband
    from app.services.jarvis.live import jarvis_live_service
    from app.services.auth import SESSION_COOKIE_NAME, create_session_token
    class Request:
        cookies = {SESSION_COOKIE_NAME: create_session_token()}
        headers = {}
        async def is_disconnected(self):
            return False
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline routing attempted network access")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(settings, "openai_api_key", "fixture-key")
    async with session_scope() as session:
        conversation = Conversation(title="Offline routing")
        session.add(conversation)
        await session.flush()
        live = LiveSession(conversation_id=conversation.id, provider_adapter="openai_live", lifecycle_state="ready")
        session.add(live)
        await session.flush()
    socket_fixture = FakeLiveSocket()
    async def create(**kwargs):
        return {"session": {"id": "fixture"}, "transport": {"sdp": "fixture"}}
    async def connect(*args, **kwargs):
        return socket_fixture
    current = {}
    events = []
    async def event(session_id, generation, value):
        events.append(value)
        if value.kind in {"user_transcript", "delegation_received", "delegation_origin_resolved", "admission_stage", "shell_call"}:
            await jarvis_coordinator.handle_provider_event(session_id, generation, value)
    adapter = OpenAILiveAdapter(session_creator=create, websocket_connector=connect, http_client_factory=forbidden)
    monkeypatch.setattr("app.services.jarvis.coordinator.get_voice_session_adapter", lambda *args: adapter)
    finished = asyncio.Event()
    send_result = adapter.send_tool_result
    async def result(**kwargs):
        await send_result(**kwargs)
        finished.set()
    monkeypatch.setattr(adapter, "send_tool_result", result)
    await adapter.connect(session_id=live.id, generation=1, context={}, on_event=event, sdp_offer="fixture")
    await wait_for_sideband(adapter, (live.id, 1))
    rows = []
    try:
        for index in range(23):
            finished.clear()
            identity = f"sample-{index}"
            current.update(response_id=identity, item={"type": "function_call", "name": "dispatch_task", "call_id": identity,
                "arguments": json.dumps({"goal": f"Draft checklist {index}", "intent_id": identity})})
            await adapter.handle_event(live.id, 1, {"type": "session.input_transcript.delta", "delta": f"Create checklist {index}",
                "start_ms": index*2000, "end_ms": index*2000+1000})
            if with_delivery:
                stream = jarvis_live_service.stream(Request(), cursor=await jarvis_live_service.latest_cursor())
                async def receive():
                    async for frame in stream:
                        envelope = json.loads(frame["data"])
                        payload = envelope.get("payload", {})
                        if envelope.get("event_type") == "brain.tool.result" and payload.get("call_id") == identity:
                            return monotonic_ns(), payload["output"]
                delivery = asyncio.create_task(receive())
            started = monotonic_ns()
            # Leave the fragment pending to exercise the real safe timeline
            # boundary. Fixtures supply completed model calls with zero delay.
            await adapter.handle_event(live.id, 1, {"type": "session.delegation.created", "offset_ms": index*2000+1000,
                "delegation": {"id": identity, "target": "responses"}})
            for value in ({"type": "response.created", "response": {"id": identity}},
                {"type": "response.output_item.done", "response_id": identity, "item": current["item"]}):
                await adapter.handle_event(live.id, 1, {"type": "response.event", "delegation_id": identity, "event": value})
            await asyncio.wait_for(finished.wait(), 3)
            ended = monotonic_ns()
            if with_delivery:
                try:
                    received, delivered = await asyncio.wait_for(delivery, 3)
                    assert delivered.get("accepted")
                    assert delivered.get("task_id")
                finally:
                    if not delivery.done():
                        delivery.cancel()
                        await asyncio.gather(delivery, return_exceptions=True)
                    await stream.aclose()
            async with session_scope() as session:
                records = list((await session.execute(select(LiveSessionEvent).where(LiveSessionEvent.session_id == live.id,
                    LiveSessionEvent.event_type == "brain.tool.result").order_by(LiveSessionEvent.sequence.desc()))).scalars())
            output = next(row.payload_json["output"] for row in records if row.payload_json["call_id"] == identity)
            assert output.get("accepted"), output
            timing = output["admission_timing"]
            tool = next(value.payload for value in events if value.kind == "admission_stage" and value.payload.get("provider_call_id") == identity)
            rows.append({"warmup": index < 3, "elapsed_ms": (ended-started)/1e6,
                "validation_ms": tool["tool_validation_ms"],
                "boundary_and_routing_ms": (tool["tool_item_received_ns"]-started)/1e6,
                "validated_call_to_shell_ms": (timing["shell_started_ns"]-tool["server_monotonic_ns"])/1e6,
                "authority_ms": (timing["authority_ready_ns"]-timing["shell_started_ns"])/1e6,
                "result_record_and_return_ms": (ended-timing["gateway_finished_ns"])/1e6,
                "gateway_ms": timing["gateway_duration_ms"]})
            if with_delivery:
                rows[-1].update(delegation_to_receipt_ms=(received-started)/1e6,
                    task_commit_to_receipt_ms=(received-timing["admission_commit_ns"])/1e6)
            # Drain the fixture's unrelated narrative loop outside measurement.
            for _ in range(100):
                if not adapter._sessions[(live.id, 1)].tool_tasks:
                    break
                await asyncio.sleep(.001)
    finally:
        await adapter.close(session_id=live.id, generation=1, reason="test", emit_event=False)
    selected = [row for row in rows if not row["warmup"]]
    target = Path(destination).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    stage_keys = ["elapsed_ms", "boundary_and_routing_ms", "validation_ms", "validated_call_to_shell_ms",
        "authority_ms", "result_record_and_return_ms", "gateway_ms"]
    if with_delivery:
        stage_keys.extend(["delegation_to_receipt_ms", "task_commit_to_receipt_ms"])
    label = "full-flow" if with_delivery else "routing"
    target.with_name(f"{target.stem}-{label}-managed.json").write_text(json.dumps({
        "schema": "offline-routing-timing.v1", "route": "managed_responses", "network_blocked": True, "failures": 0,
        "provider_delay": "fixture_zero_delay; not real model selection or ASR",
        "receipt_boundary": "actual SSE generator, simulated client; excludes HTTP/Android" if with_delivery else None,
        "summary": {key: summary([row[key] for row in selected]) for key in stage_keys},
        "samples": rows}, indent=2), encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.diagnostic
async def test_profile_pending_capture(monkeypatch):
    destination = os.environ.get("JARVIS_OFFLINE_TIMING_REPORT")
    if not destination:
        pytest.skip("Set JARVIS_OFFLINE_TIMING_REPORT to run the offline benchmark")
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline capture profiling attempted network access")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    async with session_scope() as session:
        conversation = Conversation(title="Pending capture timing")
        device = JarvisDevice(label="Offline capture", credential_hash=sha256(str(uuid4()).encode()).hexdigest(), lock_state="unlocked")
        session.add_all([conversation, device])
        await session.flush()
        live = LiveSession(conversation_id=conversation.id, controller_id=device.id, activation_id=str(uuid4()),
            provider_adapter="fake", lifecycle_state="ready")
        session.add(live)
        await session.flush()
    options = dict(device_id=device.id, session_id=live.id, generation=1, activation_id=live.activation_id)
    now = datetime.now(timezone.utc)
    await task_capture_service.report(**options, capture_id=str(uuid4()), capture_mode="activation", status="captured",
        revision=2, captured_at=now, expires_at=now+timedelta(seconds=90))
    rows = []
    for case, mode, delay in (("ready_after_100ms", "fresh", .1), ("ready_after_300ms", "fresh", .3),
        ("ready_after_1000ms", "fresh", 1.), ("optional_latest_never_ready", "activation", None),
        ("required_latest_never_ready", "fresh", None)):
        fresh = await task_capture_service.request_fresh(session_id=live.id, generation=1, request_identity=case)
        async def report():
            await asyncio.sleep(delay)
            await task_capture_service.report(**options, capture_id=fresh.capture_id, capture_mode="fresh", status="captured",
                revision=2, captured_at=datetime.now(timezone.utc), expires_at=fresh.expires_at.replace(tzinfo=timezone.utc))
        pending = asyncio.create_task(report()) if delay is not None else None
        started = monotonic_ns()
        try:
            collection = await task_capture_service.preflight_collection(**options, request_identity=case, capture_mode=mode)
            assert case != "required_latest_never_ready"
            if case == "optional_latest_never_ready":
                assert collection.missing_roles == ("latest",)
            else:
                assert {role for role, _ in collection.captures} == {"invocation", "latest"}
            outcome = "captured" if delay is not None else "optional_unavailable"
        except ValueError as error:
            assert case == "required_latest_never_ready" and "deadline" in str(error)
            outcome = "required_timeout"
        elapsed = (monotonic_ns()-started)/1e6
        if pending:
            await pending
        rows.append({"case": case, "fixture_capture_delay_ms": None if delay is None else delay*1000,
            "elapsed_ms": elapsed, "outcome": outcome})
    target = Path(destination).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.with_name(f"{target.stem}-pending-capture.json").write_text(json.dumps({
        "schema": "offline-pending-capture-timing.v1", "network_blocked": True,
        "diagnostic_samples": 5, "expected_required_timeouts": 1, "unexpected_failures": 0, "samples": rows}, indent=2), encoding="utf-8")
