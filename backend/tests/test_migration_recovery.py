from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import logging
import pytest

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from app.config import settings
from app.db.session import run_migrations
from app.db.tables import TaskContextBundle


def test_session_identity_backfill_replays_without_changing_cursors(tmp_path):
    path = tmp_path / "session-identity.db"
    config = _config(path)
    command.upgrade(config, "20261003_0035")
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO conversations (id, title) VALUES ('conversation', 'Replay')")
        connection.execute("INSERT INTO live_sessions (id, conversation_id, generation, provider_adapter, lifecycle_state) VALUES ('session', 'conversation', 1, 'fake', 'ready')")
        connection.execute("INSERT INTO live_session_events (id, session_id, sequence, event_type, payload_json) VALUES ('event', 'session', 1, 'brain.tool.result', '{}')")
        before = connection.execute("SELECT sequence, idempotency_key FROM jarvis_live_events WHERE source_domain='session'").fetchall()
        assert connection.execute("SELECT source_id FROM jarvis_live_events WHERE source_domain='session'").fetchone() == ('event',)
    command.upgrade(config, "head")
    command.stamp(config, "20261003_0035")
    command.upgrade(config, "head")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT source_id FROM jarvis_live_events WHERE source_domain='session'").fetchone() == ('session',)
        assert connection.execute("SELECT sequence, idempotency_key FROM jarvis_live_events WHERE source_domain='session'").fetchall() == before
        connection.execute("INSERT INTO live_session_events (id, session_id, sequence, event_type, payload_json) VALUES ('event-two', 'session', 2, 'provider.ready', '{}')")
        assert connection.execute("SELECT source_id FROM jarvis_live_events WHERE idempotency_key='session:event-two'").fetchone() == ('session',)


@pytest.mark.asyncio
async def test_admission_receipt_and_credential_owner_survive_unstamped_migration_replay():
    from app.db.session import session_scope
    from app.db.tables import Conversation, LiveSession, LiveAdmissionIntent, RuntimeCredentialLease
    async with session_scope() as session:
        conversation = Conversation(title="Migration admission")
        session.add(conversation)
        await session.flush()
        live = LiveSession(conversation_id=conversation.id, provider_adapter="fake", lifecycle_state="ready")
        session.add(live)
        await session.flush()
        session.add(LiveAdmissionIntent(id="receipt", session_id=live.id, generation=1, turn_scope="turn",
            intent_id="outcome", tool_name="dispatch_task", input_digest="a" * 64,
            target_task_id="pinned-target", output_json={"task_id": "original", "turn_id": "original-turn"}))
        session.add(RuntimeCredentialLease(profile_key="profile", owner_kind="worker", owner_id="exact-owner",
            host="host", pid=100, creation_stamp="200"))
    with sqlite3.connect(settings.database_path) as connection:
        before = connection.execute("SELECT * FROM live_admission_intents").fetchall()
        ownership = connection.execute("SELECT * FROM runtime_credential_leases").fetchall()
    config = Config(settings.alembic_config_path)
    command.stamp(config, "20261001_0033")
    command.upgrade(config, "head")
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT * FROM live_admission_intents").fetchall() == before
        assert connection.execute("SELECT * FROM runtime_credential_leases").fetchall() == ownership
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_in_process_migrations_preserve_application_logging(caplog) -> None:
    app_logger = logging.getLogger("app.services.result_telemetry")
    caplog.set_level(logging.INFO, logger=app_logger.name)
    handlers, level = list(logging.getLogger().handlers), logging.getLogger().level
    run_migrations()
    assert not app_logger.disabled
    assert logging.getLogger().handlers == handlers and logging.getLogger().level == level
    app_logger.info("migration_logging_fixture")
    assert "migration_logging_fixture" in caplog.text


def test_result_platform_backfill_resumes_without_changing_generated_routes(tmp_path: Path) -> None:
    database_path = tmp_path / "result-platform.db"
    config = _config(database_path)
    command.upgrade(config, "20260930_0030")
    with sqlite3.connect(database_path) as connection:
        connection.execute("INSERT INTO generated_apps (id, slug, title, status, route_path, frontend_root) VALUES ('legacy-generated', 'documents', 'Old documents app', 'draft', '/apps/documents', 'frontend/generated-apps/documents')")
        # Simulate an interrupted migration after one additive column.
        connection.execute("ALTER TABLE result_pages ADD COLUMN state_revision INTEGER NOT NULL DEFAULT 0")
    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        before = connection.execute("SELECT id, slug, kind, route_path FROM cockpit_apps ORDER BY slug").fetchall()
        assert len(before) == 3
        generated = connection.execute("SELECT slug, route_path, cockpit_app_id FROM generated_apps WHERE id = 'legacy-generated'").fetchone()
        assert generated[:2] == ('documents', '/apps/documents') and generated[2]
        assert connection.execute("SELECT kind FROM cockpit_apps WHERE id = ?", (generated[2],)).fetchone() == ('generated',)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    # Replay after a crash before Alembic stamped the completed migration.
    command.stamp(config, "20260930_0030")
    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT id, slug, kind, route_path FROM cockpit_apps ORDER BY slug").fetchall() == before


