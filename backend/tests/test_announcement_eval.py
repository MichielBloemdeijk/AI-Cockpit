"""Network-free contracts and evidence fixtures for the isolated evaluation."""
from copy import deepcopy
from pathlib import Path
import json
import pytest

from app.announcement_eval.grading import grade, recommendation
from app.announcement_eval.profiles import append_commands, profile, revise_prompt, RELEVANCE_POLICY


def fixtures(expectation="required"):
    evidence = {"expectation": expectation, "input_understood": True, "recording_complete": True,
        "playback_verified": True, "clock_valid": True, "assistant_audio_epoch_ms": 1000,
        "target_task_ids": ["a"], "eligible_ms": {"a": 2000}, "state": {
            "tasks": [{"id": "a", "status": "completed", "finished_at": 2000}], "presentations": [],
            "provider_events": [{"event": {"type": "session.output_transcript.delta", "delta": "The page is ready"}}]}}
    words = [{"word": word, "start": 2 + i / 4, "end": 2.25 + i / 4} for i, word in enumerate("The page is ready".split())]
    judgment = {"claims": [{"task_id": "a", "outcome": "completed", "start_word": 0, "end_word": 3, "complete": True}],
        "relevant": True, "interruption_ok": True}
    return evidence, {"words": words}, judgment


@pytest.mark.parametrize("name,channel", [("commentary_relevance", "commentary"), ("thinking_relevance", "thinking"),
    ("thinking_reconsider", "thinking"), ("commentary_control", "commentary")])
def test_terminal_profile_routing_preserves_verified_facts(name, channel):
    fact = {"event_type": "task.completed", "task_id": "a", "summary": "The page is ready", "commentary": False}
    command = {"event_id": "jarvis-live-update:x:0", "delegation_id": None,
        "type": "session.thinking.append", "content": "Quiet context. " + json.dumps(fact)}
    commands = append_commands(command, profile(name))
    assert commands[0]["type"] == "session." + channel + ".append"
    content = commands[0]["content"]
    parsed = json.loads(content[content.index("{"):])
    assert parsed == {**fact, "commentary": channel == "commentary"}
    assert command["content"].startswith("Quiet context")
    assert len(commands) == (2 if name == "thinking_reconsider" else 1)
    if len(commands) == 2:
        assert commands[1]["type"] == "session.instructions.append"
        assert "remain silent" in commands[1]["content"]


@pytest.mark.parametrize("kind", ["task.question", "reminder.due", "task.progress", "presentation.handled"])
def test_nonterminal_delivery_preserves_existing_contract(kind):
    command = {"event_id": "jarvis-live-update:x:0", "content": json.dumps({"event_type": kind})}
    assert append_commands(command, profile("commentary_relevance")) == [command]


def test_baseline_and_unknown_profiles():
    command = {"event_id": "jarvis-live-update:x", "content": "unchanged"}
    assert append_commands(command, profile("baseline")) == [command]
    with pytest.raises(ValueError): profile("production")
    assert revise_prompt("fixed acknowledgment rules", profile("baseline")) == "fixed acknowledgment rules"
    assert RELEVANCE_POLICY in revise_prompt("fixed acknowledgment rules", profile("thinking_relevance"))


@pytest.mark.parametrize("intent", ["requested", "discretionary", "suppressed"])
@pytest.mark.parametrize("name", ["thinking_preference", "preference_routed"])
def test_preference_delivery_uses_current_intent_and_retains_truth_and_handles(name, intent):
    fact = {"event_type": "task.completed", "task_id": "a", "goal": "Hello World page",
        "summary": "The page is ready", "presentation_id": "p", "exposure_token": "x"}
    command = {"event_id": "jarvis-live-update:x:0", "delegation_id": None, "content": json.dumps(fact)}
    preference = {"intent": intent, "reason": "User preference", "revision": 2, "recorded": True}
    actual = append_commands(command, profile(name), preference)[0]
    expected = "commentary" if name == "preference_routed" and intent == "requested" else "thinking"
    assert actual["type"] == f"session.{expected}.append"
    parsed = json.loads(actual["content"][actual["content"].index("{"):])
    assert parsed == {k: v for k, v in fact.items() if k != "goal"} | {"notification": preference}
    assert len(actual["content"].encode()) < 1600
    assert "do not announce" in actual["content"] if intent == "suppressed" else True
    with pytest.raises(ValueError, match="authoritative"):
        append_commands(command, profile(name))


def test_preference_prompts_separate_live_role_from_backend_and_preserve_baseline():
    from app.announcement_eval.profiles import revise_live_prompt, revise_backend_prompt
    from app.announcement_eval.preferences import tool_definitions
    from app.services.jarvis_tool_catalog import get_jarvis_tool_definitions
    original = get_jarvis_tool_definitions("openai_live")
    amended = tool_definitions(original)
    assert "notification_intent" in original[0]["parameters"]["properties"]
    assert "notification_intent" in amended[0]["parameters"]["properties"]
    live = revise_live_prompt("Full backend procedure", profile("thinking_preference"), {"activation_source": "wake_word"})
    assert "Full backend procedure" not in live and "keep listening" in live.lower()
    assert "delegate later preferences" in live and "Listen quietly" in live
    assert "set_task_update_policy" in revise_backend_prompt("Existing tools", profile("thinking_preference"))
    assert revise_live_prompt("unchanged", profile("baseline"), {}) == "unchanged"


