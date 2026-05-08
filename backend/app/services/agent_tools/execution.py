from __future__ import annotations

import asyncio
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
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


SHELL_META_TOKENS = ("|", "&&", "||", ";", ">", "<")


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


PYTHON_EXECUTION_TOOL = AgentTool(
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
)


SHELL_COMMAND_TOOL = AgentTool(
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
)


__all__ = [
    "PYTHON_EXECUTION_TOOL",
    "SHELL_COMMAND_TOOL",
    "run_python_execution",
    "run_shell_command",
]