"""Metadata-only result lifecycle observations, independent of presentation events."""
from alembic import op
import sqlalchemy as sa

revision = "20261001_0033"
down_revision = "20261001_0032"
branch_labels = None
depends_on = None


def upgrade():
    if "result_metrics" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table("result_metrics",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("metric_key", sa.String(255), nullable=False, unique=True),
            sa.Column("kind", sa.String(64), nullable=False),
            sa.Column("result_page_id", sa.String(36), sa.ForeignKey("result_pages.id", ondelete="CASCADE")),
            sa.Column("task_id", sa.String(36)), sa.Column("execution_run_id", sa.String(36)),
            sa.Column("attempt_id", sa.String(36)), sa.Column("revision_number", sa.Integer()),
            sa.Column("duration_ms", sa.Float()), sa.Column("values_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_result_metrics_page ON result_metrics(result_page_id, id)"))


def downgrade():
    raise RuntimeError("Keep result observation history during rollback")