def test_discretionary_payload_cannot_override_a_later_user_request_or_promise():
    fact = {"event_type": "task.completed", "task_id": "a", "summary": "The page is ready"}
    command = {"event_id": "jarvis-live-update:x:0", "content": json.dumps(fact)}
    for recorded in (True, False):
        result = append_commands(command, profile("preference_routed"),
            {"intent": "discretionary", "reason": "Initial request", "revision": int(recorded), "recorded": recorded})[0]
        assert "No completion announcement was requested" not in result["content"]
        assert "does not establish" in result["content"] and "save any correction" in result["content"]


@pytest.mark.parametrize("intent", ["requested", "discretionary", "suppressed"])
def test_model_only_sequences_do_not_route_based_on_intent_and_preserve_verified_handles(intent):
    from app.announcement_eval.profiles import MODEL_ONLY_SEQUENCES, MODEL_ONLY_RECONSIDER, RECONSIDER, CONDITIONAL_OUTCOME
    fact = {"event_type": "task.completed", "task_id": "a", "goal": "Hello World page",
        "summary": "The page is ready", "presentation_id": "p", "exposure_token": "x"}
    command = {"event_id": "jarvis-live-update:x:0", "delegation_id": None, "content": json.dumps(fact)}
    for name, channels in MODEL_ONLY_SEQUENCES.items():
        commands = append_commands(command, profile(name),
            {"intent": intent, "reason": "Actual user preference", "revision": 2, "recorded": True})
        assert tuple(c["type"] for c in commands) == tuple(f"session.{c}.append" for c in channels)
        assert len({c["event_id"] for c in commands}) == len(commands)
        assert all(c["delegation_id"] is None for c in commands)
        facts = [json.loads(c["content"][c["content"].index("{"):]) for c in commands if "{" in c["content"]]
        assert facts and all(f["task_id"] == "a" and f["exposure_token"] == "x" and f["notification"]["intent"] == intent for f in facts)
        for c in commands:
            if c["type"] == "session.instructions.append":
                assert any(text in c["content"] for text in (MODEL_ONLY_RECONSIDER, RECONSIDER, CONDITIONAL_OUTCOME))
                assert "silence" in c["content"] or "silent" in c["content"]
            assert len(c["content"].encode()) < 2000


@pytest.mark.asyncio
async def test_receipt_ordering_requires_exact_session_generation_and_event_without_speech_claim():
    import asyncio
    from types import SimpleNamespace
    from app.announcement_eval.server import AppendDelivery
    delivery = AppendDelivery()
    provider = SimpleNamespace(session_id="s", generation=2)
    commands = [{"type": "session.thinking.append", "event_id": "first"},
        {"type": "session.instructions.append", "event_id": "second"}]
    sent, records = [], []
    async def send(provider, command): sent.append(command["event_id"])
    task = asyncio.create_task(delivery.send(provider, commands, send,
        lambda stream, event, **identity: records.append(event), barrier=True, timeout=.2))
    await asyncio.sleep(.01)
    assert sent == ["first"]
    for sid, gen, kind, eid in [("other", 2, "session.thinking.appended", "first"),
        ("s", 1, "session.thinking.appended", "first"), ("s", 2, "session.commentary.appended", "first"),
        ("s", 2, "session.thinking.appended", "wrong")]:
        delivery.receipt(sid, gen, {"type": kind, "client_event_id": eid})
    await asyncio.sleep(.01)
    assert sent == ["first"]
    delivery.receipt("s", 2, {"type": "session.thinking.appended", "client_event_id": "first"})
    await task
    assert sent == ["first", "second"] and not delivery.pending
    assert not any(e.get("type") == "communication.completed" for e in records)


@pytest.mark.asyncio
async def test_receipt_barrier_timeout_preserves_missing_instruction_and_cleans_pending():
    from types import SimpleNamespace
    from app.announcement_eval.server import AppendDelivery
    delivery = AppendDelivery()
    sent, records = [], []
    async def send(provider, command): sent.append(command["event_id"])
    with pytest.raises(TimeoutError):
        await delivery.send(SimpleNamespace(session_id="s", generation=1),
            [{"event_id": "first"}, {"event_id": "second"}], send,
            lambda stream, event, **identity: records.append(event), barrier=True, timeout=.01)
    assert sent == ["first"] and not delivery.pending
    assert any(e.get("type") == "append.receipt_barrier.timeout" for e in records)


def test_instruction_only_receipt_and_multiple_appends_cannot_hide_queueing_or_missing_context():
    evidence, audio, judgment = fixtures()
    evidence["profile"] = "live_outcome_instruction"
    fact = {"task_id": "a", "event_type": "task.completed", "notification": {"intent": "requested"}}
    command = {"type": "session.instructions.append", "event_id": "jarvis-live-update:test:0", "content": json.dumps(fact)}
    evidence["state"]["provider_events"].extend([
        {"stream": "outgoing", "epoch_ms": 2400, "event": command},
        {"stream": "incoming", "event": {"type": "session.instructions.appended", "client_event_id": command["event_id"]}}])
    assert grade(evidence, audio, judgment)["status"] == "passed"
    evidence["state"]["provider_events"].append({"stream": "outgoing", "epoch_ms": 2700,
        "event": {**command, "event_id": command["event_id"] + ":1:instructions"}})
    result = grade(evidence, audio, judgment)
    assert result["status"] == "inconclusive" and any("append_receipt_missing" in u for u in result["unknowns"])
    assert result["measurements"]["a"]["application_append_delay_ms"] == 400


