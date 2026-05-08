"""Helpers for per-conversation workspace folders."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.config import settings


MAIN_BRANCH_KEY = "main"


def workspace_relative_path(conversation_id: str) -> str:
    return (Path(".cockpit") / "conversations" / conversation_id).as_posix()


def resolve_workspace_path(workspace_path: str) -> Path:
    candidate = Path(workspace_path)
    if candidate.is_absolute():
        return candidate
    return settings.backend_root.parent / candidate


def ensure_conversation_workspace(workspace_path: str) -> Path:
    path = resolve_workspace_path(workspace_path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_workspace_files(workspace_path: str) -> list[dict[str, str | int | None]]:
    root = ensure_conversation_workspace(workspace_path)
    files: list[dict[str, str | int | None]] = []
    for file_path in sorted(root.rglob("*")):
        if not file_path.is_file():
            continue
        stat = file_path.stat()
        files.append(
            {
                "path": file_path.relative_to(root).as_posix(),
                "size": stat.st_size,
                "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            }
        )
    return files