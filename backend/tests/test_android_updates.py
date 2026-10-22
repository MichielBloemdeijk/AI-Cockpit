from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.db.tables import JarvisLiveEvent
from app.main import app
from app.services import android_updates


@pytest.fixture
def releases(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(settings, "android_releases_dir", str(tmp_path / "android-releases"))
    return tmp_path


async def pair(transport: ASGITransport, device: AsyncClient) -> None:
    async with AsyncClient(transport=transport, base_url="http://testserver") as admin:
        await admin.post("/api/auth/login", json={"password": ""})
        code = (await admin.post("/api/jarvis/devices/pairing-code")).json()["code"]
    identity = (await device.post("/api/jarvis/devices/pair", json={"code": code, "label": "Pixel"})).json()
    device.headers["Authorization"] = f"Bearer {identity['credential']}"


def apk(directory: Path, content: bytes) -> Path:
    path = directory / f"build-{hashlib.sha256(content).hexdigest()[:8]}.apk"
    path.write_bytes(content)
    return path


@pytest.mark.asyncio
async def test_published_release_is_offered_to_paired_devices_only(releases: Path) -> None:
    transport = ASGITransport(app=app)
    async with (AsyncClient(transport=transport, base_url="http://testserver") as device,
                AsyncClient(transport=transport, base_url="http://testserver") as anonymous):
        await pair(transport, device)
        assert (await device.get("/api/jarvis/devices/android/update")).json() == {"release": None}
        assert (await anonymous.get("/api/jarvis/devices/android.apk")).status_code == 503

        await android_updates.publish(apk(releases, b"v10"), version_code=10, version_name="2026.10.22+abc")
        offered = (await device.get("/api/jarvis/devices/android/update")).json()["release"]
        assert offered["version_code"] == 10 and offered["sha256"] == hashlib.sha256(b"v10").hexdigest()
        download = await device.get(offered["download_path"])
        assert download.content == b"v10"
        assert download.headers["content-type"] == "application/vnd.android.package-archive"

        assert (await anonymous.get("/api/jarvis/devices/android/update")).status_code == 401
        assert (await anonymous.get(offered["download_path"])).status_code == 401
        # The first-install link stays public and always serves the current release.
        assert (await anonymous.get("/api/jarvis/devices/android.apk")).content == b"v10"

    async with session_scope() as session:
        event = (await session.execute(select(JarvisLiveEvent).where(
            JarvisLiveEvent.event_type == android_updates.UPDATE_PUBLISHED_EVENT))).scalar_one()
    assert event.payload_json == {"version_code": 10, "version_name": "2026.10.22+abc"}


@pytest.mark.asyncio
async def test_publishing_requires_increasing_version_and_prunes_old_files(releases: Path) -> None:
    for code in (1, 2, 3, 4):
        await android_updates.publish(apk(releases, f"v{code}".encode()), version_code=code, version_name=str(code))
    with pytest.raises(ValueError, match="must exceed"):
        await android_updates.publish(apk(releases, b"again"), version_code=4, version_name="4")

    kept = sorted(path.name for path in settings.android_releases_path.glob("jarvis-*.apk"))
    assert kept == ["jarvis-2.apk", "jarvis-3.apk", "jarvis-4.apk"]
    assert android_updates.release_file(4) is not None
    # Superseded releases are never offered, even while still on disk.
    assert android_updates.release_file(3) is None
