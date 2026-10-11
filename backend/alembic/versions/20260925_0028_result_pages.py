"""Allocate stable maintained document results before workflow execution."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260925_0028"
down_revision = "20260925_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "result_pages" not in tables:
        op.create_table(
            "result_pages",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("public_id", sa.String(36), nullable=False),
            sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
            sa.Column("initial_execution_run_id", sa.String(36), sa.ForeignKey("task_execution_runs.id"), nullable=False),
            sa.Column("active_execution_run_id", sa.String(36), sa.ForeignKey("task_execution_runs.id"), nullable=True),
            sa.Column("app_slug", sa.String(64), nullable=False),
            sa.Column("result_kind", sa.String(64), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("current_revision_id", sa.String(36), nullable=True),
            sa.Column("revision_number", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(200), nullable=True),
            sa.Column("summary", sa.String(500), nullable=True),
            sa.Column("warning", sa.String(255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("public_id", name="uq_result_pages_public_id"),
        )
    if "result_page_revisions" not in tables:
        op.create_table(
            "result_page_revisions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("result_page_id", sa.String(36), sa.ForeignKey("result_pages.id", ondelete="CASCADE"), nullable=False),
            sa.Column("revision_number", sa.Integer(), nullable=False),
            sa.Column("execution_run_id", sa.String(36), sa.ForeignKey("task_execution_runs.id"), nullable=False),
            sa.Column("attempt_id", sa.String(36), sa.ForeignKey("task_attempts.id"), nullable=True),
            sa.Column("artifact_id", sa.String(36), sa.ForeignKey("task_artifacts.id"), nullable=False),
            sa.Column("content_json", sa.JSON(), nullable=False),
            sa.Column("provenance_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("result_page_id", "revision_number", name="uq_result_page_revisions_number"),
        )


def downgrade() -> None:
    op.drop_table("result_page_revisions")
    op.drop_table("result_pages")
