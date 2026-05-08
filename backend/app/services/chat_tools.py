"""Reusable tool registry for chat and agent task execution."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from typing import Any, Awaitable, Callable, Literal

from app.config import settings
from app.services.app_builder import bootstrap_generated_app
from app.services.app_registry import app_registry_service


MAX_TOOL_OUTPUT_CHARS = 12_000
SEARCH_RESULT_LIMIT = 20
SHELL_META_TOKENS = ("|", "&&", "||", ";", ">", "<")


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


def _python_guard_script(code: str, read_roots: list[Path], write_roots: list[Path]) -> str:
    read_root_values = [root.as_posix() for root in read_roots]
    write_root_values = [root.as_posix() for root in write_roots]
    return f'''
from __future__ import annotations

import builtins
import io
import os
import pathlib
import shutil
import subprocess

READ_ROOTS = [pathlib.Path(value).resolve() for value in {read_root_values!r}]
WRITE_ROOTS = [pathlib.Path(value).resolve() for value in {write_root_values!r}]
USER_CODE = {code!r}


def _resolve_for_read(path_value):
    resolved = pathlib.Path(path_value).expanduser().resolve()
    for root in READ_ROOTS:
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    raise PermissionError(f"Path '{{resolved}}' is outside the allowed read roots")


def _resolve_for_write(path_value):
    resolved = pathlib.Path(path_value).expanduser().resolve()
    for root in WRITE_ROOTS:
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    raise PermissionError(f"Path '{{resolved}}' is outside the allowed write roots")


def _deny_write(*args, **kwargs):
    raise PermissionError("Python chat tool can only write inside the allowed write roots")


def _deny_subprocess(*args, **kwargs):
    raise PermissionError("Python chat tool cannot spawn subprocesses")


_original_open = builtins.open
_original_os_open = os.open
_original_chdir = os.chdir
_original_mkdir = os.mkdir
_original_makedirs = os.makedirs
_original_remove = os.remove
_original_rename = os.rename
_original_replace = os.replace
_original_rmdir = os.rmdir
_original_unlink = os.unlink


def _guarded_open(file, mode='r', *args, **kwargs):
    if any(flag in mode for flag in ('w', 'a', 'x', '+')):
        resolved = _resolve_for_write(file)
    else:
        resolved = _resolve_for_read(file)
    return _original_open(resolved, mode, *args, **kwargs)


def _guarded_os_open(path, flags, *args, **kwargs):
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
    if flags & write_flags:
        resolved = _resolve_for_write(path)
    else:
        resolved = _resolve_for_read(path)
    return _original_os_open(str(resolved), flags, *args, **kwargs)


def _guarded_chdir(path):
    resolved = _resolve_for_read(path)
    return _original_chdir(str(resolved))


def _guarded_mkdir(path, *args, **kwargs):
    resolved = _resolve_for_write(path)
    return _original_mkdir(str(resolved), *args, **kwargs)


def _guarded_makedirs(path, *args, **kwargs):
    resolved = _resolve_for_write(path)
    return _original_makedirs(str(resolved), *args, **kwargs)


def _guarded_remove(path, *args, **kwargs):
    resolved = _resolve_for_write(path)
    return _original_remove(str(resolved), *args, **kwargs)


def _guarded_rename(src, dst, *args, **kwargs):
    resolved_src = _resolve_for_write(src)
    resolved_dst = _resolve_for_write(dst)
    return _original_rename(str(resolved_src), str(resolved_dst), *args, **kwargs)


def _guarded_replace(src, dst, *args, **kwargs):
    resolved_src = _resolve_for_write(src)
    resolved_dst = _resolve_for_write(dst)
    return _original_replace(str(resolved_src), str(resolved_dst), *args, **kwargs)


def _guarded_rmdir(path, *args, **kwargs):
    resolved = _resolve_for_write(path)
    return _original_rmdir(str(resolved), *args, **kwargs)


def _guarded_unlink(path, *args, **kwargs):
    resolved = _resolve_for_write(path)
    return _original_unlink(str(resolved), *args, **kwargs)


builtins.open = _guarded_open
io.open = _guarded_open
os.open = _guarded_os_open
os.chdir = _guarded_chdir
os.makedirs = _guarded_makedirs
os.mkdir = _guarded_mkdir
os.remove = _guarded_remove
os.rename = _guarded_rename
os.replace = _guarded_replace
os.rmdir = _guarded_rmdir
os.removedirs = _deny_write
os.unlink = _guarded_unlink
os.system = _deny_subprocess
shutil.copy = _deny_write
shutil.copy2 = _deny_write
shutil.copyfile = _deny_write
shutil.copytree = _deny_write
shutil.move = _deny_write
shutil.rmtree = _deny_write
subprocess.Popen = _deny_subprocess
subprocess.run = _deny_subprocess
subprocess.call = _deny_subprocess
subprocess.check_call = _deny_subprocess
subprocess.check_output = _deny_subprocess


def _path_open(self, mode='r', *args, **kwargs):
    return _guarded_open(self, mode, *args, **kwargs)


def _path_mkdir(self, *args, **kwargs):
    return _guarded_mkdir(self, *args, **kwargs)


def _path_touch(self, mode=0o666, exist_ok=True):
    resolved = _resolve_for_write(self)
    with _original_open(resolved, 'a', encoding='utf-8'):
        return None


def _path_unlink(self, *args, **kwargs):
    return _guarded_unlink(self, *args, **kwargs)


def _path_write_bytes(self, data):
    resolved = _resolve_for_write(self)
    with _original_open(resolved, 'wb') as handle:
        return handle.write(data)


def _path_write_text(self, data, encoding=None, errors=None, newline=None):
    resolved = _resolve_for_write(self)
    with _original_open(resolved, 'w', encoding=encoding or 'utf-8', errors=errors, newline=newline) as handle:
        return handle.write(data)


pathlib.Path.open = _path_open
pathlib.Path.mkdir = _path_mkdir
pathlib.Path.rename = _deny_write
pathlib.Path.replace = _deny_write
pathlib.Path.rmdir = _deny_write
pathlib.Path.symlink_to = _deny_write
pathlib.Path.touch = _path_touch
pathlib.Path.unlink = _path_unlink
pathlib.Path.write_bytes = _path_write_bytes
pathlib.Path.write_text = _path_write_text


globals_dict = {{"__name__": "__main__"}}
exec(compile(USER_CODE, "<chat-tool-python>", "exec"), globals_dict, globals_dict)
'''


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


async def run_app_initialize(
    *,
    context: ToolExecutionContext,
    title: str | None = None,
    app_slug: str | None = None,
    description: str | None = None,
) -> ToolExecutionResult:
    normalized_title = (title or "").strip()
    normalized_slug = (app_slug or "").strip()
    if not normalized_title and not normalized_slug:
        raise ValueError("App initialization requires title or app_slug")
    bootstrap = await bootstrap_generated_app(
        goal=normalized_title or normalized_slug,
        title=normalized_title or None,
        app_slug=normalized_slug or None,
        description=description,
        source_conversation_id=context.conversation_id,
        source_task_run_id=context.run_id,
    )
    bootstrap.app = await app_registry_service.acquire_lease(
        app_id=bootstrap.app.id,
        conversation_id=context.conversation_id,
        holder_run_id=context.run_id,
    )
    mode = "created" if bootstrap.created else "attached"
    output = f"{mode.title()} generated app '{bootstrap.app.title}' at {bootstrap.route_path}."

    return ToolExecutionResult(
        tool="app_initialize",
        output=output,
        metadata={
            "mode": mode,
            "app": {
                "app_id": bootstrap.app.id,
                "slug": bootstrap.app.slug,
                "title": bootstrap.app.title,
                "description": bootstrap.app.description,
                "route_path": bootstrap.route_path,
                "frontend_root": bootstrap.frontend_root,
                "frontend_entry_path": bootstrap.frontend_entry_path,
                "frontend_layout_path": bootstrap.frontend_layout_path,
                "manifest_path": bootstrap.manifest_path,
                "asset_root": bootstrap.asset_root,
                "allowed_write_roots": bootstrap.allowed_write_roots,
                "scaffolded_files": bootstrap.scaffolded_files,
            },
            "write_roots": bootstrap.allowed_write_roots,
        },
    )


async def run_app_list(*, context: ToolExecutionContext) -> ToolExecutionResult:
    apps = await app_registry_service.list_apps()
    items = [
        {
            "id": app.id,
            "slug": app.slug,
            "title": app.title,
            "route_path": app.route_path,
            "status": app.status,
        }
        for app in apps
    ]
    if not items:
        output = "No generated apps are registered yet."
    else:
        output = "Registered apps:\n" + "\n".join(
            f"- {item['title']} ({item['slug']}) [{item['status']}]" for item in items
        )
    return ToolExecutionResult(
        tool="app_list",
        output=output,
        metadata={"apps": items, "count": len(items), "workspace_path": context.workspace_path},
    )


async def run_python_execution(
    code: str,
    *,
    context: ToolExecutionContext,
    working_directory: str | None = None,
) -> ToolExecutionResult:
    if not code.strip():
        raise ValueError("Python code is required")

    cwd = context.workspace_root
    if working_directory:
        cwd = _resolve_read_path(working_directory, context=context)

    with tempfile.TemporaryDirectory(prefix="ai-cockpit-chat-tool-") as temp_dir:
        bootstrap_path = Path(temp_dir) / "guarded_python_exec.py"
        bootstrap_path.write_text(
            _python_guard_script(code, context.read_roots, context.write_roots),
            encoding="utf-8",
        )

        try:
            completed_process = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, str(bootstrap_path)],
                cwd=str(cwd),
                capture_output=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Python execution timed out after 10 seconds") from exc

    stdout_text = completed_process.stdout.decode(errors="replace").strip()
    stderr_text = completed_process.stderr.decode(errors="replace").strip()
    if completed_process.returncode != 0:
        raise ValueError(stderr_text or stdout_text or f"Python execution failed with exit code {completed_process.returncode}")

    sections = []
    if stdout_text:
        sections.append(f"stdout:\n{stdout_text}")
    if stderr_text:
        sections.append(f"stderr:\n{stderr_text}")
    if not sections:
        sections.append("Python execution completed with no output.")

    return ToolExecutionResult(
        tool="python_execution",
        output="\n\n".join(sections),
        metadata={
            "working_directory": _display_path(cwd),
            "returncode": completed_process.returncode,
            "workspace_path": context.workspace_path,
            "sandbox_mode": "allowed_write_roots_only",
            "write_roots": [_display_path(root) for root in context.write_roots],
        },
    )


def _parse_shell_command(command: str) -> list[str]:
    normalized = command.strip()
    if not normalized:
        raise ValueError("Command is required")
    if any(token in normalized for token in SHELL_META_TOKENS):
        raise ValueError("Shell command execution does not support pipes, redirection, or command chaining")
    try:
        args = shlex.split(normalized, posix=False)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    if not args:
        raise ValueError("Command is required")
    return args


async def run_shell_command(
    command: str,
    *,
    context: ToolExecutionContext,
    working_directory: str | None = None,
) -> ToolExecutionResult:
    args = _parse_shell_command(command)
    cwd = context.workspace_root
    if working_directory:
        cwd = _resolve_write_path(working_directory, context=context)

    try:
        completed_process = await asyncio.to_thread(
            subprocess.run,
            args,
            cwd=str(cwd),
            capture_output=True,
            timeout=15,
        )
    except FileNotFoundError as exc:
        raise ValueError(f"Executable not found: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Command timed out after 15 seconds") from exc

    stdout_text = completed_process.stdout.decode(errors="replace").strip()
    stderr_text = completed_process.stderr.decode(errors="replace").strip()
    sections = []
    if stdout_text:
        sections.append(f"stdout:\n{stdout_text}")
    if stderr_text:
        sections.append(f"stderr:\n{stderr_text}")
    if not sections:
        sections.append("Command completed with no output.")

    return ToolExecutionResult(
        tool="shell_command",
        output=_truncate_output("\n\n".join(sections)),
        metadata={
            "command": command,
            "args": args,
            "working_directory": "." if cwd == context.workspace_root else _display_path(cwd),
            "returncode": completed_process.returncode,
            "write_roots": [_display_path(root) for root in context.write_roots],
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


async def _execute_app_initialize(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolExecutionResult:
    return await run_app_initialize(
        context=context,
        title=None if arguments.get("title") in (None, "") else str(arguments.get("title")),
        app_slug=None if arguments.get("app_slug") in (None, "") else str(arguments.get("app_slug")),
        description=None if arguments.get("description") in (None, "") else str(arguments.get("description")),
    )


async def _execute_app_list(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolExecutionResult:
    return await run_app_list(context=context)


async def _execute_python_execution(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolExecutionResult:
    return await run_python_execution(
        str(arguments.get("code", "")),
        context=context,
        working_directory=(
            None if arguments.get("working_directory") in (None, "") else str(arguments["working_directory"])
        ),
    )


async def _execute_shell_command(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolExecutionResult:
    return await run_shell_command(
        str(arguments.get("command", "")),
        context=context,
        working_directory=(
            None if arguments.get("working_directory") in (None, "") else str(arguments["working_directory"])
        ),
    )


TOOL_REGISTRY: dict[str, AgentTool] = {
    "workspace_search": AgentTool(
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
    "file_read": AgentTool(
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
    "file_write": AgentTool(
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
    "python_execution": AgentTool(
        name="python_execution",
        description="Run short Python code with reads allowed under configured roots and writes allowed only inside the allowed write roots.",
        input_schema=_tool_schema(
            {
                "code": _tool_property("string", "Short Python code snippet to execute."),
                "working_directory": _tool_property("string", "Optional working directory inside an allowed read root."),
            },
            required=["code"],
        ),
        executor=_execute_python_execution,
        read_only=False,
    ),
    "app_initialize": AgentTool(
        name="app_initialize",
        description="Create or attach to a generated app route under /apps/<slug>, scaffold the standard files if missing, and return the allowed app write roots. For a new app, pass a clear title that should become the visible app name; optionally pass app_slug when you need an exact route segment. For attaching an existing app, pass app_slug.",
        input_schema=_tool_schema(
            {
                "title": _tool_property("string", "Visible app title to use when creating a new generated app."),
                "app_slug": _tool_property("string", "Existing app slug to attach to, or explicit slug to use for a new app."),
                "description": _tool_property("string", "Optional short description of the generated app."),
            },
        ),
        executor=_execute_app_initialize,
        read_only=False,
    ),
    "app_list": AgentTool(
        name="app_list",
        description="List generated apps with title, slug, route path, and status so you can pick a unique name before creating a new app.",
        input_schema=_tool_schema({}),
        executor=_execute_app_list,
        read_only=True,
    ),
    "shell_command": AgentTool(
        name="shell_command",
        description="Run one non-interactive executable plus arguments from the conversation workspace. Pipes, redirection, and command chaining are not allowed.",
        input_schema=_tool_schema(
            {
                "command": _tool_property("string", "Executable plus arguments to run without pipes or shell chaining."),
                "working_directory": _tool_property("string", "Optional working directory inside an allowed write root."),
            },
            required=["command"],
        ),
        executor=_execute_shell_command,
        read_only=False,
    ),
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