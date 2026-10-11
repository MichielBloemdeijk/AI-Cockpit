"""Unattended native screenshot -> Live admission -> Codex -> result fixture.

Run with ``cd backend && uv run python ../android/scripts/verify-screen-workflow.py``.
This starts a disposable, passwordless loopback backend reachable through the
emulator's host-loopback alias. It leaves its evidence in a temp folder.
The assistant image must come from Android's onHandleScreenshot callback.
Select another installed phone image with JARVIS_TEST_AVD and JARVIS_TEST_IMAGE.
Add --orchestration for opt-in billed M3 decisions, original-question retention,
mobile history reconnect and metadata telemetry on the same disposable backend.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from uuid import uuid4
from xml.etree import ElementTree

import httpx
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
ANDROID = ROOT / "android"
BACKEND = ROOT / "backend"
SDK = Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk"
ADB = SDK / "platform-tools" / "adb.exe"
EMULATOR = SDK / "emulator" / "emulator.exe"
AVD_MANAGER = SDK / "cmdline-tools" / "latest" / "bin" / "avdmanager.bat"
AVD_NAME = os.environ.get("JARVIS_TEST_AVD", "JarvisM3Api37")
IMAGE_ID = os.environ.get("JARVIS_TEST_IMAGE", "system-images;android-37.0;google_apis;x86_64")
PACKAGE = "nl.sander.aicockpit.jarvis"
FIXTURE_PACKAGE = "nl.sander.aicockpit.screenfixture"


def command(*args: str | Path, capture: bool = True, timeout: float = 120) -> str:
    result = subprocess.run([str(a) for a in args], cwd=ROOT, text=True,
                            encoding="utf-8", errors="replace",
                            capture_output=capture, timeout=timeout, check=True)
    return result.stdout.strip() if capture else ""


def adb(serial: str, *args: str, timeout: float = 120) -> str:
    return command(ADB, "-s", serial, *args, timeout=timeout)


class FixtureFailed(RuntimeError):
    pass


def wait_for(label: str, check, seconds: float = 90):
    deadline = time.monotonic() + seconds
    last_error = None
    while time.monotonic() < deadline:
        try:
            value = check()
            if isinstance(value, ElementTree.Element) or value:
                return value
        except FixtureFailed:
            raise
        except Exception as exc:  # Startup/readiness races are expected.
            last_error = exc
        time.sleep(0.5)
    raise TimeoutError(f"Timed out waiting for {label}: {last_error}")


def find_emulator() -> str | None:
    for line in command(ADB, "devices").splitlines()[1:]:
        if not line.endswith("\tdevice"):
            continue
        serial = line.split()[0]
        if not serial.startswith("emulator-"):
            continue
        if AVD_NAME in adb(serial, "emu", "avd", "name"):
            return serial
    return None


def ensure_emulator(*, dns_servers: str | None = None) -> tuple[str, subprocess.Popen | None]:
    serial = find_emulator()
    if serial:
        return serial, None
    available = command(EMULATOR, "-list-avds").splitlines()
    if AVD_NAME not in available:
        if not SDK.joinpath(*IMAGE_ID.split(";")).is_dir():
            raise RuntimeError(f"Install the full Android image {IMAGE_ID} first")
        command(AVD_MANAGER, "create", "avd", "-n", AVD_NAME, "-k", IMAGE_ID,
                "-d", "pixel_6", timeout=60)
    occupied = {int(line.split()[0].split("-")[1]) for line in
                command(ADB, "devices").splitlines()[1:]
                if line.startswith("emulator-")}
    port = next(p for p in range(5554, 5600, 2) if p not in occupied)
    serial = f"emulator-{port}"
    emulator_args = [str(EMULATOR), "-avd", AVD_NAME,
                     "-port", str(port), "-no-window", "-no-audio",
                     "-gpu", "swiftshader_indirect", "-no-snapshot-load"]
    if dns_servers:
        emulator_args.extend(["-dns-server", dns_servers])
    if IMAGE_ID.startswith("system-images;android-37"):
        emulator_args.extend(["-memory", "4096"])
    process = start_owned_process(emulator_args,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_for("emulator boot", lambda: adb(serial, "shell", "getprop", "sys.boot_completed") == "1",
             seconds=180)
    return serial, process


def unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_owned_process(args: list[str], *, cwd: Path = BACKEND,
                        env: dict[str, str] | None = None, **streams) -> subprocess.Popen:
    """Use the existing Windows Job Object host for harness-owned descendants."""
    environment = {**os.environ, **(env or {})}
    environment["PYTHONPATH"] = str(BACKEND) + os.pathsep + environment.get("PYTHONPATH", "")
    process = subprocess.Popen(
        [sys.executable, "-m", "app.jarvis_tasks.acceptance_command_host",
         "--parent-pid", str(os.getpid()), "--", *args],
        cwd=cwd, env=environment, creationflags=subprocess.CREATE_NO_WINDOW, **streams)
    process._jarvis_job_contained = True
    return process


def start_service(args: list[str], env: dict[str, str], log_path: Path) -> subprocess.Popen:
    log = log_path.open("wb")
    try:
        process = start_owned_process([sys.executable, *args], cwd=BACKEND, env=env,
                                      stdout=log, stderr=subprocess.STDOUT)
    finally:
        log.close()
    return process


def start_frontend(port: int, backend_url: str, evidence: Path) -> subprocess.Popen:
    node = shutil.which("node")
    next_bin = ROOT / "frontend" / "node_modules" / "next" / "dist" / "bin" / "next"
    if node is None or not next_bin.is_file():
        raise RuntimeError("Install frontend dependencies before running the mobile result fixture")
    env = {**os.environ, "AI_COCKPIT_API_PROXY_URL": backend_url,
           "AI_COCKPIT_BACKEND_URL": backend_url,
           "AI_COCKPIT_NEXT_DIST_DIR": ".next-jarvis-agent-test-m2"}
    log = (evidence / "frontend.log").open("wb")
    try:
        process = start_owned_process([node, str(next_bin), "dev", "--hostname", "127.0.0.1",
                                       "--port", str(port)], cwd=ROOT / "frontend", env=env,
                                      stdout=log, stderr=subprocess.STDOUT)
    finally:
        log.close()
    return process


def stop_tree(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    # Only the exact process tree started by this harness is targeted.
    if getattr(process, "_jarvis_job_contained", False):
        # Terminate the original process handle; closing its non-inheritable
        # job handle also terminates QEMU/worker children after launcher exits.
        process.kill()
    else:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, check=False)
    process.wait(timeout=15)


def iso_seconds(earlier: str, later: str) -> float:
    return (datetime.fromisoformat(later) - datetime.fromisoformat(earlier)).total_seconds()


def verify_evidence(db_path: Path, task_id: str, activation_id: str,
                    capture_id: str, client: httpx.Client, definition: str = "screen_document@1") -> dict:
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        capture = db.execute("SELECT * FROM task_capture_reports WHERE capture_id=?",
                             (capture_id,)).fetchone()
        assert capture and capture["status"] == "captured" and capture["revision"] >= 2
        assert capture["activation_id"] == activation_id
        request = db.execute("SELECT * FROM task_context_requests WHERE task_id=?",
                             (task_id,)).fetchone()
        assert request and request["state"] == "ready"
        for key in ("activation_id", "capture_id", "device_id", "session_id", "generation"):
            assert request[key] == capture[key], f"{key} did not correlate"
        assert request["capture_revision"] <= capture["revision"]
        blob = db.execute("SELECT * FROM task_context_blobs WHERE id=?",
                          (request["blob_id"],)).fetchone()
        assert blob and blob["mime_type"] == "image/jpeg"
        image_bytes = Path(blob["storage_path"]).read_bytes()
        with Image.open(BytesIO(image_bytes)) as image:
            assert image.format == "JPEG" and image.width >= 500
            red, green, blue = image.convert("RGB").getpixel((image.width // 2, image.height * 3 // 4))
            assert blue > 140 and blue > red * 2 and blue > green * 2, "Fixture blue panel not captured"
        page = db.execute("SELECT * FROM result_pages WHERE task_id=?", (task_id,)).fetchone()
        assert page and page["status"] == "ready" and page["revision_number"] == 1
        url = f"/results/{page['public_id']}"
        notification = db.execute("SELECT * FROM notification_outbox WHERE task_id=? AND event_kind='task.completed'",
                                  (task_id,)).fetchone()
        assert notification and notification["deep_link"] == url
        assert notification["status"] == "delivered"
        task = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        attempt = db.execute("SELECT * FROM task_attempts WHERE task_id=?", (task_id,)).fetchone()
        assert task and task["status"] == "completed" and attempt
        live = db.execute("SELECT * FROM live_sessions WHERE id=?",
                          (request["session_id"],)).fetchone()
        shell_call = db.execute("SELECT created_at FROM live_session_events WHERE session_id=? "
                                "AND event_type='provider.shell_call' ORDER BY sequence DESC LIMIT 1",
                                (request["session_id"],)).fetchone()
        assert live and live["ready_at"] and shell_call
        tool_event = db.execute("SELECT payload_json FROM live_session_events WHERE session_id=? "
                                "AND event_type='brain.tool.result' ORDER BY sequence DESC LIMIT 1",
                                (request["session_id"],)).fetchone()
        timing = json.loads(tool_event["payload_json"])["output"]["timing_ms"] if tool_event else None
        assert timing and timing["ready_call_to_admission"] >= 0
        result = client.get(f"/api/results/{page['public_id']}")
        result.raise_for_status()
        body = result.json()
        assert body["url"] == url and body["active_execution_run_id"] == request["execution_run_id"]
        text = json.dumps(body["content"]).upper()
        research_usage = []
        if definition == "screen_document@1":
            assert "FIXTURE-A-VISIBLE-MARKER" in text and "BLUE" in text
        else:
            assert "TOMATO" in text and "PASTA" in text
            assert body["result_kind"] == ("recipe.v1" if definition.startswith("recipe") else "document.v1")
            if definition.startswith("recipe"):
                assert body["content"]["ingredients"] and body["content"]["instructions"] and body["content"]["sources"]
            events = db.execute("SELECT sequence, payload_json FROM task_events WHERE task_id=? "
                "AND event_type IN ('agent.native.item','agent.native.subagent.item')", (task_id,)).fetchall()
            research_usage = [{"source_event_sequence": row["sequence"], "item": json.loads(row["payload_json"])["item"]}
                for row in events if json.loads(row["payload_json"]).get("item", {}).get("type") == "webSearch"]
            assert research_usage and body["content"]["source_event_sequences"]
        return {
            "task_id": task_id, "activation_id": activation_id, "capture_id": capture_id,
            "definition": definition, "result_kind": body["result_kind"], "search_usage": research_usage,
            "active_execution_s": iso_seconds(attempt["started_at"], attempt["ended_at"]),
            "admission_to_ready_s": iso_seconds(task["created_at"], task["finished_at"]),
            "execution_run_id": request["execution_run_id"], "result_url": url,
            "image_sha256": blob["sha256"], "image_size": [blob["width"], blob["height"]],
            "fake_live_cold_to_ready_s": iso_seconds(live["created_at"], live["ready_at"]),
            "backend_ready_call_to_admission_ms": timing["ready_call_to_admission"],
            "backend_capture_preflight_ms": timing["preflight"],
            "capture_to_admission_s": iso_seconds(capture["captured_at"], task["created_at"]),
            "admission_to_upload_s": iso_seconds(task["created_at"], request["fulfilled_at"]),
            "queue_to_worker_start_s": iso_seconds(task["queue_entered_at"], attempt["started_at"]),
        }


def ui_tree(serial: str, name: str) -> str:
    path = f"/sdcard/{name}.xml"
    adb(serial, "shell", "uiautomator", "dump", path)
    return adb(serial, "shell", "cat", path)


def tap_node(serial: str, node: ElementTree.Element) -> None:
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds", ""))
    assert match, "UI node has no tappable bounds"
    x1, y1, x2, y2 = map(int, match.groups())
    adb(serial, "shell", "input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2))


def verify_local_network_permission(serial: str, server_url: str, client: httpx.Client,
                                    evidence: Path) -> None:
    """Exercise the real setup permission denial and allow-to-pair continuation."""
    permission = "android.permission.ACCESS_LOCAL_NETWORK"
    adb(serial, "shell", "am", "force-stop", PACKAGE)
    adb(serial, "shell", "pm", "revoke", PACKAGE, permission)
    adb(serial, "shell", "pm", "clear-permission-flags", PACKAGE, permission,
        "user-set", "user-fixed")
    response = client.post("/api/jarvis/devices/pairing-code")
    response.raise_for_status()
    code = response.json()["code"]
    adb(serial, "shell", "am", "start", "-W", "-n", f"{PACKAGE}/.MainActivity")

    def nodes():
        return list(ElementTree.fromstring(ui_tree(serial, "jarvis-network-permission")).iter("node"))

    fields = wait_for("setup fields", lambda: [n for n in nodes()
                      if n.get("class") == "android.widget.EditText"] or None, seconds=20)
    assert len(fields) >= 2
    for field, value in zip(fields[:2], (server_url, code)):
        tap_node(serial, field)
        adb(serial, "shell", "input", "keycombination", "113", "29")  # Ctrl+A
        adb(serial, "shell", "input", "text", value)
    adb(serial, "shell", "input", "keyevent", "4")  # Hide keyboard.

    def tap_pair():
        node = wait_for("Pair button", lambda: next((n for n in nodes()
                        if n.get("text") in {"Pair", "Pair again"}), None), seconds=20)
        tap_node(serial, node)

    def permission_button(suffix: str):
        return next((n for n in nodes() if n.get("resource-id", "").endswith(suffix)), None)

    tap_pair()
    tap_node(serial, wait_for("local-network permission denial button",
                             lambda: permission_button("permission_deny_button"), seconds=20))
    denied = wait_for("permission denial explanation", lambda: next((n for n in nodes()
                      if "Local network access is required" in n.get("text", "")), None), seconds=20)
    assert denied is not None
    (evidence / "android-network-denied.xml").write_text(
        ui_tree(serial, "jarvis-network-denied"), encoding="utf-8")
    tap_pair()
    tap_node(serial, wait_for("local-network permission allow button",
                             lambda: permission_button("permission_allow_button"), seconds=20))
    wait_for("pairing after permission grant", lambda: any(n.get("text") == "Paired securely"
             for n in nodes()), seconds=30)
    assert re.search(re.escape(permission) + r": granted=true\b",
                     adb(serial, "shell", "dumpsys", "package", PACKAGE))
    (evidence / "android-network-paired.xml").write_text(
        ui_tree(serial, "jarvis-network-paired"), encoding="utf-8")


def tap_notification(serial: str) -> None:
    # The assistant sheet is a system overlay; dismiss it before opening the
    # notification shade, just as the user would after the voice exchange.
    adb(serial, "shell", "input", "keyevent", "4")
    adb(serial, "shell", "cmd", "statusbar", "expand-notifications")

    def title_bounds():
        tree = ui_tree(serial, "jarvis-m2-notification")
        for node in ElementTree.fromstring(tree).iter("node"):
            if node.get("text") == "Task completed":
                match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]",
                                     node.get("bounds", ""))
                if match:
                    return tuple(map(int, match.groups()))
        return None

    x1, y1, x2, y2 = wait_for("Android task-completed notification", title_bounds, seconds=20)
    adb(serial, "shell", "input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2))


def verify_orchestration(client: httpx.Client, db_path: Path, serial: str, evidence: Path) -> dict:
    """No worker launcher here: only the restricted background decision host."""
    scenarios = [
        ("answer", "M3 emulator report: the environment is explicitly staging.", "Which environment should I use?"),
        ("presentation", "M3 emulator report: set this task's update policy to follow_progress, as explicitly requested. "
         "Ask me which environment to use; I have not chosen and you must not guess.", "Which environment should I use?"),
    ]
    results = []
    for name, goal, question_text in scenarios:
        response = client.post("/api/tasks", json={"goal": goal, "idempotency_key": f"m3-mobile-{name}"})
        response.raise_for_status()
        task_id = response.json()["id"]
        response = client.post(f"/api/tasks/{task_id}/questions", json={
            "question": question_text, "idempotency_key": f"m3-mobile-question-{name}",
        })
        response.raise_for_status()
        question_id = response.json()["id"]

        def decision_finished():
            with sqlite3.connect(db_path) as db:
                db.row_factory = sqlite3.Row
                row = db.execute("SELECT * FROM orchestration_decisions WHERE task_id=? ORDER BY created_at DESC LIMIT 1",
                                 (task_id,)).fetchone()
                if row and row["status"] in {"deferred", "failed", "obsolete"}:
                    raise FixtureFailed(f"M3 {name} decision stopped: {row['status']} / {row['last_error']}")
                return dict(row) if row and row["status"] == "completed" else None

        decision = wait_for(f"M3 native {name} decision", decision_finished, seconds=150)
        response = client.get(f"/api/tasks/{task_id}")
        response.raise_for_status()
        before = response.json()
        question = next(q for q in before["questions"] if q["id"] == question_id)
        if name == "answer":
            assert "staging" in (question["answer"] or "").lower()
        else:
            assert question["state"] == "asked" and question["answer"] is None
            assert before["presentation_policy"] == "follow_progress"
            assert any(item["state"] == "pending" for item in before["presentations"])
        binding = json.loads(decision["native_binding_json"])
        assert len(binding["tools"]) == 7 and binding["turn_id"]

        def mobile_history():
            tree = ui_tree(serial, f"jarvis-m3-{name}")
            visible = ('class="android.webkit.WebView"' in tree and task_id in tree
                       and question_text in tree)
            if visible:
                (evidence / f"android-m3-{name}-history.xml").write_text(tree, encoding="utf-8")
            return visible

        for _ in range(2):
            adb(serial, "shell", "am", "force-stop", PACKAGE)
            adb(serial, "shell", "am", "start", "-f", "0x10008000", "-n",
                f"{PACKAGE}/.assistant.CaptureFixtureActivity", "--es", "open_result_path", f"/tasks/{task_id}")
            wait_for(f"M3 {name} mobile question history", mobile_history, seconds=45)
        response = client.get(f"/api/tasks/{task_id}")
        response.raise_for_status()
        after = response.json()
        assert [r["id"] for r in after["runs"]] == [r["id"] for r in before["runs"]]
        assert [a["id"] for a in after["attempts"]] == [a["id"] for a in before["attempts"]]
        assert after["questions"] == before["questions"], "Reading/reconnecting must not answer a question"
        with sqlite3.connect(db_path) as db:
            assert db.execute("SELECT count(*) FROM orchestration_decisions WHERE task_id=?", (task_id,)).fetchone()[0] == 1
            call_count = db.execute("SELECT count(*) FROM orchestration_metrics WHERE decision_id=? AND kind='native_turn'",
                                   (decision["id"],)).fetchone()[0]
            assert call_count == 1, "Reconnect must not rerun a native decision"
        results.append({"scenario": name, "task_id": task_id, "question_id": question_id,
                        "decision_status": decision["status"], "native_calls": call_count,
                        "question_state": question["state"], "mobile_history_and_reconnect": "pass"})
    response = client.get("/api/orchestration")
    response.raise_for_status()
    diagnostics = response.json()
    assert diagnostics["telemetry"]["counts"]["factual_forwarding"] >= 1
    assert diagnostics["telemetry"]["native_usage"]["observed_turns"] == 2
    assert diagnostics["telemetry"]["native_usage"]["totals"]["totalTokens"] > 0
    return {"status": "pass", "scenarios": results, "telemetry": diagnostics["telemetry"],
            "scope": "Isolated API profile; fake Live admission; actual Android WebView; no physical/audio sign-off"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--orchestration", action="store_true", help="Opt-in billed restricted M3 decisions")
    parser.add_argument("--workflow", default="screen_document@1",
                        choices=["screen_document@1", "recipe_from_screen@1", "research_screen@1"],
                        help="M4 examples invoke separately billed research")
    parser.add_argument("--report", type=Path, help="Copy the redacted machine-readable report here")
    args = parser.parse_args()
    if os.name != "nt" or not ADB.is_file() or not EMULATOR.is_file():
        raise RuntimeError("This harness currently requires the Windows Android SDK")
    evidence = Path(tempfile.mkdtemp(prefix="jarvis-m2-screen-"))
    print(f"Evidence: {evidence}", flush=True)
    server = worker = frontend = booted_emulator = None
    try:
        subprocess.run([str(ANDROID / "gradlew.bat"), ":app:assembleDebug",
                        ":screen-fixture:assembleDebug"], cwd=ANDROID, check=True, timeout=300)
        serial, booted_emulator = ensure_emulator()
        print(f"Emulator: {serial}", flush=True)
        adb(serial, "install", "-r", str(ANDROID / "app/build/outputs/apk/debug/app-debug.apk"))
        adb(serial, "install", "-r", str(ANDROID / "screen-fixture/build/outputs/apk/debug/screen-fixture-debug.apk"))
        adb(serial, "shell", "pm", "grant", PACKAGE, "android.permission.POST_NOTIFICATIONS")
        if int(adb(serial, "shell", "getprop", "ro.build.version.sdk")) >= 37:
            adb(serial, "shell", "pm", "grant", PACKAGE, "android.permission.ACCESS_LOCAL_NETWORK")
        def assistant_ready():
            # First-boot provisioning and role changes can finish asynchronously.
            adb(serial, "shell", "cmd", "role", "add-role-holder", "android.app.role.ASSISTANT", PACKAGE)
            return "mComponent=" + PACKAGE in adb(serial, "shell", "dumpsys", "voiceinteraction")

        wait_for("Jarvis assistant activation", assistant_ready, seconds=45)
        # The debug fixture explicitly requests both assist context and screenshots.
        adb(serial, "shell", "settings", "put", "secure", "assist_structure_enabled", "1")
        adb(serial, "shell", "settings", "put", "secure", "assist_screenshot_enabled", "1")
        assert adb(serial, "shell", "settings", "get", "secure", "assist_screenshot_enabled") == "1"
        # Warm the separate fixture before the debug driver switches to it.
        adb(serial, "shell", "am", "start", "-W", "-n", f"{FIXTURE_PACKAGE}/.MarkerActivity")

        from app.services.task_workflows import load_workflow
        workflow = load_workflow(args.workflow)
        port = unused_port()
        db_path = evidence / "fixture.db"
        env = os.environ.copy()
        env.update({
            "DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}",
            "AUTH_PASSWORD": "", "JARVIS_ACCESS_MODE": "personal", "SESSION_SECRET_KEY": uuid4().hex,
            "TASK_EXECUTOR": "codex", "TASK_WORKFLOW_ALLOWLIST": f"{args.workflow}:{workflow.digest}",
            "TASK_RESEARCH_ENABLED": "false" if args.workflow == "screen_document@1" else "true",
            # Codex's guarded workspace sandbox grants writes under the repo,
            # not an arbitrary Windows temp directory.
            "TASK_WORKSPACES_DIR": str(BACKEND / "data" / "m2-screen-workspaces" / evidence.name),
            "TASK_CONTEXT_BLOBS_DIR": str(evidence / "blobs"),
            "CODEX_PROFILES_DIR": str(BACKEND / "data" / "m2-screen-profiles" / evidence.name),
            "CONVERSATION_WORKSPACES_DIR": str(evidence / "conversations"),
            "WORKER_MAX_BUSY": "1", "WORKER_STANDBY_COUNT": "1",
            "LOCAL_LLM_ENABLED": "false", "TASK_POOL_ENABLED": "false",
            "TASK_ORCHESTRATION_ENABLED": "false", "TASK_ORCHESTRATION_LIVE_ENABLED": "false",
        })
        if args.orchestration:
            from openai_codex import Codex
            from app.jarvis_tasks.codex_probe import _codex_config, _probe_api_key
            api_key = _probe_api_key()
            if not api_key:
                raise RuntimeError("Authorized API credential required for the opt-in M3 harness")
            profile, login_workspace = evidence / "orchestration-profile", evidence / "login-workspace"
            profile.mkdir()
            login_workspace.mkdir()
            with Codex(_codex_config(profile=profile, workspace=login_workspace)) as codex:
                codex.login_api_key(api_key)
            env.update({"TASK_ORCHESTRATION_ENABLED": "true", "TASK_ORCHESTRATION_LIVE_ENABLED": "true",
                        "TASK_ORCHESTRATION_PROFILE": str(profile),
                        "TASK_ORCHESTRATION_CONTEXT_DIR": str(evidence / "orchestration-context")})
        server = start_service(["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                "--port", str(port)], env, evidence / "server.log")
        base = f"http://127.0.0.1:{port}"
        with httpx.Client(base_url=base, timeout=10) as client:
            wait_for("test backend", lambda: client.get("/api/auth/status").status_code == 401,
                     seconds=90)
            response = client.post("/api/auth/login", json={"password": ""})
            response.raise_for_status()
            web_port = unused_port()
            frontend = start_frontend(web_port, base, evidence)
            with httpx.Client(base_url=f"http://127.0.0.1:{web_port}", timeout=10) as web_client:
                wait_for("frontend/API proxy", lambda: web_client.get("/api/auth/status").status_code == 401,
                         seconds=90)
            worker = start_service(["-m", "app.worker"],
                                   {**env, "TASK_POOL_ENABLED": "true"}, evidence / "worker.log")
            activation_id, capture_id = str(uuid4()), str(uuid4())
            android_url = f"http://10.0.2.2:{web_port}"
            api_level = int(adb(serial, "shell", "getprop", "ro.build.version.sdk"))
            if api_level >= 37:
                verify_local_network_permission(serial, android_url, client, evidence)
            adb(serial, "logcat", "-c")
            workflow_extras = [] if args.workflow == "screen_document@1" else [
                "--es", "subject", "TOMATO_PASTA_Vegetarian_menu_dish",
                "--es", "workflow_definition", args.workflow,
                "--es", "workflow_goal", "Find_a_source-backed_vegetarian_tomato_pasta_recipe" if args.workflow.startswith("recipe") else
                "Explain_the_tomato_pasta_dish_with_readable_sources"]
            adb(serial, "shell", "am", "start", "-f", "0x10008000", "-n",
                f"{PACKAGE}/.assistant.CaptureFixtureActivity",
                "--es", "server_url", android_url,
                "--es", "activation_id", activation_id,
                "--es", "capture_id", capture_id, *workflow_extras)
            if api_level >= 37:
                wait_for("fixture ready for system invocation",
                         lambda: f"system_invocation_ready={activation_id}" in
                         adb(serial, "logcat", "-d", "-s", "JarvisCaptureFixture:I", "*:S"),
                         seconds=30)
                adb(serial, "shell", "input", "keyevent", "219")  # KEYCODE_ASSIST

            def completed_task():
                response = client.get("/api/tasks")
                response.raise_for_status()
                tasks = response.json()
                assert len(tasks) <= 1, "Disposable backend has unexpected tasks"
                if not tasks:
                    return None
                task = tasks[0]
                if task["status"] in {"failed", "interrupted", "cancelled"}:
                    raise FixtureFailed(f"Fixture task ended {task['status']}: {task.get('status_reason')}")
                return task["id"] if task["status"] == "completed" else None

            task_id = wait_for("native screen workflow completion", completed_task, args.timeout)
            report = wait_for("result and notification evidence",
                              lambda: verify_evidence(db_path, task_id, activation_id, capture_id, client, args.workflow),
                              seconds=30)
            logcat = adb(serial, "logcat", "-d", "-s", "JarvisCaptureFixture:I", "*:S")
            (evidence / "android-capture.log").write_text(logcat, encoding="utf-8")
            assert "screenshot_callback=bitmap" in logcat, "Android callback did not supply pixels"

            wait_for("posted Android completion notification",
                     lambda: "Task completed" in adb(serial, "shell", "dumpsys", "notification", "--noredact"),
                     seconds=20)
            tap_notification(serial)

            def mobile_result_visible():
                tree = ui_tree(serial, "jarvis-m2-result")
                content_visible = ("FIXTURE-A-VISIBLE-MARKER" in tree and "blue panel" in tree.lower()) if args.workflow == "screen_document@1" else (
                    "tomato" in tree.lower() and "pasta" in tree.lower() and (
                        "Ingredients" in tree if args.workflow.startswith("recipe") else "document" in tree.lower()))
                visible = ('class="android.webkit.WebView"' in tree and f'package="{PACKAGE}"' in tree and content_visible)
                if visible:
                    (evidence / "android-result.xml").write_text(tree, encoding="utf-8")
                return visible

            wait_for("Android WebView result page", mobile_result_visible, seconds=45)
            # Reopen after a process death: this must not create a new task run.
            adb(serial, "shell", "am", "force-stop", PACKAGE)
            adb(serial, "shell", "am", "start", "-f", "0x10008000", "-n",
                f"{PACKAGE}/.assistant.CaptureFixtureActivity",
                "--es", "open_result_path", report["result_url"])
            wait_for("Android WebView result after reconnect", mobile_result_visible, seconds=45)
            with sqlite3.connect(db_path) as db:
                assert db.execute("SELECT count(*) FROM task_attempts WHERE task_id=?",
                                  (task_id,)).fetchone()[0] == 1, "Reconnect restarted the worker"
            if args.orchestration:
                stop_tree(worker)
                worker = None
                report["orchestration"] = verify_orchestration(client, db_path, serial, evidence)
            report["emulator"] = serial
            report["avd"] = AVD_NAME
            report["system_image"] = IMAGE_ID
            report["android_api"] = adb(serial, "shell", "getprop", "ro.build.version.sdk")
            report["local_network_permission_ui"] = "deny_then_allow_and_pair" if api_level >= 37 else "not_applicable"
            report["invocation"] = "system_assist_key" if api_level >= 37 else "debug_self_trigger"
            report["evidence_dir"] = str(evidence)
            report["mobile_url"] = android_url + report["result_url"]
            (evidence / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report, indent=2), flush=True)
    finally:
        stop_tree(worker)
        stop_tree(frontend)
        stop_tree(server)
        stop_tree(booted_emulator)


if __name__ == "__main__":
    main()
