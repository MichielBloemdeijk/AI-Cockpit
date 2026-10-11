"""Storage-only credentials, never user sessions, DB credentials or result links."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import secrets

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.db.tables import CockpitApp
from app.services.jarvis_devices import require_jarvis_auth
from app.services.auth import authorization_scheme

CAPABILITY_TTL = 15 * 60
SCHEME = "AppCapability"


@dataclass(frozen=True)
class StorageGrant:
    app_id: str
    generation: int


def assert_grant(app: CockpitApp, grant: StorageGrant | None):
    if grant is not None and (app.id != grant.app_id or app.capability_revision != grant.generation):
        raise HTTPException(403, "App capability was revoked")


def serializer():
    # Separate signing purpose prevents a storage token becoming a session.
    return URLSafeTimedSerializer(settings.session_secret_key, salt="cockpit-app-storage.v1")


def issue_capability(app: CockpitApp, *, read_only: bool = False) -> dict:
    if app.kind != "generated" or not app.enabled:
        raise HTTPException(404, "Generated app not available")
    methods = ["GET"] if read_only else ["GET", "PUT"]
    token = serializer().dumps({"app_id": app.id, "generation": app.capability_revision,
        "namespace": "state", "methods": methods, "nonce": secrets.token_hex(16)})
    return {"credential": token, "scheme": SCHEME, "app_slug": app.slug,
        "namespace": "state", "methods": methods,
        "expires_at": datetime.now(timezone.utc) + timedelta(seconds=CAPABILITY_TTL)}


async def authorize_storage(request: Request, slug: str, namespace: str) -> StorageGrant | None:
    authorization = request.headers.get("authorization", "")
    if authorization_scheme(request) != "appcapability":
        await require_jarvis_auth(request)
        return
    parts = authorization.split(maxsplit=1)
    token = parts[1].strip() if len(parts) == 2 else ""
    if not token or len(token) > 4096:
        raise HTTPException(401, "Invalid app capability")
    try:
        claims = serializer().loads(token, max_age=CAPABILITY_TTL)
    except BadSignature:
        raise HTTPException(401, "Invalid or expired app capability") from None
    if not isinstance(claims, dict):
        raise HTTPException(401, "Invalid app capability")
    async with session_scope() as session:
        app = (await session.execute(select(CockpitApp).where(CockpitApp.slug == slug,
            CockpitApp.enabled.is_(True), CockpitApp.kind == "generated"))).scalar_one_or_none()
        if (app is None or claims.get("app_id") != app.id
                or claims.get("generation") != app.capability_revision
                or namespace != "state" or claims.get("namespace") != namespace
                or request.method not in claims.get("methods", [])):
            raise HTTPException(403, "App capability does not authorize this request")
    # Generated capabilities are app-level; results belong to maintained apps.
    if request.query_params.get("result_public_id"):
        raise HTTPException(403, "App capability cannot access result-scoped state")
    return StorageGrant(app.id, app.capability_revision)
