"""HTTP surface of the app platform: management, app data and UI hosting."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import app_platform
from app.config import settings
from app.services.auth import authorization_scheme, require_auth
from app.services.jarvis_devices import require_jarvis_auth

router = APIRouter(prefix="/api/app-platform", tags=["app-platform"])
host_router = APIRouter(prefix="/api/app-host", tags=["app-platform"])

HOST_HEADERS = {
    "Content-Security-Policy": app_platform.HOST_CSP,
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, max-age=3600",
}


class InstallRequest(BaseModel):
    source: str = Field(min_length=1, max_length=255, description="Package folder name inside app_builds_dir")


class ResetRequest(BaseModel):
    collection: str | None = Field(default=None, max_length=48)
    files: bool = Field(default=False, description="Also delete the app's stored files")


class ErrorReport(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    detail: str = Field(default="", max_length=4000)


class ViewReport(BaseModel):
    collection: str = Field(min_length=1, max_length=48)
    key: str = Field(min_length=1, max_length=128)


class PutRecordRequest(BaseModel):
    value: Any
    expected_revision: int | None = Field(default=None, ge=0)


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(404, f"Not found: {exc}")


async def app_data_access(slug: str, request: Request) -> None:
    """App tokens reach only their own app; owner and device sessions reach every app."""
    if authorization_scheme(request) == app_platform.TOKEN_SCHEME:
        token = request.headers.get("authorization", "").split(maxsplit=1)[-1]
        token_app = app_platform.verify_token(token)
        if token_app is None:
            raise HTTPException(401, "App token is invalid or expired")
        if token_app != slug:
            raise HTTPException(403, "App token is scoped to another app")
        return
    await require_jarvis_auth(request)


@router.get("/apps", dependencies=[Depends(require_jarvis_auth)])
async def list_installed_apps() -> list[dict[str, Any]]:
    return [app.summary() for app in await app_platform.list_apps()]


@router.post("/apps/install", dependencies=[Depends(require_auth)])
async def install_app(body: InstallRequest) -> dict[str, Any]:
    builds = settings.app_builds_path.resolve()
    source = (builds / body.source).resolve()
    if not source.is_relative_to(builds) or source == builds or not source.is_dir():
        raise HTTPException(404, "Package folder not found in the app builds folder")
    try:
        return (await app_platform.install_package(source)).summary()
    except app_platform.ManifestError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/apps/{slug}/rollback", dependencies=[Depends(require_auth)])
async def rollback_app(slug: str) -> dict[str, Any]:
    try:
        return (await app_platform.rollback(slug)).summary()
    except app_platform.AppNotFound as exc:
        raise _not_found(exc) from exc
    except app_platform.ManifestError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/apps/{slug}/status", dependencies=[Depends(require_jarvis_auth)])
async def app_status(slug: str) -> dict[str, Any]:
    try:
        return await app_platform.status(slug)
    except app_platform.AppNotFound as exc:
        raise _not_found(exc) from exc


@router.post("/apps/{slug}/reset", dependencies=[Depends(require_auth)])
async def reset_app(slug: str, body: ResetRequest) -> dict[str, int]:
    try:
        return await app_platform.reset(slug, body.collection, files=body.files)
    except app_platform.AppNotFound as exc:
        raise _not_found(exc) from exc
    except app_platform.ManifestError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/apps/{slug}", dependencies=[Depends(require_auth)])
async def uninstall_app(slug: str) -> dict[str, bool]:
    try:
        await app_platform.uninstall(slug)
    except app_platform.AppNotFound as exc:
        raise _not_found(exc) from exc
    return {"uninstalled": True}


@router.post("/apps/{slug}/errors", dependencies=[Depends(app_data_access)], status_code=204)
async def report_app_error(slug: str, body: ErrorReport) -> None:
    try:
        await app_platform.record_error(slug, body.message, body.detail)
    except app_platform.AppNotFound as exc:
        raise _not_found(exc) from exc


@router.post("/apps/{slug}/views", dependencies=[Depends(app_data_access)], status_code=204)
async def report_app_view(slug: str, body: ViewReport) -> None:
    """The app is showing this record; Jarvis resolves "this one" to the latest view."""
    try:
        await app_platform.record_view(slug, body.collection, body.key)
    except app_platform.AppNotFound as exc:
        raise _not_found(exc) from exc
    except app_platform.RecordError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/apps/{slug}/session", dependencies=[Depends(require_jarvis_auth)])
async def open_app_session(slug: str) -> dict[str, Any]:
    """What the Cockpit page needs to host an app: its active version, entry and a scoped token."""
    try:
        app = await app_platform.get_active(slug)
    except app_platform.AppNotFound as exc:
        raise _not_found(exc) from exc
    entry = app.manifest["ui"]["entry"].removeprefix("ui/")
    return {
        **app.summary(),
        "token": app_platform.issue_token(slug),
        "frame_url": f"/api/app-host/{slug}/{app.version}/{entry}",
    }


def _record_error(exc: app_platform.RecordError) -> HTTPException:
    return HTTPException(409 if isinstance(exc, app_platform.RevisionConflict) else 422, str(exc))


@router.get("/data/{slug}/{collection}", dependencies=[Depends(app_data_access)])
async def query_app_records(
    slug: str,
    collection: str,
    where: str | None = Query(default=None, max_length=2000, description="JSON object of field equality filters"),
    order: str | None = Query(default=None, max_length=80),
    # More than 200 returns the first 200, as in the package check and functions.
    limit: int = Query(default=50, ge=1),
) -> dict[str, Any]:
    try:
        filters = json.loads(where) if where else None
        if filters is not None and not isinstance(filters, dict):
            raise app_platform.RecordError("where must be a JSON object")
        records = await app_platform.query_records(slug, collection, where=filters, order=order, limit=limit)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "where must be valid JSON") from exc
    except app_platform.AppNotFound as exc:
        raise _not_found(exc) from exc
    except app_platform.RecordError as exc:
        raise _record_error(exc) from exc
    return {"records": records}


@router.get("/data/{slug}/{collection}/{key}", dependencies=[Depends(app_data_access)])
async def get_app_record(slug: str, collection: str, key: str) -> dict[str, Any]:
    try:
        record = await app_platform.get_record(slug, collection, key)
    except app_platform.AppNotFound as exc:
        raise _not_found(exc) from exc
    except app_platform.RecordError as exc:
        raise _record_error(exc) from exc
    if record is None:
        raise HTTPException(404, "Record not found")
    return record


@router.put("/data/{slug}/{collection}/{key}", dependencies=[Depends(app_data_access)])
async def put_app_record(slug: str, collection: str, key: str, body: PutRecordRequest) -> dict[str, Any]:
    try:
        return await app_platform.put_record(slug, collection, key, body.value,
                                             expected_revision=body.expected_revision)
    except app_platform.AppNotFound as exc:
        raise _not_found(exc) from exc
    except app_platform.RecordError as exc:
        raise _record_error(exc) from exc


@router.delete("/data/{slug}/{collection}/{key}", dependencies=[Depends(app_data_access)])
async def delete_app_record(slug: str, collection: str, key: str,
                            expected_revision: int | None = Query(default=None, ge=1)) -> dict[str, bool]:
    try:
        deleted = await app_platform.delete_record(slug, collection, key, expected_revision=expected_revision)
    except app_platform.AppNotFound as exc:
        raise _not_found(exc) from exc
    except app_platform.RecordError as exc:
        raise _record_error(exc) from exc
    return {"deleted": deleted}


class CallRequest(BaseModel):
    input: Any = None


@router.post("/functions/{slug}/{name}", dependencies=[Depends(app_data_access)])
async def call_app_function(slug: str, name: str, body: CallRequest, request: Request) -> dict[str, Any]:
    """Run a function. An app's own UI may call only its functions with an http trigger."""
    try:
        app = await app_platform.get_active(slug)
        spec = (app.manifest.get("functions") or {}).get(name)
        if spec is None:
            raise HTTPException(404, f"{app.title} has no function {name}")
        if (authorization_scheme(request) == app_platform.TOKEN_SCHEME
                and "http" not in (spec.get("trigger") or {})):
            raise HTTPException(403, f"Function {name} has no http trigger, so the app's UI cannot call it")
        return (await app_platform.run_function(slug, name, body.input, trigger="call")).view()
    except app_platform.AppNotFound as exc:
        raise _not_found(exc) from exc


