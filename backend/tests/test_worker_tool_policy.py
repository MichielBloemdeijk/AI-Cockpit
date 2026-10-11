from types import SimpleNamespace
import pytest

from app.config import settings
from app.services.worker_tool_policy import WorkerPolicy, resolve_tools, normalize_overrides, save_policy, read_policy, freeze_tools, sandbox_policy, native_overrides
from app.services.task_service import task_service
from app.services.worker_coordinator import worker_coordinator
from app.services import connector_permissions as permissions


def test_selection_layers_restore_clutter_removal_but_cannot_restore_disabled_tools():
    policy = WorkerPolicy(disabled=["native.research"]).model_dump()
    selected = resolve_tools(policy, workflow={"remove": ["native.local", "native.research"]},
        task={"add": ["native.local", "native.research"], "remove": ["native.skills"]})
    assert selected == ["native.local", "native.subagents"]
    with pytest.raises(ValueError):
        normalize_overrides({"add": ["native.local"], "remove": ["native.local"]})
    with pytest.raises(ValueError):
        normalize_overrides({"add": ["native.full_access"]})
    assert sandbox_policy([], local=False, network=True) == {"type": "readOnly"}
    overrides = native_overrides({"tools": ["native.research"]})
    assert 'web_search="live"' in overrides and "features.apps=false" in overrides
    assert "features.shell_tool=false" in overrides and "agents.enabled=false" in overrides


async def claimed(key):
    created = await task_service.dispatch(goal="test", origin_surface="test", idempotency_key=key)
    worker = await worker_coordinator.activate(process_identity=key)
    claim = (await worker_coordinator.claim_next(worker.worker_id)).task
    return SimpleNamespace(task_id=created.task.id, execution_run_id=claim.execution_run_id,
        attempt_id=claim.attempt_id, claim_token=claim.claim_token,
        execution_config=claim.execution_config, workspace_path=claim.workspace_path), worker


@pytest.mark.asyncio
async def test_policy_compare_and_swap_and_run_freeze(monkeypatch):
    monkeypatch.setattr(settings, "task_executor", "codex")
    context, worker = await claimed("policy-freeze")
    frozen = await freeze_tools(context)
    revision, policy = await read_policy()
    await save_policy(policy | {"defaults": ["native.research"]}, expected_revision=revision)
    assert await freeze_tools(context) == frozen
    with pytest.raises(ValueError, match="stale"):
        await save_policy(policy, expected_revision=revision)


@pytest.mark.asyncio
async def test_connector_read_allow_send_exact_human_approval_and_revocation(monkeypatch):
    monkeypatch.setattr(settings, "task_executor", "codex")
    monkeypatch.setattr(permissions, "REGISTERED_TOOLS", {})
    permissions.register_tools("mail", {"read": "Read messages", "send": "Send a message"})
    context, worker = await claimed("permissions")
    await permissions.configure("mail", "primary", "read", "allow", expected_revision=0)
    await permissions.configure("mail", "primary", "send", "ask_first", expected_revision=0)
    called = []
    async def invoke(arguments):
        called.append(arguments)
        return {"ok": True}
    assert await permissions.execute(context, connector="mail", account="primary", tool="read",
        arguments={}, operation_key="read", invoke=invoke) == {"ok": True}
    pending = await permissions.execute(context, connector="mail", account="primary", tool="send",
        arguments={"to": "test@example.com", "body": "Hello"}, operation_key="send", invoke=invoke)
    assert pending["status"] == "waiting_for_input" and called == [{}]
    detail = await task_service.get_task(context.task_id)
    assert detail.questions[0].permission_json["arguments"]["body"] == "Hello"
    # An ordinary clarification answer cannot accidentally approve an action.
    with pytest.raises(ValueError, match="approve or deny"):
        await task_service.answer_question(context.task_id, pending["question_id"], answer="looks good", idempotency_key="ambiguous")
    await worker_coordinator.release_claim(worker.worker_id, context.attempt_id)
    await task_service.answer_question(context.task_id, pending["question_id"], answer="approve", idempotency_key="approve")
    claim = (await worker_coordinator.claim_next(worker.worker_id)).task
    context.attempt_id, context.claim_token = claim.attempt_id, claim.claim_token
    assert await permissions.execute(context, connector="mail", account="primary", tool="send",
        arguments={"to": "test@example.com", "body": "Hello"}, operation_key="send", invoke=invoke) == {"ok": True}
    assert len(called) == 2
    # Same successful receipt is replayed without a second send.
    assert await permissions.execute(context, connector="mail", account="primary", tool="send",
        arguments={"to": "test@example.com", "body": "Hello"}, operation_key="send", invoke=invoke) == {"ok": True}
    assert len(called) == 2
    await permissions.configure("mail", "primary", "send", "disabled", expected_revision=1)
    with pytest.raises(ValueError, match="disabled"):
        await permissions.authorize(context, connector="mail", account="primary", tool="send",
            arguments={"body": "Hello"}, operation_key="different")


