"""Build the signed release APK and publish it to the phone's update channel.

Run with ``cd backend && uv run python ../android/scripts/publish-update.py``.
It uses the backend's configured database and ``ANDROID_RELEASES_DIR``, so the
running backend serves the release at once and connected phones are notified.
Requires the signing key from ``create-signing-key.py``.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
ANDROID = ROOT / "android"
VERSION_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def next_version_code(published: int | None) -> int:
    """Minutes since the epoch: increasing across machines and rebuilds."""
    minutes = int((datetime.now(timezone.utc) - VERSION_EPOCH).total_seconds() // 60)
    return max(minutes, (published or 0) + 1)


def version_name() -> str:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    dirty = "-dirty" if git("status", "--porcelain", "--untracked-files=no") else ""
    return f"{datetime.now(timezone.utc):%Y.%m.%d}+{git('rev-parse', '--short', 'HEAD')}{dirty}"


def build(version_code: int, name: str) -> Path:
    gradle = ANDROID / ("gradlew.bat" if os.name == "nt" else "gradlew")
    subprocess.run([str(gradle), ":app:assembleRelease", f"-PjarvisVersionCode={version_code}",
        f"-PjarvisVersionName={name}", "--console=plain"], cwd=ANDROID, check=True, timeout=1800)
    output = ANDROID / "app/build/outputs/apk/release"
    element = json.loads((output / "output-metadata.json").read_text(encoding="utf-8"))["elements"][0]
    if element["outputFile"] != "app-release.apk":
        raise SystemExit("Release APK is unsigned; run create-signing-key.py or set JARVIS_SIGNING_PROPERTIES")
    if element["versionCode"] != version_code:
        raise SystemExit(f"Gradle built versionCode {element['versionCode']}, expected {version_code}")
    return output / element["outputFile"]


async def publish(version_code: int | None) -> None:
    from app.db.session import close_engine
    from app.services import android_updates
    current = android_updates.latest_release()
    code = version_code or next_version_code(current and current.version_code)
    name = version_name()
    try:
        release = await android_updates.publish(build(code, name), version_code=code, version_name=name)
    finally:
        await close_engine()
    print(f"Published Jarvis {release.version_name} (versionCode {release.version_code}, {release.size} bytes)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version-code", type=int, help="Override the generated versionCode")
    asyncio.run(publish(parser.parse_args().version_code))


if __name__ == "__main__":
    main()
