"""Conversation-to-task delegation without compatibility child runs."""
from __future__ import annotations

from app.services.conversation_store import EventRecord, conversation_store
from app.services.conversation_timeline import build_background_task_timeline_item
from app.services.task_service import TaskSnapshot, task_service


def _title(goal: str) -> str:
    normalized = " ".join(str(goal or "Background task").split()).strip()
    return (normalized.split("\n", 1)[0] or "Background task")[:96]


async def delegate_background_task(
    *,
    parent_run_id: str,
    goal: str,
    notify_policy: str = "important",
    priority: str = "normal",
    requested_notification: bool = False,
    idempotency_key: str,
) -> tuple[TaskSnapshot, EventRecord]:
    parent = await conversation_store.get_run(parent_run_id)
    if parent is None:
        raise ValueError("Parent conversation run not found")
    normalized_goal = " ".join(str(goal or "").split()).strip()
    if not normalized_goal:
        raise ValueError("Background task goal cannot be empty")
    normalized_policy = str(notify_policy or "important").strip().lower()
    if normalized_policy not in {"important", "completion", "all", "none"}:
        normalized_policy = "important"
    normalized_priority = "high" if str(priority or "").strip().lower() == "high" else "normal"
    parent_metadata = dict(parent.metadata_json) if isinstance(parent.metadata_json, dict) else {}
    task = await task_service.dispatch(
        goal=normalized_goal,
        origin_surface="standard_chat",
        origin_conversation_id=parent.conversation_id,
        origin_turn_id=str(parent_metadata.get("origin_turn_id") or "").strip() or None,
        importance=normalized_priority,
        presentation_policy=normalized_policy,
        idempotency_key=idempotency_key,
    )
    event = await conversation_store.append_event(
        parent.conversation_id,
        run_id=parent.id,
        actor_kind="task",
        event_type="task.delegated",
        payload_json={
            "task_id": task.task.id,
            "title": _title(normalized_goal),
            "goal": normalized_goal,
            "priority": normalized_priority,
            "presentation_policy": normalized_policy,
            "requested_notification": bool(requested_notification),
            "status": task.task.status,
        },
        branch_key=str(parent_metadata.get("branch_key") or "main"),
    )
    await conversation_store.append_timeline_item(
        parent.conversation_id,
        run_id=parent.id,
        item_payload=build_background_task_timeline_item(
            run_id=task.task.id,
            task_id=task.task.id,
            title=_title(normalized_goal),
            goal=normalized_goal,
            status=task.task.status,
            active_step="Queued",
            notify_policy=normalized_policy,
            requested_notification=bool(requested_notification),
        ),
        branch_key=str(parent_metadata.get("branch_key") or "main"),
        parent_event_id=event.id,
    )
    return task, event


__all__ = ["delegate_background_task"]
