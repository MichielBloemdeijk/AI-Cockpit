from __future__ import annotations

from app.models.apps import GeneratedAppDetail, GeneratedAppSummary
from app.services.app_registry import app_registry_service


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
    return GeneratedAppDetail(
        **present_generated_app_summary(record).model_dump(),
        frontend_root=record.frontend_root,
        frontend_entry_path=record.frontend_entry_path,
        icon_asset_path=record.icon_asset_path,
        cover_asset_path=record.cover_asset_path,
        manifest_json=record.manifest_json,
        last_error=record.last_error,
        allowed_write_roots=app_registry_service.get_allowed_write_roots(record),
    )