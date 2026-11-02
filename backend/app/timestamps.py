"""The one current-time helper, so every timestamp is timezone-aware UTC."""
from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
