"""Unattended API37 acceptance for the in-app self-updater.

Run with ``cd backend && uv run python ../android/scripts/verify-self-update.py``.
Builds three release APKs signed with a disposable key, installs v1 over ADB,
pairs through the real link, then publishes v2 and v3 to a disposable backend.
The app must install each from the push event, keep its pairing and resume
wake listening. v2 may need Android's one-time confirmation (ADB was the
installer); v3 must install without any prompt. No models or paid calls.
Updates are spaced past Android's 30-second silent-update throttle.
"""
from __future__ import annotations

import harness_support  # noqa: F401  (isolates started backends from the personal .env)

import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlencode
from uuid import uuid4

import httpx


def load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


results = load("results", "verify-result-platform.py")
screen = results.screen
publisher = load("publisher", "publish-update.py")
signing = load("signing", "create-signing-key.py")
BASE_CODE = 100


def main() -> None:
    from app.config import settings
    from app.services import android_updates
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-emulator", action="store_true")
    args = parser.parse_args()
    evidence = Path(tempfile.mkdtemp(prefix="jarvis-self-update-"))
    print(f"Evidence: {evidence}")
    report: dict = {"status": "failed", "models_called": 0, "checks": {}}
    backend = emulator = serial = None
    try:
        os.environ["JARVIS_SIGNING_PROPERTIES"] = str(evidence / "signing/android-signing.properties")
        signing.create(Path(os.environ["JARVIS_SIGNING_PROPERTIES"]))
        apks = {}
        for offset in range(3):
            code = BASE_CODE + offset
            built = publisher.build(code, f"selftest-{code}")
            apks[code] = shutil.copyfile(built, evidence / f"jarvis-{code}.apk")
        subprocess.run([str(screen.ANDROID / "gradlew.bat"), ":screen-fixture:assembleDebug", "--console=plain"],
            cwd=screen.ANDROID, check=True, timeout=900)

        port = screen.unused_port()
        db_path = evidence / "fixture.db"
        settings.android_releases_dir = str(evidence / "releases")
        settings.database_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
        env = {**os.environ, "DATABASE_URL": settings.database_url, "ANDROID_RELEASES_DIR": settings.android_releases_dir,
            "AUTH_PASSWORD": "", "JARVIS_ACCESS_MODE": "personal", "SESSION_COOKIE_SECURE": "false",
            "SESSION_SECRET_KEY": uuid4().hex, "OPENAI_API_KEY": "", "CODEX_API_KEY": "", "OPENROUTER_API_KEY": "",
            "LOCAL_LLM_ENABLED": "false", "WEB_PUSH_ENABLED": "false",
            **{key: str(evidence / key.lower()) for key in ("TASK_WORKSPACES_DIR", "TASK_CONTEXT_BLOBS_DIR",
                "CONVERSATION_WORKSPACES_DIR", "CODEX_PROFILES_DIR", "KNOWLEDGE_NOTES_DIR",
                "TASK_ORCHESTRATION_CONTEXT_DIR")}, "KNOWLEDGE_PREFERENCES_FILE": str(evidence / "preferences.yaml")}
        backend = screen.start_service(["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)], env, evidence / "server.log")
        client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15)
        screen.wait_for("backend", lambda: client.get("/api/health").status_code == 200)
        client.post("/api/auth/login", json={"password": ""}).raise_for_status()

        def publish(code: int) -> None:
            async def run():
                from app.db.session import close_engine
                try:
                    await android_updates.publish(apks[code], version_code=code, version_name=f"selftest-{code}")
                finally:
                    await close_engine()
            asyncio.run(run())

        serial, emulator = screen.ensure_emulator()
        screen.wait_for("emulator host route", lambda: "src" in screen.adb(serial, "shell", "ip", "route", "get", "10.0.2.2"), seconds=60)
        # A debug build carries a different signature; the first install is the one manual step.
        if screen.PACKAGE in screen.adb(serial, "shell", "pm", "list", "packages", screen.PACKAGE):
            screen.adb(serial, "uninstall", screen.PACKAGE)
        screen.adb(serial, "install", str(apks[BASE_CODE]))
        screen.adb(serial, "install", "-r", str(screen.ANDROID / "screen-fixture/build/outputs/apk/debug/screen-fixture-debug.apk"))
        for permission in ("RECORD_AUDIO", "POST_NOTIFICATIONS", "ACCESS_LOCAL_NETWORK"):
            screen.adb(serial, "shell", "pm", "grant", screen.PACKAGE, f"android.permission.{permission}")
        screen.adb(serial, "shell", "cmd", "role", "add-role-holder", "android.app.role.ASSISTANT", screen.PACKAGE)
        for setting in ("assist_screenshot_enabled", "assist_structure_enabled"):
            screen.adb(serial, "shell", "settings", "put", "secure", setting, "1")
        # Stands in for the user's one-time "Install unknown apps" toggle.
        screen.adb(serial, "shell", "appops", "set", screen.PACKAGE, "REQUEST_INSTALL_PACKAGES", "allow")
        screen.adb(serial, "shell", "input", "keyevent", "224"); screen.adb(serial, "shell", "wm", "dismiss-keyguard")

        code = client.post("/api/jarvis/devices/pairing-code").json()["code"]
        link = "jarvis://pair?" + urlencode({"server": f"http://10.0.2.2:{port}", "code": code})
        screen.adb(serial, "shell", "am", "start", "-W", "-a", "android.intent.action.VIEW", "-d", "'" + link + "'", "-p", screen.PACKAGE)
        def linked():
            current = results.nodes(serial, evidence, "paired")
            texts = {node.get("text", "") for node in current}
            if "Default digital assistant app" in texts:
                # Guided setup may leave Android's assistant settings on top; setup itself is not under test.
                screen.adb(serial, "shell", "input", "keyevent", "4")
                return False
            return "This phone is linked" in texts
        screen.wait_for("paired", linked, seconds=60)
        screen.wait_for("Jarvis reports v1 up to date", lambda: results.visible(serial, evidence, "Jarvis is up to date.", "v1"), seconds=30)
        screen.adb(serial, "shell", "input", "keyevent", "3")

        def installed() -> int:
            match = re.search(r"versionCode=(\d+)", screen.adb(serial, "shell", "dumpsys", "package", screen.PACKAGE))
            return int(match.group(1)) if match else 0

        def update_to(code: int) -> dict:
            screen.adb(serial, "logcat", "-c")
            started = time.monotonic()
            publish(code)
            confirmed = False
            def done():
                nonlocal confirmed
                if installed() == code:
                    return True
                if not confirmed and "Install Jarvis update" in screen.adb(serial, "shell", "dumpsys", "notification", "--noredact"):
                    # Android's one-time confirmation for an app ADB installed.
                    screen.adb(serial, "shell", "cmd", "statusbar", "expand-notifications")
                    results.tap_text(serial, evidence, "Install Jarvis update")
                    results.tap_text(serial, evidence, "Update")
                    confirmed = True
                return False
            screen.wait_for(f"self-update to {code}", done, seconds=240)
            seconds = round(time.monotonic() - started, 1)
            screen.wait_for("wake listening resumed after update", lambda: "JarvisAudioService" in
                screen.adb(serial, "shell", "dumpsys", "activity", "services", screen.PACKAGE), seconds=30)
            (evidence / f"logcat-{code}.txt").write_text(screen.adb(serial, "logcat", "-d", "-s", "JarvisUpdate:*"), encoding="utf-8")
            screen.wait_for("updated notification", lambda: f"Now running selftest-{code}" in
                screen.adb(serial, "shell", "dumpsys", "notification", "--noredact"), seconds=30)
            return {"confirmation_required": confirmed, "seconds": seconds}

        report["checks"]["v2"] = update_to(BASE_CODE + 1)
        # Android's SilentUpdatePolicy allows one silent update per app every 30 s.
        time.sleep(35)
        report["checks"]["v3"] = update_to(BASE_CODE + 2)
        assert not report["checks"]["v3"]["confirmation_required"], "Second self-update still needed a confirmation"
        screen.adb(serial, "shell", "am", "start", "-W", "-n", f"{screen.PACKAGE}/.MainActivity")
        screen.wait_for("pairing survived updates", lambda: results.visible(serial, evidence, "This phone is linked", "after"), seconds=30)
        screen.wait_for("version shown", lambda: results.visible(serial, evidence, f"App version selftest-{BASE_CODE + 2}", "version"), seconds=30)
        report["status"] = "passed"
    finally:
        (evidence / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        screen.stop_tree(backend)
        if not args.keep_emulator:
            screen.stop_tree(emulator)


if __name__ == "__main__":
    main()
