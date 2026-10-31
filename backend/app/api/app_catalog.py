"""Old generated-app catalog and state; goes with the old generated-apps code in platform step 6."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import select

from app.api.private import protect_state_write
from app.db.session import session_scope
from app.db.tables import CockpitApp
from app.services.app_catalog import get_app, present_app, read_data, write_data
from app.services.jarvis_devices import require_jarvis_auth
from app.services.app_capabilities import authorize_storage, StorageGrant, assert_grant

router = APIRouter(prefix="/api/app-catalog", tags=["app-catalog"], dependencies=[Depends(require_jarvis_auth)])
data_router = APIRouter(prefix="/api/app-catalog", tags=["app-data"], dependencies=[Depends(authorize_storage)])


@router.get("")
async def list_catalog():
    async with session_scope() as session:
        apps = (await session.execute(select(CockpitApp).where(CockpitApp.enabled.is_(True)).order_by(CockpitApp.kind, CockpitApp.title, CockpitApp.id))).scalars().all()
        return [await present_app(session, app) for app in apps]


@router.get("/{slug}")
async def app_detail(slug: str):
    async with session_scope() as session:
        return await present_app(session, await get_app(session, slug))


class DataWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: dict
    expected_revision: StrictInt = Field(ge=0)


@data_router.get("/{slug}/data/{namespace}/{key:path}")
async def get_data(slug: str, namespace: str, key: str, grant: StorageGrant | None = Depends(authorize_storage)):
    async with session_scope() as session:
        app = await get_app(session, slug)
        assert_grant(app, grant)
        return await read_data(session, app=app, namespace=namespace, key=key)


@data_router.put("/{slug}/data/{namespace}/{key:path}", dependencies=[Depends(protect_state_write)])
async def put_data(slug: str, namespace: str, key: str, body: DataWrite, grant: StorageGrant | None = Depends(authorize_storage)):
    async with session_scope() as session:
        return await write_data(session, slug=slug, namespace=namespace, key=key, value=body.value,
            expected_revision=body.expected_revision, grant=grant)
