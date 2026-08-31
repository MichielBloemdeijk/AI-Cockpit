"""Android pairing, revocation, and continuity state."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.db.session import session_scope
from app.db.tables import JarvisDevice
from app.models.jarvis import DevicePairRequest, DevicePairResponse, DeviceStatePatch, PairingCodeView
from app.services.auth import require_auth
from app.services.jarvis_devices import jarvis_device_service, require_jarvis_auth


router = APIRouter(prefix="/api/jarvis/devices", tags=["jarvis-devices"])


@router.post("/pairing-code", response_model=PairingCodeView, dependencies=[Depends(require_auth)])
async def create_pairing_code() -> PairingCodeView:
    value = await jarvis_device_service.create_pairing_code()
    return PairingCodeView(code=value.code, expires_at=value.expires_at)


@router.post("/pair", response_model=DevicePairResponse)
async def pair_device(request: DevicePairRequest) -> DevicePairResponse:
    try:
        device, credential = await jarvis_device_service.pair(code=request.code, label=request.label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DevicePairResponse(device_id=device.id, credential=credential)


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
        device.revoked_at = utc_now()
    return {"revoked": True}
