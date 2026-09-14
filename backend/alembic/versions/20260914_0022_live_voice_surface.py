"""Rename the Jarvis voice persistence surface to Live and retire legacy rows."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260914_0022"
down_revision = "20260913_0021"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables()

    # No new process can cleanly own these sessions once the legacy adapter is
    # gone. Preserve their history, but prevent them from being treated as
    # active by the Live coordinator.
    if "realtime_sessions" in tables:
        bind.execute(
            sa.text(
                "UPDATE realtime_sessions "
                "SET lifecycle_state = 'disconnected', disconnected_at = CURRENT_TIMESTAMP "
                "WHERE provider_adapter = 'openai' "
                "AND lifecycle_state IN ('connecting', 'ready')"
            )
        )

    renames = (
        ("realtime_sessions", "live_sessions"),
        ("voice_session_events", "live_session_events"),
        ("speech_deliveries", "live_speech_deliveries"),
        ("jarvis_realtime_trace_events", "jarvis_live_trace_events"),
    )
    for old, new in renames:
        if old in tables and new not in tables:
            op.rename_table(old, new)

    if "live_speech_deliveries" in _tables():
        columns = _columns("live_speech_deliveries")
        if "realtime_session_id" in columns and "live_session_id" not in columns:
            op.alter_column(
                "live_speech_deliveries",
                "realtime_session_id",
                new_column_name="live_session_id",
            )

    if "live_sessions" in _tables() and "audio_policy_json" in _columns("live_sessions"):
        op.drop_column("live_sessions", "audio_policy_json")


def downgrade() -> None:
    if "live_sessions" in _tables() and "audio_policy_json" not in _columns("live_sessions"):
        op.add_column("live_sessions", sa.Column("audio_policy_json", sa.JSON(), nullable=True))

    if "live_speech_deliveries" in _tables():
        columns = _columns("live_speech_deliveries")
        if "live_session_id" in columns and "realtime_session_id" not in columns:
            op.alter_column(
                "live_speech_deliveries",
                "live_session_id",
                new_column_name="realtime_session_id",
            )

    tables = _tables()
    for old, new in (
        ("realtime_sessions", "live_sessions"),
        ("voice_session_events", "live_session_events"),
        ("speech_deliveries", "live_speech_deliveries"),
        ("jarvis_realtime_trace_events", "jarvis_live_trace_events"),
    ):
        if new in tables and old not in tables:
            op.rename_table(new, old)
