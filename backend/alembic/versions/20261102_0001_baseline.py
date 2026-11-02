"""Baseline schema, squashed from migrations 0001-0043 (audit finding P01).

Tables, keys and indexes come from the ORM (`app.db.tables`); the drift test in
tests/test_schema_drift.py keeps them equal. The recall full-text index and the
triggers that feed it and the Jarvis live-event log are SQLite DDL the ORM does
not model, so they are listed here as SQL.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261102_0001"
down_revision = None
branch_labels = None
depends_on = None

SQLITE_DDL = (
    """
CREATE VIRTUAL TABLE recall_fts USING fts5(
    title, content, virtual_path,
    content='recall_documents', content_rowid='document_order', tokenize='unicode61'
)
    """,
    """
CREATE VIRTUAL TABLE recall_fts_trigram USING fts5(
    content, content='recall_documents', content_rowid='document_order', tokenize='trigram'
)
    """,
    """
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
    """,
    """
CREATE TRIGGER jarvis_live_session_insert AFTER INSERT ON live_session_events
BEGIN
    INSERT OR IGNORE INTO jarvis_live_events
        (id, event_type, source_domain, source_id, source_sequence,
         payload_json, idempotency_key, created_at)
    VALUES
        (lower(hex(randomblob(16))), NEW.event_type, 'session', NEW.session_id,
         NEW.sequence, NEW.payload_json, 'session:' || NEW.id, NEW.created_at);
END
    """,
    """
CREATE TRIGGER jarvis_live_task_insert AFTER INSERT ON task_events
BEGIN
    INSERT OR IGNORE INTO jarvis_live_events
        (id, event_type, source_domain, source_id, source_sequence,
         payload_json, idempotency_key, created_at)
    VALUES
        (lower(hex(randomblob(16))), NEW.event_type, 'task', NEW.id,
         NEW.sequence, NEW.payload_json, 'task:' || NEW.id, NEW.created_at);
END
    """,
    """
CREATE TRIGGER recall_documents_ad AFTER DELETE ON recall_documents BEGIN
    INSERT INTO recall_fts(recall_fts, rowid, title, content, virtual_path)
    VALUES ('delete', OLD.document_order, OLD.title, OLD.content, OLD.virtual_path);
    INSERT INTO recall_fts_trigram(recall_fts_trigram, rowid, content)
    VALUES ('delete', OLD.document_order, OLD.content);
END
    """,
    """
CREATE TRIGGER recall_documents_ai AFTER INSERT ON recall_documents BEGIN
    INSERT INTO recall_fts(rowid, title, content, virtual_path)
    VALUES (NEW.document_order, NEW.title, NEW.content, NEW.virtual_path);
    INSERT INTO recall_fts_trigram(rowid, content) VALUES (NEW.document_order, NEW.content);
END
    """,
    """
CREATE TRIGGER recall_documents_au AFTER UPDATE ON recall_documents BEGIN
    INSERT INTO recall_fts(recall_fts, rowid, title, content, virtual_path)
    VALUES ('delete', OLD.document_order, OLD.title, OLD.content, OLD.virtual_path);
    INSERT INTO recall_fts_trigram(recall_fts_trigram, rowid, content)
    VALUES ('delete', OLD.document_order, OLD.content);
    INSERT INTO recall_fts(rowid, title, content, virtual_path)
    VALUES (NEW.document_order, NEW.title, NEW.content, NEW.virtual_path);
    INSERT INTO recall_fts_trigram(rowid, content) VALUES (NEW.document_order, NEW.content);
END
    """,
    """
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
    """,
    """
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
    """,
    """
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
    """,
    """
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
    """,
    """
CREATE TRIGGER recall_question_update AFTER UPDATE OF answer, state ON task_questions BEGIN
    UPDATE recall_documents SET
        content=NEW.question || char(10) || COALESCE(NEW.answer, ''), updated_at=CURRENT_TIMESTAMP
    WHERE virtual_path='task/' || NEW.task_id || '/question/' || NEW.id || '.txt';
END
    """,
    """
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
    """,
    """
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
    """,
    """
CREATE TRIGGER recall_task_update AFTER UPDATE OF goal, status, terminal_summary ON tasks BEGIN
    UPDATE recall_documents SET
        title=substr(NEW.goal, 1, 255),
        content=NEW.goal || char(10) || 'Status: ' || NEW.status || char(10) || COALESCE(NEW.terminal_summary, ''),
        updated_at=CURRENT_TIMESTAMP
    WHERE virtual_path='task/' || NEW.id || '/summary.txt';
