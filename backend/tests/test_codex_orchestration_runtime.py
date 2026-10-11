from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.db.tables import OrchestrationAction, OrchestrationDecision, OrchestrationMetric
from app.jarvis_tasks.orchestration_mcp import catalog, handle
from app.services.codex_orchestration_runtime import CodexOrchestrationRuntime
from app.services.task_orchestration import DecisionTools, TaskOrchestrationService
from app.services.task_service import task_service


@pytest.mark.asyncio
async def test_bridge_catalog_has_only_declared_backend_bound_capabilities(monkeypatch):
    monkeypatch.setattr(settings, "task_orchestration_enabled", True)
    task = (await task_service.dispatch(goal="Use staging for this report", origin_surface="test", idempotency_key="bridge")).task
    await task_service.ask_question(task.id, question="Which environment?", idempotency_key="environment")
    service = TaskOrchestrationService()
    assert await service.acquire()
    await service.intake()
    tools = DecisionTools(await service.claim())
    listing = await handle({"id": 1, "method": "tools/list"}, tools)
    assert {row["name"] for row in listing["result"]["tools"]} == {"inspect", "answer", "control", "continue", "delegate", "retain", "presentation"}
    assert all(row["inputSchema"]["additionalProperties"] is False for row in catalog())
    response = await handle({"id": 2, "method": "tools/call", "params": {
        "name": "answer", "arguments": {"answer": "staging", "task_id": "other", "reason": "forged", "operation_key": "forged"},
    }}, tools)
    assert response["result"]["isError"]
    response = await handle({"id": 3, "method": "tools/call", "params": {
        "name": "answer", "arguments": {"answer": "staging", "reason": "Explicit user instruction", "operation_key": "answer"},
    }}, tools)
    assert response["result"]["isError"] is False
    detail = await task_service.get_task(task.id)
    assert detail.questions[0].answer == "staging"


