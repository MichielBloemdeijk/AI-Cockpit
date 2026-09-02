"""Persist authoritative Jarvis temporal context.

Revision ID: 20260902_0014
Revises: 20260901_0013
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260902_0014"
down_revision = "20260901_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jarvis_preferences",
        sa.Column("time_zone", sa.String(length=255), nullable=False, server_default="Europe/Amsterdam"),
    )
    op.add_column("realtime_sessions", sa.Column("client_time_zone", sa.String(length=255), nullable=True))
    op.add_column("realtime_sessions", sa.Column("client_utc_offset_minutes", sa.Integer(), nullable=True))
    op.add_column(
        "realtime_sessions",
        sa.Column("client_observed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("realtime_sessions", "client_observed_at")
    op.drop_column("realtime_sessions", "client_utc_offset_minutes")
    op.drop_column("realtime_sessions", "client_time_zone")
    op.drop_column("jarvis_preferences", "time_zone")
