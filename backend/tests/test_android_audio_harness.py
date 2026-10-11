import importlib.util
from hashlib import sha256
from pathlib import Path
import wave
import sqlite3
import json
import os
import subprocess
import sys
import time

import pytest


def module(filename):
    path = Path(__file__).resolve().parents[2] / "android/scripts" / filename
    spec = importlib.util.spec_from_file_location(filename, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.mark.skipif(os.name != "nt", reason="Windows native emulator harness")
def test_owned_harness_process_stops_writer_descendants(tmp_path):
    harness = module("verify-screen-workflow.py")
    marker = tmp_path / "writer.txt"
    writer = "import pathlib,time; p=pathlib.Path(" + repr(str(marker)) + "); " + "\nwhile True:\n p.write_text(str(time.time_ns())); time.sleep(.02)"
    parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(writer) + "]); time.sleep(20)"
    process = harness.start_owned_process([sys.executable, "-c", parent], cwd=tmp_path,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert marker.exists(), "Owned descendant never started"
    finally:
        harness.stop_tree(process)
    assert process.poll() is not None
    before = marker.read_bytes()
    time.sleep(.2)
    assert marker.read_bytes() == before


def test_audio_preparation_rejects_header_only_speech_before_output(tmp_path):
    harness = module("verify-audio-corpus.py")
    with wave.open(str(tmp_path / "ready01-wake.wav"), "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
    with pytest.raises(ValueError, match="empty"):
        harness.prepare(tmp_path, tmp_path / "pcm")
    assert not (tmp_path / "pcm").exists()


def test_checksum_mismatch_never_installs_audio_and_removes_staging_file(tmp_path, monkeypatch):
    harness = module("verify-audio-corpus.py")
    pcm = bytes(range(256)) * 100  # Includes bytes that truncate Windows adb stdin.
    (tmp_path / "ready01.pcm").write_bytes(pcm)
    calls = []
    def adb(command, *args, **kwargs):
        calls.append(args)
        return "0" * 64 + " file.pcm" if "sha256sum" in args else ""
    monkeypatch.setattr(harness, "adb", adb)
    monkeypatch.setattr(harness.time, "sleep", lambda *args: None)
    with pytest.raises(ValueError, match="checksum differs"):
        harness.run_sample([], tmp_path / "db", tmp_path, {"id": "ready01", "sha256": sha256(pcm).hexdigest()}, "ready", 1)
    assert any(args[:3] == ("shell", "rm", "-f") for args in calls)
    assert not any("--es" in args and "sample_id" in args for args in calls)


def test_api_pcm_conversion_rejects_silence_and_invalid_sizes():
    renderer = module("generate-api-audio-corpus.py")
    for invalid in (b"", bytes(4800), bytes(4801)):
        with pytest.raises(ValueError):
            renderer.mono_16k(invalid)
    signal = b"\x00\x04\x00\xfc" * 12_000
    converted = renderer.mono_16k(signal)
    assert len(converted) == 32_000
    assert any(converted)


def test_pilot_caps_paid_sessions_and_rejects_scenarios():
    harness = module("verify-audio-corpus.py")
    harness.validate_pilot_cases(["ready01", "ready06", "ready13"])
    for invalid in (None, [], ["ready01"] * 2, ["ready01", "ready02", "ready03", "ready04"], ["followup"], ["bad"]):
        with pytest.raises(ValueError):
            harness.validate_pilot_cases(invalid)


def test_failed_paid_sample_stops_sequence_and_preserves_unknown_outcome(tmp_path):
    harness = module("verify-audio-corpus.py")
    failed = {"sample_id": "billing-failure", "status": "failed", "raw_observations": [{"event": "live_startup_failed"}]}
    with pytest.raises(ValueError, match="Paid sequence stopped"):
        harness.require_sample_success(failed, tmp_path)
    assert json.loads((tmp_path / "stopped-sample.json").read_text()) == failed
    harness.require_sample_success({"status": "observed"}, tmp_path, baseline=True)
    harness.require_sample_success({"status": "passed"}, tmp_path)
    with pytest.raises(ValueError, match="Paid sequence stopped"):
        harness.require_sample_success({"status": "observed"}, tmp_path)


def test_failed_media_snapshot_preserves_rtp_and_never_replays_audio(monkeypatch):
    harness = module("verify-audio-corpus.py")
    initial = [{"event": "live_ready"}, {"event": "command_last_input_sample"}]
    observed = [*initial, {"event": "outbound_rtp", "bytes_sent": 4000, "packets_sent": 70},
        {"event": "runtime_health", "checkpoint": True}, {"event": "assistant_playback"}]
    calls = []
    def adb(command, *args):
        calls.append(args)
        return "snapshot-log"
    monkeypatch.setattr(harness, "adb", adb)
    monkeypatch.setattr(harness, "observations", lambda *args, **kwargs: observed)
    rows, log, media = harness.collect_failed_media([], "sample", initial, "old-log")
    assert rows == observed and log == "snapshot-log"
    assert media == {"status": "observed", "source_command_consumed": True,
        "outbound_rtp_bytes": 4000, "outbound_rtp_packets": 70, "playback_observed": True}
    assert len(calls) == 2
    assert calls[0][-3:] == ("--ez", "snapshot", "true")
    assert all("sample_id" not in call and "clear" not in call for call in calls)


def test_failed_media_missing_session_or_closed_transport_stays_unknown(monkeypatch):
    harness = module("verify-audio-corpus.py")
    calls = []
    monkeypatch.setattr(harness, "adb", lambda *args: calls.append(args) or "")
    rows, _, media = harness.collect_failed_media([], "sample", [], "old-log")
    assert media["status"] == "unavailable" and media["outbound_rtp_bytes"] is None
    assert not calls and rows == []
    initial = [{"event": "live_ready"}]
    _, _, media = harness.collect_failed_media([], "sample", initial, "old-log", timeout=0)
    assert len(calls) == 1
    assert media["status"] == "unknown" and media["outbound_rtp_bytes"] is None
    assert media["source_command_consumed"] is False
    _, _, media = harness.collect_failed_media([], "sample",
        [*initial, {"event": "outbound_rtp", "bytes_sent": True, "packets_sent": -1}], "old-log", timeout=0)
    assert media["outbound_rtp_bytes"] is None and media["outbound_rtp_packets"] is None


def test_mobile_result_rejects_terminal_failure_and_waits_for_real_publication():
    from types import SimpleNamespace
    mobile = module("verify-mobile-result.py")
    class Client:
        status = "running"
        questions = []
        def get(self, url):
            value = {"status": self.status, "questions": self.questions, "results": [{"public_id": "page"}]} if "/tasks/" in url else {"status": "ready"}
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: value)
    client = Client()
    assert mobile.result_snapshot(client, "task") is None
    client.status = "waiting_for_input"  # Promised capture uploads can still be pending.
    assert mobile.result_snapshot(client, "task") is None
    client.questions = [{"state": "asked"}]
    with pytest.raises(RuntimeError, match="unexpectedly requested clarification"):
        mobile.result_snapshot(client, "task")
    client.questions = []
    client.status = "failed"
    with pytest.raises(RuntimeError, match="Real workflow ended"):
        mobile.result_snapshot(client, "task")
    client.status = "completed"
    assert mobile.result_snapshot(client, "task") == {"status": "ready"}


def test_final_latency_is_a_separate_fixed_admission_after_functional_and_cold_cases(tmp_path, monkeypatch):
    harness = module("verify-audio-corpus.py")
    cases = json.loads(harness.MANIFEST.read_text())["cases"]
    manifest = json.loads(harness.MANIFEST.read_text())
    assert manifest["ready_latency_samples_required"] == 1
    assert manifest["ready_final_limit_ms"] == 4500
    assert "ready_p95_limit_ms" not in manifest
    output = tmp_path / "report"
    monkeypatch.setattr(harness.sys, "argv", ["audio", "--render-dir", str(tmp_path),
        "--report", str(output), "--serial", "emulator-5554", "--database", str(tmp_path / "fixture.db"),
        "--backend-url", "http://127.0.0.1:9000", "--live", "--prepare-scenarios"])
    monkeypatch.setattr(harness, "prepare", lambda *args: cases)
    monkeypatch.setattr(harness, "candidate_identity", lambda: {"id": "fixture"})
    monkeypatch.setattr(harness, "backend_attestation", lambda *args: {"instance_id": "fixture"})
    monkeypatch.setattr(harness, "adb", lambda *args, **kwargs: "37")
    monkeypatch.setattr(harness, "voice_provider_observation", lambda *args: {"id": "voice_provider", "status": "passed"})
    async def scenario(*args): return {}
    monkeypatch.setattr(harness, "prepare_scenario", scenario)
    monkeypatch.setattr(harness, "prepare_fresh", lambda *args: {"identities": {}})
    calls = []
    def sample(command, database, pcm, case, path, timeout, **kwargs):
        calls.append((case["id"], path))
        index = len(calls)
        return {"case_id": case["id"], "category": case["category"], "status": "passed", "path": path,
            "sample_id": str(index), "session_id": str(index), "turn_id": str(index),
            "task_id": str(index), "execution_run_id": str(index), "latency_ms": 4499,
            "command_last_sample_ns": 1_000_000, "client_received_ns": 4_500_000_000,
            "real_rtp_bytes_sent": 1, "playback_observed": True, "outcome": {}}
    monkeypatch.setattr(harness, "run_sample", sample)
    harness.main()
    assert calls[-1] == ("ready07", "ready")
    assert calls[-2] == ("fresh", "ready")
    assert len([path for case, path in calls if path == "cold"]) == 5
    assert len(calls) == len(cases) + 5 + 1
    report = json.loads((output / "acceptance.json").read_text())
    final = next(check for check in report["checks"] if check["id"] == "ready_latency")
    assert final["status"] == "passed"
    assert final["evidence"]["policy"] == manifest["ready_latency_policy"]
    assert len(final["evidence"]["samples"]) == 1


@pytest.mark.parametrize("flags", [["--model-diagnostics"], ["--pilot", "--case", "ready07", "--reasoning-summary"]])
def test_output_diagnostic_flags_reject_unsafe_setup_before_starting(tmp_path, monkeypatch, flags):
    harness = module("verify-managed-admission.py")
    destination = tmp_path / "report"
    monkeypatch.setattr(harness.sys, "argv", ["managed-admission", "--report", str(destination),
        "--render-dir", str(tmp_path), *flags])
    with pytest.raises(SystemExit) as error:
        harness.main()
    assert error.value.code == 2
    assert not destination.exists()


@pytest.mark.parametrize("pilot", [False, True])
def test_managed_launcher_runs_one_backend_and_one_corpus_with_cleanup(tmp_path, monkeypatch, pilot):
    from types import SimpleNamespace
    harness = module("verify-managed-admission.py")
    root = tmp_path / "project"
    (root / "backend").mkdir(parents=True)
    destination = root / "data" / "report"
    monkeypatch.setattr(harness, "ROOT", root)
    monkeypatch.setattr(harness.sys, "argv", ["managed-admission", "--report", str(destination),
        "--render-dir", str(tmp_path), *(["--pilot", "--case", "ready07"] if pilot else [])])
    servers, commands, stopped = [], [], []
    def start_service(argv, env, log):
        servers.append((argv, env, log))
        return "backend-process"
    def adb(serial, *args):
        if "voiceinteraction" in args:
            return "mComponent=nl.sander.aicockpit.jarvis"
        return "probe_ready"
    def wait_for(label, condition, **kwargs):
        assert condition(), label
    screen = SimpleNamespace(ensure_emulator=lambda: ("emulator", "emulator-process"),
        adb=adb, wait_for=wait_for, unused_port=lambda: 9000, start_service=start_service,
        stop_tree=stopped.append)
    original_loader = harness.importlib.util.module_from_spec
    original_spec = harness.importlib.util.spec_from_file_location
    monkeypatch.setattr(harness.importlib.util, "spec_from_file_location", lambda name, path:
        SimpleNamespace(name="screen", loader=SimpleNamespace(exec_module=lambda module: None))
        if name == "screen" else original_spec(name, path))
    monkeypatch.setattr(harness.importlib.util, "module_from_spec",
        lambda spec: screen if spec.name == "screen" else original_loader(spec))
    configuration = {"profile": "emulator", "settings": {"worker_pool_enabled": False}}
    monkeypatch.setattr(harness, "resolve_test_configuration", lambda profile, **kwargs:
        configuration if profile == "emulator" else pytest.fail("Unexpected test profile"))
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, *args): return SimpleNamespace(status_code=401)
    monkeypatch.setattr(harness.httpx, "Client", Client)
    def run(argv, **kwargs):
        commands.append((argv, kwargs))
        folder = destination / "audio"
        folder.mkdir()
        (folder / "acceptance.json").write_text(json.dumps({"candidate": {"id": "candidate"},
            "status": "passed", "checks": [{"id": "ready_latency", "status": "passed",
                "evidence": {"statistics": {"count": 20}}}]}))
    monkeypatch.setattr(harness.subprocess, "run", run)
    monkeypatch.delenv("OPENAI_LIVE_DELEGATION_MODE", raising=False)
    assert harness.main() == (2 if pilot else 0)
    assert len(servers) == len(commands) == 1
    assert stopped == ["backend-process", "emulator-process"]
    assert "OPENAI_LIVE_DELEGATION_MODE" not in servers[0][1]
    assert servers[0][2] == destination / "server.log"
    assert ("--pilot" in commands[0][0]) is pilot
    assert ("--prepare-scenarios" in commands[0][0]) is not pilot
    summary = json.loads((destination / "summary.json").read_text())
    assert summary["route"] == "managed_responses"
    assert summary["acceptance_claim"] is not pilot


def test_paid_sample_does_not_wait_for_truncated_log_entries():
    harness = module("verify-audio-corpus.py")
    log = 'I/JarvisAudioAcceptance(123): {"task_id":"task","server_admission_timing":{'
    with pytest.raises(ValueError, match="Malformed Android"):
        harness.observations(log, "sample", strict=True)
    valid = 'I/JarvisAudioAcceptance(123): {"sample_id":"sample","event":"durable_admission"}'
    assert harness.observations(valid, "sample", strict=True)[0]["event"] == "durable_admission"


def test_diagnostic_cleanup_closes_only_observed_sessions_on_same_backend(tmp_path, monkeypatch):
    from types import SimpleNamespace
    harness = module("verify-audio-corpus.py")
    runtime = {"instance_id": "fixture"}
    monkeypatch.setattr(harness, "backend_attestation", lambda *args: runtime)
    monkeypatch.delenv("JARVIS_ACCEPTANCE_DEVICE_TOKEN", raising=False)
    deleted = []
    class Client:
        def __init__(self, **kwargs): assert kwargs["follow_redirects"] is False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, *args, **kwargs): return SimpleNamespace(raise_for_status=lambda: None)
        def delete(self, path, **kwargs):
            deleted.append((path, kwargs))
            return SimpleNamespace(raise_for_status=lambda: None)
    monkeypatch.setattr(harness.httpx, "Client", Client)
    session_id = "00000000-0000-0000-0000-000000000001"
    database = tmp_path / "db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE live_sessions (id,generation,lifecycle_state)")
        db.execute("INSERT INTO live_sessions VALUES (?,2,'ready')", (session_id,))
    samples = [{"session_id": session_id, "generation": 2}, {"raw_observations": [
        {"event": "live_ready", "session_id": session_id, "generation": 2}]}]
    assert harness.close_observed_sessions("http://127.0.0.1:1234", {}, tmp_path / "db", runtime, samples) == 1
    assert deleted == [(f"/api/jarvis/live/sessions/{session_id}", {"params": {"generation": 2}})]
    with pytest.raises(ValueError, match="instance changed"):
        harness.close_observed_sessions("http://127.0.0.1:1234", {}, tmp_path / "db", {"instance_id": "other"}, samples)
    assert len(deleted) == 1
    with sqlite3.connect(database) as db:
        db.execute("UPDATE live_sessions SET lifecycle_state='disconnected'")
    assert harness.close_observed_sessions("http://127.0.0.1:1234", {}, database, runtime, samples) == 0
    assert len(deleted) == 1


