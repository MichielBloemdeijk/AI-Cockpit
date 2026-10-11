"""Shared task projection for direct APIs and Live state."""
from app.db.tables import Task
from app.models.tasks import TaskSummaryView


def present_task_summary(task: Task, queue_position: int | None) -> TaskSummaryView:
    return TaskSummaryView(
        id=task.id,
        origin_surface=task.origin_surface,
        origin_conversation_id=task.origin_conversation_id,
        origin_turn_id=task.origin_turn_id,
        goal=task.goal,
        execution_kind=task.execution_kind,
        status=task.status,
        status_reason=task.status_reason,
        creation_order=task.creation_order,
        current_attempt_id=task.current_attempt_id,
        current_run_id=task.current_run_id,
        pending_control_id=task.pending_control_id,
        goal_revision=task.goal_revision,
        control_revision=task.control_revision,
        workspace_generation=task.workspace_generation,
        queue_entered_at=task.queue_entered_at,
        cancellation_requested_at=task.cancellation_requested_at,
        importance=task.importance,
        presentation_policy=task.presentation_policy,
        terminal_summary=task.terminal_summary,
        waiting_until=task.waiting_until,
        resume_at=task.resume_at,
        wait_reason=task.wait_reason,
        scheduled_delivery=task.scheduled_delivery_json,
        scheduled_delivered_at=task.scheduled_delivered_at,
        queue_position=queue_position,
        created_at=task.created_at,
        updated_at=task.updated_at,
        finished_at=task.finished_at,
    )
