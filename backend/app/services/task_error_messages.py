"""Model-facing task error summaries that keep diagnostics out of unsolicited replies."""
from __future__ import annotations

import re


_FAILURE_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "a database error",
        (
            "sqlite",
            "sqlalchemy",
            "integrityerror",
            "foreign key constraint",
            "database error",
            "operationalerror",
        ),
    ),
    ("a timeout", ("timed out", "timeout", "deadline exceeded")),
    (
        "an unavailable service",
        (
            "connection refused",
            "connection reset",
            "connectionerror",
            "econnrefused",
            "service unavailable",
            "failed to connect",
            "network is unreachable",
        ),
    ),
    (
        "an authentication error",
        ("authentication failed", "unauthorized", "invalid api key", "status code 401"),
    ),
    (
        "a permission error",
        ("permission denied", "access denied", "forbidden", "status code 403"),
    ),
    ("a service rate limit", ("rate limit", "too many requests", "status code 429")),
    (
        "a missing required program",
        ("command not found", "is not recognized as an internal or external command", "filenotfounderror"),
    ),
    ("the worker stopping unexpectedly", ("worker heartbeat was lost", "worker stopped responding")),
)

_TECHNICAL_MARKERS = (
    "traceback (most recent call last)",
    "[sql:",
    "[parameters:",
    "stack trace",
    "exception:",
    "errno",
    " at 0x",
)


def failure_category(value: str | None) -> str | None:
    """Return a short, user-safe category for a recognizable technical failure."""
    normalized = " ".join(str(value or "").casefold().split())
    for category, markers in _FAILURE_CATEGORIES:
        if any(marker in normalized for marker in markers):
            return category
    return None


def public_failure_summary(value: str | None) -> str:
    """Summarize a failure without leaking SQL, traces, paths, or provider payloads."""
    raw = str(value or "").strip()
    category = failure_category(raw)
    if category:
        return f"The task failed because of {category}."
    compact = " ".join(raw.split())
    technical = (
        len(compact) > 180
        or any(marker in raw.casefold() for marker in _TECHNICAL_MARKERS)
        or bool(re.search(r"\b(?:file|line)\s+[\"']?[^\s]+", raw, flags=re.IGNORECASE))
    )
    if technical:
        return "The task failed because of an internal error."
    return compact or "The task could not finish."


def public_tool_error(value: str | None) -> dict[str, object]:
    """Build the provider-facing form of an internally retained tool exception."""
    return {
        "error": public_failure_summary(value),
        "diagnostics_available": True,
    }


__all__ = ["failure_category", "public_failure_summary", "public_tool_error"]
