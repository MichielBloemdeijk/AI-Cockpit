"""Durable background-task lifecycle used by Jarvis voice runs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from app.services.conversation_store import EventRecord, RunRecord, conversation_store
from app.services.conversation_timeline import build_background_task_timeline_item
from app.services.voice import sanitize_voice_response
from app.services.task_service import task_service


async def _schedule_notification(event_id: str) -> None:
    """Durably create delivery rows before returning to the run lifecycle."""
    from app.services.notifications import notify_event

    await notify_event(event_id)


IMPORTANT_POLICIES = {"important", "completion", "all"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted"}


@dataclass(slots=True)
class BackgroundTaskSnapshot:
    run_id: str
    conversation_id: str
    parent_run_id: str | None
    title: str
    goal: str
    status: str
    priority: str
    notify_policy: str
    requested_notification: bool
    summary: str | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None
    task_id: str | None = None


def _metadata(run: RunRecord) -> dict[str, Any]:
    return dict(run.metadata_json) if isinstance(run.metadata_json, dict) else {}


def _title(goal: str) -> str:
    first = " ".join(str(goal or "Background task").split()).strip()
    return (first.split("\n", 1)[0] or "Background task")[:96]


def snapshot(run: RunRecord) -> BackgroundTaskSnapshot:
    metadata = _metadata(run)
    return BackgroundTaskSnapshot(
        run_id=run.id,
        conversation_id=run.conversation_id,
        parent_run_id=run.parent_run_id,
        title=str(metadata.get("title") or _title(str(metadata.get("goal") or "Background task"))),
        goal=str(metadata.get("goal") or ""),
        status=run.status,
        priority=str(metadata.get("task_priority") or "normal"),
        notify_policy=str(metadata.get("notify_policy") or "important"),
        requested_notification=bool(metadata.get("requested_notification")),
        summary=str(metadata.get("final_summary") or metadata.get("summary") or "").strip() or None,
        error=str(run.error or metadata.get("error") or "").strip() or None,
        started_at=run.started_at,
        finished_at=run.finished_at,
        task_id=str(metadata.get("global_task_id") or "").strip() or None,
    )


async def delegate_background_task(
    *,
    parent_run_id: str,
    goal: str,
    notify_policy: str = "important",
    priority: str = "normal",
    requested_notification: bool = False,
    idempotency_key: str | None = None,
) -> tuple[RunRecord, EventRecord]:
    parent = await conversation_store.get_run(parent_run_id)
    if parent is None:
        raise ValueError("Parent voice run not found")
    parent_metadata = _metadata(parent)
    normalized_goal = " ".join(str(goal or "").split()).strip()
    if not normalized_goal:
        raise ValueError("Background task goal cannot be empty")
    normalized_policy = str(notify_policy or "important").strip().lower()
    if normalized_policy not in {"important", "completion", "all", "none"}:
        normalized_policy = "important"
    normalized_priority = "high" if str(priority or "").strip().lower() == "high" else "normal"
    task = await task_service.dispatch(
        goal=normalized_goal,
        origin_surface=(
            "jarvis" if parent_metadata.get("execution_mode") in {"voice_interactive", "jarvis_shell"}
            else "standard_chat"
        ),
        origin_conversation_id=parent.conversation_id,
        origin_turn_id=str(parent_metadata.get("origin_turn_id") or "").strip() or None,
        importance=normalized_priority,
        presentation_policy=normalized_policy,
        idempotency_key=idempotency_key or f"legacy-background:{parent.id}:{uuid4()}",
    )
    child_metadata = dict(parent_metadata)
    child_metadata.update(
        {
            "title": _title(normalized_goal),
            "goal": normalized_goal,
            "run_kind": "background_task",
            "execution_mode": "background",
            "llm_priority": "background",
            "voice_response": False,
            "task_priority": normalized_priority,
            "notify_policy": normalized_policy,
            "requested_notification": bool(requested_notification or normalized_policy in {"completion", "all"}),
            "task_status": "queued",
            "active_step": "Queued",
            "active_step_index": 0,
            "history": [],
            "pending_question": None,
            "final_summary": None,
            "result": None,
            "error": None,
            "parent_run_id": parent.id,
            "task_updates": [],
            "global_task_id": task.task.id,
        }
    )
    child = await conversation_store.start_run(
        parent.conversation_id,
        kind="task",
        status="queued",
        parent_run_id=parent.id,
        metadata_json=child_metadata,
    )
    await task_service.attach_runtime_reference(task.task.id, child.id)
    event = await conversation_store.append_event(
        parent.conversation_id,
        run_id=child.id,
        actor_kind="task",
        event_type="task.queued",
        payload_json={
            "task_id": task.task.id,
            "task_run_id": child.id,
            "parent_run_id": parent.id,
            "title": child_metadata["title"],
            "goal": normalized_goal,
            "priority": normalized_priority,
            "notify_policy": normalized_policy,
            "requested_notification": child_metadata["requested_notification"],
            "status": "queued",
        },
        branch_key=str(parent_metadata.get("branch_key") or "main"),
    )
    await conversation_store.append_timeline_item(
        parent.conversation_id,
        run_id=child.id,
        item_payload=build_background_task_timeline_item(
            run_id=child.id,
            task_id=task.task.id,
            title=str(child_metadata["title"]),
            goal=normalized_goal,
            status="queued",
            active_step="Queued",
            notify_policy=normalized_policy,
            requested_notification=bool(child_metadata["requested_notification"]),
        ),
        branch_key=str(parent_metadata.get("branch_key") or "main"),
        parent_event_id=event.id,
    )
    return child, event


async def request_completion_notification(conversation_id: str) -> BackgroundTaskSnapshot | None:
    """Apply a notification-only follow-up to the most recent active task."""
    runs = await conversation_store.list_runs(conversation_id)
    for run in runs:
        metadata = _metadata(run)
        if metadata.get("execution_mode") != "background" or run.status in TERMINAL_STATUSES:
            continue
        metadata["notify_policy"] = "completion"
        metadata["requested_notification"] = True
        await conversation_store.update_run_metadata(run.id, metadata)
        refreshed = await conversation_store.get_run(run.id)
        return snapshot(refreshed) if refreshed is not None else snapshot(run)
    return None


async def publish_update(run_id: str, summary: str, importance: str = "normal") -> EventRecord:
    run = await conversation_store.get_run(run_id)
    if run is None:
        raise ValueError("Background task not found")
    metadata = _metadata(run)
    if metadata.get("execution_mode") != "background":
        raise ValueError("Task updates can only be published by background tasks")
    text = " ".join(str(summary or "").split()).strip()
    if not text:
        raise ValueError("Task update cannot be empty")
    normalized_importance = str(importance or "normal").strip().lower()
    if normalized_importance not in {"low", "normal", "high", "urgent"}:
        normalized_importance = "normal"
    updates = list(metadata.get("task_updates") or [])
    updates.append(text)
    global_task_id = str(metadata.get("global_task_id") or "").strip()
    if global_task_id:
        await task_service.publish_progress(
            global_task_id,
            summary=text,
            idempotency_key=f"legacy-progress:{run.id}:{len(updates)}",
        )
    metadata["task_updates"] = updates[-20:]
    metadata["active_step"] = text
    await conversation_store.update_run_metadata(run.id, metadata)
    event, _message = await conversation_store.append_event_with_assistant_message(
        run.conversation_id,
        run_id=run.id,
        event_type="task.progress",
        content=text,
        importance=normalized_importance,
        branch_key=str(metadata.get("branch_key") or "main"),
        payload_json={
            "task_run_id": run.id,
            "title": metadata.get("title") or _title(str(metadata.get("goal") or "")),
            "summary": text,
            "speech_text": sanitize_voice_response(text),
            "importance": normalized_importance,
            "notify_policy": metadata.get("notify_policy", "important"),
            "requested_notification": bool(metadata.get("requested_notification")),
            "status": run.status,
        },
        timeline_item_payload=build_background_task_timeline_item(
            run_id=run.id,
            task_id=global_task_id or None,
            title=str(metadata.get("title") or _title(str(metadata.get("goal") or ""))),
            goal=str(metadata.get("goal") or ""),
            status=run.status,
            summary=text,
            active_step=text,
            updates=metadata["task_updates"],
            notify_policy=str(metadata.get("notify_policy") or "important"),
            requested_notification=bool(metadata.get("requested_notification")),
        ),
    )
    await _schedule_notification(event.id)
    return event


async def publish_terminal_update(
    run: RunRecord,
    *,
    status: str,
    summary: str,
    importance: str,
) -> EventRecord:
    metadata = _metadata(run)
    event_type = {
        "completed": "task.completed",
        "failed": "task.failed",
        "cancelled": "task.cancelled",
        "interrupted": "task.interrupted",
    }.get(status, "task.progress")
    text = " ".join(str(summary or "Background task update").split()).strip()
    global_task_id = str(metadata.get("global_task_id") or "").strip()
    if global_task_id and status in {"completed", "failed", "interrupted"}:
        await task_service.complete(
            global_task_id,
            status=status,
            summary=text,
            idempotency_key=f"legacy-terminal:{run.id}:{status}",
        )
    event, _message = await conversation_store.append_event_with_assistant_message(
        run.conversation_id,
        run_id=run.id,
        event_type=event_type,
        content=text,
        importance=importance,
        branch_key=str(metadata.get("branch_key") or "main"),
        payload_json={
            "task_run_id": run.id,
            "title": metadata.get("title") or _title(str(metadata.get("goal") or "")),
            "summary": text,
            "speech_text": sanitize_voice_response(text),
            "importance": importance,
            "notify_policy": metadata.get("notify_policy", "important"),
            "requested_notification": bool(metadata.get("requested_notification")),
            "status": status,
        },
        timeline_item_payload=build_background_task_timeline_item(
            run_id=run.id,
            task_id=global_task_id or None,
            title=str(metadata.get("title") or _title(str(metadata.get("goal") or ""))),
            goal=str(metadata.get("goal") or ""),
            status=status,
            summary=text,
            active_step=str(metadata.get("active_step") or ""),
            updates=[*list(metadata.get("task_updates") or []), text],
            notify_policy=str(metadata.get("notify_policy") or "important"),
            requested_notification=bool(metadata.get("requested_notification")),
        ),
    )
    await _schedule_notification(event.id)
    return event


async def list_tasks(conversation_id: str, *, include_finished: bool = False) -> list[BackgroundTaskSnapshot]:
    global_snapshots: list[BackgroundTaskSnapshot] = []
    for task_snapshot in await task_service.list_tasks():
        task = task_snapshot.task
        if task.origin_conversation_id != conversation_id:
            continue
        if not include_finished and task.status in TERMINAL_STATUSES:
            continue
        detail = await task_service.get_task(task.id)
        if detail is None:
            continue
        current_attempt = next(
            (attempt for attempt in detail.attempts if attempt.id == task.current_attempt_id),
            detail.attempts[-1] if detail.attempts else None,
        )
        runtime_reference = str(current_attempt.agent_runtime_reference or "").strip() if current_attempt else ""
        compatibility_run = await conversation_store.get_run(runtime_reference) if runtime_reference else None
        global_snapshots.append(BackgroundTaskSnapshot(
            run_id=runtime_reference or task.id,
            conversation_id=conversation_id,
            parent_run_id=compatibility_run.parent_run_id if compatibility_run else None,
            title=_title(task.goal),
            goal=task.goal,
            status=task.status,
            priority=task.importance,
            notify_policy=task.presentation_policy,
            requested_notification=task.presentation_policy in {"completion", "all"},
            summary=task.terminal_summary,
            error=task.terminal_summary if task.status in {"failed", "interrupted"} else None,
            started_at=task.created_at,
            finished_at=task.finished_at,
            task_id=task.id,
        ))
    runs = await conversation_store.list_runs(conversation_id)
    legacy_snapshots = [
        snapshot(run)
        for run in runs
        if isinstance(run.metadata_json, dict)
        and run.metadata_json.get("execution_mode") == "background"
        and not run.metadata_json.get("global_task_id")
        and (include_finished or run.status not in TERMINAL_STATUSES)
    ]
    return [*global_snapshots, *legacy_snapshots]


async def get_task(conversation_id: str, run_id: str) -> BackgroundTaskSnapshot | None:
    for task in await list_tasks(conversation_id, include_finished=True):
        if task.run_id == run_id or task.task_id == run_id:
            return task
    run = await conversation_store.get_run(run_id)
    if run is None or run.conversation_id != conversation_id:
        return None
    if not isinstance(run.metadata_json, dict) or run.metadata_json.get("execution_mode") != "background":
        return None
    return snapshot(run)


async def cancel_task(conversation_id: str, run_id: str) -> BackgroundTaskSnapshot | None:
    task = await get_task(conversation_id, run_id)
    if task is None:
        return None
    if task.status in TERMINAL_STATUSES:
        return task
    if task.task_id:
        await task_service.cancel(
            task.task_id,
            idempotency_key=f"voice-cancel:{task.task_id}:{uuid4()}",
        )
        return await get_task(conversation_id, task.task_id)
    from app.services.run_supervisor import run_supervisor

    if run_supervisor.get(run_id) is not None:
        await run_supervisor.cancel(run_id)
    else:
        from app.services.run_lifecycle import run_lifecycle

        await run_lifecycle.cancel(run_id)
    return await get_task(conversation_id, run_id)


background_task_service = None
