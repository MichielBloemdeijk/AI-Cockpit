from __future__ import annotations

import asyncio

import pytest

from app.services.run_supervisor import RunSupervisor


@pytest.mark.asyncio
async def test_supervisor_deduplicates_runs_and_keeps_task_owner_independent() -> None:
    release = asyncio.Event()
    calls: list[str] = []

    class FakeRunner:
        async def continue_run(self, run_id: str, **_kwargs) -> None:
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
        async def continue_run(self, run_id: str, **_kwargs) -> None:
            started.set()
            await asyncio.Event().wait()

    supervisor = RunSupervisor()
    supervisor.start("run-2", runner=FakeRunner())
    await started.wait()
    await supervisor.stop()

    assert supervisor.get("run-2") is None




@pytest.mark.asyncio
async def test_supervisor_logs_a_failed_run(caplog) -> None:
    class FailingRunner:
        async def continue_run(self, run_id: str, **_kwargs) -> None:
            raise RuntimeError("runner exploded")

    supervisor = RunSupervisor()
    task = supervisor.start("run-3", runner=FailingRunner())
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)

    assert any("run-3" in record.getMessage() and record.exc_info for record in caplog.records)
