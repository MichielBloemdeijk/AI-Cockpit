"""Revocable generations for scoped generated-app storage capabilities."""
from alembic import op
import sqlalchemy as sa

revision = "20261001_0032"
down_revision = "20261001_0031"
branch_labels = None
depends_on = None


def upgrade():
    present = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("cockpit_apps")}
    if "capability_revision" not in present:
        op.add_column("cockpit_apps", sa.Column("capability_revision", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    raise RuntimeError("Keep capability revocation generations during rollback")
