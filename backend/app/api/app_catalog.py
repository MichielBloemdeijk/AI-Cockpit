"""Unified catalog, current-only collections and explicitly registered app state."""
from __future__ import annotations

import base64
from datetime import datetime
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import and_, func, or_, select, update

from app.api.results import protect_state_write
from app.db.session import session_scope
from app.db.tables import CockpitApp, ResultPage
from app.models.apps import AppStorageCapabilityRequest
from app.services.app_catalog import get_app, present_app, read_data, write_data
from app.services.jarvis_devices import require_jarvis_auth
from app.services.task_results import result_snapshot
from app.services.app_capabilities import authorize_storage, issue_capability, StorageGrant, assert_grant
from app.services.auth import require_auth

router = APIRouter(prefix="/api/app-catalog", tags=["app-catalog"], dependencies=[Depends(require_jarvis_auth)])
data_router = APIRouter(prefix="/api/app-catalog", tags=["app-data"], dependencies=[Depends(authorize_storage)])


@router.post("/{slug}/storage-capability", dependencies=[Depends(require_auth), Depends(protect_state_write)])
async def create_storage_capability(slug: str, body: AppStorageCapabilityRequest):
    async with session_scope() as session:
        return issue_capability(await get_app(session, slug), read_only=body.read_only)


@router.post("/{slug}/storage-capability/revoke", dependencies=[Depends(require_auth), Depends(protect_state_write)])
async def revoke_storage_capabilities(slug: str):
    async with session_scope() as session:
        generation = (await session.execute(update(CockpitApp).where(CockpitApp.slug == slug,
            CockpitApp.kind == "generated", CockpitApp.enabled.is_(True))
            .values(capability_revision=CockpitApp.capability_revision + 1)
            .returning(CockpitApp.capability_revision))).scalar_one_or_none()
        if generation is None:
            raise HTTPException(404, "Generated app not available")
        return {"generation": generation}


@router.get("")
async def list_catalog():
    async with session_scope() as session:
        apps = (await session.execute(select(CockpitApp).where(CockpitApp.enabled.is_(True)).order_by(CockpitApp.kind, CockpitApp.title, CockpitApp.id))).scalars().all()
        return [await present_app(session, app) for app in apps]


@router.get("/{slug}")
async def app_detail(slug: str):
    async with session_scope() as session:
        return await present_app(session, await get_app(session, slug))


@router.get("/{slug}/results")
async def collection_results(slug: str, search: str = Query(default="", max_length=200),
    order: str = Query(default="updated", pattern="^(updated|title)$"),
    cursor: str | None = Query(default=None, max_length=2048), limit: int = Query(default=30, ge=1, le=100)):
    async with session_scope() as session:
        app = await get_app(session, slug)
        if app.kind != "collection":
            raise HTTPException(404, "App is not a maintained collection")
        statement = select(ResultPage).where(ResultPage.cockpit_app_id == app.id)
        if search:
            term = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            statement = statement.where(or_(ResultPage.title.ilike(f"%{term}%", escape="\\"), ResultPage.summary.ilike(f"%{term}%", escape="\\")))
        column = func.coalesce(ResultPage.title, "") if order == "title" else ResultPage.updated_at
        if cursor:
            try:
                decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if decoded["scope"] != [slug, search, order] or not isinstance(decoded["id"], str):
                    raise ValueError
                value = decoded["value"] if order == "title" else datetime.fromisoformat(decoded["value"])
                if order == "title" and not isinstance(value, str):
                    raise ValueError
            except (ValueError, TypeError, KeyError, UnicodeError):
                raise HTTPException(400, "Invalid collection cursor") from None
            compare = column > value if order == "title" else column < value
            statement = statement.where(or_(compare, and_(column == value, ResultPage.id > decoded["id"])))
        statement = statement.order_by(column.asc() if order == "title" else column.desc(), ResultPage.id).limit(limit + 1)
        pages = (await session.execute(statement)).scalars().all()
        next_cursor = None
        if len(pages) > limit:
            last = pages[limit - 1]
            value = (last.title or "") if order == "title" else last.updated_at.isoformat()
            next_cursor = base64.urlsafe_b64encode(json.dumps({"scope": [slug, search, order], "id": last.id, "value": value}).encode()).decode()
        cards = []
        for page in pages[:limit]:
            snapshot = await result_snapshot(session, page)
            cards.append({key: snapshot[key] for key in ("public_id", "url", "result_kind", "title", "summary", "status",
                "display_state", "progress_label", "warning", "actions", "revision_number", "state_revision", "updated_at")})
        return {"items": cards, "next_cursor": next_cursor}


class DataWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: dict
    expected_revision: StrictInt = Field(ge=0)
    result_public_id: str | None = Field(default=None, max_length=36)


@data_router.get("/{slug}/data/{namespace}/{key:path}")
async def get_data(slug: str, namespace: str, key: str, result_public_id: str | None = Query(default=None, max_length=36), grant: StorageGrant | None = Depends(authorize_storage)):
    async with session_scope() as session:
        app = await get_app(session, slug)
        assert_grant(app, grant)
        return await read_data(session, app=app, namespace=namespace, key=key, public_id=result_public_id)


@data_router.put("/{slug}/data/{namespace}/{key:path}", dependencies=[Depends(protect_state_write)])
async def put_data(slug: str, namespace: str, key: str, body: DataWrite, grant: StorageGrant | None = Depends(authorize_storage)):
    async with session_scope() as session:
        result = await write_data(session, slug=slug, namespace=namespace, key=key, value=body.value,
            expected_revision=body.expected_revision, public_id=body.result_public_id, grant=grant)
        if namespace == "saved":
            from app.api.results import find_result
            from app.services.task_results import append_result_event
            page = await find_result(session, key)
            await append_result_event(session, page, "action_state", event_key=f"saved:{result['revision']}")
        return result
