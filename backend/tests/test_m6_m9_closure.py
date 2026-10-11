from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.db.tables import RuntimeCredentialLease, TaskAttempt, WorkerInstance, Task, TaskExecutionRun, TaskSupervisorLease
from app.services.task_service import task_service
from app.services.worker_coordinator import worker_coordinator
from app.services.task_project_tools import call_project_tool
from app.services.runtime_auth_routes import select_route, release_stopped_owner


@pytest.mark.asyncio
async def test_supervisor_assignment_is_committed_before_single_child_take():
    generation = await worker_coordinator.acquire_supervisor("elected")
    created = await task_service.dispatch(goal="Assigned only by elected supervisor",
        origin_surface="test", idempotency_key="supervisor-assignment")
    worker = await worker_coordinator.activate(process_identity="child", supervised=True,
        supervisor_generation=generation)
    assert (await worker_coordinator.take_assignment(worker.worker_id)).task is None
    with pytest.raises(ValueError, match="supervisor assignments"):
        await worker_coordinator.claim_next(worker.worker_id)
    with pytest.raises(ValueError):
        await worker_coordinator.assign_next(worker.worker_id, owner_identity="imposter", generation=generation)
    assigned = (await worker_coordinator.assign_next(worker.worker_id,
        owner_identity="elected", generation=generation)).task
    async with session_scope() as session:
        durable = await session.get(TaskAttempt, assigned.attempt_id)
        child = await session.get(WorkerInstance, worker.worker_id)
        assert durable.task_id == created.task.id
        assert durable.worker_id == child.id and child.assignment_attempt_id == durable.id
        assert durable.claim_token == assigned.claim_token
        assert child.supervisor_generation == generation
        task = (await session.execute(select(Task).where(Task.id == created.task.id))).scalar_one()
        run = await session.get(TaskExecutionRun, task.current_run_id)
        supervisor = await session.get(TaskSupervisorLease, "task-pool")
        assert task.current_attempt_id == durable.id and run.id == durable.execution_run_id
        assert task.status == "running" and child.state == "busy"
        assert not child.metadata_json["assignment_taken"]
        assert durable.ended_at is None
    taken = (await worker_coordinator.take_assignment(worker.worker_id)).task
    assert taken.attempt_id == assigned.attempt_id
    assert (await worker_coordinator.take_assignment(worker.worker_id)).task is None


@pytest.mark.asyncio
async def test_failed_command_result_is_durable_and_replay_cannot_repeat_effect(tmp_path, monkeypatch):
    project = tmp_path / "authorized"
    project.mkdir()
    monkeypatch.setattr(settings, "task_project_roots", str(project))
    created = await task_service.dispatch(goal="Fix a failing test", origin_surface="test",
        idempotency_key="project-command", targets=(("directory", str(project), True),))
    worker = await worker_coordinator.activate(process_identity="command-worker")
    claim = (await worker_coordinator.claim_next(worker.worker_id)).task
    args = SimpleNamespace(task_id=created.task.id, run_id=claim.execution_run_id,
        attempt_id=claim.attempt_id, claim_token=claim.claim_token, workspace=Path(claim.workspace_path))
    roots = await call_project_tool("project_roots", {}, args)
    target = next(row["target_id"] for row in roots["roots"] if row["path"] == str(project))
    calls = []
    def execute(argv, cwd, root, profile):
        calls.append(argv)
        return {"exit_code": 1, "stdout": "1 failed", "stderr": ""}
    monkeypatch.setattr("app.services.task_project_tools.execute_sandboxed", execute)
    monkeypatch.setattr("app.services.task_project_tools.execute_trusted", execute)
    command = {"target_id": target, "argv": ["pytest"], "operation_key": "failing-test"}
    assert (await call_project_tool("project_command", command, args))["exit_code"] == 1
    assert (await call_project_tool("project_command", command, args))["stdout"] == "1 failed"
    assert len(calls) == 1
    detail = await task_service.get_task(created.task.id)
    assert detail.operations[0].state == "succeeded" and detail.operations[0].result_json["exit_code"] == 1
    with pytest.raises(ValueError, match="different input"):
        await call_project_tool("project_command", {**command, "argv": ["build"]}, args)
    with pytest.raises(ValueError, match="inside"):
        await call_project_tool("project_write", {"target_id": target, "path": "../escape", "content": "x", "operation_key": "escape"}, args)
    with pytest.raises(ValueError, match="authorized"):
        await call_project_tool("project_command", {**command, "target_id": "unleased"}, args)


@pytest.mark.asyncio
async def test_shared_subscription_owner_cannot_transfer_until_exact_process_exit(tmp_path, monkeypatch):
    subscription = tmp_path / "subscription"
    subscription.mkdir()
    (subscription / "auth.json").write_text("{}")
    monkeypatch.setattr(settings, "codex_subscription_profile", str(subscription))
    monkeypatch.setattr(settings, "codex_subscription_enabled", True)
    monkeypatch.setattr(settings, "openai_api_key", "isolated-test-key")
    monkeypatch.setattr("app.services.runtime_auth_routes.process_has_exited", lambda *_: False)
    first = await select_route(owner_kind="worker", owner_id="one", pid=100, stamp=1000,
        api_profile=tmp_path / "api-one", contained=True)
    second = await select_route(owner_kind="background", owner_id="two", pid=101, stamp=1001,
        api_profile=tmp_path / "api-two", contained=True)
    assert first.route == "subscription" and second.route == "api" and second.reason == "subscription_busy"
    monkeypatch.setattr("app.services.runtime_auth_routes.process_has_exited", lambda *_: False)
    assert not await release_stopped_owner(owner_kind="worker", owner_id="one")
    monkeypatch.setattr("app.services.runtime_auth_routes.process_has_exited", lambda *_: None)
    assert not await release_stopped_owner(owner_kind="worker", owner_id="one")
    monkeypatch.setattr("app.services.runtime_auth_routes.process_has_exited", lambda *_: True)
    assert await release_stopped_owner(owner_kind="worker", owner_id="one")
    async with session_scope() as session:
        assert not (await session.execute(select(RuntimeCredentialLease))).scalars().all()
    assert (await select_route(owner_kind="background", owner_id="two", pid=101, stamp=1001,
        api_profile=tmp_path / "api-two", contained=True)).route == "subscription"


