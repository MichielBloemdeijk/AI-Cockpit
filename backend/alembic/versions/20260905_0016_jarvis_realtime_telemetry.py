"""Add metadata-only Jarvis Realtime timing traces.

Revision ID: 20260905_0016
Revises: 20260902_0015
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260905_0016"
down_revision = "20260902_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "jarvis_realtime_trace_events" in inspector.get_table_names():
        return
    op.create_table(
        "jarvis_realtime_trace_events",
        sa.Column("sequence", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("activation_id", sa.String(length=64), nullable=True),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("provider_response_id", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("monotonic_ms", sa.Float(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("detail_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_jarvis_realtime_trace_events_trace",
        "jarvis_realtime_trace_events",
        ["trace_id", "sequence"],
    )
    op.create_index(
        "ix_jarvis_realtime_trace_events_created",
        "jarvis_realtime_trace_events",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_table("jarvis_realtime_trace_events")
