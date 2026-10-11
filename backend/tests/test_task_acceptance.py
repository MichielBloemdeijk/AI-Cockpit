from copy import deepcopy
from datetime import datetime, timezone, timedelta

from app.jarvis_tasks.acceptance import SCHEMA, evaluate, validate_observation, load_assertion, candidate_identity
import json
import pytest
import os
import sys
import time


@pytest.mark.skipif(os.name != "nt", reason="Windows acceptance descendant containment")
@pytest.mark.parametrize("timeout", [1, 10])
def test_acceptance_command_stops_descendants_on_timeout_and_normal_exit(tmp_path, timeout):
    from app.jarvis_tasks.acceptance import _command
    marker = tmp_path / "writer.txt"
    writer = "import pathlib,time; p=pathlib.Path(" + repr(str(marker)) + "); " + "\nwhile True:\n p.write_text(str(time.time_ns())); time.sleep(.02)"
    parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(writer) + "]); time.sleep(" + ("20" if timeout == 1 else ".3") + ")"
    result = _command("contained", [sys.executable, "-c", parent], tmp_path, tmp_path, timeout=timeout)
    assert result["status"] == ("failed" if timeout == 1 else "passed")
    assert marker.exists()
    before = marker.read_bytes()
    time.sleep(.2)
    assert marker.read_bytes() == before


def test_candidate_changes_when_wake_assets_or_build_configuration_change(tmp_path, monkeypatch):
    import app.jarvis_tasks.acceptance as acceptance
    from types import SimpleNamespace
    monkeypatch.setattr(acceptance.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="fixture-revision"))
    baseline = candidate_identity(tmp_path)
    for name in ("frontend/public/wake-model.onnx", "frontend/next.config.ts",
                 "frontend/vitest.config.mts", "frontend/playwright.live.config.ts",
                 "android/settings.gradle.kts", "android/screen-fixture/src/main/MarkerActivity.kt",
                 "android/app/src/main/java/jarvis/data/CockpitApi.kt",
                 "backend/workflows/fixture/1/instructions.md", "backend/workflows/fixture/1/workflow.yaml"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"candidate fixture")
        changed = candidate_identity(tmp_path)
        assert changed["source_digest"] != baseline["source_digest"], name
        baseline = changed


def test_candidate_prunes_generated_directories_before_traversal(tmp_path, monkeypatch):
    import app.jarvis_tasks.acceptance as acceptance
    from types import SimpleNamespace
    monkeypatch.setattr(acceptance.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="fixture"))
    generated = tmp_path / "frontend/result-export/.next/deep"
    generated.mkdir(parents=True)
    (generated / "generated.js").write_text("generated")
    real = tmp_path / "frontend/result-export/app.ts"
    real.write_text("source")
    visited = []
    walk = os.walk
    def checked_walk(*args, **kwargs):
        for directory, children, names in walk(*args, **kwargs):
            visited.append(directory)
            yield directory, children, names
    monkeypatch.setattr(acceptance.os, "walk", checked_walk)
    before = candidate_identity(tmp_path)
    assert before["source_file_count"] == 1
    assert all(".next" not in acceptance.Path(p).parts for p in visited)
    (generated / "generated.js").write_text("changed generated")
    assert candidate_identity(tmp_path) == before
    real.write_text("changed source")
    assert candidate_identity(tmp_path)["source_digest"] != before["source_digest"]


@pytest.mark.asyncio
async def test_candidate_tracks_saved_tool_policy_and_approved_skill_contents(tmp_path, monkeypatch):
    from app.services.worker_tool_policy import read_policy, save_policy
    import app.jarvis_tasks.acceptance as acceptance
    from types import SimpleNamespace
    monkeypatch.setattr(acceptance.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="fixture"))
    before = candidate_identity(tmp_path)
    skill = tmp_path / "approved" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("Approved instructions")
    revision, policy = await read_policy()
    await save_policy(policy | {"approved_skills": [str(skill)]}, expected_revision=revision)
    approved = candidate_identity(tmp_path)
    assert approved["configuration_digest"] != before["configuration_digest"]
    skill.write_text("Changed instructions")
    assert candidate_identity(tmp_path)["configuration_digest"] != approved["configuration_digest"]