@pytest.mark.parametrize("status,expected_count", [("failed", 10), ("passed", 44)])
def test_model_only_phase_exhausts_distinct_profiles_with_bounded_counts_and_retained_prior(tmp_path, monkeypatch, status, expected_count):
    from argparse import Namespace
    from app.announcement_eval import runner
    from app.jarvis_tasks import acceptance
    from app.announcement_eval.profiles import MODEL_ONLY_PROFILES, MODEL_ONLY_LIMIT
    monkeypatch.setattr(runner, "BASE", tmp_path)
    monkeypatch.setattr(runner.settings, "openai_api_key", "offline-placeholder")
    candidate = {"source_digest": "offline-source"}
    monkeypatch.setattr(acceptance, "candidate_identity", lambda: candidate)
    offline = tmp_path / "offline"; offline.mkdir()
    runner.write(offline / "offline.json", [{"exit_code": 0}])
    runner.write(offline / "candidate.json", candidate)
    prior = tmp_path / "prior"; prior.mkdir()
    runner.write(prior / "report.json", {"stage": "pilot", "runs": [{"id": n} for n in range(13)]})
    prior_identity = runner.frozen_identity(prior / "report.json")
    cases = {"cases": [{"id": c} for c in ("explicit_idle", "silence", "occupied")]}
    identity = {"definition": {"sha256": "cases"}, "renderer": {"sha256": "renderer"},
        "wake": {"sha256": "wake"}, "files": []}
    monkeypatch.setattr(runner, "prepare_corpus", lambda folder: (cases, offline, offline / "wake.wav", identity))
    def run_case(output, case, name, timing, cohort, repetition, *rest, **kwargs):
        folder = output / f"{cohort}-{name}-{case['id']}-{timing}-{repetition}"
        folder.mkdir(); runner.write(folder / "evidence.json", {})
        return folder
    monkeypatch.setattr(runner, "run_case", run_case)
    monkeypatch.setattr(runner, "regrade", lambda *args, **kwargs: {"status": status, "failures": ["recorded_failure"] if status == "failed" else [], "unknowns": []})
    monkeypatch.setattr(runner, "usage_summary", lambda *args: {"provider_requests": 0})
    args = Namespace(stage="pilot", live=True, model_only=True, resume=None, baseline=None, profiles=None,
        output=tmp_path / "pilot", offline=offline, prior=prior)
    runner.execute(args)
    report = json.loads((args.output / "report.json").read_text(encoding="utf-8"))
    assert len(report["runs"]) == expected_count <= MODEL_ONLY_LIMIT
    assert {r["profile"] for r in report["runs"]} == set(MODEL_ONLY_PROFILES)
    assert report["prior_phase"] == prior_identity == runner.frozen_identity(prior / "report.json")
    assert report["prior_phase_run_count"] == 13
    assert all("reused_from" not in r for r in report["runs"])
    assert report["decision_owner"] == "gpt-live" and report["usage"]["provider_requests"] == 0
    monkeypatch.setattr(runner, "merge_usage", lambda current, prior: current)
    previous = args.output
    args.resume = previous
    args.output = tmp_path / "continuation"
    runner.execute(args)
    continued = json.loads((args.output / "report.json").read_text(encoding="utf-8"))
    assert len(continued["runs"]) == expected_count
    assert all(r["reused_from"] == str(previous) and r["original_grade"]["status"] == status for r in continued["runs"])
    assert not list(args.output.glob("pilot-*/evidence.json"))
    if status == "failed":
        from app.announcement_eval.profiles import MODEL_ONLY_FOLLOWUP_PROFILES
        args.resume = args.output
        args.output = tmp_path / "prompt-followup"
        args.prompt_followup = True
        runner.execute(args)
        followup = json.loads((args.output / "report.json").read_text(encoding="utf-8"))
        assert len(followup["runs"]) == expected_count + len(MODEL_ONLY_FOLLOWUP_PROFILES) <= MODEL_ONLY_LIMIT
        assert {r["profile"] for r in followup["runs"] if "reused_from" not in r} == set(MODEL_ONLY_FOLLOWUP_PROFILES)


def test_listen_first_is_a_live_prompt_change_with_backend_contract_unchanged():
    from app.announcement_eval.profiles import revise_live_prompt, revise_backend_prompt, profile_identity
    original = profile("live_thinking_context")
    selected = profile("live_thinking_listen_first")
    assert "Wait until the user has finished the whole task request" in revise_live_prompt("unused", selected, {})
    assert "Delegating early does not end" in revise_live_prompt("unused", original, {})
    assert revise_backend_prompt("Backend task procedures", selected) == revise_backend_prompt("Backend task procedures", original)
    assert profile_identity(selected, "listening")["live_prompt"] != profile_identity(original, "listening")["live_prompt"]
    strict = profile("live_thinking_strict_floor")
    strict_prompt = revise_live_prompt("unused", strict, {})
    assert "ongoing description has priority" in strict_prompt and "need no spoken acknowledgment" in strict_prompt
    assert "ten seconds" in strict_prompt and "unfinished announcement as uncommunicated" in strict_prompt
    assert revise_backend_prompt("Backend task procedures", strict) == revise_backend_prompt("Backend task procedures", original)
    probe = profile("live_ack_probe")
    probe_prompt = revise_live_prompt("unused", probe, {})
    assert "acknowledge_task_update" in probe_prompt and "This does not forbid delegation" in probe_prompt
    assert "Never delegate delivery acknowledgment before" in probe_prompt and probe.diagnostic_only
    assert revise_backend_prompt("Backend task procedures", probe) == revise_backend_prompt("Backend task procedures", original)


