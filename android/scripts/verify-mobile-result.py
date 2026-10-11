"""M8 real spoken research admission -> worker -> native result -> UI follow-up.

Called by verify-managed-admission --mobile-result in its owned API37 fixture.
No transcript, model call, workflow output or screenshot is injected here.
"""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
from xml.etree import ElementTree

import httpx


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def result_snapshot(client, task_id, *, failure_type=RuntimeError):
    task = client.get(f"/api/tasks/{task_id}")
    task.raise_for_status()
    detail = task.json()
    record = detail.get("task", detail)
    if record["status"] in {"failed", "interrupted", "cancelled"}:
        raise failure_type(f"Real workflow ended {record['status']}: {record.get('terminal_summary')}")
    if record["status"] == "waiting_for_input" and any(
            question.get("state") == "asked" for question in detail.get("questions", [])):
        raise failure_type("Research fixture unexpectedly requested clarification")
    results = detail.get("results", [])
    if record["status"] != "completed" or not results:
        return None
    result = client.get(f"/api/results/{results[0]['public_id']}")
    result.raise_for_status()
    value = result.json()
    return value if value["status"] == "ready" else None


def run(screen, serial, database, backend_url, render_dir, folder, configuration, env, *, gate):
    from app.jarvis_tasks.acceptance import SCHEMA, candidate_identity, validate_observation
    from app.config import settings
    # The parent launcher owns a declared isolated profile. Bind this proof to
    # the same profile as its backend, then restore the caller's settings.
    original = {key: getattr(settings, key) for key in configuration["settings"]}
    for key, value in configuration["settings"].items():
        setattr(settings, key, value)
    candidate = candidate_identity()
    audio = module("mobile_audio", "verify-audio-corpus.py")
    ui = module("mobile_ui", "verify-result-platform.py")
    proof, status = {}, "failed"
    try:
        runtime = audio.backend_attestation(backend_url, candidate, database)
        cases = audio.prepare(render_dir, folder / "pcm")
        sdk = Path(env["LOCALAPPDATA"]) / "Android/Sdk/platform-tools/adb.exe"
        sample = audio.run_sample([str(sdk), "-s", serial], database, folder / "pcm",
            {**next(case for case in cases if case["id"] == "ready04"),
                "subject": "Research topic: Ada Lovelace and early computing."}, "ready", 45)
        (folder / "admission.json").write_text(json.dumps(sample, indent=2), encoding="utf-8")
        if sample["status"] != "passed":
            raise RuntimeError("Real research voice admission failed")
        task_id = sample["task_id"]
        def uploads_ready():
            with sqlite3.connect(database) as db:
                rows = db.execute("SELECT state FROM task_context_requests WHERE execution_run_id=?",
                    (sample["execution_run_id"],)).fetchall()
            if any(row[0] in {"failed", "expired"} for row in rows):
                raise screen.FixtureFailed("Research capture upload failed")
            return rows and all(row[0] == "ready" for row in rows)
        screen.wait_for("promised research images", uploads_ready, seconds=15)
        # The durable worker no longer needs the spoken session. Close it while
        # the worker runs, avoiding idle paid voice time during publication/UI checks.
        screen.adb(serial, "shell", "input", "keyevent", "4")
        with httpx.Client(base_url=backend_url, timeout=10) as client:
            client.post("/api/auth/login", json={"password": ""}).raise_for_status()
            print("Mobile result: waiting for sourced worker publication", flush=True)
            first = screen.wait_for("real research result", lambda: result_snapshot(client, task_id,
                failure_type=screen.FixtureFailed), seconds=240)
            if first["result_kind"] != "document.v1" or not first["content"].get("sources"):
                raise RuntimeError("Research worker did not publish a sourced document")
            if "ada" not in json.dumps(first["content"]).lower():
                raise RuntimeError("Research result does not describe the visible topic")
            path = first["url"]
            with sqlite3.connect(database) as db:
                initial_run = db.execute("SELECT current_run_id FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
                bindings = [json.loads(row[0]) for row in db.execute(
                    "SELECT content_json FROM task_context_bundles WHERE task_id=?", (task_id,))]
                # Admission owns labeled context independently of content dedup.
                roles = {image["role"] for bundle in bindings for image in
                    (bundle.get("screen_context") or {}).get("images", [])}
                notification = screen.wait_for("delivered result notification", lambda:
                    db.execute("SELECT id FROM notification_outbox WHERE task_id=? AND event_kind='task.completed' "
                        "AND status='delivered' AND deep_link=?", (task_id, path)).fetchone(), seconds=30)
                run_count = db.execute("SELECT COUNT(*) FROM task_execution_runs WHERE task_id=?", (task_id,)).fetchone()[0]
            print("Mobile result: opening real Android notification", flush=True)
            screen.adb(serial, "shell", "cmd", "statusbar", "expand-notifications")
            action = screen.wait_for("native Open result action", lambda: ui.notification_action(serial, folder), seconds=30)
            screen.tap_node(serial, action)
            screen.wait_for("published document in WebView", lambda: ui.visible(serial, folder, "Ada", "result"), seconds=45)
            screen.adb(serial, "shell", "am", "force-stop", screen.PACKAGE)
            screen.adb(serial, "shell", "am", "start", "-f", "0x10008000", "-n",
                f"{screen.PACKAGE}/.assistant.CaptureFixtureActivity", "--es", "open_result_path", path)
            screen.wait_for("reopened document after process loss", lambda: ui.visible(serial, folder, "Ada", "reopened"), seconds=45)
            with sqlite3.connect(database) as db:
                if db.execute("SELECT COUNT(*) FROM task_execution_runs WHERE task_id=?", (task_id,)).fetchone()[0] != run_count:
                    raise RuntimeError("Reopening a result created another execution run")
            # This drives the real WebView continuation form, not the task API.
            screen.adb(serial, "shell", "am", "start", "-f", "0x10008000", "-n",
                f"{screen.PACKAGE}/.assistant.CaptureFixtureActivity", "--es", "open_result_path", f"/tasks/{task_id}")
            field = screen.wait_for("task follow-up form", lambda: next((node for node in ui.nodes(serial, folder, "followup")
                if node.get("class") == "android.widget.EditText"), None), seconds=45)
            intent = screen.wait_for("follow-up result intent", lambda: next((node for node in ui.nodes(serial, folder, "intent")
                if node.get("clickable") == "true" and node.get("text", "").startswith("Answer only")), None), seconds=15)
            screen.tap_node(serial, intent)
            ui.tap_text(serial, folder, "Edit selected result")
            screen.wait_for("selected edit intent", lambda: ui.visible(serial, folder, "Edit selected result", "edit-selected"), seconds=15)
            screen.tap_node(serial, field)
            screen.adb(serial, "shell", "input", "text", "Add%sa%sshort%sfinal%ssummary%susing%sthe%sretained%ssources")
            screen.adb(serial, "shell", "input", "keyevent", "4")
            ui.tap_text(serial, folder, "Send")
            print("Mobile result: waiting for UI-originated same-task continuation", flush=True)
            def updated():
                with sqlite3.connect(database) as db:
                    current = db.execute("SELECT current_run_id FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
                if current == initial_run:
                    return None
                page = result_snapshot(client, task_id, failure_type=screen.FixtureFailed)
                if page and page["revision_number"] <= first["revision_number"]:
                    raise screen.FixtureFailed("Edit follow-up completed without revising the result")
                return page if page and page["revision_number"] > first["revision_number"] else None
            final = screen.wait_for("continued result publication", updated, seconds=240)
            with sqlite3.connect(database) as db:
                followup_run = db.execute("SELECT current_run_id FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
            proof = {"adapter": "openai_live", "workflow": "research_screen@1", "custom_wake": True,
                "real_notification_delivered": bool(notification), "notification_action_tapped": True,
                "reopened_result": True, "no_extra_run_on_reopen": True, "ui_followup": True,
                "same_task_continuation": final["public_id"] == first["public_id"] and followup_run != initial_run,
                "both_screen_roles": roles >= {"invocation", "latest"}, "source_count": len(first["content"]["sources"]),
                "task_id": task_id, "initial_run_id": initial_run, "followup_run_id": followup_run,
                "public_id": first["public_id"], "initial_result": first, "final_result": final,
                "admission_latency_ms": sample.get("latency_ms")}
            error = validate_observation("mobile_result", proof)
            if error:
                raise RuntimeError(error)
            if audio.backend_attestation(backend_url, candidate, database) != runtime:
                raise RuntimeError("Backend restarted during mobile result acceptance")
            status = "passed"
    except Exception as error:
        proof["failure"] = f"{type(error).__name__}: {error}"
        print(proof["failure"], flush=True)
    finally:
        if candidate_identity() != candidate:
            status = "failed"
            proof["candidate_stability"] = "Source/configuration changed"
        (folder / "acceptance.json").write_text(json.dumps({"schema": SCHEMA, "gate": gate, "candidate": candidate,
            "test_configuration": configuration, "finished_at": datetime.now(timezone.utc).isoformat(),
            "checks": [{"id": "mobile_result", "status": status, "evidence": proof}]}, indent=2), encoding="utf-8")
        for key, value in original.items():
            setattr(settings, key, value)
    return 0 if status == "passed" else 2
