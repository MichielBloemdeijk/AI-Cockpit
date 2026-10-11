from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.db.repositories.tasks import TaskRepository
from app.db.tables import ResourceLease, TaskAttempt, TaskOperation, WorkerInstance
from app.main import app
from app.services.jarvis_brain import restricted_brain_gateway
from app.services.task_service import task_service
from app.services.task_worker import TaskWorker
from app.services.worker_coordinator import worker_coordinator
from app.services.windows_job import process_creation_stamp
from app.services.agent_execution import AgentExecutionResult


NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_dispatch_creates_initial_turn_run_and_rejects_changed_idempotent_input() -> None:
    first, duplicate = await asyncio.gather(
        task_service.dispatch(
            goal="Inspect the repository",
            origin_surface="test",
            idempotency_key="m1-dispatch",
        ),
        task_service.dispatch(
            goal="Inspect the repository",
            origin_surface="test",
            idempotency_key="m1-dispatch",
        ),
    )
    assert first.task.id == duplicate.task.id
    detail = await task_service.get_task(first.task.id)
    assert detail is not None
    assert [(turn.sequence, turn.role, turn.content) for turn in detail.turns] == [
        (1, "user", "Inspect the repository")
    ]
    assert len(detail.runs) == 1
    assert detail.runs[0].input_turn_id == detail.turns[0].id
    assert detail.attempts[0].execution_run_id == detail.runs[0].id

    with pytest.raises(ValueError, match="different task input"):
        await task_service.dispatch(
            goal="Change the repository",
            origin_surface="test",
            idempotency_key="m1-dispatch",
        )


@pytest.mark.asyncio
async def test_completed_task_followup_reuses_task_and_workspace_generation() -> None:
    created = await task_service.dispatch(
        goal="Initial work",
        origin_surface="test",
        idempotency_key="m1-followup-task",
    )
    worker = await worker_coordinator.activate(process_identity="m1-followup-worker")
    assert worker is not None
    first_claim = await worker_coordinator.claim_next(worker.worker_id)
    assert first_claim.task is not None
    await task_service.complete(created.task.id, summary="Initial result", idempotency_key="m1-first-done")
    await worker_coordinator.release_claim(
        worker.worker_id,
        first_claim.task.attempt_id,
        claim_token=first_claim.task.claim_token,
    )

    admission = await task_service.continue_task(
        created.task.id,
        content="Refine that result",
        origin="test",
        idempotency_key="m1-followup",
    )
    duplicate = await task_service.continue_task(
        created.task.id,
        content="Refine that result",
        origin="test",
        idempotency_key="m1-followup",
    )
    assert admission.task.id == created.task.id
    assert duplicate.turn.id == admission.turn.id
    second_claim = await worker_coordinator.claim_next(worker.worker_id)
    assert second_claim.task is not None
    assert second_claim.task.execution_run_id == admission.run.id
    assert second_claim.task.workspace_path == first_claim.task.workspace_path

    detail = await task_service.get_task(created.task.id)
    assert detail is not None
    assert [run.ordinal for run in detail.runs] == [1, 2]
    assert [turn.role for turn in detail.turns] == ["user", "assistant", "user"]
    repeated_original = await task_service.dispatch(
        goal="Initial work",
        origin_surface="test",
        idempotency_key="m1-followup-task",
    )
    assert repeated_original.task.id == created.task.id
    assert repeated_original.task.current_run_id == admission.run.id


@pytest.mark.asyncio
async def test_task_history_cursor_reconnects_without_restarting_execution() -> None:
    created = await task_service.dispatch(
        goal="Inspect one", origin_surface="test", idempotency_key="history-one",
    )
    other = await task_service.dispatch(
        goal="Inspect two", origin_surface="test", idempotency_key="history-other",
    )
    first = await task_service.replay_task_events(created.task.id, after_sequence=0, limit=1)
    assert first is not None
    latest, page = first
    assert len(page) == 1 and page[0].task_id == created.task.id
    assert latest >= page[0].sequence
    await task_service.record_event(
        created.task.id, event_type="task.test.progress", payload={"step": 2},
        idempotency_key="history-progress",
    )
    resumed = await task_service.replay_task_events(
        created.task.id, after_sequence=page[0].sequence, limit=100,
    )
    assert resumed is not None
    assert all(event.task_id == created.task.id for event in resumed[1])
    assert any(event.event_type == "task.test.progress" for event in resumed[1])
    assert (await task_service.get_task(other.task.id)) is not None
    detail = await task_service.get_task(created.task.id)
    assert detail is not None and detail.task.current_attempt_id == created.task.current_attempt_id


