"""Persist provider input-item correlation on Realtime trace events."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260908_0020"
down_revision = "20260908_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("jarvis_realtime_trace_events")}
    if "provider_input_item_id" not in columns:
        op.add_column(
            "jarvis_realtime_trace_events",
            sa.Column("provider_input_item_id", sa.String(length=255), nullable=True),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("jarvis_realtime_trace_events")}
    if "provider_input_item_id" in columns:
        op.drop_column("jarvis_realtime_trace_events", "provider_input_item_id")
