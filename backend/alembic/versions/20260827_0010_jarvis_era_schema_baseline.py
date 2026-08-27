"""Establish the clean Jarvis-era durable schema.

Revision ID: 20260827_0010
Revises: 20260504_0009
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260827_0010"
down_revision = "20260504_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("generated_apps") as batch:
        batch.create_foreign_key(
            "fk_generated_apps_lease_task_run_id",
            "conversation_runs",
            ["lease_task_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_generated_apps_lease_conversation_id",
            "conversations",
            ["lease_conversation_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("conversations") as batch:
        batch.add_column(
            sa.Column("conversation_kind", sa.String(length=32), server_default="standard", nullable=False)
        )
        batch.add_column(
            sa.Column("lifecycle_status", sa.String(length=32), server_default="open", nullable=False)
        )
        batch.add_column(sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("jarvis_day_key", sa.Date(), nullable=True))
        batch.add_column(sa.Column("jarvis_day_sequence", sa.Integer(), nullable=True))
        batch.create_check_constraint(
            "ck_conversations_kind_day_fields",
            "(conversation_kind = 'standard' AND jarvis_day_key IS NULL AND jarvis_day_sequence IS NULL) "
            "OR (conversation_kind = 'jarvis_daily' AND jarvis_day_key IS NOT NULL "
            "AND jarvis_day_sequence > 0)",
        )
        batch.create_check_constraint(
            "ck_conversations_lifecycle_closed_at",
            "(lifecycle_status = 'open' AND closed_at IS NULL) "
            "OR (lifecycle_status = 'closed' AND closed_at IS NOT NULL)",
        )
        batch.create_unique_constraint(
            "uq_conversations_jarvis_day_sequence", ["jarvis_day_key", "jarvis_day_sequence"]
        )
    op.create_index(
        "ix_conversations_kind_updated", "conversations", ["conversation_kind", "updated_at"]
    )
    op.create_index("ix_conversations_lifecycle", "conversations", ["lifecycle_status"])

    with op.batch_alter_table("conversation_events") as batch:
        batch.add_column(sa.Column("idempotency_key", sa.String(length=255), nullable=True))
        batch.create_unique_constraint(
            "uq_conversation_events_conversation_idempotency",
            ["conversation_id", "idempotency_key"],
        )

    op.create_table(
        "jarvis_state",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("current_log_id", sa.String(length=36), nullable=True),
        sa.Column("jarvis_day_key", sa.Date(), nullable=True),
        sa.Column("jarvis_day_sequence", sa.Integer(), nullable=True),
        sa.Column("state_revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 'current'", name="ck_jarvis_state_singleton_id"),
        sa.CheckConstraint(
            "(current_log_id IS NULL AND jarvis_day_key IS NULL AND jarvis_day_sequence IS NULL) "
            "OR (current_log_id IS NOT NULL AND jarvis_day_key IS NOT NULL "
            "AND jarvis_day_sequence > 0)",
            name="ck_jarvis_state_pointer_fields",
        ),
        sa.ForeignKeyConstraint(["current_log_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("current_log_id"),
    )

    op.create_table(
        "worker_instances",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("process_identity", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_worker_instances_heartbeat", "worker_instances", ["heartbeat_at"])

    op.create_table(
        "tasks",
        sa.Column("creation_order", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("origin_surface", sa.String(length=32), nullable=False),
        sa.Column("origin_conversation_id", sa.String(length=36), nullable=True),
        sa.Column("origin_turn_id", sa.String(length=36), nullable=True),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("current_attempt_id", sa.String(length=36), nullable=True),
        sa.Column("cancellation_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("importance", sa.String(length=32), server_default="normal", nullable=False),
        sa.Column("presentation_policy", sa.String(length=32), server_default="natural", nullable=False),
        sa.Column("terminal_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'waiting_for_input', 'completed', "
            "'failed', 'interrupted', 'cancelled')",
            name="ck_tasks_status",
        ),
        sa.ForeignKeyConstraint(["origin_conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("creation_order"),
        sa.UniqueConstraint("id", name="uq_tasks_public_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_tasks_idempotency_key"),
    )
    op.create_index("ix_tasks_status_order", "tasks", ["status", "creation_order"])
    op.create_index("ix_tasks_origin_conversation", "tasks", ["origin_conversation_id", "creation_order"])

    op.create_table(
        "task_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=36), nullable=True),
        sa.Column("workspace_path", sa.String(length=512), nullable=True),
        sa.Column("checkpoint_json", sa.JSON(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_reason", sa.Text(), nullable=True),
        sa.Column("agent_runtime_reference", sa.String(length=255), nullable=True),
        sa.Column("usage_summary_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("attempt_number > 0", name="ck_task_attempts_number_positive"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["worker_id"], ["worker_instances.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "attempt_number", name="uq_task_attempts_task_number"),
    )
    op.create_index("ix_task_attempts_task_number", "task_attempts", ["task_id", "attempt_number"])
    op.create_index("ix_task_attempts_worker_heartbeat", "task_attempts", ["worker_id", "heartbeat_at"])

    op.create_table(
        "task_events",
        sa.Column("sequence", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("causality_json", sa.JSON(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["attempt_id"], ["task_attempts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("sequence"),
        sa.UniqueConstraint("id", name="uq_task_events_public_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_task_events_idempotency_key"),
    )
    op.create_index("ix_task_events_task_sequence", "task_events", ["task_id", "sequence"])
    op.create_index("ix_task_events_type_sequence", "task_events", ["event_type", "sequence"])

    _create_task_detail_tables()
    _create_realtime_tables()
    _add_message_truth_columns()


def _create_task_detail_tables() -> None:
    op.create_table(
        "task_questions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("asked_attempt_id", sa.String(length=36), nullable=True),
        sa.Column("answered_attempt_id", sa.String(length=36), nullable=True),
        sa.Column("source_task_event_id", sa.String(length=36), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=32), server_default="asked", nullable=False),
        sa.Column("asked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asked_attempt_id"], ["task_attempts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["answered_attempt_id"], ["task_attempts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_task_event_id"], ["task_events.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_questions_task_state", "task_questions", ["task_id", "state"])

    op.create_table(
        "task_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=True),
        sa.Column("source_task_event_id", sa.String(length=36), nullable=True),
        sa.Column("artifact_type", sa.String(length=64), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("uri", sa.String(length=1024), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("content_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["attempt_id"], ["task_attempts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_task_event_id"], ["task_events.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_artifacts_task_created", "task_artifacts", ["task_id", "created_at"])

    op.create_table(
        "task_targets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_key", sa.String(length=1024), nullable=False),
        sa.Column("mutable", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "resource_type", "resource_key", name="uq_task_targets_resource"),
    )
    op.create_index(
        "ix_task_targets_task_resource", "task_targets", ["task_id", "resource_type", "resource_key"]
    )

    op.create_table(
        "resource_leases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_key", sa.String(length=1024), nullable=False),
        sa.Column("owner_attempt_id", sa.String(length=36), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("renewed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_attempt_id"], ["task_attempts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resource_type", "resource_key", name="uq_resource_leases_resource"),
    )
    op.create_index("ix_resource_leases_owner", "resource_leases", ["owner_attempt_id"])
    op.create_index("ix_resource_leases_expiry", "resource_leases", ["expires_at"])


def _create_realtime_tables() -> None:
    op.create_table(
        "realtime_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("provider_adapter", sa.String(length=64), nullable=False),
        sa.Column("lifecycle_state", sa.String(length=32), nullable=False),
        sa.Column("controller_id", sa.String(length=255), nullable=True),
        sa.Column("provider_call_id_ciphertext", sa.Text(), nullable=True),
        sa.Column("last_event_sequence", sa.Integer(), server_default="0", nullable=False),
        sa.Column("timing_summary_json", sa.JSON(), nullable=True),
        sa.Column("usage_summary_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("generation > 0", name="ck_realtime_sessions_generation_positive"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_realtime_sessions_conversation_created",
        "realtime_sessions",
        ["conversation_id", "created_at"],
    )

    op.create_table(
        "voice_session_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["realtime_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "sequence", name="uq_voice_session_events_session_sequence"),
        sa.UniqueConstraint(
            "session_id", "idempotency_key", name="uq_voice_session_events_session_idempotency"
        ),
    )
    op.create_index(
        "ix_voice_session_events_session_created",
        "voice_session_events",
        ["session_id", "created_at"],
    )

    op.create_table(
        "task_presentations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_type", sa.String(length=64), nullable=False),
        sa.Column("coalescing_key", sa.String(length=255), nullable=False),
        sa.Column("importance", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("reserved_session_id", sa.String(length=36), nullable=True),
        sa.Column("reserved_generation", sa.Integer(), nullable=True),
        sa.Column("required_facts_json", sa.JSON(), nullable=True),
        sa.Column("source_task_event_id", sa.String(length=36), nullable=True),
        sa.Column("presented_turn_id", sa.String(length=36), nullable=True),
        sa.Column("presented_delivery_id", sa.String(length=36), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("defer_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("presented_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reserved_session_id"], ["realtime_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_task_event_id"], ["task_events.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["presented_turn_id"], ["conversation_messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_task_presentations_idempotency_key"),
    )
    op.create_index(
        "ix_task_presentations_state_created", "task_presentations", ["state", "created_at"]
    )
    op.create_index("ix_task_presentations_task", "task_presentations", ["task_id", "created_at"])

    op.create_table(
        "speech_deliveries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("assistant_turn_id", sa.String(length=36), nullable=True),
        sa.Column("realtime_session_id", sa.String(length=36), nullable=False),
        sa.Column("provider_response_id", sa.String(length=255), nullable=False),
        sa.Column("provider_item_id", sa.String(length=255), nullable=False),
        sa.Column("generated_transcript", sa.Text(), nullable=False),
        sa.Column("heard_transcript", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("generation_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("playback_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("interrupted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_task_id", sa.String(length=36), nullable=True),
        sa.Column("source_task_event_id", sa.String(length=36), nullable=True),
        sa.Column("presentation_id", sa.String(length=36), nullable=True),
        sa.Column("provider_causality_json", sa.JSON(), nullable=True),
        sa.Column("timing_summary_json", sa.JSON(), nullable=True),
        sa.Column("usage_summary_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["assistant_turn_id"], ["conversation_messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["realtime_session_id"], ["realtime_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_task_event_id"], ["task_events.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["presentation_id"], ["task_presentations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "realtime_session_id",
            "provider_response_id",
            "provider_item_id",
            name="uq_speech_deliveries_provider_item",
        ),
    )
    op.create_index(
        "ix_speech_deliveries_session_created",
        "speech_deliveries",
        ["realtime_session_id", "created_at"],
    )
    op.create_index("ix_speech_deliveries_state", "speech_deliveries", ["state"])

    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("question_id", sa.String(length=36), nullable=True),
        sa.Column("event_kind", sa.String(length=64), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("deep_link", sa.String(length=1024), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["question_id"], ["task_questions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_notification_outbox_dedupe_key"),
    )
    op.create_index(
        "ix_notification_outbox_status_created", "notification_outbox", ["status", "created_at"]
    )


def _add_message_truth_columns() -> None:
    with op.batch_alter_table("conversation_messages") as batch:
        batch.add_column(sa.Column("modality", sa.String(length=16), server_default="text", nullable=False))
        batch.add_column(
            sa.Column("execution_mode", sa.String(length=32), server_default="agent", nullable=False)
        )
        batch.add_column(
            sa.Column("communication_state", sa.String(length=32), server_default="completed", nullable=False)
        )
        batch.add_column(sa.Column("source_task_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("source_task_event_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("presented_in_turn_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_conversation_messages_source_task_id", "tasks", ["source_task_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_foreign_key(
            "fk_conversation_messages_source_task_event_id",
            "task_events",
            ["source_task_event_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_conversation_messages_presented_in_turn_id",
            "conversation_messages",
            ["presented_in_turn_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("conversation_messages") as batch:
        batch.drop_constraint("fk_conversation_messages_presented_in_turn_id", type_="foreignkey")
        batch.drop_constraint("fk_conversation_messages_source_task_event_id", type_="foreignkey")
        batch.drop_constraint("fk_conversation_messages_source_task_id", type_="foreignkey")
        batch.drop_column("presented_in_turn_id")
        batch.drop_column("source_task_event_id")
        batch.drop_column("source_task_id")
        batch.drop_column("communication_state")
        batch.drop_column("execution_mode")
        batch.drop_column("modality")

    for table in [
        "notification_outbox",
        "speech_deliveries",
        "task_presentations",
        "voice_session_events",
        "realtime_sessions",
        "resource_leases",
        "task_targets",
        "task_artifacts",
        "task_questions",
        "task_events",
        "task_attempts",
        "tasks",
        "worker_instances",
        "jarvis_state",
    ]:
        op.drop_table(table)

    with op.batch_alter_table("conversation_events") as batch:
        batch.drop_constraint("uq_conversation_events_conversation_idempotency", type_="unique")
        batch.drop_column("idempotency_key")

    op.drop_index("ix_conversations_lifecycle", table_name="conversations")
    op.drop_index("ix_conversations_kind_updated", table_name="conversations")
    with op.batch_alter_table("conversations") as batch:
        batch.drop_constraint("uq_conversations_jarvis_day_sequence", type_="unique")
        batch.drop_constraint("ck_conversations_lifecycle_closed_at", type_="check")
        batch.drop_constraint("ck_conversations_kind_day_fields", type_="check")
        batch.drop_column("jarvis_day_sequence")
        batch.drop_column("jarvis_day_key")
        batch.drop_column("closed_at")
        batch.drop_column("lifecycle_status")
        batch.drop_column("conversation_kind")

    with op.batch_alter_table("generated_apps") as batch:
        batch.drop_constraint("fk_generated_apps_lease_conversation_id", type_="foreignkey")
        batch.drop_constraint("fk_generated_apps_lease_task_run_id", type_="foreignkey")
