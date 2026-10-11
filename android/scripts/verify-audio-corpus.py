"""Debug APK input through real wake inference, handoff and Live WebRTC.

Run from backend with uv. Requires a prepared, paired API37 emulator connected
to an authorized openai_live test backend; does not opt in devices or accounts.
No transcript/function-call injection or screenshot fixtures satisfy this gate.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
import shlex
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import wave
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.jarvis_tasks.acceptance import SCHEMA, candidate_identity, database_identity, validate_observation

PACKAGE = "nl.sander.aicockpit.jarvis"
MANIFEST = Path(__file__).with_name("audio-corpus-v1.json")


def backend_attestation(url: str, candidate: dict, database: Path) -> dict:
    target = urlsplit(url)
    if target.scheme not in {"http", "https"} or target.hostname not in {"127.0.0.1", "::1", "localhost"} or (
            target.username or target.password or target.path not in {"", "/"} or target.query or target.fragment):
        raise ValueError("Acceptance requires an explicitly selected loopback backend origin")
    token = os.environ.get("JARVIS_ACCEPTANCE_DEVICE_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(base_url=url, headers=headers, timeout=10, follow_redirects=False) as client:
        if not token:
            response = client.post("/api/auth/login", json={"password": os.environ.get("JARVIS_ACCEPTANCE_PASSWORD", "")})
            if response.status_code != 200:
                raise ValueError("Backend acceptance authentication failed; provide an authorized device token or backend password")
        response = client.get("/api/jarvis/runtime-candidate")
        if response.status_code != 200:
            raise ValueError(f"Backend startup identity unavailable ({response.status_code})")
        runtime = response.json()
    if not isinstance(runtime, dict) or runtime.get("candidate") != candidate:
        raise ValueError("Prepared backend and acceptance process have different source/configuration/runtime candidates")
    if runtime.get("database_identity") != database_identity(database) or not runtime.get("instance_id"):
        raise ValueError("Prepared backend database or startup instance does not match the acceptance reader")
    return runtime


def close_observed_sessions(url, candidate, database, runtime, samples):
    """Close only this attested fixture's observed sessions before host termination."""
    if backend_attestation(url, candidate, database) != runtime:
        raise ValueError("Backend instance changed before diagnostic cleanup")
    owners = set()
    for sample in samples:
        ready = next((row for row in sample.get("raw_observations", []) if row.get("event") == "live_ready"), {})
        session_id = sample.get("session_id", ready.get("session_id"))
        generation = sample.get("generation", ready.get("generation"))
        if session_id and generation:
            from uuid import UUID
            UUID(session_id)
            owners.add((session_id, int(generation)))
    from app.services.jarvis_coordinator import ACTIVE_SESSION_STATES
    active = set()
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        for session_id, generation in owners:
            row = db.execute("SELECT generation,lifecycle_state FROM live_sessions WHERE id=?", (session_id,)).fetchone()
            if not row or row[0] != generation:
                raise ValueError("Observed session identity changed before diagnostic cleanup")
            if row[1] in ACTIVE_SESSION_STATES:
                active.add((session_id, generation))
    token = os.environ.get("JARVIS_ACCEPTANCE_DEVICE_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(base_url=url, headers=headers, timeout=15, follow_redirects=False) as client:
        if not token:
            login = client.post("/api/auth/login", json={"password": os.environ.get("JARVIS_ACCEPTANCE_PASSWORD", "")})
            login.raise_for_status()
        for session_id, generation in sorted(active, key=lambda item: item[1], reverse=True):
            client.delete(f"/api/jarvis/live/sessions/{session_id}", params={"generation": generation}).raise_for_status()
    return len(active)


def waveform(path: Path) -> bytes:
    with wave.open(str(path), "rb") as stream:
        if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth(), stream.getcomptype()) != (16000, 1, 2, "NONE"):
            raise ValueError("Corpus requires uncompressed mono 16 kHz signed 16-bit WAV")
        if not 1600 <= stream.getnframes() <= 16_000 * 30:
            raise ValueError("Corpus sample is empty or outside the duration limit")
        return stream.readframes(stream.getnframes())


