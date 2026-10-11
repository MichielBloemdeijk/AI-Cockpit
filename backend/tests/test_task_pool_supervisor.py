from __future__ import annotations

import asyncio
import os

import pytest

from app.config import settings
from app.services.task_pool_supervisor import TaskPoolManager, TaskPoolSupervisor, SupervisorLeaseBusy
from app.services.worker_coordinator import worker_coordinator


@pytest.mark.asyncio
async def test_child_registration_race_cannot_spawn_an_extra_standby(monkeypatch):
    from types import SimpleNamespace
    from app.services.task_pool_supervisor import SupervisedChild
    stop = asyncio.Event()
    registered, spawns = False, []

    class RacingCoordinator:
        async def acquire_supervisor(self, owner):
            return 1

        async def heartbeat_supervisor(self, owner, generation):
            return True

        async def stop_requested_identities(self, generation):
            return []

        async def reconcile_availability(self):
            pass

        async def worker_id_for_identity(self, identity):
            return "worker" if registered else None

        async def pool_snapshot(self):
            nonlocal registered
            # The snapshot was taken just before registration committed.
            # Old ordering then saw a registered identity + zero occupancy.
            registered = True
            stop.set()
            return SimpleNamespace(busy=0, standby=0, starting=0)

        async def confirm_pool_commands(self):
            pass

        async def release_supervisor(self, owner, generation):
            pass

    supervisor = TaskPoolSupervisor(coordinator=RacingCoordinator())
    supervisor.children["child"] = SupervisedChild("child", SimpleNamespace(returncode=None))

    async def spawn(generation):
        spawns.append(generation)

    async def stop_children():
        supervisor.children.clear()

    monkeypatch.setattr(supervisor, "_spawn_child", spawn)
    monkeypatch.setattr(supervisor, "_stop_children", stop_children)
    await supervisor.run(stop)
    assert registered and not spawns


@pytest.mark.asyncio
async def test_pool_manager_retries_lost_lease_and_repairs_failed_supervisor(monkeypatch) -> None:
    runs = 0
    serving = asyncio.Event()

    class StubSupervisor:
        async def run(self, stop: asyncio.Event) -> None:
            nonlocal runs
            runs += 1
            if runs == 1:
                raise SupervisorLeaseBusy("owned elsewhere")
            if runs == 2:
                raise RuntimeError("simulated supervisor crash")
            serving.set()
            await stop.wait()

    monkeypatch.setattr("app.services.task_pool_supervisor.TaskPoolSupervisor", StubSupervisor)
    manager = TaskPoolManager(retry_seconds=0.01)
    await manager.start()
    try:
        await asyncio.wait_for(serving.wait(), timeout=2)
        assert runs == 3
        await manager.start()  # idempotent while healthy
        assert runs == 3
    finally:
        await asyncio.wait_for(manager.stop(), timeout=2)


@pytest.mark.asyncio
async def test_pool_worker_replacement_is_durable_idempotent_and_confirmed_after_repair() -> None:
    generation = await worker_coordinator.acquire_supervisor("test-supervisor")
    assert generation is not None
    worker = await worker_coordinator.activate(
        process_identity="replace-me", supervisor_generation=generation,
    )
    assert worker is not None
    command = await worker_coordinator.control_pool(
        action="replace_worker", idempotency_key="replace-one", requester="test",
        reason="Exercise replacement", worker_id=worker.worker_id,
    )
    duplicate = await worker_coordinator.control_pool(
        action="replace_worker", idempotency_key="replace-one", requester="test",
        reason="Exercise replacement", worker_id=worker.worker_id,
    )
    assert duplicate.id == command.id
    assert command.state == "requested"
    assert "replace-me" in await worker_coordinator.stop_requested_identities(generation)
    await worker_coordinator.worker_exited(
        worker.worker_id, process_identity="replace-me", exit_code=0,
    )
    await worker_coordinator.confirm_pool_commands()
    assert command.state == "requested"
    replacement = await worker_coordinator.activate(
        process_identity="replacement", supervisor_generation=generation,
    )
    assert replacement is not None
    await worker_coordinator.confirm_pool_commands()
    from app.db.session import session_scope
    from app.db.tables import TaskPoolCommand

    async with session_scope() as session:
        persisted = await session.get(TaskPoolCommand, command.id)
        assert persisted is not None and persisted.state == "confirmed"
        assert persisted.affected_worker_ids_json == [worker.worker_id]