@pytest.mark.asyncio
async def test_workflow_kind_uses_same_turn_run_and_control_contract(monkeypatch) -> None:
    monkeypatch.setattr(task_service, "workflow_fixture_enabled", True)
    created = await task_service.dispatch(
        goal="Fixture workflow", origin_surface="test", idempotency_key="workflow-contract",
        execution_kind="workflow",
        execution_config={"definition": "fixture@1"},
    )
    first = await worker_coordinator.activate(process_identity="workflow-contract")
    assert first is not None
    claimed = (await worker_coordinator.claim_next(first.worker_id)).task
    assert claimed is not None
    assert claimed.execution_kind == "workflow"
    assert claimed.execution_config == {"definition": "fixture@1"}
    await task_service.complete(created.task.id, summary="Workflow result", idempotency_key="workflow-done")
    await worker_coordinator.release_claim(first.worker_id, claimed.attempt_id)
    continued = await task_service.continue_task(
        created.task.id,
        content="Revise the workflow result",
        origin="test",
        idempotency_key="workflow-followup",
        execution_kind="workflow",
        execution_config={"definition": "fixture@1"},
    )
    assert continued.run.ordinal == 2
    second = (await worker_coordinator.claim_next(first.worker_id)).task
    assert second is not None and second.execution_run_id == continued.run.id
    controlled = await task_service.control(
        created.task.id, action="suspend", idempotency_key="workflow-suspend",
        requester="test", reason="fixture pause", parameters={"immediate": True},
    )
    assert controlled.command.state == "stop_requested"
    confirmed = await task_service.confirm_control(
        created.task.id,
        command_id=controlled.command.id,
        stopped_attempt_id=second.attempt_id,
        stop_evidence={"kind": "fixture_writer_stopped"},
    )
    assert confirmed.task.status == "paused"
    resumed = await task_service.control(
        created.task.id, action="resume", idempotency_key="workflow-resume",
        requester="test", reason="fixture resume",
    )
    assert resumed.task.status == "queued"


@pytest.mark.asyncio
async def test_workflow_fixture_execution_pins_same_portable_run_context(monkeypatch) -> None:
    monkeypatch.setattr(task_service, "workflow_fixture_enabled", True)

    class FixtureRuntime:
        async def execute(self, context, sink, *, cancellation_requested):
            assert context.execution_kind == "workflow"
            await sink.complete("Fixture result", "Fixture evidence")
            return AgentExecutionResult(status="completed", summary="Fixture result")

    created = await task_service.dispatch(
        goal="Fixture workflow", origin_surface="test", idempotency_key="workflow-pinned-context",
        execution_kind="workflow", execution_config={"definition": "fixture@1"},
    )
    worker = TaskWorker(runtime=FixtureRuntime())
    assert await worker.activate(process_identity="workflow-pinned-context-worker")
    assert (await worker.run_once()).status == "completed"
    detail = await task_service.get_task(created.task.id)
    assert detail is not None
    assert detail.runs[0].context_reference is not None
    assert any(event.event_type == "task.context.pinned" for event in detail.events)


@pytest.mark.asyncio
async def test_workflow_admission_fails_closed_without_registered_runtime() -> None:
    assert task_service.workflow_fixture_enabled is False
    with pytest.raises(ValueError, match="constrained runtime"):
        await task_service.dispatch(
            goal="Do a workflow", origin_surface="test", idempotency_key="workflow-disabled",
            execution_kind="workflow",
        )