@pytest.mark.parametrize("status,latency", [("failed", None), ("passed", 5000), ("passed", None)])
def test_pilot_runner_stops_before_more_paid_sessions(tmp_path, monkeypatch, status, latency):
    harness = module("verify-audio-corpus.py")
    output = tmp_path / "report"
    monkeypatch.setattr(harness.sys, "argv", ["pilot", "--live", "--pilot", "--case", "ready01", "--case", "ready02",
        "--render-dir", str(tmp_path), "--report", str(output), "--serial", "emulator-5554",
        "--database", str(tmp_path / "fixture.db"), "--backend-url", "http://127.0.0.1:1234"])
    monkeypatch.setattr(harness, "candidate_identity", lambda: {"id": "fixture"})
    monkeypatch.setattr(harness, "backend_attestation", lambda *args: {"instance": "fixture"})
    monkeypatch.setattr(harness, "prepare", lambda *args: json.loads(harness.MANIFEST.read_text())["cases"])
    monkeypatch.setattr(harness, "adb", lambda *args: "37")
    monkeypatch.setattr(harness, "voice_provider_observation", lambda *args: {"id": "voice_provider", "status": "blocked", "evidence": {}})
    calls = []
    def sample(command, database, pcm_root, case, path, timeout, **kwargs):
        calls.append((case["id"], path))
        return {"case_id": case["id"], "path": path, "status": status, "latency_ms": latency, "category": case["category"]}
    monkeypatch.setattr(harness, "run_sample", sample)
    assert harness.main() == 2  # A diagnostic pilot never passes full acceptance.
    assert calls == [("ready01", "ready")]
    report = json.loads((output / "acceptance.json").read_text())
    assert report["diagnostic_pilot"]["acceptance_claim"] is False
    assert all(row["id"] != "cold_latency" for row in report["checks"])