@pytest.mark.asyncio
async def test_pool_restart_retires_current_generation_then_waits_for_new_standby() -> None:
    generation = await worker_coordinator.acquire_supervisor("restart-supervisor")
    assert generation is not None
    old = await worker_coordinator.activate(
        process_identity="restart-old", supervisor_generation=generation,
    )
    assert old is not None
    command = await worker_coordinator.control_pool(
        action="restart_pool", idempotency_key="restart-all", requester="test",
        reason="Exercise durable restart",
    )
    assert command.affected_worker_ids_json == [old.worker_id]
    assert "restart-old" in await worker_coordinator.stop_requested_identities(generation)
    await worker_coordinator.worker_exited(
        old.worker_id, process_identity="restart-old", exit_code=0,
    )
    await worker_coordinator.confirm_pool_commands()
    from app.db.session import session_scope
    from app.db.tables import TaskPoolCommand

    async with session_scope() as session:
        persisted = await session.get(TaskPoolCommand, command.id)
        assert persisted is not None and persisted.state == "requested"
    standby = await worker_coordinator.activate(
        process_identity="restart-new", supervisor_generation=generation,
    )
    assert standby is not None
    await worker_coordinator.confirm_pool_commands()
    async with session_scope() as session:
        persisted = await session.get(TaskPoolCommand, command.id)
        assert persisted is not None and persisted.state == "confirmed"


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="Supervised child containment is currently Windows-only")
async def test_supervisor_spawns_one_standby_and_reaps_its_process_tree(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("ALEMBIC_INI_PATH", str(settings.alembic_config_path))
    monkeypatch.setenv("TASK_WORKSPACES_DIR", str(settings.task_workspaces_path))
    original_poll = settings.worker_poll_interval_seconds
    settings.worker_poll_interval_seconds = 0.1
    supervisor = TaskPoolSupervisor(coordinator=worker_coordinator)
    stop = asyncio.Event()
    running = asyncio.create_task(supervisor.run(stop))
    try:
        deadline = asyncio.get_running_loop().time() + 15
        while asyncio.get_running_loop().time() < deadline:
            if running.done():
                await running
            snapshot = await worker_coordinator.pool_snapshot()
            if snapshot.standby == 1:
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("Supervisor did not register a standby child")
        assert len(supervisor.children) == 1
    finally:
        stop.set()
        await asyncio.wait_for(running, timeout=15)
        settings.worker_poll_interval_seconds = original_poll
    assert not supervisor.children
    snapshot = await worker_coordinator.pool_snapshot()
    assert snapshot.standby == 0


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="Supervised child containment is currently Windows-only")
async def test_pool_manager_auto_starts_and_elects_replacement_after_shutdown(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("ALEMBIC_INI_PATH", str(settings.alembic_config_path))
    monkeypatch.setenv("TASK_WORKSPACES_DIR", str(settings.task_workspaces_path))
    original_poll = settings.worker_poll_interval_seconds
    settings.worker_poll_interval_seconds = 0.1

    async def wait_for_standby(min_generation: int) -> None:
        deadline = asyncio.get_running_loop().time() + 15
        while asyncio.get_running_loop().time() < deadline:
            snapshot = await worker_coordinator.pool_snapshot()
            if snapshot.standby == 1 and (snapshot.supervisor_generation or 0) >= min_generation:
                return
            await asyncio.sleep(0.1)
        pytest.fail("Manager did not elect a healthy standby supervisor")

    first = TaskPoolManager(retry_seconds=0.05)
    second = TaskPoolManager(retry_seconds=0.05)
    try:
        await first.start()
        await wait_for_standby(1)
        first_generation = (await worker_coordinator.pool_snapshot()).supervisor_generation
        assert first_generation is not None
        await first.stop()
        await second.start()
        await wait_for_standby(first_generation + 1)
    finally:
        await first.stop()
        await second.stop()
        settings.worker_poll_interval_seconds = original_poll