def test_candidate_does_not_follow_linked_source_directories(tmp_path, monkeypatch):
    import app.jarvis_tasks.acceptance as acceptance
    from types import SimpleNamespace
    real_run = acceptance.subprocess.run
    monkeypatch.setattr(acceptance.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="fixture"))
    target = tmp_path / "external"
    target.mkdir()
    (target / "private.py").write_text("outside explicit source roots")
    source = tmp_path / "backend/app"
    source.mkdir(parents=True)
    link = source / "linked"
    if os.name == "nt":
        # Junctions require no symlink privilege on Windows.
        real_run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)
    try:
        assert candidate_identity(tmp_path)["source_file_count"] == 0
    finally:
        if os.name == "nt":
            os.rmdir(link)
        else:
            link.unlink()


def report():
    return {"schema": SCHEMA, "gate": "M7", "candidate": {"id": "fixture"},
        "finished_at": datetime.now(timezone.utc).isoformat(), "checks": [
            {"id": "backend", "status": "passed", "evidence": {"test_count": 1}},
            {"id": "voice_provider", "status": "passed", "evidence": {
                "adapter": "openai_live", "requested_tier": "priority", "actual_tier": "priority"}}]}


def test_readiness_requires_candidate_freshness_complete_unskipped_unique_assertions():
    valid = report()
    assert evaluate(valid, {"id": "fixture"}, gate="M7")["implementation_ready"]
    assert not evaluate(valid, {"id": "other"}, gate="M7")["implementation_ready"]
    for mutate in (
        lambda r: r["checks"].pop(),
        lambda r: r["checks"][0].update(skipped=1),
        lambda r: r["checks"].append(deepcopy(r["checks"][0])),
        lambda r: r.update(finished_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat()),
        lambda r: r["checks"][1]["evidence"].update(adapter="fake"),
        lambda r: r["checks"][1]["evidence"].update(actual_tier="default"),
    ):
        altered = deepcopy(valid)
        mutate(altered)
        assert evaluate(altered, {"id": "fixture"}, gate="M7")["status"] == "blocked"


def test_final_ready_sample_uses_client_interval_and_strict_4500_ms_limit():
    sample = {"status": "passed", "path": "ready", "sample_id": "final", "session_id": "s",
        "task_id": "task", "turn_id": "turn", "execution_run_id": "run",
        "command_last_sample_ns": 1_000_000, "client_received_ns": 4_500_000_000}
    proof = {"policy": "single_final_under_4500_ms", "clock": "android_elapsed_realtime_nanos", "samples": [sample]}
    assert validate_observation("ready_latency", proof) is None  # 4499 ms
    for invalid in ({**proof, "samples": []}, {**proof, "samples": [sample, sample]},
            {**proof, "clock": "server_clock"}, {**proof, "policy": None}):
        assert validate_observation("ready_latency", invalid)
    for key, value in (("status", "failed"), ("task_id", ""), ("client_received_ns", 0),
            ("client_received_ns", 4_501_000_000), ("path", "cold")):
        assert validate_observation("ready_latency", {**proof, "samples": [{**sample, key: value}]})


def test_system_gesture_does_not_pass_capture_gate():
    assert validate_observation("fresh_screen", {"api_level": 37, "custom_wake": False,
        "initial_screen_A": True, "fresh_screen_B": True, "clarification_before_B": True, "admitted_capture": "B"})


def test_m8_requires_real_mobile_result_and_native_privacy_not_only_admission():
    from app.jarvis_tasks.acceptance import REQUIRED
    assert {"mobile_result", "capture_privacy"} <= REQUIRED["M8"] & REQUIRED["M10"]
    assert validate_observation("mobile_result", {"custom_wake": False})
    valid = {"adapter": "openai_live", "workflow": "research_screen@1", "custom_wake": True,
        "real_notification_delivered": True, "notification_action_tapped": True, "reopened_result": True,
        "no_extra_run_on_reopen": True, "ui_followup": True, "same_task_continuation": True, "both_screen_roles": True,
        "source_count": 1, "task_id": "task", "initial_run_id": "first", "followup_run_id": "second", "public_id": "page"}
    assert validate_observation("mobile_result", valid) is None
    for key, value in (("ui_followup", False), ("followup_run_id", "first"), ("source_count", 0), ("both_screen_roles", False)):
        assert validate_observation("mobile_result", {**valid, key: value})
    assert validate_observation("capture_privacy", {"api_level": 37, "provider_calls": 0, "native_checks": {}})


