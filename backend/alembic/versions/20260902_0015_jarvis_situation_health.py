"""Add reliable situation, worker-wait, and runtime-health state.

Revision ID: 20260902_0015
Revises: 20260902_0014
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260902_0015"
down_revision = "20260902_0014"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    return {
        str(column["name"])
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _table_exists(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _task_status_constraint() -> dict[str, object] | None:
    for constraint in sa.inspect(op.get_bind()).get_check_constraints("tasks"):
        sqltext = str(constraint.get("sqltext") or "")
        if constraint.get("name") == "ck_tasks_status" or "status IN" in sqltext:
            return constraint
    return None


def _recover_interrupted_tasks_batch() -> None:
    """Recover Alembic's SQLite batch table after a process interruption."""
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    temporary_table = "_alembic_tmp_tasks"
    if temporary_table not in tables:
        return
    if "tasks" in tables:
        # The source table is still authoritative; the temporary table is only
        # a partial or complete copy that was never swapped into place.
        op.drop_table(temporary_table)
    else:
        # The copy completed and the source was dropped, but the final rename did
        # not run. Restoring its intended name lets the remaining checks resume.
        op.rename_table(temporary_table, "tasks")


def _drop_task_recall_triggers() -> None:
    for trigger in ("recall_task_insert", "recall_task_update", "recall_task_event_insert"):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")


def _create_task_recall_triggers(*, include_status_reason: bool = True) -> None:
    detail_sql = (
        "COALESCE(NEW.status_reason, '') || char(10) || COALESCE(NEW.terminal_summary, '')"
        if include_status_reason
        else "COALESCE(NEW.terminal_summary, '')"
    )
    update_columns = "goal, status, status_reason, terminal_summary" if include_status_reason else "goal, status, terminal_summary"
    op.execute(f"""
        CREATE TRIGGER recall_task_insert AFTER INSERT ON tasks BEGIN
            INSERT INTO recall_documents
                (id, virtual_path, source_type, source_id, title, content, source_timestamp)
            VALUES (
                lower(hex(randomblob(16))), 'task/' || NEW.id || '/summary.txt', 'task', NEW.id,
                substr(NEW.goal, 1, 255),
                NEW.goal || char(10) || 'Status: ' || NEW.status || char(10) || {detail_sql},
                NEW.created_at
            ) ON CONFLICT(virtual_path) DO UPDATE SET
                title=excluded.title, content=excluded.content,
                source_timestamp=excluded.source_timestamp, updated_at=CURRENT_TIMESTAMP;
        END
    """)
    op.execute(f"""
        CREATE TRIGGER recall_task_update
        AFTER UPDATE OF {update_columns} ON tasks BEGIN
            UPDATE recall_documents SET
                title=substr(NEW.goal, 1, 255),
                content=NEW.goal || char(10) || 'Status: ' || NEW.status || char(10) || {detail_sql},
                updated_at=CURRENT_TIMESTAMP
            WHERE virtual_path='task/' || NEW.id || '/summary.txt';
        END
    """)
    op.execute("""
        CREATE TRIGGER recall_task_event_insert AFTER INSERT ON task_events
        WHEN NEW.event_type IN (
            'task.queued', 'task.waiting_for_worker', 'task.worker_available',
            'task.progress', 'task.requires_input', 'task.question.answered',
            'task.waiting_until', 'task.resumed', 'task.reminder.due', 'task.completed',
            'task.failed', 'task.interrupted', 'task.cancelled'
        )
        BEGIN
            INSERT INTO recall_documents
                (id, virtual_path, source_type, source_id, title, content, source_timestamp)
            VALUES (
                lower(hex(randomblob(16))),
                'task/' || NEW.task_id || '/event/' || NEW.id || '.txt',
                'task', NEW.id,
                COALESCE((SELECT substr(goal, 1, 255) FROM tasks WHERE id=NEW.task_id), 'Task update'),
                NEW.event_type || char(10) || COALESCE(NEW.payload_json, ''), NEW.created_at
            ) ON CONFLICT(virtual_path) DO NOTHING;
        END
    """)


def upgrade() -> None:
    # SQLite DDL is not transactional. Every step is conditional so an interrupted
    # migration can safely resume before Alembic has advanced its revision marker.
    _recover_interrupted_tasks_batch()
    jarvis_state_columns = _column_names("jarvis_state")
    if "situation_revision" not in jarvis_state_columns:
        op.add_column(
            "jarvis_state",
            sa.Column("situation_revision", sa.Integer(), nullable=False, server_default="0"),
        )
    if "conversation_summary" not in jarvis_state_columns:
        op.add_column(
            "jarvis_state",
            sa.Column("conversation_summary", sa.Text(), nullable=False, server_default=""),
        )

    _drop_task_recall_triggers()
    task_columns = _column_names("tasks")
    status_constraint = _task_status_constraint()
    status_sql = str((status_constraint or {}).get("sqltext") or "")
    needs_status_reason = "status_reason" not in task_columns
    needs_status_constraint = "waiting_for_worker" not in status_sql
    if needs_status_reason or needs_status_constraint:
        with op.batch_alter_table("tasks") as batch:
            if needs_status_reason:
                batch.add_column(sa.Column("status_reason", sa.Text(), nullable=True))
            if needs_status_constraint:
                constraint_name = (status_constraint or {}).get("name")
                if constraint_name:
                    batch.drop_constraint(str(constraint_name), type_="check")
                batch.create_check_constraint(
                    "ck_tasks_status",
                    "status IN ('queued', 'waiting_for_worker', 'running', 'waiting_for_input', "
                    "'waiting_until', 'completed', 'failed', 'interrupted', 'cancelled')",
                )
    _create_task_recall_triggers()
    if not _table_exists("runtime_component_health"):
        op.create_table(
            "runtime_component_health",
            sa.Column("component", sa.String(length=64), primary_key=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )


def downgrade() -> None:
    op.drop_table("runtime_component_health")
    _drop_task_recall_triggers()
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("ck_tasks_status", type_="check")
        batch.create_check_constraint(
            "ck_tasks_status",
            "status IN ('queued', 'running', 'waiting_for_input', 'waiting_until', "
            "'completed', 'failed', 'interrupted', 'cancelled')",
        )
        batch.drop_column("status_reason")
    _create_task_recall_triggers(include_status_reason=False)
    op.drop_column("jarvis_state", "conversation_summary")
    op.drop_column("jarvis_state", "situation_revision")