def test_model_only_characterization_never_relabels_admission_or_bookkeeping_failures_as_passes():
    from app.announcement_eval.runner import model_only_stop
    result = {"status": "failed", "failures": ["conversation_interrupted"], "unknowns": [],
        "interruption_observation": [{"before_completion": True}]}
    assert not model_only_stop(result) and result["status"] == "failed"
    result["interruption_observation"][0]["before_completion"] = False
    assert model_only_stop(result)
    result["observed_claims"] = []
    assert not model_only_stop(result) and result["status"] == "failed"
    result["observed_claims"] = [{"outcome": "completed", "complete": False}]
    assert model_only_stop(result)
    result["failures"] = ["notification_preference_not_persisted:a"]
    assert not model_only_stop(result) and result["status"] == "failed"
    result["failures"].append("requested_outcome_not_spoken:a")
    assert model_only_stop(result)


def test_phantom_audio_words_cannot_support_a_negative_semantic_penalty():
    evidence, audio, judgment = fixtures("optional")
    evidence["state"]["provider_events"] = [{"event": {"type": "session.output_transcript.delta", "delta": "Okay"}}]
    audio["words"] = [{"word": word, "start": 2+i/4, "end": 2.25+i/4} for i, word in enumerate(["Okay", "Thank", "you"])]
    judgment.update(claims=[], relevant=False, penalties=[{"kind": "relevance", "start_word": 0, "end_word": 2,
        "explanation": "Unsolicited speech"}])
    result = grade(evidence, audio, judgment)
    assert result["status"] == "inconclusive" and "audio_provider_penalty_disagreement" in result["unknowns"]
    assert "irrelevant_or_unwanted_speech" not in result["failures"]


@pytest.mark.asyncio
@pytest.mark.parametrize("observed", ["thinking", "user_speaking", "assistant_speaking", "connecting", "text_fallback"])
async def test_immediate_delivery_changes_only_context_eligibility_and_restores_original_floor(tmp_path, monkeypatch, observed):
    from contextlib import asynccontextmanager
    from app.announcement_eval import server
    @asynccontextmanager
    async def lifespan(app):
        app.state.acceptance_runtime = {"candidate": {"id": "offline"}}
        yield
    async def original_floor(*args, **kwargs): return observed
    monkeypatch.setattr(server, "production_lifespan", lifespan)
    monkeypatch.setattr(server, "isolated_root", lambda *args: tmp_path)
    monkeypatch.setattr(server.jarvis_coordinator, "_floor_state", original_floor)
    monkeypatch.setattr(server.settings, "task_pool_enabled", True)
    monkeypatch.setenv("JARVIS_EVAL_ROOT", str(tmp_path))
    monkeypatch.setenv("JARVIS_EVAL_PROFILE", "live_instruction_thinking")
    monkeypatch.setenv("JARVIS_EVAL_TIMING", "immediate")
    monkeypatch.setenv("JARVIS_EVAL_REAL_WORKER", "1")
    async with server.eval_lifespan(server.app):
        assert await server.jarvis_coordinator._floor_state(None, "s") == observed
        token = server.ACTIVE_UPDATE.set(True)
        try:
            eligible = await server.jarvis_coordinator._floor_state(None, "s")
            assert eligible == ("listening" if observed in {"thinking", "user_speaking", "assistant_speaking"} else observed)
        finally:
            server.ACTIVE_UPDATE.reset(token)
        assert await server.jarvis_coordinator._floor_state(None, "s") == observed
    assert server.jarvis_coordinator._floor_state is original_floor


def test_saved_corpus_extension_preserves_original_audio():
    from app.announcement_eval.runner import corpus_extension_matches
    identity = {"definition": {"sha256": "cases"}, "renderer": {"sha256": "renderer"}, "wake": {"sha256": "wake"},
        "files": [{"path": "first/audio.wav", "sha256": "speech"}]}
    extended = deepcopy(identity); extended["files"].append({"path": "new/change.wav", "sha256": "change"})
    assert corpus_extension_matches(identity, extended)
    extended["files"][0]["sha256"] = "altered"
    assert not corpus_extension_matches(identity, extended)


