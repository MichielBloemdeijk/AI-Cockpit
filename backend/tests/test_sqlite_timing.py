"""Opt-in native SQLite timings on project-provisioned disposable test data."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
from time import perf_counter_ns

import pytest

from app.config import settings
from test_admission_timing import summary


@pytest.mark.asyncio
@pytest.mark.diagnostic
async def test_profile_native_sqlite_commit_and_connection():
    destination = os.environ.get("JARVIS_OFFLINE_TIMING_REPORT")
    if not destination:
        pytest.skip("Set JARVIS_OFFLINE_TIMING_REPORT for native SQLite diagnostics")
    path = settings.database_path
    assert path is not None
    # This uses the same isolated migrated fixture database and inherited ACLs.
    # No live application data, provider, third-party patch or runtime thread.
    def measure(submitted):
        entered = perf_counter_ns()
        rows = []
        for index in range(63):
            started = perf_counter_ns()
            connection = sqlite3.connect(path, timeout=30)
            opened = perf_counter_ns()
            for statement in ("PRAGMA journal_mode=WAL", "PRAGMA foreign_keys=ON",
                "PRAGMA synchronous=FULL", "PRAGMA busy_timeout=30000"):
                connection.execute(statement).fetchall()
            configured = perf_counter_ns()
            connection.execute("CREATE TABLE IF NOT EXISTS diagnostic_commit_probe (id INTEGER PRIMARY KEY, value TEXT)")
            connection.execute("INSERT INTO diagnostic_commit_probe (value) VALUES ('metadata')")
            written = perf_counter_ns()
            connection.commit()
            committed = perf_counter_ns()
            connection.close()
            closed = perf_counter_ns()
            rows.append(dict(warmup=index < 3, open_ms=(opened-started)/1e6,
                setup_ms=(configured-opened)/1e6, statement_ms=(written-configured)/1e6,
                native_commit_ms=(committed-written)/1e6, close_ms=(closed-committed)/1e6,
                total_ms=(closed-started)/1e6))
        return rows, (entered-submitted)/1e6
    rows, dispatch = await asyncio.to_thread(measure, perf_counter_ns())
    target = Path(destination).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    selected = [row for row in rows if not row["warmup"]]
    target.with_name(f"{target.stem}-sqlite-native.json").write_text(json.dumps(dict(
        schema="offline-native-sqlite-timing.v1", failures=0, samples=rows,
        worker_dispatch_ms=dispatch, configuration="WAL synchronous=FULL, new connection per sample",
        summary={stage: summary([row[stage] for row in selected]) for stage in selected[0] if stage != "warmup"}), indent=2), encoding="utf-8")