def test_full_audio_sized_report_loads_without_waiving_identity_or_size_boundary(tmp_path):
    import app.jarvis_tasks.acceptance as acceptance
    proof = report()
    # Representative generated full-cohort diagnostics, not an additional gate.
    proof["diagnostics"] = "x" * 2_600_000
    path = tmp_path / "full-cohort.json"
    path.write_text(json.dumps(proof), encoding="utf-8")
    assert load_assertion(path, {"id": "fixture"}, gate="M6", identifier="backend")["status"] == "passed"
    with pytest.raises(ValueError, match="candidate"):
        load_assertion(path, {"id": "other"}, gate="M6", identifier="backend")
    path.write_text(" " * (acceptance.MAX_EVIDENCE_BYTES + 1), encoding="utf-8")
    with pytest.raises(ValueError, match="bounded regular"):
        load_assertion(path, {"id": "fixture"}, gate="M6", identifier="backend")


def test_manifest_reuses_only_equivalent_backend_gates_and_live_voice_proof(tmp_path):
    import app.jarvis_tasks.acceptance as acceptance
    manifest = json.loads(acceptance.Path(acceptance.__file__).with_name("acceptance-profiles.json").read_text())
    assert manifest["backend_suites"]["M6"] == manifest["backend_suites"]["M7"] == "not diagnostic and not provider and not browser"
    assert manifest["backend_suites"]["M10"] == "not diagnostic"
    assert manifest["command_timeouts_seconds"]["backend"] == 2400
    proof = report()
    path = tmp_path / "proof.json"
    path.write_text(json.dumps(proof))
    assert load_assertion(path, {"id": "fixture"}, gate="M6", identifier="backend")["status"] == "passed"
    with pytest.raises(ValueError, match="gate"):
        load_assertion(path, {"id": "fixture"}, gate="M10", identifier="backend")
    assert load_assertion(path, {"id": "fixture"}, gate="M10", identifier="voice_provider")["status"] == "passed"


def test_capture_performance_rejects_negative_intervals_booleans_and_nonfinite_samples():
    proof = {"clock": "android_elapsed_realtime_nanos", "baseline_identity": "measured-baseline",
        "audio_underruns": 0, "anrs": 0, "overlay_jank_observed": False,
        "baseline_live_ready_ms": [1000.] * 20, "capture_live_ready_ms": [1000.] * 20,
        "live_ready_p95_difference_ms": 0., "frame_window_ns": 20_000_000_000,
        "normalized_frame_rates": {"baseline": [0.] * 20, "activation": [0.] * 20, "fresh": [0.] * 20},
        **{key: [0.0] * 20 for key in ("callback_sync_ms", "cached_upload_start_ms",
            "fresh_request_callback_ms", "initial_callback_after_speech_ms")}}
    assert validate_observation("capture_performance", proof) is None
    for invalid in (-1.0, False, float("nan"), float("inf")):
        changed = deepcopy(proof)
        changed["callback_sync_ms"][0] = invalid
        assert validate_observation("capture_performance", changed)
    proof["capture_live_ready_ms"] = [990.0] * 20
    proof["live_ready_p95_difference_ms"] = -10.0
    assert validate_observation("capture_performance", proof) is None


def test_imported_assertion_rejects_malformed_duplicate_skipped_and_old_candidate(tmp_path):
    path = tmp_path / "proof.json"
    valid = report()
    path.write_text(json.dumps(valid))
    assert load_assertion(path, {"id": "fixture"}, gate="M7", identifier="voice_provider")["status"] == "passed"
    for mutate in (lambda r: r.update(checks=None), lambda r: r.update(finished_at=None),
            lambda r: r["checks"][1].update(skipped=1), lambda r: r.update(candidate={"id": "old"}),
            lambda r: r["checks"].append(deepcopy(r["checks"][1]))):
        changed = deepcopy(valid)
        mutate(changed)
        path.write_text(json.dumps(changed))
        with pytest.raises((ValueError, TypeError)):
            load_assertion(path, {"id": "fixture"}, gate="M7", identifier="voice_provider")



