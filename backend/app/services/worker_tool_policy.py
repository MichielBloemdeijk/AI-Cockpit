"""Worker tool selection is configuration, never a grant of authority."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.db.session import session_scope
from app.db.tables import TaskExecutionRun, WorkerToolPolicy
from app.config import settings


# The pinned runtime couples shell execution and native filesystem mutation.
# Do not pretend these can be secured independently by hiding a tool name.
NATIVE_GROUPS = frozenset({"native.local", "native.research", "native.subagents", "native.skills"})
DEFAULT_TOOLS = ["native.local", "native.research", "native.subagents", "native.skills"]


class ToolOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)


class WorkerPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    defaults: list[str] = Field(default_factory=lambda: list(DEFAULT_TOOLS))
    disabled: list[str] = Field(default_factory=list)
    command_network: bool = False
    approved_skills: list[str] = Field(default_factory=list)


def identifiers(values: list[str]) -> set[str]:
    if len(values) != len(set(values)) or not set(values) <= NATIVE_GROUPS:
        raise ValueError("Unknown or duplicate native capability group")
    return set(values)


def normalize_overrides(value: dict | None) -> dict:
    overrides = ToolOverrides.model_validate(value or {})
    additions, removals = identifiers(overrides.add), identifiers(overrides.remove)
    if additions & removals:
        raise ValueError("A tool cannot be added and removed in the same layer")
    return overrides.model_dump()


def resolve_tools(policy: dict, *, workflow: dict | None = None, task: dict | None = None) -> list[str]:
    policy = WorkerPolicy.model_validate(policy)
    selected = identifiers(policy.defaults)
    forbidden = identifiers(policy.disabled)
    for layer in (workflow, task):
        override = normalize_overrides(layer)
        selected = (selected - set(override["remove"])) | set(override["add"])
    return sorted(selected - forbidden)


def policy_digest(value: dict) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def read_policy() -> tuple[int, dict]:
    async with session_scope() as session:
        row = await session.get(WorkerToolPolicy, "worker")
        return (row.revision, dict(row.configuration_json)) if row else (0, WorkerPolicy().model_dump())


async def save_policy(value: dict, *, expected_revision: int) -> tuple[int, dict]:
    parsed = WorkerPolicy.model_validate(value).model_dump()
    resolve_tools(parsed)
    if any(not Path(path).is_absolute() for path in parsed["approved_skills"]):
        raise ValueError("Approved skills must use absolute SKILL.md paths")
    skills = [str(Path(path).resolve(strict=True)) for path in parsed["approved_skills"]]
    if any(not Path(path).is_file() or Path(path).name != "SKILL.md" for path in skills):
        raise ValueError("Approved skills must name existing SKILL.md files")
    parsed["approved_skills"] = sorted(set(skills))
    async with session_scope() as session:
        # SQLite serializes the write before comparing revisions.
        from sqlalchemy import text
        await session.execute(text("INSERT OR IGNORE INTO worker_tool_policy (id, revision, configuration_json) VALUES ('worker', 0, '{}')"))
        row = await session.get(WorkerToolPolicy, "worker", populate_existing=True)
        if row.revision != expected_revision:
            raise ValueError("Worker policy revision is stale")
        row.configuration_json = parsed
        row.revision += 1
        return row.revision, parsed


async def freeze_tools(context) -> dict:
    from app.services.task_service import task_service
    await task_service.validate_execution_authority(context.task_id, attempt_id=context.attempt_id,
        execution_run_id=context.execution_run_id, claim_token=context.claim_token)
    revision, policy = await read_policy()
    config = context.execution_config
    resolved = {"schema": "worker-tools.v1", "policy_revision": revision,
        "tools": resolve_tools(policy, workflow=(config.get("workflow") or {}).get("tools"), task=config.get("tools")),
        "command_network": policy["command_network"], "approved_skills": policy["approved_skills"],
        "local_execution_mode": settings.task_command_mode}
    async with session_scope() as session:
        # Fence and configuration write share the task lock.
        task = await task_service._lock_task(session, context.task_id)
        await task_service._assert_fence(session, task, attempt_id=context.attempt_id,
            execution_run_id=context.execution_run_id, claim_token=context.claim_token)
        run = await session.get(TaskExecutionRun, context.execution_run_id)
        current = dict(run.configuration_json or {})
        if "resolved_tools" in current:
            return current["resolved_tools"]
        current["resolved_tools"] = resolved
        run.configuration_json = current
    return resolved


def sandbox_policy(roots: list[Path], *, local: bool, network: bool) -> dict:
    if not local:
        return {"type": "readOnly"}
    return {"type": "workspaceWrite", "writableRoots": sorted({str(root.resolve()) for root in roots}),
        "networkAccess": network, "excludeTmpdirEnvVar": True, "excludeSlashTmp": True}


def native_overrides(resolved: dict) -> list[str]:
    selected = set(resolved["tools"])
    return [f"agents.enabled={'true' if 'native.subagents' in selected else 'false'}",
        "features.apps=false", "features.plugins=false", "features.remote_plugin=false",
        "features.hooks=false", "features.skill_mcp_dependency_install=false",
        f'web_search={json.dumps("live" if "native.research" in selected else "disabled")}',
        f"features.shell_tool={'true' if 'native.local' in selected else 'false'}",
        f"features.unified_exec={'true' if 'native.local' in selected else 'false'}"]
