"""Add browser sequence correlation to Realtime telemetry.

Revision ID: 20260906_0018
Revises: 20260905_0017
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260906_0018"
down_revision = "20260905_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("jarvis_realtime_trace_events")
    }
    if "client_sequence" not in columns:
        op.add_column(
            "jarvis_realtime_trace_events",
            sa.Column("client_sequence", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("jarvis_realtime_trace_events", "client_sequence")