def test_report_documentation_does_not_change_executable_candidate(tmp_path, monkeypatch):
    import app.jarvis_tasks.acceptance as acceptance
    from types import SimpleNamespace
    monkeypatch.setattr(acceptance.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="fixture"))
    before = candidate_identity(tmp_path)
    path = tmp_path / "docs/jarvis-tasks/roadmap.md"
    path.parent.mkdir(parents=True); path.write_text("Report-only change")
    assert candidate_identity(tmp_path) == before


def test_manifest_allows_only_declared_gate_configuration(tmp_path, monkeypatch):
    from app.config import settings
    from app.jarvis_tasks.acceptance import resolve_test_configuration, candidate_matches
    expected = candidate_identity()
    configuration = resolve_test_configuration("coding_provider", paths={
        "task_project_roots": str(tmp_path / "project"), "codex_profiles_dir": str(tmp_path / "profiles"),
        "task_workspaces_dir": str(tmp_path / "workspaces"), "task_context_blobs_dir": str(tmp_path / "blobs"),
        "conversation_workspaces_dir": str(tmp_path / "conversations")})
    for key, value in configuration["settings"].items():
        monkeypatch.setattr(settings, key, value)
    observed = candidate_identity()
    assert candidate_matches(observed, expected, identifier="coding_provider", configuration=configuration)
    assert not candidate_matches(observed, expected, identifier="subscription_provider", configuration=configuration)
    observed["sdk"] = "different"
    assert not candidate_matches(observed, expected, identifier="coding_provider", configuration=configuration)


def test_capture_frame_gate_compares_regression_and_rejects_missing_windows():
    proof = {"clock": "android_elapsed_realtime_nanos", "baseline_identity": "controlled",
        "audio_underruns": 0, "anrs": 0, "overlay_jank_observed": False,
        "baseline_live_ready_ms": [1000.] * 20, "capture_live_ready_ms": [1000.] * 20,
        "live_ready_p95_difference_ms": 0., "frame_window_ns": 20_000_000_000,
        "normalized_frame_rates": {"baseline": [.5] * 20, "activation": [.4] * 20, "fresh": [.4] * 20},
        **{key: [0.] * 20 for key in ("callback_sync_ms", "cached_upload_start_ms",
            "fresh_request_callback_ms", "initial_callback_after_speech_ms")}}
    assert validate_observation("capture_performance", proof) is None
    proof["normalized_frame_rates"]["fresh"] = [.6] * 20
    assert validate_observation("capture_performance", proof) == "Capture-induced frame regression observed"
    proof["normalized_frame_rates"]["fresh"] = []
    assert validate_observation("capture_performance", proof)


def test_process_tree_report_accepts_only_its_declared_isolated_directories(tmp_path, monkeypatch):
    from app.config import settings
    from app.jarvis_tasks.acceptance import resolve_test_configuration
    expected = candidate_identity()
    configuration = resolve_test_configuration("process_tree", paths={key: str(tmp_path / key) for key in (
        "task_workspaces_dir", "task_context_blobs_dir", "conversation_workspaces_dir")})
    for key, value in configuration["settings"].items():
        monkeypatch.setattr(settings, key, value)
    report = {"schema": SCHEMA, "gate": "M10", "candidate": candidate_identity(),
        "test_configuration": configuration, "finished_at": datetime.now(timezone.utc).isoformat(),
        "checks": [{"id": "process_tree", "status": "passed", "evidence": {
            "exact_pid_creation_stamp": True, "descendants_stopped": True, "writer_growth_after_stop": 0}}]}
    path = tmp_path / "process-tree.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    assert load_assertion(path, expected, gate="M10", identifier="process_tree")["status"] == "passed"
    report.pop("test_configuration")
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="candidate mismatch"):
        load_assertion(path, expected, gate="M10", identifier="process_tree")


