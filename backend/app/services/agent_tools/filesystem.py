from __future__ import annotations

from typing import Any

from app.services.agent_tools.base import AgentTool
from app.services.agent_tools.base import ToolExecutionContext
from app.services.agent_tools.base import ToolExecutionResult
from app.services.agent_tools.base import _display_path
from app.services.agent_tools.base import _resolve_read_path
from app.services.agent_tools.base import _resolve_write_path
from app.services.agent_tools.base import _tool_property
from app.services.agent_tools.base import _tool_schema
from app.services.agent_tools.base import _truncate_output


SEARCH_RESULT_LIMIT = 20


async def run_workspace_search(query: str, *, context: ToolExecutionContext) -> ToolExecutionResult:
    normalized_query = query.strip().lower()
    if not normalized_query:
        raise ValueError("Search query is required")

    matches: list[dict[str, str | int]] = []
    ignored_dirs = {".git", "node_modules", ".next", ".venv", "dist", "build", "__pycache__"}
    for root in context.read_roots:
        for file_path in root.rglob("*"):
            if len(matches) >= SEARCH_RESULT_LIMIT:
                break
            if not file_path.is_file():
                continue
            if any(part in ignored_dirs for part in file_path.parts):
                continue
            if file_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".db", ".sqlite"}:
                continue
            try:
                text = file_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if normalized_query in line.lower():
                    matches.append(
                        {
                            "path": _display_path(file_path),
                            "line_number": line_number,
                            "line": line.strip(),
                        }
                    )
                    if len(matches) >= SEARCH_RESULT_LIMIT:
                        break
        if len(matches) >= SEARCH_RESULT_LIMIT:
            break

    if not matches:
        output = f"No matches found for '{query.strip()}'."
    else:
        rendered_matches = [
            f"- {match['path']}:{match['line_number']} - {match['line']}"
            for match in matches
        ]
        output = "Workspace search results:\n" + "\n".join(rendered_matches)

    return ToolExecutionResult(
        tool="workspace_search",
        output=output,
        metadata={"query": query.strip(), "matches": matches},
    )


async def run_file_read(path: str, *, context: ToolExecutionContext) -> ToolExecutionResult:
    if not path.strip():
        raise ValueError("File path is required")

    file_path = _resolve_read_path(path, context=context)
    if not file_path.is_file():
        raise ValueError(f"File not found: {path}")

    try:
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Only UTF-8 text files can be read by this tool") from exc

    output = f"Read {_display_path(file_path)}:\n\n{_truncate_output(content)}"
    return ToolExecutionResult(
        tool="file_read",
        output=output,
        metadata={
            "path": _display_path(file_path),
            "size": len(content.encode("utf-8")),
        },
    )


async def run_file_write(path: str, content: str, *, context: ToolExecutionContext) -> ToolExecutionResult:
    if not path.strip():
        raise ValueError("File path is required")

    file_path = _resolve_write_path(path, context=context)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    rendered_path = _display_path(file_path)

    return ToolExecutionResult(
        tool="file_write",
        output=f"Wrote {len(content.encode('utf-8'))} bytes to {rendered_path}.",
        metadata={
            "path": rendered_path,
            "size": len(content.encode("utf-8")),
            "workspace_path": context.workspace_path,
        },
    )


async def _execute_workspace_search(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolExecutionResult:
    return await run_workspace_search(str(arguments.get("query", "")), context=context)


async def _execute_file_read(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolExecutionResult:
    return await run_file_read(str(arguments.get("path", "")), context=context)


async def _execute_file_write(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolExecutionResult:
    return await run_file_write(
        str(arguments.get("path", "")),
        str(arguments.get("content", "")),
        context=context,
    )


FILESYSTEM_TOOLS: list[AgentTool] = [
    AgentTool(
        name="workspace_search",
        description="Search allowed read roots for a text query and return matching file lines.",
        input_schema=_tool_schema(
            {
                "query": _tool_property("string", "Case-insensitive text query to search for in allowed read roots."),
            },
            required=["query"],
        ),
        executor=_execute_workspace_search,
        read_only=True,
    ),
    AgentTool(
        name="file_read",
        description="Read a UTF-8 text file from an allowed read root.",
        input_schema=_tool_schema(
            {
                "path": _tool_property("string", "Path to a UTF-8 text file inside an allowed read root."),
            },
            required=["path"],
        ),
        executor=_execute_file_read,
        read_only=True,
    ),
    AgentTool(
        name="file_write",
        description="Write or create a UTF-8 text file inside the allowed write roots only.",
        input_schema=_tool_schema(
            {
                "path": _tool_property("string", "Destination path inside an allowed write root."),
                "content": _tool_property("string", "Full UTF-8 file contents to write."),
            },
            required=["path", "content"],
        ),
        executor=_execute_file_write,
        read_only=False,
    ),
]


__all__ = [
    "FILESYSTEM_TOOLS",
    "run_file_read",
    "run_file_write",
    "run_workspace_search",
]