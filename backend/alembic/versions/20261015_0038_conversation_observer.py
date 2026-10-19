"""Durable conversation observer work and evidence."""
from alembic import op
import sqlalchemy as sa

revision = "20261015_0038"
down_revision = "20261008_0037"
branch_labels = None
depends_on = None


def upgrade():
    from app.db.tables import ConversationObservation
    ConversationObservation.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError("Communication evidence requires the current schema")
