"""Worker native selection and human-only connector action approval."""
from alembic import op
import sqlalchemy as sa

revision = "20261008_0037"
down_revision = "20261003_0036"
branch_labels = None
depends_on = None


def upgrade():
    # Recovery may replay DDL that committed before the Alembic head stamp.
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "worker_tool_policy" not in tables:
        op.create_table("worker_tool_policy", sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("revision", sa.Integer(), nullable=False), sa.Column("configuration_json", sa.JSON(), nullable=False))
    if "connector_tool_permissions" not in tables:
        op.create_table("connector_tool_permissions", sa.Column("identity", sa.String(255), primary_key=True),
            sa.Column("connector", sa.String(100), nullable=False), sa.Column("account", sa.String(100), nullable=False),
            sa.Column("tool", sa.String(100), nullable=False), sa.Column("mode", sa.String(32), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False))
    if "permission_json" not in {column["name"] for column in inspector.get_columns("task_questions")}:
        op.add_column("task_questions", sa.Column("permission_json", sa.JSON(), nullable=True))


def downgrade():
    raise RuntimeError("Worker permission and approval contracts require the current schema")
