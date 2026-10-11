"""Bounded online SQLite snapshot; never replace a source or existing backup.

This is a database-only building block, not a full workspace/profile backup.
Callers must quiesce writers and preserve referenced files before real recovery.
"""
from __future__ import annotations

from contextlib import closing
import os
from math import isfinite
from pathlib import Path
import sqlite3
from time import monotonic


def snapshot_database(source: Path, destination: Path, *, timeout_seconds: float = 10) -> dict:
    if destination.is_symlink():
        raise FileExistsError("Snapshot destination must not be a symlink")
    source, destination = source.resolve(), destination.resolve()
    if timeout_seconds <= 0 or not isfinite(timeout_seconds):
        raise ValueError("Snapshot timeout must be positive")
    if source == destination:
        raise ValueError("Snapshot destination must differ from its source")
    if not source.is_file():
        raise FileNotFoundError("Snapshot source does not exist")
    if any(Path(str(destination) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise FileExistsError("Snapshot destination has pre-existing SQLite sidecars")
    deadline = monotonic() + timeout_seconds
    # Exclusive creation also rejects existing files and symlink targets. Never
    # delete an incomplete artifact on failure: it may be useful for diagnosis.
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)

    def progress(status, remaining, total):
        if monotonic() >= deadline:
            raise TimeoutError("SQLite snapshot exceeded its deadline; destination is incomplete")

    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=.1)) as original:
        with closing(sqlite3.connect(destination, timeout=.1)) as backup:
            original.backup(backup, pages=128, progress=progress, sleep=.01)
            backup.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
            integrity = backup.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            foreign_keys = not backup.execute("PRAGMA foreign_key_check").fetchall()
            if not integrity or not foreign_keys:
                raise ValueError("Snapshot failed integrity or foreign-key validation")
    return {"integrity": "ok", "foreign_keys": "ok", "online_backup": True,
            "scope": "database_only", "source_opened_read_only": True}
