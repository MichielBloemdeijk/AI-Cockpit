"""add generated app leases

Revision ID: 20260503_0006
Revises: 20260429_0005
Create Date: 2026-05-03 12:00:00

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260503_0006"
down_revision = "20260429_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("generated_apps")}
    existing_indexes = {index["name"] for index in inspector.get_indexes("generated_apps")}

    if "lease_task_run_id" not in existing_columns:
        op.add_column("generated_apps", sa.Column("lease_task_run_id", sa.String(length=36), nullable=True))
    if "lease_conversation_id" not in existing_columns:
        op.add_column("generated_apps", sa.Column("lease_conversation_id", sa.String(length=36), nullable=True))
    if "lease_acquired_at" not in existing_columns:
        op.add_column("generated_apps", sa.Column("lease_acquired_at", sa.DateTime(timezone=True), nullable=True))
    if "ix_generated_apps_lease_task" not in existing_indexes:
        op.create_index("ix_generated_apps_lease_task", "generated_apps", ["lease_task_run_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("generated_apps")}
    existing_indexes = {index["name"] for index in inspector.get_indexes("generated_apps")}

    if "ix_generated_apps_lease_task" in existing_indexes:
        op.drop_index("ix_generated_apps_lease_task", table_name="generated_apps")
    if "lease_acquired_at" in existing_columns:
        op.drop_column("generated_apps", "lease_acquired_at")
    if "lease_conversation_id" in existing_columns:
        op.drop_column("generated_apps", "lease_conversation_id")
    if "lease_task_run_id" in existing_columns:
        op.drop_column("generated_apps", "lease_task_run_id")
