"""Install, activate, roll back, reset and uninstall app versions.

Installing copies a validated package to `apps_dir/<slug>/<version>/`, records
the version and makes it active in one transaction. Every installed version is
kept, so rolling back only moves the active pointer. Installing the same package
again is a no-op, so a builder can safely retry its install step.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from hashlib import sha256
from pathlib import Path
import shutil
from typing import Any

from sqlalchemy import delete, func, select, text

from app.app_platform.manifest import ManifestError, compatibility_problems, load_package, package_workflows
from app.app_platform.store import AppRecord, AppVersion, InstalledApp, apps_session
from app.config import settings


MAX_PACKAGE_BYTES = 20 * 1024 * 1024
MAX_PACKAGE_FILES = 2000


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
            "functions": sorted(self.manifest.get("functions") or {}),
            "files": bool(self.manifest.get("files")),
            "jarvis_summary": (self.manifest.get("jarvis") or {}).get("summary", ""),
            "url": f"/apps/{self.slug}",
        }


def _schemas(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {name: spec["schema_json"] for name, spec in (manifest.get("collections") or {}).items()}


def _index_name(slug: str, collection: str, field: str) -> str:
    return f"ix_app_{slug.replace('-', '_')}_{collection}_{field}"


def _index_statements(slug: str, manifest: dict[str, Any]) -> list[str]:
    # Identifiers are validated against strict patterns by the manifest model,
    # so they are safe to place in DDL (SQLite cannot bind parameters there).
    statements = []
    for collection, spec in (manifest.get("collections") or {}).items():
        for field in spec.get("indexes") or []:
            statements.append(
                f"CREATE INDEX IF NOT EXISTS {_index_name(slug, collection, field)} ON app_records "
                f"(json_extract(value_json, '$.{field}')) WHERE app_slug = '{slug}' AND collection = '{collection}'"
            )
    return statements


def package_digest(root: Path) -> str:
    """A digest of every file's path and bytes; also enforces the package size limits."""
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if len(files) > MAX_PACKAGE_FILES:
        raise ManifestError(f"Package has more than {MAX_PACKAGE_FILES} files")
    digest, total = sha256(), 0
    for path in files:
        if path.is_symlink():
            raise ManifestError(f"Package contains a link: {path.relative_to(root).as_posix()}")
        data = path.read_bytes()
        total += len(data)
        if total > MAX_PACKAGE_BYTES:
            raise ManifestError("Package is larger than 20 MiB")
        digest.update(path.relative_to(root).as_posix().encode() + b"\0" + sha256(data).digest())
    return digest.hexdigest()


async def consumers(slug: str) -> dict[str, list[str]]:
    """{collection: titles of the other installed apps with a grant on it}."""
    shared: dict[str, list[str]] = {}
    for app in await list_apps():
        if app.slug == slug:
            continue
        grants = app.manifest.get("grants") or {}
        for grant in {*(grants.get("read") or []), *(grants.get("write") or [])}:
            owner, _, collection = grant.partition(".")
            if owner == slug:
                shared.setdefault(collection, []).append(app.title)
    return shared


async def schema_change_problems(slug: str, old: dict[str, dict[str, Any]],
                                 new: dict[str, dict[str, Any]]) -> list[str]:
    """Collection changes that would break stored data or another app ("stored data decides").

    A collection may always gain optional fields. Any other change (a field
    removed, renamed or retyped, a new required field, a removed collection)
    is allowed only when no other app has a grant on the collection and every
    stored record still fits the new schema (a removed collection must be empty).
    """
    from jsonschema import Draft202012Validator

    shared = await consumers(slug)
    problems: list[str] = []
    for name, old_schema in old.items():
        changes = compatibility_problems({name: old_schema}, new)
        if not changes:
            continue
        if name in shared:
            problems.append(f"{'; '.join(changes)}: {', '.join(sorted(shared[name]))} uses {slug}.{name}, so it "
                            "may only gain optional fields; put a new shape in a new collection")
            continue
        async with apps_session() as session:
            rows = list((await session.execute(select(AppRecord.record_key, AppRecord.value_json).where(
                AppRecord.app_slug == slug, AppRecord.collection == name))).all())
        if name not in new:
            if rows:
                problems.append(f"collection {name} was removed but still holds {len(rows)} records")
            continue
        validator = Draft202012Validator(new[name])
        misfits = [(key, error.message) for key, value in rows for error in list(validator.iter_errors(value))[:1]]
        if misfits:
            key, message = misfits[0]
            problems.append(f"{'; '.join(changes)}, and {len(misfits)} of {len(rows)} stored {name} records "
                            f"don't fit the new schema (for example {key!r}: {message})")
    return problems


