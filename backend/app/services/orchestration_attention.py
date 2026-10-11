"""Judged unresolved attention through the existing canonical inbox."""
from sqlalchemy import select

from app.db.repositories.tasks import TaskRepository
from app.db.tables import Task, TaskEvent


async def retain_attention(session, decision, event_key: str) -> bool:
    from app.services.task_presentations import task_presentation_service
    task = (await session.execute(select(Task).where(Task.id == decision.task_id))).scalar_one()
    source = (await session.execute(select(TaskEvent).where(
        TaskEvent.id.in_(decision.source_event_ids_json),
        TaskEvent.event_type.in_(["task.requires_input", "task.failed", "task.interrupted"]),
    ).order_by(TaskEvent.sequence.desc()).limit(1))).scalar_one_or_none()
    if source is None and task.status in {"failed", "interrupted"}:
        source = await TaskRepository(session).append_event(
            task_id=task.id, execution_run_id=task.current_run_id, event_type=f"task.{task.status}",
            payload_json={"status": task.status, "summary": task.terminal_summary, "decision_id": decision.id},
            causality_json={"source_event_ids": decision.source_event_ids_json},
            idempotency_key=f"{event_key}:attention",
        )
    if source:
        if source.id not in decision.source_event_ids_json:
            decision.source_event_ids_json = [*decision.source_event_ids_json, source.id]
        await task_presentation_service._project_batch(session, [source], judged_event_ids=frozenset({source.id}))
    return source is not None
