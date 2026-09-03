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
    op.add_column(
        "jarvis_state",
        sa.Column("situation_revision", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "jarvis_state",
        sa.Column("conversation_summary", sa.Text(), nullable=False, server_default=""),
    )
    _drop_task_recall_triggers()
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("status_reason", sa.Text(), nullable=True))
        batch.drop_constraint("ck_tasks_status", type_="check")
        batch.create_check_constraint(
            "ck_tasks_status",
            "status IN ('queued', 'waiting_for_worker', 'running', 'waiting_for_input', "
            "'waiting_until', 'completed', 'failed', 'interrupted', 'cancelled')",
        )
    _create_task_recall_triggers()
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
