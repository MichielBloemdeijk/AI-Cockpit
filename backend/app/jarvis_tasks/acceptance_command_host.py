"""Contain one acceptance command and all of its Windows descendants."""
from __future__ import annotations

import argparse
import subprocess

from app.services.windows_job import contain_current_process_tree, exit_when_parent_exits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("A command is required")
    contain_current_process_tree()
    exit_when_parent_exits(args.parent_pid)
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
