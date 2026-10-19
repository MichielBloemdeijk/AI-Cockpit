"""Self-test entry point.

    uv run python -m app.selftest offline
    uv run python -m app.selftest replay --name <run> --live [--case SUBSTRING ...]
    uv run python -m app.selftest run --suite core --name <run> --live [--case ID ...] [--android]

Paid stages require --live and stop on billing/quota/auth or evidence-integrity
failures. Each run directory has a connection allowance; nothing is retried
automatically.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import os
import subprocess
import sys

from app.selftest import harness as h
from app.selftest.review import review_run
from app.selftest.scenarios import load_suite, speech_texts

ALLOWANCE = {"browser": 16, "android": 12}
# Settings that must not leak from a personal .env into isolated checks.
TEST_ENV = {"SESSION_COOKIE_SECURE": "false", "AUTH_PASSWORD": "", "JARVIS_ACCESS_MODE": "personal",
    "JARVIS_EVAL_NETWORK_BLOCKED": "1", "TASK_POOL_ENABLED": "false", "TASK_ORCHESTRATION_ENABLED": "false",
    "TASK_ORCHESTRATION_LIVE_ENABLED": "false", "PYTHONUTF8": "1"}
JARVIS_TESTS = ["tests/test_conversation_observer.py", "tests/test_assistant_notes.py", "tests/test_selftest.py", "tests/test_jarvis_coordinator.py",
    "tests/test_live_admission_intents.py", "tests/test_model_reported_attention.py", "tests/test_openai_live_adapter.py",
    "tests/test_notification_context_floors.py", "tests/test_task_notification_preferences.py",
    "tests/test_task_notifications.py", "tests/test_task_service.py", "tests/test_task_workflows.py",
    "tests/test_orchestration_rollout.py", "tests/test_android_audio_harness.py"]


def offline(args) -> int:
    """Layer L0: network-blocked backend, frontend and Android checks plus a host probe."""
    env = {**os.environ, **TEST_ENV}
    checks = [
        ("backend", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *(args.tests or JARVIS_TESTS)], h.BACKEND),
        ("frontend", ["npx", "vitest", "run", "lib/jarvis-live.test.ts", "lib/jarvis-playback-evidence.test.ts"], h.FRONTEND),
        ("drivers", ["node", "--check", "e2e/announcement-eval.mjs"], h.FRONTEND),
        ("media", ["node", "e2e/announcement-offline.mjs"], h.FRONTEND),
        ("android", [str(h.ROOT / "android" / "gradlew.bat"), ":app:assembleDebug", ":app:testDebugUnitTest", "-q"], h.ROOT / "android"),
    ]
    results = {}
    for name, command, cwd in checks:
        if args.skip and name in args.skip:
            results[name] = "skipped"
            continue
        completed = subprocess.run(command, cwd=cwd, env=env, shell=os.name == "nt" and command[0] == "npx")
        results[name] = "passed" if completed.returncode == 0 else f"failed ({completed.returncode})"
    print(json.dumps(results, indent=2))
    return 0 if all(v in {"passed", "skipped"} for v in results.values()) else 1


def run(args) -> int:
    """Layers L2/L4: scripted voice scenarios through the real browser or Android client."""
    if not args.live:
        raise SystemExit("Voice scenarios use paid provider sessions; pass --live")
    scenarios = load_suite(args.suite)
    if args.case:
        unknown = set(args.case) - {s["id"] for s in scenarios}
        if unknown:
            raise SystemExit(f"Unknown scenario ids: {sorted(unknown)}")
        scenarios = [s for s in scenarios if s["id"] in args.case]
    output = h.run_root(args.name)
    transport = "android" if args.android else "browser"
    allowance = args.max_connections or ALLOWANCE[transport]
    speech = h.render_speech(speech_texts(scenarios), h.BASE / "speech")
    ledger = h.RequestLedger(output / "request-ledger.json")
    summary_path = output / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {"runs": []}
    overrides = {"JARVIS_CONVERSATION_OBSERVER_MODE": args.observer}
    for scenario in scenarios:
        used = sum(h.connections_used(d) for d in output.iterdir() if d.is_dir())
        needed = 2 if any(s["type"] in {"reconnect", "resume"} for s in scenario.get("steps", [])) else 1
        if used + needed > allowance:
            print(f"Connection allowance reached ({used}/{allowance}); stopping before {scenario['id']}")
            break
        folder = h.run_case(output, scenario, speech, android=args.android, env_overrides=overrides)
        evidence = json.loads((folder / "evidence.json").read_text(encoding="utf-8"))
        row = {"scenario": scenario["id"], "folder": folder.name, "transport": transport,
            "connections": h.connections_used(folder), "error": evidence.get("error"),
            "recording_complete": evidence.get("recording_complete")}
        summary["runs"].append(row)
        h.write(summary_path, summary)
        if evidence.get("paid_failure"):
            raise h.PaidFailure(evidence["paid_failure"])
        if not evidence.get("error") and evidence.get("recording_complete"):
            h.transcribe(folder, ledger)
            result = review_run(folder, scenario, ledger)
            row.update(verdict=result["verdict"], agreed=result.get("agreed"), summary=result["summary"])
            h.write(summary_path, summary)
        print(json.dumps(row))
    return 0


def replay(args) -> int:
    """Layer L1: re-assess recorded conversations with the current bookkeeper (paid Responses calls)."""
    if not args.live:
        raise SystemExit("Replay makes paid Responses calls; pass --live")
    import asyncio
    from app.selftest.replay import replay as run_replay
    summary = asyncio.run(run_replay(args.name, only=args.case))
    print(f"{summary['passed']}/{summary['total']} cases match their agreed truth")
    return 0 if summary["passed"] == summary["total"] else 1


def review(args) -> int:
    """Review an existing recorded run folder against a scenario's expectation (paid Responses)."""
    if not args.live:
        raise SystemExit("Review makes paid Responses calls; pass --live")
    folder = Path(args.folder).resolve()
    suite, _, case = args.scenario.partition(":")
    scenario = next(s for s in load_suite(suite) if s["id"] == case)
    if args.expect:
        scenario = {**scenario, "expect": args.expect}
    ledger = h.RequestLedger(folder / "review-ledger.json")
    print(json.dumps(review_run(folder, scenario, ledger), indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.selftest")
    commands = parser.add_subparsers(dest="command", required=True)
    o = commands.add_parser("offline", help="L0 network-blocked checks")
    o.add_argument("--tests", nargs="*", help="Backend test paths (default: Jarvis suites)")
    o.add_argument("--skip", nargs="*", choices=["backend", "frontend", "drivers", "media", "android"])
    o.set_defaults(handler=offline)
    l1 = commands.add_parser("replay", help="L1 bookkeeper replay of recorded conversations (paid Responses)")
    l1.add_argument("--name", required=True, help="Run directory under data/selftest")
    l1.add_argument("--case", nargs="*", help="Only cases whose folder contains one of these substrings")
    l1.add_argument("--live", action="store_true")
    l1.set_defaults(handler=replay)
    rv = commands.add_parser("review", help="Agent review of a recorded run folder (paid Responses)")
    rv.add_argument("--folder", required=True)
    rv.add_argument("--scenario", required=True, help="suite:id, e.g. core:requested_idle")
    rv.add_argument("--expect", help="Override the scenario expectation text")
    rv.add_argument("--live", action="store_true")
    rv.set_defaults(handler=review)
    r = commands.add_parser("run", help="L2/L4 scripted voice scenarios (paid)")
    r.add_argument("--suite", default="core")
    r.add_argument("--case", nargs="*")
    r.add_argument("--name", required=True, help="Run directory under data/selftest")
    r.add_argument("--android", action="store_true")
    r.add_argument("--observer", default="enabled", choices=["off", "shadow", "enabled"])
    r.add_argument("--max-connections", type=int)
    r.add_argument("--live", action="store_true")
    r.set_defaults(handler=run)
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
