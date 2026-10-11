"""Pin portable task context to each execution run."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260923_0025"
down_revision = "20260923_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "task_context_bundles" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "task_context_bundles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("execution_run_id", sa.String(36), sa.ForeignKey("task_execution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("execution_run_id", name="uq_task_context_bundles_run"),
    )


def downgrade() -> None:
    op.drop_table("task_context_bundles")