@pytest.mark.asyncio
async def test_legacy_workspace_path_is_reused_by_new_followup_run(tmp_path) -> None:
    created = await task_service.dispatch(
        goal="Legacy workspace goal",
        origin_surface="test",
        idempotency_key="m1-legacy-workspace",
    )
    legacy_path = tmp_path / "prior-workspace"
    async with session_scope() as session:
        attempt = await session.get(TaskAttempt, created.task.current_attempt_id)
        assert attempt is not None
        attempt.workspace_path = str(legacy_path)
    worker = await worker_coordinator.activate(process_identity="m1-legacy-workspace-worker")
    assert worker is not None
    first = await worker_coordinator.claim_next(worker.worker_id)
    assert first.task is not None and Path(first.task.workspace_path) == legacy_path
    await task_service.complete(created.task.id, summary="First result", idempotency_key="m1-legacy-first")
    await worker_coordinator.release_claim(worker.worker_id, first.task.attempt_id)
    await task_service.continue_task(
        created.task.id,
        content="Continue here",
        origin="test",
        idempotency_key="m1-legacy-followup",
    )
    second = await worker_coordinator.claim_next(worker.worker_id)
    assert second.task is not None and Path(second.task.workspace_path) == legacy_path


@pytest.mark.asyncio
async def test_followup_waits_for_prior_writer_exit_even_without_explicit_targets() -> None:
    created = await task_service.dispatch(
        goal="First writer",
        origin_surface="test",
        idempotency_key="m1-private-workspace-fence",
    )
    first_identity = "m1-private-first"
    first = await worker_coordinator.activate(process_identity=first_identity)
    assert first is not None
    claim = await worker_coordinator.claim_next(first.worker_id)
    assert claim.task is not None
    await task_service.complete(created.task.id, summary="First done", idempotency_key="m1-private-done")
    await task_service.continue_task(
        created.task.id,
        content="Second writer",
        origin="test",
        idempotency_key="m1-private-followup",
    )
    second = await worker_coordinator.activate(process_identity="m1-private-second")
    assert second is not None
    blocked = await worker_coordinator.claim_next(second.worker_id)
    assert blocked.task is None and blocked.blocked_task_id == created.task.id
    await worker_coordinator.worker_exited(
        first.worker_id,
        process_identity=first_identity,
        exit_code=0,
    )
    ready = await worker_coordinator.claim_next(second.worker_id)
    assert ready.task is not None and ready.task.task_id == created.task.id


@pytest.mark.asyncio
async def test_concurrent_followups_are_serialized_and_promoted_in_order() -> None:
    created = await task_service.dispatch(
        goal="Start",
        origin_surface="test",
        idempotency_key="m1-serialized",
    )
    worker = await worker_coordinator.activate(process_identity="m1-serialized-worker")
    assert worker is not None
    claim = await worker_coordinator.claim_next(worker.worker_id)
    assert claim.task is not None
    second, third = await asyncio.gather(
        task_service.continue_task(
            created.task.id, content="Second", origin="test", idempotency_key="second"
        ),
        task_service.continue_task(
            created.task.id, content="Third", origin="test", idempotency_key="third"
        ),
    )
    assert {second.run.ordinal, third.run.ordinal} == {2, 3}
    await task_service.complete(created.task.id, summary="First done", idempotency_key="first-terminal")
    detail = await task_service.get_task(created.task.id)
    assert detail is not None
    assert detail.task.status == "queued"
    assert detail.task.current_run_id == next(run.id for run in detail.runs if run.ordinal == 2)
    assert next(run.status for run in detail.runs if run.ordinal == 3) == "pending"


@pytest.mark.asyncio
async def test_immediate_restart_fences_late_worker_writes() -> None:
    created = await task_service.dispatch(
        goal="Fence me",
        origin_surface="test",
        idempotency_key="m1-fence",
    )
    worker = await worker_coordinator.activate(process_identity="m1-fence-worker")
    assert worker is not None
    claim = await worker_coordinator.claim_next(worker.worker_id)
    assert claim.task is not None
    controlled = await task_service.control(
        created.task.id,
        action="restart_execution",
        idempotency_key="m1-restart",
        parameters={"immediate": True},
        expected_run_id=claim.task.execution_run_id,
    )
    assert controlled.command.state == "stop_requested"
    with pytest.raises(ValueError, match="Stale task execution authority"):
        await task_service.publish_progress(
            created.task.id,
            summary="late write",
            idempotency_key="late-write",
            attempt_id=claim.task.attempt_id,
            execution_run_id=claim.task.execution_run_id,
            claim_token=claim.task.claim_token,
        )
    await task_service.confirm_control(
        created.task.id,
        command_id=controlled.command.id,
        stopped_attempt_id=claim.task.attempt_id,
        stop_evidence={"kind": "test_process_exit"},
    )
    await worker_coordinator.release_claim(worker.worker_id, claim.task.attempt_id)
    detail = await task_service.get_task(created.task.id)
    assert detail is not None
    assert detail.task.status == "queued"
    assert [attempt.attempt_number for attempt in detail.attempts] == [1, 2]


