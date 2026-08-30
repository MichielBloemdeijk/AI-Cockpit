from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.config import settings  # noqa: E402
from app.db.session import close_engine, ensure_database_ready, run_migrations  # noqa: E402
from app.services.jarvis_benchmark import render_jarvis_benchmark, run_jarvis_benchmark  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run the complete local Jarvis model acceptance benchmark.")
    result.add_argument("--model", default=settings.local_llm_alias)
    result.add_argument("--performance-trials", type=int, default=5)
    result.add_argument("--output", help="JSON output path. Defaults under .cockpit/benchmarks.")
    return result


async def main() -> int:
    args = parser().parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = REPO_ROOT / ".cockpit" / "benchmarks" / stamp
    run_root.mkdir(parents=True, exist_ok=True)
    settings.database_url = f"sqlite+aiosqlite:///{(run_root / 'benchmark.db').as_posix()}"
    settings.conversation_workspaces_dir = str(run_root / "conversations")
    settings.verifier_generated_apps_dir = str(run_root / "generated-apps")
    await close_engine()
    await ensure_database_ready()
    run_migrations()

    report = await run_jarvis_benchmark(
        model=args.model,
        performance_trials=max(1, args.performance_trials),
        progress_callback=lambda message: print(message, flush=True),
    )
    print(render_jarvis_benchmark(report))
    output = Path(args.output).resolve() if args.output else run_root / "report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    print(f"Report: {output}")
    await close_engine()
    return 0 if report.overall_passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
