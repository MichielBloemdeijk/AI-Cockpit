"""No-provider admission profiling and trace correctness on real durable SQLite."""
import asyncio
from collections import defaultdict
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
from app.services.admission_timing import observe_admission, span, timed
from app.services.jarvis_brain import RestrictedBrainGateway
from app.services.task_capture import task_capture_service
from app.services.task_workflows import load_workflow
from app.services.result_telemetry import observation_queue


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
@pytest.mark.parametrize("trial", range(3))
@pytest.mark.diagnostic
async def test_profile_offline_admission(tmp_path, monkeypatch, trial):
    await profile_offline_admission(monkeypatch, trial)


@pytest.mark.asyncio
@pytest.mark.parametrize("trial", range(2))
@pytest.mark.diagnostic
async def test_profile_interleaved_observation_delivery(monkeypatch, trial):
    await profile_offline_admission(monkeypatch, trial, paired=True)


async def profile_offline_admission(monkeypatch, trial, *, paired=False):
    # This is an opt-in benchmark, not a wall-clock pass/fail threshold in CI.
    destination = os.environ.get("JARVIS_OFFLINE_TIMING_REPORT")
    if not destination:
        pytest.skip("Set JARVIS_OFFLINE_TIMING_REPORT to run the offline benchmark")
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline profiling attempted network access")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "task_orchestration_enabled", False)
    definition = load_workflow("screen_document@1")
    monkeypatch.setattr(settings, "task_executor", "codex")
    monkeypatch.setattr(settings, "task_workflow_allowlist", f"screen_document@1:{definition.digest}")
    async with session_scope() as session:
        conversation = Conversation(title="Offline timing fixture")
        device = JarvisDevice(label="Offline timing", credential_hash=sha256(str(uuid4()).encode()).hexdigest(), lock_state="unlocked")
        session.add_all([conversation, device])
        await session.flush()
        live = LiveSession(conversation_id=conversation.id, provider_adapter="fake", lifecycle_state="ready",
            controller_id=device.id, activation_id=str(uuid4()),
            workflow_catalog_revision=sha256(settings.task_workflow_allowlist.encode()).hexdigest())
        session.add(live)
        await session.flush()
    capture_options = dict(device_id=device.id, session_id=live.id, generation=live.generation, activation_id=live.activation_id)
    now = datetime.now(timezone.utc)
    await task_capture_service.report(**capture_options, capture_id=str(uuid4()), capture_mode="activation",
        status="captured", revision=2, captured_at=now, expires_at=now+timedelta(seconds=90))
    gateway = RestrictedBrainGateway()
    from app.services import result_telemetry
    queued_observe = result_telemetry.observe
    @timed("result_observation_write")
    async def awaited_observe(page_id=None, **kwargs):
        # Controlled test comparator: current SQL/validation, one awaited
        # transaction per observation. No legacy production implementation.
        await result_telemetry._write_batch([dict(type="metric", page_id=page_id, metric=kwargs)])
    rows = []
    initial = None
    pulses = []
    stop_monitor = asyncio.Event()
    async def monitor_loop():
        while not stop_monitor.is_set():
            started = monotonic_ns()
            try:
                # Above Windows' 15.625ms event-loop clock resolution; short
                # deadlines can fire early and distort the probe's cadence.
                await asyncio.wait_for(stop_monitor.wait(), .05)
            except TimeoutError:
                pass
            ended = monotonic_ns()
            pulses.append(dict(start_ns=started, end_ns=ended,
                lateness_ms=max(0., (ended-started)/1e6-50.)))
    monitor = asyncio.create_task(monitor_loop())
    try:
        for index in range(23):
            # Interleave cases. First three of each case are warm-up; retain them
            # separately. Each trial gets a newly migrated, independent database.
            modes = ("queued", "awaited") if index % 2 == 0 else ("awaited", "queued")
            cases = [(case, mode) for case in ("plain", "document", "screen_ready", "continuation", "replay")
                for mode in (modes if paired else ("queued",))]
            for case, delivery in cases:
                if paired:
                    assert await observation_queue.drain()
                    monkeypatch.setattr(result_telemetry, "observe", queued_observe if delivery == "queued" else awaited_observe)
                turn = f"turn-{case}-{index}-{delivery}"
                common = dict(session_id=live.id, generation=live.generation, conversation_id=conversation.id,
                    origin_turn_id=None, turn_scope=turn, call_id=turn)
                arguments = {"goal": f"Draft a checklist {case} {index}", "intent_id": turn}
                tool = "dispatch_task"
                if case == "document":
                    arguments["result"] = {"mode": "new", "kind": "document.v1"}
                elif case == "screen_ready":
                    tool = "start_workflow"
                    arguments["definition"] = "screen_document@1"
                    fresh = await task_capture_service.request_fresh(session_id=live.id, generation=live.generation, request_identity=turn)
                    await task_capture_service.report(**capture_options, capture_id=fresh.capture_id, capture_mode="fresh",
                        status="captured", revision=2, captured_at=datetime.now(timezone.utc),
                        expires_at=fresh.expires_at.replace(tzinfo=timezone.utc))
                    common["capture_request_identity"] = turn
                elif case == "continuation":
                    tool = "continue_task"
                    arguments = {"task_id": initial.output["task_id"], "content": f"Expand checklist {index}", "intent_id": turn}
                elif case == "replay":
                    common = initial_call.copy()
                    common["call_id"] = f"delivery-{index}"
                    arguments = initial_arguments.copy()
                start = monotonic_ns()
                result = await gateway.execute(**common, tool_name=tool, arguments=arguments)
                elapsed = (monotonic_ns()-start)/1_000_000
                assert result.output["accepted"]
                timing = result.output["admission_timing"]
                assert timing["gateway_duration_ms"] <= elapsed
                if case == "plain" and initial is None:
                    initial, initial_call, initial_arguments = result, common.copy(), arguments.copy()
                if case == "replay":
                    assert result.output["task_id"] == initial.output["task_id"]
                    assert timing["commit_is_original"]
                    assert timing["admission_commit_ns"] == initial.output["admission_timing"]["admission_commit_ns"]
                else:
                    assert timing["gateway_started_ns"] <= timing["admission_commit_ns"] <= timing["gateway_finished_ns"]
                rows.append({"case": case, "observation_delivery": delivery, "warmup": index < 3,
                    "elapsed_ms": elapsed, "timing": timing})
                if paired:
                    assert await observation_queue.drain()
    finally:
        stop_monitor.set()
        await monitor
    assert await observation_queue.drain()
    background = observation_queue.status()
    assert background["failed"] == background["dropped"] == background["pending"] == 0, background
    groups = {}
    for case in {row["case"] for row in rows}:
        selected = [row for row in rows if row["case"] == case and not row["warmup"]]
        stages = defaultdict(list)
        for row in selected:
            per_call = defaultdict(float)
            for item in row["timing"]["spans"]:
                per_call[item["stage"]] += item["duration_ms"]
            for stage, value in per_call.items():
                stages[stage].append(value)
        groups[case] = {"total": summary([row["elapsed_ms"] for row in selected]),
            "stages": {stage: summary(values) for stage, values in stages.items()}}
    target = Path(destination).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    label = "paired-trial" if paired else "trial"
    target.with_name(f"{target.stem}-{label}-{trial}.json").write_text(json.dumps({
        "schema": "offline-admission-timing.v1", "trial": trial, "network_blocked": True,
        "database": "independent migrated SQLite WAL synchronous=FULL, runtime NullPool",
        "clock": "server_monotonic", "failures": 0, "samples_per_case_per_delivery": 20,
        "comparison": "interleaved awaited versus queued observations using current SQL" if paired else None,
        "observation_queue": background, "event_loop_target_ms": 50,
        "event_loop_observations": pulses,
        "summary": groups, "samples": rows}, indent=2), encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.diagnostic
async def test_profile_sse_delivery(monkeypatch):
    destination = os.environ.get("JARVIS_OFFLINE_TIMING_REPORT")
    if not destination:
        pytest.skip("Set JARVIS_OFFLINE_TIMING_REPORT to run the offline benchmark")
    from app.db.tables import JarvisLiveEvent
    from app.services.auth import SESSION_COOKIE_NAME, create_session_token
    from app.services.jarvis_live import jarvis_live_service
    class Request:
        cookies = {SESSION_COOKIE_NAME: create_session_token()}
        async def is_disconnected(self):
            return False
    # Controlled offsets across the full polling period. This measures real
    # generator/DB delay; HTTP framing, network and Android are excluded.
    rows = []
    for index in range(20):
        cursor = await jarvis_live_service.latest_cursor()
        stream = jarvis_live_service.stream(Request(), cursor=cursor)
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(.025 + index*.025)
        started = monotonic_ns()
        async with session_scope() as session:
            session.add(JarvisLiveEvent(event_type="brain.tool.result", source_domain="session", source_id="offline",
                idempotency_key=str(uuid4()), payload_json={"server_monotonic_ns": started, "output": {"accepted": True}}))
        committed = monotonic_ns()
        frame = await asyncio.wait_for(pending, 2)
        received = monotonic_ns()
        data = json.loads(frame["data"])
        assert data["delivery_timing"]["result_record_started_ns"] == started
        assert committed <= data["delivery_timing"]["sse_emitted_ns"] <= received
        rows.append({"offset_ms": 25+index*25, "commit_to_generator_receipt_ms": (received-committed)/1e6,
            "record_to_generator_receipt_ms": (received-started)/1e6})
        await stream.aclose()
    target = Path(destination).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.with_name(f"{target.stem}-sse.json").write_text(json.dumps({"schema": "offline-sse-timing.v1",
        "clock_source": "python.perf_counter_ns", "failures": 0,
        "summary": summary([row["commit_to_generator_receipt_ms"] for row in rows]), "samples": rows}, indent=2), encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.diagnostic
async def test_profile_result_stream_polling_vs_commit_trigger(monkeypatch):
    destination = os.environ.get("JARVIS_OFFLINE_TIMING_REPORT")
    if not destination:
        pytest.skip("Set JARVIS_OFFLINE_TIMING_REPORT for result delivery profiling")
    from app.api import results
    from app.services.commit_signals import result_event_committed
    from app.db.tables import ResultPage
    from app.services.task_results import append_result_event
    from test_result_platform import page_fixture
    from test_commit_signals import Request
    page = await page_fixture()
    actual_wait = result_event_committed.wait
    rows = []
    for index in range(20):
        for mode in (("polling", "triggered") if index % 2 == 0 else ("triggered", "polling")):
            waiting = asyncio.Event()
            async def wait(observed, *, timeout):
                waiting.set()
                if mode == "polling":
                    await asyncio.sleep(timeout)
                else:
                    await actual_wait(observed, timeout=timeout)
            monkeypatch.setattr(result_event_committed, "wait", wait)
            stream = results.result_events(page.public_id, Request(), None)
            await anext(stream)
            pending = asyncio.create_task(anext(stream))
            try:
                await asyncio.wait_for(waiting.wait(), 1)
                await asyncio.sleep(.025+index*.025)
                async with session_scope() as session:
                    await append_result_event(session, await session.get(ResultPage, page.id), "action_state",
                        event_key=f"diagnostic:{index}:{mode}")
                committed = monotonic_ns()
                frame = await asyncio.wait_for(pending, 2)
                received = monotonic_ns()
                assert frame["event"] == "result"
                rows.append(dict(mode=mode, offset_ms=25+index*25,
                    commit_to_receipt_ms=(received-committed)/1e6))
            finally:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
                await stream.aclose()
    assert await observation_queue.drain()
    health = observation_queue.status()
    assert health["failed"] == health["dropped"] == health["pending"] == 0, health
    target = Path(destination).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.with_name(f"{target.stem}-result-delivery.json").write_text(json.dumps(dict(
        schema="offline-result-delivery-comparison.v1", failures=0, observation_queue=health,
        comparison="interleaved 500ms wait versus commit signal; same current reads",
        summary={mode: summary([row["commit_to_receipt_ms"] for row in rows if row["mode"] == mode])
            for mode in ("polling", "triggered")}, samples=rows), indent=2), encoding="utf-8")


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
    from app.services.jarvis_coordinator import jarvis_coordinator
    from app.services.openai_live_adapter import OpenAILiveAdapter
    from test_openai_live_adapter import FakeLiveSocket, wait_for_sideband
    from app.services.jarvis_live import jarvis_live_service
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
    monkeypatch.setattr("app.services.jarvis_coordinator.get_voice_session_adapter", lambda *args: adapter)
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
    assert await observation_queue.drain()
    background = observation_queue.status()
    assert background["failed"] == background["dropped"] == background["pending"] == 0, background
    target = Path(destination).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.with_name(f"{target.stem}-pending-capture.json").write_text(json.dumps({
        "schema": "offline-pending-capture-timing.v1", "network_blocked": True,
        "observation_queue": background,
        "diagnostic_samples": 5, "expected_required_timeouts": 1, "unexpected_failures": 0, "samples": rows}, indent=2), encoding="utf-8")
