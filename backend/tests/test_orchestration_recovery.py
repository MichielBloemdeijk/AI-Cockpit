from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.db.tables import OrchestrationDecision, TaskEvent
from app.jarvis_tasks.orchestration_host import inspect_thread
from app.services.orchestration_authority import StaleDecision
from app.services.orchestration_recovery import host_has_exited
from app.services.task_orchestration import DecisionTools, TaskOrchestrationService
from app.services.task_service import task_service


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "task_orchestration_enabled", True)


class Inspector:
    def __init__(self, status="terminal"):
        self.status, self.calls = status, []
        self.started, self.release = asyncio.Event(), asyncio.Event()
        self.blocked = False

    async def inspect(self, binding, profile):
        self.calls.append((binding, profile))
        self.started.set()
        if self.blocked:
            await self.release.wait()
        return {"thread_id": binding["thread_id"], "turn_id": binding.get("turn_id"), "status": self.status}


async def fixture(inspector=None, proof=True):
    task = (await task_service.dispatch(goal="Deploy to staging", origin_surface="test", idempotency_key="task")).task
    question = await task_service.ask_question(task.id, question="Which environment?", idempotency_key="question")
    service = TaskOrchestrationService(inspector, native_host_has_exited=lambda binding: proof)
    assert await service.acquire()
    await service.intake()
    claim = await service.claim()
    async with session_scope() as session:
        row = await session.get(OrchestrationDecision, claim.id)
        row.status, row.due_at, row.last_error = "deferred", None, "billing_exhausted"
        row.native_binding_json = {"thread_id": "thread", "turn_id": "turn", "host_pid": 123,
                                   "host_creation_stamp": 456, "host_hostname": socket.gethostname()}
    return service, task, question, claim


async def decision(claim):
    async with session_scope() as session:
        return await session.get(OrchestrationDecision, claim.id)


@pytest.mark.asyncio
async def test_automatic_inspection_is_once_and_never_automatically_spends_again():
    inspector = Inspector()
    service, task, question, claim = await fixture(inspector)
    for _ in range(3):
        await service.inspect_deferred()
        assert not await service.run_one()
    row = await decision(claim)
    assert row.status == "deferred" and row.due_at is None
    assert row.outcome_json["recovery"]["safe_to_resume"] is True
    assert len(inspector.calls) == 1
    assert (await task_service.get_task(task.id)).questions[0].state == "asked"


@pytest.mark.asyncio
async def test_explicit_resume_is_audited_idempotent_and_fences_old_bridge():
    inspector = Inspector()
    service, task, question, old_claim = await fixture(inspector)
    args = {"resume": True, "operation_key": "availability-restored", "reason": "Provider availability restored by operator."}
    first = await service.recover(old_claim.id, **args)
    assert first["status"] == "pending"
    assert await service.recover(old_claim.id, **args) == first
    assert len(inspector.calls) == 1
    with pytest.raises(ValueError, match="changed arguments"):
        await service.recover(old_claim.id, **{**args, "reason": "Changed authorization"})
    with pytest.raises(StaleDecision):
        await DecisionTools(old_claim).call("answer", {"answer": "production", "operation_key": "late", "reason": "Late old host"})
    fresh = await service.claim()
    assert fresh.generation > old_claim.generation
    await DecisionTools(fresh).call("answer", {"answer": "staging", "operation_key": "answer", "reason": "Explicit goal"})
    async with session_scope() as session:
        audits = list((await session.execute(select(TaskEvent).where(TaskEvent.event_type == "task.orchestration.resumption"))).scalars())
    assert len(audits) == 1
    assert audits[0].payload_json["previous_native_binding"]["turn_id"] == "turn"
    assert not settings.task_orchestration_live_enabled


@pytest.mark.asyncio
@pytest.mark.parametrize("proof,status,expected", [(False, "terminal", "host_exit_unverified"),
                                                    (True, "uncertain", "uncertain")])
async def test_unknown_host_or_native_outcome_cannot_resume(proof, status, expected):
    inspector = Inspector(status)
    service, _, _, claim = await fixture(inspector, proof)
    result = await service.recover(claim.id, resume=True, operation_key="attempt", reason="Availability restored")
    assert result["status"] == "deferred"
    assert result["recovery"]["status"] == expected
    assert result["recovery"]["safe_to_resume"] is False
    assert len(inspector.calls) == int(proof)
    assert not await service.run_one()


@pytest.mark.asyncio
async def test_user_answer_during_inspection_obsoletes_decision_without_duplicate_action():
    inspector = Inspector()
    inspector.blocked = True
    service, task, question, claim = await fixture(inspector)
    recovery = asyncio.create_task(service.recover(claim.id, resume=True, operation_key="restore", reason="Availability restored"))
    await asyncio.wait_for(inspector.started.wait(), 2)
    await asyncio.wait_for(task_service.answer_question(task.id, question.id, answer="production", idempotency_key="user"), 2)
    inspector.release.set()
    assert (await recovery)["status"] == "obsolete"
    assert (await task_service.get_task(task.id)).questions[0].answer == "production"
    assert not await service.run_one()


