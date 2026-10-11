"""Bounded, fail-closed snapshots of shared mutable directory targets."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


MAX_FILES = 50_000
MAX_BYTES = 512 * 1024 * 1024


def directory_snapshot(path: str) -> dict[str, object]:
    """Hash names and file contents without following links outside the lease.

    A limit, unreadable entry, or symlink is an explicit unknown state; it is
    never treated as an unchanged directory on reacquisition.
    """
    root = Path(path)
    digest = hashlib.sha256()
    files = 0
    size = 0
    try:
        if not root.exists():
            return {"state": "missing"}
        if not root.is_dir() or root.is_symlink():
            return {"state": "unknown", "reason": "not_a_plain_directory"}
        for current, dirs, names in os.walk(root, followlinks=False):
            dirs.sort()
            names.sort()
            current_path = Path(current)
            for name in sorted(dirs + names):
                entry = current_path / name
                relative = entry.relative_to(root).as_posix()
                if entry.is_symlink():
                    return {"state": "unknown", "reason": "symlink", "path": relative}
                if entry.is_dir():
                    digest.update(f"d:{relative}\0".encode())
                    continue
                if not entry.is_file():
                    return {"state": "unknown", "reason": "special_file", "path": relative}
                files += 1
                if files > MAX_FILES:
                    return {"state": "unknown", "reason": "file_limit"}
                digest.update(f"f:{relative}\0".encode())
                with entry.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_BYTES:
                            return {"state": "unknown", "reason": "byte_limit"}
                        digest.update(chunk)
                digest.update(b"\0")
        return {"state": "known", "sha256": digest.hexdigest(), "files": files, "bytes": size}
    except (OSError, UnicodeError) as exc:
        return {"state": "unknown", "reason": type(exc).__name__}
