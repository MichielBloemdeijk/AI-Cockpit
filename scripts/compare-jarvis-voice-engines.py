from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services.jarvis_voice_evaluation import (  # noqa: E402
    VoiceEvaluationObservation,
    evaluate_voice_engines,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare matched Realtime 2.1 and GPT-Live Jarvis observations."
    )
    parser.add_argument("input", type=Path, help="JSONL observations from matched voice runs")
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    args = parser.parse_args()

    observations = [
        VoiceEvaluationObservation.from_dict(json.loads(line))
        for line in args.input.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report = evaluate_voice_engines(observations)
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["overall_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