@pytest.mark.asyncio
async def test_completed_subscription_owner_can_be_replaced_after_exact_exit(tmp_path, monkeypatch):
    subscription = tmp_path / "subscription"
    subscription.mkdir()
    (subscription / "auth.json").write_text("{}")
    monkeypatch.setattr(settings, "codex_subscription_profile", str(subscription))
    monkeypatch.setattr(settings, "codex_subscription_enabled", True)
    monkeypatch.setattr(settings, "openai_api_key", "isolated-test-key")
    monkeypatch.setattr("app.services.runtime_auth_routes.process_has_exited", lambda *_: False)
    first = await select_route(owner_kind="worker", owner_id="completed", pid=100, stamp=1000,
        api_profile=tmp_path / "api-one", contained=True)
    assert first.route == "subscription"
    monkeypatch.setattr("app.services.runtime_auth_routes.process_has_exited", lambda *_: True)
    replacement = await select_route(owner_kind="background", owner_id="next", pid=101, stamp=1001,
        api_profile=tmp_path / "api-two", contained=True)
    assert replacement.route == "subscription"
    async with session_scope() as session:
        lease = (await session.execute(select(RuntimeCredentialLease))).scalar_one()
        assert lease.owner_id == "next" and lease.pid == 101 and lease.creation_stamp == "1001"


@pytest.mark.asyncio
async def test_inflight_or_uncertain_project_command_blocks_new_keys_and_new_runs(tmp_path, monkeypatch):
    created = await task_service.dispatch(goal="Inspect a command outcome", origin_surface="test", idempotency_key="unknown")
    worker = await worker_coordinator.activate(process_identity="unknown-worker")
    claim = (await worker_coordinator.claim_next(worker.worker_id)).task
    fence = dict(attempt_id=claim.attempt_id, execution_run_id=claim.execution_run_id, claim_token=claim.claim_token)
    operation = await task_service.start_operation(created.task.id, operation_key="unknown-command",
        operation_type="broker.command", input_digest="a" * 64, **fence)
    with pytest.raises(ValueError, match="unresolved"):
        await task_service.start_operation(created.task.id, operation_key="bypass-inflight",
            operation_type="broker.project_write", input_digest="b" * 64, **fence)
    await task_service.finish_operation(created.task.id, operation.id, state="uncertain", result={}, **fence)
    await task_service.complete(created.task.id, status="failed", summary="Unknown command effect", idempotency_key="failed", **fence)
    await worker_coordinator.release_claim(worker.worker_id, claim.attempt_id, claim_token=claim.claim_token)
    await task_service.continue_task(created.task.id, content="Inspect before further work", origin="test", idempotency_key="new-run")
    next_claim = (await worker_coordinator.claim_next(worker.worker_id)).task
    with pytest.raises(ValueError, match="unresolved"):
        await task_service.start_operation(created.task.id, operation_key="bypass-new-run", operation_type="broker.command",
            input_digest="c" * 64, attempt_id=next_claim.attempt_id, execution_run_id=next_claim.execution_run_id,
            claim_token=next_claim.claim_token)


@pytest.mark.asyncio
@pytest.mark.parametrize("code,reason", [("unauthorized", "subscription_auth_denied"),
    ("usageLimitExceeded", "subscription_quota_exhausted"), ("rateLimitExceeded", "subscription_rate_limited")])
async def test_structured_subscription_denial_selects_api_only_at_next_boundary(tmp_path, monkeypatch, code, reason):
    from app.services.runtime_auth_routes import provider_denial, profile_revision, task_subscription_denial
    subscription = tmp_path / "subscription"
    subscription.mkdir()
    (subscription / "auth.json").write_text("{}")
    monkeypatch.setattr(settings, "codex_subscription_profile", str(subscription))
    monkeypatch.setattr(settings, "codex_subscription_enabled", True)
    monkeypatch.setattr(settings, "openai_api_key", "fixture-key")
    assert provider_denial({"codexErrorInfo": code}) == reason
    assert provider_denial({"message": "quota or unauthorized"}) is None
    assert provider_denial({"codexErrorInfo": {"responseStreamDisconnected": {"httpStatusCode": 401}}}) is None
    task = await task_service.dispatch(goal="Same durable task", origin_surface="test", idempotency_key=code)
    await task_service.record_event(task.task.id, event_type="task.auth.unavailable", idempotency_key="denied",
        payload={"reason": reason, "profile_revision": profile_revision(), "automatic_replay": False})
    denial = await task_subscription_denial(task.task.id)
    assert denial == reason
    selected = await select_route(owner_kind="background", owner_id="next", pid=123, stamp=456,
        api_profile=tmp_path / "api", contained=True, unavailable_reason=denial)
    assert selected.route == "api" and selected.reason == reason
    monkeypatch.setattr(settings, "openai_api_key", "")
    with pytest.raises(RuntimeError, match="No authorized API fallback"):
        await select_route(owner_kind="worker", owner_id="next", pid=123, stamp=456,
            api_profile=tmp_path / "api", contained=True, unavailable_reason=denial)