@pytest.mark.asyncio
async def test_scenario_setup_uses_durable_task_states_in_an_explicit_isolated_database(tmp_path, monkeypatch):
    from app.config import settings
    harness = module("verify-audio-corpus.py")
    monkeypatch.setattr(harness, "ROOT", tmp_path)
    destination = tmp_path / "data/fixture.db"
    destination.parent.mkdir()
    source, target = sqlite3.connect(settings.database_path), sqlite3.connect(destination)
    try:
        source.backup(target)
    finally:
        source.close(); target.close()
    identities = {}
    for case in ("control", "question", "followup"):
        identities[case] = await harness.prepare_scenario(destination, case)
    replay = await harness.prepare_scenario(destination, "fresh", task_id=identities["question"]["task_id"])
    assert replay["question_id"] == identities["question"]["question_id"]
    with sqlite3.connect(destination) as connection:
        assert connection.execute("SELECT state FROM task_questions WHERE id=?", (identities["question"]["question_id"],)).fetchone() == ("asked",)
        assert connection.execute("SELECT status FROM tasks WHERE id=?", (identities["followup"]["task_id"],)).fetchone() == ("completed",)
        assert connection.execute("SELECT COUNT(*) FROM task_turns WHERE role='user'").fetchone() == (3,)
        assert connection.execute("SELECT COUNT(*) FROM live_session_events").fetchone() == (0,)
        assert connection.execute("SELECT focused_task_id FROM jarvis_state WHERE id='current'").fetchone() == (replay["task_id"],)
        assert connection.execute("SELECT COUNT(*) FROM task_questions").fetchone() == (1,)
    with pytest.raises(ValueError, match="project-owned"):
        await harness.prepare_scenario(tmp_path / "outside.db", "control")


