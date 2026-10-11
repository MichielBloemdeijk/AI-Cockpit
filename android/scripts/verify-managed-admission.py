"""Run managed GPT-Live task admission against an isolated API37 backend."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.jarvis_tasks.acceptance import resolve_test_configuration, run_acceptance


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--render-dir", type=Path, required=True)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--pilot", action="store_true", help="Run at most three selected ready cases stopping on failure or latency >=4500 ms")
    parser.add_argument("--model-diagnostics", action="store_true", help="Pilot-only provider output streams, stored locally without changing reasoning settings")
    parser.add_argument("--reasoning-summary", action="store_true", help="Also request reasoning summaries; requires model diagnostics and changes the measured request")
    parser.add_argument("--capture-performance", action="store_true")
    parser.add_argument("--performance-samples", type=int, choices=range(1, 21), default=20,
        help="Capture diagnostic count; full acceptance always requires twenty")
    parser.add_argument("--mobile-result", action="store_true", help="Real custom-wake workflow, result notification/reopen and UI follow-up")
    parser.add_argument("--gate", choices=["M8", "M10"], default="M10")
    parser.add_argument("--acceptance", action="store_true", help="Run combined M10 against a prepared isolated backend; managed delegation only.")
    parser.add_argument("--evidence", action="append", default=[], metavar="ASSERTION=PATH")
    args = parser.parse_args()
    if args.model_diagnostics and not args.pilot:
        parser.error("Full model diagnostics are restricted to explicit diagnostic pilots")
    if args.reasoning_summary and not args.model_diagnostics:
        parser.error("Reasoning summaries require model diagnostics")
    if args.pilot:
        audio_spec = importlib.util.spec_from_file_location("audio", Path(__file__).with_name("verify-audio-corpus.py"))
        audio = importlib.util.module_from_spec(audio_spec)
        audio_spec.loader.exec_module(audio)
        audio.validate_pilot_cases(args.case)
        if args.acceptance or args.capture_performance or args.mobile_result:
            parser.error("Pilot cannot run combined acceptance or capture performance")
    if args.acceptance and (args.case or args.capture_performance or args.mobile_result or args.performance_samples != 20):
        parser.error("Combined acceptance requires no partial corpus/performance selection")
    if args.mobile_result and (args.case or args.capture_performance):
        parser.error("Mobile result uses its fixed real workflow sequence")
    output = args.report.resolve()
    if not output.is_relative_to(ROOT / "data") or output.exists():
        raise ValueError("Use a fresh project-owned data report directory")
    output.mkdir(parents=True)
    spec = importlib.util.spec_from_file_location("screen", Path(__file__).with_name("verify-screen-workflow.py"))
    screen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(screen)
    serial, emulator = screen.ensure_emulator()
    try:
        for apk in (ROOT / "android/app/build/outputs/apk/debug/app-debug.apk",
                    ROOT / "android/screen-fixture/build/outputs/apk/debug/screen-fixture-debug.apk"):
            screen.adb(serial, "install", "-r", str(apk))
        package = "nl.sander.aicockpit.jarvis"
        for permission in ("android.permission.RECORD_AUDIO", "android.permission.POST_NOTIFICATIONS", "android.permission.ACCESS_LOCAL_NETWORK"):
            screen.adb(serial, "shell", "pm", "grant", package, permission)
        screen.adb(serial, "shell", "cmd", "role", "add-role-holder", "android.app.role.ASSISTANT", package)
        screen.wait_for("assistant role", lambda: "mComponent=" + package in screen.adb(serial, "shell", "dumpsys", "voiceinteraction"), seconds=45)
        screen.adb(serial, "shell", "settings", "put", "secure", "assist_screenshot_enabled", "1")
        folder = output
        database, port = folder / "fixture.db", screen.unused_port()
        configuration = resolve_test_configuration("mobile_emulator" if args.mobile_result else "emulator", paths={"codex_profiles_dir": str(folder / "profiles"),
            "task_workspaces_dir": str(folder / "workspaces"), "task_context_blobs_dir": str(folder / "blobs"),
            "conversation_workspaces_dir": str(folder / "conversations")}, browser_origin=f"http://10.0.2.2:{port}")
        env = {**os.environ, **{key.upper(): str(value).lower() if isinstance(value, bool) else str(value)
            for key, value in configuration["settings"].items()}, "DATABASE_URL": f"sqlite+aiosqlite:///{database.as_posix()}",
            "AUTH_PASSWORD": "", "JARVIS_ACCESS_MODE": "personal", "SESSION_SECRET_KEY": uuid4().hex, "LOCAL_LLM_ENABLED": "false",
            "OPENAI_LIVE_DIAGNOSTICS_DIR": str(folder / "model-outputs") if args.model_diagnostics else "",
            "OPENAI_LIVE_REASONING_SUMMARY": str(args.reasoning_summary).lower(), "SESSION_COOKIE_SECURE": "false"}
        server = screen.start_service(["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)], env, folder / "server.log")
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
                screen.wait_for("backend", lambda: client.get("/api/auth/status").status_code == 401, seconds=90)
            screen.adb(serial, "logcat", "-c")
            screen.adb(serial, "shell", "am", "start", "-f", "0x10008000", "-n", f"{package}/.assistant.CaptureFixtureActivity",
                "--es", "server_url", f"http://10.0.2.2:{port}", "--ez", "probe_mode", "true",
                *(["--es", "subject", "Ada_Lovelace_and_early_computing"] if args.mobile_result else []))
            screen.wait_for("paired debug fixture", lambda: "probe_ready" in screen.adb(serial, "logcat", "-d", "-s", "JarvisCaptureFixture:I"), seconds=45)
            if args.mobile_result:
                mobile_spec = importlib.util.spec_from_file_location("mobile", Path(__file__).with_name("verify-mobile-result.py"))
                mobile = importlib.util.module_from_spec(mobile_spec)
                mobile_spec.loader.exec_module(mobile)
                return mobile.run(screen, serial, database, f"http://127.0.0.1:{port}", args.render_dir.resolve(), folder,
                    configuration, env, gate=args.gate)
            if args.acceptance:
                supplied = {}
                for entry in args.evidence:
                    identifier, path = entry.split("=", 1)
                    if identifier in supplied: raise ValueError("Duplicate evidence assertion")
                    supplied[identifier] = Path(path)
                report = run_acceptance(gate=args.gate, output=folder / "combined", evidence=supplied,
                    live=True, emulator=serial, audio_dir=args.render_dir.resolve(), database=database,
                    backend_url=f"http://127.0.0.1:{port}", emulator_configuration=configuration)
                print(json.dumps({key: report[key] for key in ("status", "implementation_ready", "reasons")}), flush=True)
                return 0 if report["status"] == "passed" else 2
            command = [sys.executable, str(Path(__file__).with_name("verify-audio-corpus.py")), "--live", "--serial", serial,
                "--render-dir", str(args.render_dir.resolve()), "--database", str(database), "--backend-url", f"http://127.0.0.1:{port}",
                "--gate", args.gate, "--report", str(folder / "audio")]
            command.append("--pilot" if args.pilot else "--prepare-scenarios")
            if args.pilot:
                command.extend(["--timeout", "30"])
            if args.model_diagnostics:
                command.extend(["--diagnostic-tail-seconds", "5"])
            for case in args.case:
                command.extend(["--case", case])
            if args.capture_performance:
                command.append("--capture-performance")
                command.extend(["--performance-samples", str(args.performance_samples)])
            with (folder / "audio.log").open("w") as log:
                subprocess.run(command, env=env, cwd=ROOT / "backend", stdout=log, stderr=subprocess.STDOUT, check=False)
            report = json.loads((folder / "audio/acceptance.json").read_text())
            if args.model_diagnostics:
                samples = next((row["evidence"]["samples"] for row in report["checks"] if row["id"] == "ready_latency"), [])
                audio.close_observed_sessions(f"http://127.0.0.1:{port}", report["candidate"], database,
                    report["backend_runtime"], samples)
                owners = {sample["session_id"] for sample in samples if sample.get("session_id")}
                def captured_owners():
                    return {json.loads(path.read_text(encoding="utf-8"))["session_id"] for path in (folder / "model-outputs").glob("*.json")}
                try:
                    screen.wait_for("closed model diagnostic traces", lambda: owners.issubset(captured_owners()), seconds=8)
                    flush_status = "observed"
                except TimeoutError:
                    flush_status = "missing"
            report["test_configuration"] = configuration
            if args.model_diagnostics:
                report["model_diagnostics"] = {"directory": str(folder / "model-outputs"), "reasoning_summary_requested": args.reasoning_summary,
                    "flush_status": flush_status, "maximum_tail_seconds": 5,
                    "acceptance_eligible": False, "latency_caveat": "Summary generation changes the measured request" if args.reasoning_summary else "Local output recording enabled; pilot sample count is not acceptance"}
            (folder / "audio/acceptance.json").write_text(json.dumps(report, indent=2))
            ready = next((check for check in report["checks"] if check["id"] == "ready_latency"), {})
            functional = {check["id"]: check["status"] for check in report["checks"]
                if check["id"] in {"audio_corpus", "cold_latency", "fresh_screen", "voice_provider"}}
            summary = {"route": "managed_responses", "candidate": report["candidate"], "ready": ready,
                "functional": functional, "report": str(folder / "audio/acceptance.json"),
                "acceptance_claim": not args.pilot and report.get("status") == "passed"}
            (output / "summary.json").write_text(json.dumps(summary, indent=2))
            print(json.dumps({"route": "managed_responses", "statistics": ready.get("evidence", {}).get("statistics")}), flush=True)
        finally:
            screen.adb(serial, "shell", "am", "broadcast", "-n", f"{package}/.assistant.AudioAcceptanceReceiver", "--ez", "clear", "true")
            screen.stop_tree(server)
    finally:
        screen.stop_tree(emulator)
    # Diagnostic pilots never establish the full performance/functional gate.
    return 0 if not args.pilot and report["checks"] and all(check["status"] == "passed" for check in report["checks"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