def test_prepare_corpus_reuses_frozen_audio_and_rejects_changed_recording(tmp_path, monkeypatch):
    import wave
    from app.announcement_eval import runner
    monkeypatch.setattr(runner, "BASE", tmp_path)
    source = tmp_path / "cached"; source.mkdir()
    output = tmp_path / "new"; output.mkdir(); (output / "corpus").mkdir()
    texts = runner.speech_texts(json.loads(runner.CORPUS.read_text(encoding="utf-8")))
    for text in texts:
        name = runner.sha256(text.encode()).hexdigest()[:16] + "-command.wav"
        for folder, samples in ((source, 16000), (output / "corpus", 16001)):
            with wave.open(str(folder / name), "wb") as audio:
                audio.setparams((1, 2, 16000, 0, "NONE", "not compressed")); audio.writeframes(bytes(samples * 2))
    files = [runner.frozen_identity(p) for p in source.glob("*.wav")]
    identity = {"definition": runner.frozen_identity(runner.CORPUS), "renderer": runner.frozen_identity(runner.ROOT / "android/scripts/generate-audio-corpus.ps1"), "files": files}
    runner.write(source / "corpus-identity.json", identity)
    runner.prepare_corpus(output, reuse=source)
    assert runner.frozen_identity(output / "corpus" / Path(files[0]["path"]).name)["sha256"] == files[0]["sha256"]
    Path(files[0]["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="Cached corpus audio"):
        runner.prepare_corpus(output, reuse=source)


@pytest.mark.asyncio
async def test_durable_notification_preference_revisions_replay_and_stale_authority():
    from app.announcement_eval.preferences import current_preference, save_preference
    from app.services.task_service import task_service
    from app.services.jarvis_coordinator import jarvis_coordinator
    accepted = await jarvis_coordinator.accept_text_turn("Begin", idempotency_key="notification-origin")
    live = await jarvis_coordinator.get_active_session()
    task = await task_service.dispatch(goal="Hello World page", origin_surface="test", idempotency_key="notification-task")
    assert (await current_preference(task.task.id))["intent"] == "discretionary"
    authority = {"session_id": live.id, "generation": live.generation}
    first = await save_preference(task.task.id, "requested", "Tell me when it finishes", **authority, call_id="a")
    second = await save_preference(task.task.id, "suppressed", "Actually stay quiet", **authority, call_id="b")
    assert first["revision"] == 1 and second["revision"] == 2
    assert await save_preference(task.task.id, "requested", "Tell me when it finishes", **authority, call_id="a") == first
    assert (await current_preference(task.task.id))["intent"] == "suppressed"
    with pytest.raises(ValueError, match="conflicts"):
        await save_preference(task.task.id, "requested", "Different request", **authority, call_id="a")
    with pytest.raises(ValueError, match="authority"):
        await save_preference(task.task.id, "requested", "Notify", session_id=live.id, generation=live.generation + 1, call_id="c")
    assert (await current_preference(task.task.id))["revision"] == 2


@pytest.mark.asyncio
async def test_preference_host_records_admission_and_later_change_through_gateway(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager
    from app.announcement_eval import server
    from app.announcement_eval.preferences import current_preference
    from app.services.jarvis_coordinator import jarvis_coordinator
    from app.services.jarvis_brain import restricted_brain_gateway
    from app.services.task_service import task_service
    accepted = await jarvis_coordinator.accept_text_turn("Begin", idempotency_key="pref-gateway-origin")
    live = await jarvis_coordinator.get_active_session()
    @asynccontextmanager
    async def lifespan(app):
        app.state.acceptance_runtime = {"candidate": {"id": "offline"}}
        yield
    monkeypatch.setattr(server, "production_lifespan", lifespan)
    monkeypatch.setattr(server, "isolated_root", lambda root, database: tmp_path)
    monkeypatch.setenv("JARVIS_EVAL_ROOT", str(tmp_path))
    monkeypatch.setenv("JARVIS_EVAL_PROFILE", "thinking_preference")
    monkeypatch.setattr(server.settings, "task_pool_enabled", False)
    authority = dict(session_id=live.id, generation=live.generation, conversation_id=accepted.conversation.id, origin_turn_id=None)
    async with server.eval_lifespan(server.app):
        admitted = await restricted_brain_gateway.execute(tool_name="dispatch_task", call_id="a", turn_scope="pref-gateway-turn",
            arguments={"goal": "Hello World page", "intent_id": "page", "notification_intent": "requested", "notification_reason": "Tell me when finished"}, **authority)
        task_id = admitted.output["task_id"]
        assert (await current_preference(task_id))["intent"] == "requested"
        await restricted_brain_gateway.execute(tool_name="set_task_update_policy", call_id="b",
            arguments={"task_reference": "Hello World page", "presentation_policy": "silent",
                "notification_intent": "suppressed", "notification_reason": "Actually stay quiet"}, **authority)
        assert (await current_preference(task_id))["revision"] == 2
        assert (await current_preference(task_id))["intent"] == "suppressed"
        assert (await task_service.get_task(task_id)).task.presentation_policy == "silent"
        assert len([e for e in server.app.state.announcement_eval.events if e["stream"] == "preferences"]) == 2
        await restricted_brain_gateway.execute(tool_name="set_task_update_policy", call_id="c",
            arguments={"task_reference": "Hello World page", "presentation_policy": "critical_only",
                "notification_intent": "requested", "notification_reason": "Notify after all"}, **authority)
        replay = await restricted_brain_gateway.execute(tool_name="set_task_update_policy", call_id="b",
            arguments={"task_reference": "Hello World page", "presentation_policy": "silent",
                "notification_intent": "suppressed", "notification_reason": "Actually stay quiet"}, **authority)
        assert (await task_service.get_task(task_id)).task.presentation_policy == "critical_only"
        assert replay.output["notification_intent"] == "requested" and replay.output["notification_revision"] == 3
    from app.services.openai_live_adapter import OpenAILiveAdapter
    assert "notification_intent" in OpenAILiveAdapter._router_configuration({})["tools"][0]["parameters"]["properties"]


def test_missing_notification_persistence_is_a_failure_even_if_speech_was_correct():
    evidence, audio, judgment = fixtures()
    evidence["profile"] = "thinking_preference"
    command = {"type": "session.thinking.append", "event_id": "jarvis-live-update:test:0",
        "content": json.dumps({"task_id": "a", "event_type": "task.completed", "notification": {"intent": "discretionary"}})}
    evidence["state"]["provider_events"].extend([
        {"stream": "outgoing", "epoch_ms": 2500, "event": command},
        {"stream": "incoming", "event": {"type": "session.thinking.appended", "client_event_id": command["event_id"]}}])
    assert "notification_preference_not_persisted:a" in grade(evidence, audio, judgment)["failures"]
    command["content"] = command["content"].replace("discretionary", "requested")
    assert grade(evidence, audio, judgment)["status"] == "passed"


def test_spoken_but_pending_attention_is_reported_separately_from_selection():
    evidence, audio, judgment = fixtures()
    evidence["state"]["presentations"] = [{"id": "p", "task_id": "a", "state": "pending"}]
    result = grade(evidence, audio, judgment)
    assert result["status"] == "passed"
    assert result["acknowledgment_observation"][0]["spoken_without_handled"]
    assert evidence["state"]["presentations"][0]["state"] == "pending"


def test_preference_candidate_requires_changed_preference_screen():
    rows = [{"profile": "thinking_preference", "case_id": id, "cohort": "pilot", "grade": {"status": "passed"}}
        for id in ("explicit_idle", "silence", "occupied")]
    assert recommendation(rows)["profile"] is None
    rows.append({**rows[0], "case_id": "changed_silence"})
    assert recommendation(rows)["profile"] == "thinking_preference"


def test_isolated_host_does_not_mutate_production_routes_or_lifespan():
    from app.main import app as production
    routes, lifespan = list(production.router.routes), production.router.lifespan_context
    from app.announcement_eval.server import app, isolated_root, ROOT
    assert production.router.routes == routes and production.router.lifespan_context is lifespan
    assert app is not production and app.router.routes[4].path.startswith("/__eval/")
    owned = ROOT / "data/announcement-evaluation/contract"
    assert isolated_root(owned, owned / "application.db") == owned.resolve()
    with pytest.raises(ValueError): isolated_root(ROOT / "data", ROOT / "data/app.db")
    with pytest.raises(ValueError): isolated_root(owned, ROOT / "backend/data/app.db")


def test_positive_client_playback_and_deadline():
    result = grade(*fixtures())
    assert result["status"] == "passed"
    assert result["measurements"]["a"]["notification_latency_ms"] == 2000


def test_production_policy_exposes_silent_terminal_context():
    from app.services.task_presentations import TaskPresentationService
    allowed = TaskPresentationService._policy_allows_candidate
    assert allowed("terminal_result", "silent")
    assert allowed("terminal_result", "critical_only")
    assert not allowed("progress", "critical_only")
    assert allowed("progress", "follow_progress")


def test_withheld_terminal_context_cannot_qualify_model_selection():
    evidence, audio, judgment = fixtures("optional")
    evidence["profile"] = "commentary_relevance"; judgment["claims"] = []
    assert "terminal_context_not_delivered:a" in grade(evidence, audio, judgment)["unknowns"]


def test_receipt_proves_delivery_but_never_proves_speech():
    evidence, audio, judgment = fixtures("optional")
    evidence["profile"] = "commentary_relevance"; judgment["claims"] = []
    command = {"type": "session.commentary.append", "event_id": "jarvis-live-update:test:0",
        "content": json.dumps({"task_id": "a", "event_type": "task.completed"})}
    evidence["state"]["provider_events"].append({"stream": "outgoing", "epoch_ms": 2500, "event": command})
    assert "terminal_context_not_delivered:a" in grade(evidence, audio, judgment)["unknowns"]
    evidence["state"]["provider_events"].append({"stream": "incoming", "event": {
        "type": "session.commentary.appended", "client_event_id": command["event_id"]}})
    result = grade(evidence, audio, judgment)
    assert result["status"] == "passed" and result["silence_intention"] == "unknown"


def test_utf8_evidence_and_durable_paid_failure_accounting(tmp_path, monkeypatch):
    import httpx
    from app.announcement_eval.runner import RequestLedger, api_request, usage_summary, write, PaidFailure
    folder = tmp_path / "case"; folder.mkdir()
    write(folder / "evidence.json", {"state": {"background_decisions": [], "text": "“I'll stay quiet.”"}})
    assert usage_summary(tmp_path, [])["background_orchestration"][0]["decisions"] == []
    ledger = RequestLedger(tmp_path)
    transport = httpx.MockTransport(lambda request: httpx.Response(429, json={"error": {"code": "insufficient_quota"}}))
    with httpx.Client(transport=transport) as client, pytest.raises(PaidFailure):
        api_request(client, "/responses", ledger, payload={"model": "test"})
    saved = json.loads(ledger.path.read_text(encoding="utf-8"))
    assert len(saved) == 1 and saved[0]["status"] == 429 and saved[0]["finished_at"]


def test_projection_reuse_only_retires_withheld_context(tmp_path, monkeypatch):
    from app.announcement_eval import runner
    from app.announcement_eval.profiles import profile_identity
    monkeypatch.setattr(runner, "root_path", lambda path: path)
    identity = profile_identity(profile("commentary_relevance"), "listening")
    del identity["terminal_projection"]
    row = {"profile": "commentary_relevance", "timing": "listening", "profile_identity": identity, "directory": str(tmp_path)}
    evidence = {"state": {"tasks": [{"presentation_policy": "critical_only", "finished_at": 1000}]}}
    runner.write(tmp_path / "evidence.json", evidence)
    assert runner.projection_reuse(row) == "unchanged_for_recorded_non_silent_tasks"
    evidence["state"]["tasks"][0]["presentation_policy"] = "silent"
    runner.write(tmp_path / "evidence.json", evidence)
    assert runner.projection_reuse(row) == "withheld_terminal_context"
    row["profile_identity"]["policy"] = "Changed prompt"
    with pytest.raises(ValueError): runner.projection_reuse(row)


def test_corpus_reuse_binds_timing_definition_renderer_and_wake():
    from app.announcement_eval.runner import corpus_signature
    identity = {"definition": {"sha256": "cases"}, "renderer": {"sha256": "renderer"}, "wake": {"sha256": "wake"},
        "files": [{"path": "first/audio.wav", "sha256": "speech"}]}
    relocated = deepcopy(identity); relocated["files"][0]["path"] = "second/audio.wav"
    assert corpus_signature(identity) == corpus_signature(relocated)
    for field in ("definition", "renderer", "wake"):
        changed = deepcopy(identity); changed[field]["sha256"] = "changed"
        assert corpus_signature(identity) != corpus_signature(changed)


@pytest.mark.parametrize("missing", ["input_understood", "recording_complete", "playback_verified", "clock_valid"])
def test_missing_evidence_never_passes(missing):
    evidence, audio, judgment = fixtures(); evidence[missing] = False
    assert grade(evidence, audio, judgment)["status"] == "inconclusive"


def test_append_receipt_and_helper_ack_do_not_prove_communication():
    evidence, audio, judgment = fixtures()
    evidence["state"]["presentations"] = [{"id": "p", "task_id": "a", "handled_by": "model_report", "handled_at": 2500}]
    judgment["claims"] = []
    result = grade(evidence, audio, judgment)
    assert "requested_outcome_not_spoken:a" in result["failures"]
    assert "premature_handled_transition:p" in result["failures"]
    judgment["claims"] = fixtures()[2]["claims"]
    assert "premature_handled_transition:p" in grade(evidence, audio, judgment)["failures"]


def test_discretionary_silence_does_not_claim_model_intent():
    evidence, audio, judgment = fixtures("optional"); judgment["claims"] = []
    result = grade(evidence, audio, judgment)
    assert result["status"] == "passed" and result["observed_no_announcement"]
    assert result["silence_intention"] == "unknown"


def test_negative_semantic_judgments_need_citations_and_actual_overlap():
    evidence, audio, judgment = fixtures()
    judgment["interruption_ok"] = False
    assert "unsubstantiated_interruption_judgment" in grade(evidence, audio, judgment)["unknowns"]
    evidence["caller_audio_windows"] = [(2900, 4100)]
    judgment["penalties"] = [{"kind": "interruption", "start_word": 0, "end_word": 3}]
    assert "conversation_interrupted" in grade(evidence, audio, judgment)["failures"]
    audio["words"] = [{"word": "Sure", "start": 2, "end": 2.2}]
    judgment["claims"] = []; judgment["penalties"][0]["end_word"] = 0
    assert "conversation_interrupted" not in grade(evidence, audio, judgment)["failures"]


@pytest.mark.parametrize("defect", ["late", "premature", "false", "duplicate", "forbidden", "interrupted", "uncitable", "contradictory"])
def test_known_negative_grading_fixtures(defect):
    evidence, audio, judgment = fixtures()
    if defect == "late":
        for word in audio["words"]: word["start"] += 15; word["end"] += 15
    elif defect == "premature": evidence["state"]["tasks"][0]["finished_at"] = 9000
    elif defect == "false": evidence["state"]["tasks"][0]["status"] = "failed"
    elif defect == "duplicate":
        evidence["max_announcements"] = 1; judgment["claims"].append(deepcopy(judgment["claims"][0]))
    elif defect == "forbidden": evidence["expectation"] = "forbidden"
    elif defect == "interrupted": judgment["claims"][0]["complete"] = False
    elif defect == "uncitable": judgment["claims"][0]["end_word"] = 100
    elif defect == "contradictory": evidence["state"]["provider_events"] = []
    assert grade(evidence, audio, judgment)["status"] != "passed"


def test_recommendation_never_selects_control_or_duplicate_incomplete_cohort():
    rows = [{"profile": "commentary_control", "case_id": id, "cohort": "pilot", "grade": {"status": "passed"}}
        for id in ("explicit_idle", "silence", "occupied")]
    assert recommendation(rows)["profile"] is None
    for row in rows: row["profile"] = "commentary_relevance"
    assert recommendation(rows)["profile"] == "commentary_relevance"
    duplicated = [{**rows[0], "cohort": "acceptance"} for _ in range(48)]
    assert recommendation(duplicated, final=True)["profile"] is None


@pytest.mark.asyncio
async def test_fixture_worker_publishes_fenced_result_once(tmp_path):
    from app.announcement_eval.server import Experiment
    from app.services.task_service import task_service
    from app.services.worker_coordinator import worker_coordinator
    from app.db.session import session_scope
    from app.db.tables import TaskEvent
    from sqlalchemy import select
    dispatched = await task_service.dispatch(goal="Hello World page", origin_surface="test", idempotency_key="eval-task")
    experiment = Experiment(tmp_path, profile("baseline"), "listening")
    worker = await worker_coordinator.activate(process_identity="fixture")
    experiment.worker_id = worker.worker_id
    await experiment.finish(dispatched.task.id, "completed", "The page is ready")
    await experiment.finish(dispatched.task.id, "completed", "The page is ready")
    async with session_scope() as db:
        events = list((await db.execute(select(TaskEvent).where(TaskEvent.task_id == dispatched.task.id))).scalars())
    assert sum(e.event_type == "task.completed" for e in events) == 1


def test_android_recording_rejects_gap_or_missing_progress(tmp_path):
    from app.announcement_eval.runner import load_helper
    render = load_helper("verify-announcements.py").render_recording
    frames = [{"offset": n * 640, "bytes": 640, "epoch_ms": 1000 + n * 20,
        "rate": 16000, "channels": 1, "format": 2, "playback_head": n * 320} for n in range(3)]
    metadata = {"complete": True, "frames": 3}
    result = render(bytes(1920), frames, metadata, tmp_path / "output.wav")
    assert result["recording_complete"] and result["playback_verified"]
    frames[1]["epoch_ms"] = 1800
    assert not render(bytes(1920), frames, metadata, tmp_path / "output.wav")["recording_complete"]
    frames[1]["epoch_ms"] = 1020; frames[2]["epoch_ms"] = 2040
    assert not render(bytes(1920), frames, metadata, tmp_path / "output.wav")["recording_complete"]
    assert render(bytes(1920), frames, metadata, tmp_path / "output.wav", [(1020, 2040)])["recording_complete"]


@pytest.mark.asyncio
async def test_host_state_reads_real_tables_and_enforces_loopback_token(tmp_path, monkeypatch):
    import httpx
    from app.announcement_eval.server import app, Experiment
    from app.services.task_service import task_service
    monkeypatch.setenv("JARVIS_EVAL_TOKEN", "offline-token")
    app.state.announcement_eval = Experiment(tmp_path, profile("baseline"), "listening")
    await task_service.dispatch(goal="Hello World", origin_surface="test", idempotency_key="state-task")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        assert (await client.get("/__eval/state")).status_code == 403
        response = await client.get("/__eval/state", headers={"x-eval-token": "offline-token"})
        assert response.status_code == 200, response.text
        assert response.json()["tasks"][0]["goal"] == "Hello World"


@pytest.mark.asyncio
async def test_provider_receive_loop_is_recorded_not_only_browser_forwarding(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import asyncio
    from app.announcement_eval import server
    adapter = server.openai_live_adapter
    monkeypatch.setattr(server.settings, "task_pool_enabled", False)
    @asynccontextmanager
    async def lifespan(app):
        app.state.acceptance_runtime = {"candidate": {"id": "offline"}}
        yield
    monkeypatch.setattr(server, "production_lifespan", lifespan)
    monkeypatch.setattr(server, "isolated_root", lambda root, database: tmp_path)
    monkeypatch.setenv("JARVIS_EVAL_ROOT", str(tmp_path))
    monkeypatch.setenv("JARVIS_EVAL_PROFILE", "baseline")
    handler = AsyncMock()
    monkeypatch.setattr(adapter, "_handle_event", handler)
    async def events():
        yield json.dumps({"type": "session.input_transcript.delta", "delta": "Tell me when it finishes"})
    provider = SimpleNamespace(session_id="offline-session", generation=1, socket=events(), closing=True,
        closed=asyncio.Event(), diagnostics=None)
    async with server.eval_lifespan(server.app):
        await adapter._receive(provider)
        rows = server.app.state.announcement_eval.events
        assert any(r["stream"] == "incoming" and r["event"].get("delta") == "Tell me when it finishes" for r in rows)
        handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_experiment_projects_silent_terminal_with_policy_and_ack_unchanged(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager
    from app.announcement_eval import server
    from app.services.task_service import task_service
    from app.services.task_presentations import task_presentation_service
    from app.services.worker_coordinator import worker_coordinator
    @asynccontextmanager
    async def lifespan(app):
        app.state.acceptance_runtime = {"candidate": {"id": "offline"}}
        yield
    monkeypatch.setattr(server, "production_lifespan", lifespan)
    monkeypatch.setattr(server, "isolated_root", lambda root, database: tmp_path)
    monkeypatch.setenv("JARVIS_EVAL_ROOT", str(tmp_path))
    monkeypatch.setenv("JARVIS_EVAL_PROFILE", "commentary_relevance")
    monkeypatch.setattr(server.settings, "task_pool_enabled", False)
    dispatched = await task_service.dispatch(goal="Hello World page", origin_surface="test",
        idempotency_key="silent-task", presentation_policy="silent")
    async with server.eval_lifespan(server.app):
        experiment = server.app.state.announcement_eval
        await experiment.finish(dispatched.task.id, "completed", "The page is ready")
        await task_presentation_service.sync_candidates()
        pending = await task_presentation_service.list_pending()
        assert len(pending) == 1 and pending[0].handled_at is None
        detail = await task_service.get_task(dispatched.task.id)
        assert detail.task.presentation_policy == "silent"
    assert server.TaskPresentationService._policy_allows_candidate("terminal_result", "silent")


@pytest.mark.parametrize("busy",[False,True])
def test_observer_cleanup_observes_without_retrying_or_marking_attention(busy):
    from app.announcement_eval.runner import drain_observer
    at=[0.0];calls=[]
    def inspect():
        calls.append(at[0])
        return {"jobs":[{"id":"job","status":"running" if busy else "completed","completed_at":None}],
            "presentations":[{"id":"p","state":"pending"}]}
    result=drain_observer(Path('unused'),timeout=3,clock=lambda:at[0],sleep=lambda n:at.__setitem__(0,at[0]+n),inspect=inspect)
    assert result["timed_out"] is busy
    assert result["presentations"][0]["state"]=="pending"
    assert 2<=at[0]<=3
    assert len(calls)<30


def test_later_communication_projection_requires_same_result_and_preserves_original(tmp_path):
    from app.announcement_eval.focused import load_evidence
    original={"state":{"presentations":[{"id":"p","source_task_event_id":"r","state":"pending"}]}}
    path=tmp_path/'evidence.json';path.write_text(json.dumps(original))
    final={"presentations":[{"id":"p","source_task_event_id":"new","state":"handled","handled_by":"conversation_observer",
        "handled_at":3000,"handled_event_id":"audit"}]}
    (tmp_path/'observer-drain.json').write_text(json.dumps(final))
    assert load_evidence(tmp_path)["state"]["presentations"][0]["state"]=="pending"
    final["presentations"][0]["source_task_event_id"]="r"
    (tmp_path/'observer-drain.json').write_text(json.dumps(final))
    assert load_evidence(tmp_path)["state"]["presentations"][0]["state"]=="handled"
    assert json.loads(path.read_text())==original
