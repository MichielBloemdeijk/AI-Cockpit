from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.db.tables import Task, TaskExecutionRun
from app.services.task_execution_preferences import normalize_agent_preferences
from app.services.task_orchestration import DecisionTools, TaskOrchestrationService
from app.services.task_service import task_service


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(settings, "task_executor", "codex")
    monkeypatch.setattr(settings, "task_orchestration_enabled", True)
    monkeypatch.setattr(settings, "task_execution_model_allowlist", "gpt-5.6-luna,fixture-model")


@pytest.mark.asyncio
async def test_continuation_preferences_inherit_without_mutating_existing_run_or_losing_replay():
    first = await task_service.dispatch(goal="Report", origin_surface="test", idempotency_key="original",
        execution_config={"model": "fixture-model", "reasoning_effort": "medium", "override_reason": "Requested deeper analysis"})
    await task_service.complete(first.task.id, summary="Done", idempotency_key="done")
    followup = await task_service.continue_task(first.task.id, content="Expand report", origin="test", idempotency_key="followup")
    assert followup.run.configuration_json == {"model": "fixture-model", "reasoning_effort": "medium", "override_reason": "Requested deeper analysis"}
    await task_service.complete(first.task.id, summary="Done again", idempotency_key="done-again")
    changed = await task_service.continue_task(first.task.id, content="Final version", origin="test", idempotency_key="changed",
        execution_config={"reasoning_effort": "high", "override_reason": "Final review"})
    assert changed.run.configuration_json == {"model": "fixture-model", "reasoning_effort": "high", "override_reason": "Final review"}
    replay = await task_service.continue_task(first.task.id, content="Expand report", origin="test", idempotency_key="followup")
    assert replay.run.id == followup.run.id
    async with session_scope() as session:
        original = await session.get(TaskExecutionRun, first.task.current_run_id)
        assert original.configuration_json == {"model": "fixture-model", "reasoning_effort": "medium", "override_reason": "Requested deeper analysis"}


@pytest.mark.asyncio
async def test_background_delegate_options_use_common_validation_and_idempotency():
    task = (await task_service.dispatch(goal="Recover report", origin_surface="test", idempotency_key="failure")).task
    await task_service.complete(task.id, summary="Failure", status="failed", idempotency_key="failed")
    service = TaskOrchestrationService()
    assert await service.acquire()
    await service.intake()
    tools = DecisionTools(await service.claim())
    args = {"goal": "Repair report", "execution_preferences": {"model": "fixture-model", "reasoning_effort": "medium", "override_reason": "Requested deeper analysis"}, "reason": "Substantial repair", "operation_key": "repair"}
    first = await tools.call("delegate", args)
    assert await tools.call("delegate", args) == first
    with pytest.raises(ValueError, match="changed arguments"):
        await tools.call("delegate", {**args, "execution_preferences": {**args["execution_preferences"], "reasoning_effort": "high"}})
    detail = await task_service.get_task(first["task_id"])
    assert detail.runs[0].configuration_json == {"model": "fixture-model", "reasoning_effort": "medium", "override_reason": "Requested deeper analysis"}
    async with session_scope() as session:
        assert len(list((await session.execute(select(Task))).scalars())) == 2


@pytest.mark.asyncio
async def test_background_pending_continuation_retains_question_and_pins_only_new_run():
    task = (await task_service.dispatch(goal="Report", origin_surface="test", idempotency_key="waiting")).task
    await task_service.ask_question(task.id, question="Choose environment", idempotency_key="environment")
    service = TaskOrchestrationService()
    assert await service.acquire()
    await service.intake()
    tools = DecisionTools(await service.claim())
    await tools.call("continue", {"content": "Use a deeper analysis on the follow-up", "execution_preferences": {"model": "fixture-model",
        "reasoning_effort": "high", "override_reason": "Accepted follow-up instruction"}, "reason": "Accepted follow-up instruction", "operation_key": "followup"})
    detail = await task_service.get_task(task.id)
    assert detail.task.current_run_id == task.current_run_id
    assert detail.runs[0].configuration_json == {}
    assert detail.runs[-1].configuration_json["reasoning_effort"] == "high"
    from app.services.task_presentations import task_presentation_service
    assert len(await task_presentation_service.list_pending()) == 1
    assert detail.questions[0].state == "asked"


@pytest.mark.parametrize("options", [{"model": "not-permitted"}, {"reasoning_effort": "ultra"},
    {"sandbox": "danger-full-access"}, {"profile": "desktop"}, {"model": True}, {"reasoning_effort": []}])
def test_invalid_preferences_never_expand_permissions(options):
    with pytest.raises(ValueError):
        normalize_agent_preferences(options)


def test_legacy_route_does_not_silently_ignore_requested_preferences(monkeypatch):
    monkeypatch.setattr(settings, "task_executor", "legacy")
    with pytest.raises(ValueError, match="Codex"):
        normalize_agent_preferences({"reasoning_effort": "medium"})


def test_voice_and_background_schema_share_permitted_options():
    from app.jarvis_tasks.orchestration_mcp import catalog
    from app.services.jarvis_tool_catalog import get_jarvis_tool_definitions
    voice = {row["name"]: row["parameters"] for row in get_jarvis_tool_definitions()}
    background = {row["name"]: row["inputSchema"] for row in catalog()}
    assert voice["dispatch_task"]["properties"]["execution_preferences"] == background["delegate"]["properties"]["execution_preferences"]
    assert voice["continue_task"]["properties"]["execution_preferences"] == background["continue"]["properties"]["execution_preferences"]
