from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.db.session import session_scope
from app.db.tables import ResourceLease, Task, TaskAttempt, WorkerInstance
from app.services.conversation_store import conversation_store
from app.services.background_tasks import delegate_background_task
from app.services.task_service import task_service
from app.services.worker_coordinator import worker_coordinator


NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_only_one_worker_becomes_active_under_contention() -> None:
    registrations = await asyncio.gather(
        worker_coordinator.activate(process_identity="worker-a", worker_id="worker-a", now=NOW),
        worker_coordinator.activate(process_identity="worker-b", worker_id="worker-b", now=NOW),
    )

    winners = [registration for registration in registrations if registration is not None]
    assert len(winners) == 1
    async with session_scope() as session:
        workers = list((await session.execute(select(WorkerInstance))).scalars().all())
    assert sum(worker.state == "idle" for worker in workers) == 1
    assert sum(worker.state == "rejected" for worker in workers) == 1


@pytest.mark.asyncio
async def test_active_worker_is_projected_as_online() -> None:
    worker = await worker_coordinator.activate(process_identity="health", worker_id="health-worker")
    assert worker is not None
    health = await task_service.worker_health()
    assert health.status == "online"
    assert health.worker_id == worker.worker_id


@pytest.mark.asyncio
async def test_strict_fifo_claim_is_stable_and_one_attempt_is_claimed() -> None:
    first = await task_service.dispatch(goal="first", origin_surface="test", idempotency_key="fifo-1")
    second = await task_service.dispatch(goal="second", origin_surface="test", idempotency_key="fifo-2")
    worker = await worker_coordinator.activate(process_identity="fifo", worker_id="fifo-worker", now=NOW)
    assert worker is not None

    claimed, concurrent = await asyncio.gather(
        worker_coordinator.claim_next(worker.worker_id, now=NOW),
        worker_coordinator.claim_next(worker.worker_id, now=NOW),
    )
    results = [result for result in (claimed, concurrent) if result.task is not None]
    assert len(results) == 1
    assert results[0].task.task_id == first.task.id
    assert results[0].task.attempt_number == 1
    assert results[0].task.workspace_path.endswith("attempt-1")

    await task_service.complete(first.task.id, summary="done", idempotency_key="fifo-done")
    await worker_coordinator.release_claim(worker.worker_id, results[0].task.attempt_id)
    next_claim = await worker_coordinator.claim_next(worker.worker_id, now=NOW + timedelta(seconds=1))
    assert next_claim.task is not None
    assert next_claim.task.task_id == second.task.id


@pytest.mark.asyncio
async def test_lease_blocked_head_is_not_bypassed_and_acquisition_is_all_or_nothing() -> None:
    blocker = await task_service.dispatch(goal="holder", origin_surface="test", idempotency_key="lease-holder")
    async with session_scope() as session:
        blocker_task = (await session.execute(select(Task).where(Task.id == blocker.task.id))).scalar_one()
        blocker_task.status = "running"
        owner = await session.get(TaskAttempt, blocker_task.current_attempt_id)
        assert owner is not None
        session.add(ResourceLease(
            id="blocking-lease",
            resource_type="repository",
            resource_key="shared",
            owner_attempt_id=owner.id,
            acquired_at=NOW,
            renewed_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
        ))

    head = await task_service.dispatch(
        goal="blocked head",
        origin_surface="test",
        idempotency_key="blocked-head",
        targets=(("directory", "free", True), ("repository", "shared", True)),
    )
    tail = await task_service.dispatch(goal="free tail", origin_surface="test", idempotency_key="free-tail")
    worker = await worker_coordinator.activate(process_identity="lease", worker_id="lease-worker", now=NOW)
    assert worker is not None

    result = await worker_coordinator.claim_next(worker.worker_id, now=NOW)
    assert result.task is None
    assert result.blocked_task_id == head.task.id
    assert (await task_service.get_task(tail.task.id)).task.status == "queued"  # type: ignore[union-attr]
    async with session_scope() as session:
        acquired_free = (await session.execute(
            select(ResourceLease).where(
                ResourceLease.resource_type == "directory",
                ResourceLease.resource_key == "free",
            )
        )).scalar_one_or_none()
        assert acquired_free is None
        await session.execute(delete(ResourceLease).where(ResourceLease.id == "blocking-lease"))

    claimed = await worker_coordinator.claim_next(worker.worker_id, now=NOW + timedelta(seconds=1))
    assert claimed.task is not None and claimed.task.task_id == head.task.id
    async with session_scope() as session:
        leases = list((await session.execute(
            select(ResourceLease).where(ResourceLease.owner_attempt_id == claimed.task.attempt_id)
            .order_by(ResourceLease.resource_type, ResourceLease.resource_key)
        )).scalars().all())
    assert [(lease.resource_type, lease.resource_key) for lease in leases] == [
        ("directory", "free"),
        ("repository", "shared"),
    ]


