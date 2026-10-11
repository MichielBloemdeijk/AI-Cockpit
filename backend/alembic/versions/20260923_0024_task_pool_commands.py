"""Audit and deduplicate administrative task-pool controls."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260923_0024"
down_revision = "20260922_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "task_pool_commands" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "task_pool_commands",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("target_worker_id", sa.String(36), nullable=True),
        sa.Column("affected_worker_ids_json", sa.JSON(), nullable=True),
        sa.Column("requester", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_task_pool_commands_idempotency"),
    )


def downgrade() -> None:
    op.drop_table("task_pool_commands")
