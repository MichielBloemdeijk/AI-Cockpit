"""Use model-reported task attention as the durable delivery authority.

Revision ID: 20260913_0021
Revises: 20260908_0020
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260913_0021"
down_revision = "20260908_0020"
branch_labels = None
depends_on = None


def _column_names(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    legacy_presentation_columns = _column_names("task_presentations")
    additions = (
        ("handled_at", sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True)),
        ("handled_by", sa.Column("handled_by", sa.String(length=64), nullable=True)),
        ("handled_turn_id", sa.Column("handled_turn_id", sa.String(length=36), nullable=True)),
        ("handled_event_id", sa.Column("handled_event_id", sa.String(length=36), nullable=True)),
        ("lease_token", sa.Column("lease_token", sa.String(length=64), nullable=True)),
        (
            "commentary_attempted_at",
            sa.Column("commentary_attempted_at", sa.DateTime(timezone=True), nullable=True),
        ),
    )
    for name, column in additions:
        if name not in legacy_presentation_columns:
            op.add_column("task_presentations", column)

    max_sequence = int(
        bind.execute(sa.text("SELECT COALESCE(MAX(sequence), 0) FROM task_events")).scalar() or 0
    )
    if not sa.inspect(bind).has_table("attention_migration_state"):
        op.create_table(
            "attention_migration_state",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("task_event_cutoff", sa.Integer(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        bind.execute(
            sa.text(
                "INSERT INTO attention_migration_state (id, task_event_cutoff) "
                "VALUES ('current', :cutoff)"
            ),
            {"cutoff": max_sequence},
        )

    # Notification evidence wins over generic legacy-state conversion.
    outbox_columns = _column_names("notification_outbox")
    if "acknowledged_at" in outbox_columns and "acknowledgement_state" in outbox_columns:
        bind.execute(
            sa.text(
                "UPDATE task_presentations SET state = 'handled', "
                "handled_at = (SELECT acknowledged_at FROM notification_outbox "
                "WHERE notification_outbox.task_event_id = task_presentations.source_task_event_id), "
                "handled_by = CASE (SELECT acknowledgement_state FROM notification_outbox "
                "WHERE notification_outbox.task_event_id = task_presentations.source_task_event_id) "
                "WHEN 'opened' THEN 'migration_notification_opened' "
                "WHEN 'spoken' THEN 'migration_legacy_spoken' "
                "WHEN 'snoozed' THEN 'migration_legacy_snooze' "
                "ELSE 'migration_notification_acknowledged' END "
                "WHERE source_task_event_id IN (SELECT task_event_id FROM notification_outbox "
                "WHERE acknowledged_at IS NOT NULL)"
            )
        )
        bind.execute(
            sa.text(
                "UPDATE notification_outbox SET status = 'delivered' "
                "WHERE status = 'acknowledged'"
            )
        )

    # Preserve real presented timestamps; do not invent missing evidence. A
    # retry after the table-rebuild phase no longer has the legacy columns.
    if "presented_at" in legacy_presentation_columns:
        bind.execute(
            sa.text(
                "UPDATE task_presentations SET "
                "handled_at = CASE WHEN state IN ('presented', 'acknowledged', 'deferred') "
                "THEN COALESCE(handled_at, presented_at) ELSE handled_at END, "
                "handled_turn_id = CASE WHEN state IN ('presented', 'acknowledged', 'deferred') "
                "THEN COALESCE(handled_turn_id, presented_turn_id) ELSE handled_turn_id END, "
                "handled_by = CASE WHEN state IN ('presented', 'acknowledged', 'deferred') "
                "AND handled_by IS NULL AND presented_at IS NOT NULL "
                "THEN 'migration_legacy_presented' "
                "WHEN state IN ('presented', 'acknowledged', 'deferred') AND handled_by IS NULL "
                "THEN 'migration_legacy_state' ELSE handled_by END, "
                "state = CASE "
                "WHEN state IN ('presented', 'acknowledged', 'deferred') THEN 'handled' "
                "WHEN state IN ('reserved', 'pending') THEN 'pending' "
                "WHEN state = 'folded' THEN 'superseded' ELSE state END"
            )
        )
    else:
        bind.execute(
            sa.text(
                "UPDATE task_presentations SET state = CASE "
                "WHEN state IN ('presented', 'acknowledged', 'deferred') THEN 'handled' "
                "WHEN state IN ('reserved', 'pending') THEN 'pending' "
                "WHEN state = 'folded' THEN 'superseded' ELSE state END"
            )
        )
    bind.execute(
        sa.text(
            "UPDATE task_presentations SET lease_token = NULL, reserved_session_id = NULL, "
            "reserved_generation = NULL, reserved_at = NULL, defer_until = NULL "
            "WHERE state != 'pending'"
        )
    )

    # Retire pre-cutoff and orphaned due reminders at the stable migration time.
    bind.execute(
        sa.text(
            "UPDATE task_presentations SET state = 'handled', "
            "handled_at = COALESCE(handled_at, (SELECT created_at FROM attention_migration_state "
            "WHERE id = 'current')), handled_by = 'migration_legacy_retired', "
            "reserved_session_id = NULL, reserved_generation = NULL, reserved_at = NULL, "
            "lease_token = NULL, defer_until = NULL, "
            "last_error = 'legacy_attention_retired_at_migration' "
            "WHERE state = 'pending' AND candidate_type = 'reminder' AND ("
            "source_task_event_id IS NULL OR source_task_event_id IN ("
            "SELECT id FROM task_events WHERE event_type = 'task.reminder.due' "
            "AND sequence <= :cutoff))"
        ),
        {"cutoff": max_sequence},
    )

    bind.execute(
        sa.text(
            "WITH ranked AS ("
            "SELECT id, ROW_NUMBER() OVER (PARTITION BY source_task_event_id "
            "ORDER BY CASE WHEN state = 'handled' THEN 0 ELSE 1 END, created_at, id) AS rank "
            "FROM task_presentations WHERE source_task_event_id IS NOT NULL) "
            "UPDATE task_presentations SET state = 'superseded', "
            "reserved_session_id = NULL, reserved_generation = NULL, reserved_at = NULL, "
            "lease_token = NULL, defer_until = NULL, "
            "last_error = COALESCE(last_error, 'duplicate_legacy_attention_superseded') "
            "WHERE id IN (SELECT id FROM ranked WHERE rank > 1)"
        )
    )

    # Every migration-handled row owns a global, session-neutral audit event.
    bind.execute(
        sa.text(
            "INSERT INTO jarvis_live_events "
            "(id, event_type, source_domain, source_id, source_sequence, payload_json, "
            "idempotency_key, created_at) "
            "SELECT lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-' || "
            "lower(hex(randomblob(2))) || '-' || lower(hex(randomblob(2))) || '-' || "
            "lower(hex(randomblob(6))), 'attention.handled', 'attention', tp.id, NULL, "
            "json_object('presentation_id', tp.id, 'task_id', tp.task_id, "
            "'source_task_event_id', tp.source_task_event_id, 'handled_by', tp.handled_by, "
            "'migration', 1), 'attention-migration-handled:' || tp.id, "
            "COALESCE(tp.handled_at, (SELECT created_at FROM attention_migration_state "
            "WHERE id = 'current')) "
            "FROM task_presentations tp WHERE tp.state = 'handled' "
            "AND tp.handled_by LIKE 'migration_%' "
            "ON CONFLICT(idempotency_key) DO NOTHING"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE task_presentations SET handled_event_id = ("
            "SELECT id FROM jarvis_live_events WHERE idempotency_key = "
            "'attention-migration-handled:' || task_presentations.id) "
            "WHERE state = 'handled' AND handled_by LIKE 'migration_%'"
        )
    )

    # Migration runs with senders stopped, so closed in-flight work is stale.
    bind.execute(
        sa.text(
            "UPDATE notification_outbox SET status = 'superseded', next_attempt_at = NULL "
            "WHERE task_event_id IN (SELECT source_task_event_id FROM task_presentations "
            "WHERE state IN ('handled', 'superseded') AND source_task_event_id IS NOT NULL) "
            "AND status IN ('queued', 'failed', 'sending')"
        )
    )

    # Fail visibly on any schema error while making completed rebuild phases
    # detectable, so a process interruption can retry the same revision.
    inspector = sa.inspect(bind)
    presentation_checks = {
        item.get("name") for item in inspector.get_check_constraints("task_presentations")
    }
    presentation_fks = {
        (tuple(item.get("constrained_columns") or ()), item.get("referred_table"))
        for item in inspector.get_foreign_keys("task_presentations")
    }
    needs_presentation_rebuild = bool(
        {"presented_at", "presented_turn_id", "presented_delivery_id"}
        & legacy_presentation_columns
    ) or "ck_task_presentations_state" not in presentation_checks or (
        ("handled_turn_id",), "conversation_messages"
    ) not in presentation_fks or (
        ("handled_event_id",), "jarvis_live_events"
    ) not in presentation_fks
    if needs_presentation_rebuild:
        with op.batch_alter_table("task_presentations", recreate="always") as batch:
            if "ck_task_presentations_state" not in presentation_checks:
                batch.create_check_constraint(
                    "ck_task_presentations_state",
                    "state IN ('pending', 'handled', 'superseded')",
                )
            if (("handled_turn_id",), "conversation_messages") not in presentation_fks:
                batch.create_foreign_key(
                    "fk_task_presentations_handled_turn_id",
                    "conversation_messages",
                    ["handled_turn_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
            if (("handled_event_id",), "jarvis_live_events") not in presentation_fks:
                batch.create_foreign_key(
                    "fk_task_presentations_handled_event_id",
                    "jarvis_live_events",
                    ["handled_event_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
            for legacy_name in ("presented_at", "presented_turn_id", "presented_delivery_id"):
                if legacy_name in legacy_presentation_columns:
                    batch.drop_column(legacy_name)

    inspector = sa.inspect(bind)
    outbox_checks = {
        item.get("name") for item in inspector.get_check_constraints("notification_outbox")
    }
    if (
        "ck_notification_outbox_status" not in outbox_checks
        or {"acknowledged_at", "acknowledgement_state"} & outbox_columns
    ):
        with op.batch_alter_table("notification_outbox", recreate="always") as batch:
            if "ck_notification_outbox_status" not in outbox_checks:
                batch.create_check_constraint(
                    "ck_notification_outbox_status",
                    "status IN ('queued', 'sending', 'delivered', 'failed', 'superseded')",
                )
            if "acknowledged_at" in outbox_columns:
                batch.drop_column("acknowledged_at")
            if "acknowledgement_state" in outbox_columns:
                batch.drop_column("acknowledgement_state")

    existing_indexes = {
        item.get("name") for item in sa.inspect(bind).get_indexes("task_presentations")
    }
    if "uq_task_presentations_session_lease" not in existing_indexes:
        op.create_index(
            "uq_task_presentations_session_lease",
            "task_presentations",
            ["reserved_session_id"],
            unique=True,
            sqlite_where=sa.text("state = 'pending' AND lease_token IS NOT NULL"),
        )
    if "uq_task_presentations_source_task_event" not in existing_indexes:
        op.create_index(
            "uq_task_presentations_source_task_event",
            "task_presentations",
            ["source_task_event_id"],
            unique=True,
            sqlite_where=sa.text("source_task_event_id IS NOT NULL"),
        )


def downgrade() -> None:
    op.drop_index("uq_task_presentations_session_lease", table_name="task_presentations")
    op.drop_index("uq_task_presentations_source_task_event", table_name="task_presentations")
    if sa.inspect(op.get_bind()).has_table("attention_migration_state"):
        op.drop_table("attention_migration_state")