def test_result_metric_migration_replay_preserves_observations(tmp_path: Path) -> None:
    database_path = tmp_path / "result-metric-replay.db"
    config = _config(database_path)
    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        connection.execute("INSERT INTO result_metrics (metric_key, kind, values_json) VALUES ('capture:test', 'capture_preflight', '{\"outcome\":\"timeout\"}')")
    command.stamp(config, "20261001_0032")
    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT metric_key, kind FROM result_metrics").fetchall() == [('capture:test', 'capture_preflight')]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def _config(database_path: Path) -> Config:
    settings.database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    config = Config(settings.alembic_config_path)
    return config


def test_storage_capability_migration_replay_preserves_revocations(tmp_path: Path) -> None:
    database_path = tmp_path / "capability-replay.db"
    config = _config(database_path)
    command.upgrade(config, "20261001_0031")
    with sqlite3.connect(database_path) as connection:
        connection.execute("ALTER TABLE cockpit_apps ADD COLUMN capability_revision INTEGER NOT NULL DEFAULT 0")
        connection.execute("UPDATE cockpit_apps SET capability_revision = 7")
    command.upgrade(config, "head")
    command.stamp(config, "20261001_0031")
    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT capability_revision FROM cockpit_apps").fetchall() == [(7,), (7,)]


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
        assert "task_context_bundles" in inspector.get_table_names()
        assert "intent_json" in {column["name"] for column in inspector.get_columns("task_operations")}
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
                    ).scalar_one() == ScriptDirectory.from_config(config).get_current_head()
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
                ScriptDirectory.from_config(config).get_current_head(),
        )


def test_context_bundle_migration_resumes_after_partial_sqlite_ddl(tmp_path: Path) -> None:
    database_path = tmp_path / "partial-context.db"
    config = _config(database_path)
    command.upgrade(config, "20260923_0024")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        TaskContextBundle.__table__.create(engine)
    finally:
        engine.dispose()
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        assert "task_context_bundles" in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == ScriptDirectory.from_config(config).get_current_head()
    finally:
        engine.dispose()


def test_capture_context_migration_resumes_after_partial_sqlite_ddl(tmp_path: Path) -> None:
    database_path = tmp_path / "partial-capture.db"
    config = _config(database_path)
    command.upgrade(config, "20260924_0026")
    with sqlite3.connect(database_path) as connection:
        connection.execute("ALTER TABLE live_sessions ADD COLUMN activation_id VARCHAR(64)")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        inspector = inspect(engine)
        assert {"activation_id", "activation_source", "workflow_catalog_revision"}.issubset(
            {column["name"] for column in inspector.get_columns("live_sessions")}
        )
        assert "task_context_requests" in inspector.get_table_names()
    finally:
        engine.dispose()


def test_task_control_plane_migration_preserves_legacy_task_evidence(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy-task.db"
    config = _config(database_path)
    command.upgrade(config, "20260914_0022")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO tasks (id, origin_surface, goal, idempotency_key, status, "
            "importance, current_attempt_id) VALUES (?, 'test', 'Legacy result', ?, "
            "'completed', 'high', ?)",
            ("legacy-task", "legacy-task-key", "legacy-attempt"),
        )
        connection.execute(
            "INSERT INTO task_attempts (id, task_id, attempt_number, workspace_path, "
            "checkpoint_json, terminal_reason) VALUES (?, ?, 3, ?, ?, 'completed')",
            ("legacy-attempt", "legacy-task", "C:/legacy/workspace", '{"step": 2}'),
        )
        connection.execute(
            "INSERT INTO task_events (id, task_id, attempt_id, event_type, idempotency_key) "
            "VALUES ('legacy-event', 'legacy-task', 'legacy-attempt', 'task.completed', 'legacy-event-key')"
        )
        connection.execute(
            "INSERT INTO task_artifacts (id, task_id, attempt_id, artifact_type, mime_type, "
            "content_text) VALUES ('legacy-artifact', 'legacy-task', 'legacy-attempt', "
            "'result', 'text/plain', 'Retained result')"
        )
    command.upgrade(config, "head")
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        task = connection.execute(
            "SELECT id, status, importance, current_attempt_id, current_run_id "
            "FROM tasks WHERE id='legacy-task'"
        ).fetchone()
        assert task is not None
        assert (task["status"], task["importance"], task["current_attempt_id"]) == (
            "completed", "high", "legacy-attempt"
        )
        run_id = task["current_run_id"]
        assert run_id
        assert connection.execute(
            "SELECT COUNT(*) FROM task_turns WHERE task_id='legacy-task'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT ordinal, status, current_attempt_id FROM task_execution_runs WHERE id=?",
            (run_id,),
        ).fetchone()[:] == (1, "completed", "legacy-attempt")
        assert connection.execute(
            "SELECT attempt_number, workspace_path, execution_run_id FROM task_attempts "
            "WHERE id='legacy-attempt'"
        ).fetchone()[:] == (3, "C:/legacy/workspace", run_id)
        assert connection.execute(
            "SELECT id, execution_run_id FROM task_events WHERE id='legacy-event'"
        ).fetchone()[:] == ("legacy-event", run_id)
        assert connection.execute(
            "SELECT id, content_text, execution_run_id FROM task_artifacts WHERE id='legacy-artifact'"
        ).fetchone()[:] == ("legacy-artifact", "Retained result", run_id)


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