def test_multiple_outcome_assertion_requires_two_distinct_tasks(tmp_path):
    harness = module("verify-audio-corpus.py")
    database = tmp_path / "fixture.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE live_session_events (session_id,sequence,event_type,payload_json)")
        connection.execute("INSERT INTO live_session_events VALUES ('session',1,'provider.user_transcript',?)",
            (json.dumps({"item_id": "input", "transcript": "Create two separate tasks"}),))
    case = {"category": "multiple_outcomes"}
    assert harness.scenario_outcome(database, "session", case, [{"task_id": "same"}, {"task_id": "same"}]) is None
    assert harness.scenario_outcome(database, "session", case, [{"task_id": "one"}, {"task_id": "two"}])["task_ids"] == ["one", "two"]


def test_spoken_clarification_uses_native_live_transcript_and_excludes_old_greeting(tmp_path):
    harness = module("verify-audio-corpus.py")
    database = tmp_path / "dialogue.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE live_session_events (session_id,sequence,event_type,payload_json)")
        for sequence, kind, payload in [(1, "provider.user_transcript", {"item_id": "wake", "transcript": "Jarvis"}),
            (2, "provider.assistant_transcript", {"transcript": "Yeah?"})]:
            connection.execute("INSERT INTO live_session_events VALUES ('session',?,?,?)", (sequence, kind, json.dumps(payload)))
    case = {"category": "ambiguity", "expected": "clarification"}
    assert harness.scenario_outcome(database, "session", case, []) is None
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO live_session_events VALUES ('session',3,'provider.assistant_transcript',?)",
            (json.dumps({"transcript": "How can I help?"}),))
        connection.execute("INSERT INTO live_session_events VALUES ('session',4,'provider.user_transcript',?)",
            (json.dumps({"item_id": "input", "transcript": "Save something useful from this screen."}),))
    assert harness.scenario_outcome(database, "session", case, []) is None
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO live_session_events VALUES ('session',5,'provider.assistant_transcript',?)",
            (json.dumps({"transcript": "What would you like saved from the screen?"}),))
    assert harness.scenario_outcome(database, "session", case, [])["expected"] == "clarification"


