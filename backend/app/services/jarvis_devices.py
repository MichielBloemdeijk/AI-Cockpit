"""Short-lived Android pairing and revocable device credentials."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from uuid import uuid4

from fastapi import HTTPException, Request, status
from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.db.tables import JarvisDevice, JarvisPairingCode
from app.services.auth import SESSION_COOKIE_NAME, verify_session_token


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
            row = (await session.execute(
                select(JarvisPairingCode).where(JarvisPairingCode.code_hash == _hash(code)).limit(1)
            )).scalar_one_or_none()
            if row is None or row.consumed_at is not None:
                raise ValueError("Pairing code is invalid")
            expiry = row.expires_at if row.expires_at.tzinfo is not None else row.expires_at.replace(tzinfo=timezone.utc)
            if expiry <= now:
                raise ValueError("Pairing code has expired")
            row.consumed_at = now
            device = JarvisDevice(
                id=str(uuid4()),
                label=" ".join(label.split()).strip(),
                credential_hash=_hash(credential),
                platform="android",
                last_seen_at=now,
            )
            session.add(device)
            await session.flush()
            return device, credential

    async def authenticate(self, request: Request) -> JarvisDevice | None:
        authorization = request.headers.get("authorization", "")
        if not authorization.lower().startswith("bearer "):
            return None
        credential = authorization.split(" ", 1)[1].strip()
        if not credential:
            return None
        async with session_scope() as session:
            device = (await session.execute(
                select(JarvisDevice)
                .where(JarvisDevice.credential_hash == _hash(credential))
                .where(JarvisDevice.revoked_at.is_(None))
                .limit(1)
            )).scalar_one_or_none()
            if device is not None:
                device.last_seen_at = utc_now()
            return device


jarvis_device_service = JarvisDeviceService()


async def require_jarvis_auth(request: Request) -> None:
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie and verify_session_token(cookie):
        return
    if await jarvis_device_service.authenticate(request) is not None:
        return
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


async def require_unlocked_device(request: Request) -> None:
    """Browser sessions are trusted; paired devices must report unlocked state."""
    if request.cookies.get(SESSION_COOKIE_NAME):
        return
    device = await jarvis_device_service.authenticate(request)
    if device is not None and device.lock_state == "locked":
        raise HTTPException(status_code=423, detail="This action is unavailable while the device is locked")
