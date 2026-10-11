"""Zero-model API37 notification controls with real native WebRTC audio.

Disposable emulator/database only. A loopback Chromium peer sends a synthetic
tone and receives actual Android microphone RTP; the backend uses its fake adapter.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

import httpx

spec = importlib.util.spec_from_file_location("results", Path(__file__).with_name("verify-result-platform.py"))
results = importlib.util.module_from_spec(spec); spec.loader.exec_module(results)
screen = results.screen


def main():
    from app.config import settings
    from app.jarvis_tasks.acceptance import candidate_identity, resolve_test_configuration
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.report.resolve()
    if evidence.exists() or not evidence.is_relative_to(screen.ROOT / "data"):
        raise ValueError("Use a fresh project-owned report directory")
    evidence.mkdir(parents=True)
    configuration = resolve_test_configuration("capture_privacy", paths={key: str(evidence / key) for key in
        ("codex_profiles_dir", "task_workspaces_dir", "task_context_blobs_dir", "conversation_workspaces_dir")})
    for key, value in configuration["settings"].items(): setattr(settings, key, value)
    report = {"status": "failed", "models_called": 0, "checks": {}, "candidate": candidate_identity()}
    backend = peer = emulator = serial = None
    try:
        port = screen.unused_port(); peer_port = screen.unused_port()
        db_path = evidence / "fixture.db"
        env = {**os.environ, **{key.upper(): str(value).lower() if isinstance(value, bool) else str(value)
            for key, value in configuration["settings"].items()}, "DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}",
            "AUTH_PASSWORD": "", "JARVIS_ACCESS_MODE": "personal", "SESSION_COOKIE_SECURE": "false",
            "SESSION_SECRET_KEY": uuid4().hex, "OPENAI_API_KEY": "", "CODEX_API_KEY": "", "OPENROUTER_API_KEY": "", "LOCAL_LLM_ENABLED": "false"}
        backend = screen.start_service(["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)], env, evidence / "server.log")
        with (evidence / "peer.log").open("wb") as log:
            peer = screen.start_owned_process([shutil.which("node"), str(screen.ROOT / "frontend/scripts/local-audio-peer.mjs"), str(peer_port)],
                cwd=screen.ROOT / "frontend", stdout=log, stderr=subprocess.STDOUT)
        client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15)
        local = httpx.Client(base_url=f"http://127.0.0.1:{peer_port}", timeout=15)
        screen.wait_for("backend", lambda: client.get("/api/auth/status").status_code == 401)
        screen.wait_for("local WebRTC peer", lambda: local.get("/ready").json()["ready"])
        client.post("/api/auth/login", json={"password": ""}).raise_for_status()
        serial, emulator = screen.ensure_emulator()
        assert serial.startswith("emulator-")
        for module in ("app", "screen-fixture"):
            screen.adb(serial, "install", "-r", str(screen.ANDROID / module / "build/outputs/apk/debug" / f"{module}-debug.apk"))
        screen.adb(serial, "shell", "am", "force-stop", screen.PACKAGE)
        screen.adb(serial, "shell", "pm", "clear", screen.PACKAGE)
        for permission in ("RECORD_AUDIO", "POST_NOTIFICATIONS", "ACCESS_LOCAL_NETWORK"):
            screen.adb(serial, "shell", "pm", "grant", screen.PACKAGE, f"android.permission.{permission}")
        screen.adb(serial, "shell", "cmd", "role", "add-role-holder", "android.app.role.ASSISTANT", screen.PACKAGE)
        for setting in ("assist_screenshot_enabled", "assist_structure_enabled"):
            screen.adb(serial, "shell", "settings", "put", "secure", setting, "1")
        screen.adb(serial, "shell", "input", "keyevent", "224"); screen.adb(serial, "shell", "wm", "dismiss-keyguard")
        screen.adb(serial, "logcat", "-c")

        def rows(sql, values=()):
            with sqlite3.connect(db_path) as db:
                db.row_factory = sqlite3.Row
                return [dict(row) for row in db.execute(sql, values)]
        def child(*arguments):
            value = subprocess.run([sys.executable, str(Path(__file__).with_name("verify-phone-browser-capture.py")), *arguments],
                cwd=screen.BACKEND, env=env, capture_output=True, text=True, check=True, timeout=30)
            return json.loads(value.stdout.strip().splitlines()[-1])
        def logs(): return screen.adb(serial, "logcat", "-d", "-s", "JarvisLocalAudio:I", "JarvisAudio:I", "JarvisMicrophone:I", "*:S")
        def probe(action="snapshot"):
            screen.adb(serial, "shell", "am", "broadcast", "-n", f"{screen.PACKAGE}/.audio.AudioControlsProbeReceiver", "--es", "probe", action)
            lines = [line.split("{", 1)[1] for line in logs().splitlines() if '{"probe"' in line]
            return json.loads("{" + lines[-1])
        def mode(name):
            value = probe()
            return value if value["state"]["mode"] == name else None
        def wait_mode(name): return screen.wait_for(name, lambda: mode(name), seconds=30)
        def click(label):
            screen.adb(serial, "shell", "cmd", "statusbar", "expand-notifications")
            def button():
                nodes = results.nodes(serial, evidence, "controls")
                if not any(n.get("resource-id", "").endswith("notification_stack_scroller") for n in nodes):
                    # A still-finishing Home/assistant transition can close the
                    # shade after the first request. Reopen from observed state.
                    screen.adb(serial, "shell", "cmd", "statusbar", "expand-notifications")
                    return None
                found = next((n for n in nodes if n.get("text", "").lower() == label.lower()), None)
                if found is None:
                    row = next((n for n in nodes if n.get("resource-id", "").endswith("expandableNotificationRow")
                        and any(child.get("text") in ("Microphone off", "Waiting for ‘Hey Jarvis’", "Starting Live…",
                            "Live conversation active", "Microphone permission needed", "Unable to connect", "Starting wake listening…")
                            for child in n.iter("node"))), None)
                    expand = next((n for n in row.iter("node") if n.get("resource-id", "").endswith("expand_button")
                        and n.get("content-desc") == "Expand"), None) if row is not None else None
                    if expand is not None: screen.tap_node(serial, expand)
                return found
            screen.tap_node(serial, screen.wait_for(label, button, seconds=20))
            screen.adb(serial, "shell", "cmd", "statusbar", "collapse")
        def web_voice(foreign=False):
            screen.adb(serial, "shell", "am", "start", "-W", "-n", f"{screen.PACKAGE}/.web.NativeVoiceFixtureActivity",
                "--ez", "untrusted_origin", str(foreign).lower())
            screen.wait_for("native bridge page", lambda: results.visible(serial, evidence, "Native voice controls", "native-bridge"), seconds=25)
        def configure(code=None):
            prior = logs().count("fixture_ready")
            extras = ["--es", "server_url", f"http://10.0.2.2:{port}", "--es", "peer_url", f"http://10.0.2.2:{peer_port}"]
            if code: extras += ["--es", "pairing_code", code]
            screen.adb(serial, "shell", "am", "start", "-W", "-n", f"{screen.PACKAGE}/.audio.LocalAudioFixtureActivity", *extras)
            screen.wait_for("fixture configuration", lambda: logs().count("fixture_ready") > prior, seconds=20)
        configure(client.post("/api/jarvis/devices/pairing-code").json()["code"])
        initial = wait_mode("paused")
        assert initial["notification"] and not initial["owner"] and initial["recorders"] == 0, initial
        report["checks"]["paused_notification_without_service"] = True
        screen.adb(serial, "shell", "input", "keyevent", "3")
        click("Listen now")
        active = wait_mode("live"); probe("remember_stop")
        first = active["state"]["session_id"]
        screen.wait_for("real duplex audio", lambda: (value := probe())["metrics"]["captured_frames"] > 100 and value["metrics"]["played_frames"] > 50, seconds=15)
        web_voice()
        screen.wait_for("main frame receives native state", lambda: results.visible(serial, evidence, "Live conversation active", "native-state"), seconds=15)
        results.tap_text(serial, evidence, "Listen now"); time.sleep(.5)
        assert probe()["state"]["session_id"] == first and probe()["recorders"] == 1
        screen.adb(serial, "shell", "input", "keyevent", "4")
        web_voice(True)
        assert probe()["state"]["session_id"] == first
        report["checks"]["webview_uses_native_owner_and_rejects_iframe_and_foreign_origin"] = True
        screen.adb(serial, "shell", "input", "keyevent", "4")
        probe("hide_panel")
        screen.adb(serial, "shell", "am", "start", "-W", "-a", "android.intent.action.VIEW", "-d", "https://example.com", "-p", "com.android.chrome")
        before = probe(); time.sleep(1); after = probe()
        assert after["state"]["session_id"] == first and after["recorders"] == 1, after
        assert after["metrics"]["captured_frames"] > before["metrics"]["captured_frames"]
        assert after["metrics"]["played_frames"] > before["metrics"]["played_frames"]
        peer_stats = local.get("/stats").json(); assert peer_stats["received_packets"] > 0 and peer_stats["sent_packets"] > 0, peer_stats
        report["checks"]["real_capture_playback_survives_panel_close_and_chrome"] = True
        report["peer_stats"] = peer_stats
        activation = active["state"]["activation_id"]
        invocation = screen.wait_for("native invocation image", lambda: rows(
            "SELECT * FROM task_capture_reports WHERE activation_id=? AND capture_mode='activation' AND status='captured'", (activation,)), seconds=15)[0]
        latest_id = child("--fresh", first, str(active["state"]["generation"]), "background-turn")["capture_id"]
        screen.wait_for("background latest image", lambda: rows("SELECT * FROM task_capture_reports WHERE capture_id=? AND status='captured'", (latest_id,)), seconds=15)
        assert probe()["state"]["session_id"] == first and probe()["recorders"] == 1
        collection = child("--collection", first, str(active["state"]["generation"]), "background-turn")
        assert set(collection["roles"]) == {"invocation", "latest"}
        screen.wait_for("both run-bound images", lambda: len(rows("SELECT id FROM task_context_requests WHERE task_id=? AND state='ready'", (collection["task_id"],))) == 2, seconds=20)
        report["checks"]["background_capture_preserves_audio_and_uploads_both_images"] = True
        screen.adb(serial, "shell", "input", "keyevent", "26")
        locked_id = child("--fresh", first, str(active["state"]["generation"]), "locked-turn")["capture_id"]
        locked = screen.wait_for("locked screenshot unavailable", lambda: rows("SELECT reason FROM task_capture_reports WHERE capture_id=? AND status='unavailable'", (locked_id,)), seconds=15)
        assert locked[0]["reason"] == "locked" and probe()["recorders"] == 1, locked
        screen.adb(serial, "shell", "input", "keyevent", "224"); screen.adb(serial, "shell", "wm", "dismiss-keyguard")
        report["checks"]["screen_lock_blocks_capture_and_preserves_active_audio"] = True
        click("Stop Live"); paused = wait_mode("paused")
        screen.wait_for("paused owner released", lambda: not probe()["owner"], seconds=10)
        assert paused["recorders"] == 0 and paused["notification"] and paused["sleep"], paused
        screen.wait_for("backend closed", lambda: rows("SELECT lifecycle_state FROM live_sessions WHERE id=?", (first,))[0]["lifecycle_state"] == "disconnected", seconds=10)
        report["checks"]["stop_releases_microphone_and_closes_exact_backend"] = True
        screen.adb(serial, "shell", "cmd", "statusbar", "expand-notifications")
        def control_row():
            nodes = results.nodes(serial, evidence, "dismiss")
            return next((n for n in nodes if n.get("resource-id", "").endswith("expandableNotificationRow")
                and any(child.get("text") == "Microphone off" for child in n.iter("node"))), None)
        row = screen.wait_for("paused control row", control_row, seconds=20)
        x1, y1, x2, y2 = map(int, re.findall(r"\d+", row.get("bounds")))
        screen.adb(serial, "shell", "input", "swipe", str((x1+x2)//2), str((y1+y2)//2), "1079", str((y1+y2)//2), "250")
        screen.wait_for("dismissed controls", lambda: not probe()["notification"], seconds=10)
        screen.adb(serial, "shell", "cmd", "statusbar", "collapse")
        time.sleep(.5); assert not probe()["notification"] and not probe()["owner"]
        screen.adb(serial, "shell", "am", "start", "-W", "-n", f"{screen.PACKAGE}/.MainActivity")
        screen.wait_for("explicit reopen restores controls", lambda: probe()["notification"], seconds=10)
        report["checks"]["dismissal_respected_until_explicit_app_open"] = True
        worker = child("--worker", collection["task_id"])
        result = client.get(f"/api/results/{worker['public_id']}"); result.raise_for_status()
        assert result.json()["status"] == "ready" and worker["roles_read"] == ["invocation", "latest"]
        report["checks"]["task_finishes_after_live_stop_with_real_worker_restart"] = True
        report["worker"] = worker
        click("Hey Jarvis"); waiting = wait_mode("waiting")
        assert waiting["recorders"] == 1 and not waiting["sleep"], waiting
        report["checks"]["notification_rearms_real_wake_microphone"] = True
        click("Listen now"); replacement = wait_mode("live")
        assert replacement["state"]["session_id"] != first
        probe("replay_stop"); time.sleep(.5)
        assert probe()["state"]["session_id"] == replacement["state"]["session_id"]
        report["checks"]["stale_pending_intent_cannot_stop_replacement"] = True
        # Natural end returns to the previous idle preference, not another Live call.
        client.delete(f"/api/jarvis/live/sessions/{replacement['state']['session_id']}?generation={replacement['state']['generation']}").raise_for_status()
        wait_mode("waiting")
        report["checks"]["natural_end_restores_wake_preference"] = True
        click("Listen now"); wait_mode("live")
        local.get("/disconnect").raise_for_status()
        failed = wait_mode("error")
        screen.wait_for("connection loss releases audio", lambda: probe()["recorders"] == 0 and not probe()["owner"], seconds=10)
        report["checks"]["connection_loss_releases_audio_without_live_retry"] = True
        click("Hey Jarvis"); wait_mode("waiting")
        click("Listen now"); wait_mode("live")
        count = len(rows("SELECT id FROM live_sessions"))
        screen.adb(serial, "shell", "am", "force-stop", screen.PACKAGE)
        screen.adb(serial, "shell", "am", "start", "-W", "-n", f"{screen.PACKAGE}/.MainActivity")
        wait_mode("waiting")
        assert len(rows("SELECT id FROM live_sessions")) == count
        report["checks"]["process_recovery_rearms_wake_without_live_restart"] = True
        # Restore the local-only transport after process death; no paid calls are possible (keys empty).
        configure()
        local.get("/delay").raise_for_status()
        click("Listen now"); wait_mode("connecting"); click("Stop Live"); wait_mode("paused")
        time.sleep(4)
        assert probe()["state"]["mode"] == "paused" and probe()["recorders"] == 0
        report["checks"]["stop_during_startup_cannot_resurrect_audio"] = True
        screen.adb(serial, "shell", "am", "force-stop", screen.PACKAGE)
        screen.adb(serial, "shell", "am", "start", "-W", "-n", f"{screen.PACKAGE}/.MainActivity")
        recovered = wait_mode("paused")
        assert not recovered["owner"] and recovered["sleep"] and recovered["recorders"] == 0
        report["checks"]["process_recovery_keeps_explicit_pause"] = True
        screen.adb(serial, "shell", "pm", "revoke", screen.PACKAGE, "android.permission.RECORD_AUDIO")
        # Revocation may kill the process. Reopen the setup UI, preserving pairing.
        screen.adb(serial, "shell", "am", "start", "-W", "-n", f"{screen.PACKAGE}/.MainActivity")
        denied = wait_mode("error")
        assert denied["state"]["text"] == "Microphone permission needed" and denied["recorders"] == 0
        click("Listen now")
        screen.wait_for("permission repair opens setup", lambda: results.visible(serial, evidence, "Allow", "permission-repair"), seconds=15)
        report["checks"]["missing_permission_never_claims_listening"] = True
        device = rows("SELECT id FROM jarvis_devices WHERE revoked_at IS NULL")[0]["id"]
        client.delete(f"/api/jarvis/devices/{device}").raise_for_status()
        screen.wait_for("host unlink removes phone controls", lambda: not probe()["notification"] and not probe()["owner"], seconds=15)
        report["checks"]["host_unlink_removes_controls"] = True
        probe("unlink"); time.sleep(.5)
        unlinked = probe(); assert not unlinked["notification"] and not unlinked["owner"] and unlinked["recorders"] == 0
        report["checks"]["unlink_removes_controls_and_audio"] = True
        assert all(row["provider_adapter"] == "fake" for row in rows("SELECT provider_adapter FROM live_sessions"))
        report["status"] = "passed"
    except Exception as error:
        report["failure"] = str(error); raise
    finally:
        if serial:
            (evidence / "android.log").write_text(screen.adb(serial, "logcat", "-d"), encoding="utf-8")
            screen.adb(serial, "shell", "am", "force-stop", screen.PACKAGE)
        (evidence / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"status": report["status"], "checks": report["checks"], "failure": report.get("failure"), "models_called": 0}), flush=True)
        screen.stop_tree(peer); screen.stop_tree(backend); screen.stop_tree(emulator)


if __name__ == "__main__": main()
