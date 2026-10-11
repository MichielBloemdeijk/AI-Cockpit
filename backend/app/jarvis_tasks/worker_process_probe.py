"""Unpaid acceptance of production MCP transport, DB access and result commits.

Tool choices and native research are explicit fixtures, not provider evidence.
No SDK/model session is started. The bridge command is the runtime's real one.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys

from app.services.codex_task_runtime import task_mcp_arguments
from app.services.task_workflows import validate_pinned_workflow


async def call_worker_bridge(context, calls):
    workflow = (validate_pinned_workflow(context.execution_config)
                if context.execution_kind == "workflow" else None)
    workspace = Path(context.workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)
    process = await asyncio.create_subprocess_exec(sys.executable,
        *task_mcp_arguments(context, workflow=workflow), cwd=workspace,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "OPENAI_API_KEY": "", "CODEX_API_KEY": "", "OPENROUTER_API_KEY": "",
             "TASK_POOL_ENABLED": "false", "TASK_ORCHESTRATION_ENABLED": "false"})
    requests = [{"jsonrpc": "2.0", "id": "initialize", "method": "initialize"},
                {"jsonrpc": "2.0", "id": "catalog", "method": "tools/list"}]
    requests.extend({"jsonrpc": "2.0", "id": i, "method": "tools/call",
        "params": {"name": name, "arguments": values}} for i, (name, values) in enumerate(calls))
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(
            ("\n".join(json.dumps(r) for r in requests) + "\n").encode()), timeout=30)
        assert process.returncode == 0, stderr.decode(errors="replace")
        replies = {json.loads(line)["id"]: json.loads(line) for line in stdout.splitlines()}
        assert all("result" in reply for reply in replies.values()), replies
        catalog = {tool["name"] for tool in replies["catalog"]["result"]["tools"]}
        assert all(name in catalog for name, _ in calls), catalog
        return [replies[i]["result"] for i in range(len(calls))]
    finally:
        if process.returncode is None:
            process.kill()
            await process.communicate()


async def probe_recipe_task(task_id: str, *, expected_images: int) -> dict:
    from app.services.task_service import task_service
    from app.services.worker_coordinator import worker_coordinator
    worker = await worker_coordinator.activate(process_identity="unpaid-recipe-process-probe")
    context = (await worker_coordinator.claim_next(worker.worker_id)).task
    assert context is not None and context.task_id == task_id, "Probe must claim its isolated recipe task"
    # Deterministic provider-protocol fixture. No network research is claimed.
    source_url = "https://example.com/recipe"
    source = await task_service.record_event(task_id, event_type="agent.native.item",
        native_item_id="probe-search", payload={"method": "item/completed", "item": {
            "type": "webSearch", "id": "probe-search", "query": "tomato sandwich",
            "action": {"type": "openPage", "url": source_url}}},
        idempotency_key="probe-source", attempt_id=context.attempt_id,
        execution_run_id=context.execution_run_id, claim_token=context.claim_token)
    reads = await call_worker_bridge(context, [("task_context_images", {}), ("task_history", {})])
    assert all(not result.get("isError") for result in reads), reads
    image_count = sum(item["type"] == "image" for item in reads[0]["content"])
    assert image_count == expected_images, image_count
    roles = {role["role"] for image in reads[0]["structuredContent"]["images"] for role in image["roles"]}
    assert reads[1]["structuredContent"]["native_research"][0]["source_event_sequence"] == source.sequence
    public_id = reads[1]["structuredContent"]["maintained_result"]["public_id"]
    payload = {"title": "Tomato sandwich", "source_event_sequences": [source.sequence],
        "ingredients": ["Two slices of bread", "One tomato", "Cheese"],
        "instructions": ["Slice the tomato.", "Layer tomato and cheese between the bread."],
        "sources": [{"source_event_sequence": source.sequence, "url": source_url, "title": "Fixture recipe"}],
        "operation_key": "probe-recipe"}
    # A new process must reopen the same DB; replay must not duplicate the result.
    publications = await call_worker_bridge(context, [("result_publish", payload), ("result_publish", payload)])
    assert all(not result.get("isError") for result in publications), publications
    assert publications[0] == publications[1]
    assert (await task_service.get_task(task_id)).task.status == "completed"
    await worker_coordinator.release_claim(worker.worker_id, context.attempt_id,
        claim_token=context.claim_token, writers_stopped=True)
    return {"status": "completed", "public_id": public_id, "images_read": image_count,
            "roles_read": sorted(roles), "worker_process_starts": 2, "publication_replay": True,
            "models_called": 0, "research": "provider protocol fixture"}
