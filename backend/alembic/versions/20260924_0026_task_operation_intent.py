"""Retain bounded operation intent for post-exit reconciliation."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260924_0026"
down_revision = "20260923_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("task_operations")}
    if "intent_json" not in columns:
        op.add_column("task_operations", sa.Column("intent_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("task_operations", "intent_json")
