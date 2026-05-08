"""add phase 2 chat settings metadata

Revision ID: 20260422_0003
Revises: 20260421_0002
Create Date: 2026-04-22 09:00:00

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260422_0003"
down_revision = "20260421_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("session_metadata_json", sa.JSON(), nullable=True))
    op.create_table(
        "app_settings",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("chat_defaults_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_column("conversations", "session_metadata_json")