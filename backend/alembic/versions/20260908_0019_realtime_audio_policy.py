"""Persist the immutable audio policy used by each Realtime session."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260908_0019"
down_revision = "20260906_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("realtime_sessions")}
    if "audio_policy_json" not in columns:
        op.add_column(
            "realtime_sessions",
            sa.Column("audio_policy_json", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("realtime_sessions")}
    if "audio_policy_json" in columns:
        op.drop_column("realtime_sessions", "audio_policy_json")
