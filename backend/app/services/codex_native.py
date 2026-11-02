"""The production facade for the pinned Codex App Server.

Everything that depends on the pinned SDK's shape lives here: the version pin,
client configuration, payload normalization, the raw App Server methods the
stable SDK does not wrap yet, and the fail-closed sandbox gate. The task and
orchestration runtimes and the diagnostic probes all go through it; the probes
only compose checks and report evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Any, Iterable, Literal
from uuid import uuid4

from app.config import settings


CODEX_RUNTIME_PIN = "0.155.1"

# Overrides every Jarvis client sets: no apps or plugins from the user's Codex setup.
DISABLED_EXTENSIONS = ("features.apps=false", "features.plugins=false", "features.remote_plugin=false")
# A client that only reasons and calls its configured MCP tools.
TOOLLESS = (
    "agents.enabled=false", *DISABLED_EXTENSIONS, 'web_search="disabled"',
    "features.shell_tool=false", "features.unified_exec=false", "features.skill_mcp_dependency_install=false",
)


def require_pinned_runtime() -> None:
    if importlib.metadata.version("openai-codex") != CODEX_RUNTIME_PIN:
        raise RuntimeError(f"Codex runtime must be pinned to {CODEX_RUNTIME_PIN}")


def client_config(
    profile: Path,
    *,
    client_name: str,
    workspace: Path | None = None,
    overrides: Iterable[str] = (),
    client_title: str | None = None,
    windows_sandbox: str | None = None,
) -> Any:
    """A client bound to one Jarvis profile; Windows clients always name their sandbox backend."""
    from openai_codex import CodexConfig

    sandbox = windows_sandbox or settings.task_windows_sandbox
    options: dict[str, Any] = {
        "env": {"CODEX_HOME": str(profile)},
        "config_overrides": (*overrides, *((f'windows.sandbox="{sandbox}"',) if os.name == "nt" else ())),
        "experimental_api": False,
        "client_name": client_name,
    }
    if workspace is not None:
        options["cwd"] = str(workspace)
    if client_title is not None:
        options["client_title"] = client_title
    return CodexConfig(**options)


def json_value(value: Any) -> Any:
    """SDK models and enums as plain JSON values."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return getattr(value, "value", value)


def walk_items(value: Any) -> list[dict[str, Any]]:
    """The native items in a payload, without descending into tool receipts."""
    converted = json_value(value)
    found: list[dict[str, Any]] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            if isinstance(item.get("type"), str) and (
                "id" in item or item.get("type") in {"agentMessage", "reasoning"}
            ):
                found.append(item)
                # A tool receipt can contain prior native items as evidence.
                # Those are data, not fresh protocol actions from this turn.
                return
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(converted)
    return found


