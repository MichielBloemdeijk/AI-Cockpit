"""Typed collection records: JSON documents validated against the app's schema.

Writes use optimistic concurrency: `expected_revision` 0 means "must not exist",
a positive number must match the stored revision, and None writes regardless.
"""
from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.app_platform.events import queue_change
from app.app_platform.lifecycle import ActiveApp, get_active
from app.app_platform.manifest import FIELD_PATTERN
from app.app_platform.store import AppRecord, apps_session

KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
RECORD_LIMIT_BYTES = 256 * 1024
QUERY_LIMIT = 200


class RecordError(ValueError):
    """Invalid request: unknown collection, bad key, schema violation."""


class RevisionConflict(RecordError):
    pass


def _record_view(row: AppRecord) -> dict[str, Any]:
    return {"key": row.record_key, "value": row.value_json, "revision": row.revision,
            "updated_at": row.updated_at.isoformat()}


def _spec(app: ActiveApp, collection: str) -> dict[str, Any]:
    spec = app.collections.get(collection)
    if spec is None:
        raise RecordError(f"{app.slug} has no collection {collection}")
    return spec


def _check_key(key: str) -> None:
    if not KEY_PATTERN.fullmatch(key):
        raise RecordError("Record keys use 1-128 letters, digits, '_', '.', ':' or '-'")


async def get_record(slug: str, collection: str, key: str) -> dict[str, Any] | None:
    app = await get_active(slug)
    _spec(app, collection)
    _check_key(key)
    async with apps_session() as session:
        row = (await session.execute(select(AppRecord).where(AppRecord.app_slug == slug,
            AppRecord.collection == collection, AppRecord.record_key == key))).scalar_one_or_none()
        return _record_view(row) if row else None


async def put_record(slug: str, collection: str, key: str, value: Any, *,
                     expected_revision: int | None = None) -> dict[str, Any]:
    app = await get_active(slug)
    spec = _spec(app, collection)
    _check_key(key)
    errors = sorted(Draft202012Validator(spec["schema_json"]).iter_errors(value), key=lambda error: list(error.path))
    if errors:
        location = "/".join(str(part) for part in errors[0].path) or "(record)"
        raise RecordError(f"{collection} record is invalid at {location}: {errors[0].message}")
    if len(json.dumps(value, separators=(",", ":")).encode()) > RECORD_LIMIT_BYTES:
        raise RecordError("Record is larger than 256 KiB")
    try:
        return await _write(app, collection, key, value, expected_revision)
    except IntegrityError:
        # Another writer created the key between our read and insert; decide again against it.
        return await _write(app, collection, key, value, expected_revision)


async def _write(app: ActiveApp, collection: str, key: str, value: Any, expected_revision: int | None) -> dict[str, Any]:
    slug = app.slug
    async with apps_session() as session:
        row = (await session.execute(select(AppRecord).where(AppRecord.app_slug == slug,
            AppRecord.collection == collection, AppRecord.record_key == key))).scalar_one_or_none()
        current = row.revision if row else 0
        if expected_revision is not None and expected_revision != current:
            raise RevisionConflict(f"Expected revision {expected_revision}, found {current}")
        if row is None:
            row = AppRecord(app_slug=slug, collection=collection, record_key=key, value_json=value, revision=1)
            session.add(row)
        else:
            row.value_json = value
            row.revision = current + 1
        await session.flush()
        await session.refresh(row)
        await queue_change(session, app, {"event": "record_changed", "collection": collection, "key": key,
                                          "change": "put", "revision": row.revision, "value": value})
        return _record_view(row)


async def delete_record(slug: str, collection: str, key: str, *, expected_revision: int | None = None) -> bool:
    app = await get_active(slug)
    _spec(app, collection)
    _check_key(key)
    async with apps_session() as session:
        row = (await session.execute(select(AppRecord).where(AppRecord.app_slug == slug,
            AppRecord.collection == collection, AppRecord.record_key == key))).scalar_one_or_none()
        if row is None:
            return False
        if expected_revision is not None and expected_revision != row.revision:
            raise RevisionConflict(f"Expected revision {expected_revision}, found {row.revision}")
        await session.delete(row)
        await queue_change(session, app, {"event": "record_changed", "collection": collection, "key": key,
                                          "change": "delete"})
        return True


async def query_records(slug: str, collection: str, *, where: dict[str, Any] | None = None,
                        order: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Equality filters and ordering on top-level fields; declared indexes make them fast."""
    app = await get_active(slug)
    _spec(app, collection)
    statement = select(AppRecord).where(AppRecord.app_slug == slug, AppRecord.collection == collection)
    for field, expected in (where or {}).items():
        if not re.fullmatch(FIELD_PATTERN, field):
            raise RecordError(f"Cannot filter on {field}")
        if not (expected is None or isinstance(expected, (str, int, float, bool))):
            raise RecordError("Filters compare top-level fields with a single value")
        extracted = func.json_extract(AppRecord.value_json, f"$.{field}")
        statement = statement.where(extracted.is_(None) if expected is None else extracted == expected)
    if order:
        descending = order.startswith("-")
        field = order.removeprefix("-")
        if field in {"updated_at", "key"}:
            column = AppRecord.updated_at if field == "updated_at" else AppRecord.record_key
        elif re.fullmatch(FIELD_PATTERN, field):
            column = func.json_extract(AppRecord.value_json, f"$.{field}")
        else:
            raise RecordError(f"Cannot order by {field}")
        statement = statement.order_by(column.desc() if descending else column.asc())
    else:
        statement = statement.order_by(AppRecord.updated_at.desc())
    statement = statement.limit(max(1, min(int(limit), QUERY_LIMIT)))
    async with apps_session() as session:
        return [_record_view(row) for row in (await session.execute(statement)).scalars()]
