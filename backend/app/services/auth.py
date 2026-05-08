"""Simple session-cookie auth with bcrypt-hashed password."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Cookie, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.hash import bcrypt

from app.config import settings

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "cockpit_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret_key)


def verify_password(plain: str) -> bool:
    """Verify a plain-text password against the stored hash."""
    if not settings.auth_password_hash:
        # No password configured → allow all (dev mode)
        logger.warning("No AUTH_PASSWORD_HASH set — auth disabled (dev mode)")
        return True
    try:
        return bcrypt.verify(plain, settings.auth_password_hash)
    except Exception:
        return False


def create_session_token() -> str:
    """Create a signed session token."""
    return _serializer().dumps({"authenticated": True})


def verify_session_token(token: str) -> bool:
    """Verify a session token. Returns True if valid."""
    try:
        data = _serializer().loads(token, max_age=SESSION_MAX_AGE)
        return data.get("authenticated") is True
    except (BadSignature, SignatureExpired):
        return False


def require_auth(request: Request) -> None:
    """FastAPI dependency: raises 401 if not authenticated."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token or not verify_session_token(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
