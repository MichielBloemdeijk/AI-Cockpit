"""Per-app file stores: upload, download, list and delete, within the app's quota.

Only apps whose manifest has a `files` section have one. Names are paths such
as `photos/2026/a.jpg`; the bytes are stored under `app_files_dir/<slug>/<id>`
so names never become filesystem paths.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
import shutil
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from app.app_platform.events import queue_change
from app.app_platform.lifecycle import ActiveApp, get_active
from app.app_platform.store import AppFile, apps_session, utc_now
from app.config import settings

NAME_PATTERN = re.compile(r"^(?!.*//)(?!.*(^|/)\.\.?(/|$))[A-Za-z0-9_][A-Za-z0-9_. /-]{0,198}(?<![/ ])$")
FILE_LIMIT_BYTES = 25 * 1024 * 1024
LIST_LIMIT = 500
DEFAULT_TYPE = "application/octet-stream"
TYPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,63}$")


class FileError(ValueError):
    """Invalid request: no file store, bad name, too large, over quota."""


def _meta(row: AppFile) -> dict[str, Any]:
    return {"name": row.name, "content_type": row.content_type, "size": row.size, "sha256": row.sha256,
            "updated_at": row.updated_at.isoformat()}


def _quota(app: ActiveApp) -> int:
    spec = app.manifest.get("files")
    if not spec:
        raise FileError(f"{app.slug} has no file store; declare \"files\" in its manifest")
    return int(spec.get("quota_mb", 100)) * 1024 * 1024


def check_name(name: str) -> None:
    if not NAME_PATTERN.fullmatch(name):
        raise FileError("File names use letters, digits, '_', '.', '-', spaces and '/' folders (at most 199)")


def _blob(slug: str, file_id: str) -> Path:
    return settings.app_files_path / slug / file_id


def files_root(slug: str) -> Path:
    return settings.app_files_path / slug


async def put_file(slug: str, name: str, data: bytes, content_type: str | None = None) -> dict[str, Any]:
    app = await get_active(slug)
    quota = _quota(app)
    check_name(name)
    content_type = (content_type or DEFAULT_TYPE).split(";")[0].strip().lower() or DEFAULT_TYPE
    if not TYPE_PATTERN.fullmatch(content_type):
        raise FileError(f"Invalid content type {content_type!r}")
    if len(data) > FILE_LIMIT_BYTES:
        raise FileError("Files are at most 25 MiB")
    file_id = str(uuid4())
    path = _blob(slug, file_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    replaced: str | None = None
    try:
        async with apps_session() as session:
            row = (await session.execute(select(AppFile).where(AppFile.app_slug == slug, AppFile.name == name))
                   ).scalar_one_or_none()
            used = (await session.execute(select(func.coalesce(func.sum(AppFile.size), 0))
                    .where(AppFile.app_slug == slug))).scalar_one()
            if used - (row.size if row else 0) + len(data) > quota:
                raise FileError(f"The app's file store is full ({quota // (1024 * 1024)} MB)")
            if row is None:
                row = AppFile(id=file_id, app_slug=slug, name=name, content_type=content_type, size=len(data),
                              sha256=sha256(data).hexdigest())
                session.add(row)
            else:
                replaced = row.id
                # The id names the stored bytes, so a new version gets a new id.
                row.id, row.content_type, row.size = file_id, content_type, len(data)
                row.sha256, row.updated_at = sha256(data).hexdigest(), utc_now()
            await session.flush()
            await queue_change(session, app, {"event": "file_changed", "name": name, "change": "put",
                                              "size": len(data), "content_type": content_type})
            meta = _meta(row)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if replaced:
        _blob(slug, replaced).unlink(missing_ok=True)
    return meta


async def get_file(slug: str, name: str) -> tuple[dict[str, Any], Path] | None:
    app = await get_active(slug)
    _quota(app)
    check_name(name)
    async with apps_session() as session:
        row = (await session.execute(select(AppFile).where(AppFile.app_slug == slug, AppFile.name == name))
               ).scalar_one_or_none()
    return (_meta(row), _blob(slug, row.id)) if row else None


async def read_file(slug: str, name: str) -> tuple[dict[str, Any], bytes] | None:
    found = await get_file(slug, name)
    return (found[0], found[1].read_bytes()) if found else None


async def list_files(slug: str, prefix: str | None = None, limit: int = LIST_LIMIT) -> list[dict[str, Any]]:
    app = await get_active(slug)
    _quota(app)
    statement = select(AppFile).where(AppFile.app_slug == slug).order_by(AppFile.name)
    if prefix:
        statement = statement.where(func.substr(AppFile.name, 1, len(prefix)) == prefix)
    async with apps_session() as session:
        rows = (await session.execute(statement.limit(max(1, min(int(limit), LIST_LIMIT))))).scalars()
        return [_meta(row) for row in rows]


async def delete_file(slug: str, name: str) -> bool:
    app = await get_active(slug)
    _quota(app)
    check_name(name)
    async with apps_session() as session:
        row = (await session.execute(select(AppFile).where(AppFile.app_slug == slug, AppFile.name == name))
               ).scalar_one_or_none()
        if row is None:
            return False
        file_id = row.id
        await session.delete(row)
        await queue_change(session, app, {"event": "file_changed", "name": name, "change": "delete"})
    _blob(slug, file_id).unlink(missing_ok=True)
    return True


async def usage(slug: str) -> dict[str, int]:
    async with apps_session() as session:
        count, size = (await session.execute(select(func.count(), func.coalesce(func.sum(AppFile.size), 0))
                       .where(AppFile.app_slug == slug))).one()
    return {"count": int(count), "bytes": int(size)}


async def clear_files(slug: str) -> int:
    """Delete every file of an app (reset); returns how many."""
    async with apps_session() as session:
        rows = list((await session.execute(select(AppFile).where(AppFile.app_slug == slug))).scalars())
        for row in rows:
            await session.delete(row)
    for row in rows:
        _blob(slug, row.id).unlink(missing_ok=True)
    return len(rows)


def remove_store(slug: str) -> None:
    """Remove the app's folder of stored bytes (uninstall)."""
    shutil.rmtree(files_root(slug), ignore_errors=True)
