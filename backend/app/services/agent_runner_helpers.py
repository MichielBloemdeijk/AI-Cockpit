from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from app.services.agent_run_support import _clear_live_timeline_item
from app.services.conversation_store import conversation_store
from app.services.conversation_timeline import build_visible_text_timeline_item


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


async def persist_visible_text_event(
    *,
    conversation_id: str,
    run_id: str,
    step_number: int,
    visible_text: str,
    metadata: dict[str, Any],
    checkpoint_state=None,
) -> None:
    if not visible_text:
        return
    event = await conversation_store.append_event(
        conversation_id,
        run_id=run_id,
        actor_kind="assistant",
        event_type="agent.text.output",
        payload_json={"step": step_number, "text": visible_text},
        branch_key=str(metadata.get("branch_key") or "main"),
    )
    if metadata.get("execution_mode") != "background":
        await conversation_store.append_timeline_item(
            conversation_id,
            run_id=run_id,
            item_payload=build_visible_text_timeline_item(run_id=run_id, step=step_number, content=visible_text),
            branch_key=str(metadata.get("branch_key") or "main"),
            parent_event_id=event.id,
        )
    await _clear_live_timeline_item(
        run_id,
        metadata,
        checkpoint_state,
        item_id=f"assistant:{run_id}:{step_number}:text",
    )


async def restore_runtime_metadata_from_events(
    *,
    conversation_id: str,
    run_id: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    events = await conversation_store.list_events_for_run(conversation_id, run_id, limit=5000)
    if not events:
        return metadata

    pending_tool_arguments: dict[tuple[int, str], deque[dict[str, Any]]] = defaultdict(deque)
    restored_tool_entries: list[dict[str, Any]] = []
    latest_completed_action: str | None = None
    latest_completed_step = safe_int(metadata.get("current_step"), 0)
    started_event_recorded = bool(metadata.get("started_event_recorded"))

    for event in events:
        payload = event.payload_json if isinstance(event.payload_json, dict) else {}
        if event.event_type == "agent.run.started":
            started_event_recorded = True
            continue
        if event.event_type == "agent.tool.called":
            step = safe_int(payload.get("step"), 0)
            tool_name = str(payload.get("tool") or "").strip()
            arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
            if step > 0 and tool_name:
                pending_tool_arguments[(step, tool_name)].append(dict(arguments))
            continue
        if event.event_type != "agent.tool.completed":
            continue

        step = safe_int(payload.get("step"), 0)
        tool_name = str(payload.get("tool") or "").strip()
        if step <= 0 or not tool_name:
            continue
        arguments_queue = pending_tool_arguments[(step, tool_name)]
        arguments = arguments_queue.popleft() if arguments_queue else {}
        ok = bool(payload.get("ok"))
        entry = {
            "kind": "tool",
            "tool": tool_name,
            "arguments": arguments,
            "ok": ok,
            "output": payload.get("output") if ok else payload.get("error"),
        }
        artifact_id = payload.get("artifact_id")
        if artifact_id:
            entry["artifact_id"] = artifact_id
        restored_tool_entries.append(entry)
        if step >= latest_completed_step:
            latest_completed_step = step
            latest_completed_action = f"Completed {tool_name}" if ok else f"Tool failed: {tool_name}"

    existing_history = list(metadata.get("history", [])) if isinstance(metadata.get("history"), list) else []
    existing_tool_count = sum(1 for item in existing_history if isinstance(item, dict) and item.get("kind") == "tool")
    if len(restored_tool_entries) > existing_tool_count:
        metadata["history"] = existing_history + restored_tool_entries[existing_tool_count:]

    if latest_completed_step > safe_int(metadata.get("current_step"), 0):
        metadata["current_step"] = latest_completed_step
        if latest_completed_action and not isinstance(metadata.get("pending_question"), dict):
            metadata["current_action"] = latest_completed_action

    if started_event_recorded:
        metadata["started_event_recorded"] = True
    return metadata
