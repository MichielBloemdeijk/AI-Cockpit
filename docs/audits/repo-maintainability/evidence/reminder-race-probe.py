"""Does the reminder scheduler overwrite a status committed after its unlocked read?

From repository root: backend/.venv/Scripts/python.exe docs/audits/repo-maintainability/evidence/reminder-race-probe.py
Uses a fresh temporary SQLite DB and no personal config, provider or network.
A concurrent writer is simulated with a plain sqlite3 connection that commits
`status='cancelled'` between the scheduler's read and its write; a real
TaskService.control would commit the same way, since it does not wait on a
lock the scheduler never takes.
"""
from datetime import timedelta
from pathlib import Path
from contextlib import closing
import asyncio
import json
import os
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[4]
os.environ["AI_COCKPIT_ENV_FILE"] = str(ROOT / "docs/audits/repo-maintainability/evidence/no-personal-env")
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.db.session import close_engine, ensure_database_ready, initialize_engine, run_migrations  # noqa: E402
from app.db.repositories.conversations import utc_now  # noqa: E402
from app.services import reminder_scheduler as scheduler_module  # noqa: E402
from app.services.task_service import task_service  # noqa: E402


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="cockpit-reminder-race-", ignore_cleanup_errors=True) as folder:
        db_path = Path(folder) / "race.db"
        settings.database_url = "sqlite+aiosqlite:///" + db_path.as_posix()
        await ensure_database_ready()
        run_migrations()
        initialize_engine(settings.database_url)
        try:
            due = utc_now() + timedelta(seconds=30)
            snapshot = await task_service.schedule_reminder(text="probe", due_at=due, idempotency_key="probe")
            task_id = snapshot.task.id

            original = scheduler_module.TaskRepository

            class InterleavingRepository(original):
                def __init__(self, session):
                    # Runs after _resume_one's unlocked SELECT and before its writes.
                    with closing(sqlite3.connect(db_path, timeout=5)) as other, other:
                        other.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (task_id,))
                    super().__init__(session)

            scheduler_module.TaskRepository = InterleavingRepository
            try:
                resumed = await scheduler_module.reminder_scheduler._resume_one(task_id, timestamp=due + timedelta(seconds=1))
            finally:
                scheduler_module.TaskRepository = original

            with closing(sqlite3.connect(db_path)) as check:
                final_status = check.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
            print(json.dumps({
                "scheduler_reported_resume": resumed,
                "concurrently_committed_status": "cancelled",
                "final_status": final_status,
                "lost_update": final_status != "cancelled",
            }, indent=1))
        finally:
            await close_engine()


asyncio.run(main())