@pytest.mark.asyncio
async def test_concurrent_resume_cannot_duplicate_native_turn():
    inspector = Inspector()
    inspector.blocked = True
    service, _, _, claim = await fixture(inspector)
    args = {"resume": True, "operation_key": "restore", "reason": "Availability restored"}
    a = asyncio.create_task(service.recover(claim.id, **args))
    b = asyncio.create_task(service.recover(claim.id, **args))
    await asyncio.wait_for(inspector.started.wait(), 2)
    await asyncio.sleep(0.02)
    inspector.release.set()
    results = await asyncio.gather(a, b, return_exceptions=True)
    assert any(isinstance(result, dict) and result["status"] == "pending" for result in results)
    assert all(isinstance(result, (dict, StaleDecision)) for result in results)
    assert (await decision(claim)).status == "pending"
    assert sum(result is not None for result in await asyncio.gather(service.claim(), service.claim())) == 1


@pytest.mark.asyncio
async def test_committed_receipt_wins_even_if_native_history_is_unavailable():
    inspector = Inspector("uncertain")
    service, task, _, claim = await fixture(inspector, False)
    async with session_scope() as session:
        row = await session.get(OrchestrationDecision, claim.id)
        row.status = "running"
    await DecisionTools(claim).call("answer", {"answer": "staging", "operation_key": "answer", "reason": "Goal specifies staging"})
    async with session_scope() as session:
        row = await session.get(OrchestrationDecision, claim.id)
        row.status = "deferred"
    result = await service.recover(claim.id)
    assert result["status"] == "completed"
    assert inspector.calls == []
    assert (await task_service.get_task(task.id)).questions[0].answer == "staging"


