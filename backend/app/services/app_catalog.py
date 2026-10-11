"""Trusted app identities and bounded optimistic JSON state (never canonical content)."""
from __future__ import annotations

import json
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool
from sqlalchemy import select, update

from app.db.tables import AppDataRecord, CockpitApp, GeneratedApp, ResultPage
from app.services.app_capabilities import StorageGrant, assert_grant

RECORD_LIMIT = 64 * 1024
APP_QUOTA = 10 * 1024 * 1024
COLLECTIONS = {"documents": "Documents", "recipes": "Recipes"}


class SavedValue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    saved: StrictBool


async def collection_app(session, slug: str) -> CockpitApp:
    if slug not in COLLECTIONS:
        raise ValueError("Maintained app is not registered")
    app = (await session.execute(select(CockpitApp).where(CockpitApp.slug == slug))).scalar_one()
    if app.kind != "collection" or not app.enabled:
        raise ValueError("Maintained app is unavailable")
    return app


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
    generated = (await session.execute(select(GeneratedApp).where(GeneratedApp.cockpit_app_id == app.id))).scalar_one_or_none() if app.kind == "generated" else None
    return {"id": app.id, "slug": app.slug, "title": generated.title if generated else app.title,
        "description": generated.description if generated else app.description, "kind": app.kind,
        "route_path": generated.route_path if generated else app.route_path, "runtime": app.runtime,
        "enabled": app.enabled, "status": generated.status if generated else "ready",
        "generated_app_id": generated.id if generated else None,
        "ordering": ["updated", "title"] if app.kind == "collection" else [],
        "created_at": app.created_at, "updated_at": generated.updated_at if generated else app.updated_at}


async def storage_scope(session, app: CockpitApp, namespace: str, key: str, public_id: str | None) -> ResultPage | None:
    if not 1 <= len(key) <= 128 or any(ord(char) < 32 for char in key):
        raise HTTPException(422, "Invalid record key")
    if namespace == "saved" and app.kind == "collection":
        # Saved records always use result scope, derived from the key; no aliasing.
        if public_id is not None and public_id != key:
            raise HTTPException(422, "Saved key must match result")
        public_id = key
    elif namespace != "state" or app.kind != "generated":
        raise HTTPException(404, "Namespace not registered")
    page = None
    if public_id is not None:
        page = (await session.execute(select(ResultPage).where(ResultPage.public_id == public_id,
            ResultPage.cockpit_app_id == app.id))).scalar_one_or_none()
        if page is None:
            raise HTTPException(404, "Result not found in app")
    return page


async def read_data(session, *, app: CockpitApp, namespace: str, key: str, public_id: str | None = None) -> dict:
    page = await storage_scope(session, app, namespace, key, public_id)
    record = (await session.execute(select(AppDataRecord).where(AppDataRecord.cockpit_app_id == app.id,
        AppDataRecord.scope_key == (page.id if page else ""), AppDataRecord.namespace == namespace,
        AppDataRecord.record_key == key))).scalar_one_or_none()
    return {"value": record.value_json if record else ({"saved": False} if namespace == "saved" else None),
        "revision": record.revision if record else 0}


async def write_data(session, *, slug: str, namespace: str, key: str, value: dict,
                     expected_revision: int, public_id: str | None = None, grant: StorageGrant | None = None) -> dict:
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
    if grant is not None and public_id is not None:
        raise HTTPException(403, "App capability cannot access result-scoped state")
    page = await storage_scope(session, app, namespace, key, public_id)
    if namespace == "saved":
        try:
            value = SavedValue.model_validate(value).model_dump()
        except ValueError:
            raise HTTPException(422, "Invalid saved value") from None
    if not isinstance(value, dict):
        raise HTTPException(422, "App state must be a JSON object")
    try:
        size = len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(422, "Invalid JSON state") from None
    if size > RECORD_LIMIT:
        raise HTTPException(413, "Record exceeds 64 KiB")
    record = (await session.execute(select(AppDataRecord).where(AppDataRecord.cockpit_app_id == app.id,
        AppDataRecord.scope_key == (page.id if page else ""), AppDataRecord.namespace == namespace,
        AppDataRecord.record_key == key))).scalar_one_or_none()
    if expected_revision != (record.revision if record else 0):
        raise HTTPException(409, "App state revision is stale")
    next_size = app.storage_bytes - (record.size_bytes if record else 0) + size
    if next_size > APP_QUOTA:
        raise HTTPException(413, "App storage quota exceeded")
    if record is None:
        record = AppDataRecord(cockpit_app_id=app.id, result_page_id=page.id if page else None,
            scope_key=page.id if page else "", namespace=namespace, record_key=key, revision=1,
            value_json=value, size_bytes=size)
        session.add(record)
    else:
        record.revision += 1
        record.value_json, record.size_bytes = value, size
    app.storage_bytes = next_size
    await session.flush()
    return {"value": value, "revision": record.revision}
