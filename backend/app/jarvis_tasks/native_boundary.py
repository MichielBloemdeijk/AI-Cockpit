"""Credential-free native boundary diagnostics; never retries with full access."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time
from dataclasses import asdict

from openai_codex import Codex
from app.services.codex_native import CODEX_RUNTIME_PIN, DISABLED_EXTENSIONS, client_config, sandbox_checks


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
        client = Codex(client_config(profile, workspace=work, overrides=DISABLED_EXTENSIONS,
            windows_sandbox=args.mode, client_name="jarvis_native_boundary"))
        try:
            started = time.perf_counter()
            checks = sandbox_checks(client, workspace=work, escape_target=root / f"escape-{iteration}")
            reports.append({"iteration": iteration, "duration_ms": (time.perf_counter()-started)*1000,
                "checks": [asdict(check) for check in checks]})
        finally:
            client.close()
        if any(check.status != "pass" for check in checks):
            break
    report = {"runtime": CODEX_RUNTIME_PIN, "mode": args.mode, "provider_requests": 0,
              "passed": len(reports) == 2 and all(c["status"] == "pass" for r in reports for c in r["checks"]),
              "iterations": reports}
    (root / "boundary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
