"""add intermediate phase foundation metadata

Revision ID: 20260423_0004
Revises: 20260422_0003
Create Date: 2026-04-23 10:00:00

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260423_0004"
down_revision = "20260422_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("workspace_path", sa.String(length=512), nullable=True))

    op.create_table(
        "conversation_branches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("branch_key", sa.String(length=128), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("parent_branch_key", sa.String(length=128), nullable=True),
        sa.Column("branched_from_message_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["branched_from_message_id"], ["conversation_messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "branch_key", name="uq_conversation_branches_conversation_branch_key"),
    )
    op.create_index(
        "ix_conversation_branches_conversation_created",
        "conversation_branches",
        ["conversation_id", "created_at"],
    )

    op.add_column("memory_items", sa.Column("knowledge_path", sa.String(length=512), nullable=True))
    op.add_column("memory_items", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("memory_items", "deleted_at")
    op.drop_column("memory_items", "knowledge_path")
    op.drop_index("ix_conversation_branches_conversation_created", table_name="conversation_branches")
    op.drop_table("conversation_branches")
    op.drop_column("conversations", "workspace_path")