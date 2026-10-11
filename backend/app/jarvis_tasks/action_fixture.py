from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
from typing import Any


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--effect", type=Path, required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--mode", choices=("complete", "hang-after-effect"), required=True)
    return parser


def _append(journal: Path, record: dict[str, Any]) -> None:
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, separators=(",", ":"), ensure_ascii=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    args = _parser().parse_args()
    _append(
        args.journal,
        {"operation_id": args.operation_id, "state": "started"},
    )
    args.effect.parent.mkdir(parents=True, exist_ok=True)
    with args.effect.open("w", encoding="utf-8") as handle:
        handle.write("effect")
        handle.flush()
        os.fsync(handle.fileno())
    if args.mode == "hang-after-effect":
        while True:
            time.sleep(0.1)
    _append(
        args.journal,
        {"operation_id": args.operation_id, "state": "succeeded"},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
