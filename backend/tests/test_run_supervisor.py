from __future__ import annotations

import asyncio

import pytest

from app.services.run_supervisor import RunSupervisor
from app.services.conversation_store import conversation_store


@pytest.mark.asyncio
async def test_supervisor_deduplicates_runs_and_keeps_task_owner_independent() -> None:
    release = asyncio.Event()
    calls: list[str] = []

    class FakeRunner:
        async def continue_run(self, run_id: str) -> None:
            calls.append(run_id)
            await release.wait()

    supervisor = RunSupervisor()
    first = supervisor.start("run-1", runner=FakeRunner())
    second = supervisor.start("run-1", runner=FakeRunner())
    assert first is second
    await asyncio.sleep(0)
    assert supervisor.get("run-1") is first
    assert calls == ["run-1"]

    release.set()
    await first
    await asyncio.sleep(0)
    assert supervisor.get("run-1") is None


@pytest.mark.asyncio
async def test_supervisor_stop_cancels_owned_tasks() -> None:
    started = asyncio.Event()

    class FakeRunner:
        async def continue_run(self, run_id: str) -> None:
            started.set()
            await asyncio.Event().wait()

    supervisor = RunSupervisor()
    supervisor.start("run-2", runner=FakeRunner())
    await started.wait()
    await supervisor.stop()

    assert supervisor.get("run-2") is None


@pytest.mark.asyncio
async def test_queued_run_claim_is_single_use_and_terminal_runs_cannot_restart() -> None:
    conversation = await conversation_store.create_conversation(mode_hint="voice")
    run = await conversation_store.start_run(conversation.id, kind="assistant", status="queued", metadata_json={})

    claimed = await conversation_store.claim_run_for_execution(run.id)
    assert claimed is not None
    assert claimed.status == "running"
    assert await conversation_store.claim_run_for_execution(run.id) is None

    await conversation_store.update_run_status(run.id, status="cancelled", error="cancelled")
    assert await conversation_store.claim_run_for_execution(run.id) is None