def prepare(folder: Path, output: Path) -> list[dict]:
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use a fresh PCM directory")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    prepared = []
    # Validate every source before writing anything or starting a device.
    for case in manifest["cases"]:
        wake = waveform(folder / f"{case['id']}-wake.wav")
        command = waveform(folder / f"{case['id']}-command.wav")
        prefix = bytes(16_000) + wake + bytes(32_000)
        pcm = prefix + command + bytes(64_000)
        prepared.append({**case, "pcm": pcm, "command_start_sample": len(prefix) // 2,
            "command_end_sample": (len(prefix) + len(command)) // 2, "sha256": sha256(pcm).hexdigest(),
            "continuation_pcm": bytes(16_000) + command + bytes(64_000),
            "continuation_end_sample": (16_000 + len(command)) // 2})
    output.mkdir(parents=True, exist_ok=True)
    for case in prepared:
        (output / f"{case['id']}.pcm").write_bytes(case.pop("pcm"))
        continuation = case.pop("continuation_pcm")
        (output / f"{case['id']}-continuation.pcm").write_bytes(continuation)
        case["continuation_sha256"] = sha256(continuation).hexdigest()
    (output / "manifest.json").write_text(json.dumps({"schema": "jarvis-prerecorded-audio.v1",
        "corpus_sha256": sha256(MANIFEST.read_bytes()).hexdigest(), "cases": prepared}, indent=2) + "\n", encoding="utf-8")
    return prepared


def adb(command: list[str], *arguments: str, input_bytes=None) -> str:
    # adb shell joins its arguments into a remote POSIX shell command. Local
    # subprocess argv boundaries alone do not preserve spaces or metacharacters.
    if arguments and arguments[0] == "shell":
        arguments = ("shell", shlex.join(arguments[1:]))
    result = subprocess.run([*command, *arguments], input=input_bytes, capture_output=True, check=True, timeout=30)
    return result.stdout.decode("utf-8", errors="replace")


def observations(log: str, sample_id: str, *, strict=False) -> list[dict]:
    rows = []
    for line in log.splitlines():
        if "JarvisAudioAcceptance" not in line or "{" not in line:
            continue
        try:
            row = json.loads(line[line.index("{"):])
            if row.get("sample_id") == sample_id:
                rows.append(row)
        except ValueError:
            if strict:
                raise ValueError("Malformed Android acceptance log entry; refusing to extend a paid session for missing evidence")
            continue
    return rows


def durable(database: Path, row: dict, session_id: str) -> bool:
    # Read-only verification cannot create, migrate, or change the test backend.
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        return db.execute("SELECT 1 FROM task_turns t JOIN task_execution_runs r ON r.id=t.execution_run_id "
            "JOIN live_sessions l ON l.id=? JOIN live_admission_intents i ON i.session_id=l.id "
            "AND json_extract(i.output_json, '$.turn_id')=t.id "
            "AND json_extract(i.output_json, '$.task_id')=t.task_id "
            "AND json_extract(i.output_json, '$.execution_run_id')=r.id "
            "WHERE t.id=? AND t.task_id=? AND r.id=? AND l.provider_adapter='openai_live'",
            (session_id, row["turn_id"], row["task_id"], row["execution_run_id"])).fetchone() is not None


def recipe_clarification(replies: list[str]) -> bool:
    # Live transcripts split even a single spoken question into segments and
    # need not include final punctuation before the user interrupts to answer.
    text = " ".join(" ".join(replies).lower().split())
    return "vegetarian" in text and ("?" in text or "would you" in text or "do you" in text)


def fixture_panel(path: str, *, screen_b: bool) -> bool:
    """Require a visible color band; the assistant may cover the lower panel."""
    from PIL import Image
    with Image.open(path) as image:
        region = image.convert("RGB").crop((image.width // 8, 0, image.width // 4, image.height))
    def matches(pixel):
        red, green, blue = pixel
        return (red > 140 and red > blue * 2 and red > green * 2) if screen_b else (
            blue > 140 and blue > red * 2 and blue > green * 2)
    consecutive = 0
    for y in range(region.height):
        fraction = sum(matches(region.getpixel((x, y))) for x in range(region.width)) / region.width
        consecutive = consecutive + 1 if fraction >= .95 else 0
        if consecutive >= math.ceil(region.height * .03):
            return True
    return False


def scenario_outcome(database: Path, session_id: str, case: dict, admissions: list[dict]) -> dict | None:
    """Assert actual provider dialogue and durable changes, never injected calls."""
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        events = [(kind, json.loads(payload or "{}")) for kind, payload in db.execute(
            "SELECT event_type,payload_json FROM live_session_events WHERE session_id=? ORDER BY sequence", (session_id,))]
        user = [payload for kind, payload in events if kind == "provider.user_transcript"]
        # Every fixed corpus command contains at least four words. The wake
        # phrase may be transcribed/answered before the command is finalized;
        # it cannot establish the requested dialogue outcome.
        if len(" ".join(payload.get("transcript", "") for payload in user).split()) < 4:
            return None
        origins = {payload.get("item_id") for payload in user}
        calls = [payload for kind, payload in events if kind == "brain.tool.result" and payload.get("origin_item_id") in origins]
        last_input = max(i for i, (kind, _) in enumerate(events) if kind == "provider.user_transcript")
        replies = [payload.get("transcript", "") for kind, payload in events[last_input + 1:]
            if kind in {"provider.assistant_transcript", "provider.assistant_response"}]
        expected = case.get("expected")
        if expected in {"spoken_answer", "clarification"}:
            # The tester checks the fixed corpus outcome. This does not impose
            # wording or an ambiguity classifier on production routing.
            for text in reversed(replies):
                spoken_command = " ".join(payload.get("transcript", "") for payload in user).lower()
                if (expected == "spoken_answer" and "joke" in spoken_command and "penguin" in text.lower()) or (
                    expected == "clarification" and "?" in text):
                    return {"expected": expected, "transcript": text, "user_transcript": user[-1]["transcript"]}
            return None
        if expected == "fresh_screen_B":
            if len(admissions) != 1 or session_id != case.get("session_id"):
                return None
            initial = db.execute("SELECT r.*,b.storage_path,b.sha256 FROM task_capture_reports r "
                "JOIN task_context_requests c ON c.capture_id=r.capture_id AND c.state='ready' "
                "JOIN task_context_blobs b ON b.id=c.blob_id "
                "WHERE r.capture_id=? AND r.session_id=? AND r.activation_id=? AND r.capture_mode='activation' AND r.status='captured'",
                (case.get("initial_capture_id"), session_id, case.get("activation_id"))).fetchone()
            fresh = db.execute("SELECT c.*,b.storage_path,b.sha256 FROM task_context_requests c JOIN task_context_blobs b ON b.id=c.blob_id "
                "WHERE c.execution_run_id=? AND c.session_id=? AND c.activation_id=? AND c.capture_mode='fresh' AND c.state='ready'",
                (admissions[0]["execution_run_id"], session_id, case.get("activation_id"))).fetchone()
            question = db.execute("SELECT asked_at FROM task_questions WHERE id=? AND task_id=?",
                (case.get("question_id"), case.get("task_id"))).fetchone()
            if not initial or not fresh or not question or question["asked_at"] > fresh["requested_at"]:
                return None
            prior_replies = [payload.get("transcript", "") for kind, payload in events[:last_input]
                if kind in {"provider.assistant_transcript", "provider.assistant_response"}]
            if not recipe_clarification(prior_replies):
                return None
            if initial["sha256"] == fresh["sha256"] or not fixture_panel(initial["storage_path"], screen_b=False) or not fixture_panel(fresh["storage_path"], screen_b=True):
                return None
            return {"expected": expected, "api_level": 37, "custom_wake": True,
                "initial_screen_A": True, "fresh_screen_B": True, "clarification_before_B": True,
                "admitted_capture": "B", "initial_capture_id": initial["capture_id"],
                "fresh_capture_id": fresh["capture_id"], "image_sha256": fresh["sha256"],
                "session_id": session_id, "activation_id": case["activation_id"]}
        if expected in {"cancel", "answer_question", "same_task_new_run"}:
            target = case.get("task_id")
            if not target:
                return None
            if expected == "same_task_new_run":
                if len(admissions) != 1 or admissions[0]["task_id"] != target or admissions[0]["execution_run_id"] == case.get("prior_run_id"):
                    return None
                return {"expected": expected, "task_id": target, "prior_run_id": case["prior_run_id"], "new_run_id": admissions[0]["execution_run_id"]}
            task = db.execute("SELECT status FROM tasks WHERE id=?", (target,)).fetchone()
            names = {"cancel_task", "control_task"} if expected == "cancel" else {"answer_task_question"}
            if not any(payload.get("ok") and payload.get("name") in names for payload in calls):
                return None
            if expected == "cancel" and task and task["status"] == "cancelled":
                return {"expected": expected, "task_id": target, "status": task["status"]}
            if expected == "answer_question":
                question = db.execute("SELECT state,answer FROM task_questions WHERE id=? AND task_id=?", (case.get("question_id"), target)).fetchone()
                if question and question["state"] == "answered" and "vegetarian" in (question["answer"] or "").lower():
                    return {"expected": expected, "task_id": target, "question_id": case["question_id"], "state": question["state"]}
            return None
        if case["category"] == "multiple_outcomes" and len(admissions) == 2:
            if len({row["task_id"] for row in admissions}) != 2:
                return None
            return {"expected": "separate_tasks", "task_ids": [row["task_id"] for row in admissions]}
        if expected is None:
            return {"expected": "durable_admission"}
    return None


async def prepare_scenario(database: Path, identifier: str, *, task_id: str | None = None) -> dict:
    """Normal task-service fixtures in the explicitly selected development DB.

    Does not synthesize voice events, capture readiness, calls or images. These
    initial states are recorded separately from the real spoken action.
    """
    from app.config import settings
    from app.db.session import close_engine
    from app.services.task_service import task_service
    from app.services.natural_task_resolver import natural_task_resolver
    target = database.resolve()
    if not target.is_relative_to((ROOT / "data").resolve()):
        raise ValueError("Scenario setup requires a project-owned isolated database under data")
    if not target.is_file():
        raise ValueError("Scenario setup requires an existing isolated database")
    with sqlite3.connect(target.as_uri() + "?mode=ro", uri=True) as db:
        if db.execute("SELECT 1 FROM worker_instances WHERE state IN ('busy','starting','standby') LIMIT 1").fetchone():
            raise ValueError("Scenario setup requires an isolated backend with its worker pool stopped")
    original = settings.database_url
    await close_engine()
    settings.database_url = f"sqlite+aiosqlite:///{target.as_posix()}"
    try:
        if identifier == "fresh":
            if not task_id:
                raise ValueError("Fresh scenario requires its actually admitted initial recipe")
            snapshot = await task_service.get_task(task_id)
        else:
            goal = {"control": "Python comparison task: compare lists and tuples",
                "question": "Recipe task: choose a dinner recipe",
                "followup": "Notes application task: outline a notes application"}[identifier]
            snapshot = await task_service.dispatch(goal=goal, origin_surface="audio_acceptance_setup",
                idempotency_key=f"audio-prerequisite:{identifier}:{time.time_ns()}", result_request={"mode": "answer"})
        result = {"task_id": snapshot.task.id, "prior_run_id": snapshot.task.current_run_id}
        if identifier in {"question", "fresh"}:
            detail = await task_service.get_task(snapshot.task.id)
            pending = [question for question in detail.questions if question.state == "asked"]
            text = "Would you prefer the vegetarian recipe option?"
            if pending:
                if len(pending) != 1 or pending[0].question != text:
                    raise ValueError("Scenario has an unexpected pending question")
                question = pending[0]
            else:
                question = await task_service.ask_question(snapshot.task.id, question=text,
                    idempotency_key=f"audio-question:{time.time_ns()}")
            result["question_id"] = question.id
        if identifier == "followup":
            await task_service.complete(snapshot.task.id, summary="Initial notes application outline complete",
                idempotency_key=f"audio-complete:{time.time_ns()}")
        await natural_task_resolver.touch(snapshot.task.id)
        return result
    finally:
        await close_engine()
        settings.database_url = original



def server_stages(database, session_id):
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute("SELECT event_type,payload_json FROM live_session_events WHERE session_id=? ORDER BY sequence", (session_id,)).fetchall()
    result = []
    for kind, raw in rows:
        payload = json.loads(raw)
        stamp = payload.get("server_monotonic_ns")
        if stamp:
            result.append({"stage": payload.get("stage", kind), "server_monotonic_ns": stamp,
                "origin_correlation_id": payload.get("origin_correlation_id"), "origin_item_id": payload.get("origin_item_id"),
                "provider_call_id": payload.get("provider_call_id", payload.get("call_id")),
                **{key: payload[key] for key in ("tool_item_received_ns", "tool_validation_ms", "request_start_observable",
                    "selector_response_observed_ns") if key in payload}})
        timing = payload.get("output", {}).get("admission_timing", {})
        for stage in ("shell_started", "authority_ready", "gateway_started", "capture_ready", "admission_commit", "gateway_finished"):
            if timing.get(stage + "_ns"):
                result.append({"stage": stage, "server_monotonic_ns": timing[stage + "_ns"],
                    "origin_correlation_id": payload.get("origin_correlation_id"), "provider_call_id": payload.get("call_id"),
                    "clock_source": timing.get("clock_source"), "commit_is_original": timing.get("commit_is_original", False),
                    **({"spans": timing.get("spans", [])} if stage == "gateway_finished" else {})})
    return {"clock": "server_monotonic", "observations": result}

def voice_provider_observation(database):
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        observed = [(session_id, kind, json.loads(raw)) for session_id, kind, raw in db.execute(
            "SELECT session_id,event_type,payload_json FROM live_session_events "
            "WHERE event_type IN ('provider.delegation_usage','provider.delegation_tier_fallback') ORDER BY sequence")]
    usages = [(session_id, row) for session_id, kind, row in observed if kind == "provider.delegation_usage"]
    if not usages:
        return {"id": "voice_provider", "status": "blocked", "evidence": {"reason": "No real delegated usage observed"}}
    failures = []
    proofs = []
    for session_id, usage in usages:
        fallbacks = [row for owner, kind, row in observed if owner == session_id and kind == "provider.delegation_tier_fallback"]
        proof = {"adapter": "openai_live", "requested_tier": usage.get("requested_tier"),
            "actual_tier": usage.get("actual_tier"), "fallback_count": len(fallbacks),
            "fallback_reason": fallbacks[0].get("reason") if fallbacks else None,
            "session_id": session_id, "response_id": usage.get("response_id"), "cost": None,
            "cost_status": "unavailable", "usage": usage.get("usage")}
        error = validate_observation("voice_provider", proof)
        if error: failures.append(error)
        proofs.append(proof)
    return {"id": "voice_provider", "status": "failed" if failures else "passed",
        "evidence": {**proofs[0], "observations": proofs, "failures": failures}}


def run_sample(command, database, pcm_root, case, path, timeout, *, continuation=False, sample_prefix=None,
        measure_only=False, health_required=False, diagnostic_tail_seconds=0):
    sample_id = f"{sample_prefix or path}-{case['id']}"
    if not continuation:
        adb(command, "shell", "am", "broadcast", "-n", f"{PACKAGE}/.assistant.CaptureProbeReceiver", "--es", "command", "hide")
    adb(command, "shell", "am", "broadcast", "-n", f"{PACKAGE}/.assistant.AudioAcceptanceReceiver", "--ez", "clear", "true")
    if case.get("category") == "screen_workflow":
        subject = case.get("subject") or ("Ingredients: tomatoes, chickpeas, spinach, olive oil, garlic."
            if case["id"] in {"ready02", "ready06", "ready10", "ready14", "ready18"}
            else "Research topic: how solar panels convert sunlight into electricity.")
        # Pixels come from the foreground fixture app through the actual
        # assistant callback. The harness never supplies screenshot bytes.
        adb(command, "shell", "am", "start", "-f", "0x10008000", "-n",
            "nl.sander.aicockpit.screenfixture/.MarkerActivity", "--es", "subject", subject)
    time.sleep(1)
    # run-as keeps source PCM in private app storage. Paths come only from the fixed manifest.
    adb(command, "shell", "run-as", PACKAGE, "mkdir", "-p", "files/audio-acceptance")
    pcm_path = pcm_root / f"{case['id']}{'-continuation' if continuation else ''}.pcm"
    digest = case["continuation_sha256"] if continuation else case["sha256"]
    pcm = pcm_path.read_bytes()
    if sha256(pcm).hexdigest() != digest:
        raise ValueError("Prerecorded sample checksum changed")
    # Windows adb stdin can truncate PCM. Use adb's file-transfer protocol
    # for this synthetic test audio, copy into private storage, and remove
    # the temporary emulator file before production audio processing starts.
    remote_path = f"files/audio-acceptance/{sample_id}.pcm"
    staging_path = f"/data/local/tmp/jarvis-audio-{sample_id}-{digest[:16]}.pcm"
    try:
        adb(command, "push", str(pcm_path), staging_path)
        adb(command, "shell", "run-as", PACKAGE, "cp", staging_path, remote_path)
    finally:
        adb(command, "shell", "rm", "-f", staging_path)
    remote_digest = adb(command, "shell", "run-as", PACKAGE, "sha256sum", remote_path).split()[0]
    if remote_digest != digest:
        raise ValueError("Transferred prerecorded sample checksum differs")
    adb(command, "logcat", "-b", "all", "-c")
    adb(command, "shell", "am", "broadcast", "-n", f"{PACKAGE}/.assistant.AudioAcceptanceReceiver",
        "--es", "sample_id", sample_id, "--ei", "command_start_sample", str(8000 if continuation else case["command_start_sample"]),
        "--ei", "command_end_sample", str(case["continuation_end_sample"] if continuation else case["command_end_sample"]),
        "--ez", "ready_path", str(path == "ready").lower(), "--ez", "continuation", str(continuation).lower())
    deadline = time.monotonic() + timeout
    rows = []
    log = ""
    checkpoint_requested = False
    admission_observed_at = None
    while time.monotonic() < deadline:
        log = adb(command, "logcat", "-d", "-v", "brief", "JarvisAudioAcceptance:I", "*:S")
        try:
            rows = observations(log, sample_id, strict=True)
        except ValueError:
            save_sample_log(pcm_root, sample_id, log)
            raise
        if any(row.get("event") == "live_startup_failed" for row in rows):
            break
        admitted = [row for row in rows if row.get("event") == "durable_admission"]
        ready = next((row for row in rows if row.get("event") == "live_ready"), None)
        playback = any(row.get("event") == "assistant_playback" for row in rows)
        rtp = max((row.get("bytes_sent", 0) for row in rows if row.get("event") == "outbound_rtp"), default=0)
        if ready and (measure_only or len(admitted) == case["admissions"]) and playback and rtp > 0 and any(
                row.get("event") == "command_last_input_sample" for row in rows):
            if measure_only or all(durable(database, row, ready["session_id"]) for row in admitted):
                outcome = {"expected": "measured_capture_disabled_baseline"} if measure_only else scenario_outcome(database, ready["session_id"], case, admitted)
                if outcome is None:
                    time.sleep(.2)
                    continue
                if diagnostic_tail_seconds:
                    admission_observed_at = admission_observed_at or time.monotonic()
                    if time.monotonic() - admission_observed_at < diagnostic_tail_seconds and time.monotonic() + .3 < deadline:
                        time.sleep(.2)
                        continue
                if health_required:
                    if not measure_only and admitted:
                        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
                            uploaded = db.execute("SELECT COUNT(*),SUM(state!='ready') FROM task_context_requests WHERE execution_run_id=? AND session_id=?",
                                (admitted[0]["execution_run_id"], ready["session_id"])).fetchone()
                        if not uploaded or not uploaded[0] or uploaded[1] or not any(item.get("event") == "capture_upload_started" for item in rows):
                            time.sleep(.2)
                            continue
                    if not checkpoint_requested:
                        adb(command, "shell", "am", "broadcast", "-n", f"{PACKAGE}/.assistant.AudioAcceptanceReceiver", "--ez", "snapshot", "true")
                        checkpoint_requested = True
                    if not any(item.get("event") == "runtime_health" and item.get("checkpoint") is True for item in rows):
                        time.sleep(.2)
                        continue
                row = admitted[0] if admitted else {}
                anr_log = adb(command, "logcat", "-b", "events", "-d", "-v", "brief", "am_anr:I", "*:S")
                save_sample_log(pcm_root, sample_id, log)
                return {**row, "sample_id": sample_id, "case_id": case["id"], "session_id": ready["session_id"], "path": path,
                    "status": "observed" if measure_only else "passed", "category": case["category"], "pcm_sha256": digest,
                    "real_rtp_bytes_sent": rtp, "playback_observed": playback, "raw_observations": rows,
                    "admissions": admitted, "outcome": outcome, "anr_event_log": anr_log,
                    "server_stages": server_stages(database, ready["session_id"])}
        time.sleep(.2)
    rows, log, media = collect_failed_media(command, sample_id, rows, log)
    save_sample_log(pcm_root, sample_id, log)
    ready = next((row for row in rows if row.get("event") == "live_ready"), None)
    return {"sample_id": sample_id, "case_id": case["id"], "path": path, "status": "failed", "category": case["category"],
        "pcm_sha256": digest, "media_diagnostics": media,
        "raw_observations": rows, **({"session_id": ready["session_id"], "server_stages": server_stages(database, ready["session_id"])} if ready else {})}


def collect_failed_media(command, sample_id, rows, log, *, timeout=3):
    """Preserve media evidence after failure without retrying speech or admission."""
    def summary(status, **detail):
        rtp = [row for row in rows if row.get("event") == "outbound_rtp"]
        def counter(key):
            return max((row[key] for row in rtp if type(row.get(key)) is int and row[key] >= 0), default=None)
        return {"status": status,
            "source_command_consumed": any(row.get("event") == "command_last_input_sample" for row in rows),
            # Source consumption precedes handoff/APM/RTP; it is not proof of
            # received audio or complete provider transcription.
            "outbound_rtp_bytes": counter("bytes_sent"),
            "outbound_rtp_packets": counter("packets_sent"),
            "playback_observed": any(row.get("event") == "assistant_playback" for row in rows), **detail}
    if not any(row.get("event") == "live_ready" for row in rows):
        return rows, log, summary("unavailable", reason="Live readiness was not observed")
    try:
        adb(command, "shell", "am", "broadcast", "-n", f"{PACKAGE}/.assistant.AudioAcceptanceReceiver", "--ez", "snapshot", "true")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            log = adb(command, "logcat", "-d", "-v", "brief", "JarvisAudioAcceptance:I", "*:S")
            rows = observations(log, sample_id, strict=True)
            media = summary("observed")
            if (media["outbound_rtp_bytes"] is not None and media["outbound_rtp_packets"] is not None
                    and any(row.get("event") == "runtime_health" and row.get("checkpoint") is True for row in rows)):
                return rows, log, media
            time.sleep(.2)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return rows, log, summary("unknown", reason=f"Media snapshot failed: {type(exc).__name__}")
    return rows, log, summary("unknown", reason="Media snapshot was not observed before the bounded timeout")


def save_sample_log(pcm_root, sample_id, log):
    folder = pcm_root.parent / "android-logs"
    folder.mkdir(exist_ok=True)
    (folder / f"{sample_id}.log").write_text(log, encoding="utf-8")


def require_sample_success(sample, report, *, baseline=False):
    """Stop paid sequencing on a failed/unknown outcome; retain the failed call."""
    expected = "observed" if baseline else "passed"
    if sample.get("status") != expected:
        (report / "stopped-sample.json").write_text(json.dumps(sample, indent=2), encoding="utf-8")
        raise ValueError(f"Paid sequence stopped after {sample.get('sample_id', sample.get('case_id'))}: "
            f"{sample.get('status', 'unknown')}; no further provider samples were authorized")


def validate_pilot_cases(selected_cases):
    """A diagnostic pilot cannot accidentally expand into cold/scenario sessions."""
    if not 1 <= len(selected_cases or []) <= 3 or len(set(selected_cases)) != len(selected_cases):
        raise ValueError("Pilot requires one to three distinct ready case IDs")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    ready = {case["id"] for case in manifest["cases"][:20]}
    if set(selected_cases) - ready:
        raise ValueError("Pilot supports ready admissions only")


def prepare_fresh(command, database, pcm_root, initial_case, timeout, *, prefix="fresh_initial", health_required=False):
    initial = run_sample(command, database, pcm_root, initial_case, "ready", timeout,
        sample_prefix=prefix, health_required=health_required)
    if initial["status"] != "passed":
        return {"blocked": "Initial real recipe admission failed", "initial": initial}
    # Committed admission deliberately precedes upload completion. Do not ask
    # the fixture question while the task is still waiting for promised images.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
            states = [row[0] for row in db.execute(
                "SELECT state FROM task_context_requests WHERE execution_run_id=?", (initial["execution_run_id"],))]
        if states and all(state == "ready" for state in states):
            break
        if any(state in {"failed", "expired"} for state in states):
            return {"blocked": "Initial promised image upload failed", "initial": initial}
        time.sleep(.2)
    else:
        return {"blocked": "Initial promised image uploads did not complete", "initial": initial}
    identities = asyncio.run(prepare_scenario(database, "fresh", task_id=initial["task_id"]))
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT activation_id,capture_id FROM task_capture_reports WHERE session_id=? AND capture_mode='activation' AND status='captured'", (initial["session_id"],)).fetchone()
    if not row:
        raise ValueError("Initial assistant screenshot missing")
    identities.update(session_id=initial["session_id"], activation_id=row[0], initial_capture_id=row[1])
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
            replies = [json.loads(payload or "{}").get("transcript", "") for (payload,) in db.execute(
                "SELECT payload_json FROM live_session_events WHERE session_id=? AND event_type IN ('provider.assistant_transcript','provider.assistant_response')",
                (initial["session_id"],))]
        if recipe_clarification(replies):
            break
        time.sleep(.2)
    else:
        return {"blocked": "Real Live clarification was not observed before screen B", "initial": initial}
    adb(command, "shell", "am", "broadcast", "-a", "nl.sander.aicockpit.screenfixture.CHANGE",
        "-p", "nl.sander.aicockpit.screenfixture", "--receiver-foreground", "--receiver-include-background",
        "--ez", "screen_b", "true", "--es", "subject", "Ingredients: carrots, lentils, ginger, rice.")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if "screen_b_drawn=true" in adb(command, "logcat", "-d", "-s", "JarvisScreenFixture:I"):
            return {"identities": identities, "initial": initial}
        time.sleep(.2)
    return {"blocked": "Foreground fixture screen B did not draw before continuation", "initial": initial}


def performance_proof(groups):
    """Derive intervals and counters from correlated Android observations."""
    baseline, activation, fresh = (groups[key] for key in ("baseline", "activation", "fresh"))
    if not baseline or len(baseline) != len(activation):
        raise ValueError("Measured paired baseline and activation samples missing")
    rows = [*baseline, *activation, *fresh]
    if len({row["sample_id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate performance samples")
    def event(row, kind, predicate=lambda item: True):
        matches = [item for item in row["raw_observations"] if item.get("event") == kind and predicate(item)]
        if not matches:
            raise ValueError(f"{row['sample_id']}: {kind} observation missing")
        selected = matches[-1]
        if selected.get("clock") != "android_elapsed_realtime_nanos":
            raise ValueError("Performance observations have incompatible clocks")
        return selected
    def interval(end, start):
        if type(end) is not int or type(start) is not int or start <= 0 or end < start:
            raise ValueError("Invalid Android performance interval")
        return (end - start) / 1_000_000
    def startup(row):
        return interval(event(row, "live_ready")["observed_ns"], event(row, "live_wake_detected")["observed_ns"])
    proof = {"clock": "android_elapsed_realtime_nanos", "baseline_identity": sha256(json.dumps(baseline, sort_keys=True).encode()).hexdigest(),
        "baseline_live_ready_ms": [startup(row) for row in baseline], "capture_live_ready_ms": [startup(row) for row in activation],
        "callback_sync_ms": [], "cached_upload_start_ms": [],
        "fresh_request_callback_ms": [], "initial_callback_after_speech_ms": []}
    for row in baseline:
        if row.get("android_screenshot_enabled") != "0":
            raise ValueError("Measured baseline did not disable Android screenshot capture")
    def p95(values):
        return sorted(values)[math.ceil(.95 * len(values)) - 1]
    proof["live_ready_p95_difference_ms"] = p95(proof["capture_live_ready_ms"]) - p95(proof["baseline_live_ready_ms"])
    for row in [*activation, *fresh]:
        if row.get("status") != "passed" or row.get("android_screenshot_enabled") != "1":
            raise ValueError("A required capture sample failed or capture was disabled")
        mode = "fresh" if row in fresh else "activation"
        callback = event(row, "capture_callback", lambda item: item.get("mode") == mode)
        if callback.get("bitmap_present") is not True or callback.get("locked") is not False:
            raise ValueError("Required foreground screenshot was unavailable")
        duration = callback["synchronous_duration_ns"]
        if type(duration) is not int or duration < 0:
            raise ValueError("Invalid synchronous callback measurement")
        proof["callback_sync_ms"].append(duration / 1_000_000)
        if mode == "activation":
            speech_end = event(row, "command_last_input_sample")["command_last_sample_ns"]
            proof["initial_callback_after_speech_ms"].append(max(0, (callback["callback_ns"] - speech_end) / 1_000_000))
            upload = event(row, "capture_upload_started", lambda item: item.get("capture_id") == callback["capture_id"])
            proof["cached_upload_start_ms"].append(interval(upload["upload_started_ns"], upload["context_delivered_ns"]))
        else:
            request = event(row, "capture_requested", lambda item: item.get("capture_id") == callback["capture_id"])
            proof["fresh_request_callback_ms"].append(interval(callback["callback_ns"], request["observed_ns"]))
    audio, anrs, jank = {}, [], []
    normalized = {key: [] for key in ("baseline", "activation", "fresh")}
    for row in rows:
        if not row.get("real_rtp_bytes_sent", 0) or row.get("playback_observed") is not True:
            raise ValueError("Performance requires real RTP and assistant playback")
        health = event(row, "runtime_health", lambda item: item.get("checkpoint") is True)
        for key in ("frame_count", "first_draw_frames", "first_draw_overruns", "janky_frames", "dropped_frame_observations", "invalid_frame_observations", "audio_underruns", "anr_exits", "current_anrs"):
            if type(health.get(key)) is not int or health[key] < 0:
                raise ValueError(f"{row['sample_id']}: measured {key} missing")
        if health["frame_count"] <= health["first_draw_frames"] or health["dropped_frame_observations"] or health["invalid_frame_observations"]:
            raise ValueError("Overlay frame observation is incomplete")
        endpoints = [item["observed_ns"] for item in row["raw_observations"] if item.get("event") in
            {"live_ready", "command_last_input_sample", "capture_callback", "capture_upload_started"}]
        if type(health.get("observed_ns")) is not int or health["observed_ns"] < max(endpoints):
            raise ValueError("Runtime health checkpoint preceded the measured capture interval")
        window = health.get("frame_window_end_ns", 0) - health.get("frame_window_start_ns", 0)
        if window != 20_000_000_000 or health["observed_ns"] < health["frame_window_end_ns"]:
            raise ValueError("Equal completed frame observation windows missing")
        group = "baseline" if row in baseline else "activation" if row in activation else "fresh"
        normalized[group].append(health["janky_frames"] / (health["frame_count"] - health["first_draw_frames"]))
        if "anr_event_log" not in row:
            raise ValueError("System ANR event observation missing")
        anrs.append(health["anr_exits"] + health["current_anrs"] + sum(PACKAGE in line for line in row["anr_event_log"].splitlines()))
        jank.append(health["janky_frames"])
        audio[row["session_id"]] = max(audio.get(row["session_id"], 0), health["audio_underruns"])
    proof.update(audio_underruns=sum(audio.values()), anrs=max(anrs), overlay_jank_observed=max(p95(normalized["activation"]), p95(normalized["fresh"])) > p95(normalized["baseline"]),
        frame_window_ns=20_000_000_000, normalized_frame_rates=normalized,
        sample_ids=[row["sample_id"] for row in rows], audio_underruns_by_session=audio,
        anr_checkpoint_counts=anrs, janky_frame_counts=jank)
    return proof


def run_performance(command, args, cases):
    groups = {key: [] for key in ("baseline", "activation", "fresh")}
    original = adb(command, "shell", "settings", "get", "secure", "assist_screenshot_enabled").strip()
    if original not in {"0", "1", "null"}:
        raise ValueError("Unrecognized Android screenshot setting")
    groups["android_screenshot_setting_before"] = original
    try:
        for index in range(args.performance_samples):
            for group, value in (("baseline", "0"), ("activation", "1")) if index % 2 == 0 else (("activation", "1"), ("baseline", "0")):
                adb(command, "shell", "settings", "put", "secure", "assist_screenshot_enabled", value)
                print(f"Capture performance {group}: {index + 1}/{args.performance_samples}", flush=True)
                row = run_sample(command, args.database, args.report / "pcm", cases[1], "ready", args.timeout,
                    sample_prefix=f"performance-{group}-{index + 1:02}", measure_only=group == "baseline", health_required=True)
                row["android_screenshot_enabled"] = adb(command, "shell", "settings", "get", "secure", "assist_screenshot_enabled").strip()
                groups[group].append(row)
                require_sample_success(row, args.report, baseline=group == "baseline")
        adb(command, "shell", "settings", "put", "secure", "assist_screenshot_enabled", "1")
        fresh = next(case for case in cases if case["id"] == "fresh")
        groups["fresh_setups"] = []
        for index in range(args.performance_samples):
            # Each observation gets its own activation: retained images expire
            # after 90 seconds, well before twenty observation windows finish.
            setup = prepare_fresh(command, args.database, args.report / "pcm", cases[1], args.timeout,
                prefix=f"performance-fresh-initial-{index + 1:02}", health_required=True)
            groups["fresh_setups"].append(setup)
            if "blocked" in setup:
                raise ValueError(f"Paid sequence stopped: {setup['blocked']}")
            print(f"Capture performance fresh: {index + 1}/{args.performance_samples}", flush=True)
            row = run_sample(command, args.database, args.report / "pcm", {**fresh, **setup["identities"]}, "ready", args.timeout,
                continuation=True, sample_prefix=f"performance-fresh-{index + 1:02}", health_required=True)
            row["android_screenshot_enabled"] = adb(command, "shell", "settings", "get", "secure", "assist_screenshot_enabled").strip()
            groups["fresh"].append(row)
            require_sample_success(row, args.report)
    finally:
        if original == "null":
            adb(command, "shell", "settings", "delete", "secure", "assist_screenshot_enabled")
        else:
            adb(command, "shell", "settings", "put", "secure", "assist_screenshot_enabled", original)
        groups["android_screenshot_setting_after"] = adb(command, "shell", "settings", "get", "secure", "assist_screenshot_enabled").strip()
        (args.report / "capture-performance-samples.json").write_text(json.dumps(groups, indent=2) + "\n", encoding="utf-8")
        if groups["android_screenshot_setting_after"] != original:
            raise ValueError("Android screenshot setting was not restored")
    try:
        proof = performance_proof(groups)
    except (KeyError, TypeError, ValueError) as exc:
        return {"id": "capture_performance", "status": "blocked", "evidence": {"reason": str(exc)}}
    error = validate_observation("capture_performance", proof)
    return {"id": "capture_performance", "status": "passed" if error is None else "failed" if args.performance_samples == 20 else "blocked",
        "evidence": {**proof, **({"reason": error} if error else {})}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True, help="Fresh report directory")
    parser.add_argument("--serial", help="Prepared API37 emulator serial")
    parser.add_argument("--database", type=Path, help="Prepared isolated backend database")
    parser.add_argument("--backend-url", help="Prepared loopback backend origin; authenticated startup identity must match this process and database")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--gate", choices=["M8", "M10"], default="M10")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--prerequisites", type=Path, help="Prepared isolated scenario task/question/run identities")
    parser.add_argument("--prepare-scenarios", action="store_true", help="Create normal task-service prerequisites in the selected project-owned isolated DB")
    parser.add_argument("--case", action="append", dest="selected_cases", help="Run selected fixed case IDs for diagnosis; incomplete gates remain blocked")
    parser.add_argument("--pilot", action="store_true", help="One to three selected ready admissions only; stop on failure or latency >=4500 ms. Does not pass acceptance.")
    parser.add_argument("--diagnostic-tail-seconds", type=int, choices=range(0, 6), default=0,
        help="Pilot-only bounded observation after admission; admission timestamps stay unchanged")
    parser.add_argument("--capture-performance", action="store_true", help="Measure twenty capture-disabled baselines, twenty activation uploads and twenty fresh continuations on the prepared emulator")
    parser.add_argument("--performance-samples", type=int, choices=range(1, 21), default=20, help="Smaller diagnostic repetitions cannot pass the twenty-sample gate")
    args = parser.parse_args()
    if args.diagnostic_tail_seconds and not args.pilot:
        parser.error("Diagnostic output tail requires a pilot")
    if args.pilot:
        validate_pilot_cases(args.selected_cases)
        if args.capture_performance or args.prerequisites or args.prepare_scenarios:
            parser.error("Pilot does not run performance or scenario prerequisites")
    candidate = candidate_identity()
    args.report.mkdir(parents=True, exist_ok=False)
    checks = []
    command = None
    runtime = None
    try:
        cases = prepare(args.render_dir, args.report / "pcm")
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if args.selected_cases and set(args.selected_cases) - {case["id"] for case in cases}:
            raise ValueError("Unknown fixed corpus case")
        if not args.live or not args.serial or not args.serial.startswith("emulator-") or not args.database or not args.backend_url:
            raise ValueError("Live acceptance requires an explicitly selected prepared emulator and isolated backend")
        runtime = backend_attestation(args.backend_url, candidate, args.database)
        sdk = Path(os.environ["LOCALAPPDATA"]) / "Android/Sdk/platform-tools/adb.exe"
        command = [str(sdk), "-s", args.serial]
        if int(adb(command, "shell", "getprop", "ro.build.version.sdk").strip()) != 37:
            raise ValueError("API37 is required")
        if args.capture_performance:
            if args.selected_cases or args.prerequisites:
                raise ValueError("Capture performance uses its fixed measured sequence")
            checks.append(run_performance(command, args, cases))
        else:
            results = []
            for path in (("ready",) if args.pilot else ("ready", "cold")):
                selected = cases[:20] if path == "ready" else [case for case in cases if case["id"] in manifest["cold_cases"]]
                if args.selected_cases:
                    selected = [case for case in selected if case["id"] in args.selected_cases]
                samples = []
                for case in selected:
                    print(f"Audio {path}: {case['id']}", flush=True)
                    sample = run_sample(command, args.database, args.report / "pcm", case, path, args.timeout,
                        diagnostic_tail_seconds=args.diagnostic_tail_seconds)
                    samples.append(sample)
                    if not args.pilot:
                        require_sample_success(sample, args.report)
                    if args.pilot and (sample.get("status") != "passed" or sample.get("latency_ms") is None or sample["latency_ms"] >= 4500):
                        print("Pilot stopped: failed admission or 4.5-second latency limit", flush=True)
                        break
                successful = sorted(row["latency_ms"] for row in samples if row.get("status") == "passed" and row.get("latency_ms") is not None)
                proof = {"clock": "android_elapsed_realtime_nanos", "samples": samples,
                    "statistics": {"failures": sum(row.get("status") != "passed" for row in samples),
                        "successful_samples": len(successful), "median_ms": __import__("statistics").median(successful) if successful else None,
                        "p95_ms": successful[math.ceil(.95 * len(successful)) - 1] if successful else None,
                        "maximum_ms": max(successful) if successful else None}}
                if path == "cold":
                    error = validate_observation("cold_latency", proof)
                    checks.append({"id": "cold_latency", "status": "passed" if error is None else "failed", "evidence": proof})
                else:
                    ready_samples = samples
                results.extend(samples)
            proof = {"corpus_sha256": sha256(MANIFEST.read_bytes()).hexdigest(), "cases": results,
                "real_rtp_bytes_sent": sum(row.get("real_rtp_bytes_sent", 0) for row in results),
                "playback_observed": all(row.get("playback_observed") for row in results)}
            prerequisites = json.loads(args.prerequisites.read_text(encoding="utf-8")) if args.prerequisites else {}
            # Prerequisites require one matching task. Run these before the
            # multiple-outcome case creates other Python/notes tasks.
            scenario_order = {"control": 0, "question": 1, "followup": 2,
                "ambiguous": 3, "nonmatch": 4, "multiple": 5, "fresh": 6}
            for case in sorted([] if args.pilot else cases[20:], key=lambda item: scenario_order[item["id"]]):
                if args.selected_cases and case["id"] not in args.selected_cases:
                    continue
                if args.prepare_scenarios and case["id"] in {"control", "question", "followup"}:
                    prerequisites[case["id"]] = asyncio.run(prepare_scenario(args.database, case["id"]))
                if args.prepare_scenarios and case["id"] == "fresh":
                    setup = prepare_fresh(command, args.database, args.report / "pcm", cases[1], args.timeout)
                    if "blocked" in setup:
                        results.append({"case_id": "fresh", "status": "blocked", "category": "fresh_screen",
                            "reason": setup["blocked"], "initial": setup["initial"]})
                        require_sample_success(results[-1], args.report)
                    prerequisites["fresh"] = setup["identities"]
                if case.get("prerequisite") and case["id"] not in prerequisites:
                    results.append({"sample_id": case["id"], "case_id": case["id"], "category": case["category"], "status": "blocked",
                        "reason": "Prepared scenario prerequisite is missing"})
                    continue
                print(f"Audio scenario: {case['id']}", flush=True)
                prepared = prerequisites.get(case["id"], {})
                allowed = {"task_id", "question_id", "prior_run_id", "session_id", "activation_id", "initial_capture_id"}
                if not isinstance(prepared, dict) or set(prepared) - allowed:
                    raise ValueError("Scenario prerequisites may only supply persisted identities")
                results.append(run_sample(command, args.database, args.report / "pcm",
                    {**case, **prepared}, "ready", args.timeout, continuation=case["id"] == "fresh"))
                require_sample_success(results[-1], args.report)
            proof["cases"] = results
            proof["real_rtp_bytes_sent"] = sum(row.get("real_rtp_bytes_sent", 0) for row in results)
            proof["playback_observed"] = all(row.get("playback_observed") for row in results)
            error = validate_observation("audio_corpus", proof)
            checks.append({"id": "audio_corpus", "status": "passed" if error is None else "blocked", "evidence": proof})
            fresh = next((row for row in results if row.get("case_id") == "fresh"), None)
            if fresh and fresh.get("status") == "passed":
                checks.append({"id": "fresh_screen", "status": "passed", "evidence": fresh["outcome"]})
            # The final fixed case is measured after all functional/cold scenarios.
            # Pilots use their last requested case; never choose a faster prior sample.
            if args.pilot:
                final = ready_samples[-1] if ready_samples else {}
            else:
                print(f"Audio final admission: {manifest['ready_final_case']}", flush=True)
                final = run_sample(command, args.database, args.report / "pcm",
                    next(case for case in cases if case["id"] == manifest["ready_final_case"]), "ready", args.timeout)
            final_proof = {"policy": "single_final_under_4500_ms",
                "clock": "android_elapsed_realtime_nanos", "samples": [final],
                "statistics": {"successful_samples": int(final.get("status") == "passed"),
                    "failures": int(final.get("status") != "passed"), "latency_ms": final.get("latency_ms")},
                "functional_ready_samples": ready_samples}
            error = validate_observation("ready_latency", final_proof)
            checks.append({"id": "ready_latency", "status": "passed" if error is None else "failed", "evidence": final_proof})
            if args.gate == "M10":
                checks.append(voice_provider_observation(args.database))
            (args.report / "prerequisites.json").write_text(json.dumps(prerequisites, indent=2), encoding="utf-8")
    except (OSError, ValueError, subprocess.SubprocessError, sqlite3.Error, httpx.HTTPError) as exc:
        checks.append({"id": "capture_performance" if args.capture_performance else "audio_corpus", "status": "blocked", "evidence": {"reason": str(exc)}})
    finally:
        if command:
            adb(command, "shell", "am", "broadcast", "-n", f"{PACKAGE}/.assistant.AudioAcceptanceReceiver", "--ez", "clear", "true")
            adb(command, "shell", "am", "broadcast", "-n", f"{PACKAGE}/.assistant.CaptureProbeReceiver", "--es", "command", "hide")
    report = {"schema": SCHEMA, "gate": args.gate, "candidate": candidate, "checks": checks,
        "finished_at": datetime.now(timezone.utc).isoformat(), "deployment_authorized": False}
    if args.pilot:
        report["diagnostic_pilot"] = {"requested_cases": args.selected_cases, "maximum_sessions": len(args.selected_cases),
            "stop_latency_ms": 4500, "acceptance_claim": False}
    if runtime is not None:
        report["backend_runtime"] = runtime
        try:
            if backend_attestation(args.backend_url, candidate, args.database) != runtime:
                raise ValueError("Prepared backend restarted during audio acceptance")
        except (ValueError, httpx.HTTPError) as exc:
            report["candidate"] = None
            checks.append({"id": "backend_stability", "status": "failed", "evidence": {"reason": str(exc)}})
    if candidate_identity() != candidate:
        report["candidate"] = None
        checks.append({"id": "candidate_stability", "status": "failed", "evidence": {"reason": "Source/configuration changed during audio acceptance"}})
    (args.report / "acceptance.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if checks and all(check["status"] == "passed" for check in checks) else 2


if __name__ == "__main__":
    raise SystemExit(main())