def test_nonmatch_requires_the_actual_command_not_just_an_answer_keyword(tmp_path):
    harness = module("verify-audio-corpus.py")
    database = tmp_path / "dialogue.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE live_session_events (session_id,sequence,event_type,payload_json)")
        for sequence, kind, payload in [(1, "provider.user_transcript", {"item_id": "input", "transcript": "about the kokopenguin"}),
            (2, "provider.assistant_transcript", {"transcript": "Did you mean rockhopper penguin?"})]:
            connection.execute("INSERT INTO live_session_events VALUES ('session',?,?,?)", (sequence, kind, json.dumps(payload)))
    case = {"category": "nonmatch", "expected": "spoken_answer"}
    assert harness.scenario_outcome(database, "session", case, []) is None


def test_fresh_clarification_accepts_segmented_native_question_but_not_a_statement():
    harness = module("verify-audio-corpus.py")
    assert harness.recipe_clarification(["Quick question:", "would you like the vegetarian option"])
    assert not harness.recipe_clarification(["The vegetarian recipe is saved."])
    assert not harness.recipe_clarification(["Would you prefer a quick weeknight recipe?"])


def test_fresh_fixture_waits_for_promised_uploads_before_asking(tmp_path, monkeypatch):
    harness = module("verify-audio-corpus.py")
    database = tmp_path / "fixture.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE task_context_requests (execution_run_id,state)")
        db.execute("INSERT INTO task_context_requests VALUES ('run','pending')")
        db.execute("CREATE TABLE task_capture_reports (session_id,capture_mode,status,activation_id,capture_id)")
        db.execute("INSERT INTO task_capture_reports VALUES ('session','activation','captured','activation','capture')")
        db.execute("CREATE TABLE live_session_events (session_id,event_type,payload_json)")
        db.execute("INSERT INTO live_session_events VALUES ('session','provider.assistant_transcript',?)",
            (json.dumps({"transcript": "Would you prefer the vegetarian option?"}),))
    monkeypatch.setattr(harness, "run_sample", lambda *args, **kwargs: {
        "status": "passed", "task_id": "task", "execution_run_id": "run", "session_id": "session"})
    def finish_upload(_):
        with sqlite3.connect(database) as db:
            db.execute("UPDATE task_context_requests SET state='ready'")
    monkeypatch.setattr(harness.time, "sleep", finish_upload)
    async def ask(*args, **kwargs):
        with sqlite3.connect(database) as db:
            assert db.execute("SELECT state FROM task_context_requests").fetchone() == ("ready",)
        return {"task_id": "task", "question_id": "question"}
    monkeypatch.setattr(harness, "prepare_scenario", ask)
    monkeypatch.setattr(harness, "adb", lambda *args: "screen_b_drawn=true")
    assert harness.prepare_fresh([], database, tmp_path, {}, 1)["identities"]["question_id"] == "question"


