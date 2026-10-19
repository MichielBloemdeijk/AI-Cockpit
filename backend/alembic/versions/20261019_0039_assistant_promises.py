"""Track promises Jarvis makes in conversation."""
from alembic import op
import sqlalchemy as sa

revision = "20261019_0039"
down_revision = "20261015_0038"
branch_labels = None
depends_on = None


def upgrade():
    # Recovery may replay DDL that committed before the Alembic head stamp.
    if "assistant_promises" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "assistant_promises",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("live_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(500), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column("source_fragment_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("resolution", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("session_id", "source_key", name="uq_assistant_promise_source"),
    )


def downgrade():
    op.drop_table("assistant_promises")
