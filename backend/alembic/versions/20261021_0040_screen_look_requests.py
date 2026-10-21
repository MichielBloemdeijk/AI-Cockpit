"""Let a spoken screen look request pixels without a task run."""
from alembic import op
import sqlalchemy as sa

revision = "20261021_0040"
down_revision = "20261019_0039"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("task_context_requests") as batch:
        for column in ("task_id", "execution_run_id", "workflow_run_id"):
            batch.alter_column(column, existing_type=sa.String(36), nullable=True)


def downgrade():
    op.execute("DELETE FROM task_context_requests WHERE task_id IS NULL")
    with op.batch_alter_table("task_context_requests") as batch:
        for column in ("task_id", "execution_run_id", "workflow_run_id"):
            batch.alter_column(column, existing_type=sa.String(36), nullable=False)