def test_adb_preserves_subject_as_one_remote_argument(monkeypatch):
    import shlex
    from types import SimpleNamespace
    harness = module("verify-audio-corpus.py")
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout=b"ok")
    monkeypatch.setattr(harness.subprocess, "run", run)
    subject = "Ingredients: carrots, lentils; ginger's rice."
    assert harness.adb(["adb", "-s", "emulator"], "shell", "am", "broadcast", "--es", "subject", subject) == "ok"
    assert shlex.split(calls[0][-1]) == ["am", "broadcast", "--es", "subject", subject]
    harness.adb(["adb"], "push", "local path", "/data/local/tmp/audio")
    assert calls[1] == ["adb", "push", "local path", "/data/local/tmp/audio"]


def test_panel_proof_ignores_centered_label_and_rejects_wrong_screen(tmp_path):
    from PIL import Image, ImageDraw
    harness = module("verify-audio-corpus.py")
    for name, color in (("A", (0, 40, 220)), ("B", (220, 30, 0)), ("blank", (255, 255, 255))):
        image = Image.new("RGB", (200, 400), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 200, 200, 400), fill=color)
        draw.rectangle((80, 295, 120, 305), fill="white")
        image.save(tmp_path / f"{name}.jpg")
    assert harness.fixture_panel(str(tmp_path / "A.jpg"), screen_b=False)
    assert harness.fixture_panel(str(tmp_path / "B.jpg"), screen_b=True)
    assert not harness.fixture_panel(str(tmp_path / "A.jpg"), screen_b=True)
    assert not harness.fixture_panel(str(tmp_path / "blank.jpg"), screen_b=True)
    with Image.open(tmp_path / "B.jpg") as image:
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 240, 200, 400), fill=(40, 36, 53))
        image.save(tmp_path / "B-overlay.jpg")
    assert harness.fixture_panel(str(tmp_path / "B-overlay.jpg"), screen_b=True)