async def grant_problems(slug: str, grants: dict[str, Any]) -> list[str]:
    """Granted collections that don't exist: the app named in a grant must be installed with it."""
    problems = []
    for grant in sorted({*(grants.get("read") or []), *(grants.get("write") or [])}):
        owner, _, collection = grant.partition(".")
        if owner == "system":
            continue  # the pattern allows only existing system collections
        try:
            provider = await get_active(owner)
        except AppNotFound:
            problems.append(f"Grant {grant}: no app {owner} is installed")
            continue
        if collection not in provider.collections:
            problems.append(f"Grant {grant}: {provider.title} has no collection {collection} "
                            f"(it has {', '.join(sorted(provider.collections)) or 'none'})")
    return problems


async def install_package(source: Path) -> ActiveApp:
    """Validate, copy and activate a package. Raises ManifestError when it is not installable."""
    from app.app_platform.workflows import workflow_conflicts

    manifest, schemas = load_package(source)
    digest = package_digest(source)
    stored = manifest.model_dump(by_alias=True, mode="json")
    for name, schema in schemas.items():
        stored["collections"][name]["schema_json"] = schema
    stored["workflow_definitions"] = package_workflows(source, manifest)
    conflicts = workflow_conflicts(manifest.slug, manifest.workflows)
    if conflicts:
        raise ManifestError(f"Workflow ids already in use: {', '.join(conflicts)}")

    destination = settings.apps_path / manifest.slug / str(manifest.version)
    async with apps_session() as session:
        existing = await session.get(InstalledApp, manifest.slug)
        versions = (await session.execute(select(AppVersion.version, AppVersion.manifest_json)
            .where(AppVersion.slug == manifest.slug))).all()
        same = next((value for version, value in versions if version == manifest.version), None)
        if same is not None and package_digest(destination) == digest:
            return ActiveApp(manifest.slug, same["title"], manifest.version, same, destination)
        if versions and manifest.version <= max(version for version, _ in versions):
            raise ManifestError(f"{manifest.slug} version {manifest.version} is not newer than the installed versions")
        if existing is not None:
            active = next(value for version, value in versions if version == existing.active_version)
            problems = await schema_change_problems(manifest.slug, _schemas(active), schemas)
            if problems:
                raise ManifestError("Incompatible with stored data: " + "; ".join(problems))
        problems = await grant_problems(manifest.slug, stored.get("grants") or {})
        if problems:
            raise ManifestError("; ".join(problems))
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



async def uninstall(slug: str) -> None:
    """Remove an app with every version, record and index. It cannot be undone."""
    async with apps_session() as session:
        app = await session.get(InstalledApp, slug)
        if app is None:
            raise AppNotFound(slug)
        manifests = (await session.execute(select(AppVersion.manifest_json).where(AppVersion.slug == slug))).scalars()
        for manifest in manifests:
            for collection, spec in (manifest.get("collections") or {}).items():
                for field in spec.get("indexes") or []:
                    await session.execute(text(f"DROP INDEX IF EXISTS {_index_name(slug, collection, field)}"))
        # Versions, records, files, runs and schedules go with the app through ON DELETE CASCADE.
        await session.execute(delete(InstalledApp).where(InstalledApp.slug == slug))
    shutil.rmtree(settings.apps_path / slug, ignore_errors=True)
    shutil.rmtree(settings.app_files_path / slug, ignore_errors=True)