def test_runner_executes_audio_and_capture_stages_with_declared_configuration(tmp_path, monkeypatch):
    import app.jarvis_tasks.acceptance as acceptance
    # Exercise the retained strict measurement protocol independently of the decision.
    monkeypatch.setattr(acceptance, "accepted_exceptions", lambda gate: {})
    commands = []
    def execute(identifier, command, cwd, output, **kwargs):
        commands.append((identifier, command, kwargs.get("env", {})))
        return {"id": identifier, "status": "passed", "evidence": {"mock": True}}
    monkeypatch.setattr(acceptance, "_command", execute)
    configuration = acceptance.resolve_test_configuration("emulator", paths={
        "codex_profiles_dir": str(tmp_path / "profiles"), "task_workspaces_dir": str(tmp_path / "workspaces"),
        "task_context_blobs_dir": str(tmp_path / "blobs"), "conversation_workspaces_dir": str(tmp_path / "conversations")}, browser_origin="http://10.0.2.2:8123")
    report = acceptance.run_acceptance(gate="M8", output=tmp_path / "report", evidence={}, live=True,
        emulator="emulator-5554", audio_dir=tmp_path, database=tmp_path / "fixture.db", backend_url="http://127.0.0.1:8001",
        emulator_configuration=configuration)
    audio = next(row for row in commands if row[0] == "audio_execution")
    capture = next(row for row in commands if row[0] == "capture_execution")
    assert "--prepare-scenarios" in audio[1] and "--capture-performance" not in audio[1]
    assert "--capture-performance" in capture[1]
    assert "OPENAI_LIVE_DELEGATION_MODE" not in audio[2] and "OPENAI_LIVE_DELEGATION_MODE" not in capture[2]
    assert audio[2] == capture[2]
    assert not report["implementation_ready"]  # A successful launcher is not observation evidence.


def test_runner_respects_accepted_cohorts_without_waiving_privacy_or_mobile_result(tmp_path, monkeypatch):
    import app.jarvis_tasks.acceptance as acceptance
    commands = []
    def execute(identifier, command, cwd, output, **kwargs):
        commands.append((identifier, command))
        return {"id": identifier, "status": "passed", "evidence": {"mock": True}}
    monkeypatch.setattr(acceptance, "_command", execute)
    result = acceptance.run_acceptance(gate="M8", output=tmp_path / "report", evidence={}, live=True,
        emulator="emulator-5554", audio_dir=tmp_path, database=tmp_path / "fixture.db", backend_url="http://127.0.0.1:8001")
    audio = next(command for identifier, command in commands if identifier == "audio_execution")
    assert "--pilot" in audio and audio[-2:] == ["--case", "ready07"]
    assert "--prepare-scenarios" not in audio
    assert not any(identifier == "capture_execution" for identifier, _ in commands)
    privacy = next(command for identifier, command in commands if identifier == "capture_privacy_execution")
    mobile = next(command for identifier, command in commands if identifier == "mobile_result_execution")
    assert privacy[1].endswith("verify-capture-feasibility.py")
    assert mobile[1].endswith("verify-managed-admission.py") and "--mobile-result" in mobile
    by_id = {row["id"]: row for row in result["checks"]}
    assert by_id["audio_corpus"]["status"] == "accepted_with_caveats"
    assert by_id["capture_privacy"]["status"] == by_id["mobile_result"]["status"] == "blocked"
    assert not result["implementation_ready"]


def test_accepted_decision_cannot_be_forged_or_used_to_waive_other_assertions(monkeypatch):
    import app.jarvis_tasks.acceptance as acceptance
    valid = {**report(), "gate": "M8", "checks": [
        {"id": identifier, "status": "accepted_with_caveats", "evidence": proof}
        for identifier, proof in acceptance.accepted_exceptions("M8").items()]}
    monkeypatch.setattr(acceptance, "validate_observation", lambda identifier, evidence: None)
    valid["checks"].extend({"id": identifier, "status": "passed", "evidence": {"fixture": True}}
        for identifier in acceptance.REQUIRED["M8"] - acceptance.accepted_exceptions("M8").keys())
    assert acceptance.evaluate(valid, valid["candidate"], gate="M8")["implementation_ready"]
    for mutate in (
        lambda r: r["checks"][0].update(status="passed"),
        lambda r: r["checks"][0]["evidence"].update(decision="invented"),
        lambda r: r["checks"][-1].update(status="accepted_with_caveats"),
        lambda r: r["checks"].pop(0),
    ):
        altered = deepcopy(valid)
        mutate(altered)
        assert not acceptance.evaluate(altered, valid["candidate"], gate="M8")["implementation_ready"]
    assert not acceptance.accepted_exceptions("M9")
