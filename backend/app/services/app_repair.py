"""Repairs the app runtime starts on its own when an app reports new errors.

A repair is an ordinary `build_app@1` task, so it is fenced, recoverable and
notifies on completion like any build; nothing announces its start.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Any

from app import app_platform
from app.services.task_service import task_service

BUILD_WORKFLOW = "build_app@1"


async def start_app_repair(app: app_platform.ActiveApp, errors: list[dict[str, Any]]) -> str | None:
    fingerprint = sha256("\n".join(sorted(error["message"] for error in errors)).encode()).hexdigest()[:16]
    try:
        snapshot = await task_service.dispatch(
            goal=app_platform.repair_goal(app, errors, "it reported these errors while in use"),
            origin_surface="app_platform", execution_kind="workflow",
            execution_config={"definition": BUILD_WORKFLOW},
            idempotency_key=f"app-repair:{app.slug}:{app.version}:{fingerprint}")
    except ValueError:
        return None  # the builder is not enabled; the next repair check tries again a day later
    return snapshot.task.id
