"""API-37 native wake/visible-session/fresh/protected/denied/locked capture probe.

Run from backend with uv run python ../android/scripts/verify-capture-feasibility.py.
No model, injected bitmap, platform bypass, or physical phone is used.
"""
from __future__ import annotations

from io import BytesIO
import json
import importlib.util
import os
import sys
import asyncio
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
from uuid import uuid4

import httpx
from PIL import Image

spec = importlib.util.spec_from_file_location("screen_workflow", Path(__file__).with_name("verify-screen-workflow.py"))
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)
ANDROID, PACKAGE, FIXTURE_PACKAGE = workflow.ANDROID, workflow.PACKAGE, workflow.FIXTURE_PACKAGE
adb, ensure_emulator = workflow.adb, workflow.ensure_emulator
start_service, stop_tree, unused_port, wait_for = workflow.start_service, workflow.stop_tree, workflow.unused_port, workflow.wait_for


def main():
    from app.config import settings
    from app.jarvis_tasks.acceptance import ROOT, SCHEMA, candidate_identity, resolve_test_configuration, _command
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True, help="Fresh project-owned M8 privacy evidence directory")
    args = parser.parse_args()
    evidence = args.report.resolve()
    if evidence.exists() or not evidence.is_relative_to(ROOT / "data"):
        raise ValueError("Use a fresh project-owned privacy report directory")
    evidence.mkdir(parents=True)
    configuration = resolve_test_configuration("capture_privacy", paths={
        "codex_profiles_dir": str(evidence / "profiles"), "task_workspaces_dir": str(evidence / "workspaces"),
        "task_context_blobs_dir": str(evidence / "blobs"), "conversation_workspaces_dir": str(evidence / "conversations")})
    for key, value in configuration["settings"].items():
        setattr(settings, key, value)
    candidate = candidate_identity()
    print(f"Evidence: {evidence}", flush=True)
    server = emulator = None
    pin_set = False
    report = {"checks": {}, "evidence_dir": str(evidence)}
    try:
        subprocess.run([str(ANDROID / "gradlew.bat"), ":app:assembleDebug",
                        ":screen-fixture:assembleDebug"], cwd=ANDROID, check=True, timeout=300)
        serial, emulator = ensure_emulator()
        assert adb(serial, "shell", "getprop", "ro.build.version.sdk") == "37"
        adb(serial, "shell", "input", "keyevent", "224")
        adb(serial, "shell", "wm", "dismiss-keyguard")
        for module in ("app", "screen-fixture"):
            adb(serial, "install", "-r", str(ANDROID / module / "build/outputs/apk/debug" / f"{module}-debug.apk"))
        for permission in ("POST_NOTIFICATIONS", "ACCESS_LOCAL_NETWORK"):
            adb(serial, "shell", "pm", "grant", PACKAGE, f"android.permission.{permission}")
        adb(serial, "shell", "cmd", "role", "add-role-holder", "android.app.role.ASSISTANT", PACKAGE)
        wait_for("assistant", lambda: "mComponent=" + PACKAGE in
                 adb(serial, "shell", "dumpsys", "voiceinteraction"), seconds=45)
        for setting in ("assist_screenshot_enabled", "assist_structure_enabled"):
            adb(serial, "shell", "settings", "put", "secure", setting, "1")
        adb(serial, "shell", "am", "start", "-W", "-n", f"{FIXTURE_PACKAGE}/.MarkerActivity")
        from app.services.task_workflows import load_workflow
        definition = load_workflow("screen_document@1")
        db_path = evidence / "fixture.db"
        port = unused_port()
        env = {**os.environ, **{key.upper(): str(value).lower() if isinstance(value, bool) else str(value)
            for key, value in configuration["settings"].items()}, "DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}",
               "AUTH_PASSWORD": "", "JARVIS_ACCESS_MODE": "personal", "SESSION_SECRET_KEY": uuid4().hex,
               "TASK_WORKFLOW_ALLOWLIST": f"screen_document@1:{definition.digest}",
               "TASK_CONTEXT_BLOBS_DIR": str(evidence / "blobs"),
               "TASK_EXECUTOR": "codex", "TASK_POOL_ENABLED": "false", "LOCAL_LLM_ENABLED": "false",
               "OPENAI_API_KEY": "", "CODEX_API_KEY": "", "OPENROUTER_API_KEY": ""}
        server = start_service(["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                "--port", str(port)], env, evidence / "server.log")
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
            wait_for("backend", lambda: client.get("/api/auth/status").status_code == 401)
            client.post("/api/auth/login", json={"password": ""}).raise_for_status()
            activation, root_capture = str(uuid4()), str(uuid4())
            adb(serial, "logcat", "-c")
            adb(serial, "shell", "am", "start", "-f", "0x10008000", "-n",
                f"{PACKAGE}/.assistant.CaptureFixtureActivity", "--ez", "probe_mode", "true",
                "--es", "server_url", f"http://10.0.2.2:{port}",
                "--es", "activation_id", activation, "--es", "capture_id", root_capture)

            def logs():
                return adb(serial, "logcat", "-d", "-s", "JarvisCaptureFixture:I", "*:S")

            wait_for("probe configured", lambda: f"probe_ready activation_id={activation}" in logs(), seconds=30)

            def command(name, capture_id, mode="fresh", enabled=False):
                adb(serial, "shell", "am", "broadcast", "-n", f"{PACKAGE}/.assistant.CaptureProbeReceiver",
                    "--es", "command", name, "--es", "capture_id", capture_id,
                    "--es", "capture_mode", mode, "--ez", "enabled", str(enabled).lower())
                wait_for(name, lambda: f"probe_command={name} capture_id={capture_id} complete" in logs(), seconds=20)

            def capture(capture_id, *, require_session=True):
                result = client.get(f"/api/jarvis/captures/{capture_id}")
                if result.status_code == 404:
                    return None
                result.raise_for_status()
                body = result.json()
                return body if body["status"] in {"captured", "unavailable", "expired"} and (body["session_id"] or not require_session) else None

            def db_rows(sql, args=()):
                with sqlite3.connect(db_path) as db:
                    db.row_factory = sqlite3.Row
                    return [dict(r) for r in db.execute(sql, args).fetchall()]

            def admit(capture_id):
                # Privacy probes use the real capture/task service without a model.
                result = subprocess.run([sys.executable, __file__, "--admit", capture_id],
                                        cwd=workflow.BACKEND, env=env, capture_output=True,
                                        text=True, timeout=20)
                return json.loads(result.stdout.strip().splitlines()[-1])

            def submitted_image(capture_id):
                admitted = admit(capture_id)
                assert admitted["accepted"], admitted
                rows = wait_for("private uploaded image", lambda: db_rows(
                    "SELECT b.* FROM task_context_requests r JOIN task_context_blobs b ON b.id=r.blob_id "
                    "WHERE r.capture_id=? AND r.state='ready'", (capture_id,)), seconds=25)
                return Image.open(BytesIO(Path(rows[0]["storage_path"]).read_bytes())).convert("RGB")

            command("consent", root_capture, enabled=True)
            command("wake", root_capture, "activation")
            initial = wait_for("background wake capture status", lambda: capture(root_capture), seconds=10)
            report["initial_background_wake"] = initial
            report["checks"]["wake_policy_observed"] = initial["status"] in {"captured", "unavailable"}
            # onShow is not proof that the assistant window has drawn.
            time.sleep(2)
            command("consent", root_capture, enabled=False)
            opted_out_id = str(uuid4())
            command("refresh", opted_out_id)
            opted_out = wait_for("opted out context", lambda: capture(opted_out_id), seconds=10)
            report["checks"]["screenshot_independent_of_structure_consent"] = opted_out["status"] == "captured"
            command("consent", root_capture, enabled=True)
            report["context_consent"] = "Explicit debug fixture opt-in using the production preference; defaults are unchanged"
            first_id = str(uuid4())
            command("refresh", first_id)
            time.sleep(2)
            (evidence / "voiceinteraction-pending.txt").write_text(
                adb(serial, "shell", "dumpsys", "voiceinteraction"), encoding="utf-8")
            first = wait_for("visible assistant capture", lambda: capture(first_id), seconds=30)
            report["visible_assistant_capture"] = first
            report["checks"]["visible_assistant_capture"] = first["status"] == "captured"
            if first["status"] == "captured":
                image = submitted_image(first_id)
                report["checks"]["underlying_screen_a"] = has_panel(image, blue=True)

            adb(serial, "shell", "am", "broadcast", "-a", f"{FIXTURE_PACKAGE}.CHANGE",
                "-p", FIXTURE_PACKAGE, "--ez", "screen_b", "true")
            time.sleep(0.5)
            second_id = str(uuid4())
            command("refresh", second_id)
            second = wait_for("fresh screen B", lambda: capture(second_id), seconds=10)
            report["checks"]["fresh_keeps_activation_and_session"] = all(
                first[k] == second[k] for k in ("activation_id", "session_id", "generation"))
            report["checks"]["one_live_session"] = len(db_rows("SELECT id FROM live_sessions")) == 1
            if second["status"] == "captured":
                image = submitted_image(second_id)
                report["checks"]["underlying_screen_b"] = has_panel(image, blue=False)
            else:
                report["checks"]["underlying_screen_b"] = False
            report["fresh_screen_b"] = second

            for name in ("protected", "android_disabled", "denied"):
                if name == "protected":
                    adb(serial, "shell", "am", "broadcast", "-a", f"{FIXTURE_PACKAGE}.CHANGE",
                        "-p", FIXTURE_PACKAGE, "--ez", "secure", "true")
                else:
                    adb(serial, "shell", "am", "broadcast", "-a", f"{FIXTURE_PACKAGE}.CHANGE",
                        "-p", FIXTURE_PACKAGE, "--ez", "secure", "false")
                    adb(serial, "shell", "settings", "put", "secure", "assist_screenshot_enabled",
                        "0" if name == "android_disabled" else "1")
                    adb(serial, "shell", "cmd", "appops", "set", PACKAGE, "READ_SCREEN_CONTEXT",
                        "default" if name == "android_disabled" else "ignore")
                time.sleep(0.5)
                capture_id = str(uuid4())
                command("refresh", capture_id)
                result = wait_for(name + " capture", lambda: capture(capture_id), seconds=10)
                count_before = len(db_rows("SELECT id FROM tasks"))
                admission = admit(capture_id)
                time.sleep(2.5)
                report[name] = result
                report["checks"][name + "_rejects_without_task"] = (
                    not admission["accepted"] and result["status"] == "unavailable" and len(db_rows("SELECT id FROM tasks")) == count_before
                    and not db_rows("SELECT id FROM task_context_requests WHERE capture_id=?", (capture_id,)))

            # Secure lock is established on this disposable AVD, then restored.
            adb(serial, "shell", "settings", "put", "secure", "assist_screenshot_enabled", "1")
            adb(serial, "shell", "cmd", "appops", "set", PACKAGE, "READ_SCREEN_CONTEXT", "default")
            assert "Pin set to" in adb(serial, "shell", "locksettings", "set-pin", "2468")
            pin_set = True
            adb(serial, "shell", "input", "keyevent", "223")
            time.sleep(1)
            locked_id = str(uuid4())
            command("wake", locked_id, "activation")
            # Lock purges retained capture state before any late session binding.
            # Its terminal no-pixels report is valid without that binding.
            locked = wait_for("locked capture", lambda: capture(locked_id, require_session=False), seconds=10)
            count_before = len(db_rows("SELECT id FROM tasks"))
            assert not admit(locked_id)["accepted"]
            time.sleep(2.5)
            report["locked"] = locked
            report["checks"]["locked_rejects_without_task"] = (
                locked["status"] == "unavailable" and locked["reason"] == "locked"
                and len(db_rows("SELECT id FROM tasks")) == count_before)
            (evidence / "android-capture.log").write_text(logs(), encoding="utf-8")
        executed = _command("capture_contracts", [sys.executable, "-m", "pytest", "tests/test_task_capture.py",
            "tests/test_screen_collection.py", "tests/test_worker_database_process.py",
            "-q", "--tb=short", f"--junitxml={evidence / 'capture_contracts.xml'}"],
            workflow.BACKEND, evidence, junit=evidence / "capture_contracts.xml",
            env={**{key.upper(): str(value).lower() if isinstance(value, bool) else str(value)
                 for key, value in configuration["settings"].items()},
                 "JARVIS_LIVE_CODEX": "0", "JARVIS_M4_LIVE": "0", "OPENAI_API_KEY": "", "CODEX_API_KEY": ""})
        report["contracts"] = {**executed["evidence"], "skipped": executed["skipped"]}
        report["status"] = "passed" if all(report["checks"].values()) and executed["status"] == "passed" else "failed"
        print(json.dumps(report, indent=2), flush=True)
        assert report["status"] == "passed", report["checks"]
    except Exception as error:
        report["status"] = "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        if 'serial' in locals():
            restoration = []
            if pin_set:
                restoration.append(("locksettings", "clear", "--old", "2468"))
            restoration.extend([
                ("input", "keyevent", "224"),
                ("wm", "dismiss-keyguard"),
                ("settings", "put", "secure", "assist_screenshot_enabled", "1"),
                ("cmd", "appops", "set", PACKAGE, "READ_SCREEN_CONTEXT", "default"),
            ])
            try:
                adb(serial, "shell", "am", "broadcast", "-n", f"{PACKAGE}/.assistant.CaptureProbeReceiver",
                    "--es", "command", "consent", "--ez", "enabled", "false")
            except Exception as error:
                report.setdefault("cleanup_errors", []).append(str(error))
            for args in restoration:
                try:
                    adb(serial, "shell", *args)
                except Exception as error:
                    report.setdefault("cleanup_errors", []).append(str(error))
            try:
                (evidence / "android-capture.log").write_text(adb(serial, "logcat", "-d"), encoding="utf-8")
            except Exception as error:
                report.setdefault("cleanup_errors", []).append(str(error))
        (evidence / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        proof = {"api_level": 37, "provider_calls": 0, "native_checks": report["checks"], "contracts": report.get("contracts", {}),
            "report": str(evidence / "report.json")}
        (evidence / "acceptance.json").write_text(json.dumps({"schema": SCHEMA, "gate": "M8", "candidate": candidate,
            "test_configuration": configuration, "finished_at": datetime.now(timezone.utc).isoformat(),
            "checks": [{"id": "capture_privacy", "status": report.get("status", "failed"), "evidence": proof}]}, indent=2), encoding="utf-8")
        stop_tree(server)
        stop_tree(emulator)


async def admit_capture(capture_id):
    from app.db.session import session_scope, close_engine
    from app.db.tables import TaskCaptureReport, LiveSession
    from sqlalchemy import select
    from app.services.task_capture import CaptureCollection, task_capture_service
    from app.services.task_service import task_service
    try:
        async with session_scope() as session:
            capture = await session.get(TaskCaptureReport, capture_id)
            live = (await session.execute(select(LiveSession).where(
                LiveSession.controller_id == capture.device_id, LiveSession.activation_id == capture.activation_id)
                .order_by(LiveSession.created_at.desc()).limit(1))).scalar_one()
        binding = await task_capture_service.preflight(
            device_id=capture.device_id, session_id=live.id,
            generation=live.generation, activation_id=capture.activation_id,
            capture_id=capture_id, capture_mode=capture.capture_mode, wait_seconds=0,
        )
        task = await task_service.dispatch(
            goal="Describe the visible screen marker", origin_surface="jarvis_voice",
            idempotency_key=f"capture-probe:{capture_id}", execution_kind="workflow",
            execution_config={"definition": "screen_document@1"},
            capture_binding=CaptureCollection(
                ((("invocation" if binding.capture_mode == "activation" else "latest"), binding),),
                (("latest" if binding.capture_mode == "activation" else "invocation"),)),
        )
        print(json.dumps({"accepted": True, "task_id": task.task.id}))
    except ValueError as error:
        print(json.dumps({"accepted": False, "reason": str(error)}))
    finally:
        await close_engine()


def has_panel(image, *, blue):
    streak = 0
    for y in range(image.height):
        red, green, value = image.getpixel((image.width // 8, y))
        matches = (value > 140 and value > 2 * red and value > 2 * green) if blue else (red > 140 and red > 2 * value and red > 2 * green)
        streak = streak + 1 if matches else 0
        if streak >= image.height * .03:
            return True
    return False


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--admit":
        asyncio.run(admit_capture(sys.argv[2]))
    else:
        main()
