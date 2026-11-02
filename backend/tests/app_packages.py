"""Where app-platform tests stage package folders before installing them."""
from __future__ import annotations

from pathlib import Path

from app.config import settings


def staging_path() -> Path:
    """Next to the per-test apps folder that conftest points settings at."""
    return Path(settings.apps_dir).parent / "app-builds"