@pytest.mark.asyncio
@pytest.mark.provider
@pytest.mark.skipif(os.environ.get("JARVIS_LIVE_CODEX") != "1", reason="Opt-in billed native integration")
@pytest.mark.parametrize("scenario", ["answer", "presentation"])
async def test_actual_restricted_codex_host_answers_question(tmp_path: Path, monkeypatch, scenario):
    from openai_codex import Codex
    from app.jarvis_tasks.codex_probe import _codex_config, _probe_api_key
    api_key = _probe_api_key()
    if not api_key:
        pytest.skip("No authorized API credential")
    monkeypatch.setattr(settings, "task_orchestration_enabled", True)
    profile = tmp_path / "dedicated-api-profile"
    workspace = tmp_path / "login-workspace"
    profile.mkdir()
    workspace.mkdir()
    monkeypatch.setattr(settings, "task_orchestration_profile", str(profile))
    monkeypatch.setattr(settings, "task_orchestration_context_dir", str(tmp_path / "context"))

    def login():
        with Codex(_codex_config(profile=profile, workspace=workspace)) as client:
            client.login_api_key(api_key)

    await asyncio.to_thread(login)
    goal = ("Use the staging environment for the deployment report. The environment is explicitly staging, not production."
            if scenario == "answer" else
            "Create a deployment report. First change this task's update policy to follow_progress as I explicitly prefer progress updates. "
            "Leave the environment question for me: I have not chosen an environment and you must not guess.")
    task = (await task_service.dispatch(goal=goal, origin_surface="test", idempotency_key="live-m3")).task
    question = await task_service.ask_question(task.id, question="What environment should I use for this deployment report?", idempotency_key="environment")
    runtime = CodexOrchestrationRuntime(permitted=True)  # authorized isolated test; not unattended rollout
    service = TaskOrchestrationService(runtime)
    assert await service.acquire()
    await service.intake()
    from test_m3_preflight import exercise_code_only_services, saturate_pool
    occupied = await saturate_pool()
    background = asyncio.create_task(service.run_one())
    try:
        await asyncio.wait_for(runtime.native_started.wait(), 15)
        service_elapsed_ms = await asyncio.wait_for(exercise_code_only_services(occupied), 5)
        assert await asyncio.wait_for(background, 150)
        detail = await task_service.get_task(task.id)
        async with session_scope() as session:
            decision = (await session.execute(select(OrchestrationDecision).where(OrchestrationDecision.task_id == task.id))).scalar_one()
            usage = (await session.execute(select(OrchestrationMetric).where(
                OrchestrationMetric.decision_id == decision.id, OrchestrationMetric.kind == "native_usage",
            ))).scalar_one_or_none()
            native_calls = list((await session.execute(select(OrchestrationMetric).where(
                OrchestrationMetric.decision_id == decision.id, OrchestrationMetric.kind == "native_turn",
            ))).scalars())
            actions = list((await session.execute(select(OrchestrationAction).where(
                OrchestrationAction.decision_id == decision.id,
            ))).scalars())
        assert decision.status == "completed", (decision.last_error, decision.outcome_json, decision.native_binding_json,
            [e.payload_json for e in detail.events if e.event_type == "task.orchestration.runtime"][-4:])
        assert detail.questions[0].id == question.id
        if scenario == "answer":
            assert "staging" in detail.questions[0].answer.casefold()
        else:
            from app.services.task_presentations import task_presentation_service
            assert detail.questions[0].state == "asked"
            assert detail.questions[0].answer is None
            assert detail.task.presentation_policy == "follow_progress"
            assert any(action.action == "set_update_policy" for action in actions)
            assert any(row.task_id == task.id for row in await task_presentation_service.list_pending())
        assert detail.questions[0].asking_run_id == task.current_run_id
        assert decision.native_binding_json["turn_id"]
        assert decision.native_binding_json["tools"] == [f"jarvis_orchestration.{name}" for name in sorted(row["name"] for row in catalog())]
        assert len(native_calls) == 1
        assert usage is not None, "The actual native stream must expose provider token usage"
        assert usage.values_json["total"]["totalTokens"] > 0
        from app.services.orchestration_recovery import host_has_exited
        assert host_has_exited(decision.native_binding_json)
        inspection = await runtime.inspect(decision.native_binding_json, str(profile))
        assert inspection["status"] == "terminal"
        assert inspection["thread_id"] == decision.native_binding_json["thread_id"]
        assert inspection["turn_id"] == decision.native_binding_json["turn_id"]
        assert inspection["turn_statuses"] == ["completed"]
        report_path = os.environ.get("JARVIS_M3_REPORT")
        if report_path:
            destination = Path(report_path)
            if scenario != "answer":
                destination = destination.with_stem(f"{destination.stem}-{scenario}")
            destination.write_text(json.dumps({
                "schema": "jarvis-m3-native-decision.v1", "status": "pass",
                "scenario": scenario,
                "model": decision.model, "effort": decision.effort,
                "decision_status": decision.status,
                "worker_busy_at_native_start": 8,
                "exact_question_answered": "staging" in (detail.questions[0].answer or "").casefold(),
                "question_state": detail.questions[0].state,
                "presentation_policy": detail.task.presentation_policy,
                "committed_actions": [action.action for action in actions],
                "asking_run_preserved": detail.questions[0].asking_run_id == task.current_run_id,
                "shared_voice_admission_control_forwarding_history": "pass",
                "shared_service_elapsed_ms": service_elapsed_ms,
                "owned_host_stopped": runtime.process is None,
                "native_tools": decision.native_binding_json["tools"],
                "native_thread_id": decision.native_binding_json["thread_id"],
                "native_turn_id": decision.native_binding_json["turn_id"],
                "native_call_count": len(native_calls),
                "token_usage": usage.values_json,
                "post_exit_read_only_native_inspection": inspection,
                "scope": "Isolated API profile; this does not enable unattended production orchestration.",
            }, indent=2), encoding="utf-8")
    finally:
        await runtime.stop()
        if not background.done():
            background.cancel()
        await asyncio.gather(background, return_exceptions=True)
