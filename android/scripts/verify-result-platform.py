"""Unattended Android acceptance for results kept as app records.

A fixture worker publishes a Document into the Documents app. The phone, paired
through its real deep link against a disposable password-backed backend behind
the real Next frontend, must notify without stealing focus, open the record in
the app from the notification's "Open result" action, show it again after a
process death without rerunning anything, report the record it shows, and open
the document's external source in the browser.

No model, capture, provider, speech or physical-device claims.
Run from backend with: uv run python ../android/scripts/verify-result-platform.py
"""
from __future__ import annotations

import harness_support  # noqa: F401  (isolates started backends from the personal .env)

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time
from urllib.parse import urlencode
from uuid import uuid4
from xml.etree import ElementTree

import httpx

spec = importlib.util.spec_from_file_location("screen_fixture", Path(__file__).with_name("verify-screen-workflow.py"))
screen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screen)
PASSWORD = "m5-emulator-fixture"


def parse_hierarchy(output):
    prefix = "INSTRUMENTATION_RESULT: hierarchy="
    if prefix not in output:
        raise RuntimeError("Read-only hierarchy fixture did not return a snapshot")
    value = output.split(prefix, 1)[1]
    if "</hierarchy>" not in value:
        raise RuntimeError("Read-only hierarchy snapshot was incomplete")
    return value.split("</hierarchy>", 1)[0].strip() + "</hierarchy>"


def nodes(serial, evidence, name="current"):
    # Fresh read in a separate fixture package: no global-idle prerequisite,
    # cached device XML or restart of the Jarvis/Chrome process under test.
    output = screen.adb(serial, "shell", "am", "instrument", "-w",
        f"{screen.FIXTURE_PACKAGE}/.HierarchyDumpInstrumentation", timeout=15)
    (evidence / f"android-{name}-raw.txt").write_text(output, encoding="utf-8")
    tree = parse_hierarchy(output)
    with (evidence / "hierarchy-dumps.log").open("a", encoding="utf-8") as log:
        log.write(f"{name}: fresh read-only instrumentation snapshot\n")
    (evidence / f"android-{name}.xml").write_text(tree, encoding="utf-8")
    return list(ElementTree.fromstring(tree).iter("node"))


def text_node(serial, evidence, text):
    return next((node for node in nodes(serial, evidence) if node.get("text") == text or node.get("content-desc") == text), None)


def tap_text(serial, evidence, text):
    node = screen.wait_for(text, lambda: text_node(serial, evidence, text), seconds=30)
    screen.tap_node(serial, node)


def notification_action(serial, evidence):
    current = nodes(serial, evidence, "notification")
    action = next((n for n in current if n.get("text") == "Open result"), None)
    if action is not None:
        return action
    expand = next((n for n in current if n.get("resource-id", "").endswith("expand_button")), None)
    if expand is not None:
        screen.tap_node(serial, expand)
    return None


def visible(serial, evidence, marker, name):
    current = nodes(serial, evidence, name)
    return any(marker in node.get("text", "") or marker in node.get("content-desc", "") for node in current)


def open_result(serial, path, *, clear=False, server=None, pairing_code=None):
    # Single quotes keep the device shell from splitting the record link at "&".
    extras = ["--es", "path", f"'{path}'", "--ez", "clear_cookies", str(clear).lower()]
    if pairing_code:
        extras += ["--es", "server_url", server, "--es", "pairing_code", pairing_code]
    screen.adb(serial, "shell", "am", "start", "-W", "-f", "0x10008000", "-n",
        f"{screen.PACKAGE}/.web.ResultFixtureActivity", *extras)


def login(serial, evidence, password):
    field = screen.wait_for("WebView password field", lambda: next((n for n in nodes(serial, evidence, "login")
        if n.get("class") == "android.widget.EditText"), None), seconds=30)
    screen.tap_node(serial, field)
    screen.adb(serial, "shell", "input", "keycombination", "113", "29")
    screen.adb(serial, "shell", "input", "text", password)
    screen.adb(serial, "shell", "input", "keyevent", "4")
    tap_text(serial, evidence, "Sign in")


def browser_page_visible(serial, evidence, marker):
    """Whether the browser shows the page, after closing the tips and promotions it lays over it."""
    current = nodes(serial, evidence, "external-page")
    if any(marker in node.get("text", "") or marker in node.get("content-desc", "") for node in current):
        return True
    dismiss = next((node for node in current if node.get("text") in {"Close", "No thanks", "Got it", "Not now"}
                    and node.get("clickable") == "true"), None)
    if dismiss is not None:
        screen.tap_node(serial, dismiss)
    return False


