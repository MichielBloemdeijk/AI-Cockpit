from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

from app.config import settings


MAX_TOOL_OUTPUT_CHARS = 12_000


@dataclass(slots=True)
class ToolExecutionResult:
    tool: str
    output: str
    metadata: dict[str, Any]


@dataclass(slots=True)
class ToolExecutionContext:
    conversation_id: str
    run_id: str | None
    workspace_path: str
    workspace_root: Path
    read_roots: list[Path]
    write_roots: list[Path]


ToolExecutor = Callable[[ToolExecutionContext, dict[str, Any]], Awaitable[ToolExecutionResult]]
ToolResultSerializer = Callable[[ToolExecutionResult], str]


@dataclass(slots=True)
class AgentTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    executor: ToolExecutor
    read_only: bool = False
    allows_parallel: bool = False
    result_serializer: ToolResultSerializer | None = None
    ui_event_name: str | None = None

    @property
    def arguments(self) -> dict[str, str]:
        properties = self.input_schema.get("properties") if isinstance(self.input_schema, dict) else {}
        required = set(self.input_schema.get("required") or []) if isinstance(self.input_schema, dict) else set()
        hints: dict[str, str] = {}
        if not isinstance(properties, dict):
            return hints
        for name, schema in properties.items():
            if not isinstance(schema, dict):
                hints[name] = "any"
                continue
            type_name = str(schema.get("type") or "any")
            if name not in required:
                type_name += " (optional)"
            hints[name] = type_name
        return hints

    async def execute(self, *, context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolExecutionResult:
        return await self.executor(context, arguments)

    def serialize_result(self, result: ToolExecutionResult) -> str:
        if self.result_serializer is not None:
            return self.result_serializer(result)
        return result.output

    def to_provider_definition(self, provider_format: Literal["openai", "anthropic"] = "openai") -> dict[str, Any]:
        if provider_format == "anthropic":
            return {
                "name": self.name,
                "description": self.description,
                "input_schema": self.input_schema,
            }
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


def _tool_schema(
    properties: dict[str, dict[str, Any]],
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def _tool_property(type_name: str, description: str) -> dict[str, Any]:
    return {
        "type": type_name,
        "description": description,
    }


def _workspace_root() -> Path:
    return settings.backend_root.parent.resolve()


def _search_roots() -> list[Path]:
    configured = [path.resolve() for path in settings.allowed_dir_list]
    if configured:
        return configured
    return [_workspace_root()]


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(_workspace_root()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _resolve_under_roots(path_value: str, roots: list[Path], *, base: Path) -> Path:
    raw_path = Path(path_value).expanduser()
    if raw_path.is_absolute():
        candidates = [raw_path.resolve()]
    else:
        repo_root = _workspace_root()
        base_candidate = (base / raw_path).resolve()
        repo_candidate = (repo_root / raw_path).resolve()
        repo_anchor = repo_root / raw_path.parts[0] if raw_path.parts else repo_root
        if repo_anchor.is_dir():
            candidates = [repo_candidate]
        elif repo_anchor.exists():
            candidates = [repo_candidate, base_candidate]
        else:
            candidates = [base_candidate, repo_candidate]
    seen: set[str] = set()
    for resolved in candidates:
        key = resolved.as_posix()
        if key in seen:
            continue
        seen.add(key)
        for root in roots:
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                continue
    allowed = ", ".join(root.as_posix() for root in roots)
    raise ValueError(f"Path '{path_value}' is outside the allowed roots: {allowed}")


def _resolve_read_path(path_value: str, *, context: ToolExecutionContext) -> Path:
    return _resolve_under_roots(path_value, context.read_roots, base=context.workspace_root)


def _resolve_write_path(path_value: str, *, context: ToolExecutionContext) -> Path:
    return _resolve_under_roots(path_value, context.write_roots, base=context.workspace_root)


def build_tool_context(conversation_id: str, workspace_path: str, *, run_id: str | None = None) -> ToolExecutionContext:
    workspace_root = Path(workspace_path)
    if not workspace_root.is_absolute():
        workspace_root = (_workspace_root() / workspace_root).resolve()
    return ToolExecutionContext(
        conversation_id=conversation_id,
        run_id=run_id,
        workspace_path=workspace_path,
        workspace_root=workspace_root,
        read_roots=_search_roots(),
        write_roots=[workspace_root],
    )


def _truncate_output(value: str) -> str:
    if len(value) <= MAX_TOOL_OUTPUT_CHARS:
        return value
    return value[:MAX_TOOL_OUTPUT_CHARS] + "\n\n...[truncated]"


__all__ = [
    "AgentTool",
    "ToolExecutionContext",
    "ToolExecutionResult",
    "build_tool_context",
    "_display_path",
    "_resolve_read_path",
    "_resolve_write_path",
    "_tool_property",
    "_tool_schema",
    "_truncate_output",
]