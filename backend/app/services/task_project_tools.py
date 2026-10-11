"""General coding capabilities resolved from server-owned fenced directory leases."""
from __future__ import annotations

import asyncio
from hashlib import sha256
import importlib.metadata
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.db.tables import ResourceLease, TaskTarget, TaskAttempt
from app.services.task_service import task_service


class SandboxBoundaryUnavailable(RuntimeError):
    """No user command started; the host boundary did not pass its probes."""


def project_tool_catalog() -> list[dict]:
    root = {"type": "string", "description": "Target ID returned by project_roots; omit for private workspace."}
    def tool(name, description, properties, required):
        return {"name": name, "description": description, "inputSchema": {
            "type": "object", "properties": properties, "required": required,
            "additionalProperties": False}}
    return [
        tool("project_roots", "List this attempt's leased authorized filesystem projects.", {}, []),
        tool("project_list", "List a directory in an authorized project.",
             {"target_id": root, "path": {"type": "string"}}, []),
        tool("project_read", "Read a bounded UTF-8 source file in an authorized project.",
             {"target_id": root, "path": {"type": "string"}}, ["path"]),
        tool("project_write", "Atomically edit an authorized project file; journal before success.",
             {"target_id": root, "path": {"type": "string"}, "content": {"type": "string"},
              "operation_key": {"type": "string"}}, ["path", "content", "operation_key"]),
        tool("project_command", f"Run build/test argv in configured {settings.task_command_mode} command mode. Trusted mode inherits user filesystem access. Failed tests return exit code. Interrupted commands are uncertain and never replayed.",
             {"target_id": root, "cwd": {"type": "string"},
              "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1},
              "operation_key": {"type": "string"}}, ["argv", "operation_key"]),
    ]


async def authorized_roots(args) -> dict[str, Path]:
    await task_service.validate_execution_authority(args.task_id, attempt_id=args.attempt_id,
        execution_run_id=args.run_id, claim_token=args.claim_token)
    configured = [Path(value.strip()).resolve() for value in settings.task_project_roots.split(",") if value.strip()]
    async with session_scope() as session:
        attempt = await session.get(TaskAttempt, args.attempt_id)
        workspace = Path(attempt.workspace_path).resolve()
        if args.workspace.resolve() != workspace:
            raise ValueError("Workspace does not match the durable attempt")
        roots = {"workspace": workspace}
        rows = (await session.execute(select(TaskTarget, ResourceLease).join(ResourceLease,
            (ResourceLease.task_id == TaskTarget.task_id)
            & (ResourceLease.resource_key == TaskTarget.resource_key)
            & (ResourceLease.resource_type == TaskTarget.resource_type)).where(
                TaskTarget.task_id == args.task_id, TaskTarget.mutable.is_(True),
                ResourceLease.owner_attempt_id == args.attempt_id,
                ResourceLease.claim_token == args.claim_token,
                ResourceLease.execution_run_id == args.run_id))).all()
        for target, lease in rows:
            if target.resource_type not in {"directory", "path", "workspace", "repository", "generated_app"}:
                continue
            raw = Path(target.resource_key)
            path = (raw if raw.is_absolute() else settings.backend_root.parent / raw).resolve()
            if not any(path == allowed or path.is_relative_to(allowed) for allowed in configured):
                continue
            if not path.is_dir() or lease.canonical_key != str(path).rstrip("\\/").casefold():
                raise ValueError("Project target changed since lease assignment")
            roots[target.id] = path
    return roots


def inside(root: Path, relative, *, directory: bool = False) -> Path:
    if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("Project path must be relative and inside its authorized root")
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or (path == root and not directory):
        raise ValueError("Project path must stay inside its authorized root")
    return path


