from __future__ import annotations

from typing import Any, Literal

from app.services.agent_tools.apps import APP_TOOLS
from app.services.agent_tools.base import AgentTool
from app.services.agent_tools.base import ToolExecutionContext
from app.services.agent_tools.base import ToolExecutionResult
from app.services.agent_tools.execution import PYTHON_EXECUTION_TOOL
from app.services.agent_tools.execution import SHELL_COMMAND_TOOL
from app.services.agent_tools.filesystem import FILESYSTEM_TOOLS


TOOL_REGISTRY: dict[str, AgentTool] = {
    tool.name: tool
    for tool in [
        *FILESYSTEM_TOOLS,
        PYTHON_EXECUTION_TOOL,
        *APP_TOOLS,
        SHELL_COMMAND_TOOL,
    ]
}


def get_agent_tool_definitions() -> list[AgentTool]:
    return list(TOOL_REGISTRY.values())


def get_agent_tool_provider_definitions(provider_format: Literal["openai", "anthropic"] = "openai") -> list[dict[str, Any]]:
    return [tool.to_provider_definition(provider_format) for tool in get_agent_tool_definitions()]


async def execute_agent_tool(
    *,
    context: ToolExecutionContext,
    tool: str,
    arguments: dict[str, Any],
) -> ToolExecutionResult:
    tool_definition = TOOL_REGISTRY.get(tool)
    if tool_definition is None:
        raise ValueError(f"Unsupported tool: {tool}")
    return await tool_definition.execute(context=context, arguments=arguments)


async def execute_chat_tool(
    *,
    context: ToolExecutionContext,
    tool: str,
    query: str | None = None,
    code: str | None = None,
    working_directory: str | None = None,
) -> ToolExecutionResult:
    if tool not in {"workspace_search", "python_execution"}:
        raise ValueError(f"Unsupported tool: {tool}")
    arguments: dict[str, Any] = {}
    if query is not None:
        arguments["query"] = query
    if code is not None:
        arguments["code"] = code
    if working_directory is not None:
        arguments["working_directory"] = working_directory
    return await execute_agent_tool(context=context, tool=tool, arguments=arguments)


__all__ = [
    "TOOL_REGISTRY",
    "execute_agent_tool",
    "execute_chat_tool",
    "get_agent_tool_definitions",
    "get_agent_tool_provider_definitions",
]