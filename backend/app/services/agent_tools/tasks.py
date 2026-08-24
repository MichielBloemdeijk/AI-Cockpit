"""Native task-management tools exposed to the agent loop."""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any

from app.services.agent_tools.base import AgentTool, ToolExecutionContext, ToolExecutionResult, _tool_property, _tool_schema
from app.services.background_tasks import cancel_task, get_task, list_tasks, publish_update


def _render(snapshot: Any) -> str:
    if is_dataclass(snapshot):
        return json.dumps(asdict(snapshot), default=str)
    return json.dumps(snapshot, default=str)


async def _publish(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolExecutionResult:
    event = await publish_update(
        context.run_id or "",
        str(arguments.get("summary") or ""),
        str(arguments.get("importance") or "normal"),
    )
    return ToolExecutionResult(
        tool="task_publish_update",
        output="Task update published.",
        metadata={"event_id": event.id, "sequence": event.sequence},
    )


async def _list(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolExecutionResult:
    tasks = await list_tasks(context.conversation_id, include_finished=bool(arguments.get("include_finished")))
    return ToolExecutionResult(tool="list_background_tasks", output=json.dumps([_render(task) for task in tasks]), metadata={"count": len(tasks)})


async def _get(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolExecutionResult:
    task = await get_task(context.conversation_id, str(arguments.get("run_id") or ""))
    if task is None:
        raise ValueError("Background task not found")
    return ToolExecutionResult(tool="get_background_task", output=_render(task), metadata={})


async def _cancel(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolExecutionResult:
    task = await cancel_task(context.conversation_id, str(arguments.get("run_id") or ""))
    if task is None:
        raise ValueError("Background task not found")
    return ToolExecutionResult(tool="cancel_background_task", output=_render(task), metadata={"status": task.status})


TASK_TOOLS = [
    AgentTool(
        name="task_publish_update",
        description="Publish a material milestone from the current background task.",
        input_schema=_tool_schema(
            {"summary": _tool_property("string", "Short user-facing milestone."), "importance": {"type": "string", "enum": ["low", "normal", "high", "urgent"]}},
            required=["summary", "importance"],
        ),
        executor=_publish,
    ),
    AgentTool(
        name="list_background_tasks",
        description="List background tasks belonging to this voice conversation.",
        input_schema=_tool_schema({"include_finished": {"type": "boolean"}}),
        executor=_list,
        read_only=True,
    ),
    AgentTool(
        name="get_background_task",
        description="Inspect one background task belonging to this voice conversation.",
        input_schema=_tool_schema({"run_id": _tool_property("string", "Task run ID.")}, required=["run_id"]),
        executor=_get,
        read_only=True,
    ),
    AgentTool(
        name="cancel_background_task",
        description="Cancel one background task belonging to this voice conversation.",
        input_schema=_tool_schema({"run_id": _tool_property("string", "Task run ID.")}, required=["run_id"]),
        executor=_cancel,
    ),
]
