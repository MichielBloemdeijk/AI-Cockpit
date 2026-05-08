"""create conversation persistence schema

Revision ID: 20260421_0002
Revises: 20260421_0001
Create Date: 2026-04-21 00:10:00

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260421_0002"
down_revision = "20260421_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("mode_hint", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversations_updated_at", "conversations", ["updated_at"])

    op.create_table(
        "conversation_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("parent_run_id", sa.String(length=36), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_run_id"], ["conversation_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversation_runs_conversation_started",
        "conversation_runs",
        ["conversation_id", "started_at"],
    )
    op.create_index("ix_conversation_runs_status", "conversation_runs", ["status"])

    op.create_table(
        "conversation_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("branch_key", sa.String(length=128), nullable=True),
        sa.Column("parent_event_id", sa.String(length=36), nullable=True),
        sa.Column("actor_kind", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_event_id"], ["conversation_events.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["conversation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversation_events_conversation_created",
        "conversation_events",
        ["conversation_id", "created_at"],
    )
    op.create_index(
        "ix_conversation_events_run_sequence",
        "conversation_events",
        ["run_id", "sequence"],
    )
    op.create_index("ix_conversation_events_event_type", "conversation_events", ["event_type"])

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("source_event_id", sa.String(length=36), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("author_label", sa.String(length=255), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_format", sa.String(length=32), nullable=False),
        sa.Column("is_final", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["conversation_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_event_id"], ["conversation_events.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversation_messages_conversation_created",
        "conversation_messages",
        ["conversation_id", "created_at"],
    )

    op.create_table(
        "conversation_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("source_event_id", sa.String(length=36), nullable=True),
        sa.Column("artifact_type", sa.String(length=64), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("content_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["conversation_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_event_id"], ["conversation_events.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversation_artifacts_conversation_created",
        "conversation_artifacts",
        ["conversation_id", "created_at"],
    )

    op.create_table(
        "memory_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("source_conversation_id", sa.String(length=36), nullable=False),
        sa.Column("source_event_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["source_conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_event_id"], ["conversation_events.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_memory_items_conversation_created",
        "memory_items",
        ["source_conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_memory_items_conversation_created", table_name="memory_items")
    op.drop_table("memory_items")
    op.drop_index("ix_conversation_artifacts_conversation_created", table_name="conversation_artifacts")
    op.drop_table("conversation_artifacts")
    op.drop_index("ix_conversation_messages_conversation_created", table_name="conversation_messages")
    op.drop_table("conversation_messages")
    op.drop_index("ix_conversation_events_event_type", table_name="conversation_events")
    op.drop_index("ix_conversation_events_run_sequence", table_name="conversation_events")
    op.drop_index("ix_conversation_events_conversation_created", table_name="conversation_events")
    op.drop_table("conversation_events")
    op.drop_index("ix_conversation_runs_status", table_name="conversation_runs")
    op.drop_index("ix_conversation_runs_conversation_started", table_name="conversation_runs")
    op.drop_table("conversation_runs")
    op.drop_index("ix_conversations_updated_at", table_name="conversations")
    op.drop_table("conversations")