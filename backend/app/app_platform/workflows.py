"""The workflows that installed apps provide.

Installing an app enables its workflows; there is no separate allowlist. The
workflow catalog is built synchronously (for example for a voice session's tool
list), so this reads the active versions straight from `apps.db`.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3

from sqlalchemy.engine import make_url

from app.config import settings


@dataclass(frozen=True, slots=True)
class AppWorkflow:
    slug: str
    version: int
    folder: Path
    result_collections: tuple[str, ...]


def installed_workflows() -> dict[str, AppWorkflow]:
    """{workflow id: provider} for the active version of every installed app."""
    database = make_url(settings.resolved_apps_database_url).database
    if not database or database == ":memory:" or not Path(database).is_file():
        return {}
    try:
        with closing(sqlite3.connect(f"file:{Path(database).as_posix()}?mode=ro", uri=True, timeout=30)) as db:
            rows = db.execute(
                "SELECT v.slug, v.package_path, v.manifest_json FROM installed_apps a "
                "JOIN app_versions v ON v.slug = a.slug AND v.version = a.active_version").fetchall()
    except sqlite3.Error:
        return {}
    provided: dict[str, AppWorkflow] = {}
    for slug, package_path, manifest_json in rows:
        manifest = json.loads(manifest_json)
        results = tuple(name for name, spec in (manifest.get("collections") or {}).items() if spec.get("result_type"))
        for workflow_id, definition in (manifest.get("workflow_definitions") or {}).items():
            provided[workflow_id] = AppWorkflow(slug, definition["version"],
                                                Path(package_path) / "workflows" / workflow_id, results)
    return provided


def workflow_conflicts(slug: str, workflow_ids: list[str]) -> list[str]:
    """Workflow ids this app may not use because the core or another app already does."""
    from app.services.task_workflows import core_workflow_ids

    taken = core_workflow_ids()
    others = {workflow_id for workflow_id, provider in installed_workflows().items() if provider.slug != slug}
    return sorted(workflow_id for workflow_id in workflow_ids if workflow_id in taken | others)
