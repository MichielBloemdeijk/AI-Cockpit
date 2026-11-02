"""Short-lived Android pairing and revocable device credentials."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from uuid import uuid4

from fastapi import HTTPException, Request, status
from sqlalchemy import func, select, update

from app.config import settings
from app.db.session import session_scope
from app.db.tables import JarvisDevice, JarvisLiveEvent, JarvisPairingCode
from app.services.auth import SESSION_COOKIE_NAME, verify_session_token, authorization_scheme, device_session_id


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(value: str) -> str:
    return hashlib.sha256(f"{settings.session_secret_key}:jarvis-device:{value}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PairingCode:
    code: str
    expires_at: datetime


class JarvisDeviceService:
    async def create_pairing_code(self) -> PairingCode:
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = utc_now() + timedelta(minutes=5)
        async with session_scope() as session:
            session.add(JarvisPairingCode(
                id=str(uuid4()), code_hash=_hash(code), expires_at=expires_at
            ))
        return PairingCode(code=code, expires_at=expires_at)

    async def pair(self, *, code: str, label: str) -> tuple[JarvisDevice, str]:
        now = utc_now()
        credential = secrets.token_urlsafe(32)
        async with session_scope() as session:
            consumed = (await session.execute(update(JarvisPairingCode)
                .where(JarvisPairingCode.code_hash == _hash(code),
                       JarvisPairingCode.consumed_at.is_(None), JarvisPairingCode.expires_at > now)
                .values(consumed_at=now).returning(JarvisPairingCode.id))).scalar_one_or_none()
            if consumed is None:
                raise ValueError("Link is invalid, expired or already used. Generate a new Link phone code.")
            latest_event_cursor = int((await session.execute(
                select(func.max(JarvisLiveEvent.sequence))
            )).scalar_one_or_none() or 0)
            device = JarvisDevice(
                id=str(uuid4()),
                label=" ".join(label.split()).strip(),
                credential_hash=_hash(credential),
                platform="android",
                event_cursor=latest_event_cursor,
                last_seen_at=now,
            )
            session.add(device)
            await session.flush()
            return device, credential

    async def authenticate(self, request: Request, *, bearer_only: bool = False) -> JarvisDevice | None:
        authorization = request.headers.get("authorization", "")
        credential = authorization.split(" ", 1)[1].strip() if authorization.lower().startswith("bearer ") else ""
        device_id = device_session_id(request.cookies.get(SESSION_COOKIE_NAME, "")) if not authorization and not bearer_only else None
        if not credential and not device_id:
            return None
        async with session_scope() as session:
            device = (await session.execute(
                update(JarvisDevice)
                .where(JarvisDevice.credential_hash == _hash(credential) if credential else JarvisDevice.id == device_id)
                .where(JarvisDevice.revoked_at.is_(None))
                .values(last_seen_at=utc_now())
                .returning(JarvisDevice)
            )).scalar_one_or_none()
            # Acquire the writer before reading: WAL read-to-write upgrades can
            # fail immediately when capture/observations commit concurrently.
            return device


jarvis_device_service = JarvisDeviceService()


async def require_jarvis_auth(request: Request) -> None:
    if authorization_scheme(request) == "appcapability":
        raise HTTPException(status_code=401, detail="App capability is not accepted here")
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie and verify_session_token(cookie):
        return
    if await jarvis_device_service.authenticate(request) is not None:
        return
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


async def require_paired_device(request: Request) -> JarvisDevice:
    """Capture bytes and actionable upload tokens are never available to a browser cookie."""
    device = await jarvis_device_service.authenticate(request, bearer_only=True)
    if device is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Paired device required")
    return device


async def require_unlocked_device(request: Request) -> None:
    """Browser sessions are trusted; paired devices must report unlocked state."""
    if verify_session_token(request.cookies.get(SESSION_COOKIE_NAME, "")):
        return
    device = await jarvis_device_service.authenticate(request)
    if device is not None and device.lock_state == "locked":
        raise HTTPException(status_code=423, detail="This action is unavailable while the device is locked")


async def require_task_access(request: Request) -> None:
    """Phones may view/continue/control tasks, never administer the worker pool."""
    await require_jarvis_auth(request)
    if verify_session_token(request.cookies.get(SESSION_COOKIE_NAME, "")):
        return
    if request.method == "GET":
        return
    await require_unlocked_device(request)
    import re
    path = request.url.path.removeprefix("/api/tasks")
    if request.method != "POST" or not (path == "" or re.fullmatch(r"/[0-9a-fA-F-]{36}/(turns|control|cancel|retry|questions/[0-9a-fA-F-]{36}/answer)", path)):
        raise HTTPException(status_code=403, detail="Owner session required for worker administration")
    body = await request.json()
    if body.get("execution_config"):
        raise HTTPException(status_code=403, detail="Owner session required for tool overrides")