@pytest.mark.asyncio
async def test_result_artifact_and_terminal_state_roll_back_together(monkeypatch) -> None:
    created = await task_service.dispatch(
        goal="Atomic result",
        origin_surface="test",
        idempotency_key="m1-atomic-result",
    )
    worker = await worker_coordinator.activate(process_identity="m1-atomic-worker")
    assert worker is not None
    claim = await worker_coordinator.claim_next(worker.worker_id)
    assert claim.task is not None
    original = TaskRepository.append_event

    async def reject_terminal(self, *args, **kwargs):
        if kwargs.get("event_type") == "task.completed":
            raise RuntimeError("terminal projection failed")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(TaskRepository, "append_event", reject_terminal)
    with pytest.raises(RuntimeError, match="terminal projection failed"):
        await task_service.complete(
            created.task.id,
            summary="Done",
            final_text="Full result",
            idempotency_key="m1-atomic-complete",
            attempt_id=claim.task.attempt_id,
            execution_run_id=claim.task.execution_run_id,
            claim_token=claim.task.claim_token,
        )
    detail = await task_service.get_task(created.task.id)
    assert detail is not None and detail.task.status == "running"
    assert detail.artifacts == ()
    assert detail.runs[0].result_reference is None


@pytest.mark.asyncio
async def test_cooperative_control_acknowledges_boundary_before_process_exit() -> None:
    created = await task_service.dispatch(
        goal="Yield at a safe boundary",
        origin_surface="test",
        idempotency_key="m1-cooperative-control",
    )
    worker = await worker_coordinator.activate(process_identity="m1-cooperative-worker")
    assert worker is not None
    claim = await worker_coordinator.claim_next(worker.worker_id)
    assert claim.task is not None
    command = await task_service.control(
        created.task.id,
        action="yield",
        idempotency_key="m1-cooperative-yield",
        parameters={"immediate": False},
    )
    assert command.command.state == "requested"
    await task_service.acknowledge_control_boundary(
        created.task.id,
        command_id=command.command.id,
        attempt_id=claim.task.attempt_id,
    )
    pending = await task_service.pending_control(created.task.id, attempt_id=claim.task.attempt_id)
    assert pending is not None and pending.state == "stop_requested"
    with pytest.raises(ValueError, match="Stale task execution authority"):
        await task_service.publish_progress(
            created.task.id,
            summary="too late",
            idempotency_key="m1-cooperative-late",
            attempt_id=claim.task.attempt_id,
            execution_run_id=claim.task.execution_run_id,
            claim_token=claim.task.claim_token,
        )
    await worker_coordinator.worker_exited(
        worker.worker_id,
        process_identity="m1-cooperative-worker",
        exit_code=1,
    )
    detail = await task_service.get_task(created.task.id)
    assert detail is not None and detail.task.status == "queued"


@pytest.mark.asyncio
async def test_observed_worker_exit_marks_inflight_operation_uncertain() -> None:
    created = await task_service.dispatch(
        goal="Perform action",
        origin_surface="test",
        idempotency_key="m1-operation",
    )
    identity = "m1-operation-worker"
    worker = await worker_coordinator.activate(process_identity=identity)
    assert worker is not None
    claim = await worker_coordinator.claim_next(worker.worker_id)
    assert claim.task is not None
    operation = await task_service.start_operation(
        created.task.id,
        operation_key="external-action-1",
        operation_type="fixture.write",
        input_digest="digest",
        attempt_id=claim.task.attempt_id,
        execution_run_id=claim.task.execution_run_id,
        claim_token=claim.task.claim_token,
    )
    recovered = await worker_coordinator.worker_exited(
        worker.worker_id,
        process_identity=identity,
        exit_code=9,
    )
    assert recovered == ()
    detail = await task_service.get_task(created.task.id)
    assert detail is not None and detail.task.status == "interrupted"
    async with session_scope() as session:
        saved = await session.get(TaskOperation, operation.id)
        assert saved is not None and saved.state == "uncertain"


