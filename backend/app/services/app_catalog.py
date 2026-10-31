"""Old generated-app identities and their bounded optimistic JSON state.

Superseded by the app platform (docs/jarvis-tasks/13-app-platform.md); this goes
with the rest of the old generated-apps code in platform step 6.
"""
from __future__ import annotations

import json
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from sqlalchemy import select, update

from app.db.tables import AppDataRecord, CockpitApp, GeneratedApp
from app.services.app_capabilities import StorageGrant, assert_grant

RECORD_LIMIT = 64 * 1024
APP_QUOTA = 10 * 1024 * 1024


async def sync_generated_app(session, generated: GeneratedApp) -> CockpitApp:
    app = await session.get(CockpitApp, generated.cockpit_app_id) if generated.cockpit_app_id else None
    if app is None:
        slug = generated.slug
        if (await session.execute(select(CockpitApp.id).where(CockpitApp.slug == slug))).first():
            slug = f"generated-{generated.id}"
        app = CockpitApp(id=str(uuid5(NAMESPACE_URL, f"ai-cockpit:app:generated:{generated.id}")),
            slug=slug, title=generated.title, kind="generated", runtime="generated.v1",
            route_path=generated.route_path)
        session.add(app)
        await session.flush()
        generated.cockpit_app_id = app.id
    app.title, app.description, app.route_path = generated.title, generated.description, generated.route_path
    return app


async def get_app(session, slug: str) -> CockpitApp:
    app = (await session.execute(select(CockpitApp).where(CockpitApp.slug == slug, CockpitApp.enabled.is_(True)))).scalar_one_or_none()
    if app is None:
        raise HTTPException(404, "App not found")
    return app


async def present_app(session, app: CockpitApp) -> dict:
    generated = (await session.execute(select(GeneratedApp).where(GeneratedApp.cockpit_app_id == app.id))).scalar_one_or_none()
    return {"id": app.id, "slug": app.slug, "title": generated.title if generated else app.title,
        "description": generated.description if generated else app.description, "kind": app.kind,
        "route_path": generated.route_path if generated else app.route_path, "runtime": app.runtime,
        "enabled": app.enabled, "status": generated.status if generated else "ready",
        "generated_app_id": generated.id if generated else None, "ordering": [],
        "created_at": app.created_at, "updated_at": generated.updated_at if generated else app.updated_at}


def _check_scope(app: CockpitApp, namespace: str, key: str) -> None:
    if not 1 <= len(key) <= 128 or any(ord(char) < 32 for char in key):
        raise HTTPException(422, "Invalid record key")
    if namespace != "state" or app.kind != "generated":
        raise HTTPException(404, "Namespace not registered")


async def _record(session, app: CockpitApp, namespace: str, key: str) -> AppDataRecord | None:
    return (await session.execute(select(AppDataRecord).where(AppDataRecord.cockpit_app_id == app.id,
        AppDataRecord.scope_key == "", AppDataRecord.namespace == namespace,
        AppDataRecord.record_key == key))).scalar_one_or_none()


async def read_data(session, *, app: CockpitApp, namespace: str, key: str) -> dict:
    _check_scope(app, namespace, key)
    record = await _record(session, app, namespace, key)
    return {"value": record.value_json if record else None, "revision": record.revision if record else 0}


async def write_data(session, *, slug: str, namespace: str, key: str, value: dict,
                     expected_revision: int, grant: StorageGrant | None = None) -> dict:
    if type(expected_revision) is not int or expected_revision < 0:
        raise HTTPException(422, "Expected revision must be a nonnegative integer")
    # First statement takes the app write lock BEFORE reading quota/revision. This
    # serializes quota+CAS on SQLite across processes, not just this event loop.
    locked = await session.execute(update(CockpitApp).where(CockpitApp.slug == slug,
        CockpitApp.enabled.is_(True)).values(storage_bytes=CockpitApp.storage_bytes).returning(CockpitApp.id))
    if locked.scalar_one_or_none() is None:
        raise HTTPException(404, "App not found")
    app = await get_app(session, slug)
    assert_grant(app, grant)
    _check_scope(app, namespace, key)
    if not isinstance(value, dict):
        raise HTTPException(422, "App state must be a JSON object")
    try:
        size = len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(422, "Invalid JSON state") from None
    if size > RECORD_LIMIT:
        raise HTTPException(413, "Record exceeds 64 KiB")
    record = await _record(session, app, namespace, key)
    if expected_revision != (record.revision if record else 0):
        raise HTTPException(409, "App state revision is stale")
    next_size = app.storage_bytes - (record.size_bytes if record else 0) + size
    if next_size > APP_QUOTA:
        raise HTTPException(413, "App storage quota exceeded")
    if record is None:
        record = AppDataRecord(cockpit_app_id=app.id, scope_key="", namespace=namespace, record_key=key,
            revision=1, value_json=value, size_bytes=size)
        session.add(record)
    else:
        record.revision += 1
        record.value_json, record.size_bytes = value, size
    app.storage_bytes = next_size
    await session.flush()
    return {"value": value, "revision": record.revision}