def performance_groups():
    groups = {key: [] for key in ("baseline", "activation", "fresh")}
    for group in groups:
        for index in range(20):
            def event(name, **payload):
                return {"event": name, "observed_ns": 2_000_000_000, "clock": "android_elapsed_realtime_nanos", **payload}
            events = [event("live_wake_detected", observed_ns=1_000_000_000), event("live_ready"),
                event("runtime_health", observed_ns=25_000_000_000, frame_window_start_ns=1_000_000_000,
                    frame_window_end_ns=21_000_000_000, checkpoint=True, frame_count=10,
                    first_draw_frames=1, first_draw_overruns=1, janky_frames=0,
                    dropped_frame_observations=0, invalid_frame_observations=0, audio_underruns=0, anr_exits=0, current_anrs=0)]
            if group != "baseline":
                mode = "fresh" if group == "fresh" else "activation"
                events.extend([event("capture_callback", mode=mode, capture_id="capture", bitmap_present=True,
                    locked=False, callback_ns=3_000_000_000, synchronous_duration_ns=1_000_000),
                    event("command_last_input_sample", command_last_sample_ns=4_000_000_000),
                    event("capture_requested", capture_id="capture"), event("capture_upload_started", capture_id="capture",
                        context_delivered_ns=3_000_000_000, upload_started_ns=3_100_000_000)])
            groups[group].append({"sample_id": f"{group}{index}", "session_id": f"session{group}{index}",
                "status": "observed" if group == "baseline" else "passed", "raw_observations": events,
                "android_screenshot_enabled": "0" if group == "baseline" else "1",
                "real_rtp_bytes_sent": 1000, "playback_observed": True, "anr_event_log": ""})
    return groups


