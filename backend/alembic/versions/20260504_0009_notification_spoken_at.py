"""Track successful spoken delivery acknowledgements."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260504_0009"
down_revision = "20260504_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notification_deliveries", sa.Column("spoken_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("notification_deliveries", "spoken_at")
