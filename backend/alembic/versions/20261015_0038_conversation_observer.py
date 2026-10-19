"""Durable conversation observer work and evidence."""
from alembic import op
import sqlalchemy as sa

revision = "20261015_0038"
down_revision = "20261008_0037"
branch_labels = None
depends_on = None


def upgrade():
    # Recovery may replay DDL that committed before the Alembic head stamp.
    if "conversation_observations" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "conversation_observations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("live_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("usage_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(512), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("session_id", "evidence_hash", name="uq_conversation_observation_evidence"),
    )


def downgrade():
    op.drop_table("conversation_observations")
