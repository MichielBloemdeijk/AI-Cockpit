"""Credential-free native boundary diagnostics; never retries with full access."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time
from dataclasses import asdict

from openai_codex import Codex, CodexConfig
from app.jarvis_tasks.codex_probe import _sandbox_checks


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--mode", choices=["elevated", "unelevated"], default="unelevated")
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    profile = root / "profile"
    profile.mkdir(exist_ok=True)
    reports = []
    for iteration in range(2):
        work = root / f"task-{iteration}"
        work.mkdir(exist_ok=True)
        client = Codex(CodexConfig(env={"CODEX_HOME": str(profile)}, cwd=str(work),
            config_overrides=(f'windows.sandbox="{args.mode}"', "features.apps=false",
                              "features.plugins=false", "features.remote_plugin=false"),
            experimental_api=False, client_name="jarvis_native_boundary"))
        try:
            started = time.perf_counter()
            checks = _sandbox_checks(client, workspace=work, escape_target=root / f"escape-{iteration}")
            reports.append({"iteration": iteration, "duration_ms": (time.perf_counter()-started)*1000,
                "checks": [asdict(check) for check in checks]})
        finally:
            client.close()
        if any(check.status != "pass" for check in checks):
            break
    report = {"runtime": "0.155.1", "mode": args.mode, "provider_requests": 0,
              "passed": len(reports) == 2 and all(c["status"] == "pass" for r in reports for c in r["checks"]),
              "iterations": reports}
    (root / "boundary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
