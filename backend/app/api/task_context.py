"""Paired-device screenshot status and run-bound context delivery."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from app.db.tables import JarvisDevice, TaskCaptureReport
from app.db.session import session_scope
from app.models.jarvis import JarvisCaptureStatusRequest, JarvisCaptureStatusView
from app.services.jarvis_devices import require_paired_device
from app.services.auth import require_auth
from app.services.task_capture import task_capture_service
from app.services.task_context_upload import MAX_JPEG_BYTES, task_context_upload_service


router = APIRouter(prefix="/api/jarvis", tags=["jarvis-capture"])


@router.get("/captures/{capture_id}", dependencies=[Depends(require_auth)])
async def capture_diagnostic(capture_id: str) -> dict:
    async with session_scope() as session:
        row = await session.get(TaskCaptureReport, capture_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Capture not found")
        return {
            "capture_id": row.capture_id, "device_id": row.device_id,
            "activation_id": row.activation_id, "capture_mode": row.capture_mode,
            "status": row.status, "revision": row.revision,
            "session_id": row.session_id, "generation": row.generation,
            "reason": row.reason, "captured_at": row.captured_at,
            "expires_at": row.expires_at,
        }


@router.put("/captures/{capture_id}/status", response_model=JarvisCaptureStatusView)
async def report_capture_status(
    capture_id: str, report: JarvisCaptureStatusRequest,
    device: JarvisDevice = Depends(require_paired_device),
) -> JarvisCaptureStatusView:
    try:
        row = await task_capture_service.report(
            device_id=device.id, capture_id=capture_id,
            activation_id=report.activation_id, capture_mode=report.capture_mode,
            status=report.status, revision=report.revision,
            expires_at=report.expires_at, captured_at=report.captured_at,
            reason=report.reason, callback_monotonic_ms=report.callback_monotonic_ms,
            session_id=report.session_id, generation=report.generation,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JarvisCaptureStatusView.model_validate(row, from_attributes=True)


@router.get("/context-requests/pending")
async def pending_context_requests(
    device: JarvisDevice = Depends(require_paired_device),
) -> dict:
    return {"requests": await task_context_upload_service.pending(device_id=device.id)}


@router.put("/context-requests/{request_id}/content")
async def upload_context_content(
    request_id: str, request: Request,
    x_upload_token: str = Header(),
    x_content_sha256: str = Header(),
    x_capture_id: str = Header(),
    x_activation_id: str = Header(),
    x_generation: int = Header(),
    x_captured_at: datetime = Header(),
    x_capture_monotonic_ms: float = Header(),
    device: JarvisDevice = Depends(require_paired_device),
) -> dict:
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "image/jpeg":
        raise HTTPException(status_code=415, detail="Only image/jpeg is accepted")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_JPEG_BYTES:
            raise HTTPException(status_code=413, detail="JPEG exceeds the five MiB upload limit")
        chunks.append(chunk)
    try:
        return await task_context_upload_service.accept(
            request_id=request_id, device_id=device.id, token=x_upload_token,
            content=b"".join(chunks), content_sha256=x_content_sha256,
            capture_id=x_capture_id, activation_id=x_activation_id,
            generation=x_generation, captured_at=x_captured_at,
            capture_monotonic_ms=x_capture_monotonic_ms,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class ContextUnavailableRequest(BaseModel):
    reason: str


@router.post("/context-requests/{request_id}/unavailable")
async def context_unavailable(
    request_id: str, report: ContextUnavailableRequest,
    device: JarvisDevice = Depends(require_paired_device),
) -> dict:
    try:
        return await task_context_upload_service.fail(
            request_id=request_id, device_id=device.id, reason=report.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
