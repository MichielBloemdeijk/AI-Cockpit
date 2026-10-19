"""Isolated hosts, speech rendering, voice runs and paid-request accounting.

Every run gets its own database and folders under data/selftest. Recorded
artifacts are retained on failure, never deleted.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
import wave

import httpx

from app.config import settings

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "data" / "selftest"
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
RENDERER = ROOT / "android" / "scripts" / "generate-audio-corpus.ps1"
WAKE = ROOT / "data" / "audio-corpus-api-v1" / "ready01-wake.wav"
PROVIDER_FAILURES = ("insufficient_quota", "billing", "invalid_api_key", "rate_limit_exceeded",
    "authentication_error", "quota_exhausted")


class PaidFailure(RuntimeError):
    """Billing, quota, auth or evidence-integrity failure: stop paid work."""


def write(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def file_identity(path: Path) -> dict:
    return {"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest()}


def utterance_id(text: str) -> str:
    # Drivers locate speech by this name; keep it in sync with the e2e and Android drivers.
    return sha256(text.encode()).hexdigest()[:16]


def run_root(name: str) -> Path:
    path = (BASE / name).resolve()
    if not path.is_relative_to(BASE.resolve()) or path == BASE.resolve():
        raise ValueError("Use a dedicated directory under data/selftest")
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_android_helper():
    path = ROOT / "android" / "scripts" / "verify-screen-workflow.py"
    spec = importlib.util.spec_from_file_location("verify_screen_workflow", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render_speech(texts: list[str], folder: Path) -> Path:
    """Render caller speech offline (Windows SAPI); reuses already rendered files."""
    folder.mkdir(parents=True, exist_ok=True)
    missing = [t for t in sorted(set(texts)) if not (folder / f"{utterance_id(t)}-command.wav").exists()]
    if missing:
        # The renderer requires a fresh directory; render there, then keep the files.
        batch = folder.parent / f"{folder.name}-render-{secrets.token_hex(4)}"
        manifest = folder.parent / f"{batch.name}.json"
        write(manifest, {"wake": "Hey Jarvis", "cases": [{"id": utterance_id(t), "text": t} for t in missing]})
        try:
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(RENDERER),
                "-OutputDirectory", str(batch), "-ManifestPath", str(manifest)], check=True, timeout=300,
                stdout=subprocess.DEVNULL)
            for rendered in batch.glob("*.wav"):
                rendered.replace(folder / rendered.name)
        finally:
            manifest.unlink(missing_ok=True)
            for leftover in batch.glob("*"):
                leftover.unlink()
            if batch.exists():
                batch.rmdir()
    for text in texts:
        with wave.open(str(folder / f"{utterance_id(text)}-command.wav")) as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000) or audio.getnframes() < 1600:
                raise ValueError(f"Invalid rendered speech for: {text}")
    return folder


def wake_audio(speech_folder: Path) -> Path:
    if WAKE.is_file():
        return WAKE
    rendered = sorted(speech_folder.glob("*-wake.wav"))
    if not rendered:
        raise FileNotFoundError("No wake recording available")
    return rendered[0]


def unused_port() -> int:
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def wait_ready(url: str, process, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=2, trust_env=False) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Owned self-test service exited during startup; inspect its retained log")
            try:
                if client.get(url).status_code < 400:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(.2)
    raise TimeoutError("Owned self-test service did not become ready")


def service_environment(folder: Path, backend_port: int, *, real_worker: bool = False, overrides: dict | None = None) -> dict:
    env = {**os.environ, "JARVIS_EVAL_ROOT": str(folder), "JARVIS_EVAL_TOKEN": secrets.token_urlsafe(32),
        "JARVIS_EVAL_REAL_WORKER": "1" if real_worker else "", "JARVIS_ACCESS_MODE": "personal", "AUTH_PASSWORD": "",
        "DATABASE_URL": f"sqlite+aiosqlite:///{(folder / 'application.db').as_posix()}",
        "SESSION_SECRET_KEY": secrets.token_urlsafe(32), "SESSION_COOKIE_SECURE": "false",
        "TASK_POOL_ENABLED": "true" if real_worker else "false",
        "TASK_ORCHESTRATION_ENABLED": str(settings.task_orchestration_enabled).lower(),
        "TASK_ORCHESTRATION_LIVE_ENABLED": str(settings.task_orchestration_live_enabled).lower(), "LOCAL_LLM_ENABLED": "false",
        "OPENAI_LIVE_DIAGNOSTICS_DIR": str(folder / "diagnostics"), "OPENAI_LIVE_IDLE_TIMEOUT_SECONDS": "60",
        "CONVERSATION_WORKSPACES_DIR": str(folder / "conversations"), "TASK_WORKSPACES_DIR": str(folder / "tasks"),
        "TASK_CONTEXT_BLOBS_DIR": str(folder / "context"), "VERIFIER_GENERATED_APPS_DIR": str(folder / "verifier"),
        "CODEX_PROFILES_DIR": str(folder / "profiles"),
        "TASK_ORCHESTRATION_CONTEXT_DIR": str(folder / "background-context"),
        "TASK_ORCHESTRATION_PROFILE": str(folder / "background-profile"),
        "AI_COCKPIT_BACKEND_URL": f"http://127.0.0.1:{backend_port}",
        "AI_COCKPIT_API_PROXY_URL": f"http://127.0.0.1:{backend_port}",
        "AI_COCKPIT_NEXT_DIST_DIR": ".next-selftest", "AI_COCKPIT_TSCONFIG_PATH": ".next-selftest-tsconfig.json",
        "NEXT_PUBLIC_JARVIS_WAKE_MODEL_URL": "/jarvis-wake/models/jarvis.onnx"}
    env.update(overrides or {})
    return env


class RequestLedger(list):
    """Durable record of paid requests made by the harness itself (not Jarvis)."""

    def __init__(self, path: Path):
        super().__init__(json.loads(path.read_text(encoding="utf-8")) if path.exists() else [])
        self.path = path

    def persist(self) -> None:
        write(self.path, list(self))

    def count(self, purpose: str) -> int:
        return sum(row["purpose"] == purpose for row in self)


def api_request(client: httpx.Client, endpoint: str, ledger: RequestLedger, *, data=None, files=None, payload=None) -> dict:
    row = {"purpose": endpoint, "started_at": datetime.now(timezone.utc).isoformat()}
    ledger.append(row)
    ledger.persist()
    try:
        response = client.post(settings.openai_live_base_url.rstrip("/") + endpoint,
            headers={"Authorization": "Bearer " + settings.openai_api_key}, data=data, files=files, json=payload)
        row.update(status=response.status_code, request_id=response.headers.get("x-request-id"))
        if response.status_code != 200:
            try:
                error = response.json().get("error", {})
                row["error"] = {k: error.get(k) for k in ("type", "code")}
            except ValueError:
                row["error"] = {"type": "non_json_error"}
            raise PaidFailure(f"Paid {endpoint} failed with HTTP {response.status_code}: {row['error']}; no automatic retry")
        result = response.json()
        row["usage"] = result.get("usage")
        return result
    finally:
        row["finished_at"] = datetime.now(timezone.utc).isoformat()
        ledger.persist()


def provider_failure(text: str) -> bool:
    text = text.lower()
    return any(word in text for word in PROVIDER_FAILURES) or "http 401" in text or "http 429" in text


def drain_observer(database: Path, *, timeout: float = 30, clock=time.monotonic, sleep=time.sleep, inspect=None) -> dict:
    """Wait for background bookkeeping to settle after voice closes; never retries inference."""
    def snapshot():
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=.2) as db:
            db.row_factory = sqlite3.Row
            jobs = [dict(r) for r in db.execute("SELECT id,status,completed_at FROM conversation_observations ORDER BY created_at,id")]
            presentations = [dict(r) for r in db.execute(
                "SELECT id,source_task_event_id,state,handled_by,handled_at,handled_event_id FROM task_presentations")]
        return {"jobs": jobs, "presentations": presentations}
    inspect = inspect or snapshot
    start = clock(); stable_since = None; previous = None
    while True:
        try:
            state = inspect()
        except (sqlite3.Error, OSError, ValueError) as exc:
            return {"error": type(exc).__name__, "timed_out": False, "elapsed_ms": (clock() - start) * 1000}
        identity = [(j["id"], j["status"], j.get("completed_at")) for j in state["jobs"]]
        busy = any(j["status"] in {"pending", "running"} for j in state["jobs"])
        if busy or identity != previous:
            stable_since = clock()
        previous = identity
        # The bookkeeper may still create a job after its ~6 s reaction window.
        settled = not busy and clock() - start >= 10 and clock() - stable_since >= 10
        if settled or clock() - start >= timeout:
            return {**state, "timed_out": not settled, "elapsed_ms": (clock() - start) * 1000}
        sleep(min(.2, max(0, timeout - (clock() - start))))


def run_case(output: Path, scenario: dict, speech_folder: Path, *, android: bool = False,
             env_overrides: dict | None = None) -> Path:
    """Run one scenario against an isolated host through the real client path."""
    suffix = "android" if android else "browser"
    folder = output / f"{scenario['id']}-{suffix}"
    if folder.exists():
        raise FileExistsError(f"{folder} already exists; runs are never overwritten")
    folder.mkdir()
    helper = load_android_helper()
    backend_port, frontend_port = unused_port(), unused_port()
    real_worker = bool(scenario.get("real_worker"))
    env = service_environment(folder, backend_port, real_worker=real_worker, overrides=env_overrides)
    spec = {"run_id": folder.name, "case": scenario, "profile": "production", "timing": "production",
        "opening": scenario.get("opening", ""), "corpus_directory": str(speech_folder),
        "wake_audio": str(wake_audio(speech_folder)), "adaptive": scenario.get("adaptive"),
        "backend_url": f"http://127.0.0.1:{backend_port}", "frontend_url": f"http://127.0.0.1:{frontend_port}",
        "python": sys.executable, "backend_directory": str(BACKEND), "real_worker": real_worker}
    tsconfig = FRONTEND / ".next-selftest-tsconfig.json"
    write(tsconfig, {"extends": "./tsconfig.json", "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", "**/*.mts",
        ".next-selftest/types/**/*.ts", ".next-selftest/dev/types/**/*.ts"],
        "exclude": ["node_modules", ".next", ".next-live-smoke"]})
    write(folder / "spec.json", spec)
    env["JARVIS_EVAL_SPEC"] = str(folder / "spec.json")
    backend_process = frontend_process = None
    with (folder / "backend.log").open("w", encoding="utf-8") as backend_log, \
            (folder / "frontend.log").open("w", encoding="utf-8") as frontend_log:
        try:
            backend_process = helper.start_owned_process([sys.executable, "-m", "uvicorn", "app.selftest.host:app",
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
                    process.wait(timeout=240 + int(scenario.get("observation_tail_ms", 0)) / 1000)
                finally:
                    helper.stop_tree(process)
        finally:
            if android:
                # The emulator app is outside the driver's process tree. Close its recorder.
                serial = helper.find_emulator()
                if serial:
                    for extra in (("--ez", "finish_recording", "true"), ("--ez", "clear", "true")):
                        try:
                            helper.adb(serial, "shell", "am", "broadcast", "-n",
                                "nl.sander.aicockpit.jarvis/.assistant.AudioAcceptanceReceiver", *extra, timeout=10)
                        except subprocess.SubprocessError:
                            pass
            try:
                if backend_process and backend_process.poll() is None:
                    try:
                        with httpx.Client(base_url=spec["backend_url"], timeout=10, trust_env=False) as client:
                            live = client.get("/api/jarvis/state").json().get("session")
                            if live:
                                client.delete(f"/api/jarvis/live/sessions/{live['id']}", params={"generation": live["generation"]})
                    except (httpx.HTTPError, ValueError, KeyError):
                        pass
                    write(folder / "observer-drain.json", drain_observer(folder / "application.db"))
            finally:
                helper.stop_tree(frontend_process)
                helper.stop_tree(backend_process)
    evidence_path = folder / "evidence.json"
    if not evidence_path.exists():
        write(evidence_path, {"case_id": scenario["id"], "error": "driver_produced_no_evidence"})
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    log_text = (folder / "backend.log").read_text(encoding="utf-8", errors="replace")
    if provider_failure(json.dumps(evidence.get("state", {}))) or provider_failure(log_text):
        evidence["paid_failure"] = "Voice provider billing/quota/auth failure"
        write(evidence_path, evidence)
    return folder


def connections_used(folder: Path) -> int:
    events = folder / "events.jsonl"
    if not events.exists():
        return 0
    return sum(json.loads(line).get("event", {}).get("type") == "provider.connect.request"
               for line in events.read_text(encoding="utf-8").splitlines())


def pack_voiced(source: Path, destination: Path, segments: list[tuple[float, float]], *, pad: float = .25,
                gap: float = .6) -> list[dict]:
    """Concatenate voiced segments (silence removed) and return the time map back to the original."""
    with wave.open(str(source)) as audio:
        params, rate = audio.getparams(), audio.getframerate()
        frames = audio.readframes(audio.getnframes())
    width = params.sampwidth * params.nchannels
    silence = b"\x00" * int(gap * rate) * width
    out, mapping, cursor = bytearray(), [], 0.0
    for start, end in segments:
        a, b = max(0.0, start - pad), end + pad
        chunk = frames[int(a * rate) * width:int(b * rate) * width]
        mapping.append({"packed_start": cursor, "original_start": a, "duration": len(chunk) / width / rate})
        out += chunk + silence
        cursor += len(chunk) / width / rate + gap
    with wave.open(str(destination), "wb") as packed:
        packed.setparams(params)
        packed.writeframes(bytes(out))
    return mapping


def unpack_time(t: float, mapping: list[dict]) -> float | None:
    for m in mapping:
        if m["packed_start"] <= t <= m["packed_start"] + m["duration"]:
            return m["original_start"] + (t - m["packed_start"])
    return None


def transcribe(folder: Path, ledger: RequestLedger) -> dict:
    """Independent word-level transcript of what the client actually played (whisper-1).

    Only voiced audio is sent (silence removed), because Whisper invents words over
    long silences; word times are mapped back to the original recording.
    """
    from app.selftest.review import voiced
    path = folder / "assistant-transcription.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    segments = voiced(folder / "assistant.wav")
    if not segments:
        result = {"words": [], "text": "", "note": "no assistant audio"}
        write(path, result)
        return result
    packed = folder / "assistant-voiced.wav"
    mapping = pack_voiced(folder / "assistant.wav", packed, segments)
    with httpx.Client(timeout=90, trust_env=False) as client, packed.open("rb") as audio:
        raw = api_request(client, "/audio/transcriptions", ledger,
            data={"model": "whisper-1", "response_format": "verbose_json", "language": "en",
                "timestamp_granularities[]": "word"}, files={"file": ("assistant.wav", audio, "audio/wav")})
    words = []
    for word in raw.get("words", []):
        start, end = unpack_time(word["start"], mapping), unpack_time(word["end"], mapping)
        if start is None or end is None:
            continue  # falls in inserted silence: not real speech
        words.append({**word, "start": round(start, 2), "end": round(end, 2)})
    result = {"text": raw.get("text"), "words": words, "packing": mapping}
    write(path, result)
    return result