def execute_sandboxed(argv: list[str], cwd: Path, root: Path, profile: Path) -> dict:
    from openai_codex import Codex, CodexConfig
    from app.jarvis_tasks.codex_probe import _command_exec, _sandbox_checks
    if importlib.metadata.version("openai-codex") != "0.155.1":
        raise RuntimeError("Project commands require pinned Codex 0.155.1")
    # Commands need no provider credential. They use a private per-command host
    # inside the attempt's kill-on-close tree, never a shared credential cache.
    overrides = ("features.apps=false", "features.plugins=false", "features.remote_plugin=false",
        *((f'windows.sandbox="{settings.task_windows_sandbox}"',) if os.name == "nt" else ()))
    profile.mkdir(parents=True, exist_ok=True)
    client = Codex(CodexConfig(env={"CODEX_HOME": str(profile)}, cwd=str(cwd),
        config_overrides=overrides, experimental_api=False, client_name="jarvis_project_command"))
    try:
        probe = root / f"jarvis-command-probe-{uuid4()}"
        escape = root.parent / f".jarvis-command-escape-{uuid4()}"
        probe.mkdir()
        try:
            checks = _sandbox_checks(client, workspace=probe, escape_target=escape)
            failed = [check.id for check in checks if check.status != "pass"]
            if failed:
                raise SandboxBoundaryUnavailable(f"Project command sandbox preflight failed closed: {', '.join(failed)}")
        finally:
            import shutil
            shutil.rmtree(probe, ignore_errors=True)
            escape.unlink(missing_ok=True)
        # The pinned Windows sandbox can report a nonzero child exit as an
        # RPC error, losing its structured exit code. This wrapper always exits
        # zero after recording the actual child result; killed hosts never return
        # a result and the operation remains uncertain. Child argv is never shell text.
        wrapper = """import json,subprocess,sys,os,shutil,uuid
from pathlib import Path
folder=Path('.')/('jarvis-command-output-'+str(uuid.uuid4()))
folder.mkdir()
try:
    scratch=folder/'scratch'; scratch.mkdir()
    env=dict(os.environ, TEMP=str(scratch.resolve()), TMP=str(scratch.resolve()), TMPDIR=str(scratch.resolve()))
    out=folder/'stdout'; err=folder/'stderr'
    with out.open('wb') as stdout, err.open('wb') as stderr:
        result=subprocess.run(json.loads(sys.argv[1]),stdout=stdout,stderr=stderr,env=env)
    def tail(path):
        with path.open('rb') as stream:
            stream.seek(max(0,path.stat().st_size-128000))
            return stream.read().decode('utf-8',errors='replace')
    print(json.dumps({'exit_code':result.returncode,'stdout':tail(out),'stderr':tail(err)}))
finally:
    shutil.rmtree(folder)
"""
        response = _command_exec(client, {"command": [sys.executable, "-c", wrapper, json.dumps(argv)], "cwd": str(cwd),
            "sandboxPolicy": {"type": "workspaceWrite", "writableRoots": [str(root)],
                              "networkAccess": False, "excludeTmpdirEnvVar": True,
                              "excludeSlashTmp": True}, "timeoutMs": None})
        if response.get("exitCode") != 0:
            raise RuntimeError("Sandbox command wrapper failed without a durable child result")
        result = json.loads(response.get("stdout") or "")
        if (not isinstance(result, dict) or type(result.get("exit_code")) is not int
                or not isinstance(result.get("stdout"), str) or not isinstance(result.get("stderr"), str)):
            raise RuntimeError("Sandbox command returned an invalid child result")
        return result
    finally:
        client.close()


def execute_trusted(argv: list[str], cwd: Path, root: Path, profile: Path) -> dict:
    """Ordinary child in the already contained worker's Job Object, no sandbox retry."""
    import subprocess
    import shutil
    folder = root / f"jarvis-command-output-{uuid4()}"
    folder.mkdir()
    try:
        scratch = folder / "scratch"
        scratch.mkdir()
        env = dict(os.environ, TEMP=str(scratch), TMP=str(scratch), TMPDIR=str(scratch))
        # Command scratch never contains the worker's private credential profile.
        for key in ("CODEX_HOME", "OPENAI_API_KEY"):
            env.pop(key, None)
        out, err = folder / "stdout", folder / "stderr"
        with out.open("wb") as stdout, err.open("wb") as stderr:
            child = subprocess.run(argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        def tail(path):
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 128000))
                return stream.read().decode("utf-8", errors="replace")
        return {"exit_code": child.returncode, "stdout": tail(out), "stderr": tail(err)}
    finally:
        shutil.rmtree(folder)


