"""add generated app registry

Revision ID: 20260429_0005
Revises: 20260423_0004
Create Date: 2026-04-29 12:00:00

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260429_0005"
down_revision = "20260423_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generated_apps",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("route_path", sa.String(length=255), nullable=False),
        sa.Column("frontend_root", sa.String(length=512), nullable=False),
        sa.Column("frontend_entry_path", sa.String(length=512), nullable=True),
        sa.Column("icon_asset_path", sa.String(length=512), nullable=True),
        sa.Column("cover_asset_path", sa.String(length=512), nullable=True),
        sa.Column("verification_status", sa.String(length=32), nullable=True),
        sa.Column("source_task_run_id", sa.String(length=36), nullable=True),
        sa.Column("source_conversation_id", sa.String(length=36), nullable=True),
        sa.Column("manifest_json", sa.JSON(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["source_conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_task_run_id"], ["conversation_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_generated_apps_updated_at", "generated_apps", ["updated_at"])
    op.create_index("ix_generated_apps_status", "generated_apps", ["status"])


def downgrade() -> None:
    op.drop_index("ix_generated_apps_status", table_name="generated_apps")
    op.drop_index("ix_generated_apps_updated_at", table_name="generated_apps")
    op.drop_table("generated_apps")