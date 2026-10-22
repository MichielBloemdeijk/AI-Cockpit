"""Self-hosted Android release channel for the Jarvis phone app.

Published APKs live in ``settings.android_releases_path`` next to ``latest.json``.
The phone polls the manifest with its device credential and installs newer
builds itself; Android rejects any APK not signed with the installed app's key.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

from app.config import settings
from app.db.session import session_scope
from app.db.tables import JarvisLiveEvent

MANIFEST = "latest.json"
RETAINED_RELEASES = 3
UPDATE_PUBLISHED_EVENT = "android.update_published"


@dataclass(frozen=True)
class AndroidRelease:
    version_code: int
    version_name: str
    sha256: str
    size: int
    file_name: str
    published_at: str


def latest_release() -> AndroidRelease | None:
    path = settings.android_releases_path / MANIFEST
    if not path.is_file():
        return None
    release = AndroidRelease(**json.loads(path.read_text(encoding="utf-8")))
    return release if (settings.android_releases_path / release.file_name).is_file() else None


def release_file(version_code: int) -> Path | None:
    release = latest_release()
    # Only the current release is offered; superseded files are pruned anyway.
    if release is None or release.version_code != version_code:
        return None
    return settings.android_releases_path / release.file_name


async def publish(apk: Path, *, version_code: int, version_name: str) -> AndroidRelease:
    """Copy a signed APK into the channel, switch the manifest and notify phones."""
    current = latest_release()
    if current is not None and version_code <= current.version_code:
        raise ValueError(f"versionCode {version_code} must exceed the published {current.version_code}")
    directory = settings.android_releases_path
    directory.mkdir(parents=True, exist_ok=True)
    file_name = f"jarvis-{version_code}.apk"
    staged = directory / f"{file_name}.partial"
    shutil.copyfile(apk, staged)
    digest = hashlib.sha256()
    with staged.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    staged.replace(directory / file_name)
    release = AndroidRelease(version_code=version_code, version_name=version_name, sha256=digest.hexdigest(),
        size=(directory / file_name).stat().st_size, file_name=file_name,
        published_at=datetime.now(timezone.utc).isoformat())
    manifest = directory / f"{MANIFEST}.partial"
    manifest.write_text(json.dumps(asdict(release), indent=2), encoding="utf-8")
    manifest.replace(directory / MANIFEST)
    for old in sorted(directory.glob("jarvis-*.apk"), key=lambda path: int(path.stem.removeprefix("jarvis-")))[:-RETAINED_RELEASES]:
        old.unlink()
    # The live stream rescans the database, so a separate publishing process
    # still reaches connected phones within a second.
    async with session_scope() as session:
        session.add(JarvisLiveEvent(event_type=UPDATE_PUBLISHED_EVENT, source_domain="device",
            idempotency_key=f"android-update:{version_code}",
            payload_json={"version_code": version_code, "version_name": version_name}))
    return release
