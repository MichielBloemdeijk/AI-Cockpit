"""APIs for the generated frontend app registry."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.models.apps import (
    GeneratedAppCreateRequest,
    GeneratedAppDetail,
    GeneratedAppSummary,
    GeneratedAppUpdateRequest,
    AppStorageCapabilityRequest,
)
from app.presenters.apps import present_generated_app_detail, present_generated_app_summary
from app.services.app_registry import GeneratedAppRecord, app_registry_service
from app.services.auth import require_auth
from app.api.results import protect_state_write

router = APIRouter(prefix="/api/apps", tags=["apps"], dependencies=[Depends(require_auth)])


@router.get("", response_model=list[GeneratedAppSummary], include_in_schema=False)
@router.get("/", response_model=list[GeneratedAppSummary])
async def list_apps():
    records = await app_registry_service.list_apps()
    return [present_generated_app_summary(record) for record in records]


@router.post("", response_model=GeneratedAppDetail, status_code=status.HTTP_201_CREATED, include_in_schema=False)
@router.post("/", response_model=GeneratedAppDetail, status_code=status.HTTP_201_CREATED)
async def create_app(req: GeneratedAppCreateRequest):
    try:
        record = await app_registry_service.create_app(
            title=req.title,
            slug=req.slug,
            description=req.description,
            status=req.status,
            verification_status=req.verification_status,
            source_task_run_id=req.source_task_run_id,
            source_conversation_id=req.source_conversation_id,
            manifest_json=req.manifest_json,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return present_generated_app_detail(record)


@router.get("/slug/{slug}", response_model=GeneratedAppDetail)
async def get_app_by_slug(slug: str):
    record = await app_registry_service.get_app_by_slug(slug)
    if record is None:
        raise HTTPException(status_code=404, detail="App not found")
    return present_generated_app_detail(record)


@router.get("/{app_id}", response_model=GeneratedAppDetail)
async def get_app(app_id: str):
    record = await app_registry_service.get_app(app_id)
    if record is None:
        raise HTTPException(status_code=404, detail="App not found")
    return present_generated_app_detail(record)


@router.patch("/{app_id}", response_model=GeneratedAppDetail)
async def update_app(app_id: str, req: GeneratedAppUpdateRequest):
    record = await app_registry_service.update_app(app_id, **req.model_dump(exclude_unset=True))
    if record is None:
        raise HTTPException(status_code=404, detail="App not found")
    return present_generated_app_detail(record)


@router.post("/{app_id}/storage-capability", dependencies=[Depends(protect_state_write)])
async def generated_app_storage(app_id: str, body: AppStorageCapabilityRequest):
    """Resolve by generated identity, including legacy/catalog slug collisions."""
    from sqlalchemy import select
    from app.db.session import session_scope
    from app.db.tables import CockpitApp, GeneratedApp
    from app.services.app_capabilities import issue_capability
    async with session_scope() as session:
        catalog = (await session.execute(select(CockpitApp).join(GeneratedApp,
            GeneratedApp.cockpit_app_id == CockpitApp.id).where(GeneratedApp.id == app_id,
            CockpitApp.enabled.is_(True)))).scalar_one_or_none()
        if catalog is None:
            raise HTTPException(404, "Generated app not available")
        return issue_capability(catalog, read_only=body.read_only)
