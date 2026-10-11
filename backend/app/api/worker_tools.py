"""Owner-only policy configuration; no worker/MCP configuration surface."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from app.services.auth import require_auth
from app.config import settings
from app.services.worker_tool_policy import WorkerPolicy, read_policy, save_policy, NATIVE_GROUPS
from app.services import connector_permissions

router = APIRouter(prefix="/api/worker-tools", tags=["worker-tools"], dependencies=[Depends(require_auth)])


class PolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int
    policy: WorkerPolicy


class PermissionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    connector: str
    account: str
    tool: str
    mode: str
    expected_revision: int


@router.get("")
async def get_policy():
    revision, policy = await read_policy()
    return {"revision": revision, "policy": policy, "groups": sorted(NATIVE_GROUPS),
        "local_execution_mode": settings.task_command_mode,
        "connectors": [{"connector": connector, "tool": tool, "description": description}
            for (connector, tool), description in sorted(connector_permissions.REGISTERED_TOOLS.items())]}


@router.put("")
async def put_policy(request: PolicyUpdate):
    try:
        revision, policy = await save_policy(request.policy.model_dump(), expected_revision=request.expected_revision)
        return {"revision": revision, "policy": policy}
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.put("/permissions")
async def put_permission(request: PermissionUpdate):
    try:
        return await connector_permissions.configure(**request.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
