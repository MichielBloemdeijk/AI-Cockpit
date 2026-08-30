"""Retire primary-voice and browser-only delivery persistence.

Revision ID: 20260830_0011
Revises: 20260827_0010
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260830_0011"
down_revision = "20260827_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("notification_deliveries")
    op.drop_index("uq_conversations_primary_voice", table_name="conversations")
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("is_primary_voice")


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(
            sa.Column(
                "is_primary_voice",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
    op.create_index(
        "uq_conversations_primary_voice",
        "conversations",
        ["is_primary_voice"],
        unique=True,
        sqlite_where=sa.text("is_primary_voice = 1 AND archived_at IS NULL"),
    )
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("subscription_id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("spoken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["conversation_events.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["push_subscriptions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "subscription_id",
            "event_id",
            name="uq_notification_delivery_subscription_event",
        ),
    )
    op.create_index(
        "ix_notification_deliveries_event",
        "notification_deliveries",
        ["event_id"],
    )
    op.create_index(
        "ix_notification_deliveries_status",
        "notification_deliveries",
        ["status"],
    )