def foreground(serial):
    return screen.adb(serial, "shell", "dumpsys", "activity", "activities")


def external_activity(serial, url, browser_package):
    state = foreground(serial)
    resumed = resumed_activity(state)
    return state if resumed and browser_package + "/" in resumed and url in state else None


def resumed_activity(state):
    return next((line for line in state.splitlines() if "ResumedActivity" in line and "ActivityRecord{" in line), None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--keep-emulator", action="store_true", help="Keep only the test AVD running for iterative debugging")
    args = parser.parse_args()
    if os.name != "nt" or not screen.ADB.is_file() or not screen.EMULATOR.is_file():
        raise RuntimeError("This harness requires the installed Windows Android SDK")
    evidence = Path(tempfile.mkdtemp(prefix="jarvis-m5-results-"))
    print(f"Evidence: {evidence}", flush=True)
    server = external = frontend = emulator = None
    serial = None
    report = {"status": "failed", "scope": "Model-free API37 production result delivery; not capture/provider/physical acceptance"}
    try:
        node = shutil.which("node")
        next_bin = screen.ROOT / "frontend/node_modules/next/dist/bin/next"
        if not node or not next_bin.is_file():
            raise RuntimeError("Frontend dependencies are unavailable")
        subprocess.run([str(screen.ANDROID / "gradlew.bat"), ":app:testDebugUnitTest", ":app:assembleDebug", ":screen-fixture:assembleDebug"],
            cwd=screen.ANDROID, check=True, timeout=300)
        serial, emulator = screen.ensure_emulator()
        report["emulator_pid"] = emulator.pid if emulator else None
        print(f"Emulator: {serial}; waiting for network", flush=True)
        screen.wait_for("emulator host route", lambda: "src" in screen.adb(serial, "shell", "ip", "route", "get", "10.0.2.2"), seconds=60)
        assert int(screen.adb(serial, "shell", "getprop", "ro.build.version.sdk")) >= 37
        screen.adb(serial, "install", "-r", str(screen.ANDROID / "app/build/outputs/apk/debug/app-debug.apk"))
        screen.adb(serial, "install", "-r", str(screen.ANDROID / "screen-fixture/build/outputs/apk/debug/screen-fixture-debug.apk"))
        screen.adb(serial, "shell", "pm", "grant", screen.PACKAGE, "android.permission.POST_NOTIFICATIONS")
        screen.adb(serial, "shell", "pm", "grant", screen.PACKAGE, "android.permission.ACCESS_LOCAL_NETWORK")
        assert "android.permission.ACCESS_LOCAL_NETWORK: granted=true" in screen.adb(serial, "shell", "dumpsys", "package", screen.PACKAGE)
        screen.adb(serial, "shell", "am", "force-stop", screen.PACKAGE)
        screen.adb(serial, "shell", "cmd", "statusbar", "collapse")
        screen.adb(serial, "shell", "input", "keyevent", "3")
        screen.adb(serial, "logcat", "-c")
        port, external_port, web_port = screen.unused_port(), screen.unused_port(), screen.unused_port()
        db_path = evidence / "fixture.db"
        external_url = f"http://10.0.2.2:{external_port}/source"
        resolved = screen.adb(serial, "shell", "cmd", "package", "resolve-activity", "--brief",
            "-a", "android.intent.action.VIEW", "-d", external_url)
        component = resolved.splitlines()[-1]
        if "/" not in component or component.startswith("android/"):
            raise RuntimeError("A default emulator HTTP browser is required")
        browser_package = component.split("/", 1)[0]
        env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}",
            "AUTH_PASSWORD": PASSWORD, "JARVIS_ACCESS_MODE": "password", "JARVIS_PUBLIC_URL": f"http://10.0.2.2:{web_port}",
            "SESSION_SECRET_KEY": uuid4().hex, "SESSION_COOKIE_SECURE": "false",
            "RESULT_FIXTURE_EXTERNAL_URL": external_url, "TASK_POOL_ENABLED": "false",
            "TASK_ORCHESTRATION_ENABLED": "false", "TASK_ORCHESTRATION_LIVE_ENABLED": "false",
            "TASK_RESEARCH_ENABLED": "false", "TASK_WORKFLOW_ALLOWLIST": "", "LOCAL_LLM_ENABLED": "false",
            "OPENAI_API_KEY": "", "CODEX_API_KEY": "", "OPENROUTER_API_KEY": "", "WEB_PUSH_ENABLED": "false",
            "TASK_WORKSPACES_DIR": str(evidence / "workspaces"), "TASK_CONTEXT_BLOBS_DIR": str(evidence / "blobs"),
            "CONVERSATION_WORKSPACES_DIR": str(evidence / "conversations"),
            "KNOWLEDGE_NOTES_DIR": str(evidence / "knowledge"), "KNOWLEDGE_PREFERENCES_FILE": str(evidence / "preferences.yaml"),
            "CODEX_PROFILES_DIR": str(evidence / "profiles"),
            "TASK_ORCHESTRATION_CONTEXT_DIR": str(evidence / "orchestration")}
        app_dir = str(Path(__file__).parent)
        server = screen.start_service(["-m", "uvicorn", "result_platform_fixture:app", "--app-dir", app_dir,
            "--host", "127.0.0.1", "--port", str(port)], env, evidence / "server.log")
        external = screen.start_service(["-m", "uvicorn", "result_platform_fixture:external_app", "--app-dir", app_dir,
            "--host", "127.0.0.1", "--port", str(external_port)], env, evidence / "external.log")
        # The phone talks to the frontend, which hosts /apps/<slug> and forwards /api, as Tailscale serves it.
        frontend = screen.start_frontend(web_port, f"http://127.0.0.1:{port}", evidence, production=True)
        android_url = f"http://10.0.2.2:{web_port}"
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
            screen.wait_for("isolated API", lambda: client.get("/api/health").status_code == 200)
            screen.wait_for("frontend", lambda: httpx.get(f"http://127.0.0.1:{web_port}/login", timeout=90).status_code == 200,
                seconds=240)
            client.post("/api/auth/login", json={"password": PASSWORD}).raise_for_status()
            started = time.monotonic()
            created = client.post("/api/tasks", json={"goal": "Android result fixture", "idempotency_key": uuid4().hex,
                "result": {"mode": "new"}})
            created.raise_for_status()
            task_id = created.json()["id"]
            assert created.json()["result_url"] is None  # a new result has no record until it is published
            report["admission_ms"] = round((time.monotonic() - started) * 1000)
            # The record's key is the run that creates it, so its link is known before publication.
            run_id = client.get(f"/api/tasks/{task_id}").json()["current_run_id"]
            path = f"/apps/documents?collection=document&key={run_id}"
            pairing = client.post("/api/jarvis/devices/pairing-code")
            pairing.raise_for_status()
            # Exercise the real Android link entry point from an unpaired app.
            screen.adb(serial, "shell", "pm", "clear", screen.PACKAGE)
            for permission in ("android.permission.POST_NOTIFICATIONS", "android.permission.ACCESS_LOCAL_NETWORK", "android.permission.RECORD_AUDIO"):
                screen.adb(serial, "shell", "pm", "grant", screen.PACKAGE, permission)
            link = "jarvis://pair?" + urlencode({"server": android_url, "code": pairing.json()["code"]})
            # shell quotes preserve the query's ampersand; no permanent credential.
            screen.adb(serial, "shell", "am", "start", "-W", "-a", "android.intent.action.VIEW", "-d", "'" + link + "'", "-p", screen.PACKAGE)
            screen.wait_for("automatic link persisted", lambda: visible(serial, evidence, "This phone is linked", "link-received"), seconds=30)
            screen.adb(serial, "shell", "am", "force-stop", screen.PACKAGE)
            screen.adb(serial, "shell", "am", "start", "-W", "-a", "android.intent.action.VIEW", "-d", "'" + link + "'", "-p", screen.PACKAGE)
            screen.wait_for("retained linked native app", lambda: visible(serial, evidence, "This phone is linked", "linked"), seconds=30)
            assert "Replace phone link" not in screen.adb(serial, "shell", "dumpsys", "activity", "top")
            # The device session opens the app without an owner login, before the record exists.
            open_result(serial, path, clear=True)
            screen.wait_for("device-authenticated Documents app", lambda: visible(serial, evidence, "Documents", "app"), seconds=60)
            # The saved device credential renews a lost WebView cookie without owner login.
            open_result(serial, path, clear=True)
            screen.wait_for("cookie renewal without login", lambda: visible(serial, evidence, "Documents", "cookie-renewed"), seconds=60)
            screen.adb(serial, "shell", "input", "keyevent", "3")
            started = time.monotonic()
            published = client.post(f"/_fixture/publish/{task_id}")
            published.raise_for_status()
            assert published.json()["url"] == path and published.json()["revision"] == 1
            notification = screen.wait_for("native Open result notification", lambda: (
                dump if "Task completed" in (dump := screen.adb(serial, "shell", "dumpsys", "notification", "--noredact"))
                and "Open result" in dump else None), seconds=30)
            (evidence / "android-notification.txt").write_text(notification, encoding="utf-8")
            home = foreground(serial)
            (evidence / "android-completion-foreground.txt").write_text(home, encoding="utf-8")
            assert resumed_activity(home) and screen.PACKAGE not in resumed_activity(home), "Completion stole foreground focus"
            screen.adb(serial, "shell", "cmd", "statusbar", "expand-notifications")
            # Android may collapse actions until its expand affordance is tapped.
            action = screen.wait_for("expanded notification Open result action", lambda: notification_action(serial, evidence), seconds=30)
            screen.tap_node(serial, action)
            screen.wait_for("notification result content", lambda: visible(serial, evidence,
                "RESULT-NATIVE-DOCUMENT-READY", "ready"), seconds=30)
            report["publication_to_visible_ms"] = round((time.monotonic() - started) * 1000)
            record = client.get(f"/api/app-platform/data/documents/document/{run_id}").json()
            assert record["revision"] == 1
            # Process death: the record shows again and nothing reruns.
            screen.adb(serial, "shell", "am", "force-stop", screen.PACKAGE)
            open_result(serial, path)
            screen.wait_for("reopened record", lambda: visible(serial, evidence, "RESULT-NATIVE-DOCUMENT-READY", "reopened"), seconds=60)
            assert client.get(f"/api/app-platform/data/documents/document/{run_id}").json() == record
            tap_text(serial, evidence, "External fixture source")
            activity = screen.wait_for("external browser activity", lambda: external_activity(serial, external_url, browser_package), seconds=30)
            # Chrome first resumes a no-display intent dispatcher. Reopening
            # Jarvis during that handoff lets the pending Chrome launch win.
            screen.wait_for("external browser page", lambda: browser_page_visible(serial, evidence,
                "RESULT-EXTERNAL-BROWSER-ONLY"), seconds=45)
            (evidence / "android-external-activity.txt").write_text(activity, encoding="utf-8")
            # Return to the unchanged result rather than using browser Back as
            # a surrogate proof; force-stop/reopen already verified persistence.
            open_result(serial, path)
            screen.wait_for("record after external navigation", lambda: visible(serial, evidence,
                "RESULT-NATIVE-DOCUMENT-READY", "external-return"), seconds=60)
            with sqlite3.connect(db_path) as db:
                assert db.execute("SELECT count(*) FROM task_attempts WHERE task_id=?", (task_id,)).fetchone()[0] == 1
                deep_link = db.execute("SELECT deep_link FROM notification_outbox WHERE task_id=?", (task_id,)).fetchone()[0]
                assert deep_link == path
            # The app reported the record on screen, so Jarvis knows what "this one" means.
            with sqlite3.connect(db_path.parent / "apps.db") as apps:
                assert apps.execute("SELECT record_key FROM app_views WHERE app_slug='documents'").fetchone() == (run_id,)
            report.update(status="pass", android_api=screen.adb(serial, "shell", "getprop", "ro.build.version.sdk"),
                avd=screen.AVD_NAME, system_image=screen.IMAGE_ID, result_url=path, task_id=task_id,
                real_deep_link=True, interrupted_link_recovery=True, device_scoped_session=True, no_owner_login=True,
                cookie_loss_notification_device_return=True, open_result_action_tapped=True,
                completion_does_not_launch=True, record_reopened_after_process_death=True, record_view_reported=True,
                external_browser_intent=True, reopen_does_not_execute=True, models_called=0, evidence_dir=str(evidence))
    except Exception as exc:
        report["failure"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        try:
            if serial:
                (evidence / "android.log").write_text(screen.adb(serial, "logcat", "-d", timeout=15), encoding="utf-8")
                screenshot = subprocess.run([str(screen.ADB), "-s", serial, "exec-out", "screencap", "-p"],
                    capture_output=True, timeout=15, check=True)
                (evidence / "android-final.png").write_bytes(screenshot.stdout)
            (evidence / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        finally:
            for process in (frontend, external, server, None if args.keep_emulator else emulator):
                screen.stop_tree(process)
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
