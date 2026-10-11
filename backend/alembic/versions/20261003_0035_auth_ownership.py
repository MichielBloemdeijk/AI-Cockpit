"""Shared subscription credential ownership across contained runtime hosts."""
from alembic import op
import sqlalchemy as sa

revision = "20261003_0035"
down_revision = "20261002_0034"
branch_labels = None
depends_on = None


def upgrade():
    if "runtime_credential_leases" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table("runtime_credential_leases",
        sa.Column("profile_key", sa.String(64), primary_key=True),
        sa.Column("owner_kind", sa.String(32), nullable=False),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.Column("creation_stamp", sa.String(64), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))


def downgrade():
    raise RuntimeError("Confirm credential owners stopped before removing ownership records")
