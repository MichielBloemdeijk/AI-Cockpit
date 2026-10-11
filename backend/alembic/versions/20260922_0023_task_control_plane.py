"""Add the durable M1 task control-plane contracts."""
from __future__ import annotations

from hashlib import sha256
import json
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision = "20260922_0023"
down_revision = "20260914_0022"
branch_labels = None
depends_on = None


def _uuid() -> str:
    return str(uuid4())


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _create_task_recall_triggers() -> None:
    op.execute("""
        CREATE TRIGGER recall_task_insert AFTER INSERT ON tasks BEGIN
            INSERT INTO recall_documents
                (id, virtual_path, source_type, source_id, title, content, source_timestamp)
            VALUES (
                lower(hex(randomblob(16))), 'task/' || NEW.id || '/summary.txt', 'task', NEW.id,
                substr(NEW.goal, 1, 255),
                NEW.goal || char(10) || 'Status: ' || NEW.status || char(10) || COALESCE(NEW.terminal_summary, ''),
                NEW.created_at
            ) ON CONFLICT(virtual_path) DO UPDATE SET
                title=excluded.title, content=excluded.content,
                source_timestamp=excluded.source_timestamp, updated_at=CURRENT_TIMESTAMP;
        END
    """)
    op.execute("""
        CREATE TRIGGER recall_task_update AFTER UPDATE OF goal, status, terminal_summary ON tasks BEGIN
            UPDATE recall_documents SET
                title=substr(NEW.goal, 1, 255),
                content=NEW.goal || char(10) || 'Status: ' || NEW.status || char(10) || COALESCE(NEW.terminal_summary, ''),
                updated_at=CURRENT_TIMESTAMP
            WHERE virtual_path='task/' || NEW.id || '/summary.txt';
        END
    """)
    op.execute("""
        CREATE TRIGGER recall_task_event_insert AFTER INSERT ON task_events
        WHEN NEW.event_type IN (
            'task.queued', 'task.progress', 'task.requires_input', 'task.question.answered',
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
    if "task_execution_runs" in _tables() and "execution_kind" in _columns("tasks"):
        return
    op.execute("DROP TRIGGER IF EXISTS recall_task_event_insert")
    op.execute("DROP TRIGGER IF EXISTS recall_task_update")
    op.execute("DROP TRIGGER IF EXISTS recall_task_insert")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("ck_tasks_status", type_="check")
        batch.create_check_constraint(
            "ck_tasks_status",
            "status IN ('queued', 'waiting_for_worker', 'running', 'waiting_for_input', "
            "'waiting_until', 'paused', 'completed', 'failed', 'interrupted', 'cancelled')",
        )
        batch.add_column(sa.Column("execution_kind", sa.String(32), nullable=False, server_default="agent"))
        batch.add_column(sa.Column("current_run_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("pending_control_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("goal_revision", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("control_revision", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("workspace_generation", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(
            sa.Column("queue_entered_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
        )
    _create_task_recall_triggers()

    op.create_table(
        "task_turns",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("artifact_reference", sa.String(1024), nullable=True),
        sa.Column("origin", sa.String(64), nullable=False),
        sa.Column("request_identity", sa.String(255), nullable=True),
        sa.Column("input_fingerprint", sa.String(64), nullable=True),
        sa.Column("execution_run_id", sa.String(36), nullable=True),
        sa.Column("question_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("task_id", "sequence", name="uq_task_turns_task_sequence"),
        sa.UniqueConstraint("task_id", "request_identity", name="uq_task_turns_request_identity"),
    )
    op.create_table(
        "task_execution_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("input_turn_id", sa.String(36), sa.ForeignKey("task_turns.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("execution_kind", sa.String(32), nullable=False, server_default="agent"),
        sa.Column("configuration_json", sa.JSON(), nullable=True),
        sa.Column("workspace_generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("current_attempt_id", sa.String(36), nullable=True),
        sa.Column("context_reference", sa.String(1024), nullable=True),
        sa.Column("result_reference", sa.String(1024), nullable=True),
        sa.Column("terminal_summary", sa.Text(), nullable=True),
        sa.Column("queue_entered_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("ordinal > 0", name="ck_task_execution_runs_ordinal_positive"),
        sa.UniqueConstraint("task_id", "ordinal", name="uq_task_execution_runs_task_ordinal"),
    )
    op.create_index("ix_task_execution_runs_queue", "task_execution_runs", ["status", "queue_entered_at"])

    op.create_table(
        "task_control_commands",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "execution_run_id", sa.String(36),
            sa.ForeignKey("task_execution_runs.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("attempt_id", sa.String(36), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("parameters_json", sa.JSON(), nullable=True),
        sa.Column("requester", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("provider_call_id", sa.String(255), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="requested"),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.UniqueConstraint("task_id", "idempotency_key", name="uq_task_control_commands_task_key"),
    )
    op.create_table(
        "task_native_bindings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "execution_run_id", sa.String(36),
            sa.ForeignKey("task_execution_runs.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("native_thread_id", sa.String(255), nullable=True),
        sa.Column("profile_id", sa.String(255), nullable=True),
        sa.Column("runtime_version", sa.String(64), nullable=False),
        sa.Column("compatibility_json", sa.JSON(), nullable=True),
        sa.Column("storage_reference", sa.String(1024), nullable=True),
        sa.Column("replacement_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("execution_run_id", "generation", name="uq_task_native_bindings_run_generation"),
    )
    op.create_table(
        "task_operations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "execution_run_id", sa.String(36),
            sa.ForeignKey("task_execution_runs.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("attempt_id", sa.String(36), nullable=True),
        sa.Column("operation_key", sa.String(255), nullable=False),
        sa.Column("operation_type", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="started"),
        sa.Column("input_digest", sa.String(64), nullable=True),
        sa.Column("result_reference", sa.String(1024), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("task_id", "operation_key", name="uq_task_operations_task_key"),
    )
    op.create_table(
        "task_supervisor_leases",
        sa.Column("lease_key", sa.String(64), primary_key=True),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("owner_identity", sa.String(255), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.add_column("worker_instances", sa.Column("supervisor_generation", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("worker_instances", sa.Column("assignment_attempt_id", sa.String(36), nullable=True))
    op.add_column("worker_instances", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))

    for column in (
        sa.Column("execution_run_id", sa.String(36), nullable=True),
        sa.Column("claim_token", sa.String(64), nullable=True),
        sa.Column("supervisor_generation", sa.Integer(), nullable=True),
        sa.Column("parent_attempt_id", sa.String(36), nullable=True),
        sa.Column("resume_reason", sa.String(64), nullable=True),
        sa.Column("checkpoint_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_progress_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_operation", sa.String(255), nullable=True),
        sa.Column("operation_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("operation_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_metadata_json", sa.JSON(), nullable=True),
    ):
        op.add_column("task_attempts", column)

    op.add_column("task_events", sa.Column("execution_run_id", sa.String(36), nullable=True))
    op.add_column("task_events", sa.Column("native_item_id", sa.String(255), nullable=True))
    op.create_index(
        "uq_task_events_run_native_item",
        "task_events",
        ["execution_run_id", "native_item_id"],
        unique=True,
    )
    op.add_column("task_questions", sa.Column("asking_run_id", sa.String(36), nullable=True))
    op.add_column("task_questions", sa.Column("resolution_revision", sa.Integer(), nullable=True))
    op.add_column("task_artifacts", sa.Column("execution_run_id", sa.String(36), nullable=True))
    for column in (
        sa.Column("canonical_key", sa.String(1024), nullable=True),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("execution_run_id", sa.String(36), nullable=True),
        sa.Column("claim_token", sa.String(64), nullable=True),
        sa.Column("supervisor_generation", sa.Integer(), nullable=True),
        sa.Column("writer_identity", sa.String(255), nullable=True),
        sa.Column("release_evidence_json", sa.JSON(), nullable=True),
    ):
        op.add_column("resource_leases", column)

    bind = op.get_bind()
    tasks = bind.execute(
        sa.text(
            "SELECT id, goal, origin_surface, idempotency_key, status, importance, "
            "current_attempt_id, created_at, updated_at, finished_at FROM tasks ORDER BY creation_order"
        )
    ).mappings().all()
    for task in tasks:
        turn_id = _uuid()
        run_id = _uuid()
        fingerprint = sha256(
            json.dumps(
                {"configuration": {}, "content": task["goal"], "execution_kind": "agent"},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        bind.execute(
            sa.text(
                "INSERT INTO task_turns "
                "(id, task_id, sequence, role, content, origin, request_identity, input_fingerprint, created_at) "
                "VALUES (:id, :task_id, 1, 'user', :content, :origin, :request_identity, :fingerprint, :created_at)"
            ),
            {
                "id": turn_id,
                "task_id": task["id"],
                "content": task["goal"],
                "origin": task["origin_surface"],
                "request_identity": f"legacy:{task['idempotency_key']}",
                "fingerprint": fingerprint,
                "created_at": task["created_at"],
            },
        )
        bind.execute(
            sa.text(
                "INSERT INTO task_execution_runs "
                "(id, task_id, ordinal, input_turn_id, status, execution_kind, workspace_generation, "
                "current_attempt_id, queue_entered_at, started_at, finished_at, created_at, updated_at) "
                "VALUES (:id, :task_id, 1, :turn_id, :status, 'agent', 1, :attempt_id, :queued_at, "
                ":started_at, :finished_at, :created_at, :updated_at)"
            ),
            {
                "id": run_id,
                "task_id": task["id"],
                "turn_id": turn_id,
                "status": task["status"],
                "attempt_id": task["current_attempt_id"],
                "queued_at": task["created_at"],
                "started_at": task["updated_at"] if task["status"] == "running" else None,
                "finished_at": task["finished_at"],
                "created_at": task["created_at"],
                "updated_at": task["updated_at"],
            },
        )
        bind.execute(
            sa.text("UPDATE task_turns SET execution_run_id = :run_id WHERE id = :turn_id"),
            {"run_id": run_id, "turn_id": turn_id},
        )
        bind.execute(
            sa.text(
                "UPDATE tasks SET current_run_id = :run_id, queue_entered_at = created_at "
                "WHERE id = :task_id"
            ),
            {"run_id": run_id, "task_id": task["id"]},
        )
        bind.execute(
            sa.text("UPDATE task_attempts SET execution_run_id = :run_id WHERE task_id = :task_id"),
            {"run_id": run_id, "task_id": task["id"]},
        )
        bind.execute(
            sa.text("UPDATE task_events SET execution_run_id = :run_id WHERE task_id = :task_id"),
            {"run_id": run_id, "task_id": task["id"]},
        )
        bind.execute(
            sa.text("UPDATE task_questions SET asking_run_id = :run_id WHERE task_id = :task_id"),
            {"run_id": run_id, "task_id": task["id"]},
        )
        bind.execute(
            sa.text("UPDATE task_artifacts SET execution_run_id = :run_id WHERE task_id = :task_id"),
            {"run_id": run_id, "task_id": task["id"]},
        )


def downgrade() -> None:
    with op.batch_alter_table("resource_leases") as batch:
        for name in (
            "release_evidence_json", "writer_identity", "supervisor_generation", "claim_token",
            "execution_run_id", "task_id", "canonical_key",
        ):
            batch.drop_column(name)
    with op.batch_alter_table("task_artifacts") as batch:
        batch.drop_column("execution_run_id")
    with op.batch_alter_table("task_questions") as batch:
        batch.drop_column("resolution_revision")
        batch.drop_column("asking_run_id")
    with op.batch_alter_table("task_events") as batch:
        batch.drop_index("uq_task_events_run_native_item")
        batch.drop_column("native_item_id")
        batch.drop_column("execution_run_id")
    with op.batch_alter_table("task_attempts") as batch:
        for name in (
            "recovery_metadata_json", "operation_deadline", "operation_started_at", "active_operation",
            "last_progress_at", "checkpoint_version", "resume_reason", "parent_attempt_id",
            "supervisor_generation", "claim_token", "execution_run_id",
        ):
            batch.drop_column(name)
    with op.batch_alter_table("worker_instances") as batch:
        batch.drop_column("verified_at")
        batch.drop_column("assignment_attempt_id")
        batch.drop_column("supervisor_generation")
    op.drop_table("task_supervisor_leases")
    op.drop_table("task_operations")
    op.drop_table("task_native_bindings")
    op.drop_table("task_control_commands")
    op.drop_index("ix_task_execution_runs_queue", table_name="task_execution_runs")
    op.drop_table("task_execution_runs")
    op.drop_table("task_turns")
    op.execute("DROP TRIGGER IF EXISTS recall_task_event_insert")
    op.execute("DROP TRIGGER IF EXISTS recall_task_update")
    op.execute("DROP TRIGGER IF EXISTS recall_task_insert")
    with op.batch_alter_table("tasks") as batch:
        for name in (
            "queue_entered_at", "workspace_generation", "control_revision", "goal_revision",
            "pending_control_id", "current_run_id", "execution_kind",
        ):
            batch.drop_column(name)
        batch.drop_constraint("ck_tasks_status", type_="check")
        batch.create_check_constraint(
            "ck_tasks_status",
            "status IN ('queued', 'waiting_for_worker', 'running', 'waiting_for_input', "
            "'waiting_until', 'completed', 'failed', 'interrupted', 'cancelled')",
        )
    _create_task_recall_triggers()