async def call_project_tool(name: str, arguments: dict, args) -> dict:
    roots = await authorized_roots(args)
    if name == "project_roots":
        return {"roots": [{"target_id": key, "path": str(path)} for key, path in roots.items()]}
    target_id = arguments.get("target_id", "workspace")
    if target_id not in roots:
        raise ValueError("Project target is not authorized and leased to this attempt")
    root = roots[target_id]
    if name == "project_list":
        path = inside(root, arguments.get("path", ""), directory=True)
        if not path.is_dir():
            raise ValueError("Project directory does not exist")
        entries = sorted(path.iterdir(), key=lambda p: p.name)[:500]
        return {"entries": [{"name": p.name, "directory": p.is_dir()} for p in entries if not p.is_symlink()]}
    if name == "project_read":
        path = inside(root, arguments.get("path"))
        if not path.is_file() or path.stat().st_size > 256_000:
            raise ValueError("Project read requires a UTF-8 file below 256 KB")
        return {"path": arguments["path"], "content": path.read_text(encoding="utf-8")}
    if name not in {"project_write", "project_command"}:
        raise ValueError("Unknown project tool")
    key = arguments.get("operation_key")
    if not isinstance(key, str) or not 1 <= len(key) <= 200:
        raise ValueError("A stable operation_key is required")
    if name == "project_write":
        path = inside(root, arguments.get("path"))
        content = arguments.get("content")
        if not isinstance(content, str) or len(content.encode("utf-8")) > 256_000:
            raise ValueError("Project write requires UTF-8 content below 256 KB")
    else:
        path = inside(root, arguments.get("cwd", ""), directory=True)
        argv = arguments.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a and "\0" not in a for a in argv):
            raise ValueError("Command requires a nonempty argv array")
        if not path.is_dir():
            raise ValueError("Command working directory does not exist")
    intent = {"target_id": target_id, "root": str(root), **arguments}
    if name == "project_command":
        intent["execution_mode"] = getattr(args, "command_mode", settings.task_command_mode)
    digest = sha256(json.dumps(intent, sort_keys=True).encode()).hexdigest()
    fence = dict(attempt_id=args.attempt_id, execution_run_id=args.run_id, claim_token=args.claim_token)
    operation = await task_service.start_operation(args.task_id, operation_key=f"{args.run_id}:{key}",
        operation_type="broker.project_write" if name == "project_write" else "broker.command",
        input_digest=digest, intent=intent, **fence)
    if operation.state == "succeeded":
        return dict(operation.result_json or {})
    # Recheck authority after journal commit and before starting the effect.
    if (await authorized_roots(args)).get(target_id) != root:
        raise ValueError("Project authority changed before execution")
    if name == "project_write":
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.parent.resolve().is_relative_to(root):
            raise ValueError("Project path escaped its authorized root")
        temp = path.with_name(f".{path.name}.{uuid4()}.tmp")
        try:
            with temp.open("x", encoding="utf-8", newline="\n") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
        result = {"path": arguments["path"], "sha256": sha256(content.encode()).hexdigest()}
    else:
        profile = settings.codex_profiles_path / args.task_id / "commands" / operation.id
        try:
            execute = execute_sandboxed if getattr(args, "command_mode", settings.task_command_mode) == "native" else execute_trusted
            result = await asyncio.to_thread(execute, argv, path, root, profile)
            result["execution_mode"] = getattr(args, "command_mode", settings.task_command_mode)
        except SandboxBoundaryUnavailable:
            await task_service.finish_operation(args.task_id, operation.id, state="failed",
                result={"effect_started": False, "reason": "sandbox_boundary_unavailable"}, **fence)
            raise
        except Exception:
            # A lost/invalid response cannot prove that the child did no work.
            await task_service.finish_operation(args.task_id, operation.id, state="uncertain",
                result={"reason": "command_result_unavailable"}, **fence)
            raise
    await task_service.finish_operation(args.task_id, operation.id, state="succeeded", result=result, **fence)
    return result
