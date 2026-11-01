"""Remove the old generated-apps system: its registry, catalog and per-app storage.

Apps are app-platform packages now (docs/jarvis-tasks/13-app-platform.md), with
their own database. The old tables only held development data; their source
folders were removed on 2026-10-30.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261101_0043"
down_revision = "20261031_0042"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    # Safe to replay: each table is dropped only while it exists, dependents first.
    for table in ("app_data_records", "generated_apps", "cockpit_apps"):
        if inspector.has_table(table):
            op.drop_table(table)
    op.execute("DELETE FROM resource_leases WHERE resource_type = 'generated_app'")
    op.execute("DELETE FROM task_targets WHERE resource_type = 'generated_app'")


def downgrade():
    raise NotImplementedError("The old generated-apps tables are not restored; reset the development database instead")
