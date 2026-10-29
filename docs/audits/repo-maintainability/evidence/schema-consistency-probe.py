"""Fresh isolated migration/metadata audit. No personal configuration or DB.

From repository root: backend/.venv/Scripts/python.exe docs/audits/repo-maintainability/evidence/schema-consistency-probe.py
"""
from pathlib import Path
import json
import os
import sys
import tempfile
import warnings
from collections import Counter

ROOT = Path(__file__).resolve().parents[4]
os.environ["AI_COCKPIT_ENV_FILE"] = str(ROOT / "docs/audits/repo-maintainability/evidence/no-personal-env")
sys.path.insert(0, str(ROOT / "backend"))
from sqlalchemy import create_engine, inspect, text
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.config import settings
from app.db.base import Base
from app.db import tables
from app.db.session import run_migrations

with tempfile.TemporaryDirectory(prefix="cockpit-schema-audit-") as folder:
    settings.database_url = "sqlite+aiosqlite:///" + (Path(folder) / "audit.db").as_posix()
    run_migrations()
    engine = create_engine(settings.resolved_database_url.replace("+aiosqlite", ""))
    with engine.connect() as connection, warnings.catch_warnings(record=True) as caught:
        differences = compare_metadata(MigrationContext.configure(connection, opts={"compare_type": True}), Base.metadata)
        foreign_key_check = list(connection.execute(text("PRAGMA foreign_key_check")))
        triggers = [r[0] for r in connection.execute(text("SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name"))]
        inspector = inspect(connection)
        missing = []
        for table in Base.metadata.sorted_tables:
            actual = inspector.get_foreign_keys(table.name)
            for fk in table.foreign_key_constraints:
                columns = [e.parent.name for e in fk.elements]
                targets = [e.column.name for e in fk.elements]
                target_table = next(iter(fk.elements)).column.table.name
                present = any(v["constrained_columns"] == columns and v["referred_table"] == target_table and v["referred_columns"] == targets and v.get("options", {}).get("ondelete") == fk.ondelete for v in actual)
                if not present:
                    missing.append({"table":table.name,"columns":columns,"target":target_table,"target_columns":targets,"ondelete":fk.ondelete})
        connection.rollback()
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql("INSERT INTO tasks(id,origin_surface,goal,idempotency_key) VALUES('audit-task','audit','schema probe','audit-schema')")
        connection.exec_driver_sql("INSERT INTO task_artifacts(id,task_id,execution_run_id,artifact_type,mime_type) VALUES('audit-artifact','audit-task','nonexistent-run','text','text/plain')")
        orphan_accepted = bool(connection.exec_driver_sql("SELECT count(*) FROM task_artifacts WHERE execution_run_id='nonexistent-run'").scalar_one())
        connection.rollback()
        print(json.dumps({"autogenerate_operation_counts":dict(Counter(d[0] if isinstance(d,tuple) else "column-change" for d in differences)),"metadata_foreign_keys_missing_or_different":missing,"orphan_artifact_run_accepted_with_foreign_keys_on":orphan_accepted,"warnings":[str(w.message) for w in caught],"foreign_key_check_before_probe":[list(r) for r in foreign_key_check],"triggers":triggers,"table_count":len(inspector.get_table_names())},indent=2))
    engine.dispose()
