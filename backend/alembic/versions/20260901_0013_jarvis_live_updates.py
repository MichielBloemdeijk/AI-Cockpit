"""Make Jarvis task updates durable and explicitly correlated.

Revision ID: 20260901_0013
Revises: 20260831_0012
"""
from __future__ import annotations

from alembic import op


revision = "20260901_0013"
down_revision = "20260831_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE tasks
        SET presentation_policy = CASE lower(presentation_policy)
            WHEN 'all' THEN 'follow_progress'
            WHEN 'none' THEN 'silent'
            WHEN 'never' THEN 'silent'
            WHEN 'silent' THEN 'silent'
            WHEN 'follow_progress' THEN 'follow_progress'
            ELSE 'critical_only'
        END
        """
    )
    op.execute(
        """
        UPDATE task_presentations
        SET state = CASE
            WHEN state IN ('reserved', 'deferred') THEN 'pending'
            WHEN state = 'folded' THEN 'superseded'
            ELSE state
        END,
        reserved_session_id = NULL,
        reserved_generation = NULL,
        reserved_at = NULL,
        defer_until = NULL
        """
    )
    op.execute(
        "ALTER TABLE notification_outbox ADD COLUMN task_event_id VARCHAR(36) "
        "REFERENCES task_events(id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE notification_outbox ADD COLUMN acknowledgement_state VARCHAR(32)"
    )
    op.execute(
        """
        UPDATE notification_outbox
        SET task_event_id = json_extract(payload_json, '$.task_event_id'),
            acknowledgement_state = json_extract(payload_json, '$.acknowledgement_state')
        """
    )
    op.create_index(
        "uq_notification_outbox_task_event",
        "notification_outbox",
        ["task_event_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_notification_outbox_task_event", table_name="notification_outbox")
    op.drop_column("notification_outbox", "acknowledgement_state")
    op.drop_column("notification_outbox", "task_event_id")
    op.execute(
        """
        UPDATE tasks
        SET presentation_policy = CASE presentation_policy
            WHEN 'follow_progress' THEN 'all'
            WHEN 'silent' THEN 'none'
            ELSE 'natural'
        END
        """
    )
