"""Apps start their own workflows (agent work) from functions and their UI.

A workflow an app provides runs as an ordinary Codex task: fenced, recoverable,
publishing into the app's result collection and notifying on completion. Only
workflows that need no initial context can start this way; a screen workflow
needs the phone's screen, which only Jarvis captures. Each start is a paid task,
so an app may start at most `app_workflow_daily_limit` in 24 hours.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select

from app import app_platform
from app.config import settings
from app.db.session import read_session_scope
from app.db.tables import Task
from app.services.task_service import task_service, utc_now
from app.services.task_workflows import load_workflow

KEY_PREFIX = "app-workflow"


class AppWorkflowError(ValueError):
    pass


async def start_app_workflow(slug: str, workflow_id: str, goal: str, *, request_key: str) -> dict[str, Any]:
    """Start one of the app's own workflows; `request_key` makes a retried start return the same task."""
    app = await app_platform.get_active(slug)
    goal = " ".join(str(goal or "").split())
    if not goal or len(goal) > 4000:
        raise AppWorkflowError("A workflow needs a goal of at most 4000 characters")
    definition = (app.manifest.get("workflow_definitions") or {}).get(workflow_id)
    if definition is None:
        raise AppWorkflowError(f"{app.title} has no workflow {workflow_id}")
    loaded = load_workflow(f"{workflow_id}@{definition['version']}")
    if loaded.required_initial_context:
        raise AppWorkflowError(f"Workflow {workflow_id} needs the phone's screen; only Jarvis can start it")
    prefix = f"{KEY_PREFIX}:{slug}:"
    key = f"{prefix}{workflow_id}:{request_key}"[:255]
    async with read_session_scope() as session:
        existing = (await session.execute(select(Task).where(Task.idempotency_key == key))).scalar_one_or_none()
        if existing is None:
            started = (await session.execute(select(func.count()).select_from(Task).where(
                Task.idempotency_key.startswith(prefix), Task.created_at >= utc_now() - timedelta(days=1)))).scalar_one()
            if started >= settings.app_workflow_daily_limit:
                raise AppWorkflowError(f"{app.title} already started {started} workflows in the last 24 hours, "
                                       f"its limit")
    snapshot = await task_service.dispatch(
        goal=goal, origin_surface="app_platform", execution_kind="workflow",
        execution_config={"definition": f"{workflow_id}@{definition['version']}"}, idempotency_key=key)
    return {"task_id": snapshot.task.id, "status": snapshot.task.status, "url": f"/tasks/{snapshot.task.id}"}
