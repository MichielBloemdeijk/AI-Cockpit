"""Harmless descendant writer used only by the M0 containment probe."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time


def _child(output: Path) -> None:
    while True:
        with output.open("a", encoding="utf-8") as stream:
            stream.write(f"{time.monotonic_ns()}\n")
            stream.flush()
        time.sleep(0.04)


def _parent(output: Path, pid_file: Path) -> None:
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app.jarvis_tasks.writer_fixture",
            "--child",
            "--output",
            str(output),
        ],
        cwd=str(output.parent),
    )
    pid_file.write_text(str(child.pid), encoding="ascii")
    try:
        child.wait()
    finally:
        if child.poll() is None:
            child.kill()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pid-file", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.child:
        _child(args.output)
        return 0
    if args.pid_file is None:
        parser.error("--pid-file is required for the parent writer")
    args.pid_file.write_text(str(os.getpid()), encoding="ascii")
    _parent(args.output, args.pid_file.with_suffix(".child.pid"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
