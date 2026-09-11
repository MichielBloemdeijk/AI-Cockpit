#!/usr/bin/env python3
"""Report persisted Jarvis Realtime v3 terminal records."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from datetime import datetime


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.jarvis_latency_report import (  # noqa: E402
    build_jarvis_latency_report,
    render_jarvis_latency_report,
)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _load_records(path: str | None) -> list[dict]:
    raw = json.load(sys.stdin if not path or path == "-" else Path(path).open(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("turns") or raw.get("records") or raw.get("results") or []
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise ValueError("Input must be a JSON array or an object containing turns/records/results")
    return raw


def main() -> int:
    parser = argparse.ArgumentParser(description="Report Jarvis Realtime v3 latency and reliability")
    parser.add_argument("input", nargs="?", help="JSON file, or -/omitted for stdin")
    parser.add_argument("--output", help="Write report JSON to this path")
    parser.add_argument("--configuration-id")
    parser.add_argument("--benchmark-run-id")
    parser.add_argument("--start", help="Inclusive ISO-8601 timestamp")
    parser.add_argument("--end", help="Inclusive ISO-8601 timestamp")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of the human report")
    args = parser.parse_args()

    report = build_jarvis_latency_report(
        _load_records(args.input),
        configuration_id=args.configuration_id,
        benchmark_run_id=args.benchmark_run_id,
        start=_parse_datetime(args.start),
        end=_parse_datetime(args.end),
    )
    serialized = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(serialized + "\n", encoding="utf-8")
    if args.json:
        print(serialized)
    else:
        print(render_jarvis_latency_report(report))
        if args.output:
            print(f"JSON: {Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

