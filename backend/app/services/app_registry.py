"""Durable registry and contract helpers for generated frontend apps."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re

from sqlalchemy import select, update

from app.config import settings
from app.db.session import session_scope
from app.db.tables import GeneratedApp, GeneratedAppStatus
from app.services.conversation_store import conversation_store


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized or "app"


def _metadata_app_payload(metadata: dict[str, object]) -> dict[str, object] | None:
    app_context = metadata.get("app_context") if isinstance(metadata.get("app_context"), dict) else {}
    app = app_context.get("app") if isinstance(app_context, dict) else None
    if isinstance(app, dict):
        return app
    payload = metadata.get("payload") if isinstance(metadata.get("payload"), dict) else {}
    app = payload.get("app") if isinstance(payload, dict) else None
    return app if isinstance(app, dict) else None


@dataclass(slots=True)
class GeneratedAppRecord:
    id: str
    slug: str
    title: str
    description: str | None
    status: str
    route_path: str
    frontend_root: str
    frontend_entry_path: str | None
    icon_asset_path: str | None
    cover_asset_path: str | None
    verification_status: str | None
    source_task_run_id: str | None
    source_conversation_id: str | None
    lease_task_run_id: str | None
    lease_conversation_id: str | None
    lease_acquired_at: datetime | None
    manifest_json: dict | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class AppLeaseError(ValueError):
    pass


class AppLeaseConflictError(AppLeaseError):
    def __init__(self, *, title: str, slug: str, lease_task_run_id: str, lease_conversation_id: str | None) -> None:
        self.slug = slug
        self.lease_task_run_id = lease_task_run_id
        self.lease_conversation_id = lease_conversation_id
        detail = (
            f"App '{title}' ({slug}) is currently leased by task {lease_task_run_id}"
            + (f" in conversation {lease_conversation_id}" if lease_conversation_id else "")
            + "."
        )
        super().__init__(detail)


@dataclass(slots=True)
class GeneratedAppContract:
    route_path: str
    frontend_root: str
    frontend_entry_path: str
    frontend_layout_path: str
    manifest_path: str
    asset_root: str
    allowed_write_roots: list[str]


def _normalize_route_path_prefix(value: str | None) -> str:
    normalized = str(value or "").strip().strip("/")
    if not normalized:
        return "/apps"
    return f"/{normalized}"


def _normalize_contract_override(contract_override: object) -> dict[str, str] | None:
    if not isinstance(contract_override, dict):
        return None

    frontend_root_base = str(contract_override.get("frontend_root_base") or "").strip().replace("\\", "/")
    asset_root_base = str(contract_override.get("asset_root_base") or "").strip().replace("\\", "/")
    if not frontend_root_base or not asset_root_base:
        return None

    return {
        "route_path_prefix": _normalize_route_path_prefix(str(contract_override.get("route_path_prefix") or "/apps")),
        "frontend_root_base": frontend_root_base,
        "asset_root_base": asset_root_base,
    }


def _contract_payload(contract: GeneratedAppContract) -> dict[str, object]:
    return {
        "route_path": contract.route_path,
        "frontend_root": contract.frontend_root,
        "frontend_entry_path": contract.frontend_entry_path,
        "frontend_layout_path": contract.frontend_layout_path,
        "manifest_path": contract.manifest_path,
        "asset_root": contract.asset_root,
        "allowed_write_roots": contract.allowed_write_roots,
    }


def _contract_from_payload(payload: object) -> GeneratedAppContract | None:
    contract_payload = payload.get("contract") if isinstance(payload, dict) else None
    if not isinstance(contract_payload, dict):
        return None

    route_path = str(contract_payload.get("route_path") or "").strip()
    frontend_root = str(contract_payload.get("frontend_root") or "").strip()
    frontend_entry_path = str(contract_payload.get("frontend_entry_path") or "").strip()
    frontend_layout_path = str(contract_payload.get("frontend_layout_path") or "").strip()
    manifest_path = str(contract_payload.get("manifest_path") or "").strip()
    asset_root = str(contract_payload.get("asset_root") or "").strip()
    allowed_write_roots = [
        str(value).strip()
        for value in (contract_payload.get("allowed_write_roots") or [])
        if str(value).strip()
    ]
    if not all((route_path, frontend_root, frontend_entry_path, frontend_layout_path, manifest_path, asset_root)):
        return None
    if not allowed_write_roots:
        allowed_write_roots = [frontend_root, asset_root]

    return GeneratedAppContract(
        route_path=route_path,
        frontend_root=frontend_root,
        frontend_entry_path=frontend_entry_path,
        frontend_layout_path=frontend_layout_path,
        manifest_path=manifest_path,
        asset_root=asset_root,
        allowed_write_roots=allowed_write_roots,
    )


def _record(model: GeneratedApp) -> GeneratedAppRecord:
    return GeneratedAppRecord(
        id=model.id,
        slug=model.slug,
        title=model.title,
        description=model.description,
        status=model.status,
        route_path=model.route_path,
        frontend_root=model.frontend_root,
        frontend_entry_path=model.frontend_entry_path,
        icon_asset_path=model.icon_asset_path,
        cover_asset_path=model.cover_asset_path,
        verification_status=model.verification_status,
        source_task_run_id=model.source_task_run_id,
        source_conversation_id=model.source_conversation_id,
        lease_task_run_id=model.lease_task_run_id,
        lease_conversation_id=model.lease_conversation_id,
        lease_acquired_at=model.lease_acquired_at,
        manifest_json=model.manifest_json,
        last_error=model.last_error,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _repo_root() -> Path:
    return settings.backend_root.parent.resolve()


def generated_app_contract(slug: str, *, contract_override: dict[str, str] | None = None) -> GeneratedAppContract:
    normalized_slug = _slugify(slug)
    override = _normalize_contract_override(contract_override)
    route_path_prefix = override["route_path_prefix"] if override is not None else "/apps"
    frontend_root_base = Path(override["frontend_root_base"]) if override is not None else Path("frontend") / "app" / "apps"
    asset_root_base = Path(override["asset_root_base"]) if override is not None else Path("frontend") / "public" / "apps"
    frontend_root = (frontend_root_base / normalized_slug).as_posix()
    frontend_entry_path = (Path(frontend_root) / "page.tsx").as_posix()
    frontend_layout_path = (Path(frontend_root) / "layout.tsx").as_posix()
    manifest_path = (Path(frontend_root) / "cockpit-app.json").as_posix()
    asset_root = (asset_root_base / normalized_slug).as_posix()
    return GeneratedAppContract(
        route_path=f"{route_path_prefix}/{normalized_slug}",
        frontend_root=frontend_root,
        frontend_entry_path=frontend_entry_path,
        frontend_layout_path=frontend_layout_path,
        manifest_path=manifest_path,
        asset_root=asset_root,
        allowed_write_roots=[frontend_root, asset_root],
    )


def resolve_generated_app_contract(
    slug: str,
    manifest_json: dict[str, object] | None = None,
    *,
    contract_override: dict[str, str] | None = None,
) -> GeneratedAppContract:
    stored_contract = _contract_from_payload(manifest_json)
    if stored_contract is not None:
        return stored_contract
    return generated_app_contract(slug, contract_override=contract_override)


class AppRegistryService:
    async def _lease_holder_is_active(
        self,
        *,
        conversation_id: str,
        app_id: str,
        holder_run_id: str | None = None,
    ) -> bool:
        runs = await conversation_store.list_runs(conversation_id)
        if holder_run_id:
            runs = [run for run in runs if run.id == holder_run_id]

        for run in runs:
            run_status = str(run.status or "").strip().lower()
            metadata = dict(run.metadata_json or {})
            if run_status in {"completed", "failed", "cancelled", "interrupted", "paused"}:
                continue
            if run_status:
                is_active = run_status in {"pending", "running"}
            else:
                active_status = str(metadata.get("agent_status") or metadata.get("task_status") or "").strip().lower()
                is_active = active_status in {"pending", "running"}
            if not is_active:
                continue
            app = _metadata_app_payload(metadata)
            if not isinstance(app, dict):
                continue
            if str(app.get("app_id") or "").strip() == app_id:
                return True
        return False

    async def list_apps(self) -> list[GeneratedAppRecord]:
        async with session_scope() as session:
            result = await session.execute(
                select(GeneratedApp).order_by(GeneratedApp.updated_at.desc(), GeneratedApp.created_at.desc())
            )
            return [_record(item) for item in result.scalars().all()]

    async def get_app(self, app_id: str) -> GeneratedAppRecord | None:
        async with session_scope() as session:
            item = await session.get(GeneratedApp, app_id)
            return None if item is None else _record(item)

    async def get_app_by_slug(self, slug: str) -> GeneratedAppRecord | None:
        async with session_scope() as session:
            result = await session.execute(
                select(GeneratedApp).where(GeneratedApp.slug == _slugify(slug)).limit(1)
            )
            item = result.scalar_one_or_none()
            return None if item is None else _record(item)

    async def create_app(
        self,
        *,
        title: str,
        slug: str | None = None,
        description: str | None = None,
        status: str = GeneratedAppStatus.draft.value,
        verification_status: str | None = None,
        source_task_run_id: str | None = None,
        source_conversation_id: str | None = None,
        manifest_json: dict | None = None,
        contract_override: dict[str, str] | None = None,
    ) -> GeneratedAppRecord:
        normalized_slug = _slugify(slug or title)
        if await self.get_app_by_slug(normalized_slug):
            raise ValueError("An app with that slug already exists")

        contract = generated_app_contract(normalized_slug, contract_override=contract_override)
        payload = dict(manifest_json or {})
        payload.setdefault("kind", "frontend_generated_app")
        payload.setdefault("version", 1)
        payload.setdefault("contract", _contract_payload(contract))

        async with session_scope() as session:
            item = GeneratedApp(
                slug=normalized_slug,
                title=title.strip(),
                description=description,
                status=status,
                route_path=contract.route_path,
                frontend_root=contract.frontend_root,
                frontend_entry_path=contract.frontend_entry_path,
                verification_status=verification_status,
                source_task_run_id=source_task_run_id,
                source_conversation_id=source_conversation_id,
                manifest_json=payload,
            )
            session.add(item)
            await session.flush()
            return _record(item)

    async def update_app(self, app_id: str, **changes) -> GeneratedAppRecord | None:
        values = {key: value for key, value in changes.items() if value is not None}
        if not values:
            return await self.get_app(app_id)
        values["updated_at"] = _utc_now()

        async with session_scope() as session:
            result = await session.execute(
                update(GeneratedApp).where(GeneratedApp.id == app_id).values(**values)
            )
            if not result.rowcount:
                return None
            item = await session.get(GeneratedApp, app_id)
            return None if item is None else _record(item)

    async def delete_app(self, app_id: str) -> bool:
        async with session_scope() as session:
            item = await session.get(GeneratedApp, app_id)
            if item is None:
                return False
            await session.delete(item)
            return True

    async def acquire_lease(
        self,
        *,
        app_id: str,
        conversation_id: str,
        holder_run_id: str | None = None,
        task_run_id: str | None = None,
    ) -> GeneratedAppRecord:
        resolved_holder_run_id = holder_run_id or task_run_id
        record = await self.get_app(app_id)
        if record is None:
            raise AppLeaseError("App not found")

        active_holder_conversation = str(record.lease_conversation_id or "").strip()
        active_holder_run = str(record.lease_task_run_id or "").strip()
        active_holder_is_running = (
            active_holder_conversation
            and await self._lease_holder_is_active(
                conversation_id=active_holder_conversation,
                app_id=app_id,
            )
        )
        holder_mismatch = active_holder_conversation and active_holder_conversation != conversation_id
        if active_holder_is_running and holder_mismatch:
            raise AppLeaseConflictError(
                title=record.title,
                slug=record.slug,
                lease_task_run_id=str(record.lease_task_run_id or active_holder_conversation),
                lease_conversation_id=record.lease_conversation_id,
            )

        now = _utc_now()
        async with session_scope() as session:
            await session.execute(
                update(GeneratedApp)
                .where(GeneratedApp.id == app_id)
                .values(
                    lease_task_run_id=resolved_holder_run_id,
                    lease_conversation_id=conversation_id,
                    lease_acquired_at=now,
                    updated_at=now,
                )
            )
            item = await session.get(GeneratedApp, app_id)
            if item is None:
                raise AppLeaseError("App not found")
            return _record(item)

    async def release_lease(
        self,
        *,
        app_id: str,
        conversation_id: str | None = None,
        holder_run_id: str | None = None,
        task_run_id: str | None = None,
    ) -> GeneratedAppRecord | None:
        resolved_holder_run_id = holder_run_id or task_run_id
        record = await self.get_app(app_id)
        if record is None:
            return None
        if conversation_id and record.lease_conversation_id and record.lease_conversation_id != conversation_id:
            return record
        if not conversation_id and resolved_holder_run_id and record.lease_task_run_id and record.lease_task_run_id != resolved_holder_run_id:
            return record

        now = _utc_now()
        async with session_scope() as session:
            await session.execute(
                update(GeneratedApp)
                .where(GeneratedApp.id == app_id)
                .values(
                    lease_task_run_id=None,
                    lease_conversation_id=None,
                    lease_acquired_at=None,
                    updated_at=now,
                )
            )
            item = await session.get(GeneratedApp, app_id)
            return None if item is None else _record(item)

    async def get_recent_app_for_conversation(self, conversation_id: str) -> GeneratedAppRecord | None:
        runs = await conversation_store.list_runs(conversation_id)
        for run in runs:
            metadata = dict(run.metadata_json or {})
            app = _metadata_app_payload(metadata)
            if not isinstance(app, dict):
                continue
            app_id = str(app.get("app_id") or "").strip()
            slug = str(app.get("slug") or "").strip()
            if app_id:
                record = await self.get_app(app_id)
                if record is not None:
                    return record
            if slug:
                record = await self.get_app_by_slug(slug)
                if record is not None:
                    return record
        return None

    def get_allowed_write_roots(self, record: GeneratedAppRecord) -> list[str]:
        return resolve_generated_app_contract(record.slug, record.manifest_json).allowed_write_roots

    def get_absolute_write_roots(self, record: GeneratedAppRecord) -> list[str]:
        return [(_repo_root() / path).resolve().as_posix() for path in self.get_allowed_write_roots(record)]


app_registry_service = AppRegistryService()