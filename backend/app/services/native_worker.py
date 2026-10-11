"""Small pinned App Server facade for exact policy and approved skill loading."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import shutil


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


def start_turn(thread, prompt: str, *, effort: str, policy: dict):
    # The high-level SDK sandbox enum discards explicit writable roots.
    # Preserve the SDK subscription registration before the turn can emit items.
    from openai_codex.api import TurnHandle
    started, subscription = thread._client._start_turn(thread.id,
        [{"type": "text", "text": prompt}], params={"effort": effort,
            "approvalPolicy": "never", "sandboxPolicy": policy}, for_handle=True)
    return TurnHandle(thread._client, thread.id, started.turn.id, _subscription=subscription)


def approved_skill_config(client, workspace: Path, resolved: dict) -> dict:
    approved = {str(Path(path).resolve()).casefold() for path in resolved["approved_skills"]}
    enabled = "native.skills" in resolved["tools"]
    # Register extra roots before inventorying, so incidental skills discovered
    # through those roots are disabled too.
    if approved and enabled:
        client._client._request_raw("skills/extraRoots/set", {
            "extraRoots": [str(Path(path).parent) for path in resolved["approved_skills"]]})
    raw = client._client._request_raw("skills/list", {"cwds": [str(workspace)], "forceReload": True})
    skills = [skill for entry in raw.get("data", []) for skill in entry.get("skills", [])]
    config = [{"path": skill["path"], "enabled": enabled and str(Path(skill["path"]).resolve()).casefold() in approved}
        for skill in skills]
    # Explicit approved paths are resolved by Codex's own extra-root mechanism.
    if approved and enabled:
        config.extend({"path": path, "enabled": True} for path in resolved["approved_skills"])
    return {"skills": {"config": config}}


def verify_native_workload(client, workspace: Path) -> None:
    from app.jarvis_tasks.codex_probe import _command_exec
    from app.services.worker_tool_policy import sandbox_policy
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
        result = _command_exec(client, {"command": [sys.executable, "-c", code, str(scratch)],
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
        result = _command_exec(client, {"command": [node, "--eval", script, str(scratch)],
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
    from app.services.codex_task_runtime import _json_value
    observed, result = {parent_thread_id}, []
    while descendants - observed:
        child_id = sorted(descendants - observed)[0]
        observed.add(child_id)
        history = _json_value(await asyncio.to_thread(client._client.thread_read, child_id, include_turns=True))
        for turn in (history.get("thread") or {}).get("turns", []):
            for item in turn.get("items", []):
                descendants.update(str(value) for value in item.get("receiverThreadIds", []) if value)
            if turn.get("status") == "inProgress":
                if stop_active:
                    await asyncio.to_thread(client._client.turn_interrupt, child_id, turn["id"])
                continue
            if turn.get("status") != "completed":
                continue
            for item in turn.get("items", []):
                if item.get("id"):
                    result.append({"parent_thread_id": parent_thread_id, "thread_id": child_id,
                        "turn_id": turn.get("id"), "item": item})
    return result
