"""Add the maintained result catalog, durable presentation events and app state.

Additive/resumable on SQLite: never rebuild the task/result evidence tables.
"""
from uuid import NAMESPACE_URL, uuid5

from alembic import op
import sqlalchemy as sa

revision = "20261001_0031"
down_revision = "20260930_0030"
branch_labels = None
depends_on = None


def _id(slug):
    return str(uuid5(NAMESPACE_URL, f"ai-cockpit:app:{slug}"))


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "cockpit_apps" not in tables:
        op.create_table("cockpit_apps",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("slug", sa.String(128), nullable=False, unique=True),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("description", sa.Text()),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("route_path", sa.String(255), nullable=False),
            sa.Column("runtime", sa.String(64), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("storage_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint("kind IN ('collection', 'generated')", name="ck_cockpit_apps_kind"),
            sa.CheckConstraint("storage_bytes >= 0", name="ck_cockpit_apps_storage_bytes"))

    # All additions have SQL defaults or are nullable, preserving existing rows.
    additions = {
        "generated_apps": {"cockpit_app_id": "VARCHAR(36) REFERENCES cockpit_apps(id)"},
        "result_pages": {
            "cockpit_app_id": "VARCHAR(36) REFERENCES cockpit_apps(id)",
            "state_revision": "INTEGER NOT NULL DEFAULT 0",
            "first_published_at": "DATETIME", "last_published_at": "DATETIME"},
        "result_page_revisions": {
            "schema_id": "VARCHAR(64)", "base_revision_id": "VARCHAR(36)",
            "base_revision_number": "INTEGER", "source_task_event_id": "VARCHAR(36)",
            "publication_key": "VARCHAR(255)"},
    }
    for table, columns in additions.items():
        present = {item["name"] for item in sa.inspect(bind).get_columns(table)}
        for name, definition in columns.items():
            if name not in present:
                op.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS uq_generated_catalog ON generated_apps(cockpit_app_id)"))
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS uq_result_revision_publication ON result_page_revisions(result_page_id, publication_key)"))

    if "result_page_events" not in tables:
        op.create_table("result_page_events",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("result_page_id", sa.String(36), sa.ForeignKey("result_pages.id", ondelete="CASCADE"), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("event_key", sa.String(255), nullable=False),
            sa.Column("event_type", sa.String(64), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("source_task_event_id", sa.String(36)),
            sa.Column("revision_id", sa.String(36)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("result_page_id", "sequence", name="uq_result_event_sequence"),
            sa.UniqueConstraint("result_page_id", "event_key", name="uq_result_event_key"))
    if "app_data_records" not in tables:
        op.create_table("app_data_records",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("cockpit_app_id", sa.String(36), sa.ForeignKey("cockpit_apps.id"), nullable=False),
            sa.Column("result_page_id", sa.String(36), sa.ForeignKey("result_pages.id")),
            sa.Column("scope_key", sa.String(36), nullable=False, server_default=""),
            sa.Column("namespace", sa.String(64), nullable=False),
            sa.Column("record_key", sa.String(128), nullable=False),
            sa.Column("value_json", sa.JSON(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("cockpit_app_id", "scope_key", "namespace", "record_key", name="uq_app_data_scope"),
            sa.CheckConstraint("revision > 0 AND size_bytes >= 0", name="ck_app_data_revision_size"))

    metadata = sa.MetaData()
    catalog = sa.Table("cockpit_apps", metadata, autoload_with=bind)
    generated = sa.Table("generated_apps", metadata, autoload_with=bind)
    for slug, title in (("documents", "Documents"), ("recipes", "Recipes")):
        if not bind.execute(sa.select(catalog.c.id).where(catalog.c.slug == slug)).first():
            bind.execute(catalog.insert().values(id=_id(slug), slug=slug, title=title,
                description=f"Maintained {title.lower()} from tasks", kind="collection",
                route_path=f"/collections/{slug}", runtime="maintained.v1"))
    used = set(bind.execute(sa.select(catalog.c.slug)).scalars())
    for row in bind.execute(sa.select(generated)).mappings().all():
        if row["cockpit_app_id"]:
            continue
        # Preserve old generated routes/slugs even if they collide with a collection.
        slug = row["slug"]
        if slug in used:
            slug = f"generated-{row['id']}"
        used.add(slug)
        app_id = _id(f"generated:{row['id']}")
        if not bind.execute(sa.select(catalog.c.id).where(catalog.c.id == app_id)).first():
            bind.execute(catalog.insert().values(id=app_id, slug=slug, title=row["title"],
                description=row["description"], kind="generated", route_path=row["route_path"],
                runtime="generated.v1", created_at=row["created_at"], updated_at=row["updated_at"]))
        bind.execute(generated.update().where(generated.c.id == row["id"]).values(cockpit_app_id=app_id))
    op.execute(sa.text("UPDATE result_pages SET cockpit_app_id = (SELECT id FROM cockpit_apps WHERE slug = result_pages.app_slug) WHERE cockpit_app_id IS NULL"))
    op.execute(sa.text("UPDATE result_page_revisions SET schema_id = (SELECT result_kind FROM result_pages WHERE id = result_page_revisions.result_page_id), publication_key = 'legacy:' || artifact_id WHERE schema_id IS NULL"))
    op.execute(sa.text("UPDATE result_pages SET first_published_at = (SELECT min(created_at) FROM result_page_revisions WHERE result_page_id = result_pages.id), last_published_at = (SELECT max(created_at) FROM result_page_revisions WHERE result_page_id = result_pages.id) WHERE current_revision_id IS NOT NULL AND first_published_at IS NULL"))


def downgrade():
    # Evidence-bearing migrations intentionally require a reviewed rollback.
    raise RuntimeError("Result-platform downgrade would discard durable app/result state")
