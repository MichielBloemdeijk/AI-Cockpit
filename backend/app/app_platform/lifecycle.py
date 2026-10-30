"""Install, activate and roll back app versions.

Installing copies a validated package to `apps_dir/<slug>/<version>/`, records
the version and makes it active in one transaction. Every installed version is
kept, so rolling back only moves the active pointer.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Any

from sqlalchemy import select, text

from app.app_platform.manifest import ManifestError, compatibility_problems, load_package
from app.app_platform.store import AppVersion, InstalledApp, apps_session
from app.config import settings


class AppNotFound(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class ActiveApp:
    slug: str
    title: str
    version: int
    manifest: dict[str, Any]
    package_path: Path

    @property
    def collections(self) -> dict[str, dict[str, Any]]:
        return self.manifest.get("collections") or {}

    def summary(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "title": self.title,
            "version": self.version,
            "description": self.manifest.get("description", ""),
            "collections": sorted(self.collections),
            "jarvis_summary": (self.manifest.get("jarvis") or {}).get("summary", ""),
            "url": f"/apps/{self.slug}",
        }


def _schemas(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {name: spec["schema_json"] for name, spec in (manifest.get("collections") or {}).items()}


def _index_statements(slug: str, manifest: dict[str, Any]) -> list[str]:
    # Identifiers are validated against strict patterns by the manifest model,
    # so they are safe to place in DDL (SQLite cannot bind parameters there).
    statements = []
    for collection, spec in (manifest.get("collections") or {}).items():
        for field in spec.get("indexes") or []:
            name = f"ix_app_{slug.replace('-', '_')}_{collection}_{field}"
            statements.append(
                f"CREATE INDEX IF NOT EXISTS {name} ON app_records (json_extract(value_json, '$.{field}')) "
                f"WHERE app_slug = '{slug}' AND collection = '{collection}'"
            )
    return statements


async def install_package(source: Path) -> ActiveApp:
    """Validate, copy and activate a package. Raises ManifestError when it is not installable."""
    manifest, schemas = load_package(source)
    stored = manifest.model_dump(by_alias=True, mode="json")
    for name, schema in schemas.items():
        stored["collections"][name]["schema_json"] = schema

    destination = settings.apps_path / manifest.slug / str(manifest.version)
    async with apps_session() as session:
        existing = await session.get(InstalledApp, manifest.slug)
        versions = (await session.execute(select(AppVersion.version, AppVersion.manifest_json)
            .where(AppVersion.slug == manifest.slug))).all()
        if versions and manifest.version <= max(version for version, _ in versions):
            raise ManifestError(f"{manifest.slug} version {manifest.version} is not newer than the installed versions")
        if existing is not None:
            active = next(value for version, value in versions if version == existing.active_version)
            problems = compatibility_problems(_schemas(active), schemas)
            if problems:
                raise ManifestError("Incompatible with the active version: " + "; ".join(problems))
        if destination.exists():
            raise ManifestError(f"Package folder already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)
        try:
            if existing is None:
                session.add(InstalledApp(slug=manifest.slug, title=manifest.title, active_version=manifest.version))
                await session.flush()
            else:
                existing.title = manifest.title
                existing.active_version = manifest.version
            session.add(AppVersion(slug=manifest.slug, version=manifest.version, manifest_json=stored,
                                   package_path=str(destination)))
            for statement in _index_statements(manifest.slug, stored):
                await session.execute(text(statement))
        except BaseException:
            shutil.rmtree(destination, ignore_errors=True)
            raise
    return ActiveApp(manifest.slug, manifest.title, manifest.version, stored, destination)


async def rollback(slug: str) -> ActiveApp:
    """Activate the newest installed version older than the active one."""
    async with apps_session() as session:
        app = await session.get(InstalledApp, slug)
        if app is None:
            raise AppNotFound(slug)
        previous = (await session.execute(select(AppVersion).where(
            AppVersion.slug == slug, AppVersion.version < app.active_version,
        ).order_by(AppVersion.version.desc()).limit(1))).scalar_one_or_none()
        if previous is None:
            raise ManifestError(f"{slug} has no earlier version to roll back to")
        app.active_version = previous.version
        app.title = previous.manifest_json["title"]
        return ActiveApp(slug, app.title, previous.version, previous.manifest_json, Path(previous.package_path))


async def get_active(slug: str) -> ActiveApp:
    async with apps_session() as session:
        app = await session.get(InstalledApp, slug)
        if app is None:
            raise AppNotFound(slug)
        version = await session.get(AppVersion, (slug, app.active_version))
        assert version is not None
        return ActiveApp(slug, app.title, version.version, version.manifest_json, Path(version.package_path))


async def list_apps() -> list[ActiveApp]:
    async with apps_session() as session:
        rows = (await session.execute(select(InstalledApp, AppVersion).join(AppVersion, (
            AppVersion.slug == InstalledApp.slug) & (AppVersion.version == InstalledApp.active_version))
            .order_by(InstalledApp.title))).all()
        return [ActiveApp(app.slug, app.title, version.version, version.manifest_json, Path(version.package_path))
                for app, version in rows]


async def find_app(reference: str) -> ActiveApp:
    """Resolve an app by slug or (case-insensitive) title, for spoken references."""
    normalized = " ".join(reference.split()).casefold()
    apps = await list_apps()
    for app in apps:
        if app.slug == normalized or app.title.casefold() == normalized:
            return app
    matches = [app for app in apps if normalized and normalized in app.title.casefold()]
    if len(matches) == 1:
        return matches[0]
    raise AppNotFound(reference)


async def package_file(slug: str, version: int, relative: str) -> Path:
    """A file inside an installed version's ui/ folder, or AppNotFound."""
    async with apps_session() as session:
        row = await session.get(AppVersion, (slug, version))
    if row is None:
        raise AppNotFound(slug)
    ui_root = (Path(row.package_path) / "ui").resolve()
    candidate = (ui_root / relative).resolve()
    if not candidate.is_relative_to(ui_root) or not candidate.is_file():
        raise AppNotFound(relative)
    return candidate

