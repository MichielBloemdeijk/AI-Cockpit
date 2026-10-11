"""Durable cross-tool Live admission identities."""
from alembic import op
import sqlalchemy as sa

revision = "20261002_0034"
down_revision = "20261001_0033"
branch_labels = None
depends_on = None


def upgrade():
    if "live_admission_intents" in sa.inspect(op.get_bind()).get_table_names():
        if "target_task_id" not in {column["name"] for column in sa.inspect(op.get_bind()).get_columns("live_admission_intents")}:
            op.add_column("live_admission_intents", sa.Column("target_task_id", sa.String(36)))
        return  # SQLite DDL can commit before Alembic stamps the revision.
    op.create_table("live_admission_intents",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("live_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("turn_scope", sa.String(255), nullable=False),
        sa.Column("intent_id", sa.String(128), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("target_task_id", sa.String(36)),
        sa.Column("output_json", sa.JSON()), sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))


def downgrade():
    raise RuntimeError("Keep durable admission identities during rollback")
