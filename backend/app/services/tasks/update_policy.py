"""Canonical task update policies shared by APIs, workers, and Jarvis."""
from __future__ import annotations


CRITICAL_ONLY = "critical_only"
FOLLOW_PROGRESS = "follow_progress"
SILENT = "silent"
TASK_UPDATE_POLICIES = frozenset({CRITICAL_ONLY, FOLLOW_PROGRESS, SILENT})

_LEGACY_POLICY_MAP = {
    "natural": CRITICAL_ONLY,
    "important": CRITICAL_ONLY,
    "completion": CRITICAL_ONLY,
    "all": FOLLOW_PROGRESS,
    "none": SILENT,
    "never": SILENT,
}


def normalize_task_update_policy(value: str | None, *, default: str = CRITICAL_ONLY) -> str:
    normalized = str(value or default).strip().lower()
    normalized = _LEGACY_POLICY_MAP.get(normalized, normalized)
    if normalized not in TASK_UPDATE_POLICIES:
        raise ValueError(
            "Task update policy must be critical_only, follow_progress, or silent"
        )
    return normalized


__all__ = [
    "CRITICAL_ONLY",
    "FOLLOW_PROGRESS",
    "SILENT",
    "TASK_UPDATE_POLICIES",
    "normalize_task_update_policy",
]