@pytest.mark.asyncio
async def test_observed_worker_exit_recovers_once_from_checkpoint_then_interrupts() -> None:
    created = await task_service.dispatch(
        goal="Recover safely",
        origin_surface="test",
        idempotency_key="m1-recovery",
        targets=(("directory", "projects/recovery", True),),
    )
    first_identity = "m1-recovery-worker-1"
    first = await worker_coordinator.activate(process_identity=first_identity)
    assert first is not None
    first_claim = await worker_coordinator.claim_next(first.worker_id)
    assert first_claim.task is not None
    await task_service.save_checkpoint(
        created.task.id,
        checkpoint={"step": 3, "completed_operations": ["safe-1"]},
        attempt_id=first_claim.task.attempt_id,
        execution_run_id=first_claim.task.execution_run_id,
        claim_token=first_claim.task.claim_token,
    )
    recovered = await worker_coordinator.worker_exited(
        first.worker_id,
        process_identity=first_identity,
        exit_code=7,
    )
    assert recovered == (created.task.id,)
    detail = await task_service.get_task(created.task.id)
    assert detail is not None and detail.task.status == "queued"
    assert [attempt.attempt_number for attempt in detail.attempts] == [1, 2]
    async with session_scope() as session:
        leases = list((await session.execute(select(ResourceLease))).scalars())
        assert len(leases) == 2
        assert all(lease.owner_attempt_id == detail.attempts[1].id for lease in leases)

    second_identity = "m1-recovery-worker-2"
    second = await worker_coordinator.activate(process_identity=second_identity)
    assert second is not None
    second_claim = await worker_coordinator.claim_next(second.worker_id)
    assert second_claim.task is not None
    assert second_claim.task.checkpoint == {"step": 3, "completed_operations": ["safe-1"]}
    assert second_claim.task.workspace_path == first_claim.task.workspace_path
    recovered_again = await worker_coordinator.worker_exited(
        second.worker_id,
        process_identity=second_identity,
        exit_code=8,
    )
    assert recovered_again == ()
    interrupted = await task_service.get_task(created.task.id)
    assert interrupted is not None and interrupted.task.status == "interrupted"
    async with session_scope() as session:
        assert list((await session.execute(select(ResourceLease))).scalars().all()) == []


@pytest.mark.asyncio
async def test_directory_ancestor_lease_blocks_child_but_not_unrelated_task() -> None:
    holder = await task_service.dispatch(
        goal="Hold root",
        origin_surface="test",
        idempotency_key="m1-holder",
        targets=(("directory", "projects/root", True),),
    )
    first = await worker_coordinator.activate(process_identity="m1-root-worker")
    assert first is not None
    held = await worker_coordinator.claim_next(first.worker_id)
    assert held.task is not None and held.task.task_id == holder.task.id

    child = await task_service.dispatch(
        goal="Edit child",
        origin_surface="test",
        idempotency_key="m1-child",
        targets=(("directory", "projects/root/child", True),),
    )
    free = await task_service.dispatch(
        goal="Edit elsewhere",
        origin_surface="test",
        idempotency_key="m1-free",
        targets=(("directory", "projects/elsewhere", True),),
    )
    second = await worker_coordinator.activate(process_identity="m1-second-worker")
    assert second is not None
    claim = await worker_coordinator.claim_next(second.worker_id)
    assert claim.task is not None and claim.task.task_id == free.task.id
    assert (await task_service.get_task(child.task.id)).task.status == "queued"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_expired_heartbeat_never_releases_unconfirmed_writer_lease() -> None:
    holder = await task_service.dispatch(
        goal="Hold directory",
        origin_surface="test",
        idempotency_key="m1-expired-holder",
        targets=(("directory", "projects/expired-holder", True),),
    )
    worker = await worker_coordinator.activate(process_identity="m1-expired-worker", now=NOW)
    assert worker is not None
    claim = await worker_coordinator.claim_next(worker.worker_id, now=NOW)
    assert claim.task is not None
    blocked = await task_service.dispatch(
        goal="Write the same directory",
        origin_surface="test",
        idempotency_key="m1-expired-blocked",
        targets=(("directory", "projects/expired-holder/subdir", True),),
    )
    await worker_coordinator.reconcile_availability(
        now=NOW + timedelta(seconds=settings.worker_stale_after_seconds + 1)
    )
    second = await worker_coordinator.activate(
        process_identity="m1-expired-second",
        now=NOW + timedelta(seconds=settings.worker_stale_after_seconds + 2),
    )
    assert second is not None
    no_claim = await worker_coordinator.claim_next(
        second.worker_id,
        now=NOW + timedelta(seconds=settings.worker_lease_ttl_seconds + 5),
    )
    assert no_claim.task is None
    assert no_claim.blocked_task_id == blocked.task.id
    async with session_scope() as session:
        assert len(list((await session.execute(select(ResourceLease))).scalars())) == 2
    await worker_coordinator.worker_exited(
        worker.worker_id,
        process_identity="m1-expired-worker",
        exit_code=9,
    )
    available = await worker_coordinator.claim_next(second.worker_id)
    assert available.task is not None and available.task.task_id == blocked.task.id


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="Windows exact process-exit observation")
async def test_stale_fallback_releases_lease_only_after_exact_process_exit() -> None:
    created = await task_service.dispatch(
        goal="Observe process death",
        origin_surface="test",
        idempotency_key="m1-observed-stale",
        targets=(("directory", "projects/observed-stale", True),),
    )
    worker = await worker_coordinator.activate(
        process_identity="m1-observed-process", now=NOW
    )
    assert worker is not None
    claim = await worker_coordinator.claim_next(worker.worker_id, now=NOW)
    assert claim.task is not None
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(0.3)"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    stamp = process_creation_stamp(process.pid)
    assert stamp is not None
    async with session_scope() as session:
        row = await session.get(WorkerInstance, worker.worker_id)
        assert row is not None
        row.metadata_json = {"pid": process.pid, "process_creation_stamp": stamp}
    process.wait(timeout=5)
    await worker_coordinator.reconcile_availability(
        now=NOW + timedelta(seconds=settings.worker_stale_after_seconds + 1)
    )
    detail = await task_service.get_task(created.task.id)
    assert detail is not None and detail.task.status == "interrupted"
    async with session_scope() as session:
        assert list((await session.execute(select(ResourceLease))).scalars()) == []