async def reset(slug: str, collection: str | None = None, *, files: bool = False) -> dict[str, int]:
    """Delete an app's records (all, or one collection's) and, when asked, its files."""
    from app.app_platform.files import clear_files

    app = await get_active(slug)
    if collection is not None and collection not in app.collections:
        raise ManifestError(f"{slug} has no collection {collection}")
    statement = delete(AppRecord).where(AppRecord.app_slug == slug)
    if collection is not None:
        statement = statement.where(AppRecord.collection == collection)
    async with apps_session() as session:
        records = (await session.execute(statement)).rowcount or 0
    return {"records": records, "files": await clear_files(slug) if files else 0}


async def status(slug: str) -> dict[str, Any]:
    """What Jarvis needs to judge an app's state: versions, data, function runs and recent errors."""
    from app.app_platform.errors import recent_errors
    from app.app_platform.files import usage
    from app.app_platform.function_host import recent_runs
    from app.app_platform.store import AppSchedule

    app = await get_active(slug)
    async with apps_session() as session:
        versions = list((await session.execute(select(AppVersion.version).where(AppVersion.slug == slug)
            .order_by(AppVersion.version))).scalars())
        counts = dict((await session.execute(select(AppRecord.collection, func.count()).where(
            AppRecord.app_slug == slug).group_by(AppRecord.collection))).all())
        schedules = {row.function: row.next_run_at.replace(tzinfo=row.next_run_at.tzinfo or timezone.utc).isoformat()
                     for row in (await session.execute(
            select(AppSchedule).where(AppSchedule.app_slug == slug))).scalars()}
    functions = {name: {"trigger": spec.get("trigger"), **({"next_run_at": schedules[name]} if name in schedules else {})}
                 for name, spec in sorted((app.manifest.get("functions") or {}).items())}
    return {**app.summary(), "versions": versions,
            "records": {name: counts.get(name, 0) for name in sorted(app.collections)},
            "functions": functions, "stored_files": await usage(slug) if app.manifest.get("files") else None,
            "grants": app.manifest.get("grants") or {}, "used_by": await consumers(slug),
            "recent_runs": await recent_runs(slug), "recent_errors": await recent_errors(slug)}


async def data_catalog() -> dict[str, Any]:
    """Every installed app's collections (schema, record count, an example) and the system collections."""
    from app.app_platform import system

    apps = []
    for app in await list_apps():
        shared = await consumers(app.slug)
        collections = {}
        async with apps_session() as session:
            for name, spec in sorted(app.collections.items()):
                count = (await session.execute(select(func.count()).where(
                    AppRecord.app_slug == app.slug, AppRecord.collection == name))).scalar_one()
                example = (await session.execute(select(AppRecord.value_json).where(
                    AppRecord.app_slug == app.slug, AppRecord.collection == name)
                    .order_by(AppRecord.updated_at.desc()).limit(1))).scalar_one_or_none()
                collections[f"{app.slug}.{name}"] = {"schema": spec["schema_json"], "records": count,
                                                     "example": example, "used_by": sorted(shared.get(name, []))}
        apps.append({"slug": app.slug, "title": app.title, "version": app.version,
                     "summary": (app.manifest.get("jarvis") or {}).get("summary", ""), "collections": collections})
    return {"apps": apps, "system": {f"system.{name}": {"schema": schema, "read_only": True}
                                     for name, schema in system.SCHEMAS.items()}}


async def checkout(slug: str, destination: Path) -> ActiveApp:
    """Copy the active version's package to an empty folder, for a builder to change."""
    app = await get_active(slug)
    if destination.exists() and any(destination.iterdir()):
        raise ManifestError(f"Checkout folder is not empty: {destination}")
    shutil.copytree(app.package_path, destination, dirs_exist_ok=True)
    return app