def message_text(item: dict[str, Any]) -> str | None:
    if item.get("type") != "agentMessage":
        return None
    for key in ("text", "content", "message"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            parts = [
                str(part.get("text") or "")
                for part in value
                if isinstance(part, dict) and part.get("text")
            ]
            if parts:
                return "\n".join(parts).strip()
    return None


def request_raw(client: Any, method: str, params: dict[str, Any]) -> Any:
    """An App Server method the stable SDK does not wrap yet."""
    return client._client._request_raw(method, params)


def command_exec(client: Any, params: dict[str, Any]) -> dict[str, Any]:
    result = request_raw(client, "command/exec", params)
    if not isinstance(result, dict):
        raise TypeError("command/exec returned a non-object response")
    return result


def read_thread(client: Any, thread_id: str) -> dict[str, Any]:
    return json_value(client._client.thread_read(thread_id, include_turns=True))


def interrupt_turn(client: Any, thread_id: str, turn_id: str) -> None:
    client._client.turn_interrupt(thread_id, turn_id)


def mcp_tool_inventory(client: Any, thread_id: str) -> list[str]:
    """The MCP tools a thread actually exposes, as sorted `server.tool` names."""
    inventory = json_value(request_raw(client, "mcpServerStatus/list", {"threadId": thread_id, "detail": "full", "limit": 100}))
    return sorted(f"{server.get('name')}.{tool}" for server in (inventory.get("data") or []) for tool in (server.get("tools") or {}))


def start_turn(thread, prompt: str, *, effort: str, policy: dict):
    # The high-level SDK sandbox enum discards explicit writable roots.
    # Preserve the SDK subscription registration before the turn can emit items.
    from openai_codex.api import TurnHandle
    started, subscription = thread._client._start_turn(thread.id,
        [{"type": "text", "text": prompt}], params={"effort": effort,
            "approvalPolicy": "never", "sandboxPolicy": policy}, for_handle=True)
    return TurnHandle(thread._client, thread.id, started.turn.id, _subscription=subscription)


@dataclass(slots=True)
class SandboxCheck:
    id: str
    status: Literal["pass", "fail"]
    summary: str
    evidence: dict[str, Any]
    elapsed_ms: float


class SandboxBoundaryUnavailable(RuntimeError):
    """The host's sandbox did not behave as requested; nothing may run unrestricted instead."""


def sandbox_checks(client: Any, *, workspace: Path, escape_target: Path) -> list[SandboxCheck]:
    """Read-only, in-root write and escape behavior of the pinned sandbox on this host."""
    checks: list[SandboxCheck] = []
    work = workspace / "sandbox-workspace"
    work.mkdir(parents=True, exist_ok=True)
    write_policy = {
        "type": "workspaceWrite",
        "writableRoots": [str(work)],
        "networkAccess": False,
        "excludeTmpdirEnvVar": True,
        "excludeSlashTmp": True,
    }
    cases = [
        ("read_only", work / "read-only.txt", {"type": "readOnly"}, False),
        ("workspace_inside", work / "inside.txt", write_policy, True),
        ("workspace_escape", escape_target, write_policy, False),
    ]
    for name, target, policy, should_exist in cases:
        started = time.perf_counter()
        target.unlink(missing_ok=True)
        code = f"from pathlib import Path; Path({str(target)!r}).write_text('m0', encoding='utf-8')"
        evidence: dict[str, Any] = {"policy": policy, "expected_file_exists": should_exist}
        try:
            response = command_exec(client, {
                "command": [sys.executable, "-c", code],
                "cwd": str(work),
                "sandboxPolicy": policy,
                "timeoutMs": 10_000,
            })
            exists = target.exists()
            passed = exists is should_exist
            summary = ("Filesystem behavior matched the requested sandbox policy." if passed
                       else "The runtime wrote outside the requested sandbox policy.")
            evidence |= {"actual_file_exists": exists, "exit_code": response.get("exitCode"),
                         "stderr_present": bool(response.get("stderr"))}
        except Exception as exc:  # noqa: BLE001 - denial is expected for negative cases
            exists = target.exists()
            passed = not should_exist and not exists
            summary = ("The runtime denied the prohibited filesystem write." if passed
                       else "The App Server sandbox check could not complete as expected.")
            evidence |= {"actual_file_exists": exists, "denied": True, "error_type": type(exc).__name__,
                         "error_summary": str(exc)[-400:]}
        checks.append(SandboxCheck(id=f"sandbox.{name}", status="pass" if passed else "fail", summary=summary,
                                   evidence=evidence, elapsed_ms=round((time.perf_counter() - started) * 1000, 3)))
    return checks


def require_sandbox_boundary(client: Any, workspace: Path) -> None:
    """The execution gate: probe a disposable root and refuse to run if the sandbox misbehaves here."""
    probe_root = workspace / f".jarvis-sandbox-{uuid4()}"
    escape = workspace.parent / f".jarvis-sandbox-escape-{uuid4()}"
    probe_root.mkdir()
    try:
        checks = sandbox_checks(client, workspace=probe_root, escape_target=escape)
    finally:
        escape.unlink(missing_ok=True)
        shutil.rmtree(probe_root, ignore_errors=True)
    failed = [
        f"{check.id}(exit={check.evidence.get('exit_code')}, exists={check.evidence.get('actual_file_exists')}, "
        f"denied={check.evidence.get('denied', False)}, "
        f"error={check.evidence.get('error_type')}:{check.evidence.get('error_summary')})"
        for check in checks if check.status != "pass"
    ]
    if failed:
        raise SandboxBoundaryUnavailable("Codex sandbox preflight failed closed: " + ", ".join(failed))


def observation_payload(payload: dict) -> dict:
    """Prevent image duplication and recursive history receipts in the timeline."""
    from hashlib import sha256
    item = payload.get("item")
    if not isinstance(item, dict) or item.get("type") != "mcpToolCall":
        return payload
    if item.get("tool") not in {"task_history", "task_context_images"}:
        return payload
    item = dict(item)
    for field in ("result", "output"):
        if field in item and item[field] is not None:
            encoded = json.dumps(item[field], sort_keys=True, ensure_ascii=True).encode()
            item[field] = {"omitted": "Read the task history or frozen image capability directly",
                "sha256": sha256(encoded).hexdigest(), "bytes": len(encoded)}
    return payload | {"item": item}


def approved_skill_config(client, workspace: Path, resolved: dict) -> dict:
    approved = {str(Path(path).resolve()).casefold() for path in resolved["approved_skills"]}
    enabled = "native.skills" in resolved["tools"]
    # Register extra roots before inventorying, so incidental skills discovered
    # through those roots are disabled too.
    if approved and enabled:
        request_raw(client, "skills/extraRoots/set", {
            "extraRoots": [str(Path(path).parent) for path in resolved["approved_skills"]]})
    raw = request_raw(client, "skills/list", {"cwds": [str(workspace)], "forceReload": True})
    skills = [skill for entry in raw.get("data", []) for skill in entry.get("skills", [])]
    config = [{"path": skill["path"], "enabled": enabled and str(Path(skill["path"]).resolve()).casefold() in approved}
        for skill in skills]
    # Explicit approved paths are resolved by Codex's own extra-root mechanism.
    if approved and enabled:
        config.extend({"path": path, "enabled": True} for path in resolved["approved_skills"])
    return {"skills": {"config": config}}


def verify_native_workload(client, workspace: Path) -> None:
    from app.services.tasks.worker_tool_policy import sandbox_policy
    scratch = workspace / ".jarvis-scratch"
    scratch.mkdir(exist_ok=True)
    code = """import os, tempfile, subprocess, sys
from pathlib import Path
os.environ.update(TEMP=sys.argv[1], TMP=sys.argv[1], TMPDIR=sys.argv[1])
with tempfile.TemporaryDirectory(dir=sys.argv[1]) as folder:
    Path(folder, 'write-proof').write_text('ok')
    assert subprocess.run([sys.executable, '-c', 'print(123)'], capture_output=True).returncode == 0
print('native-python-ok')
"""
    try:
        result = command_exec(client, {"command": [sys.executable, "-c", code, str(scratch)],
            "cwd": str(workspace), "sandboxPolicy": sandbox_policy([workspace], local=True, network=False),
            "timeoutMs": 10_000})
        if result.get("exitCode") != 0 or "native-python-ok" not in result.get("stdout", ""):
            raise RuntimeError("Representative Python workload did not pass")
        node = shutil.which("node")
        if not node:
            raise RuntimeError("Node is required for the native workload gate")
        script = """const fs=require('fs'),path=require('path'),cp=require('child_process');
const folder=fs.mkdtempSync(path.join(process.argv[1],'node-'));
fs.writeFileSync(path.join(folder,'proof'),'ok');
if(cp.spawnSync(process.execPath,['-e','console.log(123)']).status!==0)process.exit(3);
fs.rmSync(folder,{recursive:true});console.log('native-node-ok');"""
        result = command_exec(client, {"command": [node, "--eval", script, str(scratch)],
            "cwd": str(workspace), "sandboxPolicy": sandbox_policy([workspace], local=True, network=False),
            "timeoutMs": 10_000})
        if result.get("exitCode") != 0 or "native-node-ok" not in result.get("stdout", ""):
            raise RuntimeError("Representative Node workload did not pass")
    except Exception as exc:
        raise RuntimeError("Native local tools unavailable: Windows temporary-directory workload failed. "
                           "No unrestricted retry was attempted.") from exc


async def subagent_observations(client, parent_thread_id: str, descendants: set[str], *, stop_active: bool = False) -> list[dict]:
    """Read completed child work without stopping live children during a wait."""
    import asyncio
    observed, result = {parent_thread_id}, []
    while descendants - observed:
        child_id = sorted(descendants - observed)[0]
        observed.add(child_id)
        history = await asyncio.to_thread(read_thread, client, child_id)
        for turn in (history.get("thread") or {}).get("turns", []):
            for item in turn.get("items", []):
                descendants.update(str(value) for value in item.get("receiverThreadIds", []) if value)
            if turn.get("status") == "inProgress":
                if stop_active:
                    await asyncio.to_thread(interrupt_turn, client, child_id, turn["id"])
                continue
            if turn.get("status") != "completed":
                continue
            for item in turn.get("items", []):
                if item.get("id"):
                    result.append({"parent_thread_id": parent_thread_id, "thread_id": child_id,
                        "turn_id": turn.get("id"), "item": item})
    return result