@pytest.mark.asyncio
async def test_pool_caps_eight_busy_workers_and_keeps_one_standby() -> None:
    workers = []
    for index in range(settings.worker_max_busy):
        await task_service.dispatch(
            goal=f"Task {index}",
            origin_surface="test",
            idempotency_key=f"m1-pool-task-{index}",
        )
        worker = await worker_coordinator.activate(
            process_identity=f"m1-pool-{index}",
            worker_id=f"m1-pool-{index}",
        )
        assert worker is not None
        claim = await worker_coordinator.claim_next(worker.worker_id)
        assert claim.task is not None
        workers.append(worker)
    standby = await worker_coordinator.activate(
        process_identity="m1-standby",
        worker_id="m1-standby",
    )
    assert standby is not None
    rejected = await worker_coordinator.activate(
        process_identity="m1-overflow",
        worker_id="m1-overflow",
    )
    assert rejected is None
    snapshot = await worker_coordinator.pool_snapshot()
    assert snapshot.busy == settings.worker_max_busy
    assert snapshot.standby == settings.worker_standby_count
    assert snapshot.saturated is True
    await worker_coordinator.retire(workers[0].worker_id)
    still_saturated = await worker_coordinator.pool_snapshot()
    assert still_saturated.busy == settings.worker_max_busy
    queued = await task_service.dispatch(
        goal="Wait for exact retiring writer exit", origin_surface="test",
        idempotency_key="m1-retiring-capacity",
    )
    no_claim = await worker_coordinator.claim_next(standby.worker_id)
    assert no_claim.task is None
    assert (await task_service.get_task(queued.task.id)).task.status == "queued"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_eight_deterministic_busy_workers_publish_without_uncaught_sqlite_locks(monkeypatch) -> None:
    monkeypatch.setattr(task_service, "workflow_fixture_enabled", True)
    entered = 0
    all_entered = asyncio.Event()
    entered_events = [asyncio.Event() for _ in range(8)]
    release = asyncio.Event()

    class FixtureRuntime:
        async def execute(self, context, sink, *, cancellation_requested):
            nonlocal entered
            entered += 1
            entered_events[entered - 1].set()
            if entered == 8:
                all_entered.set()
            await release.wait()
            for step in range(5):
                await sink.progress(f"fixture step {step}")
                await sink.checkpoint({"step": step})
            await sink.complete("Fixture completed", f"Fixture result for {context.task_id}")
            return AgentExecutionResult(status="completed", summary="Fixture completed")

    runtime = FixtureRuntime()
    for index in range(8):
        await task_service.dispatch(
            goal=f"Fixture task {index}",
            origin_surface="test",
            idempotency_key=f"m1-stress-task-{index}",
        )
    workers = []
    executions = []
    for index in range(8):
        worker = TaskWorker(runtime=runtime)
        assert await worker.activate(process_identity=f"m1-stress-worker-{index}")
        workers.append(worker)
        executions.append(asyncio.create_task(worker.run_once()))
        await asyncio.wait_for(entered_events[index].wait(), timeout=15)
    await asyncio.wait_for(all_entered.wait(), timeout=15)
    snapshot = await worker_coordinator.pool_snapshot()
    assert snapshot.busy == 8
    async def admit(index: int) -> float:
        started = time.perf_counter()
        await task_service.dispatch(
            goal=f"Admitted while saturated {index}",
            origin_surface="test",
            idempotency_key=f"m1-stress-queued-{index}",
            execution_kind="workflow",
            execution_config={"definition": "fixture@1"},
        )
        return time.perf_counter() - started

    latencies = await asyncio.gather(*(admit(index) for index in range(16)))
    p95 = sorted(latencies)[15]  # nearest-rank p95 of 16 observations
    assert p95 < 2.0, f"Saturated admission p95={p95:.3f}s"
    release.set()
    results = await asyncio.wait_for(asyncio.gather(*executions), timeout=30)
    assert [result.status for result in results] == ["completed"] * 8
    assert len([task for task in await task_service.list_tasks() if task.task.status == "completed"]) == 8


