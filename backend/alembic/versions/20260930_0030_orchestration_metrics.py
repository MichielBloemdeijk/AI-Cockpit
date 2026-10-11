"""Durable metadata-only orchestration measurements, outside the live event stream."""
from alembic import op
import sqlalchemy as sa

revision = "20260930_0030"
down_revision = "20260930_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "orchestration_metrics" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table("orchestration_metrics",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("metric_key", sa.String(255), nullable=False, unique=True),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("decision_id", sa.String(36), sa.ForeignKey("orchestration_decisions.id", ondelete="CASCADE")),
            sa.Column("claim_generation", sa.Integer()),
            sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE")),
            sa.Column("source_sequence", sa.Integer()),
            sa.Column("duration_ms", sa.Float()),
            sa.Column("values_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("orchestration_metrics")}
    if "ix_orchestration_metric_kind_created" not in indexes:
        op.create_index("ix_orchestration_metric_kind_created", "orchestration_metrics", ["kind", "created_at"])


def downgrade() -> None:
    op.drop_table("orchestration_metrics")