def test_capture_performance_derives_baseline_intervals_and_requires_observed_health():
    from copy import deepcopy
    harness = module("verify-audio-corpus.py")
    groups = performance_groups()
    proof = harness.performance_proof(groups)
    assert proof["live_ready_p95_difference_ms"] == 0.0
    assert proof["callback_sync_ms"] == [1.0] * 40
    assert proof["fresh_request_callback_ms"] == [1000.0] * 20
    assert proof["cached_upload_start_ms"] == [100.0] * 20
    assert harness.validate_observation("capture_performance", proof) is None
    for key, value in (("frame_count", 0), ("audio_underruns", None),
            ("dropped_frame_observations", 1), ("anr_exits", None)):
        missing = deepcopy(groups)
        missing["activation"][0]["raw_observations"][2][key] = value
        with pytest.raises(ValueError):
            harness.performance_proof(missing)
    for row in groups["fresh"]:
        row["raw_observations"][2]["janky_frames"] = 1
    assert harness.validate_observation("capture_performance", harness.performance_proof(groups))
    for row in groups["baseline"]:
        row["raw_observations"][2]["janky_frames"] = 1
    assert not harness.performance_proof(groups)["overlay_jank_observed"]
    groups["fresh"][0]["raw_observations"][2]["observed_ns"] = 1_000_000_000
    with pytest.raises(ValueError, match="preceded"):
        harness.performance_proof(groups)


def test_capture_performance_rejects_wrong_baseline_clock_and_duplicate_sample():
    harness = module("verify-audio-corpus.py")
    groups = performance_groups()
    groups["baseline"][0]["android_screenshot_enabled"] = "1"
    with pytest.raises(ValueError, match="did not disable"):
        harness.performance_proof(groups)
    groups["baseline"][0]["android_screenshot_enabled"] = "0"
    groups["baseline"][0]["raw_observations"][0]["clock"] = "wall_clock"
    with pytest.raises(ValueError, match="incompatible clocks"):
        harness.performance_proof(groups)
    groups["baseline"][0]["raw_observations"][0]["clock"] = "android_elapsed_realtime_nanos"
    groups["activation"][0]["sample_id"] = groups["baseline"][0]["sample_id"]
    with pytest.raises(ValueError, match="Duplicate"):
        harness.performance_proof(groups)


def test_audio_attestation_rejects_other_backends_and_database_configuration(tmp_path, monkeypatch):
    harness = module("verify-audio-corpus.py")
    database = tmp_path / "fixture.db"
    candidate = {"id": "candidate"}
    runtime = {"candidate": candidate, "database_identity": harness.database_identity(database), "instance_id": "startup"}
    from types import SimpleNamespace
    class Client:
        def __init__(self, **kwargs): self.kwargs = kwargs
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, *args, **kwargs): return SimpleNamespace(status_code=200)
        def get(self, *args, **kwargs): return SimpleNamespace(status_code=200, json=lambda: runtime)
    monkeypatch.setattr(harness.httpx, "Client", Client)
    for url in ("https://external.test", "http://user:password@127.0.0.1", "http://127.0.0.1/unrelated"):
        with pytest.raises(ValueError, match="loopback"):
            harness.backend_attestation(url, candidate, database)
    assert harness.backend_attestation("http://127.0.0.1:9000", candidate, database) == runtime
    runtime["candidate"] = {"id": "different"}
    with pytest.raises(ValueError, match="different source"):
        harness.backend_attestation("http://127.0.0.1:9000", candidate, database)
    runtime["candidate"] = candidate
    with pytest.raises(ValueError, match="database"):
        harness.backend_attestation("http://127.0.0.1:9000", candidate, tmp_path / "other.db")
