"""Post-exit proof for brokered, private-workspace file writes."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any


def confirmed_file_write(workspace_path: str, intent: dict[str, Any] | None) -> dict[str, str] | None:
    """Confirm the intended content is present, without following escape links.

    A mismatch is ambiguous: it does not prove the operation never happened.
    Call only after the writer process tree has been observed stopped.
    """
    if not isinstance(intent, dict):
        return None
    value = intent.get("path")
    expected = intent.get("sha256")
    if not isinstance(value, str) or not value or not isinstance(expected, str) or len(expected) != 64:
        return None
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    try:
        root = Path(workspace_path).resolve(strict=True)
        if not root.is_dir():
            return None
        target = root / relative
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return None
        if not target.is_file() or target.stat().st_size > 256_000:
            return None
        if not target.resolve(strict=True).is_relative_to(root):
            return None
        actual = sha256(target.read_bytes()).hexdigest()
        if actual != expected:
            return None
        return {"path": relative.as_posix(), "sha256": actual}
    except (OSError, ValueError):
        return None
