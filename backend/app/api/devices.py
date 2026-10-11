"""Android pairing, revocation, and continuity state."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from urllib.parse import urlencode
from app.config import settings

from app.db.session import session_scope
from app.db.tables import JarvisDevice, JarvisLiveEvent, LiveSession
from app.models.jarvis import DevicePairRequest, DevicePairResponse, DeviceStatePatch, PairingCodeView
from app.services.auth import require_auth, create_device_session, SESSION_COOKIE_NAME, SESSION_MAX_AGE
from app.services.jarvis_devices import jarvis_device_service, require_jarvis_auth, require_paired_device


router = APIRouter(prefix="/api/jarvis/devices", tags=["jarvis-devices"])


@router.post("/pairing-code", response_model=PairingCodeView, dependencies=[Depends(require_auth)])
async def create_pairing_code(request: Request, response: Response) -> PairingCodeView:
    import qrcode
    from qrcode.image.svg import SvgPathImage
    value = await jarvis_device_service.create_pairing_code()
    server = settings.jarvis_public_url.rstrip("/")
    # Never derive a phone address from untrusted forwarded/Host headers.
    link = f"{server}/link-phone#{urlencode({'server': server, 'code': value.code})}" if server else ""
    qr = qrcode.make(link, image_factory=SvgPathImage).to_string().decode() if link else ""
    response.headers["Cache-Control"] = "private, no-store"
    return PairingCodeView(code=value.code, expires_at=value.expires_at, server_url=server, link=link, qr_svg=qr)


@router.get("", dependencies=[Depends(require_auth)])
async def list_devices():
    async with session_scope() as session:
        rows = (await session.execute(select(JarvisDevice).where(JarvisDevice.revoked_at.is_(None)))).scalars().all()
        return [{"id": row.id, "label": row.label, "last_seen_at": row.last_seen_at} for row in rows]


@router.get("/android.apk", include_in_schema=False)
async def android_build():
    from fastapi.responses import FileResponse
    path = settings.backend_root.parent / "android/app/build/outputs/apk/debug/app-debug.apk"
    if not path.is_file():
        raise HTTPException(status_code=503, detail="Android build is not prepared yet")
    return FileResponse(path, media_type="application/vnd.android.package-archive", filename="Jarvis.apk")


@router.post("/web-session")
async def device_web_session(response: Response, device: JarvisDevice = Depends(require_paired_device)):
    response.set_cookie(SESSION_COOKIE_NAME, create_device_session(device.id), max_age=SESSION_MAX_AGE,
                        httponly=True, samesite="lax", secure=settings.session_cookie_secure)
    response.headers["Cache-Control"] = "private, no-store"
    return {"status": "ok"}


@router.post("/unlink")
async def unlink_self(device: JarvisDevice = Depends(require_paired_device)):
    return await revoke_device(device.id)


@router.post("/pair", response_model=DevicePairResponse)
async def pair_device(request: DevicePairRequest) -> DevicePairResponse:
    try:
        device, credential = await jarvis_device_service.pair(code=request.code, label=request.label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DevicePairResponse(
        device_id=device.id,
        credential=credential,
        event_cursor=device.event_cursor,
    )


@router.patch("/{device_id}", dependencies=[Depends(require_jarvis_auth)])
async def patch_device(device_id: str, request: DeviceStatePatch, http_request: Request) -> dict[str, object]:
    authenticated_device = await jarvis_device_service.authenticate(http_request)
    if authenticated_device is not None and authenticated_device.id != device_id:
        raise HTTPException(status_code=403, detail="A device may update only its own state")
    async with session_scope() as session:
        device = await session.get(JarvisDevice, device_id)
        if device is None or device.revoked_at is not None:
            raise HTTPException(status_code=404, detail="Device not found")
        values = request.model_dump(exclude_unset=True)
        if values.get("lock_state") is not None:
            device.lock_state = str(values["lock_state"])
        if values.get("event_cursor") is not None:
            device.event_cursor = int(values["event_cursor"])
        if values.get("privacy") is not None:
            device.privacy_json = dict(values["privacy"] or {})
        await session.flush()
        return {"id": device.id, "lock_state": device.lock_state, "event_cursor": device.event_cursor}


@router.delete("/{device_id}", dependencies=[Depends(require_auth)])
async def revoke_device(device_id: str) -> dict[str, bool]:
    from app.services.jarvis_devices import utc_now

    async with session_scope() as session:
        device = await session.get(JarvisDevice, device_id)
        if device is None:
            raise HTTPException(status_code=404, detail="Device not found")
        if device.revoked_at is not None:
            return {"revoked": True}
        device.revoked_at = utc_now()
        session.add(JarvisLiveEvent(event_type="device.unlinked", source_domain="device", source_id=device_id,
            target_device_id=device_id, idempotency_key=f"device-unlinked:{device_id}", payload_json={"device_id": device_id}))
        calls = list((await session.execute(select(LiveSession).where(LiveSession.controller_id == device_id,
            LiveSession.lifecycle_state.in_(("connecting", "ready"))))).scalars().all())
    from app.services.jarvis_coordinator import jarvis_coordinator
    for call in calls:
        try:
            await jarvis_coordinator.supersede_session(call.id, generation=call.generation, reason="device_unlinked")
        except ValueError:
            pass  # A concurrently ended/replaced generation already released its controller.
    return {"revoked": True}
