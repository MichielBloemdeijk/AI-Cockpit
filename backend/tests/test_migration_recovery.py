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
        assert "jarvis_live_trace_events" in inspector.get_table_names()
        assert "client_sequence" in {
            column["name"]
            for column in inspector.get_columns("jarvis_live_trace_events")
        }
        assert "task_projection_checkpoints" in inspector.get_table_names()
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
                ).scalar_one() == "20260914_0022"
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
                "20260914_0022",
        )


def test_live_surface_migration_preserves_history_and_retires_active_legacy_sessions(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "live-surface.db"
    config = _config(database_path)
    command.upgrade(config, "20260908_0020")

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO realtime_sessions "
            "(id, conversation_id, generation, provider_adapter, lifecycle_state, "
            "audio_policy_json) VALUES (?, ?, ?, ?, ?, ?)",
            ("session-legacy", "conversation-legacy", 1, "openai", "ready", "{}"),
        )
        connection.execute(
            "INSERT INTO jarvis_realtime_trace_events "
            "(trace_id, source, event_type, observed_at, detail_json) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?)",
            ("trace-legacy", "browser", "session.created", "{}"),
        )
        connection.execute(
            "INSERT INTO voice_session_events "
            "(id, session_id, sequence, event_type, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            ("event-legacy", "session-legacy", 1, "session.created", "{}"),
        )

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        session = connection.execute(
            "SELECT lifecycle_state, disconnected_at FROM live_sessions WHERE id = ?",
            ("session-legacy",),
        ).fetchone()
        assert session is not None
        assert session["lifecycle_state"] == "disconnected"
        assert session["disconnected_at"] is not None
        assert connection.execute(
            "SELECT COUNT(*) FROM live_session_events WHERE id = 'event-legacy'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM jarvis_live_trace_events WHERE trace_id = 'trace-legacy'"
        ).fetchone()[0] == 1
        assert "audio_policy_json" not in {
            row[1] for row in connection.execute("PRAGMA table_info(live_sessions)")
        }
        assert "live_session_id" in {
            row[1] for row in connection.execute("PRAGMA table_info(live_speech_deliveries)")
        }


def test_model_reported_attention_migrates_evidence_audits_and_constraints(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "attention-legacy.db"
    config = _config(database_path)
    command.upgrade(config, "20260908_0020")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO tasks (id, origin_surface, goal, idempotency_key) "
            "VALUES ('task-legacy', 'test', 'Legacy task', 'task-legacy-key')"
        )
        for event_id, event_type in (
            ("event-presented", "task.completed"),
            ("event-ack", "task.completed"),
            ("event-null", "task.completed"),
            ("event-reminder", "task.reminder.due"),
        ):
            connection.execute(
                "INSERT INTO task_events (id, task_id, event_type, idempotency_key) "
                "VALUES (?, 'task-legacy', ?, ?)",
                (event_id, event_type, f"key-{event_id}"),
            )
        presentations = (
            (
                "presentation-presented",
                "terminal_result",
                "presented",
                "event-presented",
                "2026-01-02 03:04:05",
            ),
            ("presentation-ack", "terminal_result", "pending", "event-ack", None),
            ("presentation-null", "terminal_result", "acknowledged", "event-null", None),
            ("presentation-reminder", "reminder", "pending", "event-reminder", None),
            ("presentation-orphan", "reminder", "pending", None, None),
        )
        for presentation_id, candidate_type, state, event_id, presented_at in presentations:
            connection.execute(
                "INSERT INTO task_presentations "
                "(id, task_id, candidate_type, coalescing_key, importance, state, "
                "source_task_event_id, presented_at, idempotency_key) "
                "VALUES (?, 'task-legacy', ?, ?, 'normal', ?, ?, ?, ?)",
                (
                    presentation_id,
                    candidate_type,
                    f"coalesce-{presentation_id}",
                    state,
                    event_id,
                    presented_at,
                    f"key-{presentation_id}",
                ),
            )
        connection.execute(
            "INSERT INTO notification_outbox "
            "(id, task_id, task_event_id, event_kind, dedupe_key, payload_json, deep_link, "
            "status, acknowledged_at, acknowledgement_state) "
            "VALUES ('delivery-ack', 'task-legacy', 'event-ack', 'task.completed', "
            "'delivery-key', '{}', '/tasks/task-legacy', 'acknowledged', "
            "'2026-02-03 04:05:06', 'opened')"
        )

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = {
            row["id"]: row
            for row in connection.execute(
                "SELECT id, state, handled_at, handled_by, handled_event_id "
                "FROM task_presentations"
            )
        }
        assert rows["presentation-presented"]["handled_at"] == "2026-01-02 03:04:05"
        assert rows["presentation-ack"]["handled_at"] == "2026-02-03 04:05:06"
        assert rows["presentation-ack"]["handled_by"] == "migration_notification_opened"
        assert rows["presentation-null"]["handled_at"] is None
        assert rows["presentation-reminder"]["handled_by"] == "migration_legacy_retired"
        assert rows["presentation-orphan"]["handled_by"] == "migration_legacy_retired"
        assert all(row["handled_event_id"] for row in rows.values())
        audit_count = connection.execute(
            "SELECT COUNT(*) FROM jarvis_live_events WHERE event_type = 'attention.handled'"
        ).fetchone()[0]
        assert audit_count == len(rows)
        outbox = connection.execute(
            "SELECT status, delivered_at FROM notification_outbox WHERE id = 'delivery-ack'"
        ).fetchone()
        assert tuple(outbox) == ("delivered", None)

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        inspector = inspect(engine)
        foreign_keys = {
            (tuple(item["constrained_columns"]), item["referred_table"])
            for item in inspector.get_foreign_keys("task_presentations")
        }
        assert (("handled_turn_id",), "conversation_messages") in foreign_keys
        assert (("handled_event_id",), "jarvis_live_events") in foreign_keys
        indexes = {item["name"] for item in inspector.get_indexes("task_presentations")}
        assert "uq_task_presentations_session_lease" in indexes
        assert "uq_task_presentations_source_task_event" in indexes
    finally:
        engine.dispose()

    # Simulate a process exit after all SQLite DDL committed but before Alembic
    # advanced its revision marker. Re-running 0021 must be idempotent.
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE alembic_version SET version_num = '20260908_0020'"
        )
    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM jarvis_live_events WHERE event_type = 'attention.handled'"
        ).fetchone()[0] == len(rows)
