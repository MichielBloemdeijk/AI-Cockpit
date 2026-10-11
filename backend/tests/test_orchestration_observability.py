from __future__ import annotations

import asyncio
from datetime import timedelta
import json

import pytest
from sqlalchemy import select, update

from app.config import settings
from app.db.session import session_scope
from app.db.tables import OrchestrationDecision, OrchestrationMetric, TaskExecutionRun
from app.services.orchestration_telemetry import record, snapshot, timestamp, token_usage
from app.services.task_orchestration import DecisionTools, TaskOrchestrationService
from app.services.task_service import task_service


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "task_orchestration_enabled", True)


async def prepared(runtime=None):
    task = (await task_service.dispatch(goal="Use staging for the report", origin_surface="test", idempotency_key="question-task")).task
    await task_service.ask_question(task.id, question="Which environment?", idempotency_key="question")
    service = TaskOrchestrationService(runtime)
    assert await service.acquire()
    await service.intake()
    return service, task


class FixtureRuntime:
    async def decide(self, claim, tools):
        return await tools.call("answer", {"answer": "staging", "reason": "Explicit goal", "operation_key": "answer"})

    async def stop(self):
        pass


@pytest.mark.asyncio
async def test_durable_phase_metrics_distinguish_queue_forwarding_runtime_and_paid_turns():
    service, task = await prepared(FixtureRuntime())
    async with session_scope() as session:
        await session.execute(update(OrchestrationDecision).values(created_at=timestamp() - timedelta(seconds=2)))
    completed = (await task_service.dispatch(goal="Factual result", origin_surface="test", idempotency_key="fact-task")).task
    await task_service.complete(completed.id, summary="Done", idempotency_key="fact")
    await service.intake()
    assert await service.run_one()
    async with session_scope() as session:
        stats = await snapshot(session)
        metrics = list((await session.execute(select(OrchestrationMetric))).scalars())
    assert stats["counts"]["factual_forwarding"] == 1
    assert stats["counts"]["decision_queue"] == 1
    assert stats["counts"]["runtime_invocation"] == 1
    assert stats["counts"].get("native_turn", 0) == 0  # Fixture invocation is not a native/paid call.
    assert stats["recent_duration_samples"]["decision_queue"]["max_ms"] >= 2000
    assert stats["action_outcomes"]["committed"] == 1
    assert stats["native_usage"]["cost_usd"] is None
    await service.intake()
    assert not await service.run_one()
    async with session_scope() as session:
        before = (await snapshot(session))["counts"]
    await service.intake()  # Already-forwarded own audit events cause no new measurements.
    async with session_scope() as session:
        assert (await snapshot(session))["counts"] == before
    assert all("Explicit goal" not in json.dumps(row.values_json) and "Which environment" not in json.dumps(row.values_json) for row in metrics)


@pytest.mark.asyncio
async def test_feedback_metrics_rollback_with_cursor_and_facts():
    task = (await task_service.dispatch(goal="Fact", origin_surface="test", idempotency_key="rollback")).task
    await task_service.complete(task.id, summary="Done", idempotency_key="done")
    service = TaskOrchestrationService()
    async def crash():
        raise RuntimeError("Crash before feedback commit")
    with pytest.raises(RuntimeError):
        await service.intake(before_commit=crash)
    async with session_scope() as session:
        assert (await snapshot(session))["counts"] == {}
    await service.intake()
    async with session_scope() as session:
        assert (await snapshot(session))["counts"]["factual_forwarding"] == 1


@pytest.mark.asyncio
async def test_replay_and_stale_action_metrics_never_replace_action_authority():
    service, task = await prepared()
    tools = DecisionTools(await service.claim())
    args = {"reason": "Needs user input", "operation_key": "retain"}
    await tools.call("retain", args)
    await tools.call("retain", args)
    await task_service.control(task.id, action="cancel", idempotency_key="cancel")
    from app.services.orchestration_authority import StaleDecision
    with pytest.raises(StaleDecision):
        await tools.call("answer", {"answer": "staging", "reason": "Late answer", "operation_key": "late"})
    async with session_scope() as session:
        assert (await snapshot(session))["action_outcomes"] == {"committed": 1, "idempotent_replay": 1, "stale_rejected": 1}


def usage(total=70):
    return {"threadId": "thread", "turnId": "turn", "tokenUsage": {
        "total": {"totalTokens": total, "inputTokens": 50, "outputTokens": total - 50, "secret": "never-store"},
        "last": {"totalTokens": total, "cachedInputTokens": 5}, "irrelevant": "never-store"}}


@pytest.mark.asyncio
async def test_cumulative_usage_snapshots_replace_not_sum_and_unknown_cost_is_not_zero():
    service, task = await prepared()
    claim = await service.claim()
    async with session_scope() as session:
        for value in [70, 70, 80]:
            await record(session, key="usage:test", kind="native_usage", decision_id=claim.id,
                         task_id=task.id, generation=claim.generation,
                         values=token_usage(usage(value), thread_id="thread", turn_id="turn"), replace=True)
    async with session_scope() as session:
        stats = await snapshot(session)
    assert stats["counts"]["native_usage"] == 1
    assert stats["native_usage"]["totals"]["totalTokens"] == 80
    assert stats["native_usage"]["cost_usd"] is None
    assert "never-store" not in json.dumps(stats)
    assert token_usage(usage(), thread_id="different", turn_id="turn") is None
    invalid = usage()
    invalid["tokenUsage"]["total"]["inputTokens"] = -1
    assert token_usage(invalid, thread_id="thread", turn_id="turn") is None


@pytest.mark.asyncio
async def test_native_protocol_records_turn_and_usage_without_provider(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "offline-protocol-fixture")
    import socket
    def no_network(*args, **kwargs):
        pytest.fail("Offline native protocol fixture attempted a network connection")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    from app.services.codex_orchestration_runtime import CodexOrchestrationRuntime
    service, task = await prepared()
    claim = await service.claim()
    messages = [
        {"type": "connected", "thread_id": "thread", "tools": []},
        {"type": "binding", "thread_id": "thread", "turn_id": "turn"},
        *[{"type": "event", "event_type": "thread/tokenUsage/updated", "payload": usage(value)} for value in [70, 70, 80]],
        {"type": "result", "status": "completed", "reason": "Fixture result"},
    ]
    class Pipe:
        def write(self, data):
            pass
        async def drain(self):
            pass
        async def readline(self):
            return (json.dumps(messages.pop(0)) + "\n").encode() if messages else b""
    class Process:
        pid, returncode = 12345, 0
        stdin, stdout = Pipe(), Pipe()
        async def wait(self):
            return 0
        def kill(self):
            pytest.fail("Completed fixture host should not be killed")
    async def spawn(*args, **kwargs):
        return Process()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    runtime = CodexOrchestrationRuntime(permitted=True)
    result = await runtime.decide(claim, DecisionTools(claim))
    assert result["status"] == "completed" and runtime.process is None
    async with session_scope() as session:
        stats = await snapshot(session)
    assert stats["counts"]["native_turn"] == 1
    assert stats["counts"]["native_usage"] == 1
    assert stats["native_usage"]["totals"]["totalTokens"] == 80
