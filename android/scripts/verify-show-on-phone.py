"""Unattended API37 acceptance for "show me the result" app switching.

Run with ``cd backend && uv run python ../android/scripts/verify-show-on-phone.py``.
Installs the debug APK as the default assistant, pairs it with a disposable
backend behind the real Next frontend (as Tailscale serves it) and puts Android
Settings in front. A fixture worker publishes a Document; the real show_on_phone
brain call must then bring Jarvis to the front on that record in the Documents
app over Settings, with no voice overlay showing; Back returns to Settings. On a
locked phone the same call must post the result notification and leave the lock
screen alone. The Live session row stands in for a connected call: no models or
paid calls.
"""
from __future__ import annotations

import harness_support  # noqa: F401  (isolates started backends from the personal .env)

import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import urlencode
from uuid import uuid4

import httpx


def load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


results = load("results", "verify-result-platform.py")
screen = results.screen
SETTINGS = "com.android.settings/"
WEBVIEW = f"{screen.PACKAGE}/.web.JarvisWebViewActivity"


def in_process(coroutine_factory):
    async def run():
        from app.db.session import close_engine
        try:
            return await coroutine_factory()
        finally:
            await close_engine()
    return asyncio.run(run())


def main() -> None:
    from app.config import settings
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-emulator", action="store_true")
    args = parser.parse_args()
    evidence = Path(tempfile.mkdtemp(prefix="jarvis-show-on-phone-"))
    print(f"Evidence: {evidence}")
    report: dict = {"status": "failed", "models_called": 0, "checks": {}}
    backend = frontend = emulator = serial = None
    try:
        subprocess.run([str(screen.ANDROID / "gradlew.bat"), ":app:assembleDebug", "--console=plain"],
            cwd=screen.ANDROID, check=True, timeout=900)
        port, web_port = screen.unused_port(), screen.unused_port()
        db_path = evidence / "fixture.db"
        settings.database_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
        env = {**os.environ, "DATABASE_URL": settings.database_url,
            "AUTH_PASSWORD": "", "JARVIS_ACCESS_MODE": "personal", "SESSION_COOKIE_SECURE": "false",
            # The phone's WebView writes come from the frontend's address.
            "JARVIS_PUBLIC_URL": f"http://10.0.2.2:{web_port}",
            "SESSION_SECRET_KEY": uuid4().hex, "OPENAI_API_KEY": "", "CODEX_API_KEY": "", "OPENROUTER_API_KEY": "",
            "LOCAL_LLM_ENABLED": "false", "WEB_PUSH_ENABLED": "false",
            # No task pool: the fixture worker below publishes the result itself.
            "TASK_POOL_ENABLED": "false", "TASK_ORCHESTRATION_ENABLED": "false",
            "TASK_ORCHESTRATION_LIVE_ENABLED": "false",
            **{key: str(evidence / key.lower()) for key in ("TASK_WORKSPACES_DIR", "TASK_CONTEXT_BLOBS_DIR",
                "CONVERSATION_WORKSPACES_DIR", "CODEX_PROFILES_DIR", "KNOWLEDGE_NOTES_DIR",
                "TASK_ORCHESTRATION_CONTEXT_DIR", "ANDROID_RELEASES_DIR")},
            "KNOWLEDGE_PREFERENCES_FILE": str(evidence / "preferences.yaml")}
        backend = screen.start_service(["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            env, evidence / "server.log")
        client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15)
        screen.wait_for("backend", lambda: client.get("/api/health").status_code == 200)
        client.post("/api/auth/login", json={"password": ""}).raise_for_status()
        # The phone talks to the frontend, which hosts /apps/<slug> and forwards /api to the backend.
        frontend = screen.start_frontend(web_port, f"http://127.0.0.1:{port}", evidence, production=True)
        screen.wait_for("frontend", lambda: httpx.get(f"http://127.0.0.1:{web_port}/login", timeout=60).status_code == 200,
            seconds=240)

        serial, emulator = screen.ensure_emulator()
        screen.wait_for("emulator host route", lambda: "src" in screen.adb(serial, "shell", "ip", "route", "get", "10.0.2.2"), seconds=60)
        # Earlier self-update runs leave a release-signed build the debug APK cannot replace.
        if screen.PACKAGE in screen.adb(serial, "shell", "pm", "list", "packages", screen.PACKAGE):
            screen.adb(serial, "uninstall", screen.PACKAGE)
        screen.adb(serial, "install", str(screen.ANDROID / "app/build/outputs/apk/debug/app-debug.apk"))
        # Backup restore can bring back a pairing to an earlier run's backend.
        screen.adb(serial, "shell", "pm", "clear", screen.PACKAGE)
        for permission in ("RECORD_AUDIO", "POST_NOTIFICATIONS", "ACCESS_LOCAL_NETWORK"):
            screen.adb(serial, "shell", "pm", "grant", screen.PACKAGE, f"android.permission.{permission}")
        screen.adb(serial, "shell", "cmd", "role", "add-role-holder", "android.app.role.ASSISTANT", screen.PACKAGE)
        screen.adb(serial, "shell", "input", "keyevent", "224"); screen.adb(serial, "shell", "wm", "dismiss-keyguard")

        code = client.post("/api/jarvis/devices/pairing-code").json()["code"]
        link = "jarvis://pair?" + urlencode({"server": f"http://10.0.2.2:{web_port}", "code": code})
        screen.adb(serial, "shell", "am", "start", "-W", "-a", "android.intent.action.VIEW", "-d", "'" + link + "'", "-p", screen.PACKAGE)
        def linked():
            texts = {node.get("text", "") for node in results.nodes(serial, evidence, "paired")}
            if "Default digital assistant app" in texts:
                screen.adb(serial, "shell", "input", "keyevent", "4")
                return False
            return "This phone is linked" in texts and f"http://10.0.2.2:{web_port}" in texts
        screen.wait_for("paired", linked, seconds=60)
        device_id = next(item["id"] for item in client.get("/api/jarvis/devices").json() if not item.get("revoked_at"))

        async def prepare():
            from app.db.session import session_scope
            from app.db.tables import Conversation, LiveSession, TaskResult
            from app.services.results.records import present
            from app.services.tasks.service import task_service
            from app.services.tasks.worker_coordinator import worker_coordinator
            from sqlalchemy import select
            created = await task_service.dispatch(goal="Write a short packing list", origin_surface="test",
                idempotency_key="show-on-phone-acceptance", result_request={"mode": "new"})
            # A fixture worker publishes the Document the way a real run does.
            worker = await worker_coordinator.activate(process_identity="show-on-phone-fixture-worker")
            claim = (await worker_coordinator.claim_next(worker.worker_id)).task
            assert claim is not None and claim.task_id == created.task.id
            await task_service.complete(created.task.id, summary="Packing list",
                final_text="PACKING-LIST-READY\n\n- Socks\n- Charger", idempotency_key="show-on-phone-publish",
                attempt_id=claim.attempt_id, execution_run_id=claim.execution_run_id, claim_token=claim.claim_token)
            await worker_coordinator.release_claim(worker.worker_id, claim.attempt_id, claim_token=claim.claim_token,
                writers_stopped=True)
            async with session_scope() as session:
                conversation = Conversation(title="Show on phone acceptance")
                session.add(conversation); await session.flush()
                live = LiveSession(conversation_id=conversation.id, provider_adapter="fake", lifecycle_state="ready",
                    controller_id=device_id, activation_id=str(uuid4()))
                session.add(live); await session.flush()
                result = (await session.execute(select(TaskResult).where(TaskResult.task_id == created.task.id))).scalar_one()
                return created.task.id, live.id, live.generation, present(result)["url"]
        task_id, session_id, generation, path = in_process(prepare)

        def show(call_id: str) -> dict:
            async def call():
                from app.services.jarvis.brain import restricted_brain_gateway
                result = await restricted_brain_gateway.execute(tool_name="show_on_phone",
                    arguments={"task_id": task_id}, call_id=call_id, conversation_id="show-on-phone",
                    origin_turn_id=None, session_id=session_id, generation=generation)
                return result.output
            return in_process(call)

        def resumed() -> str:
            return results.resumed_activity(results.foreground(serial)) or ""

        # Unlocked: another app in front, no Jarvis overlay showing.
        screen.adb(serial, "shell", "am", "start", "-W", "-a", "android.settings.SETTINGS")
        screen.wait_for("Settings in front", lambda: SETTINGS in resumed(), seconds=20)
        screen.adb(serial, "logcat", "-c")
        output = show("acceptance-unlocked")
        screen.wait_for("Jarvis switched to the result", lambda: WEBVIEW in resumed(), seconds=20)
        log = screen.adb(serial, "logcat", "-d", "-s", "JarvisPhoneDisplay:*")
        assert f"opened {path}" in log, log
        screen.wait_for("the Documents app showing the record", lambda: results.visible(
            serial, evidence, "PACKING-LIST-READY", "shown"), seconds=60)
        report["checks"]["unlocked"] = {"tool_output": output, "resumed": resumed(), "path": path}
        screen.adb(serial, "shell", "input", "keyevent", "4")
        try:
            screen.wait_for("Back returns to Settings", lambda: SETTINGS in resumed(), seconds=20)
        finally:
            report["checks"]["after_back"] = resumed()

        # Locked: the same request must become a notification, not a launch.
        screen.adb(serial, "shell", "input", "keyevent", "26")
        screen.wait_for("screen off", lambda: "mWakefulness=Asleep" in screen.adb(serial, "shell", "dumpsys", "power")
            or "mWakefulness=Dozing" in screen.adb(serial, "shell", "dumpsys", "power"), seconds=20)
        output = show("acceptance-locked")
        screen.wait_for("result notification", lambda: "Tap to open what you asked Jarvis to show." in
            screen.adb(serial, "shell", "dumpsys", "notification", "--noredact"), seconds=20)
        assert WEBVIEW not in resumed(), resumed()
        report["checks"]["locked"] = {"tool_output": output, "resumed": resumed()}
        screen.adb(serial, "shell", "input", "keyevent", "224"); screen.adb(serial, "shell", "wm", "dismiss-keyguard")
        report["status"] = "passed"
    finally:
        (evidence / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        screen.stop_tree(frontend)
        screen.stop_tree(backend)
        if not args.keep_emulator:
            screen.stop_tree(emulator)


if __name__ == "__main__":
    main()
