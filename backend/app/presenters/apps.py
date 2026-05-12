from __future__ import annotations

from app.models.apps import GeneratedAppDetail, GeneratedAppSummary
from app.services.app_registry import app_registry_service, resolve_generated_app_contract


def present_generated_app_summary(record) -> GeneratedAppSummary:
    return GeneratedAppSummary(
        id=record.id,
        slug=record.slug,
        title=record.title,
        description=record.description,
        status=record.status,
        route_path=record.route_path,
        verification_status=record.verification_status,
        source_task_run_id=record.source_task_run_id,
        source_conversation_id=record.source_conversation_id,
        lease_task_run_id=record.lease_task_run_id,
        lease_conversation_id=record.lease_conversation_id,
        lease_acquired_at=record.lease_acquired_at,
        updated_at=record.updated_at,
        created_at=record.created_at,
    )


def present_generated_app_detail(record) -> GeneratedAppDetail:
    contract = resolve_generated_app_contract(record.slug, record.manifest_json)
    return GeneratedAppDetail(
        **present_generated_app_summary(record).model_dump(),
        frontend_root=contract.frontend_root,
        frontend_entry_path=contract.frontend_entry_path,
        icon_asset_path=record.icon_asset_path,
        cover_asset_path=record.cover_asset_path,
        manifest_json=record.manifest_json,
        last_error=app_registry_service.get_display_error(record),
        allowed_write_roots=contract.allowed_write_roots,
    )