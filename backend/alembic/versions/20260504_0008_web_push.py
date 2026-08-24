"""Add Web Push subscription and delivery persistence.

Revision ID: 20260504_0008
Revises: 20260504_0007
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260504_0008"
down_revision = "20260504_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.Text(), nullable=False),
        sa.Column("auth", sa.Text(), nullable=False),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("endpoint", name="uq_push_subscriptions_endpoint"),
    )
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("subscription_id", sa.String(length=36), sa.ForeignKey("push_subscriptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", sa.String(length=36), sa.ForeignKey("conversation_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("subscription_id", "event_id", name="uq_notification_delivery_subscription_event"),
    )
    op.create_index("ix_notification_deliveries_event", "notification_deliveries", ["event_id"])
    op.create_index("ix_notification_deliveries_status", "notification_deliveries", ["status"])


def downgrade() -> None:
    op.drop_index("ix_notification_deliveries_status", table_name="notification_deliveries")
    op.drop_index("ix_notification_deliveries_event", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")
    op.drop_table("push_subscriptions")
