"""Results become app records: replace result pages with links to app records.

Documents and Recipes are app packages now; their results are records in the
app platform's database. The result pages, revisions, events and metrics, the
seeded collection apps and their saved flags are development data and dropped.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261031_0042"
down_revision = "20261028_0041"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    # Safe to replay: every step checks what is already done.
    if inspector.has_table("cockpit_apps"):
        op.execute("DELETE FROM app_data_records WHERE cockpit_app_id IN (SELECT id FROM cockpit_apps WHERE kind = 'collection')")
        op.execute("DELETE FROM cockpit_apps WHERE kind = 'collection'")
    if inspector.has_table("app_data_records") and "result_page_id" in {
            column["name"] for column in inspector.get_columns("app_data_records")}:
        with op.batch_alter_table("app_data_records", recreate="always") as batch:
            batch.drop_column("result_page_id")
    for table in ("result_metrics", "result_page_events", "result_page_revisions", "result_pages"):
        if inspector.has_table(table):
            op.drop_table(table)
    if inspector.has_table("task_results"):
        return
    op.create_table(
        "task_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("app_slug", sa.String(48), nullable=False),
        sa.Column("collection", sa.String(48), nullable=False),
        sa.Column("record_key", sa.String(128), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("execution_run_id", sa.String(36), sa.ForeignKey("task_execution_runs.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("app_slug", "collection", "record_key", name="uq_task_results_record"),
    )
    op.create_index("ix_task_results_task_id", "task_results", ["task_id"])


def downgrade():
    raise NotImplementedError("Result pages are not restored; reset the development database instead")
