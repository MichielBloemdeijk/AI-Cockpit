"""Remember when a result page was last opened, so Jarvis knows which page is on screen."""
from alembic import op
import sqlalchemy as sa

revision = "20261028_0041"
down_revision = "20261021_0040"
branch_labels = None
depends_on = None


def upgrade():
    if "last_viewed_at" in {column["name"] for column in sa.inspect(op.get_bind()).get_columns("result_pages")}:
        return
    with op.batch_alter_table("result_pages") as batch:
        batch.add_column(sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    with op.batch_alter_table("result_pages") as batch:
        batch.drop_column("last_viewed_at")
