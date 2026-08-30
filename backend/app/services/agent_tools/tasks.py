"""Read/control tools over first-class global tasks."""
from __future__ import annotations

import json
from typing import Any

from app.services.agent_tools.base import (
    AgentTool,
    ToolExecutionContext,
    ToolExecutionResult,
    _tool_property,
    _tool_schema,
)
from app.services.task_service import TERMINAL_TASK_STATUSES, TaskSnapshot, task_service


def _render(snapshot: TaskSnapshot) -> dict[str, Any]:
    task = snapshot.task
    return {
        "task_id": task.id,
        "title": " ".join(task.goal.split())[:96],
        "goal": task.goal,
        "status": task.status,
        "importance": task.importance,
        "presentation_policy": task.presentation_policy,
        "queue_position": snapshot.queue_position,
        "terminal_summary": task.terminal_summary,
        "created_at": task.created_at.isoformat(),
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    }


async def _list(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolExecutionResult:
    include_finished = bool(arguments.get("include_finished"))
    snapshots = [
        snapshot
        for snapshot in await task_service.list_tasks()
        if snapshot.task.origin_conversation_id == context.conversation_id
        and (include_finished or snapshot.task.status not in TERMINAL_TASK_STATUSES)
    ]
    return ToolExecutionResult(
        tool="list_tasks",
        output=json.dumps([_render(snapshot) for snapshot in snapshots], ensure_ascii=False),
        metadata={"count": len(snapshots), "read_only": True},
    )


async def _owned_task(context: ToolExecutionContext, task_id: str) -> TaskSnapshot:
    detail = await task_service.get_task(task_id)
    if detail is None or detail.task.origin_conversation_id != context.conversation_id:
        raise ValueError("Task not found")
    return TaskSnapshot(task=detail.task, queue_position=detail.queue_position)


async def _get(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolExecutionResult:
    snapshot = await _owned_task(context, str(arguments.get("task_id") or ""))
    return ToolExecutionResult(
        tool="get_task",
        output=json.dumps(_render(snapshot), ensure_ascii=False),
        metadata={"read_only": True},
    )


async def _cancel(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolExecutionResult:
    task_id = str(arguments.get("task_id") or "")
    await _owned_task(context, task_id)
    snapshot = await task_service.cancel(
        task_id,
        idempotency_key=f"agent-tool:{context.run_id or context.conversation_id}:{task_id}",
    )
    return ToolExecutionResult(
        tool="cancel_task",
        output=json.dumps(_render(snapshot), ensure_ascii=False),
        metadata={"status": snapshot.task.status},
    )


TASK_TOOLS = [
    AgentTool(
        name="list_tasks",
        description="List first-class tasks originating from this conversation.",
        input_schema=_tool_schema({"include_finished": {"type": "boolean"}}),
        executor=_list,
        read_only=True,
    ),
    AgentTool(
        name="get_task",
        description="Inspect one first-class task originating from this conversation.",
        input_schema=_tool_schema(
            {"task_id": _tool_property("string", "Stable global task ID.")},
            required=["task_id"],
        ),
        executor=_get,
        read_only=True,
    ),
    AgentTool(
        name="cancel_task",
        description="Cancel one first-class task originating from this conversation.",
        input_schema=_tool_schema(
            {"task_id": _tool_property("string", "Stable global task ID.")},
            required=["task_id"],
        ),
        executor=_cancel,
    ),
]


__all__ = ["TASK_TOOLS"]