END
    """,
)


def upgrade():
    # WAL is persistent database state; readers stay responsive during a writer.
    op.execute("PRAGMA journal_mode=WAL")
    op.create_table('app_settings',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('chat_defaults_json', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('connector_tool_permissions',
    sa.Column('identity', sa.String(length=255), nullable=False),
    sa.Column('connector', sa.String(length=100), nullable=False),
    sa.Column('account', sa.String(length=100), nullable=False),
    sa.Column('tool', sa.String(length=100), nullable=False),
    sa.Column('mode', sa.String(length=32), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('identity')
    )
    op.create_table('conversations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=True),
    sa.Column('mode_hint', sa.String(length=64), nullable=True),
    sa.Column('session_metadata_json', sa.JSON(), nullable=True),
    sa.Column('workspace_path', sa.String(length=512), nullable=True),
    sa.Column('conversation_kind', sa.String(length=32), server_default='standard', nullable=False),
    sa.Column('lifecycle_status', sa.String(length=32), server_default='open', nullable=False),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('jarvis_day_key', sa.Date(), nullable=True),
    sa.Column('jarvis_day_sequence', sa.Integer(), nullable=True),
    sa.Column('last_event_sequence', sa.Integer(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(conversation_kind = 'standard' AND jarvis_day_key IS NULL AND jarvis_day_sequence IS NULL) OR (conversation_kind = 'jarvis_daily' AND jarvis_day_key IS NOT NULL AND jarvis_day_sequence > 0)", name='ck_conversations_kind_day_fields'),
    sa.CheckConstraint("(lifecycle_status = 'open' AND closed_at IS NULL) OR (lifecycle_status = 'closed' AND closed_at IS NOT NULL)", name='ck_conversations_lifecycle_closed_at'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('jarvis_day_key', 'jarvis_day_sequence', name='uq_conversations_jarvis_day_sequence')
    )
    op.create_index('ix_conversations_kind_updated', 'conversations', ['conversation_kind', 'updated_at'], unique=False)
    op.create_index('ix_conversations_lifecycle', 'conversations', ['lifecycle_status'], unique=False)
    op.create_index('ix_conversations_updated_at', 'conversations', ['updated_at'], unique=False)
    op.create_table('jarvis_devices',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('label', sa.String(length=255), nullable=False),
    sa.Column('credential_hash', sa.String(length=128), nullable=False),
    sa.Column('platform', sa.String(length=32), nullable=False),
    sa.Column('lock_state', sa.String(length=32), server_default='unknown', nullable=False),
    sa.Column('privacy_json', sa.JSON(), nullable=True),
    sa.Column('event_cursor', sa.Integer(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('credential_hash')
    )
    op.create_table('jarvis_live_events',
    sa.Column('sequence', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('event_type', sa.String(length=128), nullable=False),
    sa.Column('source_domain', sa.String(length=32), nullable=False),
    sa.Column('source_id', sa.String(length=36), nullable=True),
    sa.Column('source_sequence', sa.Integer(), nullable=True),
    sa.Column('target_device_id', sa.String(length=36), nullable=True),
    sa.Column('payload_json', sa.JSON(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('sequence'),
    sa.UniqueConstraint('id', name='uq_jarvis_live_events_id'),
    sa.UniqueConstraint('idempotency_key', name='uq_jarvis_live_events_idempotency')
    )
    op.create_index('ix_jarvis_live_events_created', 'jarvis_live_events', ['created_at'], unique=False)
    op.create_table('jarvis_live_trace_events',
    sa.Column('sequence', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('trace_id', sa.String(length=64), nullable=False),
    sa.Column('client_sequence', sa.Integer(), nullable=True),
    sa.Column('activation_id', sa.String(length=64), nullable=True),
    sa.Column('session_id', sa.String(length=36), nullable=True),
    sa.Column('generation', sa.Integer(), nullable=True),
    sa.Column('request_id', sa.String(length=64), nullable=True),
    sa.Column('provider_response_id', sa.String(length=255), nullable=True),
    sa.Column('provider_input_item_id', sa.String(length=255), nullable=True),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('event_type', sa.String(length=128), nullable=False),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('monotonic_ms', sa.Float(), nullable=True),
    sa.Column('duration_ms', sa.Float(), nullable=True),
    sa.Column('detail_json', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('sequence')
    )
    op.create_index('ix_jarvis_live_trace_events_created', 'jarvis_live_trace_events', ['created_at'], unique=False)
    op.create_index('ix_jarvis_live_trace_events_trace', 'jarvis_live_trace_events', ['trace_id', 'sequence'], unique=False)
    op.create_table('jarvis_pairing_codes',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('code_hash', sa.String(length=128), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code_hash')
    )
    op.create_index('ix_jarvis_pairing_codes_expiry', 'jarvis_pairing_codes', ['expires_at'], unique=False)
    op.create_table('jarvis_preferences',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('voice', sa.String(length=32), server_default='marin', nullable=False),
    sa.Column('concision', sa.String(length=32), server_default='concise', nullable=False),
    sa.Column('wake_behavior', sa.String(length=32), server_default='follow_up', nullable=False),
    sa.Column('developer_mode', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('android_privacy_json', sa.JSON(), nullable=True),
    sa.Column('time_zone', sa.String(length=255), server_default='Europe/Amsterdam', nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("id = 'current'", name='ck_jarvis_preferences_singleton_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('push_subscriptions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('endpoint', sa.Text(), nullable=False),
    sa.Column('p256dh', sa.Text(), nullable=False),
    sa.Column('auth', sa.Text(), nullable=False),
    sa.Column('user_agent', sa.String(length=512), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('endpoint')
    )
    op.create_table('recall_documents',
    sa.Column('document_order', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('virtual_path', sa.String(length=1024), nullable=False),
    sa.Column('source_type', sa.String(length=32), nullable=False),
    sa.Column('source_id', sa.String(length=36), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('source_timestamp', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('document_order'),
    sa.UniqueConstraint('id', name='uq_recall_documents_id'),
    sa.UniqueConstraint('virtual_path', name='uq_recall_documents_virtual_path')
    )
    op.create_index('ix_recall_documents_source', 'recall_documents', ['source_type', 'source_id'], unique=False)
    op.create_index('ix_recall_documents_timestamp', 'recall_documents', ['source_timestamp'], unique=False)
    op.create_table('runtime_component_health',
    sa.Column('component', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_failure_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('failure_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('component')
    )
    op.create_table('runtime_credential_leases',
    sa.Column('profile_key', sa.String(length=64), nullable=False),
    sa.Column('owner_kind', sa.String(length=32), nullable=False),
    sa.Column('owner_id', sa.String(length=255), nullable=False),
    sa.Column('host', sa.String(length=255), nullable=False),
    sa.Column('pid', sa.Integer(), nullable=False),
    sa.Column('creation_stamp', sa.String(length=64), nullable=False),
    sa.Column('acquired_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('profile_key')
    )
    op.create_table('task_context_blobs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('storage_path', sa.String(length=1024), nullable=False),
    sa.Column('byte_length', sa.Integer(), nullable=False),
    sa.Column('mime_type', sa.String(length=64), nullable=False),
    sa.Column('width', sa.Integer(), nullable=False),
    sa.Column('height', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('sha256')
    )
    op.create_table('task_pool_commands',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('idempotency_key', sa.String(length=255), nullable=False),
    sa.Column('action', sa.String(length=32), nullable=False),
    sa.Column('target_worker_id', sa.String(length=36), nullable=True),
    sa.Column('affected_worker_ids_json', sa.JSON(), nullable=True),
    sa.Column('requester', sa.String(length=64), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('state', sa.String(length=32), nullable=False),
    sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key', name='uq_task_pool_commands_idempotency')
    )
    op.create_table('task_projection_checkpoints',
    sa.Column('projection_key', sa.String(length=64), nullable=False),
    sa.Column('last_sequence', sa.Integer(), server_default='0', nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('projection_key')
    )
    op.create_table('task_supervisor_leases',
    sa.Column('lease_key', sa.String(length=64), nullable=False),
    sa.Column('generation', sa.Integer(), server_default='1', nullable=False),
    sa.Column('owner_identity', sa.String(length=255), nullable=False),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('lease_key')
    )
    op.create_table('worker_instances',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('state', sa.String(length=32), nullable=False),
    sa.Column('process_identity', sa.String(length=255), nullable=True),
    sa.Column('supervisor_generation', sa.Integer(), server_default='1', nullable=False),
    sa.Column('assignment_attempt_id', sa.String(length=36), nullable=True),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('metadata_json', sa.JSON(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('stopped_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_worker_instances_heartbeat', 'worker_instances', ['heartbeat_at'], unique=False)
    op.create_table('worker_tool_policy',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('configuration_json', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('conversation_runs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('parent_run_id', sa.String(length=36), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('metadata_json', sa.JSON(), nullable=True),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['parent_run_id'], ['conversation_runs.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_conversation_runs_conversation_started', 'conversation_runs', ['conversation_id', 'started_at'], unique=False)
    op.create_index('ix_conversation_runs_status', 'conversation_runs', ['status'], unique=False)
    op.create_table('live_sessions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('generation', sa.Integer(), nullable=False),
    sa.Column('provider_adapter', sa.String(length=64), nullable=False),
    sa.Column('lifecycle_state', sa.String(length=32), nullable=False),
    sa.Column('controller_id', sa.String(length=255), nullable=True),
    sa.Column('activation_id', sa.String(length=64), nullable=True),
    sa.Column('activation_source', sa.String(length=32), nullable=True),
    sa.Column('workflow_catalog_revision', sa.String(length=64), nullable=True),
    sa.Column('provider_call_id_ciphertext', sa.Text(), nullable=True),
    sa.Column('last_event_sequence', sa.Integer(), server_default='0', nullable=False),
    sa.Column('timing_summary_json', sa.JSON(), nullable=True),
    sa.Column('usage_summary_json', sa.JSON(), nullable=True),
    sa.Column('client_time_zone', sa.String(length=255), nullable=True),
    sa.Column('client_utc_offset_minutes', sa.Integer(), nullable=True),
    sa.Column('client_observed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('ready_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('disconnected_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint('generation > 0', name='ck_live_sessions_generation_positive'),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_live_sessions_conversation_created', 'live_sessions', ['conversation_id', 'created_at'], unique=False)
    op.create_table('tasks',
    sa.Column('creation_order', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('origin_surface', sa.String(length=32), nullable=False),
    sa.Column('origin_conversation_id', sa.String(length=36), nullable=True),
    sa.Column('origin_turn_id', sa.String(length=36), nullable=True),
    sa.Column('goal', sa.Text(), nullable=False),
    sa.Column('execution_kind', sa.String(length=32), server_default='agent', nullable=False),
    sa.Column('status', sa.String(length=32), server_default='queued', nullable=False),
    sa.Column('status_reason', sa.Text(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=255), nullable=False),
    sa.Column('current_attempt_id', sa.String(length=36), nullable=True),
    sa.Column('current_run_id', sa.String(length=36), nullable=True),
    sa.Column('pending_control_id', sa.String(length=36), nullable=True),
    sa.Column('goal_revision', sa.Integer(), server_default='1', nullable=False),
    sa.Column('control_revision', sa.Integer(), server_default='0', nullable=False),
    sa.Column('workspace_generation', sa.Integer(), server_default='1', nullable=False),
    sa.Column('queue_entered_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('cancellation_requested_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('importance', sa.String(length=32), server_default='normal', nullable=False),
    sa.Column('presentation_policy', sa.String(length=32), server_default='critical_only', nullable=False),
    sa.Column('terminal_summary', sa.Text(), nullable=True),
    sa.Column('waiting_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resume_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('wait_reason', sa.Text(), nullable=True),
    sa.Column('scheduled_delivery_json', sa.JSON(), nullable=True),
    sa.Column('scheduled_delivery_key', sa.String(length=255), nullable=True),
    sa.Column('scheduled_delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('queued', 'waiting_for_worker', 'running', 'waiting_for_input', 'waiting_until', 'completed', 'paused', 'failed', 'interrupted', 'cancelled')", name='ck_tasks_status'),
    sa.ForeignKeyConstraint(['origin_conversation_id'], ['conversations.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('creation_order'),
    sa.UniqueConstraint('id', name='uq_tasks_public_id'),
    sa.UniqueConstraint('idempotency_key', name='uq_tasks_idempotency_key'),
    sa.UniqueConstraint('scheduled_delivery_key')
    )
    op.create_index('ix_tasks_origin_conversation', 'tasks', ['origin_conversation_id', 'creation_order'], unique=False)
    op.create_index('ix_tasks_resume_at', 'tasks', ['status', 'resume_at'], unique=False)
    op.create_index('ix_tasks_status_order', 'tasks', ['status', 'creation_order'], unique=False)
    op.create_table('assistant_promises',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('session_id', sa.String(length=36), nullable=False),
    sa.Column('generation', sa.Integer(), nullable=False),
    sa.Column('text', sa.String(length=500), nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=True),
    sa.Column('source_key', sa.String(length=64), nullable=False),
    sa.Column('source_fragment_ids', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('resolution', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['session_id'], ['live_sessions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('session_id', 'source_key', name='uq_assistant_promise_source')
    )
    op.create_table('conversation_events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=True),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('branch_key', sa.String(length=128), nullable=True),
    sa.Column('parent_event_id', sa.String(length=36), nullable=True),
    sa.Column('actor_kind', sa.String(length=32), nullable=False),
    sa.Column('event_type', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('schema_version', sa.Integer(), nullable=False),
    sa.Column('payload_json', sa.JSON(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=255), nullable=True),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['parent_event_id'], ['conversation_events.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['run_id'], ['conversation_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('conversation_id', 'idempotency_key', name='uq_conversation_events_conversation_idempotency')
    )
    op.create_index('ix_conversation_events_conversation_created', 'conversation_events', ['conversation_id', 'created_at'], unique=False)
    op.create_index('ix_conversation_events_event_type', 'conversation_events', ['event_type'], unique=False)
    op.create_index('ix_conversation_events_run_sequence', 'conversation_events', ['run_id', 'sequence'], unique=False)
    op.create_index('uq_conversation_events_conversation_sequence', 'conversation_events', ['conversation_id', 'sequence'], unique=True)
    op.create_table('conversation_observations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('session_id', sa.String(length=36), nullable=False),
    sa.Column('generation', sa.Integer(), nullable=False),
    sa.Column('evidence_hash', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('input_json', sa.JSON(), nullable=False),
    sa.Column('result_json', sa.JSON(), nullable=True),
    sa.Column('usage_json', sa.JSON(), nullable=True),
    sa.Column('error', sa.String(length=512), nullable=True),
    sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['session_id'], ['live_sessions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('session_id', 'evidence_hash', name='uq_conversation_observation_evidence')
    )
    op.create_table('jarvis_state',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('current_log_id', sa.String(length=36), nullable=True),
    sa.Column('jarvis_day_key', sa.Date(), nullable=True),
    sa.Column('jarvis_day_sequence', sa.Integer(), nullable=True),
    sa.Column('state_revision', sa.Integer(), server_default='0', nullable=False),
    sa.Column('situation_revision', sa.Integer(), server_default='0', nullable=False),
    sa.Column('conversation_summary', sa.Text(), server_default='', nullable=False),
    sa.Column('focused_task_id', sa.String(length=36), nullable=True),
    sa.Column('recent_task_refs_json', sa.JSON(), nullable=True),
    sa.Column('live_event_cursor', sa.Integer(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("id = 'current'", name='ck_jarvis_state_singleton_id'),
    sa.CheckConstraint('(current_log_id IS NULL AND jarvis_day_key IS NULL AND jarvis_day_sequence IS NULL) OR (current_log_id IS NOT NULL AND jarvis_day_key IS NOT NULL AND jarvis_day_sequence > 0)', name='ck_jarvis_state_pointer_fields'),
    sa.ForeignKeyConstraint(['current_log_id'], ['conversations.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['focused_task_id'], ['tasks.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('current_log_id')
    )
    op.create_table('live_admission_intents',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('session_id', sa.String(length=36), nullable=False),
    sa.Column('generation', sa.Integer(), nullable=False),
    sa.Column('turn_scope', sa.String(length=255), nullable=False),
    sa.Column('intent_id', sa.String(length=128), nullable=False),
    sa.Column('tool_name', sa.String(length=64), nullable=False),
    sa.Column('input_digest', sa.String(length=64), nullable=False),
    sa.Column('target_task_id', sa.String(length=36), nullable=True),
    sa.Column('output_json', sa.JSON(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['live_sessions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('live_session_events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('session_id', sa.String(length=36), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('event_type', sa.String(length=128), nullable=False),
    sa.Column('payload_json', sa.JSON(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['live_sessions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('session_id', 'idempotency_key', name='uq_live_session_events_session_idempotency'),
    sa.UniqueConstraint('session_id', 'sequence', name='uq_live_session_events_session_sequence')
    )
    op.create_index('ix_live_session_events_session_created', 'live_session_events', ['session_id', 'created_at'], unique=False)
    op.create_table('orchestration_decisions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('issue_key', sa.String(length=64), nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=False),
    sa.Column('execution_run_id', sa.String(length=36), nullable=True),
    sa.Column('question_id', sa.String(length=36), nullable=True),
    sa.Column('trigger_kind', sa.String(length=32), nullable=False),
    sa.Column('source_event_ids_json', sa.JSON(), nullable=False),
    sa.Column('source_sequence', sa.Integer(), nullable=False),
    sa.Column('observed_json', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('claim_generation', sa.Integer(), nullable=False),
    sa.Column('claim_owner', sa.String(length=255), nullable=True),
    sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('context_json', sa.JSON(), nullable=True),
    sa.Column('context_digest', sa.String(length=64), nullable=True),
    sa.Column('model', sa.String(length=128), nullable=False),
    sa.Column('effort', sa.String(length=16), nullable=False),
    sa.Column('auth_profile', sa.String(length=255), nullable=False),
    sa.Column('native_binding_json', sa.JSON(), nullable=True),
    sa.Column('outcome_json', sa.JSON(), nullable=True),
    sa.Column('decision_reason', sa.Text(), nullable=True),
    sa.Column('last_error', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('issue_key')
    )
    op.create_index('ix_orchestration_due', 'orchestration_decisions', ['status', 'due_at', 'source_sequence'], unique=False)
    op.create_table('task_capture_reports',
    sa.Column('capture_id', sa.String(length=64), nullable=False),
    sa.Column('device_id', sa.String(length=36), nullable=False),
    sa.Column('session_id', sa.String(length=36), nullable=True),
    sa.Column('generation', sa.Integer(), nullable=True),
    sa.Column('activation_id', sa.String(length=64), nullable=False),
    sa.Column('capture_mode', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('reason', sa.String(length=64), nullable=True),
    sa.Column('captured_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('callback_monotonic_ms', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['device_id'], ['jarvis_devices.id'], ),
    sa.ForeignKeyConstraint(['session_id'], ['live_sessions.id'], ),
    sa.PrimaryKeyConstraint('capture_id')
    )
    op.create_index('ix_task_capture_reports_activation', 'task_capture_reports', ['device_id', 'activation_id', 'capture_mode'], unique=False)
    op.create_table('task_targets',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=False),
    sa.Column('resource_type', sa.String(length=64), nullable=False),
    sa.Column('resource_key', sa.String(length=1024), nullable=False),
    sa.Column('mutable', sa.Boolean(), server_default='1', nullable=False),
    sa.Column('discovered_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('task_id', 'resource_type', 'resource_key', name='uq_task_targets_resource')
    )
    op.create_index('ix_task_targets_task_resource', 'task_targets', ['task_id', 'resource_type', 'resource_key'], unique=False)
    op.create_table('task_turns',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.Column('content', sa.Text(), nullable=True),
    sa.Column('artifact_reference', sa.String(length=1024), nullable=True),
    sa.Column('origin', sa.String(length=64), nullable=False),
    sa.Column('request_identity', sa.String(length=255), nullable=True),
    sa.Column('input_fingerprint', sa.String(length=64), nullable=True),
    sa.Column('execution_run_id', sa.String(length=36), nullable=True),
    sa.Column('question_id', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('task_id', 'request_identity', name='uq_task_turns_request_identity'),
    sa.UniqueConstraint('task_id', 'sequence', name='uq_task_turns_task_sequence')
    )
    op.create_table('conversation_artifacts',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=True),
    sa.Column('source_event_id', sa.String(length=36), nullable=True),
    sa.Column('artifact_type', sa.String(length=64), nullable=False),
    sa.Column('mime_type', sa.String(length=128), nullable=False),
    sa.Column('content_text', sa.Text(), nullable=True),
    sa.Column('content_json', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['conversation_runs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_event_id'], ['conversation_events.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_conversation_artifacts_conversation_created', 'conversation_artifacts', ['conversation_id', 'created_at'], unique=False)
    op.create_table('memory_items',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('scope', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('source_conversation_id', sa.String(length=36), nullable=False),
    sa.Column('source_event_id', sa.String(length=36), nullable=True),
    sa.Column('knowledge_path', sa.String(length=512), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['source_conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_event_id'], ['conversation_events.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_memory_items_conversation_created', 'memory_items', ['source_conversation_id', 'created_at'], unique=False)
    op.create_table('orchestration_actions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('decision_id', sa.String(length=36), nullable=False),
    sa.Column('operation_key', sa.String(length=128), nullable=False),
    sa.Column('action', sa.String(length=32), nullable=False),
    sa.Column('input_digest', sa.String(length=64), nullable=False),
    sa.Column('outcome_json', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['decision_id'], ['orchestration_decisions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('decision_id', 'operation_key', name='uq_orchestration_action_operation')
    )
    op.create_table('orchestration_metrics',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('metric_key', sa.String(length=255), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('decision_id', sa.String(length=36), nullable=True),
    sa.Column('claim_generation', sa.Integer(), nullable=True),
    sa.Column('task_id', sa.String(length=36), nullable=True),
    sa.Column('source_sequence', sa.Integer(), nullable=True),
    sa.Column('duration_ms', sa.Float(), nullable=True),
    sa.Column('values_json', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['decision_id'], ['orchestration_decisions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('metric_key')
    )
    op.create_index('ix_orchestration_metric_kind_created', 'orchestration_metrics', ['kind', 'created_at'], unique=False)
    op.create_table('task_execution_runs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('input_turn_id', sa.String(length=36), nullable=False),
    sa.Column('status', sa.String(length=32), server_default='queued', nullable=False),
    sa.Column('execution_kind', sa.String(length=32), server_default='agent', nullable=False),
    sa.Column('configuration_json', sa.JSON(), nullable=True),
    sa.Column('workspace_generation', sa.Integer(), server_default='1', nullable=False),
    sa.Column('current_attempt_id', sa.String(length=36), nullable=True),
    sa.Column('context_reference', sa.String(length=1024), nullable=True),
    sa.Column('result_reference', sa.String(length=1024), nullable=True),
    sa.Column('terminal_summary', sa.Text(), nullable=True),
    sa.Column('queue_entered_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint('ordinal > 0', name='ck_task_execution_runs_ordinal_positive'),
    sa.ForeignKeyConstraint(['input_turn_id'], ['task_turns.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('task_id', 'ordinal', name='uq_task_execution_runs_task_ordinal')
    )
    op.create_index('ix_task_execution_runs_queue', 'task_execution_runs', ['status', 'queue_entered_at'], unique=False)
    op.create_table('task_attempts',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=False),
    sa.Column('execution_run_id', sa.String(length=36), nullable=True),
    sa.Column('attempt_number', sa.Integer(), nullable=False),
    sa.Column('worker_id', sa.String(length=36), nullable=True),
    sa.Column('claim_token', sa.String(length=64), nullable=True),
    sa.Column('supervisor_generation', sa.Integer(), nullable=True),
    sa.Column('parent_attempt_id', sa.String(length=36), nullable=True),
    sa.Column('resume_reason', sa.String(length=64), nullable=True),
    sa.Column('workspace_path', sa.String(length=512), nullable=True),
    sa.Column('checkpoint_json', sa.JSON(), nullable=True),
    sa.Column('checkpoint_version', sa.Integer(), server_default='0', nullable=False),
    sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_progress_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('active_operation', sa.String(length=255), nullable=True),
    sa.Column('operation_started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('operation_deadline', sa.DateTime(timezone=True), nullable=True),
    sa.Column('recovery_metadata_json', sa.JSON(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('terminal_reason', sa.Text(), nullable=True),
    sa.Column('agent_runtime_reference', sa.String(length=255), nullable=True),
    sa.Column('usage_summary_json', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint('attempt_number > 0', name='ck_task_attempts_number_positive'),
    sa.ForeignKeyConstraint(['execution_run_id'], ['task_execution_runs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['parent_attempt_id'], ['task_attempts.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['worker_id'], ['worker_instances.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('task_id', 'attempt_number', name='uq_task_attempts_task_number')
    )
    op.create_index('ix_task_attempts_task_number', 'task_attempts', ['task_id', 'attempt_number'], unique=False)
    op.create_index('ix_task_attempts_worker_heartbeat', 'task_attempts', ['worker_id', 'heartbeat_at'], unique=False)
    op.create_table('task_context_bundles',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=False),
    sa.Column('execution_run_id', sa.String(length=36), nullable=False),
    sa.Column('schema_version', sa.String(length=64), nullable=False),
    sa.Column('content_sha256', sa.String(length=64), nullable=False),
    sa.Column('content_json', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['execution_run_id'], ['task_execution_runs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('execution_run_id', name='uq_task_context_bundles_run')
    )
    op.create_table('task_control_commands',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=False),
    sa.Column('execution_run_id', sa.String(length=36), nullable=True),
    sa.Column('attempt_id', sa.String(length=36), nullable=True),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('parameters_json', sa.JSON(), nullable=True),
    sa.Column('requester', sa.String(length=64), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('provider_call_id', sa.String(length=255), nullable=True),
    sa.Column('idempotency_key', sa.String(length=255), nullable=False),
    sa.Column('state', sa.String(length=32), server_default='requested', nullable=False),
    sa.Column('requested_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('rejected_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['execution_run_id'], ['task_execution_runs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('task_id', 'idempotency_key', name='uq_task_control_commands_task_key')
    )
    op.create_table('task_native_bindings',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=False),
    sa.Column('execution_run_id', sa.String(length=36), nullable=False),
    sa.Column('generation', sa.Integer(), server_default='1', nullable=False),
    sa.Column('native_thread_id', sa.String(length=255), nullable=True),
    sa.Column('profile_id', sa.String(length=255), nullable=True),
    sa.Column('runtime_version', sa.String(length=64), nullable=False),
    sa.Column('compatibility_json', sa.JSON(), nullable=True),
    sa.Column('storage_reference', sa.String(length=1024), nullable=True),
    sa.Column('replacement_reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['execution_run_id'], ['task_execution_runs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('execution_run_id', 'generation', name='uq_task_native_bindings_run_generation')
    )
    op.create_table('task_operations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=False),
    sa.Column('execution_run_id', sa.String(length=36), nullable=False),
    sa.Column('attempt_id', sa.String(length=36), nullable=True),
    sa.Column('operation_key', sa.String(length=255), nullable=False),
    sa.Column('operation_type', sa.String(length=128), nullable=False),
    sa.Column('state', sa.String(length=32), server_default='started', nullable=False),
    sa.Column('input_digest', sa.String(length=64), nullable=True),
    sa.Column('intent_json', sa.JSON(), nullable=True),
    sa.Column('result_reference', sa.String(length=1024), nullable=True),
    sa.Column('result_json', sa.JSON(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['execution_run_id'], ['task_execution_runs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('task_id', 'operation_key', name='uq_task_operations_task_key')
    )
    op.create_table('task_results',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=False),
    sa.Column('app_slug', sa.String(length=48), nullable=False),
    sa.Column('collection', sa.String(length=48), nullable=False),
    sa.Column('record_key', sa.String(length=128), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('execution_run_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['execution_run_id'], ['task_execution_runs.id'], ),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('app_slug', 'collection', 'record_key', name='uq_task_results_record')
    )
    op.create_index(op.f('ix_task_results_task_id'), 'task_results', ['task_id'], unique=False)
    op.create_table('workflow_runs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=False),
    sa.Column('execution_run_id', sa.String(length=36), nullable=False),
    sa.Column('workflow_id', sa.String(length=64), nullable=False),
    sa.Column('workflow_version', sa.Integer(), nullable=False),
    sa.Column('workflow_digest', sa.String(length=64), nullable=False),
    sa.Column('normalized_inputs_json', sa.JSON(), nullable=False),
    sa.Column('stage', sa.String(length=64), nullable=False),
    sa.Column('context_reference', sa.String(length=1024), nullable=True),
    sa.Column('result_reference', sa.String(length=1024), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['execution_run_id'], ['task_execution_runs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('execution_run_id', name='uq_workflow_runs_execution_run')
    )
    op.create_table('resource_leases',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('resource_type', sa.String(length=64), nullable=False),
    sa.Column('resource_key', sa.String(length=1024), nullable=False),
    sa.Column('canonical_key', sa.String(length=1024), nullable=True),
    sa.Column('task_id', sa.String(length=36), nullable=True),
    sa.Column('execution_run_id', sa.String(length=36), nullable=True),
    sa.Column('owner_attempt_id', sa.String(length=36), nullable=False),
    sa.Column('claim_token', sa.String(length=64), nullable=True),
    sa.Column('supervisor_generation', sa.Integer(), nullable=True),
    sa.Column('writer_identity', sa.String(length=255), nullable=True),
    sa.Column('release_evidence_json', sa.JSON(), nullable=True),
    sa.Column('acquired_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('renewed_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['execution_run_id'], ['task_execution_runs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['owner_attempt_id'], ['task_attempts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('resource_type', 'resource_key', name='uq_resource_leases_resource')
    )
    op.create_index('ix_resource_leases_expiry', 'resource_leases', ['expires_at'], unique=False)
    op.create_index('ix_resource_leases_owner', 'resource_leases', ['owner_attempt_id'], unique=False)
    op.create_table('task_events',
    sa.Column('sequence', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=False),
    sa.Column('attempt_id', sa.String(length=36), nullable=True),
    sa.Column('execution_run_id', sa.String(length=36), nullable=True),
    sa.Column('native_item_id', sa.String(length=255), nullable=True),
    sa.Column('event_type', sa.String(length=128), nullable=False),
    sa.Column('payload_json', sa.JSON(), nullable=True),
    sa.Column('causality_json', sa.JSON(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['attempt_id'], ['task_attempts.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['execution_run_id'], ['task_execution_runs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('sequence'),
    sa.UniqueConstraint('execution_run_id', 'native_item_id', name='uq_task_events_run_native_item'),
    sa.UniqueConstraint('id', name='uq_task_events_public_id'),
    sa.UniqueConstraint('idempotency_key', name='uq_task_events_idempotency_key')
    )
    op.create_index('ix_task_events_task_sequence', 'task_events', ['task_id', 'sequence'], unique=False)
    op.create_index('ix_task_events_type_sequence', 'task_events', ['event_type', 'sequence'], unique=False)
    op.create_table('conversation_messages',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=True),
    sa.Column('source_event_id', sa.String(length=36), nullable=True),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.Column('author_label', sa.String(length=255), nullable=True),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('content_format', sa.String(length=32), nullable=False),
    sa.Column('is_final', sa.Boolean(), nullable=False),
    sa.Column('modality', sa.String(length=16), server_default='text', nullable=False),
    sa.Column('execution_mode', sa.String(length=32), server_default='agent', nullable=False),
    sa.Column('communication_state', sa.String(length=32), server_default='completed', nullable=False),
    sa.Column('source_task_id', sa.String(length=36), nullable=True),
    sa.Column('source_task_event_id', sa.String(length=36), nullable=True),
    sa.Column('presented_in_turn_id', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['presented_in_turn_id'], ['conversation_messages.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['run_id'], ['conversation_runs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_event_id'], ['conversation_events.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_task_event_id'], ['task_events.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_task_id'], ['tasks.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_conversation_messages_conversation_created', 'conversation_messages', ['conversation_id', 'created_at'], unique=False)
    op.create_table('task_artifacts',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=False),
    sa.Column('attempt_id', sa.String(length=36), nullable=True),
    sa.Column('execution_run_id', sa.String(length=36), nullable=True),
    sa.Column('source_task_event_id', sa.String(length=36), nullable=True),
    sa.Column('artifact_type', sa.String(length=64), nullable=False),
    sa.Column('mime_type', sa.String(length=128), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=True),
    sa.Column('uri', sa.String(length=1024), nullable=True),
    sa.Column('content_text', sa.Text(), nullable=True),
    sa.Column('content_json', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['attempt_id'], ['task_attempts.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['execution_run_id'], ['task_execution_runs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_task_event_id'], ['task_events.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_task_artifacts_task_created', 'task_artifacts', ['task_id', 'created_at'], unique=False)
    op.create_table('task_questions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=False),
    sa.Column('asking_run_id', sa.String(length=36), nullable=True),
    sa.Column('resolution_revision', sa.Integer(), nullable=True),
    sa.Column('asked_attempt_id', sa.String(length=36), nullable=True),
    sa.Column('answered_attempt_id', sa.String(length=36), nullable=True),
    sa.Column('source_task_event_id', sa.String(length=36), nullable=True),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('permission_json', sa.JSON(), nullable=True),
    sa.Column('answer', sa.Text(), nullable=True),
    sa.Column('state', sa.String(length=32), server_default='asked', nullable=False),
    sa.Column('asked_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('answered_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['answered_attempt_id'], ['task_attempts.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['asked_attempt_id'], ['task_attempts.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['asking_run_id'], ['task_execution_runs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_task_event_id'], ['task_events.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_task_questions_task_state', 'task_questions', ['task_id', 'state'], unique=False)
    op.create_table('conversation_branches',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('branch_key', sa.String(length=128), nullable=False),
    sa.Column('label', sa.String(length=255), nullable=True),
    sa.Column('parent_branch_key', sa.String(length=128), nullable=True),
    sa.Column('branched_from_message_id', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['branched_from_message_id'], ['conversation_messages.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('conversation_id', 'branch_key', name='uq_conversation_branches_conversation_branch_key')
    )
    op.create_index('ix_conversation_branches_conversation_created', 'conversation_branches', ['conversation_id', 'created_at'], unique=False)
    op.create_table('notification_outbox',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=False),
    sa.Column('task_event_id', sa.String(length=36), nullable=True),
    sa.Column('question_id', sa.String(length=36), nullable=True),
    sa.Column('event_kind', sa.String(length=64), nullable=False),
    sa.Column('dedupe_key', sa.String(length=255), nullable=False),
    sa.Column('payload_json', sa.JSON(), nullable=True),
    sa.Column('deep_link', sa.String(length=1024), nullable=False),
    sa.Column('status', sa.String(length=32), server_default='queued', nullable=False),
    sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("status IN ('queued', 'sending', 'delivered', 'failed', 'superseded')", name='ck_notification_outbox_status'),
    sa.ForeignKeyConstraint(['question_id'], ['task_questions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_event_id'], ['task_events.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dedupe_key', name='uq_notification_outbox_dedupe_key')
    )
    op.create_index('ix_notification_outbox_status_created', 'notification_outbox', ['status', 'created_at'], unique=False)
    op.create_index('uq_notification_outbox_task_event', 'notification_outbox', ['task_event_id'], unique=True)
    op.create_table('task_context_requests',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=True),
    sa.Column('execution_run_id', sa.String(length=36), nullable=True),
    sa.Column('workflow_run_id', sa.String(length=36), nullable=True),
    sa.Column('capture_id', sa.String(length=64), nullable=False),
    sa.Column('capture_revision', sa.Integer(), nullable=False),
    sa.Column('device_id', sa.String(length=36), nullable=False),
    sa.Column('session_id', sa.String(length=36), nullable=False),
    sa.Column('generation', sa.Integer(), nullable=False),
    sa.Column('activation_id', sa.String(length=64), nullable=False),
    sa.Column('capture_mode', sa.String(length=16), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('failure_reason', sa.String(length=64), nullable=True),
    sa.Column('blob_id', sa.String(length=36), nullable=True),
    sa.Column('artifact_id', sa.String(length=36), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('requested_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('fulfilled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('failed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['artifact_id'], ['task_artifacts.id'], ),
    sa.ForeignKeyConstraint(['blob_id'], ['task_context_blobs.id'], ),
    sa.ForeignKeyConstraint(['capture_id'], ['task_capture_reports.capture_id'], ),
    sa.ForeignKeyConstraint(['device_id'], ['jarvis_devices.id'], ),
    sa.ForeignKeyConstraint(['execution_run_id'], ['task_execution_runs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['session_id'], ['live_sessions.id'], ),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workflow_run_id'], ['workflow_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('execution_run_id', 'capture_id', name='uq_task_context_requests_run_capture')
    )
    op.create_table('task_presentations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=False),
    sa.Column('candidate_type', sa.String(length=64), nullable=False),
    sa.Column('coalescing_key', sa.String(length=255), nullable=False),
    sa.Column('importance', sa.String(length=32), nullable=False),
    sa.Column('state', sa.String(length=32), server_default='pending', nullable=False),
    sa.Column('reserved_session_id', sa.String(length=36), nullable=True),
    sa.Column('reserved_generation', sa.Integer(), nullable=True),
    sa.Column('required_facts_json', sa.JSON(), nullable=True),
    sa.Column('source_task_event_id', sa.String(length=36), nullable=True),
    sa.Column('idempotency_key', sa.String(length=255), nullable=False),
    sa.Column('retry_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('defer_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('reserved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('handled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('handled_by', sa.String(length=64), nullable=True),
    sa.Column('handled_turn_id', sa.String(length=36), nullable=True),
    sa.Column('handled_event_id', sa.String(length=36), nullable=True),
    sa.Column('lease_token', sa.String(length=64), nullable=True),
    sa.Column('commentary_attempted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("state IN ('pending', 'handled', 'superseded')", name='ck_task_presentations_state'),
    sa.ForeignKeyConstraint(['handled_event_id'], ['jarvis_live_events.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['handled_turn_id'], ['conversation_messages.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['reserved_session_id'], ['live_sessions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_task_event_id'], ['task_events.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key', name='uq_task_presentations_idempotency_key')
    )
    op.create_index('ix_task_presentations_state_created', 'task_presentations', ['state', 'created_at'], unique=False)
    op.create_index('ix_task_presentations_task', 'task_presentations', ['task_id', 'created_at'], unique=False)
    op.create_index('uq_task_presentations_session_lease', 'task_presentations', ['reserved_session_id'], unique=True, sqlite_where=sa.text("state = 'pending' AND lease_token IS NOT NULL"))
    op.create_index('uq_task_presentations_source_task_event', 'task_presentations', ['source_task_event_id'], unique=True, sqlite_where=sa.text('source_task_event_id IS NOT NULL'))
    op.create_table('live_speech_deliveries',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('assistant_turn_id', sa.String(length=36), nullable=True),
    sa.Column('live_session_id', sa.String(length=36), nullable=False),
    sa.Column('provider_response_id', sa.String(length=255), nullable=False),
    sa.Column('provider_item_id', sa.String(length=255), nullable=False),
    sa.Column('generated_transcript', sa.Text(), nullable=False),
    sa.Column('heard_transcript', sa.Text(), nullable=True),
    sa.Column('state', sa.String(length=32), nullable=False),
    sa.Column('generation_started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('playback_started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('interrupted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('source_task_id', sa.String(length=36), nullable=True),
    sa.Column('source_task_event_id', sa.String(length=36), nullable=True),
    sa.Column('presentation_id', sa.String(length=36), nullable=True),
    sa.Column('provider_causality_json', sa.JSON(), nullable=True),
    sa.Column('timing_summary_json', sa.JSON(), nullable=True),
    sa.Column('usage_summary_json', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['assistant_turn_id'], ['conversation_messages.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['live_session_id'], ['live_sessions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['presentation_id'], ['task_presentations.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_task_event_id'], ['task_events.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_task_id'], ['tasks.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('live_session_id', 'provider_response_id', 'provider_item_id', name='uq_live_speech_deliveries_provider_item')
    )
    op.create_index('ix_live_speech_deliveries_session_created', 'live_speech_deliveries', ['live_session_id', 'created_at'], unique=False)
    op.create_index('ix_live_speech_deliveries_state', 'live_speech_deliveries', ['state'], unique=False)

    for statement in SQLITE_DDL:
        op.execute(statement)
    op.execute("INSERT INTO jarvis_preferences (id) VALUES ('current')")


def downgrade():
    raise NotImplementedError("The baseline is not reversed; reset the development database instead")
