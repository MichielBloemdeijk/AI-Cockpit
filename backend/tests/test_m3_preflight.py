"""Shared-service/runtime feasibility, not acceptance of the unbuilt M3 loops."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import threading
from time import monotonic

from httpx import ASGITransport, AsyncClient
import pytest

from app.db.session import session_scope
from app.db.tables import Conversation
from app.main import app
from app.services.jarvis_brain import restricted_brain_gateway
from app.services.task_service import task_service
from app.services.task_presentations import task_presentation_service
from app.services.worker_coordinator import worker_coordinator


async def saturate_pool():
    tasks = []
    for index in range(8):
        task = await task_service.dispatch(goal=f"Occupied worker {index}", origin_surface="test",
                                           idempotency_key=f"m3-preflight-worker-task-{index}")
        worker = await worker_coordinator.activate(process_identity=f"m3-preflight-{index}")
        assert worker is not None
        assert (await worker_coordinator.claim_next(worker.worker_id)).task is not None
        tasks.append(task)
    assert (await worker_coordinator.pool_snapshot()).busy == 8
    return tasks


async def exercise_code_only_services(occupied):
    started = monotonic()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        assert (await client.post("/api/auth/login", json={"password": ""})).status_code == 200
        response = await client.post("/api/tasks", json={"goal": "New voice task",
                                                        "idempotency_key": "m3-preflight-admit"})
        assert response.status_code == 201, response.text
        task_id = response.json()["id"]
        response = await client.post(f"/api/tasks/{task_id}/control",
                                     json={"action": "cancel", "idempotency_key": "m3-preflight-cancel"})
        assert response.status_code == 200 and response.json()["command"]["state"] == "confirmed"
        async with session_scope() as session:
            conversation = Conversation(title="M3 voice preflight")
            session.add(conversation)
            await session.flush()
            conversation_id = conversation.id
        delegated = await restricted_brain_gateway.execute(
            tool_name="dispatch_task", arguments={"goal": "Draft a voice-requested note"},
            call_id="m3-preflight-voice-dispatch", conversation_id=conversation_id,
            origin_turn_id=None,
        )
        assert delegated.output["accepted"]
        controlled = await restricted_brain_gateway.execute(
            tool_name="control_task", arguments={"task_id": delegated.output["internal_task_id"],
                "action": "cancel", "reason": "The user cancelled the voice-requested note"},
            call_id="m3-preflight-voice-cancel", conversation_id=conversation_id,
            origin_turn_id=None,
        )
        assert controlled.output["command_state"] == "confirmed"
        await task_service.complete(occupied[0].task.id, summary="Factual completed result",
                                    idempotency_key="m3-preflight-complete")
        await task_presentation_service.sync_candidates()
        assert any(item.task_id == occupied[0].task.id
                   for item in await task_presentation_service.list_pending())
        history = await client.get(f"/api/tasks/{occupied[0].task.id}/events")
        assert history.status_code == 200
        assert any(event["event_type"] == "task.completed" for event in history.json()["events"])
    return round((monotonic() - started) * 1000, 1)


@pytest.mark.asyncio
async def test_blocked_background_host_does_not_block_saturated_pool_services():
    occupied = await saturate_pool()
    started, release = threading.Event(), threading.Event()

    def blocked_host():
        started.set()
        assert release.wait(15), "Test did not release isolated host"

    background = asyncio.create_task(asyncio.to_thread(blocked_host))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        await asyncio.wait_for(exercise_code_only_services(occupied), timeout=5)
        assert not background.done()
    finally:
        release.set()
        await background


@pytest.mark.asyncio
@pytest.mark.provider
@pytest.mark.skipif(os.environ.get("JARVIS_LIVE_CODEX") != "1", reason="Billed live runtime preflight is opt-in")
async def test_restricted_codex_runtime_is_independent_of_eight_busy_workers(tmp_path: Path):
    from openai_codex import Codex
    from app.jarvis_tasks.codex_probe import (
        _codex_config, _probe_api_key, _workflow_boundary_checks, TARGET_MODEL, TARGET_EFFORT,
    )
    api_key = _probe_api_key()
    if not api_key:
        pytest.skip("No authorized API credential")
    occupied = await saturate_pool()
    profile, workspace = tmp_path / "profile", tmp_path / "workspace"
    profile.mkdir()
    workspace.mkdir()

    def restricted_runtime():
        # Dedicated API profile; never reads or mutates desktop subscription auth.
        with Codex(_codex_config(profile=profile, workspace=workspace)) as client:
            client.login_api_key(api_key)
        return _workflow_boundary_checks(profile=profile, workspace=workspace)

    background = asyncio.create_task(asyncio.to_thread(restricted_runtime))
    service_elapsed_ms = await asyncio.wait_for(exercise_code_only_services(occupied), timeout=5)
    checks, details = await asyncio.wait_for(asyncio.shield(background), timeout=180)
    report = {"schema": "jarvis-m3-runtime-preflight.v1", "model": TARGET_MODEL, "effort": TARGET_EFFORT,
              "worker_busy_at_runtime_start": 8, "code_only_service_elapsed_ms": service_elapsed_ms,
              "checks": [asdict(check) for check in checks], "runtime": details,
              "scope": "Reuses M0 restricted MCP probe; M3 decision loops/tools are not implemented"}
    output = os.environ.get("JARVIS_M3_PREFLIGHT_REPORT")
    if output:
        Path(output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert checks and all(check.status == "pass" for check in checks), report
