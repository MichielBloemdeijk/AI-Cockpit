from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import logging
import sqlite3

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.config import settings
from app.db.session import run_migrations


def _use_database(database_path: Path) -> Config:
    settings.database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    return Config(settings.alembic_config_path)


def test_in_process_migrations_preserve_application_logging(caplog) -> None:
    app_logger = logging.getLogger("app.services.task_service")
    caplog.set_level(logging.INFO, logger=app_logger.name)
    handlers, level = list(logging.getLogger().handlers), logging.getLogger().level
    run_migrations()
    assert not app_logger.disabled
    assert logging.getLogger().handlers == handlers and logging.getLogger().level == level
    app_logger.info("migration_logging_fixture")
    assert "migration_logging_fixture" in caplog.text


def test_run_migrations_serializes_parallel_sqlite_startup(tmp_path: Path) -> None:
    database_path = tmp_path / "parallel.db"
    config = _use_database(database_path)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run_migrations) for _ in range(2)]
        for future in futures:
            future.result(timeout=30)

    with closing(sqlite3.connect(database_path)) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchall() == [
            (ScriptDirectory.from_config(config).get_current_head(),),
        ]


def test_baseline_feeds_recall_search_and_the_live_event_log(tmp_path: Path) -> None:
    # The FTS tables and triggers are raw SQLite DDL in the baseline, outside the ORM.
    database_path = tmp_path / "baseline.db"
    _use_database(database_path)
    run_migrations()
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            "INSERT INTO tasks(id, origin_surface, goal, idempotency_key) "
            "VALUES ('baseline-task', 'test', 'Water the basil', 'baseline-task')"
        )
        connection.execute(
            "INSERT INTO task_events(id, task_id, event_type, idempotency_key) "
            "VALUES ('baseline-event', 'baseline-task', 'task.queued', 'baseline-event')"
        )
        assert connection.execute(
            "SELECT virtual_path FROM recall_fts WHERE recall_fts MATCH 'basil' ORDER BY virtual_path"
        ).fetchall() == [("task/baseline-task/event/baseline-event.txt",), ("task/baseline-task/summary.txt",)]
        assert connection.execute(
            "SELECT idempotency_key FROM jarvis_live_events WHERE source_domain = 'task'"
        ).fetchall() == [("task:baseline-event",)]
        assert connection.execute("SELECT id FROM jarvis_preferences").fetchall() == [("current",)]
