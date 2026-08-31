"""Jarvis v2 continuity, scheduling, recall, streaming, and device state.

Revision ID: 20260831_0012
Revises: 20260830_0011
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260831_0012"
down_revision = "20260830_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jarvis_state") as batch:
        batch.add_column(sa.Column("focused_task_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("recent_task_refs_json", sa.JSON(), nullable=True))
        batch.add_column(
            sa.Column("live_event_cursor", sa.Integer(), server_default="0", nullable=False)
        )
        batch.create_foreign_key(
            "fk_jarvis_state_focused_task_id",
            "tasks",
            ["focused_task_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("tasks", recreate="always") as batch:
        batch.drop_constraint("ck_tasks_status", type_="check")
        batch.create_check_constraint(
            "ck_tasks_status",
            "status IN ('queued', 'running', 'waiting_for_input', 'waiting_until', "
            "'completed', 'failed', 'interrupted', 'cancelled')",
        )
        batch.add_column(sa.Column("waiting_until", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("resume_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("wait_reason", sa.Text(), nullable=True))
        batch.add_column(sa.Column("scheduled_delivery_json", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("scheduled_delivery_key", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("scheduled_delivered_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_unique_constraint(
            "uq_tasks_scheduled_delivery_key", ["scheduled_delivery_key"]
        )
    op.create_index("ix_tasks_resume_at", "tasks", ["status", "resume_at"])

    op.create_table(
        "jarvis_preferences",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("voice", sa.String(length=32), server_default="marin", nullable=False),
        sa.Column("concision", sa.String(length=32), server_default="concise", nullable=False),
        sa.Column("wake_behavior", sa.String(length=32), server_default="follow_up", nullable=False),
        sa.Column("developer_mode", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("android_privacy_json", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 'current'", name="ck_jarvis_preferences_singleton_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        "INSERT INTO jarvis_preferences "
        "(id, voice, concision, wake_behavior, developer_mode, android_privacy_json) "
        "VALUES ('current', 'marin', 'concise', 'follow_up', 0, '{}')"
    )

    op.create_table(
        "jarvis_live_events",
        sa.Column("sequence", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("source_domain", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=True),
        sa.Column("source_sequence", sa.Integer(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("sequence"),
        sa.UniqueConstraint("id", name="uq_jarvis_live_events_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_jarvis_live_events_idempotency"),
    )
    op.create_index("ix_jarvis_live_events_created", "jarvis_live_events", ["created_at"])

    op.create_table(
        "recall_documents",
        sa.Column("document_order", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("virtual_path", sa.String(length=1024), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("document_order"),
        sa.UniqueConstraint("id", name="uq_recall_documents_id"),
        sa.UniqueConstraint("virtual_path", name="uq_recall_documents_virtual_path"),
    )
    op.create_index("ix_recall_documents_source", "recall_documents", ["source_type", "source_id"])
    op.create_index("ix_recall_documents_timestamp", "recall_documents", ["source_timestamp"])

    op.create_table(
        "jarvis_devices",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("credential_hash", sa.String(length=128), nullable=False),
        sa.Column("platform", sa.String(length=32), server_default="android", nullable=False),
        sa.Column("lock_state", sa.String(length=32), server_default="unknown", nullable=False),
        sa.Column("privacy_json", sa.JSON(), nullable=True),
        sa.Column("event_cursor", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credential_hash"),
    )
    op.create_table(
        "jarvis_pairing_codes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash"),
    )
    op.create_index("ix_jarvis_pairing_codes_expiry", "jarvis_pairing_codes", ["expires_at"])

    _create_live_event_triggers()
    _create_recall_indexes_and_triggers()
    _rebuild_recall_documents()


def _create_live_event_triggers() -> None:
    op.execute("""
        CREATE TRIGGER jarvis_live_conversation_insert AFTER INSERT ON conversation_events
        WHEN EXISTS (
            SELECT 1 FROM conversations
            WHERE id = NEW.conversation_id AND conversation_kind = 'jarvis_daily'
        )
        BEGIN
            INSERT OR IGNORE INTO jarvis_live_events
                (id, event_type, source_domain, source_id, source_sequence,
                 payload_json, idempotency_key, created_at)
            VALUES
                (lower(hex(randomblob(16))), NEW.event_type, 'conversation', NEW.id,
                 NEW.sequence, NEW.payload_json, 'conversation:' || NEW.id, NEW.created_at);
        END
    """)
    op.execute("""
        CREATE TRIGGER jarvis_live_task_insert AFTER INSERT ON task_events
        BEGIN
            INSERT OR IGNORE INTO jarvis_live_events
                (id, event_type, source_domain, source_id, source_sequence,
                 payload_json, idempotency_key, created_at)
            VALUES
                (lower(hex(randomblob(16))), NEW.event_type, 'task', NEW.id,
                 NEW.sequence, NEW.payload_json, 'task:' || NEW.id, NEW.created_at);
        END
    """)
    op.execute("""
        CREATE TRIGGER jarvis_live_session_insert AFTER INSERT ON voice_session_events
        BEGIN
            INSERT OR IGNORE INTO jarvis_live_events
                (id, event_type, source_domain, source_id, source_sequence,
                 payload_json, idempotency_key, created_at)
            VALUES
                (lower(hex(randomblob(16))), NEW.event_type, 'session', NEW.id,
                 NEW.sequence, NEW.payload_json, 'session:' || NEW.id, NEW.created_at);
        END
    """)


def _create_recall_indexes_and_triggers() -> None:
    op.execute("""
        CREATE VIRTUAL TABLE recall_fts USING fts5(
            title, content, virtual_path,
            content='recall_documents', content_rowid='document_order', tokenize='unicode61'
        )
    """)
    op.execute("""
        CREATE VIRTUAL TABLE recall_fts_trigram USING fts5(
            content, content='recall_documents', content_rowid='document_order', tokenize='trigram'
        )
    """)
    op.execute("""
        CREATE TRIGGER recall_documents_ai AFTER INSERT ON recall_documents BEGIN
            INSERT INTO recall_fts(rowid, title, content, virtual_path)
            VALUES (NEW.document_order, NEW.title, NEW.content, NEW.virtual_path);
            INSERT INTO recall_fts_trigram(rowid, content) VALUES (NEW.document_order, NEW.content);
        END
    """)
    op.execute("""
        CREATE TRIGGER recall_documents_ad AFTER DELETE ON recall_documents BEGIN
            INSERT INTO recall_fts(recall_fts, rowid, title, content, virtual_path)
            VALUES ('delete', OLD.document_order, OLD.title, OLD.content, OLD.virtual_path);
            INSERT INTO recall_fts_trigram(recall_fts_trigram, rowid, content)
            VALUES ('delete', OLD.document_order, OLD.content);
        END
    """)
    op.execute("""
        CREATE TRIGGER recall_documents_au AFTER UPDATE ON recall_documents BEGIN
            INSERT INTO recall_fts(recall_fts, rowid, title, content, virtual_path)
            VALUES ('delete', OLD.document_order, OLD.title, OLD.content, OLD.virtual_path);
            INSERT INTO recall_fts_trigram(recall_fts_trigram, rowid, content)
            VALUES ('delete', OLD.document_order, OLD.content);
            INSERT INTO recall_fts(rowid, title, content, virtual_path)
            VALUES (NEW.document_order, NEW.title, NEW.content, NEW.virtual_path);
            INSERT INTO recall_fts_trigram(rowid, content) VALUES (NEW.document_order, NEW.content);
        END
    """)
    op.execute("""
        CREATE TRIGGER recall_message_insert AFTER INSERT ON conversation_messages
        WHEN NEW.role IN ('user', 'assistant') AND NEW.communication_state != 'unheard'
        BEGIN
            INSERT INTO recall_documents
                (id, virtual_path, source_type, source_id, title, content, source_timestamp)
            VALUES (
                lower(hex(randomblob(16))),
                'conversation/' || NEW.conversation_id || '/message/' || NEW.id || '.txt',
                'conversation', NEW.id,
                COALESCE((SELECT title FROM conversations WHERE id = NEW.conversation_id), 'Conversation'),
                NEW.content, NEW.created_at
            ) ON CONFLICT(virtual_path) DO UPDATE SET
                title=excluded.title, content=excluded.content,
                source_timestamp=excluded.source_timestamp, updated_at=CURRENT_TIMESTAMP;
        END
    """)
    op.execute("""
        CREATE TRIGGER recall_task_insert AFTER INSERT ON tasks BEGIN
            INSERT INTO recall_documents
                (id, virtual_path, source_type, source_id, title, content, source_timestamp)
            VALUES (
                lower(hex(randomblob(16))), 'task/' || NEW.id || '/summary.txt', 'task', NEW.id,
                substr(NEW.goal, 1, 255),
                NEW.goal || char(10) || 'Status: ' || NEW.status || char(10) || COALESCE(NEW.terminal_summary, ''),
                NEW.created_at
            ) ON CONFLICT(virtual_path) DO UPDATE SET
                title=excluded.title, content=excluded.content,
                source_timestamp=excluded.source_timestamp, updated_at=CURRENT_TIMESTAMP;
        END
    """)
    op.execute("""
        CREATE TRIGGER recall_task_update AFTER UPDATE OF goal, status, terminal_summary ON tasks BEGIN
            UPDATE recall_documents SET
                title=substr(NEW.goal, 1, 255),
                content=NEW.goal || char(10) || 'Status: ' || NEW.status || char(10) || COALESCE(NEW.terminal_summary, ''),
                updated_at=CURRENT_TIMESTAMP
            WHERE virtual_path='task/' || NEW.id || '/summary.txt';
        END
    """)
    op.execute("""
        CREATE TRIGGER recall_task_event_insert AFTER INSERT ON task_events
        WHEN NEW.event_type IN (
            'task.queued', 'task.progress', 'task.requires_input', 'task.question.answered',
            'task.waiting_until', 'task.resumed', 'task.reminder.due', 'task.completed',
            'task.failed', 'task.interrupted', 'task.cancelled'
        )
        BEGIN
            INSERT INTO recall_documents
                (id, virtual_path, source_type, source_id, title, content, source_timestamp)
            VALUES (
                lower(hex(randomblob(16))),
                'task/' || NEW.task_id || '/event/' || NEW.id || '.txt',
                'task', NEW.id,
                COALESCE((SELECT substr(goal, 1, 255) FROM tasks WHERE id=NEW.task_id), 'Task update'),
                NEW.event_type || char(10) || COALESCE(NEW.payload_json, ''), NEW.created_at
            ) ON CONFLICT(virtual_path) DO NOTHING;
        END
    """)
    op.execute("""
        CREATE TRIGGER recall_question_insert AFTER INSERT ON task_questions BEGIN
            INSERT INTO recall_documents
                (id, virtual_path, source_type, source_id, title, content, source_timestamp)
            VALUES (
                lower(hex(randomblob(16))),
                'task/' || NEW.task_id || '/question/' || NEW.id || '.txt',
                'task', NEW.id, 'Task question',
                NEW.question || char(10) || COALESCE(NEW.answer, ''), NEW.asked_at
            ) ON CONFLICT(virtual_path) DO UPDATE SET
                content=excluded.content, updated_at=CURRENT_TIMESTAMP;
        END
    """)
    op.execute("""
        CREATE TRIGGER recall_question_update AFTER UPDATE OF answer, state ON task_questions BEGIN
            UPDATE recall_documents SET
                content=NEW.question || char(10) || COALESCE(NEW.answer, ''), updated_at=CURRENT_TIMESTAMP
            WHERE virtual_path='task/' || NEW.task_id || '/question/' || NEW.id || '.txt';
        END
    """)
    op.execute("""
        CREATE TRIGGER recall_memory_insert AFTER INSERT ON memory_items
        WHEN NEW.status = 'approved'
        BEGIN
            INSERT INTO recall_documents
                (id, virtual_path, source_type, source_id, title, content, source_timestamp)
            VALUES (
                lower(hex(randomblob(16))), 'memory/' || NEW.id || '.txt', 'memory', NEW.id,
                NEW.title, NEW.content, NEW.created_at
            ) ON CONFLICT(virtual_path) DO UPDATE SET
                title=excluded.title, content=excluded.content, updated_at=CURRENT_TIMESTAMP;
        END
    """)
    op.execute("""
        CREATE TRIGGER recall_memory_update AFTER UPDATE OF title, content, status ON memory_items BEGIN
            DELETE FROM recall_documents WHERE virtual_path='memory/' || NEW.id || '.txt' AND NEW.status != 'approved';
            INSERT INTO recall_documents
                (id, virtual_path, source_type, source_id, title, content, source_timestamp)
            SELECT lower(hex(randomblob(16))), 'memory/' || NEW.id || '.txt', 'memory', NEW.id,
                   NEW.title, NEW.content, NEW.created_at
            WHERE NEW.status='approved'
            ON CONFLICT(virtual_path) DO UPDATE SET
                title=excluded.title, content=excluded.content, updated_at=CURRENT_TIMESTAMP;
        END
    """)


def _rebuild_recall_documents() -> None:
    op.execute("""
        INSERT OR IGNORE INTO recall_documents
            (id, virtual_path, source_type, source_id, title, content, source_timestamp)
        SELECT lower(hex(randomblob(16))),
               'conversation/' || m.conversation_id || '/message/' || m.id || '.txt',
               'conversation', m.id, COALESCE(c.title, 'Conversation'), m.content, m.created_at
        FROM conversation_messages m JOIN conversations c ON c.id=m.conversation_id
        WHERE m.role IN ('user', 'assistant') AND m.communication_state != 'unheard'
    """)
    op.execute("""
        INSERT OR IGNORE INTO recall_documents
            (id, virtual_path, source_type, source_id, title, content, source_timestamp)
        SELECT lower(hex(randomblob(16))), 'task/' || id || '/summary.txt', 'task', id,
               substr(goal, 1, 255),
               goal || char(10) || 'Status: ' || status || char(10) || COALESCE(terminal_summary, ''),
               created_at
        FROM tasks
    """)
    op.execute("""
        INSERT OR IGNORE INTO recall_documents
            (id, virtual_path, source_type, source_id, title, content, source_timestamp)
        SELECT lower(hex(randomblob(16))), 'memory/' || id || '.txt', 'memory', id,
               title, content, created_at
        FROM memory_items WHERE status='approved'
    """)


def downgrade() -> None:
    for trigger in [
        "recall_memory_update", "recall_memory_insert", "recall_question_update",
        "recall_question_insert", "recall_task_event_insert", "recall_task_update",
        "recall_task_insert", "recall_message_insert", "recall_documents_au",
        "recall_documents_ad", "recall_documents_ai", "jarvis_live_session_insert",
        "jarvis_live_task_insert", "jarvis_live_conversation_insert",
    ]:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    op.execute("DROP TABLE IF EXISTS recall_fts_trigram")
    op.execute("DROP TABLE IF EXISTS recall_fts")
    op.drop_table("jarvis_pairing_codes")
    op.drop_table("jarvis_devices")
    op.drop_table("recall_documents")
    op.drop_table("jarvis_live_events")
    op.drop_table("jarvis_preferences")
    op.drop_index("ix_tasks_resume_at", table_name="tasks")
    with op.batch_alter_table("tasks", recreate="always") as batch:
        batch.drop_constraint("uq_tasks_scheduled_delivery_key", type_="unique")
        batch.drop_constraint("ck_tasks_status", type_="check")
        batch.create_check_constraint(
            "ck_tasks_status",
            "status IN ('queued', 'running', 'waiting_for_input', 'completed', "
            "'failed', 'interrupted', 'cancelled')",
        )
        batch.drop_column("scheduled_delivered_at")
        batch.drop_column("scheduled_delivery_key")
        batch.drop_column("scheduled_delivery_json")
        batch.drop_column("wait_reason")
        batch.drop_column("resume_at")
        batch.drop_column("waiting_until")
    with op.batch_alter_table("jarvis_state") as batch:
        batch.drop_constraint("fk_jarvis_state_focused_task_id", type_="foreignkey")
        batch.drop_column("live_event_cursor")
        batch.drop_column("recent_task_refs_json")
        batch.drop_column("focused_task_id")