@pytest.mark.asyncio
async def test_unregistered_connector_cannot_gain_access(monkeypatch):
    monkeypatch.setattr(permissions, "REGISTERED_TOOLS", {})
    with pytest.raises(ValueError, match="not registered"):
        await permissions.configure("mail", "primary", "send", "allow", expected_revision=0)


@pytest.mark.asyncio
async def test_connector_uncertain_effect_cannot_be_bypassed_with_a_new_key(monkeypatch):
    monkeypatch.setattr(settings, "task_executor", "codex")
    monkeypatch.setattr(permissions, "REGISTERED_TOOLS", {})
    permissions.register_tools("mail", {"send": "Send a message"})
    await permissions.configure("mail", "primary", "send", "allow", expected_revision=0)
    context, _ = await claimed("uncertain-connector")
    calls = []
    async def uncertain(arguments):
        calls.append(arguments)
        raise RuntimeError("Transport lost after send")
    with pytest.raises(RuntimeError, match="after send"):
        await permissions.execute(context, connector="mail", account="primary", tool="send",
            arguments={"body": "Hello"}, operation_key="send", invoke=uncertain)
    with pytest.raises(ValueError, match="unresolved uncertain"):
        await permissions.execute(context, connector="mail", account="primary", tool="send",
            arguments={"body": "Hello"}, operation_key="send-again", invoke=uncertain)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_changed_connector_arguments_require_a_new_human_decision(monkeypatch):
    monkeypatch.setattr(settings, "task_executor", "codex")
    monkeypatch.setattr(permissions, "REGISTERED_TOOLS", {})
    permissions.register_tools("mail", {"send": "Send a message"})
    await permissions.configure("mail", "primary", "send", "ask_first", expected_revision=0)
    context, worker = await claimed("changed-connector")
    first = await permissions.authorize(context, connector="mail", account="primary", tool="send",
        arguments={"body": "First"}, operation_key="send")
    await worker_coordinator.release_claim(worker.worker_id, context.attempt_id)
    await task_service.answer_question(context.task_id, first["question_id"], answer="approve", idempotency_key="approve")
    claim = (await worker_coordinator.claim_next(worker.worker_id)).task
    context.attempt_id, context.claim_token = claim.attempt_id, claim.claim_token
    second = await permissions.authorize(context, connector="mail", account="primary", tool="send",
        arguments={"body": "Changed"}, operation_key="send")
    assert second["status"] == "waiting_for_input" and second["question_id"] != first["question_id"]


@pytest.mark.asyncio
async def test_background_context_cannot_approve_a_permission_question(monkeypatch):
    from test_task_orchestration import ready, rows
    from app.services.task_orchestration import DecisionTools
    from app.db.tables import OrchestrationAction
    monkeypatch.setattr(settings, "task_orchestration_enabled", True)
    task = (await task_service.dispatch(goal="Send a report", origin_surface="test", idempotency_key="human-only")).task
    question = await task_service.ask_question(task.id, question="Approve this exact send?",
        permission={"tool": "send", "arguments": {"body": "Report"}}, idempotency_key="permission")
    service = await ready()
    tools = DecisionTools(await service.claim())
    with pytest.raises(ValueError, match="explicit human decision"):
        await tools.call("answer", {"answer": "approve", "operation_key": "agent-approval", "reason": "The goal asks for a send"})
    assert not await rows(OrchestrationAction)
    assert (await task_service.get_task(task.id)).questions[0].state == "asked"
    await task_service.answer_question(task.id, question.id, answer="approve", idempotency_key="human-approval")
