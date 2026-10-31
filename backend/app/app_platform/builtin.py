"""Apps that ship with Cockpit, in `backend/apps/<slug>/`, installed on start.

A built-in app is an ordinary package: once installed, Jarvis can change, fix or
roll it back like any other app. Start only installs a source package that is
newer than every installed version, so Jarvis's own changes are never overwritten.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import func, select

from app.app_platform.lifecycle import install_package
from app.app_platform.manifest import MANIFEST_FILE, ManifestError
from app.app_platform.store import AppVersion, apps_session
from app.config import settings

logger = logging.getLogger(__name__)


def builtin_root() -> Path:
    return settings.backend_root / "apps"


async def install_builtin_apps(root: Path | None = None) -> list[str]:
    """Install or upgrade the built-in packages; returns 'slug@version' for each install."""
    installed: list[str] = []
    folder = root or builtin_root()
    for package in sorted(path for path in folder.iterdir() if (path / MANIFEST_FILE).is_file()) if folder.is_dir() else []:
        try:
            manifest = json.loads((package / MANIFEST_FILE).read_text(encoding="utf-8"))
            slug, version = manifest["slug"], manifest["version"]
            async with apps_session() as session:
                newest = (await session.execute(select(func.max(AppVersion.version))
                    .where(AppVersion.slug == slug))).scalar_one_or_none()
            if newest is not None and version <= newest:
                continue
            await install_package(package)
            installed.append(f"{slug}@{version}")
        except (ManifestError, OSError, ValueError, KeyError) as exc:
            # One broken package must not keep the others or Cockpit from starting.
            logger.error("Built-in app %s was not installed: %s", package.name, exc)
    return installed
