"""Use session identity for session-domain replay, retaining event deduplication."""
from alembic import op

revision = "20261003_0036"
down_revision = "20261003_0035"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DROP TRIGGER IF EXISTS jarvis_live_session_insert")
    op.execute("""
        CREATE TRIGGER jarvis_live_session_insert AFTER INSERT ON live_session_events
        BEGIN
            INSERT OR IGNORE INTO jarvis_live_events
                (id, event_type, source_domain, source_id, source_sequence,
                 payload_json, idempotency_key, created_at)
            VALUES
                (lower(hex(randomblob(16))), NEW.event_type, 'session', NEW.session_id,
                 NEW.sequence, NEW.payload_json, 'session:' || NEW.id, NEW.created_at);
        END
    """)
    op.execute("""
        UPDATE jarvis_live_events
        SET source_id = (
            SELECT session_id FROM live_session_events
            WHERE 'session:' || live_session_events.id = jarvis_live_events.idempotency_key
        )
        WHERE source_domain = 'session' AND EXISTS (
            SELECT 1 FROM live_session_events
            WHERE 'session:' || live_session_events.id = jarvis_live_events.idempotency_key
        )
    """)


def downgrade():
    raise RuntimeError("Session replay consumers require the current session identity contract")
