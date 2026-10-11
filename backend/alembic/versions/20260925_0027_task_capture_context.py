"""Persist capture readiness and run-bound private context upload."""
from __future__ import annotations

import json
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision = "20260925_0027"
down_revision = "20260924_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    live_columns = {column["name"] for column in inspector.get_columns("live_sessions")}
    for name, length in (("activation_id", 64), ("activation_source", 32), ("workflow_catalog_revision", 64)):
        if name not in live_columns:
            op.add_column("live_sessions", sa.Column(name, sa.String(length), nullable=True))
    event_columns = {column["name"] for column in inspector.get_columns("jarvis_live_events")}
    if "target_device_id" not in event_columns:
        op.add_column("jarvis_live_events", sa.Column("target_device_id", sa.String(36), nullable=True))
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "task_capture_reports" not in tables:
        op.create_table(
        "task_capture_reports",
        sa.Column("capture_id", sa.String(64), primary_key=True),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("jarvis_devices.id"), nullable=False),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("live_sessions.id"), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=True),
        sa.Column("activation_id", sa.String(64), nullable=False),
        sa.Column("capture_mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("callback_monotonic_ms", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("task_capture_reports")}
    if "ix_task_capture_reports_activation" not in indexes:
        op.create_index("ix_task_capture_reports_activation", "task_capture_reports", ["device_id", "activation_id", "capture_mode"])
    if "workflow_runs" not in tables:
        op.create_table(
        "workflow_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("execution_run_id", sa.String(36), sa.ForeignKey("task_execution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workflow_id", sa.String(64), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("workflow_digest", sa.String(64), nullable=False),
        sa.Column("normalized_inputs_json", sa.JSON(), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("context_reference", sa.String(1024), nullable=True),
        sa.Column("result_reference", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("execution_run_id", name="uq_workflow_runs_execution_run"),
    )
    if "task_context_blobs" not in tables:
        op.create_table(
        "task_context_blobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("storage_path", sa.String(1024), nullable=False),
        sa.Column("byte_length", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    if "task_context_requests" not in tables:
        op.create_table(
        "task_context_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("execution_run_id", sa.String(36), sa.ForeignKey("task_execution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workflow_run_id", sa.String(36), sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("capture_id", sa.String(64), sa.ForeignKey("task_capture_reports.capture_id"), nullable=False),
        sa.Column("capture_revision", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("jarvis_devices.id"), nullable=False),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("live_sessions.id"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("activation_id", sa.String(64), nullable=False),
        sa.Column("capture_mode", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("failure_reason", sa.String(64), nullable=True),
        sa.Column("blob_id", sa.String(36), sa.ForeignKey("task_context_blobs.id"), nullable=True),
        sa.Column("artifact_id", sa.String(36), sa.ForeignKey("task_artifacts.id"), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("execution_run_id", "capture_id", name="uq_task_context_requests_run_capture"),
    )
    connection = op.get_bind()
    for row in connection.execute(sa.text(
        "SELECT id, task_id, input_turn_id, configuration_json FROM task_execution_runs WHERE execution_kind = 'workflow'"
    )).mappings():
        config = json.loads(row["configuration_json"] or "{}")
        pinned = config.get("workflow") if isinstance(config, dict) else None
        if not isinstance(pinned, dict) or not all(key in pinned for key in ("id", "version", "digest")):
            continue
        if connection.execute(sa.text(
            "SELECT 1 FROM workflow_runs WHERE execution_run_id = :run_id"
        ), {"run_id": row["id"]}).first() is not None:
            continue
        connection.execute(sa.text(
            "INSERT INTO workflow_runs (id, task_id, execution_run_id, workflow_id, workflow_version, workflow_digest, normalized_inputs_json, stage) "
            "VALUES (:id, :task_id, :run_id, :workflow_id, :version, :digest, :inputs, 'admitted')"
        ), {
            "id": str(uuid4()), "task_id": row["task_id"], "run_id": row["id"],
            "workflow_id": pinned["id"], "version": pinned["version"], "digest": pinned["digest"],
            "inputs": json.dumps({"input_turn_id": row["input_turn_id"]}),
        })


def downgrade() -> None:
    op.drop_table("task_context_requests")
    op.drop_table("task_context_blobs")
    op.drop_table("workflow_runs")
    op.drop_index("ix_task_capture_reports_activation", table_name="task_capture_reports")
    op.drop_table("task_capture_reports")
    op.drop_column("jarvis_live_events", "target_device_id")
    op.drop_column("live_sessions", "workflow_catalog_revision")
    op.drop_column("live_sessions", "activation_source")
    op.drop_column("live_sessions", "activation_id")
