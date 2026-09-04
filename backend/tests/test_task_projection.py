from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db.session import session_scope
from app.db.tables import TaskEvent, TaskPresentation, TaskProjectionCheckpoint
from app.services.task_presentations import task_presentation_service
from app.services.task_service import task_service
from app.services.worker_coordinator import worker_coordinator


@pytest.mark.asyncio
async def test_task_attention_projection_advances_incrementally() -> None:
    assert await worker_coordinator.activate(process_identity="projection-worker") is not None
    task = await task_service.dispatch(
        goal="Project incremental progress",
        idempotency_key="projection-task",
        origin_surface="test",
        presentation_policy="follow_progress",
    )
    for index in range(12):
        await task_service.publish_progress(
            task.task.id,
            summary=f"Step {index}",
            idempotency_key=f"projection-progress-{index}",
        )

    assert await task_presentation_service.sync_candidates() == 12
    async with session_scope() as session:
        checkpoint = await session.get(TaskProjectionCheckpoint, "task_attention")
        latest = int((await session.execute(select(func.max(TaskEvent.sequence)))).scalar_one())
        presentations = list((await session.execute(
            select(TaskPresentation).where(TaskPresentation.task_id == task.task.id)
        )).scalars())
    assert checkpoint is not None and checkpoint.last_sequence == latest
    assert sum(item.state == "pending" for item in presentations) == 1
    assert sum(item.state == "superseded" for item in presentations) == 11

    await task_service.set_update_policy(task.task.id, "critical_only")
    assert await task_presentation_service.sync_candidates() == 0
    assert await task_presentation_service.sync_candidates() == 0
    async with session_scope() as session:
        checkpoint = await session.get(TaskProjectionCheckpoint, "task_attention")
        latest = int((await session.execute(select(func.max(TaskEvent.sequence)))).scalar_one())
    assert checkpoint is not None and checkpoint.last_sequence == latest