@pytest.mark.asyncio
async def test_target_expansion_checkpoints_releases_and_requeues_same_task() -> None:
    created = await task_service.dispatch(
        goal="discover targets",
        origin_surface="test",
        idempotency_key="expand-task",
        targets=(("repository", "one", True),),
    )
    worker = await worker_coordinator.activate(process_identity="expand", worker_id="expand-worker", now=NOW)
    assert worker is not None
    claimed = await worker_coordinator.claim_next(worker.worker_id, now=NOW)
    assert claimed.task is not None

    next_attempt_id = await worker_coordinator.checkpoint_and_requeue(
        worker_id=worker.worker_id,
        task_id=created.task.id,
        attempt_id=claimed.task.attempt_id,
        checkpoint={"step": 4},
        expanded_targets=(("repository", "two", True),),
        idempotency_key="expand-once",
    )
    retried_attempt_id = await worker_coordinator.checkpoint_and_requeue(
        worker_id=worker.worker_id,
        task_id=created.task.id,
        attempt_id=claimed.task.attempt_id,
        checkpoint={"step": 4},
        expanded_targets=(("repository", "two", True),),
        idempotency_key="expand-once",
    )
    assert retried_attempt_id == next_attempt_id
    detail = await task_service.get_task(created.task.id)
    assert detail is not None
    assert detail.task.status == "queued"
    assert [attempt.attempt_number for attempt in detail.attempts] == [1, 2]
    assert detail.attempts[0].terminal_reason == "target_expansion"
    assert detail.attempts[1].id == next_attempt_id
    assert detail.attempts[1].checkpoint_json == {"step": 4}
    async with session_scope() as session:
        assert (await session.execute(select(ResourceLease))).scalars().all() == []

    reclaimed = await worker_coordinator.claim_next(worker.worker_id, now=NOW + timedelta(seconds=1))
    assert reclaimed.task is not None
    assert reclaimed.task.task_id == created.task.id
    assert reclaimed.task.attempt_number == 2
    assert reclaimed.task.checkpoint == {"step": 4}
    assert [(item[0], item[1]) for item in reclaimed.task.targets] == [
        ("repository", "one"),
        ("repository", "two"),
    ]


@pytest.mark.asyncio
async def test_stale_takeover_interrupts_without_retry_and_releases_leases() -> None:
    created = await task_service.dispatch(
        goal="lose worker",
        origin_surface="test",
        idempotency_key="stale-task",
        targets=(("repository", "stale", True),),
    )
    old = await worker_coordinator.activate(process_identity="old", worker_id="old-worker", now=NOW)
    assert old is not None
    claim = await worker_coordinator.claim_next(old.worker_id, now=NOW)
    assert claim.task is not None

    replacement = await worker_coordinator.activate(
        process_identity="new",
        worker_id="new-worker",
        now=NOW + timedelta(seconds=30),
    )
    assert replacement is not None
    assert replacement.took_over == (created.task.id,)
    detail = await task_service.get_task(created.task.id)
    assert detail is not None
    assert detail.task.status == "interrupted"
    assert len(detail.attempts) == 1
    assert detail.attempts[0].terminal_reason == "worker_stale"
    async with session_scope() as session:
        assert (await session.execute(select(ResourceLease))).scalars().all() == []


@pytest.mark.asyncio
async def test_api_restart_does_not_change_worker_owned_task() -> None:
    conversation = await conversation_store.create_conversation(title="Origin")
    parent = await conversation_store.start_run(conversation.id, kind="assistant")
    child, _ = await delegate_background_task(
        parent_run_id=parent.id,
        goal="survive API",
        idempotency_key="api-restart",
    )
    await conversation_store.transition_run_status(
        parent.id,
        expected_statuses={"running"},
        status="completed",
    )
    created = await task_service.get_task(str(child.metadata_json["global_task_id"]))
    assert created is not None
    worker = await worker_coordinator.activate(process_identity="api", worker_id="api-worker", now=NOW)
    assert worker is not None
    claimed = await worker_coordinator.claim_next(worker.worker_id, now=NOW)
    assert claimed.task is not None

    assert await conversation_store.mark_running_runs_interrupted() == 0
    detail = await task_service.get_task(created.task.id)
    assert detail is not None and detail.task.status == "running"
    compatibility = await conversation_store.get_run(child.id)
    assert compatibility is not None and compatibility.status == "queued"
