"""Unpaid paired-browser -> native Android -> normal Chrome capture acceptance.

Uses the real bearer event stream, production capture-only assistant invocation,
Android callback and run-bound upload. No system gesture, injected pixels, live
provider, physical phone or main application database is involved.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Thread
import time
from uuid import uuid4

import httpx
from PIL import Image

spec = importlib.util.spec_from_file_location("results", Path(__file__).with_name("verify-result-platform.py"))
results = importlib.util.module_from_spec(spec)
spec.loader.exec_module(results)
screen = results.screen


class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        color = "blue" if "screen=b" not in self.path else "red"
        body = (f'<html><meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<body style="margin:0;background:{color};color:white;font-size:30px">'
                f'<h1>CHROME RECIPE {color.upper()}</h1><p>Tomato sandwich with cheese</p>'
                f'<div style="height:1600px"></div></body></html>').encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


async def fresh(session_id, generation, turn):
    from app.services.task_capture import task_capture_service
    from app.db.session import close_engine
    try:
        item = await task_capture_service.request_fresh(session_id=session_id, generation=int(generation), request_identity=turn)
        print(json.dumps({"capture_id": item.capture_id}))
    finally:
        await close_engine()


async def admit_collection(session_id, generation, turn):
    from app.db.session import session_scope, close_engine
    from app.db.tables import LiveSession
    from app.services.task_capture import task_capture_service
    from app.services.task_service import task_service
    try:
        async with session_scope() as session:
            live = await session.get(LiveSession, session_id)
        collection = await task_capture_service.preflight_collection(
            device_id=live.controller_id, session_id=live.id, generation=int(generation),
            activation_id=live.activation_id, request_identity=turn, capture_mode="fresh")
        task = await task_service.dispatch(goal="Create a recipe from this Chrome screen",
            origin_surface="jarvis_voice", idempotency_key=f"phone-browser:{turn}", execution_kind="workflow",
            execution_config={"definition": "recipe_from_screen@1"}, capture_binding=collection)
        print(json.dumps({"task_id": task.task.id, "roles": [role for role, _ in collection.captures]}))
    finally:
        await close_engine()


async def recipe_worker(task_id):
    from app.db.session import close_engine
    from app.jarvis_tasks.worker_process_probe import probe_recipe_task
    try:
        print(json.dumps(await probe_recipe_task(task_id, expected_images=2)))
    finally:
        await close_engine()


def main():
    from app.config import settings
    from app.jarvis_tasks.acceptance import candidate_identity, resolve_test_configuration
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    evidence = args.report.resolve()
    if evidence.exists() or not evidence.is_relative_to(screen.ROOT / "data"):
        raise ValueError("Use a fresh project-owned report directory")
    evidence.mkdir(parents=True)
    configuration = resolve_test_configuration("capture_privacy", paths={
        key: str(evidence / key) for key in ("codex_profiles_dir", "task_workspaces_dir", "task_context_blobs_dir", "conversation_workspaces_dir")})
    for key, value in configuration["settings"].items():
        setattr(settings, key, value)
    report = {"status": "failed", "models_called": 0, "checks": {}, "candidate": candidate_identity(), "test_configuration": configuration}
    server = emulator = serial = web = None
    try:
        serial, emulator = screen.ensure_emulator()
        assert serial.startswith("emulator-")
        for module in ("app", "screen-fixture"):
            screen.adb(serial, "install", "-r", str(screen.ANDROID / module / "build/outputs/apk/debug" / f"{module}-debug.apk"))
        screen.adb(serial, "shell", "am", "force-stop", screen.PACKAGE)
        screen.adb(serial, "shell", "pm", "clear", screen.PACKAGE)
        for permission in ("POST_NOTIFICATIONS", "ACCESS_LOCAL_NETWORK"):
            screen.adb(serial, "shell", "pm", "grant", screen.PACKAGE, f"android.permission.{permission}")
        screen.adb(serial, "shell", "cmd", "role", "add-role-holder", "android.app.role.ASSISTANT", screen.PACKAGE)
        for setting in ("assist_screenshot_enabled", "assist_structure_enabled"):
            screen.adb(serial, "shell", "settings", "put", "secure", setting, "1")
        screen.adb(serial, "shell", "input", "keyevent", "224")
        screen.adb(serial, "shell", "wm", "dismiss-keyguard")
        web = ThreadingHTTPServer(("127.0.0.1", 0), Page)
        Thread(target=web.serve_forever, daemon=True).start()
        url = f"http://10.0.2.2:{web.server_port}/recipe"
        screen.adb(serial, "shell", "am", "start", "-W", "-a", "android.intent.action.VIEW", "-d", url, "-p", "com.android.chrome")
        # Complete only Chrome's first-run UI on this disposable emulator.
        for _ in range(5):
            def chrome_nodes():
                output = screen.adb(serial, "shell", "am", "instrument", "-w",
                    f"{screen.FIXTURE_PACKAGE}/.HierarchyDumpInstrumentation", timeout=15)
                (evidence / "chrome-instrumentation.txt").write_text(output, encoding="utf-8")
                from xml.etree import ElementTree
                tree = results.parse_hierarchy(output)
                (evidence / "chrome-start.xml").write_text(tree, encoding="utf-8")
                return list(ElementTree.fromstring(tree).iter("node"))
            nodes = screen.wait_for("Chrome first-run UI", chrome_nodes, seconds=30)
            action = next((n for n in nodes if n.get("text") in ("Use without an account", "Accept & continue", "No thanks", "Got it")), None)
            if action is None:
                break
            screen.tap_node(serial, action)
            time.sleep(.5)
        screen.wait_for("Chrome recipe", lambda: results.visible(serial, evidence, "CHROME RECIPE BLUE", "chrome-recipe"), seconds=30)
        port = screen.unused_port()
        db_path = evidence / "fixture.db"
        env = {**os.environ, **{key.upper(): str(value).lower() if isinstance(value, bool) else str(value)
            for key, value in configuration["settings"].items()},
            "DATABASE_URL": f"sqlite+aiosqlite:///{Path(os.path.relpath(db_path, screen.BACKEND)).as_posix()}",
            "AUTH_PASSWORD": "", "JARVIS_ACCESS_MODE": "personal", "SESSION_SECRET_KEY": uuid4().hex,
            "OPENAI_API_KEY": "", "CODEX_API_KEY": "", "OPENROUTER_API_KEY": "", "LOCAL_LLM_ENABLED": "false"}
        server = screen.start_service(["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)], env, evidence / "server.log")
        def rows(sql, values=()):
            with sqlite3.connect(db_path) as db:
                db.row_factory = sqlite3.Row
                return [dict(r) for r in db.execute(sql, values)]
        def terminal(capture_id):
            found = rows("SELECT * FROM task_capture_reports WHERE capture_id=?", (capture_id,))
            return found[0] if found and found[0]["status"] in ("captured", "unavailable", "expired") else None
        def child(*arguments):
            value = subprocess.run([sys.executable, *arguments], cwd=screen.BACKEND, env=env,
                text=True, capture_output=True, timeout=25, check=True)
            return json.loads(value.stdout.strip().splitlines()[-1])
        def request(turn):
            return child(str(Path(__file__)), "--fresh", live["id"], str(live["generation"]), turn)["capture_id"]
        def submitted(capture_id, color):
            result = child(str(Path(__file__).with_name("verify-capture-feasibility.py")), "--admit", capture_id)
            assert result["accepted"], result
            uploaded = screen.wait_for("run-bound uploaded screen", lambda: rows(
                "SELECT b.* FROM task_context_requests r JOIN task_context_blobs b ON b.id=r.blob_id WHERE r.capture_id=? AND r.state='ready'", (capture_id,)), seconds=20)[0]
            image = Image.open(BytesIO(Path(uploaded["storage_path"]).read_bytes())).convert("RGB")
            r, g, b = image.getpixel((image.width // 2, image.height // 2))
            assert (b > 180 and r < 60 and g < 60) if color == "blue" else (r > 180 and b < 60 and g < 60), (r, g, b)
            # This upload-only probe must not occupy the recipe worker's queue.
            client.post(f"/api/tasks/{result['task_id']}/cancel", json={"idempotency_key": "upload-probe-done"}).raise_for_status()
            return sha256(Path(uploaded["storage_path"]).read_bytes()).hexdigest()
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15) as client:
            screen.wait_for("backend", lambda: client.get("/api/auth/status").status_code == 401)
            client.post("/api/auth/login", json={"password": ""}).raise_for_status()
            code = client.post("/api/jarvis/devices/pairing-code").json()["code"]
            activation = str(uuid4())
            screen.adb(serial, "logcat", "-c")
            screen.adb(serial, "shell", "am", "start", "-W", "-n", f"{screen.PACKAGE}/.assistant.CaptureFixtureActivity",
                "--es", "server_url", f"http://10.0.2.2:{port}", "--es", "pairing_code", code,
                "--es", "activation_id", activation, "--es", "chrome_url", url, "--ez", "browser_capture_probe", "true")
            live = screen.wait_for("paired browser call", lambda: rows("SELECT * FROM live_sessions WHERE activation_id=?", (activation,)), seconds=30)[0]
            initial_id = screen.wait_for("capture request", lambda: rows("SELECT capture_id FROM task_capture_reports WHERE activation_id=? AND capture_mode='activation'", (activation,)), seconds=10)[0]["capture_id"]
            initial = screen.wait_for("Chrome invocation capture", lambda: terminal(initial_id), seconds=15)
            report["invocation"] = initial
            assert initial["status"] == "captured", initial
            assert rows("SELECT id FROM task_context_blobs") == []
            first_hash = submitted(initial_id, "blue")
            report["checks"]["normal_chrome_invocation_and_run_bound_upload"] = True
            screen.adb(serial, "shell", "am", "start", "-W", "-a", "android.intent.action.VIEW", "-d", url + "?screen=b", "-p", "com.android.chrome")
            screen.wait_for("changed Chrome recipe", lambda: results.visible(serial, evidence, "CHROME RECIPE RED", "chrome-changed"), seconds=20)
            fresh_id = request("turn-1")
            latest = screen.wait_for("fresh Chrome capture", lambda: terminal(fresh_id), seconds=15)
            report["latest"] = latest
            assert latest["status"] == "captured", latest
            collection = child(str(Path(__file__)), "--collection", live["id"], str(live["generation"]), "turn-1")
            assert set(collection["roles"]) == {"invocation", "latest"}
            def both_uploaded():
                found = rows("SELECT capture_id,state FROM task_context_requests WHERE task_id=?", (collection["task_id"],))
                return found if len(found) == 2 and all(r["state"] == "ready" for r in found) else None
            uploaded_collection = screen.wait_for("both frozen images uploaded", both_uploaded, seconds=20)
            assert {r["capture_id"] for r in uploaded_collection} == {initial_id, fresh_id}
            worker_result = child(str(Path(__file__)), "--worker", collection["task_id"])
            assert worker_result["roles_read"] == ["invocation", "latest"], worker_result
            published = client.get(f"/api/results/{worker_result['public_id']}")
            published.raise_for_status()
            assert published.json()["status"] == "ready" and published.json()["revision_number"] == 1
            assert published.json()["content"]["title"] == "Tomato sandwich"
            report["worker"] = worker_result
            report["checks"]["recipe_worker_real_process_reads_images_history_and_publishes"] = True
            assert submitted(fresh_id, "red") != first_hash
            report["checks"]["recipe_task_frozen_collection_uploads_both_roles"] = True
            assert request("turn-1") == fresh_id
            report["checks"]["distinct_fresh_screen_keeps_voice_call_and_deduplicates_replay"] = len(rows("SELECT id FROM live_sessions")) == 1
            screen.adb(serial, "shell", "settings", "put", "secure", "assist_screenshot_enabled", "0")
            blocked_id = request("turn-2")
            blocked = screen.wait_for("disabled screenshot", lambda: terminal(blocked_id), seconds=15)
            report["disabled"] = blocked
            assert blocked["status"] == "unavailable" and blocked["reason"] in ("platform_or_app_policy", "callback_null", "android_screenshot_disabled"), blocked
            report["checks"]["android_screenshot_policy_preserved"] = True
            report["checks"]["single_fake_voice_call_no_provider_connection"] = rows("SELECT provider_adapter FROM live_sessions") == [{"provider_adapter": "fake"}]
            report["apk_sha256"] = sha256((screen.ANDROID / "app/build/outputs/apk/debug/app-debug.apk").read_bytes()).hexdigest()
            report["status"] = "passed"
    except Exception as error:
        report["failure"] = str(error)
        raise
    finally:
        if serial:
            (evidence / "android-capture.log").write_text(screen.adb(serial, "logcat", "-d", "-s", "JarvisPhoneCapture:I", "JarvisScreenCapture:W", "JarvisCaptureFixture:I", "*:S"), encoding="utf-8")
            screen.adb(serial, "shell", "settings", "put", "secure", "assist_screenshot_enabled", "1")
            screen.adb(serial, "shell", "am", "force-stop", screen.PACKAGE)
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        (evidence / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"status": report["status"], "checks": report["checks"], "failure": report.get("failure"), "models_called": 0}), flush=True)
        if web:
            web.shutdown()
            web.server_close()
        screen.stop_tree(server)
        screen.stop_tree(emulator)


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--fresh":
        asyncio.run(fresh(*sys.argv[2:]))
    elif len(sys.argv) == 5 and sys.argv[1] == "--collection":
        asyncio.run(admit_collection(*sys.argv[2:]))
    elif len(sys.argv) == 3 and sys.argv[1] == "--worker":
        asyncio.run(recipe_worker(sys.argv[2]))
    else:
        main()
