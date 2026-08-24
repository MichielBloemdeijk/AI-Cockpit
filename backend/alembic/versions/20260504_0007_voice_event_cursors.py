"""Add primary voice state and concurrency-safe event cursors.

Revision ID: 20260504_0007
Revises: 20260503_0006
"""
from __future__ import annotations

from collections import defaultdict

from alembic import op
import sqlalchemy as sa


revision = "20260504_0007"
down_revision = "20260503_0006"
branch_labels = None
depends_on = None


def _has_column(inspector: sa.Inspector, table: str, column: str) -> bool:
    return column in {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_column(inspector, "conversations", "is_primary_voice"):
        op.add_column(
            "conversations",
            sa.Column("is_primary_voice", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        )
    if not _has_column(inspector, "conversations", "last_event_sequence"):
        op.add_column(
            "conversations",
            sa.Column("last_event_sequence", sa.Integer(), nullable=False, server_default=sa.text("0")),
        )

    # Normalize historical cursors before enforcing uniqueness.  The existing
    # application normally has no duplicates, but this makes the migration
    # safe for databases written by older concurrent workers.
    rows = bind.execute(
        sa.text(
            "SELECT conversation_id, id, sequence FROM conversation_events "
            "ORDER BY conversation_id, sequence, created_at, id"
        )
    ).fetchall()
    by_conversation: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for conversation_id, event_id, sequence in rows:
        by_conversation[str(conversation_id)].append((str(event_id), int(sequence or 0)))

    for conversation_id, events in by_conversation.items():
        needs_normalization = any(sequence != index for index, (_event_id, sequence) in enumerate(events, start=1))
        if needs_normalization:
            for index, (event_id, _sequence) in enumerate(events, start=1):
                bind.execute(
                    sa.text("UPDATE conversation_events SET sequence = :sequence WHERE id = :event_id"),
                    {"sequence": -index, "event_id": event_id},
                )
            for index, (event_id, _sequence) in enumerate(events, start=1):
                bind.execute(
                    sa.text("UPDATE conversation_events SET sequence = :sequence WHERE id = :event_id"),
                    {"sequence": index, "event_id": event_id},
                )
        bind.execute(
            sa.text("UPDATE conversations SET last_event_sequence = :sequence WHERE id = :conversation_id"),
            {"sequence": len(events), "conversation_id": conversation_id},
        )

    existing_indexes = {index["name"] for index in inspector.get_indexes("conversation_events")}
    if "uq_conversation_events_conversation_sequence" not in existing_indexes:
        op.create_index(
            "uq_conversation_events_conversation_sequence",
            "conversation_events",
            ["conversation_id", "sequence"],
            unique=True,
        )

    conversation_indexes = {index["name"] for index in inspector.get_indexes("conversations")}
    if "uq_conversations_primary_voice" not in conversation_indexes:
        bind.exec_driver_sql(
            "CREATE UNIQUE INDEX uq_conversations_primary_voice "
            "ON conversations (is_primary_voice) "
            "WHERE is_primary_voice = 1 AND archived_at IS NULL"
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("DROP INDEX IF EXISTS uq_conversations_primary_voice")
    bind.exec_driver_sql("DROP INDEX IF EXISTS uq_conversation_events_conversation_sequence")
    inspector = sa.inspect(bind)
    if _has_column(inspector, "conversations", "last_event_sequence"):
        op.drop_column("conversations", "last_event_sequence")
    if _has_column(inspector, "conversations", "is_primary_voice"):
        op.drop_column("conversations", "is_primary_voice")