@pytest.mark.asyncio
async def test_supervisor_generation_lease_fails_over_only_after_expiry() -> None:
    first = await worker_coordinator.acquire_supervisor("supervisor-a", now=NOW)
    assert first == 1
    assert await worker_coordinator.acquire_supervisor("supervisor-b", now=NOW) is None
    second = await worker_coordinator.acquire_supervisor(
        "supervisor-b",
        now=NOW + timedelta(seconds=settings.supervisor_lease_ttl_seconds + 1),
    )
    assert second == 2
    assert await worker_coordinator.heartbeat_supervisor("supervisor-a", 1, now=NOW) is False


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="Windows exact process identity")
async def test_supervisor_lease_takes_over_immediately_after_verified_owner_exit(monkeypatch) -> None:
    owner = f"{socket.gethostname()}:123456:987654321:owner"
    assert await worker_coordinator.acquire_supervisor(owner, now=NOW) == 1
    monkeypatch.setattr(
        "app.services.worker_coordinator.process_has_exited", lambda *_: False,
    )
    assert await worker_coordinator.acquire_supervisor("replacement", now=NOW) is None
    monkeypatch.setattr(
        "app.services.worker_coordinator.process_has_exited", lambda *_: True,
    )
    assert await worker_coordinator.acquire_supervisor("replacement", now=NOW) == 2


@pytest.mark.asyncio
async def test_old_supervisor_generation_cannot_renew_or_publish_after_failover() -> None:
    assert await worker_coordinator.acquire_supervisor("generation-one", now=NOW) == 1
    created = await task_service.dispatch(
        goal="Generation fenced",
        origin_surface="test",
        idempotency_key="m1-generation-fence",
    )
    old = await worker_coordinator.activate(
        process_identity="m1-generation-old", supervisor_generation=1, now=NOW
    )
    assert old is not None
    claim = await worker_coordinator.claim_next(old.worker_id, now=NOW)
    assert claim.task is not None
    assert await worker_coordinator.acquire_supervisor(
        "generation-two",
        now=NOW + timedelta(seconds=settings.supervisor_lease_ttl_seconds + 1),
    ) == 2
    assert await worker_coordinator.renew_claim(
        old.worker_id,
        claim.task.attempt_id,
        claim_token=claim.task.claim_token,
    ) is False
    with pytest.raises(ValueError, match="supervisor generation"):
        await task_service.publish_progress(
            created.task.id,
            summary="late old generation",
            idempotency_key="m1-generation-late",
            attempt_id=claim.task.attempt_id,
            execution_run_id=claim.task.execution_run_id,
            claim_token=claim.task.claim_token,
        )


