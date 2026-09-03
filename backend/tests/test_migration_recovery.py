from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.config import settings
from app.db.session import run_migrations


def _config(database_path: Path) -> Config:
    settings.database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    config = Config(settings.alembic_config_path)
    return config


def test_situation_health_migration_resumes_after_partial_sqlite_ddl(tmp_path: Path) -> None:
    database_path = tmp_path / "partial.db"
    config = _config(database_path)
    command.upgrade(config, "20260902_0014")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "ALTER TABLE jarvis_state ADD COLUMN situation_revision INTEGER DEFAULT '0' NOT NULL"
        )
        connection.execute("CREATE TABLE _alembic_tmp_tasks AS SELECT * FROM tasks")

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        inspector = inspect(engine)
        assert {column["name"] for column in inspector.get_columns("jarvis_state")} >= {
            "situation_revision",
            "conversation_summary",
        }
        assert "status_reason" in {
            column["name"] for column in inspector.get_columns("tasks")
        }
        assert "runtime_component_health" in inspector.get_table_names()
        assert "_alembic_tmp_tasks" not in inspector.get_table_names()
        status_check = next(
            constraint
            for constraint in inspector.get_check_constraints("tasks")
            if constraint.get("name") == "ck_tasks_status"
        )
        assert "waiting_for_worker" in str(status_check.get("sqltext"))
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260902_0015"
    finally:
        engine.dispose()


def test_run_migrations_serializes_parallel_sqlite_startup(tmp_path: Path) -> None:
    database_path = tmp_path / "parallel.db"
    config = _config(database_path)
    command.upgrade(config, "20260902_0014")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run_migrations) for _ in range(2)]
        for future in futures:
            future.result(timeout=30)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "20260902_0015",
        )
