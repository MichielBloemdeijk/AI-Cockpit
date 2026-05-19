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
from app.services.agent_tools.base import emit_tool_progress


SEARCH_RESULT_LIMIT = 20


def _line_count(content: str) -> int:
    return len(content.splitlines()) if content else 0


async def run_workspace_search(query: str, *, context: ToolExecutionContext) -> ToolExecutionResult:
    normalized_query = query.strip().lower()
    if not normalized_query:
        raise ValueError("Search query is required")

    matches: list[dict[str, str | int]] = []
    ignored_dirs = {".git", "node_modules", ".next", ".venv", "dist", "build", "__pycache__"}
    scanned_files = 0
    await emit_tool_progress(context, f"Searching for {query.strip()!r}")
    for root in context.read_roots:
        await emit_tool_progress(context, f"Scanning {_display_path(root)}")
        for file_path in root.rglob("*"):
            if len(matches) >= SEARCH_RESULT_LIMIT:
                break
            if not file_path.is_file():
                continue
            if any(part in ignored_dirs for part in file_path.parts):
                continue
            if file_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".db", ".sqlite"}:
                continue
            scanned_files += 1
            if scanned_files % 100 == 0:
                await emit_tool_progress(context, f"Scanned {scanned_files} files")
            try:
                text = file_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if normalized_query in line.lower():
                    display_path = _display_path(file_path)
                    matches.append(
                        {
                            "path": display_path,
                            "line_number": line_number,
                            "line": line.strip(),
                        }
                    )
                    if len(matches) <= 3:
                        await emit_tool_progress(context, f"Found a match in {display_path}")
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

    await emit_tool_progress(
        context,
        f"Found {len(matches)} matches" if matches else f"No matches found for {query.strip()!r}",
    )

    return ToolExecutionResult(
        tool="workspace_search",
        output=output,
        metadata={"query": query.strip(), "matches": matches},
    )


async def run_file_read(path: str, *, context: ToolExecutionContext) -> ToolExecutionResult:
    if not path.strip():
        raise ValueError("File path is required")

    await emit_tool_progress(context, f"Resolving {path.strip()}")
    file_path = _resolve_read_path(path, context=context)
    if not file_path.is_file():
        raise ValueError(f"File not found: {path}")

    rendered_path = _display_path(file_path)
    await emit_tool_progress(context, f"Reading {rendered_path}")
    try:
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Only UTF-8 text files can be read by this tool") from exc

    await emit_tool_progress(context, f"Loaded {len(content.encode('utf-8'))} bytes from {rendered_path}")
    output = f"Read {rendered_path}:\n\n{_truncate_output(content)}"
    return ToolExecutionResult(
        tool="file_read",
        output=output,
        metadata={
            "path": rendered_path,
            "size": len(content.encode("utf-8")),
        },
    )


async def run_file_write(path: str, content: str, *, context: ToolExecutionContext) -> ToolExecutionResult:
    if not path.strip():
        raise ValueError("File path is required")

    await emit_tool_progress(context, f"Resolving destination {path.strip()}")
    file_path = _resolve_write_path(path, context=context)
    if not file_path.parent.exists():
        await emit_tool_progress(context, f"Creating { _display_path(file_path.parent) }")
    file_path.parent.mkdir(parents=True, exist_ok=True)
    rendered_path = _display_path(file_path)
    byte_count = len(content.encode("utf-8"))
    line_count = _line_count(content)
    if line_count > 0:
        await emit_tool_progress(context, f"Writing {rendered_path} ({line_count} lines)")
    else:
        await emit_tool_progress(context, f"Writing {byte_count} bytes to {rendered_path}")
    file_path.write_text(content, encoding="utf-8")
    if line_count > 0:
        await emit_tool_progress(context, f"Updated {rendered_path} ({line_count} lines)")
    else:
        await emit_tool_progress(context, f"Saved {rendered_path}")

    return ToolExecutionResult(
        tool="file_write",
        output=(
            f"Updated {rendered_path} ({line_count} lines)."
            if line_count > 0
            else f"Wrote {byte_count} bytes to {rendered_path}."
        ),
        metadata={
            "path": rendered_path,
            "size": byte_count,
            "line_count": line_count,
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