@pytest.mark.asyncio
async def test_turn_control_and_pool_http_contracts() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": ""})
        created = await client.post(
            "/api/tasks",
            json={"goal": "API task", "idempotency_key": "m1-api-task"},
        )
        assert created.status_code == 201
        task_id = created.json()["id"]
        cancelled = await client.post(
            f"/api/tasks/{task_id}/control",
            json={"action": "cancel", "idempotency_key": "m1-api-cancel"},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["command"]["state"] == "confirmed"
        turn = await client.post(
            f"/api/tasks/{task_id}/turns",
            json={"content": "Continue through API", "idempotency_key": "m1-api-turn"},
        )
        assert turn.status_code == 201
        assert turn.json()["task_id"] == task_id
        history = await client.get(f"/api/tasks/{task_id}/events", params={"after": 0, "limit": 1})
        assert history.status_code == 200
        first_event = history.json()["events"][0]
        reconnected = await client.get(
            f"/api/tasks/{task_id}/events", params={"after": first_event["sequence"]}
        )
        assert reconnected.status_code == 200
        assert all(item["task_id"] == task_id for item in reconnected.json()["events"])
        assert all(
            item["sequence"] > first_event["sequence"]
            for item in reconnected.json()["events"]
        )
        pool = await client.get("/api/tasks/pool")
        assert pool.status_code == 200
        assert pool.json()["max_busy"] == settings.worker_max_busy
        assert pool.json()["enabled"] is False
        assert isinstance(pool.json()["workers"], list)


@pytest.mark.asyncio
async def test_authenticated_pool_control_endpoint_is_idempotent(monkeypatch) -> None:
    monkeypatch.setattr(settings, "task_pool_enabled", True)
    generation = await worker_coordinator.acquire_supervisor("http-pool")
    assert generation is not None
    worker = await worker_coordinator.activate(
        process_identity="http-pool-worker", supervisor_generation=generation,
    )
    assert worker is not None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        unauthenticated = await client.post(
            "/api/tasks/pool/control",
            json={"action": "replace_worker", "idempotency_key": "http-replace", "reason": "test", "worker_id": worker.worker_id},
        )
        assert unauthenticated.status_code in {401, 403}
        await client.post("/api/auth/login", json={"password": ""})
        request = {
            "action": "replace_worker", "idempotency_key": "http-replace",
            "reason": "test", "worker_id": worker.worker_id,
        }
        first = await client.post("/api/tasks/pool/control", json=request)
        repeated = await client.post("/api/tasks/pool/control", json=request)
        assert first.status_code == repeated.status_code == 200
        assert first.json()["id"] == repeated.json()["id"]
        assert first.json()["affected_worker_ids"] == [worker.worker_id]


@pytest.mark.asyncio
async def test_jarvis_continuation_and_control_share_task_contract() -> None:
    created = await task_service.dispatch(
        goal="Draft a note",
        origin_surface="jarvis",
        idempotency_key="m1-jarvis-task",
    )
    base = {"conversation_id": "test-conversation", "origin_turn_id": None}
    controlled = await restricted_brain_gateway.execute(
        tool_name="control_task",
        arguments={
            "task_id": created.task.id,
            "action": "set_importance",
            "importance": "high",
            "reason": "The user prioritized the note",
        },
        call_id="m1-jarvis-priority",
        **base,
    )
    assert controlled.output["command_state"] == "confirmed"
    cancelled = await restricted_brain_gateway.execute(
        tool_name="control_task",
        arguments={
            "task_id": created.task.id,
            "action": "cancel",
            "reason": "The user changed direction",
        },
        call_id="m1-jarvis-cancel",
        **base,
    )
    assert cancelled.output["status"] == "cancelled"
    continued = await restricted_brain_gateway.execute(
        tool_name="continue_task",
        arguments={"task_id": created.task.id, "content": "Draft the revised note"},
        call_id="m1-jarvis-continue",
        **base,
    )
    assert continued.output["run_ordinal"] == 2
    detail = await task_service.get_task(created.task.id)
    assert detail is not None
    assert [run.ordinal for run in detail.runs] == [1, 2]
    assert any(control.requester == "jarvis" for control in detail.controls)