@pytest.mark.asyncio
async def test_recovery_endpoint_auth_enabled_gate_and_strict_schema(monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    import app.api.orchestration as api
    service, _, _, claim = await fixture(Inspector())
    monkeypatch.setattr(api, "task_orchestration_service", service)
    path = f"/api/orchestration/{claim.id}/resume"
    body = {"operation_key": "restore", "reason": "Provider availability restored"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post(path, json=body)).status_code == 401
        await client.post("/api/auth/login", json={"password": ""})
        assert (await client.post(path, json={**body, "generation": 100})).status_code == 422
        monkeypatch.setattr(settings, "task_orchestration_enabled", False)
        assert (await client.post(path, json=body)).status_code == 409
        monkeypatch.setattr(settings, "task_orchestration_enabled", True)
        result = await client.post(path, json=body)
        assert result.status_code == 200 and result.json()["status"] == "pending"


@pytest.mark.parametrize("statuses,turn_id,expected", [(["completed"], "turn", "terminal"),
    (["failed"], "turn", "terminal"), (["interrupted"], "turn", "terminal"),
    (["inProgress"], "turn", "uncertain"), (["unknown"], "turn", "uncertain"),
    ([], "turn", "uncertain"), ([], None, "terminal"), (["completed", "inProgress"], None, "uncertain")])
def test_native_inspector_only_reads_exact_stored_thread(statuses, turn_id, expected):
    calls = []
    def read(thread_id, include_turns):
        calls.append((thread_id, include_turns))
        return {"thread": {"id": "thread", "turns": [{"id": "turn" if i == 0 else "other", "status": status}
                                                     for i, status in enumerate(statuses)]}}
    client = SimpleNamespace(_client=SimpleNamespace(thread_read=read))
    assert inspect_thread(client, {"thread_id": "thread", "turn_id": turn_id})["status"] == expected
    assert calls == [("thread", True)]  # No start/resume/account/tool operation exists on the fake.


def test_host_exit_proof_rejects_unknown_identity_and_other_machine(monkeypatch):
    import app.services.orchestration_recovery as recovery
    monkeypatch.setattr(recovery, "process_has_exited", lambda *args: True)
    assert not host_has_exited({"host_pid": 123, "host_creation_stamp": 456})
    assert not host_has_exited({"host_pid": 123, "host_creation_stamp": 456, "host_hostname": "other-machine"})
    assert host_has_exited({"host_pid": 123, "host_creation_stamp": 456, "host_hostname": socket.gethostname()})


@pytest.mark.asyncio
async def test_due_native_retry_requires_inspection_instead_of_duplicate_generation():
    from app.services.task_orchestration import now
    inspector = Inspector("uncertain")
    service, _, _, claim = await fixture(inspector)
    async with session_scope() as session:
        row = await session.get(OrchestrationDecision, claim.id)
        row.due_at, row.last_error = now(), "rate_limited"
    assert await service.claim() is None
    assert (await decision(claim)).due_at is None
    await service.inspect_deferred()
    assert (await decision(claim)).outcome_json["recovery"]["safe_to_resume"] is False
    assert await service.claim() is None


@pytest.mark.asyncio
async def test_automatic_inspection_does_not_starve_behind_100_already_inspected_bindings():
    from uuid import uuid4
    from app.services.orchestration_authority import digest
    inspector = Inspector()
    service, _, _, claim = await fixture(inspector)
    async with session_scope() as session:
        original = await session.get(OrchestrationDecision, claim.id)
        original.source_sequence = 101
        for sequence in range(1, 101):
            binding = {**original.native_binding_json, "thread_id": f"known-thread-{sequence}"}
            session.add(OrchestrationDecision(id=str(uuid4()), issue_key=digest({"known": sequence}),
                task_id=original.task_id, execution_run_id=original.execution_run_id, question_id=original.question_id,
                trigger_kind=original.trigger_kind, source_event_ids_json=original.source_event_ids_json,
                source_sequence=sequence, observed_json=original.observed_json, status="deferred",
                context_json=original.context_json, context_digest=original.context_digest,
                model=original.model, effort=original.effort, auth_profile=original.auth_profile,
                native_binding_json=binding, outcome_json={"recovery": {"binding_digest": digest(binding)}}))
    await service.inspect_deferred()
    assert len(inspector.calls) == 1
    assert inspector.calls[0][0]["thread_id"] == "thread"
    assert (await decision(claim)).outcome_json["recovery"]["safe_to_resume"] is True


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="Verified Windows containment route")
async def test_actual_contained_writer_crash_reconciles_one_committed_action():
    """Real subprocess/SQLite/Job Object; no model or provider call."""
    from uuid import uuid4
    from app.services.windows_job import process_creation_stamp, process_has_exited

    child_code = "\n".join([
        "import asyncio, json, sys",
        "from app.db.session import initialize_engine, session_scope",
        "from app.db.tables import OrchestrationDecision",
        "from app.services.task_orchestration import DecisionClaim, DecisionTools",
        "async def main():",
        "    payload = json.loads(sys.argv[1])",
        "    initialize_engine(payload['database_url'])",
        "    async with session_scope() as session:",
        "        row = await session.get(OrchestrationDecision, payload['id'])",
        "        claim = DecisionClaim(row.id, payload['generation'], payload['owner'], row.context_json)",
        "    await DecisionTools(claim).call('answer', {'answer':'staging', 'reason':'Explicit goal', 'operation_key':'answer'})",
        "    print('committed', flush=True)",
        "    await asyncio.Event().wait()",
        "asyncio.run(main())",
    ])
    host_code = "\n".join([
        "import json, os, subprocess, sys",
        "from app.services.windows_job import contain_current_process_tree, exit_when_parent_exits, process_creation_stamp",
        "contain_current_process_tree()",
        f"exit_when_parent_exits({os.getpid()})",
        "payload = sys.stdin.readline()",
        f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}, payload], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)",
        "signal = child.stdout.readline().strip()",
        "if signal != 'committed':",
        "    print(json.dumps({'error': child.stderr.read()[-4000:]}), flush=True)",
        "    os._exit(2)",
        "print(json.dumps({'pid':child.pid, 'stamp':process_creation_stamp(child.pid)}), flush=True)",
        "sys.stdin.readline()",
        "os._exit(9)",
    ])
    host = await asyncio.create_subprocess_exec(sys.executable, "-c", host_code,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0))
    try:
        task = (await task_service.dispatch(goal="Use staging", origin_surface="test", idempotency_key="crash-task")).task
        question = await task_service.ask_question(task.id, question="Which environment?", idempotency_key="environment")
        stamp = process_creation_stamp(host.pid)
        assert stamp
        service = TaskOrchestrationService()
        service.owner = f"{socket.gethostname()}:{host.pid}:{stamp}:{uuid4()}"
        assert await service.acquire()
        await service.intake()
        claim = await service.claim()
        async with session_scope() as session:
            row = await session.get(OrchestrationDecision, claim.id)
            row.native_binding_json = {"host_pid": host.pid, "host_creation_stamp": stamp,
                "host_hostname": socket.gethostname(), "thread_id": "fixture-native", "turn_id": "fixture-turn"}
        host.stdin.write((json.dumps({"database_url": settings.database_url, "id": claim.id,
                                     "generation": claim.generation, "owner": claim.owner}) + "\n").encode())
        await host.stdin.drain()
        signal = await asyncio.wait_for(host.stdout.readline(), 15)
        assert signal, (await host.stderr.read()).decode(errors="replace")[-4000:]
        child = json.loads(signal)
        assert "error" not in child, child.get("error")
        host.stdin.write(b"crash\n")
        await host.stdin.drain()
        assert await asyncio.wait_for(host.wait(), 5) == 9
        deadline = asyncio.get_running_loop().time() + 5
        while process_has_exited(child["pid"], child["stamp"]) is not True and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.03)
        assert process_has_exited(child["pid"], child["stamp"]) is True
        replacement = TaskOrchestrationService()  # Real exit proof, no injected takeover.
        assert await replacement.acquire()
        assert not await replacement.run_one()  # Atomic receipt replaces another model invocation.
        row = await decision(claim)
        assert row.status == "completed" and len(row.outcome_json["reconciled_actions"]) == 1
        detail = await task_service.get_task(task.id)
        assert detail.questions[0].id == question.id and detail.questions[0].answer == "staging"
        with pytest.raises(StaleDecision):
            await DecisionTools(claim).call("answer", {"answer": "production", "reason": "Late writer", "operation_key": "late"})
    finally:
        if host.returncode is None:
            host.kill()
        await host.wait()
