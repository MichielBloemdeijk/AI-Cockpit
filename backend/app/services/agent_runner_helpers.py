from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from app.services.agent_prompt_builder import (
    NATIVE_AGENT_ASK_USER_TOOL,
    NATIVE_AGENT_PLAN_TOOL,
)
from app.services.agent_run_support import _clear_live_timeline_item
from app.services.conversation_store import conversation_store
from app.services.conversation_timeline import build_visible_text_timeline_item


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def extract_partial_json_string_field(raw_arguments: str, field_name: str) -> str | None:
    marker = f'"{field_name}"'
    start = raw_arguments.find(marker)
    if start < 0:
        return None
    colon = raw_arguments.find(":", start + len(marker))
    if colon < 0:
        return None
    index = colon + 1
    while index < len(raw_arguments) and raw_arguments[index].isspace():
        index += 1
    if index >= len(raw_arguments) or raw_arguments[index] != '"':
        return None
    index += 1
    value_chars: list[str] = []
    escaped = False
    escape_map = {
        '"': '"',
        "\\": "\\",
        "/": "/",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
    }
    while index < len(raw_arguments):
        char = raw_arguments[index]
        if escaped:
            value_chars.append(escape_map.get(char, char))
            escaped = False
            index += 1
            continue
        if char == "\\":
            escaped = True
            index += 1
            continue
        if char == '"':
            break
        value_chars.append(char)
        index += 1
    return "".join(value_chars)


def extract_partial_json_string_array_field(raw_arguments: str, field_name: str, *, limit: int = 12) -> list[str]:
    marker = f'"{field_name}"'
    start = raw_arguments.find(marker)
    if start < 0:
        return []
    colon = raw_arguments.find(":", start + len(marker))
    if colon < 0:
        return []
    index = colon + 1
    while index < len(raw_arguments) and raw_arguments[index].isspace():
        index += 1
    if index >= len(raw_arguments) or raw_arguments[index] != "[":
        return []

    items: list[str] = []
    index += 1
    while index < len(raw_arguments) and len(items) < limit:
        char = raw_arguments[index]
        if char == "]":
            break
        if char != '"':
            index += 1
            continue

        index += 1
        value_chars: list[str] = []
        escaped = False
        escape_map = {
            '"': '"',
            "\\": "\\",
            "/": "/",
            "b": "\b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
        }
        while index < len(raw_arguments):
            char = raw_arguments[index]
            if escaped:
                value_chars.append(escape_map.get(char, char))
                escaped = False
                index += 1
                continue
            if char == "\\":
                escaped = True
                index += 1
                continue
            if char == '"':
                index += 1
                break
            value_chars.append(char)
            index += 1
        item = "".join(value_chars).strip()
        if item:
            items.append(item)
    return items


def truncate_live_tool_preview(preview: str, limit: int = 2000) -> str:
    if len(preview) <= limit:
        return preview
    return f"...\n{preview[-limit:]}"


def count_buffered_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + 1


def render_partial_plan_preview(raw_arguments: str) -> str:
    summary = extract_partial_json_string_field(raw_arguments, "summary")
    steps = extract_partial_json_string_array_field(raw_arguments, "steps")
    open_points = extract_partial_json_string_array_field(raw_arguments, "open_questions")
    assumptions = extract_partial_json_string_array_field(raw_arguments, "assumptions")

    sections: list[str] = ["Drafting execution plan"]
    if summary:
        sections.append(summary)
    if steps:
        sections.append("Steps\n" + "\n".join(f"- {step}" for step in steps))
    if open_points:
        sections.append("Open points\n" + "\n".join(f"- {item}" for item in open_points))
    if assumptions:
        sections.append("Assumptions\n" + "\n".join(f"- {item}" for item in assumptions))
    return truncate_live_tool_preview("\n\n".join(sections))


def render_live_tool_call_preview(tool_name: str, raw_arguments: str) -> str:
    normalized_tool = tool_name.strip() or "tool"
    path = extract_partial_json_string_field(raw_arguments, "path")

    if normalized_tool == NATIVE_AGENT_PLAN_TOOL:
        return render_partial_plan_preview(raw_arguments)

    if normalized_tool == NATIVE_AGENT_ASK_USER_TOOL:
        question = extract_partial_json_string_field(raw_arguments, "question")
        if question:
            return truncate_live_tool_preview(f"Preparing a question for the user\n\n{question}")
        return "Preparing a question for the user"

    if normalized_tool == "file_write":
        content_preview = extract_partial_json_string_field(raw_arguments, "content")
        if content_preview:
            line_count = count_buffered_lines(content_preview)
            byte_count = len(content_preview.encode("utf-8"))
            if path:
                return f"Preparing to write {path} ({line_count} lines, {byte_count} bytes buffered)"
            return f"Preparing file_write ({line_count} lines, {byte_count} bytes buffered)"
        if path:
            return f"Preparing to write {path}"

    if path:
        return f"Preparing {normalized_tool} for {path}"

    compact_arguments = raw_arguments.strip()
    if compact_arguments:
        return truncate_live_tool_preview(f"Preparing {normalized_tool}\n\n{compact_arguments}")
    return f"Preparing {normalized_tool}"


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
