"""Short-lived capability tokens that scope a request to one installed app.

An app's UI never holds the owner's session; the Cockpit page mints a token for
that app and attaches it to the data requests it makes on the app's behalf.
"""
from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings

TOKEN_MAX_AGE_SECONDS = 15 * 60
SCHEME = "appplatform"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret_key, salt="cockpit-app-platform.v1")


def issue_token(slug: str) -> str:
    return _serializer().dumps({"app": slug})


def verify_token(token: str) -> str | None:
    """The app slug the token was issued for, or None when invalid or expired."""
    try:
        data = _serializer().loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    slug = data.get("app") if isinstance(data, dict) else None
    return slug if isinstance(slug, str) and slug else None
