"""Control prerequisites are projected for every client, never inferred from summary text."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.tasks import _answer_supported
from app.presenters.tasks import present_task_summary
from app.services.tasks.service import task_service


def _snapshot(kind: str, capabilities: list[str] | None = None):
    run = SimpleNamespace(id="run-1", execution_kind=kind,
                          configuration_json={"workflow": {"capabilities": capabilities or []}} if kind == "workflow" else {})
    return SimpleNamespace(task=SimpleNamespace(current_run_id="run-1"), runs=[run])


def test_answer_only_follow_up_depends_on_the_current_run() -> None:
    assert _answer_supported(_snapshot("agent"))
    assert _answer_supported(_snapshot("workflow", ["task.finish@1", "result.publish@1"]))
    assert not _answer_supported(_snapshot("workflow", ["result.publish@1"]))


@pytest.mark.asyncio
async def test_retry_acknowledgement_follows_the_recorded_reason_not_the_summary() -> None:
    created = await task_service.dispatch(goal="Edit the shared folder", idempotency_key="prereq-task", origin_surface="test")
    task = created.task
    task.status_reason = "shared_resource_changed"
    task.terminal_summary = "Reworded summary without the old phrase"
    assert present_task_summary(task, None).retry_requires_change_acknowledgement
    task.status_reason = "shared_resource_unobservable"
    task.terminal_summary = "This target cannot be checked"
    assert not present_task_summary(task, None).retry_requires_change_acknowledgement
