from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import statistics
from xml.etree import ElementTree

from app.jarvis_tasks.codex_probe import run_codex_probe


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.jarvis_tasks")
    commands = parser.add_subparsers(dest="command", required=True)
    login = commands.add_parser("subscription-login", help="One-time sign-in to the persistent dedicated Jarvis ChatGPT profile.")
    login.add_argument("--device-code", action="store_true", help="Authorize from your usual browser using a one-time device code.")
    rollout = commands.add_parser("rollout-check", help="Read-only local preparation and explicit unresolved rollout gates (no provider calls).")
    rollout.add_argument("--report", type=Path, required=True)
    rollout.add_argument("--evidence", type=Path, help="Current candidate M10 acceptance report")
    acceptance = commands.add_parser("acceptance", help="Run candidate-bound M6–M10 closure checks; missing or skipped evidence blocks readiness.")
    acceptance.add_argument("--gate", choices=["M6", "M7", "M8", "M9", "M10"], default="M10")
    acceptance.add_argument("--report", type=Path, required=True, help="Fresh report directory")
    acceptance.add_argument("--evidence", action="append", default=[], metavar="ASSERTION=PATH")
    acceptance.add_argument("--live", action="store_true", help="Run authorized billable provider suites")
    acceptance.add_argument("--emulator", help="Already prepared API37 emulator serial")
    acceptance.add_argument("--audio-dir", type=Path, help="Rendered versioned WAV corpus")
    acceptance.add_argument("--database", type=Path, help="Prepared isolated emulator backend database")
    acceptance.add_argument("--backend-url", help="Prepared loopback backend for runtime candidate verification")
    rehearsal = commands.add_parser("rollout-rehearsal", help="Isolated database restore/restart, migration and disablement fixtures; never changes production.")
    rehearsal.add_argument("--report", type=Path, required=True)
    migration = commands.add_parser("migration-rehearsal", help="Read-only snapshot of configured SQLite DB; migrate/verify only a disposable clone.")
    migration.add_argument("--report", type=Path, required=True)
    validate = commands.add_parser("validate", help="Validate a versioned workflow without provider calls.")
    validate.add_argument("definition")
    fixtures = commands.add_parser("fixtures", help="Run isolated service fixtures for a reference workflow.")
    fixtures.add_argument("definition", choices=["recipe_from_screen@1", "research_screen@1"])
    fixtures.add_argument("--report", type=Path, required=True)
    evaluation = commands.add_parser("eval", help="Opt-in paid native, source and same-task revision samples.")
    evaluation.add_argument("definition", choices=["recipe_from_screen@1", "research_screen@1"])
    evaluation.add_argument("--live", action="store_true", required=True)
    evaluation.add_argument("--samples", type=int, choices=range(1, 5), default=2)
    evaluation.add_argument("--report", type=Path, required=True, help="Dedicated report directory")
    android = commands.add_parser("android", help="Unattended Windows API37 emulator acceptance")
    android_commands = android.add_subparsers(dest="android_command", required=True)
    demo = android_commands.add_parser("demo", help="Real screenshot, guarded Codex and notification-to-result navigation")
    demo.add_argument("--workflow", default="recipe_from_screen@1", choices=["screen_document@1", "recipe_from_screen@1", "research_screen@1"])
    demo.add_argument("--report", type=Path, required=True)
    demo.add_argument("--timeout", type=int, default=300)

    probe = commands.add_parser(
        "probe-codex",
        help="Run the opt-in M0 Codex runtime contract probe.",
    )
    probe.add_argument("--report", type=Path, required=True)
    probe.add_argument(
        "--live",
        action="store_true",
        help="Run billable model/session checks in addition to local runtime checks.",
    )
    probe.add_argument(
        "--profile-dir",
        type=Path,
        help="Dedicated Jarvis Codex profile. Defaults to a disposable isolated profile.",
    )
    probe.add_argument(
        "--keep-run-dir",
        action="store_true",
        help="Retain the disposable workspace for diagnostics (credentials are never reported).",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "acceptance":
        from app.jarvis_tasks.acceptance import run_acceptance
        try:
            evidence = {}
            for entry in args.evidence:
                identifier, path = entry.split("=", 1)
                if identifier in evidence:
                    raise ValueError("Duplicate evidence assertion")
                evidence[identifier] = Path(path)
            report = run_acceptance(gate=args.gate, output=args.report.resolve(), evidence=evidence, live=args.live,
                emulator=args.emulator, audio_dir=args.audio_dir, database=args.database, backend_url=args.backend_url)
        except (ValueError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(json.dumps({key: report[key] for key in ("status", "implementation_ready", "reasons")}, indent=2))
        return 0 if report["status"] == "passed" else 2
    if args.command == "subscription-login":
        from openai_codex import Codex, CodexConfig
        from app.services.runtime_auth_routes import subscription_profile
        profile = subscription_profile()
        desktop = (Path.home() / ".codex").resolve()
        if profile == desktop or profile.is_relative_to(desktop) or desktop.is_relative_to(profile):
            raise ValueError("Subscription login requires a separate Jarvis profile")
        profile.mkdir(parents=True, exist_ok=True)
        with Codex(CodexConfig(env={"CODEX_HOME": str(profile)}, experimental_api=False,
            config_overrides=('cli_auth_credentials_store="file"', 'forced_login_method="chatgpt"'),
            client_name="jarvis_subscription_login")) as client:
            handle = client.login_chatgpt_device_code() if args.device_code else client.login_chatgpt()
            instructions = ({"verification_url": handle.verification_url, "user_code": handle.user_code}
                if args.device_code else {"auth_url": handle.auth_url})
            print(json.dumps({**instructions, "profile": str(profile), "persistent": True}), flush=True)
            result = handle.wait()
            print(json.dumps({"success": result.success}), flush=True)
            return 0 if result.success else 1
    if args.command == "rollout-check":
        from app.config import settings
        from app.jarvis_tasks.rollout_check import inspect_rollout
        report = inspect_rollout(settings, acceptance_path=args.evidence)
        if not _write_rollout_report(args.report, report):
            return 1
        print(json.dumps({key: report[key] for key in ("status", "preparation_status", "ready_for_enablement")}, indent=2))
        return 0 if report["status"] == "passed" else 2
    if args.command == "rollout-rehearsal":
        from app.jarvis_tasks.rollout_check import run_rehearsal
        report = run_rehearsal()
        if not _write_rollout_report(args.report, report):
            return 1
        print(json.dumps({key: report[key] for key in ("status", "tests_passed", "ready_for_enablement")}, indent=2))
        return 0 if report["status"] == "passed" else 1
    if args.command == "migration-rehearsal":
        from app.config import settings
        from app.jarvis_tasks.rollout_check import migration_rehearsal
        report = migration_rehearsal(settings)
        if not _write_rollout_report(args.report, report):
            return 1
        print(json.dumps({key: report[key] for key in ("status", "ready_for_enablement")}, indent=2))
        return 0 if report["status"] == "passed" else 1
    if args.command == "android":
        script = Path(__file__).resolve().parents[3] / "android/scripts/verify-screen-workflow.py"
        return subprocess.run([sys.executable, str(script), "--workflow", args.workflow,
            "--report", str(args.report.resolve()), "--timeout", str(args.timeout)],
            cwd=Path(__file__).resolve().parents[2]).returncode
    if args.command == "eval":
        folder = args.report.resolve()
        folder.mkdir(parents=True, exist_ok=True)
        if list(folder.glob("*-*.json")):
            print("Use a fresh report directory to avoid mixing evaluation samples", file=sys.stderr)
            return 1
        env = {**os.environ, "JARVIS_M4_LIVE": "1", "JARVIS_M4_SAMPLES": str(args.samples),
               "JARVIS_M4_REPORT_DIR": str(folder)}
        completed = subprocess.run([sys.executable, "-m", "pytest", "tests/test_m4_workflows_live.py",
            "-k", args.definition.split("@")[0], "-q", "--tb=short"],
            cwd=Path(__file__).resolve().parents[2], env=env)
        reports = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("*-*.json"))]
        timings = {}
        for field in ("cold_execution_seconds", "ready_followup_seconds"):
            values = sorted(report[field] for report in reports if field in report)
            timings[field] = {"sample_count": len(values), "median": statistics.median(values) if values else None,
                             "p95": values[math.ceil(.95 * len(values)) - 1] if values else None,
                             "individual_seconds": values}
        summary = {"definition": args.definition, "status": "passed" if completed.returncode == 0 and len(reports) == args.samples else "failed",
                   "requested_samples": args.samples, "observed_samples": len(reports), "latency": timings,
                   "usage": {"native": [report.get("native_usage") for report in reports],
                             "search": [report.get("search_usage") for report in reports]},
                   "cost_status": "provider_invoice_cost_unavailable", "production_latency_guarantee": False,
                   "native_usage_counting": "Cumulative thread snapshots (total) and last model-call snapshots (last); do not sum resumed-thread cumulative totals across attempts.",
                   "sampling": "Fresh task/native profile, then retained same-task profile for the ready follow-up; each attempt starts an App Server.",
                   "quality_method": "Schema/evidence/revision assertions plus retained-source agent review; schema checks alone do not establish semantic correctness."}
        (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return 0 if summary["status"] == "passed" else 1
    if args.command in {"validate", "fixtures"}:
        from app.services.task_workflows import load_workflow
        try:
            loaded = load_workflow(args.definition)
        except (ValueError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        metadata = {"definition": args.definition, "digest": loaded.digest,
                    "input_schema": loaded.input_schema, "output_schema": loaded.output_schema,
                    "capabilities": list(loaded.capabilities), "purpose": loaded.purpose,
                    "required_initial_context": list(loaded.required_initial_context)}
        if args.command == "validate":
            print(json.dumps(metadata, indent=2))
            return 0
        backend = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(prefix="jarvis-fixture-report-") as folder:
            junit = Path(folder) / "results.xml"
            completed = subprocess.run([sys.executable, "-m", "pytest", "tests/test_m4_workflows.py",
                "tests/test_worker_tool_policy.py", "-k", f"{loaded.id} or registry or selection or connector",
                "-q", "--tb=short", f"--junitxml={junit}"], cwd=backend, capture_output=True, text=True)
            suites = ElementTree.parse(junit).getroot() if junit.exists() else None
            checks = [{"name": case.attrib.get("name"), "status": "failed" if case.find("failure") is not None or case.find("error") is not None else
                       "skipped" if case.find("skipped") is not None else "passed"}
                      for case in suites.iter("testcase")] if suites is not None else []
        report = metadata | {"status": "passed" if completed.returncode == 0 and checks else "failed",
                             "checks": checks, "provider_calls": 0, "isolated": True}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(completed.stdout)
        if completed.returncode:
            print(completed.stderr, file=sys.stderr)
        return 0 if report["status"] == "passed" else 1
    if args.command == "probe-codex":
        return run_codex_probe(
            report_path=args.report,
            live=args.live,
            profile_dir=args.profile_dir,
            keep_run_dir=args.keep_run_dir,
        )
    raise AssertionError(f"Unhandled command: {args.command}")


def _write_rollout_report(path: Path, report: dict) -> bool:
    from app.jarvis_tasks.rollout_check import write_rollout_report
    try:
        write_rollout_report(path, report)
        return True
    except (ValueError, OSError):
        print("Cannot write rollout report: use a fresh .json path or this command's existing report.", file=sys.stderr)
        return False


if __name__ == "__main__":
    raise SystemExit(main())
