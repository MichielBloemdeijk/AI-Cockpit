"""Add an incremental task-attention projection checkpoint.

Revision ID: 20260905_0017
Revises: 20260905_0016
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260905_0017"
down_revision = "20260905_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "task_projection_checkpoints" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "task_projection_checkpoints",
        sa.Column("projection_key", sa.String(length=64), primary_key=True),
        sa.Column("last_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("task_projection_checkpoints")
