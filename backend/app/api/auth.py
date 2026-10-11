"""Auth endpoints: login, logout, status."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from app.config import settings
from app.services.auth import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    create_session_token,
    require_auth,
    verify_password,
    device_session_id,
    verify_session_token,
)
from app.services.jarvis_devices import require_jarvis_auth, jarvis_device_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str


@router.post("/login")
async def login(req: LoginRequest, request: Request, response: Response):
    if (request.headers.get("authorization") or device_session_id(request.cookies.get(SESSION_COOKIE_NAME, ""))
            or "JarvisPhone/" in request.headers.get("user-agent", "")):
        raise HTTPException(status_code=403, detail="Linked phones use their device session. Relink in the Android app if needed.")
    if not verify_password(req.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password")
    token = create_session_token()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
    )
    return {"status": "ok"}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"status": "ok"}


@router.get("/status")
async def auth_status(request: Request, _: None = Depends(require_jarvis_auth)):
    if verify_session_token(request.cookies.get(SESSION_COOKIE_NAME, "")):
        return {"authenticated": True, "scope": "owner", "device": None}
    device = await jarvis_device_service.authenticate(request)
    if device is None:
        raise HTTPException(status_code=401, detail="Phone link is unavailable")
    return {"authenticated": True, "scope": "device", "device": {
        "id": device.id, "label": device.label, "paired": True,
    }}


@router.get("/config")
async def auth_config():
    return {"password_required": bool(settings.auth_password.get_secret_value()) or settings.jarvis_access_mode == "password"}
