"""Durable background issues and transactionally committed action receipts."""
from alembic import op
import sqlalchemy as sa

revision = "20260930_0029"
down_revision = "20260925_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "orchestration_decisions" not in tables:
        op.create_table(
            "orchestration_decisions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("issue_key", sa.String(64), nullable=False, unique=True),
            sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
            sa.Column("execution_run_id", sa.String(36)),
            sa.Column("question_id", sa.String(36)),
            sa.Column("trigger_kind", sa.String(32), nullable=False),
            sa.Column("source_event_ids_json", sa.JSON(), nullable=False),
            sa.Column("source_sequence", sa.Integer(), nullable=False),
            sa.Column("observed_json", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("due_at", sa.DateTime(timezone=True)),
            sa.Column("claim_generation", sa.Integer(), nullable=False),
            sa.Column("claim_owner", sa.String(255)),
            sa.Column("claimed_at", sa.DateTime(timezone=True)),
            sa.Column("context_json", sa.JSON()),
            sa.Column("context_digest", sa.String(64)),
            sa.Column("model", sa.String(128), nullable=False),
            sa.Column("effort", sa.String(16), nullable=False),
            sa.Column("auth_profile", sa.String(255), nullable=False),
            sa.Column("native_binding_json", sa.JSON()),
            sa.Column("outcome_json", sa.JSON()),
            sa.Column("decision_reason", sa.Text()),
            sa.Column("last_error", sa.String(64)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
        )
        op.create_index("ix_orchestration_due", "orchestration_decisions", ["status", "due_at", "source_sequence"])
    if "orchestration_actions" not in tables:
        op.create_table(
            "orchestration_actions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("decision_id", sa.String(36), sa.ForeignKey("orchestration_decisions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("operation_key", sa.String(128), nullable=False),
            sa.Column("action", sa.String(32), nullable=False),
            sa.Column("input_digest", sa.String(64), nullable=False),
            sa.Column("outcome_json", sa.JSON()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("decision_id", "operation_key", name="uq_orchestration_action_operation"),
        )


def downgrade() -> None:
    op.drop_table("orchestration_actions")
    op.drop_table("orchestration_decisions")