FILE_HEADERS = {
    # Stored files are the app's data, never pages of Cockpit's own origin.
    "Content-Security-Policy": "sandbox",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, no-store",
}


def _file_error(exc: Exception) -> HTTPException:
    return HTTPException(422, str(exc))


@router.get("/files/{slug}", dependencies=[Depends(app_data_access)])
async def list_app_files(slug: str, prefix: str | None = Query(default=None, max_length=200)) -> dict[str, Any]:
    try:
        return {"files": await app_platform.list_files(slug, prefix)}
    except app_platform.AppNotFound as exc:
        raise _not_found(exc) from exc
    except app_platform.FileError as exc:
        raise _file_error(exc) from exc


@router.put("/files/{slug}/{name:path}", dependencies=[Depends(app_data_access)])
async def put_app_file(slug: str, name: str, request: Request) -> dict[str, Any]:
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > app_platform.files.FILE_LIMIT_BYTES:
            raise HTTPException(413, "Files are at most 25 MiB")
    try:
        return await app_platform.put_file(slug, name, bytes(data), request.headers.get("content-type"))
    except app_platform.AppNotFound as exc:
        raise _not_found(exc) from exc
    except app_platform.FileError as exc:
        raise _file_error(exc) from exc


@router.get("/files/{slug}/{name:path}", dependencies=[Depends(app_data_access)])
async def get_app_file(slug: str, name: str) -> FileResponse:
    try:
        found = await app_platform.get_file(slug, name)
    except app_platform.AppNotFound as exc:
        raise _not_found(exc) from exc
    except app_platform.FileError as exc:
        raise _file_error(exc) from exc
    if found is None:
        raise HTTPException(404, "File not found")
    meta, path = found
    return FileResponse(path, media_type=meta["content_type"], filename=Path(meta["name"]).name,
                        content_disposition_type="attachment", headers=FILE_HEADERS)


@router.delete("/files/{slug}/{name:path}", dependencies=[Depends(app_data_access)])
async def delete_app_file(slug: str, name: str) -> dict[str, bool]:
    try:
        return {"deleted": await app_platform.delete_file(slug, name)}
    except app_platform.AppNotFound as exc:
        raise _not_found(exc) from exc
    except app_platform.FileError as exc:
        raise _file_error(exc) from exc


@host_router.get("/_sdk/cockpit-app.js")
async def app_sdk() -> FileResponse:
    return FileResponse(app_platform.SDK_FILE, media_type="text/javascript", headers={"X-Content-Type-Options": "nosniff",
                                                                          "Cache-Control": "no-cache"})


@host_router.get("/{slug}/{version}/{path:path}")
async def app_ui_file(slug: str, version: int, path: str) -> FileResponse:
    """Static UI files of an installed version. Bundles hold no secrets; data needs a token."""
    try:
        file = await app_platform.package_file(slug, version, path or "index.html")
    except app_platform.AppNotFound as exc:
        raise _not_found(exc) from exc
    return FileResponse(file, headers=HOST_HEADERS)
