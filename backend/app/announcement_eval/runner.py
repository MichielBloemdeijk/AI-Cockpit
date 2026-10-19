"""One entry point: uv run python -m app.announcement_eval.runner STAGE.

All paid work is explicit. Saved audio can be regraded independently of voice
sessions. Fresh scoped artifacts are retained on failure, never deleted.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import statistics
import socket
import sqlite3
import subprocess
import sys
import time
import wave

import httpx

from app.config import settings
from app.announcement_eval.grading import RUBRIC, RUBRIC_HASH, GRADER_SCHEMA, caller_windows, frozen_identity, grade, recommendation
from app.announcement_eval.profiles import profile, profile_identity, PREFERENCE_PROFILES, MODEL_ONLY_PROFILES, MODEL_ONLY_FOLLOWUP_PROFILES, MODEL_ONLY_LIMIT

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "data" / "announcement-evaluation"
CORPUS = Path(__file__).with_name("corpus.json")
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
SCREENING_CASES = [{"id": "changed_silence", "expectation": "forbidden", "steps": [
    {"type": "opening"}, {"type": "admit", "count": 1},
    {"type": "speak", "text": "Actually, I changed my mind. Stay quiet when the Hello World page finishes. I will check it myself."},
    {"type": "wait", "seconds": 3}, {"type": "finish"}, {"type": "wait", "seconds": 16}]}]


def load_helper(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "android" / "scripts" / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def root_path(path):
    path = path.resolve()
    if path == BASE.resolve() or not path.is_relative_to(BASE.resolve()):
        raise ValueError("Use a dedicated directory under data/announcement-evaluation")
    return path


def text_variant(text):
    return text.replace("Create a task to write", "Please start a task that writes").replace(
        "Create two separate tasks", "Please start two independent tasks").replace(
        "Tell me when", "Let me know once")


def speech_texts(corpus):
    texts = {corpus["opening"], corpus["holdout_opening"]}
    for case in corpus["cases"] + SCREENING_CASES:
        for step in case["steps"]:
            if "text" in step:
                texts.update((step["text"], text_variant(step["text"])))
    return sorted(texts)


def prepare_corpus(output, *, reuse=None):
    folder = output / "corpus"
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    manifest = {"wake": corpus["wake"], "cases": [
        {"id": sha256(text.encode()).hexdigest()[:16], "text": text} for text in speech_texts(corpus)]}
    manifest_path = output / "speech-manifest.json"
    write(manifest_path, manifest)
    if not folder.exists():
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "android/scripts/generate-audio-corpus.ps1"),
            "-OutputDirectory", str(folder), "-ManifestPath", str(manifest_path)], check=True, timeout=180,
            stdout=subprocess.DEVNULL)
    if reuse is not None:
        source = root_path(reuse)
        source_path = source / "corpus-identity.json"
        saved = json.loads(source_path.read_text(encoding="utf-8")) if source_path.exists() else json.loads((source / "report.json").read_text(encoding="utf-8"))["corpus_identity"]
        if saved["definition"]["sha256"] != frozen_identity(CORPUS)["sha256"] or saved["renderer"]["sha256"] != frozen_identity(ROOT / "android/scripts/generate-audio-corpus.ps1")["sha256"]:
            raise ValueError("Cached corpus definition/renderer differs")
        manifest_names = {case["id"] + "-command.wav" for case in manifest["cases"]}
        for identity in saved["files"]:
            cached = Path(identity["path"]).resolve()
            if not cached.is_relative_to(BASE.resolve()) or cached.name not in manifest_names or frozen_identity(cached) != identity:
                raise ValueError("Cached corpus audio differs from its frozen identity")
        for identity in saved["files"]:
            shutil.copy2(identity["path"], folder / Path(identity["path"]).name)
        write(output / "corpus-reuse.json", {"source": str(source), "copied_files": saved["files"],
            "additional_texts": [case["text"] for case in manifest["cases"] if case["id"] + "-command.wav" not in {Path(f["path"]).name for f in saved["files"]}]})
    identity = {"definition": frozen_identity(CORPUS), "renderer": frozen_identity(ROOT / "android/scripts/generate-audio-corpus.ps1"),
        "files": []}
    for case in manifest["cases"]:
        path = folder / f"{case['id']}-command.wav"
        with wave.open(str(path)) as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000) or audio.getnframes() < 1600:
                raise ValueError("Invalid offline-rendered speech")
        identity["files"].append(frozen_identity(path))
    # Reuse the accepted wake recording when available; no calibration or user recording is required.
    wake = ROOT / "data/audio-corpus-api-v1/ready01-wake.wav"
    if not wake.is_file():
        wake = folder / (manifest["cases"][0]["id"] + "-wake.wav")
    identity["wake"] = frozen_identity(wake)
    identity["sha256"] = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    write(output / "corpus-identity.json", identity)
    return corpus, folder, wake, identity


def corpus_signature(identity):
    return {"definition": identity["definition"]["sha256"], "renderer": identity["renderer"]["sha256"],
        "wake": identity["wake"]["sha256"],
        "files": sorted((Path(f["path"]).name, f["sha256"]) for f in identity["files"])}


def corpus_extension_matches(prior, current):
    """New diagnostic speech may extend the corpus without reusing it as acceptance."""
    old, new = corpus_signature(prior), corpus_signature(current)
    return all(old[key] == new[key] for key in ("definition", "renderer", "wake")) and set(old["files"]) <= set(new["files"])


def unused_port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def wait_ready(url, process, timeout=120):
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=2, trust_env=False) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Owned evaluation service exited during startup; inspect its retained log")
            try:
                if client.get(url).status_code < 400:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(.2)
    raise TimeoutError("Owned evaluation service did not become ready")


def service_environment(folder, selected, timing, backend_port, real_worker=False):
    return {**os.environ, "JARVIS_EVAL_ROOT": str(folder), "JARVIS_EVAL_PROFILE": selected,
        "JARVIS_EVAL_TIMING": timing, "JARVIS_EVAL_TOKEN": secrets.token_urlsafe(32),
        "JARVIS_EVAL_REAL_WORKER": "1" if real_worker else "", "JARVIS_ACCESS_MODE": "personal", "AUTH_PASSWORD": "",
        "DATABASE_URL": f"sqlite+aiosqlite:///{(folder / 'application.db').as_posix()}",
        "SESSION_SECRET_KEY": secrets.token_urlsafe(32), "SESSION_COOKIE_SECURE": "false",
        "TASK_POOL_ENABLED": "true" if real_worker else "false",
        "TASK_ORCHESTRATION_ENABLED": str(settings.task_orchestration_enabled).lower(),
        "TASK_ORCHESTRATION_LIVE_ENABLED": str(settings.task_orchestration_live_enabled).lower(), "LOCAL_LLM_ENABLED": "false",
        "OPENAI_LIVE_MODEL": "gpt-live-1", "OPENAI_LIVE_VOICE": "marin", "OPENAI_LIVE_RESPONSES_MODEL": "gpt-5.6-luna",
        "OPENAI_LIVE_REASONING_EFFORT": "low", "OPENAI_LIVE_REASONING_SUMMARY": "false",
        "OPENAI_LIVE_DIAGNOSTICS_DIR": str(folder / "diagnostics"), "OPENAI_LIVE_IDLE_TIMEOUT_SECONDS": "60",
        "CONVERSATION_WORKSPACES_DIR": str(folder / "conversations"), "TASK_WORKSPACES_DIR": str(folder / "tasks"),
        "TASK_CONTEXT_BLOBS_DIR": str(folder / "context"), "VERIFIER_GENERATED_APPS_DIR": str(folder / "verifier"),
        "CODEX_PROFILES_DIR": str(folder / "profiles"),
        "TASK_ORCHESTRATION_CONTEXT_DIR": str(folder / "background-context"),
        "TASK_ORCHESTRATION_PROFILE": str(folder / "background-profile"),
        "AI_COCKPIT_BACKEND_URL": f"http://127.0.0.1:{backend_port}",
        "AI_COCKPIT_API_PROXY_URL": f"http://127.0.0.1:{backend_port}",
        "AI_COCKPIT_NEXT_DIST_DIR": ".next-announcement-eval", "AI_COCKPIT_TSCONFIG_PATH": ".next-announcement-eval-tsconfig.json",
        "NEXT_PUBLIC_JARVIS_WAKE_MODEL_URL": "/jarvis-wake/models/jarvis.onnx"}


class PaidFailure(RuntimeError):
    pass


class RequestLedger(list):
    def __init__(self, output, entries=()):
        super().__init__(entries)
        self.path = output / "request-ledger.json"

    def persist(self):
        write(self.path, self)


def api_request(client, endpoint, ledger, *, data=None, files=None, payload=None):
    row = {"purpose": endpoint, "started_at": datetime.now(timezone.utc).isoformat()}
    ledger.append(row)
    if isinstance(ledger, RequestLedger): ledger.persist()
    try:
        response = client.post(settings.openai_live_base_url.rstrip("/") + endpoint,
            headers={"Authorization": "Bearer " + settings.openai_api_key}, data=data, files=files, json=payload)
        row.update(status=response.status_code, request_id=response.headers.get("x-request-id"))
        if response.status_code != 200:
            try:
                error = response.json().get("error", {})
                row["error"] = {k: error.get(k) for k in ("type", "code", "param")}
                row["error"]["message"] = str(error.get("message", "")).replace(settings.openai_api_key, "[redacted]")[:2000]
            except ValueError:
                row["error"] = {"type": "non_json_error"}
            raise PaidFailure(f"Paid {endpoint} failed with HTTP {response.status_code}: {row['error']}; no automatic retry")
        result = response.json()
        row["usage"] = result.get("usage")
        row["service_tier"] = result.get("service_tier")
        row["cost"] = None
        return result
    finally:
        row["finished_at"] = datetime.now(timezone.utc).isoformat()
        if isinstance(ledger, RequestLedger): ledger.persist()


def regrade(folder, ledger, *, live):
    artifacts = folder / "artifact-identity.json"
    if artifacts.exists():
        for identity in json.loads(artifacts.read_text(encoding="utf-8")):
            if frozen_identity(Path(identity["path"])) != identity:
                raise ValueError("Recorded evidence changed; cannot regrade it as the frozen run")
    evidence = json.loads((folder / "evidence.json").read_text(encoding="utf-8"))
    # Recover the first recorder-version's missing receive-loop events from its
    # independently retained provider diagnostics. Never rewrite original evidence.
    events = evidence.get("state", {}).get("provider_events", [])
    if events and not any(e.get("stream") == "incoming" for e in events):
        offset = statistics.median(e["epoch_ms"] - e["monotonic_ns"] / 1e6 for e in events)
        recovered = []
        for path in (folder / "diagnostics").glob("*.json"):
            diagnostic = json.loads(path.read_text(encoding="utf-8"))
            for item in diagnostic.get("events", []):
                event = item["event"]
                if str(event.get("type", "")).startswith("response."):
                    event = {"type": "response.event", "event": event}
                recovered.append({"epoch_ms": offset + item["observed_ns"] / 1e6,
                    "monotonic_ns": item["observed_ns"], "stream": "incoming", "event": event,
                    "session_id": diagnostic["session_id"], "generation": diagnostic["generation"]})
        if recovered:
            evidence["state"]["provider_events"] = sorted(events + recovered, key=lambda e: e["epoch_ms"])
            recognized = " ".join(e["event"].get("delta", "") for e in recovered
                if e["event"].get("type") == "session.input_transcript.delta").lower()
            evidence["recognized_request"] = recognized
            evidence["input_understood"] = "hello" in recognized and "world" in recognized and bool(evidence.get("target_task_ids"))
            evidence["recovered_receive_loop_events"] = len(recovered)
            write(folder / "derived-evidence.json", evidence)
    audio_path = folder / "audio-transcription.json"
    judgment_path = folder / f"semantic-grade-{RUBRIC_HASH[:16]}.json"
    if live and not evidence.get("error") and not evidence.get("capture_error"):
        with httpx.Client(timeout=60, trust_env=False) as client:
            if not audio_path.exists():
                with (folder / "assistant.wav").open("rb") as audio:
                    transcript = api_request(client, "/audio/transcriptions", ledger,
                        data={"model": "whisper-1", "response_format": "verbose_json", "language": "en",
                            "timestamp_granularities[]": "word"}, files={"file": ("assistant.wav", audio, "audio/wav")})
                write(audio_path, transcript)
            if not judgment_path.exists():
                transcript = json.loads(audio_path.read_text(encoding="utf-8"))
                # Blind grading: no profile name, injected commentary or backend responses.
                grade_input = {"case_id": evidence["case_id"], "expectation": evidence["expectation"],
                    "caller": [m for m in evidence.get("markers", []) if m["type"].startswith("caller.")],
                    "recognized_caller": [{"epoch_ms": e["epoch_ms"], "text": e["event"].get("delta", "")}
                        for e in evidence.get("state", {}).get("provider_events", [])
                        if e["event"]["type"] == "session.input_transcript.delta"],
                    "tasks": evidence.get("state", {}).get("tasks", []),
                    "words": [{"index": index, **word, "overlap_with_caller": any(
                        evidence["assistant_audio_epoch_ms"] + word["start"] * 1000 < end - 100
                        and evidence["assistant_audio_epoch_ms"] + word["end"] * 1000 > start + 100
                        for start, end in caller_windows(evidence))}
                        for index, word in enumerate(transcript.get("words", []))],
                    "audio_epoch_ms": evidence.get("assistant_audio_epoch_ms")}
                response_path = folder / f"semantic-response-{RUBRIC_HASH[:16]}.json"
                schema = json.loads(json.dumps(GRADER_SCHEMA))
                fields = schema["properties"]["claims"]["items"]["properties"]
                for field in ("start_word", "end_word"):
                    fields[field].update(minimum=0, maximum=max(0, len(grade_input["words"]) - 1))
                    schema["properties"]["penalties"]["items"]["properties"][field].update(
                        minimum=0, maximum=max(0, len(grade_input["words"]) - 1))
                result = json.loads(response_path.read_text(encoding="utf-8")) if response_path.exists() else api_request(client, "/responses", ledger, payload={"model": "gpt-5.6-luna",
                    "service_tier": "priority", "reasoning": {"effort": "low"}, "store": False,
                    "instructions": RUBRIC, "input": json.dumps(grade_input),
                    "text": {"format": {"type": "json_schema", "name": "announcement_grade", "strict": True, "schema": schema}}})
                write(response_path, result)
                text = "".join(c.get("text", "") for item in result.get("output", [])
                    if item.get("type") == "message" for c in item.get("content", []) if c.get("type") == "output_text")
                judgment = json.loads(text)
                judgment["rubric_sha256"] = RUBRIC_HASH
                write(judgment_path, judgment)
    audio = json.loads(audio_path.read_text(encoding="utf-8")) if audio_path.exists() else None
    judgment = json.loads(judgment_path.read_text(encoding="utf-8")) if judgment_path.exists() else None
    if judgment is not None and judgment.get("rubric_sha256") != RUBRIC_HASH:
        judgment = None  # Preserve earlier judgments; a changed rubric needs explicit regrading.
    result = grade(evidence, audio, judgment)
    write(folder / "grade.json", result)
    return result


def provider_failure(evidence):
    text = json.dumps(evidence.get("state", {})).lower()
    return any(word in text for word in ("insufficient_quota", "billing", "invalid_api_key", "rate_limit_exceeded", "authentication_error", "quota_exhausted"))


def model_only_stop(result):
    """Keep shared admission/bookkeeping failures distinct from reminder selection.

    This controls further characterization only. The original failed grade still
    prevents this candidate from qualifying or entering final acceptance.
    """
    if result["status"] == "passed":
        return False
    failures = result.get("failures", [])
    if result.get("unknowns") or not failures:
        return True
    for failure in failures:
        if failure.startswith("notification_preference_not_persisted:"):
            continue
        if failure == "conversation_interrupted" and result.get("interruption_observation") and all(
            item["before_completion"] for item in result["interruption_observation"]):
            continue
        if failure == "conversation_interrupted" and result.get("interruption_observation") and "observed_claims" in result and not any(
            claim["outcome"] in {"completed", "failed"} for claim in result["observed_claims"]):
            continue  # Characterize task selection; retain the unrelated conversation failure.
        return True
    return False


def projection_reuse(row):
    """Reuse unaffected screening; a withheld update cannot test selection."""
    expected = profile_identity(profile(row["profile"]), row["timing"])
    prior = dict(row["profile_identity"])
    prior.pop("sha256"); expected.pop("sha256")
    prior.setdefault("terminal_projection", "production")
    before = prior.pop("terminal_projection"); after = expected.pop("terminal_projection")
    if prior != expected:
        raise ValueError("Resumed routing/prompt policy changed")
    if before == after:
        return "identical_profile"
    if (before, after) != ("production", "all"):
        raise ValueError("Resumed terminal projection changed incompatibly")
    evidence = json.loads((root_path(Path(row["directory"])) / "evidence.json").read_text(encoding="utf-8"))
    if any(t["presentation_policy"] == "silent" and t.get("finished_at") is not None
            for t in evidence["state"]["tasks"]):
        return "withheld_terminal_context"
    return "unchanged_for_recorded_non_silent_tasks"


def recorded_candidate(row, fallback):
    original_report = root_path(Path(row["directory"]).parent) / "report.json"
    return json.loads(original_report.read_text(encoding="utf-8"))["candidate"] if original_report.is_file() else fallback


def recovered_report(output, prior_path, offline_path):
    """Recover a report-writer failure from immutable, already recorded cases."""
    if prior_path is None or offline_path is None:
        raise ValueError("Missing report: recovery requires --resume with its prior pilot and --offline with the recorded candidate")
    prior = json.loads((root_path(prior_path) / "report.json").read_text(encoding="utf-8"))
    offline = root_path(offline_path)
    report = {"schema": "jarvis-announcement-evaluation.v1", "stage": "pilot",
        "candidate": json.loads((offline / "candidate.json").read_text(encoding="utf-8")),
        "corpus_identity": json.loads((offline / "corpus-identity.json").read_text(encoding="utf-8")),
        "rubric_sha256": RUBRIC_HASH, "runs": [], "selected_timing": "listening",
        "prior_pilot_usage": prior["usage"], "recovered_report_writer_failure": True}
    for original in prior["runs"]:
        reuse = projection_reuse(original)
        row = {**original, "recorded_candidate": recorded_candidate(original, prior["candidate"]), "projection_reuse": reuse}
        if reuse == "withheld_terminal_context":
            row.update(cohort="retired_diagnostic", original_grade=original.get("original_grade", original["grade"]),
                grade={"status": "inconclusive", "failures": [], "unknowns": ["terminal_context_withheld_by_production_silent_policy"]})
        report["runs"].append(row)
    for path in output.glob("*/spec.json"):
        spec = json.loads(path.read_text(encoding="utf-8")); folder = path.parent
        evidence = json.loads((folder / "evidence.json").read_text(encoding="utf-8"))
        observed = next(e["event"]["candidate"] for e in evidence["state"]["provider_events"]
            if e["event"]["type"] == "experiment.started")
        if observed["source_digest"] != report["candidate"]["source_digest"]:
            raise ValueError("Recovered source differs from its recorded offline candidate")
        prefix = f"-{spec['profile']}-{spec['case']['id']}-"
        cohort, rest = folder.name.split(prefix, 1)
        repetition, timing, transport = rest.split("-", 2)
        row = {"case_id": spec["case"]["id"], "profile": spec["profile"], "timing": timing, "cohort": cohort,
            "repetition": int(repetition), "transport": transport, "adaptive_id": (spec.get("adaptive") or {}).get("id"),
            "directory": str(folder), "profile_identity": evidence["state"]["profile"],
            "recorded_candidate": report["candidate"], "grade": None}
        report["runs"].append(row)
    if not report["runs"] or len(report["runs"]) > 13:
        raise ValueError("Recovered diagnostic case count is invalid")
    report["usage"] = usage_summary(output, [])
    write(output / "report.json", report)
    return report


def drain_observer(database, *, timeout=30, clock=time.monotonic, sleep=time.sleep, inspect=None):
    """Observe existing background work after voice closes; never retry inference."""
    def snapshot():
        with sqlite3.connect(database.as_uri()+"?mode=ro", uri=True, timeout=.2) as db:
            db.row_factory=sqlite3.Row
            jobs=[dict(r) for r in db.execute("SELECT id,status,completed_at FROM conversation_observations ORDER BY created_at,id")]
            presentations=[dict(r) for r in db.execute("SELECT id,source_task_event_id,state,handled_by,handled_at,handled_event_id FROM task_presentations")]
        for p in presentations:
            if p["handled_at"]:
                dt=datetime.fromisoformat(p["handled_at"])
                p["handled_at"]=(dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()*1000
        return {"jobs":jobs,"presentations":presentations}
    inspect=inspect or snapshot
    start=clock();stable_since=None;previous=None
    while True:
        try: state=inspect()
        except (sqlite3.Error,OSError,ValueError) as exc:
            return {"error":type(exc).__name__,"timed_out":False,"elapsed_ms":(clock()-start)*1000}
        identity=[(j["id"],j["status"],j.get("completed_at")) for j in state["jobs"]]
        busy=any(j["status"] in {"pending","running"} for j in state["jobs"])
        if busy or identity!=previous:stable_since=clock()
        previous=identity
        settled=not busy and clock()-start>=2 and clock()-stable_since>=2
        if settled or clock()-start>=timeout:
            return {**state,"timed_out":not settled,"elapsed_ms":(clock()-start)*1000,
                "meaning":"Background processing observation, independent of notification latency"}
        sleep(min(.2,max(0,timeout-(clock()-start))))


def run_case(output, case, selected, timing, cohort, repetition, corpus_folder, wake, *, adaptive=None, android=False):
    suffix = "android" if android else "adaptive" if adaptive else "browser"
    folder = output / f"{cohort}-{selected}-{case['id']}-{repetition}-{timing}-{suffix}"
    folder.mkdir()
    helper = load_helper("verify-screen-workflow.py")
    backend_port, frontend_port = unused_port(), unused_port()
    env = service_environment(folder, selected, timing, backend_port, real_worker=android and case["id"] == "explicit_idle")
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    frozen_case = json.loads(json.dumps(case))
    if cohort == "acceptance" and case["id"] == "silence" and repetition == 2:
        frozen_case["steps"] = json.loads(json.dumps(SCREENING_CASES[0]["steps"]))
    if cohort == "acceptance":
        for step in frozen_case["steps"]:
            if "text" in step:
                step["text"] = text_variant(step["text"])
    spec = {"run_id": folder.name, "case": frozen_case, "profile": selected, "timing": timing,
        "opening": corpus["holdout_opening"] if cohort == "acceptance" else corpus["opening"],
        "corpus_directory": str(corpus_folder), "wake_audio": str(wake), "adaptive": adaptive,
        "backend_url": f"http://127.0.0.1:{backend_port}", "frontend_url": f"http://127.0.0.1:{frontend_port}",
        "python": sys.executable, "backend_directory": str(BACKEND), "real_worker": android and case["id"] == "explicit_idle"}
    tsconfig = FRONTEND / ".next-announcement-eval-tsconfig.json"
    write(tsconfig, {"extends": "./tsconfig.json", "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", "**/*.mts",
        ".next-announcement-eval/types/**/*.ts", ".next-announcement-eval/dev/types/**/*.ts"],
        "exclude": ["node_modules", ".next", ".next-live-smoke"]})
    spec["frontend_tsconfig"] = frozen_identity(tsconfig)
    write(folder / "spec.json", spec)
    env["JARVIS_EVAL_SPEC"] = str(folder / "spec.json")
    backend_process = frontend_process = None
    with (folder / "backend.log").open("w", encoding="utf-8") as backend_log, (folder / "frontend.log").open("w", encoding="utf-8") as frontend_log:
        try:
            backend_process = helper.start_owned_process([sys.executable, "-m", "uvicorn", "app.announcement_eval.server:app",
                "--host", "127.0.0.1", "--port", str(backend_port)], cwd=BACKEND, env=env,
                stdout=backend_log, stderr=subprocess.STDOUT)
            wait_ready(spec["backend_url"] + "/api/health", backend_process)
            if android:
                command = [sys.executable, str(ROOT / "android/scripts/verify-announcements.py"), "--spec", str(folder / "spec.json")]
            else:
                frontend_process = helper.start_owned_process(["node", str(FRONTEND / "node_modules/next/dist/bin/next"),
                    "dev", "--hostname", "127.0.0.1", "--port", str(frontend_port)], cwd=FRONTEND, env=env,
                    stdout=frontend_log, stderr=subprocess.STDOUT)
                wait_ready(spec["frontend_url"] + "/jarvis", frontend_process)
                command = ["node", str(FRONTEND / "e2e/announcement-eval.mjs")]
            with (folder / "driver.log").open("w", encoding="utf-8") as driver_log:
                process = helper.start_owned_process(command, cwd=FRONTEND, env=env, stdout=driver_log, stderr=subprocess.STDOUT)
                try:
                    process.wait(timeout=180 + int(case.get("observation_tail_ms",0))/1000)
                finally:
                    helper.stop_tree(process)
        finally:
            if android:
                # The emulator app is outside the driver's Windows Job Object.
                # Close its debug recorder even if the driver timed out.
                serial = helper.find_emulator()
                if serial:
                    for extra in (("--ez", "finish_recording", "true"), ("--ez", "clear", "true")):
                        try:
                            helper.adb(serial, "shell", "am", "broadcast", "-n",
                                "nl.sander.aicockpit.jarvis/.assistant.AudioAcceptanceReceiver", *extra, timeout=10)
                        except subprocess.SubprocessError:
                            pass
            # Try normal provider close before terminating any owned host.
            try:
                if backend_process and backend_process.poll() is None:
                    try:
                        with httpx.Client(base_url=spec["backend_url"], timeout=10, trust_env=False) as client:
                            live = client.get("/api/jarvis/state").json().get("session")
                            if live:
                                client.delete(f"/api/jarvis/live/sessions/{live['id']}", params={"generation": live["generation"]})
                    except (httpx.HTTPError, ValueError, KeyError):
                        pass
                    if env.get("JARVIS_CONVERSATION_OBSERVER_MODE", "off") != "off":
                        write(folder/"observer-drain.json", drain_observer(folder/"application.db"))
            finally:
                helper.stop_tree(frontend_process)
                helper.stop_tree(backend_process)
    evidence_path = folder / "evidence.json"
    if not evidence_path.exists():
        write(evidence_path, {"case_id": case["id"], "expectation": case["expectation"],
            "error": "driver_produced_no_evidence", "profile": selected})
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    log_text = (folder / "backend.log").read_text(encoding="utf-8", errors="replace").lower()
    if provider_failure(evidence) or any(s in log_text for s in ("insufficient_quota", "invalid_api_key", "http 401", "http 429", "billing")):
        evidence["paid_failure"] = "Voice provider billing/quota/auth failure; stopped paid sequence"
        write(evidence_path, evidence)
    recorded_files = [p for p in folder.iterdir() if p.is_file()
        and p.name not in {"application.db", "application.db-shm", "application.db-wal", "artifact-identity.json"}]
    recorded_files.extend((folder / "diagnostics").glob("*.json"))
    write(folder / "artifact-identity.json", [frozen_identity(p) for p in recorded_files])
    return folder


def usage_summary(output, ledger):
    frontends, backends, connections = [], [], []
    for path in output.glob("*/events.jsonl"):
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        derived = path.parent / "derived-evidence.json"
        if derived.exists() and not any(r.get("stream") == "incoming" for r in rows):
            rows = json.loads(derived.read_text(encoding="utf-8"))["state"]["provider_events"]
        sessions = {}
        for row in rows:
            event = row["event"]
            if event.get("type") == "provider.connect.request":
                connections.append({"session_id": row.get("session_id"), "generation": row.get("generation"), "epoch_ms": row["epoch_ms"]})
            if event.get("type") == "session.started":
                sessions.setdefault(row.get("session_id"), {"usage": None, "finalized": False})
            if event.get("type") in {"session.usage.updated", "session.closed"}:
                session = sessions.setdefault(row.get("session_id"), {"usage": None, "finalized": False})
                session["usage"] = event.get("usage", session["usage"])
                session["finalized"] = event.get("type") == "session.closed"
            if event.get("type") == "response.event" and event.get("event", {}).get("type") == "response.completed":
                response = event["event"].get("response", {})
                if response.get("usage"):
                    backends.append({"response_id": response.get("id"), "usage": response["usage"]})
        frontends.extend({"session_id": key, **usage} for key, usage in sessions.items())
    # Never sum repeated cumulative snapshots or duplicate backend response IDs.
    backends = list({r["response_id"]: r for r in backends}.values())
    return {"jarvis_voice": frontends, "jarvis_connection_requests": connections,
        "jarvis_backend": backends, "transcription_and_grading_requests": ledger,
        "background_orchestration": [{"directory": str(p.parent),
            "decisions": json.loads(p.read_text(encoding="utf-8")).get("state", {}).get("background_decisions", []),
            "metrics": json.loads(p.read_text(encoding="utf-8")).get("state", {}).get("background_metrics", [])}
            for p in output.glob("*/evidence.json")],
        "simulated_caller": [json.loads(p.read_text(encoding="utf-8")) for p in output.glob("*/caller-usage.json")],
        "real_worker": [{"directory": str(p.parent), "attempts": json.loads(p.read_text(encoding="utf-8")).get("state", {}).get("attempts", [])}
            for p in output.glob("*android/evidence.json")], "monetary_cost": None}


def merge_usage(current, prior):
    if prior:
        for key in ("jarvis_voice", "jarvis_connection_requests", "jarvis_backend", "transcription_and_grading_requests",
            "background_orchestration", "simulated_caller", "real_worker"):
            current[key] = list({json.dumps(row, sort_keys=True): row for row in prior.get(key, []) + current[key]}.values())
    return current


def offline_host_probe(output):
    helper = load_helper("verify-screen-workflow.py")
    folder = output / "host-preflight"; folder.mkdir()
    port = unused_port()
    environment = service_environment(folder, "commentary_relevance", "listening", port)
    environment.update(OPENAI_API_KEY="", TASK_ORCHESTRATION_LIVE_ENABLED="false")
    process = None
    with (folder / "backend.log").open("w", encoding="utf-8") as log:
        try:
            process = helper.start_owned_process([sys.executable, "-m", "uvicorn", "app.announcement_eval.server:app",
                "--host", "127.0.0.1", "--port", str(port)], cwd=BACKEND, env=environment, stdout=log, stderr=subprocess.STDOUT)
            url = f"http://127.0.0.1:{port}"
            wait_ready(url + "/api/health", process, timeout=30)
            with httpx.Client(base_url=url, trust_env=False) as client:
                response = client.get("/__eval/state", headers={"x-eval-token": environment["JARVIS_EVAL_TOKEN"]})
                response.raise_for_status()
                assert response.json()["tasks"] == [] and response.json()["sessions"] == []
                assert client.get("/__eval/state").status_code == 403
                write(folder / "result.json", {"status": "passed", "fresh_migrations": True, "provider_requests": 0})
        finally:
            helper.stop_tree(process)


def execute(args):
    if getattr(args, "natural_experience", False):
        from app.announcement_eval.natural_experience import execute as experience_execute
        return experience_execute(args)
    if getattr(args, "natural_updates", False):
        from app.announcement_eval.natural_updates import execute as natural_execute
        return natural_execute(args)
    if getattr(args,"conversation_bookkeeper",False):
        from app.announcement_eval.bookkeeping import execute as bookkeeping_execute
        return bookkeeping_execute(args)
    if getattr(args,"adoption",False) and args.stage != "offline":
        from app.announcement_eval.adoption import execute as adoption_execute
        return adoption_execute(args)
    if getattr(args, "verify_mechanisms", False) and args.stage != "offline":
        from app.announcement_eval.verification import execute as verification_execute
        return verification_execute(args)
    if getattr(args, "focused", False) and args.stage != "offline":
        from app.announcement_eval.focused import execute as focused_execute
        return focused_execute(args)
    model_only = getattr(args, "model_only", False)
    prompt_followup = getattr(args, "prompt_followup", False)
    native_diagnostics = getattr(args, "native_diagnostics", False)
    if native_diagnostics and (not prompt_followup or not args.resume):
        raise ValueError("Native diagnostics require the retained model-only prompt follow-up")
    model_profiles = (*MODEL_ONLY_PROFILES, *MODEL_ONLY_FOLLOWUP_PROFILES) if prompt_followup else MODEL_ONLY_PROFILES
    if prompt_followup and (not model_only or not args.resume):
        raise ValueError("Prompt follow-up requires continuation of the retained model-only phase")
    if model_only and (args.stage != "pilot" or args.profiles or args.baseline):
        raise ValueError("The model-only phase is a bounded pilot; do not reset the prior diagnostic phase")
    if args.stage in {"pilot", "acceptance"} and not args.live:
        raise ValueError("Paid voice stages require --live")
    if args.live and not settings.openai_api_key.strip():
        raise ValueError("Existing authorized OPENAI_API_KEY is unavailable")
    output = root_path(args.output)
    if args.stage == "report":
        report_path = output / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else recovered_report(output, args.resume, args.offline)
        ledger_path = output / "request-ledger.json"
        entries = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else report["usage"].get("transcription_and_grading_requests", [])
        ledger = RequestLedger(output, entries)
        try:
            for row in report["runs"]:
                if row["cohort"] != "retired_diagnostic":
                    row["grade"] = regrade(Path(row["directory"]), ledger, live=args.live)
        finally:
            report["grading_rubric_sha256"] = RUBRIC_HASH
            report["recommendation"] = recommendation(report["runs"], final=report.get("stage") == "acceptance")
            failed_silence = {r["profile"] for r in report["runs"] if r["cohort"] != "retired_diagnostic"
                and "requested_silence_violated" in (r.get("grade") or {}).get("failures", [])}
            if not report.get("screening_profiles") and failed_silence >= {"commentary_relevance", "thinking_relevance", "thinking_reconsider"}:
                report["stopped_reason"] = "All model-controlled candidates violated explicit silence; paid screening stopped before acceptance"
            report["usage"] = merge_usage(usage_summary(output, ledger), report.get("continued_phase_usage"))
            write(output / "report.json", report)
        return
    output.mkdir(parents=True, exist_ok=False)
    if args.stage == "offline":
        from app.jarvis_tasks.acceptance import candidate_identity
        checked_candidate = candidate_identity()
        commands = [
            [sys.executable, "-m", "pytest", "-q", "tests/test_announcement_eval.py", "tests/test_openai_live_adapter.py",
                "tests/test_model_reported_attention.py", "tests/test_android_audio_harness.py"],
            ["node", "--check", str(FRONTEND / "e2e/announcement-eval.mjs")],
            ["node", "--check", str(FRONTEND / "e2e/announcement-caller.mjs")],
            ["node", str(FRONTEND / "e2e/announcement-offline.mjs")],
            [str(ROOT / "android/gradlew.bat"), "-p", str(ROOT / "android"), ":app:assembleDebug", ":app:testDebugUnitTest"],
        ]
        if getattr(args, "focused", False) or getattr(args, "verify_mechanisms", False):
            commands[0].append("tests/test_announcement_assessment.py")
        if getattr(args, "verify_mechanisms", False):
            commands[0].append("tests/test_announcement_verification.py")
        if getattr(args,"adoption",False):
            commands[0].extend(["tests/test_task_notification_preferences.py", "tests/test_notification_context_floors.py", "tests/test_live_admission_intents.py",
                "tests/test_announcement_assessment.py", "tests/test_announcement_verification.py",
                "tests/test_task_workflows.py", "tests/test_task_notifications.py", "tests/test_task_service.py", "tests/test_jarvis_coordinator.py"])
            commands[0].append("tests/test_orchestration_rollout.py")
            commands.append([sys.executable,"-m","pytest","-q","tests/test_conversation_observer.py","tests/test_natural_updates.py","tests/test_natural_experience.py"])
            commands.append(["node",str(FRONTEND / "node_modules/vitest/vitest.mjs"),"run","--root",str(FRONTEND),
                "lib/jarvis-live.test.ts","lib/jarvis-playback-evidence.test.ts"])
        results = []
        for command in commands:
            # These unit suites cover the direct presentation contract. The live
            # host retains the current background-orchestration configuration.
            environment = {**os.environ, "TASK_POOL_ENABLED": "false", "TASK_ORCHESTRATION_ENABLED": "false",
                "TASK_ORCHESTRATION_LIVE_ENABLED": "false", "JARVIS_EVAL_NETWORK_BLOCKED": "1",
                "JARVIS_ACCESS_MODE":"personal","AUTH_PASSWORD":"","SESSION_COOKIE_SECURE":"false"}
            timeout = 600 if command[1:3] == ["-m", "pytest"] else 180
            try:
                run = subprocess.run(command, cwd=BACKEND, env=environment, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired as error:
                def decoded(value):
                    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
                results.append({"command": command, "exit_code": 124, "timed_out": True,
                    "timeout_seconds": timeout, "output": decoded(error.stdout) + decoded(error.stderr)})
                write(output / "offline.json", results)
                raise RuntimeError("Offline check timed out; partial output retained") from error
            results.append({"command": command, "exit_code": run.returncode, "output": run.stdout + run.stderr})
            if run.returncode:
                write(output / "offline.json", results)
                raise RuntimeError("Offline checks failed")
        write(output / "offline.json", results)
        offline_host_probe(output)
        prepare_corpus(output, reuse=args.corpus_from)
        if getattr(args, "focused", False):
            from app.announcement_eval.focused import prepare_corpus as prepare_focused_corpus
            prepare_focused_corpus(output, reuse=args.corpus_from)
        if getattr(args, "verify_mechanisms", False):
            from app.announcement_eval.verification import prepare_corpus as prepare_verification_corpus
            prepare_verification_corpus(output)
        if getattr(args,"adoption",False):
            from app.announcement_eval.adoption import prepare_corpus as prepare_adoption_corpus
            prepare_adoption_corpus(output,concise=getattr(args,"concise_updates",False),
                acknowledgment=getattr(args,"acknowledgment_probe",False))
        if candidate_identity() != checked_candidate:
            raise ValueError("Source/configuration changed during offline checks")
        write(output / "candidate.json", checked_candidate)
        return
    if not args.offline:
        raise ValueError("Paid stages require --offline pointing to passing offline evidence")
    offline = root_path(args.offline)
    if not all(r["exit_code"] == 0 for r in json.loads((offline / "offline.json").read_text(encoding="utf-8"))):
        raise ValueError("Offline evidence did not pass")
    corpus, corpus_folder, wake, corpus_identity = prepare_corpus(offline)
    from app.jarvis_tasks.acceptance import candidate_identity
    candidate = candidate_identity()
    if json.loads((offline / "candidate.json").read_text(encoding="utf-8")) != candidate:
        raise ValueError("Source/configuration changed after offline checks")
    if args.stage == "acceptance":
        if not args.pilot:
            raise ValueError("Acceptance requires --pilot pointing to a qualifying frozen pilot report")
        pilot = json.loads((root_path(args.pilot) / "report.json").read_text(encoding="utf-8"))
        if pilot["candidate"] != candidate or pilot["corpus_identity"] != corpus_identity:
            raise ValueError("Candidate/corpus changed after pilot; saved evidence cannot qualify this candidate")
        selected = pilot["recommendation"]["profile"]
        if selected is None or profile(selected).diagnostic_only:
            raise ValueError("No candidate qualified in the pilot")
        timing = pilot.get("selected_timing", "listening")
    report = {"schema": "jarvis-announcement-evaluation.v1", "stage": args.stage, "candidate": candidate,
        "corpus_identity": corpus_identity, "rubric_sha256": RUBRIC_HASH, "runs": [], "selected_timing": "listening"}
    if model_only:
        prior_path = root_path(args.prior) / "report.json" if getattr(args, "prior", None) else None
        if prior_path is None or not prior_path.is_file():
            raise ValueError("The model-only phase requires --prior pointing to the retained thirteen-run report")
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
        if prior.get("stage") != "pilot" or len(prior["runs"]) != 13:
            raise ValueError("The retained prior diagnostic phase must contain its thirteen runs")
        report.update(diagnostic_phase="model-only-context-v1", diagnostic_limit=MODEL_ONLY_LIMIT,
            prior_phase=frozen_identity(prior_path), prior_phase_run_count=len(prior["runs"]),
            screening_profiles=list(model_profiles), additional_screening_cases=SCREENING_CASES,
            decision_owner="gpt-live", screening_order=["explicit_idle", "silence", "changed_silence", "occupied"])
        if prompt_followup:
            report["prompt_followup"] = {"profiles": list(MODEL_ONLY_FOLLOWUP_PROFILES),
                "purpose": "Isolate reconsideration wording, conditional outcome authority, complete-request delegation, and stronger interruption/relevance priority; no application reminder decisions"}
            report["targeted_screening_order"] = {"live_thinking_strict_floor": ["occupied", "explicit_idle", "silence", "changed_silence"]}
        if native_diagnostics:
            report["native_diagnostics"] = {"acknowledgment_probe": "live_ack_probe", "context_timing_profile": "live_thinking_listen_first",
                "speech_decision_owner": "gpt-live", "diagnostic_only": True, "phase_limit_unchanged": MODEL_ONLY_LIMIT}
    ledger = RequestLedger(output)
    count = 0
    prior_model_usage = None
    if model_only and args.resume:
        resumed = json.loads((root_path(args.resume) / "report.json").read_text(encoding="utf-8"))
        if resumed.get("diagnostic_phase") != "model-only-context-v1" or resumed.get("diagnostic_limit") != MODEL_ONLY_LIMIT:
            raise ValueError("Model-only continuation must retain the same bounded phase")
        if not corpus_signature(resumed["corpus_identity"]) == corpus_signature(corpus_identity):
            raise ValueError("Model-only continuation changed the frozen corpus")
        if list(model_profiles)[:len(resumed["screening_profiles"])] != resumed["screening_profiles"] or resumed["prior_phase"] != report["prior_phase"]:
            raise ValueError("Model-only continuation changed the comparison or prior evidence")
        if prompt_followup and not set(MODEL_ONLY_PROFILES) <= {r["profile"] for r in resumed["runs"]}:
            raise ValueError("Complete the original ten configuration screens before the targeted prompt follow-up")
        for original in resumed["runs"]:
            if original["profile_identity"] != profile_identity(profile(original["profile"]), original["timing"]):
                raise ValueError("Model-only continuation changed a recorded prompt or channel sequence")
            folder = Path(original["directory"])
            saved_grade = folder / "original-grade.json"
            if not saved_grade.exists() and (folder / "grade.json").exists():
                shutil.copy2(folder / "grade.json", saved_grade)
            row = {**original, "reused_from": str(args.resume),
                "original_grade": original.get("original_grade", original.get("grade")),
                "recorded_candidate": original.get("recorded_candidate", resumed["candidate"]),
                "grade": regrade(folder, ledger, live=False)}
            report["runs"].append(row)
        count = len(report["runs"])
        if not 0 < count <= MODEL_ONLY_LIMIT:
            raise ValueError("Model-only continuation case count is invalid")
        prior_model_usage = resumed["usage"]
        report["continued_phase_usage"] = prior_model_usage
        report["continued_phase"] = frozen_identity(root_path(args.resume) / "report.json")
        report["grading_source_candidate"] = candidate

    def all_usage():
        return merge_usage(usage_summary(output, ledger), prior_model_usage)
    cases = {c["id"]: c for c in corpus["cases"]}
    if args.stage == "pilot":
        cases.update({c["id"]: c for c in SCREENING_CASES})
    profiles = model_profiles if model_only else args.profiles or ("commentary_relevance", "thinking_relevance", "thinking_reconsider")
    if args.profiles:
        if args.stage != "pilot" or not args.resume or any(name not in PREFERENCE_PROFILES for name in profiles):
            raise ValueError("New preference screening requires pilot --resume and only the isolated preference profiles")
        report["screening_profiles"] = list(profiles)
        report["additional_screening_cases"] = SCREENING_CASES

    def one(case_id, name, timing="listening", cohort="pilot", repetition=1, **extra):
        nonlocal count
        if model_only:
            existing = next((r for r in report["runs"] if (r["case_id"], r["profile"], r["timing"], r["cohort"], r["repetition"])
                == (case_id, name, timing, cohort, repetition)), None)
            if existing is not None:
                return existing
        if args.stage == "pilot" and count >= (MODEL_ONLY_LIMIT if model_only else 13):
            raise RuntimeError("Diagnostic case limit reached")
        count += 1
        print(f"Run {count}: {name}/{case_id}/{timing}", flush=True)
        folder = run_case(output, cases[case_id], name, timing, cohort, repetition, corpus_folder, wake, **extra)
        row = {"case_id": case_id, "profile": name, "timing": timing, "cohort": cohort,
            "transport": "android" if extra.get("android") else "adaptive" if extra.get("adaptive") else "browser",
            "repetition": repetition, "adaptive_id": extra.get("adaptive", {}).get("id"),
            "directory": str(folder), "profile_identity": profile_identity(profile(name), timing),
            "recorded_candidate": candidate, "grade": None}
        report["runs"].append(row)
        write(output / "report.json", {**report, "usage": all_usage()})
        evidence = json.loads((folder / "evidence.json").read_text(encoding="utf-8"))
        if evidence.get("paid_failure"):
            row["grade"] = {"status": "inconclusive", "failures": [], "unknowns": [evidence["paid_failure"]]}
            raise PaidFailure(evidence["paid_failure"])
        row["grade"] = regrade(folder, ledger, live=True)
        if row["grade"]["status"] == "inconclusive" or row["grade"].get("unknowns"):
            raise RuntimeError("Scenario evidence is inconclusive; stop paid work for local investigation")
        return row

    try:
        if args.stage == "pilot" and model_only:
            for name in profiles:
                for case_id in report.get("targeted_screening_order", {}).get(name, report["screening_order"]):
                    row = next((r for r in report["runs"] if r["case_id"] == case_id and r["profile"] == name
                        and r["cohort"] == "pilot"), None)
                    if row is None:
                        row = one(case_id, name)
                    if model_only_stop(row["grade"]):
                        break  # Keep this failure; investigate the next distinct configuration.
            # One transport-only diagnostic for a retained context-delivery gap.
            # It changes delivery timing, never the model's speech decision.
            if args.resume and any(r["profile"] == "live_commentary_context" and r["case_id"] == "silence"
                and any("terminal_context_not_delivered" in u for u in r["grade"].get("unknowns", [])) for r in report["runs"]):
                if not any(r["cohort"] == "delivery_diagnostic" for r in report["runs"]):
                    one("silence", "live_commentary_context", "immediate", cohort="delivery_diagnostic")
            if native_diagnostics:
                one("explicit_idle", "live_ack_probe", cohort="bookkeeping_probe")
                one("occupied", "live_thinking_listen_first", "immediate", cohort="context_timing_probe")
            choice = recommendation(report["runs"])
            if choice["profile"]:
                immediate_passes = []
                for repetition in (1, 2):
                    listening = one("occupied", choice["profile"], cohort="timing", repetition=repetition)
                    if listening["grade"]["status"] != "passed":
                        break
                    immediate = one("occupied", choice["profile"], "immediate", cohort="timing", repetition=repetition)
                    immediate_passes.append(immediate["grade"]["status"] == "passed")
                    if not immediate_passes[-1]:
                        break
                if immediate_passes == [True, True]:
                    report["selected_timing"] = "immediate"
            else:
                report["stopped_reason"] = "All defined model-only configurations have retained screening failures or inconclusive evidence; no candidate qualifies"
        elif args.stage == "pilot":
            if args.resume:
                prior = json.loads((root_path(args.resume) / "report.json").read_text(encoding="utf-8"))
                matches = corpus_extension_matches(prior["corpus_identity"], corpus_identity) if args.profiles else corpus_signature(prior["corpus_identity"]) == corpus_signature(corpus_identity)
                if prior["stage"] != "pilot" or not matches:
                    raise ValueError("Resumed screening must use the same pilot corpus")
                report["prior_pilot_usage"] = prior["usage"]
                report["prior_baseline_usage"] = prior.get("prior_baseline_usage")
                for original in prior["runs"]:
                    reuse = projection_reuse(original)
                    row = {**original, "reused_from": str(args.resume), "projection_reuse": reuse,
                        "recorded_candidate": recorded_candidate(original, prior["candidate"])}
                    if reuse == "withheld_terminal_context" or original["cohort"] == "retired_diagnostic":
                        row["cohort"] = "retired_diagnostic"
                        row["original_grade"] = original.get("original_grade", original["grade"])
                        row["grade"] = {"status": "inconclusive", "failures": [],
                            "unknowns": ["terminal_context_withheld_by_production_silent_policy"]}
                    else:
                        row["grade"] = regrade(Path(row["directory"]), ledger, live=False)
                    report["runs"].append(row)
                count = len(report["runs"])
                bases = [r for r in report["runs"] if r["cohort"] == "baseline"]
                if len(bases) != 1: raise ValueError("Resumed pilot requires one baseline")
                base = bases[0]
            elif args.baseline:
                prior = json.loads((root_path(args.baseline) / "report.json").read_text(encoding="utf-8"))
                bases = [r for r in prior["runs"] if r["cohort"] == "baseline" and r["case_id"] == "explicit_idle"]
                if len(bases) != 1 or projection_reuse(bases[0]) != "identical_profile":
                    raise ValueError("Prior baseline is not the same routing/prompt profile")
                if corpus_signature(prior["corpus_identity"]) != corpus_signature(corpus_identity):
                    raise ValueError("Prior baseline audio differs from frozen corpus")
                base = {**bases[0], "reused_from": str(args.baseline), "recorded_candidate": prior["candidate"], "grade": None}
                report["runs"].append(base); count = 1
                report["prior_baseline_usage"] = prior["usage"]
                base["grade"] = regrade(Path(base["directory"]), ledger, live=True)
            else:
                base = one("explicit_idle", "baseline", cohort="baseline")
            if base["grade"]["status"] == "inconclusive":
                raise RuntimeError("Baseline media/recognition evidence inconclusive; repair locally before more paid runs")
            control_used = any(r["cohort"] == "control" for r in report["runs"])
            screened = False
            for name in profiles:
                if name == "thinking_reconsider" and screened:
                    break
                passes = True
                screening_cases = ("explicit_idle", "silence", "changed_silence", "occupied") if name in PREFERENCE_PROFILES else ("explicit_idle", "silence", "occupied")
                for case_id in screening_cases:
                    row = next((r for r in report["runs"] if r["case_id"] == case_id and r["profile"] == name
                        and r["cohort"] == "pilot"), None)
                    if row is None: row = one(case_id, name)
                    if row["grade"].get("unknowns"):
                        raise RuntimeError("Resumed evidence remains inconclusive; stop paid work")
                    if row["grade"]["status"] != "passed":
                        passes = False
                        if not control_used and any("requested_outcome_not_spoken" in f for f in row["grade"]["failures"]):
                            one("explicit_idle", "commentary_control", cohort="control")
                            control_used = True
                        break
                screened |= passes
                if passes and name in PREFERENCE_PROFILES:
                    break  # Freeze the first qualifying new candidate; no unnecessary sweep.
            choice = recommendation(report["runs"])
            if choice["profile"] == "commentary_relevance":
                immediate_passes = []
                for repetition in (1, 2):
                    row = one("occupied", choice["profile"], cohort="timing", repetition=repetition)
                    if row["grade"]["status"] != "passed":
                        break
                    immediate = one("occupied", choice["profile"], "immediate", cohort="timing", repetition=repetition)
                    immediate_passes.append(immediate["grade"]["status"] == "passed")
                    if not immediate_passes[-1]:
                        break
                if immediate_passes == [True, True]:
                    report["selected_timing"] = "immediate"
        else:
            report["selected_timing"] = timing
            for repetition in (1, 2, 3):
                for case_id in cases:
                    row = one(case_id, selected, timing, "acceptance", repetition)
                    if row["grade"]["status"] != "passed":
                        raise RuntimeError("Frozen candidate regressed; retain the incomplete cohort and stop")
            for index, agenda in enumerate(corpus["adaptive"], 1):
                row = one(agenda["family"], selected, timing, "acceptance", index + 3, adaptive=agenda)
                if row["grade"]["status"] != "passed":
                    raise RuntimeError("Adaptive candidate regressed; stop")
            for case_id in corpus["android"]:
                row = one(case_id, selected, timing, "acceptance", 1, android=True)
                if row["grade"]["status"] != "passed":
                    raise RuntimeError("Android candidate regressed; stop")
    except Exception as error:
        report["stopped_reason"] = str(error)
        raise
    finally:
        report["recommendation"] = recommendation([r for r in report["runs"] if r.get("grade")], final=args.stage == "acceptance")
        report["usage"] = all_usage()
        write(output / "report.json", report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("offline", "pilot", "acceptance", "report"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--natural-updates", action="store_true", help="Four configurations, 60 calibration requests, 24 browser and 6 Android sessions")
    parser.add_argument("--natural-experience", action="store_true", help="Agent-reviewed architecture comparison: 240 classifications, 24 exploration, 32 confirmation, six Android connections")
    parser.add_argument("--family", choices=("reference", "baseline", "preferences", "bookkeeping", "both"))
    parser.add_argument("--fixture-set", choices=("development", "heldout"), default="development")
    parser.add_argument("--diagnostic", help="Frozen unique natural-experience diagnostic identity")
    parser.add_argument("--configuration", choices=("1","2","3","4"))
    parser.add_argument("--calibrate", action="store_true", help="Text calibration only, requires --live")
    parser.add_argument("--confirm", action="store_true", help="Frozen winner confirmation, requires passed screen")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--offline", type=Path)
    parser.add_argument("--pilot", type=Path)
    parser.add_argument("--baseline", type=Path, help="Reuse one recorded baseline instead of repeating its voice session")
    parser.add_argument("--resume", type=Path, help="Reuse unaffected screening after a harness repair; retain identities and retired diagnostics in the case limit")
    parser.add_argument("--profiles", nargs="+", help="Authorized follow-up preference candidates; keep prior runs in the thirteen-case ceiling")
    parser.add_argument("--corpus-from", type=Path, help="Offline only: reuse verified frozen audio alongside new diagnostic speech")
    parser.add_argument("--model-only", action="store_true", help="New model-controlled context phase: ten fixed configurations, at most 44 diagnostic runs")
    parser.add_argument("--prior", type=Path, help="Retained thirteen-run phase; referenced with original identities, never reused as new candidate evidence")
    parser.add_argument("--prompt-followup", action="store_true", help="Targeted model-prompt variants after the ten channel screens; retain the same 44-session phase limit")
    parser.add_argument("--native-diagnostics", action="store_true", help="One explicit post-speech delegation probe and one occupied immediate-context case; no application reminder decisions")
    parser.add_argument("--focused", action="store_true", help="Calibrated A/B diagnostic, at most 24 fresh sessions; separate historical reassessment")
    parser.add_argument("--verify-mechanisms", action="store_true", help="Independent duplex audit and at most eight targeted floor/deadline probes")
    parser.add_argument("--adoption",action="store_true",help="Production A adoption; 16 browser plus six gated Android sessions")
    parser.add_argument("--conversation-bookkeeper",action="store_true",help="Subscriptions and observer: twelve calibration fixtures, at most 16 browser and six gated Android sessions")
    parser.add_argument("--concise-updates",action="store_true",help="New adoption tuning phase: one concise production policy, at most eight provider sessions")
    parser.add_argument("--acknowledgment-probe",action="store_true",help="Plain-language delivery recording: one idle case and one explicit bookkeeping control, at most two sessions")
    parser.add_argument("--agent-review",action="store_true",help="Record one adoption case for direct agent review; no new semantic-grader request")
    parser.add_argument("--batch",choices=("baseline","preference","acknowledgment","timing"),default="baseline")
    args = parser.parse_args()
    try:
        execute(args)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError, httpx.HTTPError) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
