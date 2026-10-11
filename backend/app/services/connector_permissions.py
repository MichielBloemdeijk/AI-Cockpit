"""Per-account tool grants and exact-action, durable human approval.

Integrations register supported tools in code. Workers cannot register tools,
change grants, or treat a clarification answered by an agent as approval.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re

from sqlalchemy import select

from app.db.session import session_scope
from app.db.tables import ConnectorToolPermission, TaskQuestion


REGISTERED_TOOLS: dict[tuple[str, str], str] = {}
MODES = frozenset({"allow", "ask_first", "disabled"})


def register_tools(connector: str, tools: dict[str, str]) -> None:
    for tool, description in tools.items():
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", connector) or not re.fullmatch(r"[a-zA-Z0-9_./-]{1,100}", tool):
            raise ValueError("Invalid stable connector/tool identity")
        REGISTERED_TOOLS[connector, tool] = description


def identity(connector: str, account: str, tool: str) -> str:
    if (connector, tool) not in REGISTERED_TOOLS or not account or len(account) > 100:
        raise ValueError("Tool is not registered for this connector/account")
    return sha256(json.dumps([connector, account, tool], separators=(",", ":")).encode()).hexdigest()


async def configure(connector: str, account: str, tool: str, mode: str, *, expected_revision: int) -> dict:
    if mode not in MODES:
        raise ValueError("Permission must be allow, ask_first or disabled")
    key = identity(connector, account, tool)
    async with session_scope() as session:
        from sqlalchemy import text
        await session.execute(text("INSERT OR IGNORE INTO connector_tool_permissions (identity, connector, account, tool, mode, revision) VALUES (:identity, :connector, :account, :tool, 'disabled', 0)"),
            {"identity": key, "connector": connector, "account": account, "tool": tool})
        row = await session.get(ConnectorToolPermission, key, populate_existing=True)
        if row.revision != expected_revision:
            raise ValueError("Connector permission revision is stale")
        row.mode, row.revision = mode, row.revision + 1
        return {"identity": key, "mode": mode, "revision": row.revision}


async def authorize(context, *, connector: str, account: str, tool: str, arguments: dict,
                    operation_key: str) -> dict:
    from app.services.task_service import task_service
    fence = dict(attempt_id=context.attempt_id, execution_run_id=context.execution_run_id,
                 claim_token=context.claim_token)
    await task_service.validate_execution_authority(context.task_id, **fence)
    key = identity(connector, account, tool)
    digest = sha256(json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    if not operation_key or len(operation_key) > 100:
        raise ValueError("A stable operation_key is required")
    async with session_scope() as session:
        policy = await session.get(ConnectorToolPermission, key)
        if policy is None or policy.mode == "disabled":
            raise ValueError("Connector tool is disabled")
        revision = policy.revision
        if policy.mode == "allow":
            return {"identity": key, "revision": revision, "action_digest": digest}
        permission = {"identity": key, "connector": connector, "account": account, "tool": tool,
            "policy_revision": revision, "action_digest": digest, "arguments": arguments,
            "operation_key": operation_key}
        rows = (await session.execute(select(TaskQuestion).where(
            TaskQuestion.task_id == context.task_id, TaskQuestion.asking_run_id == context.execution_run_id,
            TaskQuestion.permission_json.is_not(None)))).scalars().all()
        matching = [row for row in rows if row.permission_json == permission]
        if matching:
            row = matching[-1]
            if row.state == "answered" and (row.answer or "").casefold() == "approve":
                return {"identity": key, "revision": revision, "action_digest": digest, "question_id": row.id}
            if row.state == "answered":
                raise ValueError("The user denied this action")
            return {"status": "waiting_for_input", "question_id": row.id}
    question = await task_service.ask_question(context.task_id,
        question=f"Approve {connector}/{tool} for account {account}: {json.dumps(arguments, ensure_ascii=False)}",
        permission=permission, idempotency_key=f"permission:{context.execution_run_id}:{key}:{revision}:{digest}:{operation_key}", **fence)
    return {"status": "waiting_for_input", "question_id": question.id}


async def execute(context, *, connector: str, account: str, tool: str, arguments: dict,
                  operation_key: str, invoke) -> dict:
    """Adapter seam used by supported integrations, including mock acceptance.

Approval is not success: effects still use the durable uncertain-operation
contract. A new attempt may replay a committed receipt, never an uncertain send.
"""
    grant = await authorize(context, connector=connector, account=account, tool=tool,
                            arguments=arguments, operation_key=operation_key)
    if grant.get("status"):
        return grant
    from app.services.task_service import task_service
    fence = dict(attempt_id=context.attempt_id, execution_run_id=context.execution_run_id, claim_token=context.claim_token)
    async with session_scope() as session:
        row = await session.get(ConnectorToolPermission, grant["identity"])
        if row is None or row.revision != grant["revision"] or row.mode == "disabled":
            raise ValueError("Connector permission changed before execution")
    operation = await task_service.start_operation(context.task_id,
        operation_key=f"connector:{context.execution_run_id}:{operation_key}", operation_type="connector.tool@1",
        input_digest=sha256(f"{grant['identity']}:{grant['action_digest']}".encode()).hexdigest(),
        intent={"connector": connector, "account": account, "tool": tool, "grant": grant}, **fence)
    if operation.state == "succeeded":
        return dict(operation.result_json or {})
    result = await invoke(arguments)
    await task_service.finish_operation(context.task_id, operation.id, state="succeeded", result=result, **fence)
    return result
