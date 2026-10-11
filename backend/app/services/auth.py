"""Signed owner/device sessions and optional private-config password."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Cookie, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
import secrets

from app.config import settings

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "cockpit_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret_key)


def verify_password(plain: str) -> bool:
    """Optional private-config password; password mode fails closed if absent."""
    configured = settings.auth_password.get_secret_value()
    if not configured:
        return settings.jarvis_access_mode == "personal" and plain == ""
    return secrets.compare_digest(plain.encode("utf-8"), configured.encode("utf-8"))


def create_device_session(device_id: str) -> str:
    return _serializer().dumps({"device_id": device_id})


def device_session_id(token: str) -> str | None:
    try:
        data = _serializer().loads(token, max_age=SESSION_MAX_AGE)
        value = data.get("device_id")
        return value if isinstance(value, str) and value else None
    except (BadSignature, SignatureExpired, AttributeError):
        return None


def create_session_token() -> str:
    """Create a signed session token."""
    return _serializer().dumps({"authenticated": True})


def verify_session_token(token: str) -> bool:
    """Verify a session token. Returns True if valid."""
    try:
        data = _serializer().loads(token, max_age=SESSION_MAX_AGE)
        return data.get("authenticated") is True
    except (BadSignature, SignatureExpired, AttributeError):
        return False


def authorization_scheme(request: Request) -> str:
    parts = request.headers.get("authorization", "").split(maxsplit=1)
    return parts[0].casefold() if parts else ""


def require_auth(request: Request) -> None:
    """FastAPI dependency: raises 401 if not authenticated."""
    if authorization_scheme(request) == "appcapability":
        raise HTTPException(status_code=401, detail="App capability is not accepted here")
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token or not verify_session_token(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
