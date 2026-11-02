"""The migrated database must match the ORM model it is used through.

Both tests fail today (audit finding P01): migrations added eight task foreign
keys as plain columns and left old index names. The phase-3 squash to a
baseline generated from the ORM fixes them; `strict` then turns the expected
failures into failures, so remove the markers with the squash.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3
from contextlib import closing

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import create_engine

from app.db import tables  # noqa: F401  (registers every table on Base.metadata)
from app.db.base import Base

P01 = pytest.mark.xfail(strict=True, raises=(AssertionError, pytest.fail.Exception), reason="P01: schema drift until the phase-3 migration squash")


@P01
def test_fresh_migrated_database_matches_the_orm(migrated_template_database: Path) -> None:
    engine = create_engine(f"sqlite:///{migrated_template_database.as_posix()}")
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            differences = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()
    assert differences == []


@P01
def test_declared_task_foreign_keys_are_enforced(migrated_template_database: Path, tmp_path: Path) -> None:
    database = tmp_path / "orphan.db"
    database.write_bytes(migrated_template_database.read_bytes())
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO tasks(id, origin_surface, goal, idempotency_key) "
            "VALUES ('drift-task', 'test', 'schema drift', 'drift-task')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO task_artifacts(id, task_id, execution_run_id, artifact_type, mime_type) "
                "VALUES ('drift-artifact', 'drift-task', 'missing-run', 'text', 'text/plain')"
            )
