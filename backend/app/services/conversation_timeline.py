from __future__ import annotations

from typing import Any

TIMELINE_ITEM_EVENT_TYPE = "chat.timeline.item.committed"


def humanize_tool_name(tool_name: str) -> str:
    normalized = str(tool_name or "").strip().replace("_", " ")
    return normalized or "tool"


def build_tool_activity_id(*, run_id: str, step: int, tool_name: str, tool_call_id: str | None = None) -> str:
    stable_call_id = str(tool_call_id or "").strip()
    if stable_call_id:
        return f"tool:{run_id}:{stable_call_id}"
    return f"tool:{run_id}:{step}:{tool_name}"


def compact_tool_target_label(value: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    if not normalized:
        return ""
    parts = [part for part in normalized.split("/") if part]
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return parts[0] if parts else normalized


def summarize_tool_result_content(
    tool_name: str,
    arguments: dict[str, Any],
    ok: bool,
    output: str | None = None,
    error: str | None = None,
    *,
    include_error_detail: bool = True,
) -> str:
    if not ok:
        failure = str(error or "").strip()
        if include_error_detail and failure:
            return failure
        path = compact_tool_target_label(str(arguments.get("path") or ""))
        if tool_name == "file_write" and path:
            return f"Failed to update {path}"
        if tool_name == "file_read" and path:
            return f"Failed to read {path}"
        return f"{humanize_tool_name(tool_name).capitalize()} failed"

    path = compact_tool_target_label(str(arguments.get("path") or ""))
    if tool_name == "file_write":
        content = str(arguments.get("content") or "")
        line_count = len(content.splitlines()) if content else 0
        if path and line_count > 0:
            return f"Updated {path} ({line_count} lines)"
        if path:
            return f"Updated {path}"
    if tool_name == "file_read" and path:
        return f"Read {path}"
    if tool_name == "workspace_search":
        query = str(arguments.get("query") or "").strip()
        if query:
            return f"Finished searching for {query}"
    if tool_name == "shell_command":
        return "Finished the command"
    if tool_name == "python_execution":
        return "Finished the Python check"
    if tool_name == "app_initialize":
        title = str(arguments.get("title") or arguments.get("app_slug") or "the app").strip()
        return f"Initialized {title}" if title else "Finished initializing the app"
    if tool_name == "app_verify":
        slug = str(arguments.get("app_slug") or "").strip()
        return f"Verified {slug}" if slug else "Verified the app"
    output_text = str(output or "").strip()
    if output_text:
        return output_text
    return f"Finished {humanize_tool_name(tool_name)}"


def summarize_tool_progress_content(tool_name: str, arguments: dict[str, Any], ok: bool) -> str:
    return summarize_tool_result_content(
        tool_name,
        arguments,
        ok,
        include_error_detail=False,
    )


def build_timeline_item(
    *,
    item_id: str,
    role: str,
    kind: str,
    content: str,
    title: str | None = None,
    label: str | None = None,
    tone: str | None = None,
    step: int | None = None,
    tool_name: str | None = None,
    status: str = "completed",
    badges: list[str] | None = None,
    code: str | None = None,
    sections: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "item_id": item_id,
        "role": role,
        "kind": kind,
        "content": content,
        "status": status,
    }
    if title:
        payload["title"] = title
    if label:
        payload["label"] = label
    if tone:
        payload["tone"] = tone
    if step is not None:
        payload["step"] = step
    if tool_name:
        payload["tool_name"] = tool_name
    if badges:
        payload["badges"] = badges
    if code:
        payload["code"] = code
    if sections:
        payload["sections"] = sections
    if metadata:
        payload["metadata"] = metadata
    return payload


def build_user_message_timeline_item(*, message_id: str, content: str) -> dict[str, Any]:
    return build_timeline_item(
        item_id=f"user:{message_id}",
        role="user",
        kind="message",
        content=content,
    )


def build_assistant_message_timeline_item(
    *,
    message_id: str,
    content: str,
    item_id: str | None = None,
) -> dict[str, Any]:
    return build_timeline_item(
        item_id=item_id or f"assistant:{message_id}",
        role="assistant",
        kind="message",
        content=content,
    )


def build_visible_text_timeline_item(*, run_id: str, step: int, content: str) -> dict[str, Any]:
    return build_timeline_item(
        item_id=f"assistant:{run_id}:{step}:text",
        role="assistant",
        kind="message",
        content=content,
        step=step,
    )


def build_thought_timeline_item(*, run_id: str, step: int, content: str) -> dict[str, Any]:
    return build_timeline_item(
        item_id=f"thought:{run_id}:{step}",
        role="assistant",
        kind="thought",
        label="Thinking",
        title="Thought",
        content=content,
        step=step,
    )


def build_question_timeline_item(*, run_id: str, pending_question: dict[str, Any]) -> dict[str, Any]:
    question_id = str(pending_question.get("id") or run_id).strip()
    question = str(pending_question.get("question") or "The agent is waiting for input.").strip()
    question_kind = str(pending_question.get("kind") or "question").strip() or "question"
    return build_timeline_item(
        item_id=f"question:{run_id}:{question_id}",
        role="assistant",
        kind="question",
        label="Question",
        content=question,
        tone="warning",
        badges=[question_kind],
    )


def build_tool_result_timeline_item(
    *,
    run_id: str,
    step: int,
    tool_name: str,
    arguments: dict[str, Any],
    ok: bool,
    output: str | None = None,
    error: str | None = None,
    result_metadata: dict[str, Any] | None = None,
    tool_call_id: str | None = None,
) -> dict[str, Any]:
    if tool_name == "file_write":
        detail = str(arguments.get("content") or "")
    elif tool_name == "app_initialize":
        app = result_metadata.get("app") if isinstance(result_metadata, dict) else None
        scaffolded_files = app.get("scaffolded_files") if isinstance(app, dict) else None
        detail = "\n".join(str(path) for path in scaffolded_files) if isinstance(scaffolded_files, list) else ""
        detail = f"Created files:\n{detail}" if detail else str(output or error or "")
    else:
        detail = str(output or error or "")
    return build_timeline_item(
        item_id=build_tool_activity_id(
            run_id=run_id,
            step=step,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
        ),
        role="assistant",
        kind="tool_result",
        label="Tool result" if ok else "Tool failure",
        title=humanize_tool_name(tool_name),
        content=summarize_tool_result_content(tool_name, arguments, ok, output, error),
        tone="success" if ok else "error",
        step=step,
        tool_name=tool_name,
        badges=[f"step {step}"],
        code=detail or None,
        metadata={
            "tool": tool_name,
            "ok": ok,
            "arguments": arguments,
            "result_metadata": result_metadata,
        },
    )


def normalize_live_item(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    item_id = str(value.get("item_id") or "").strip()
    role = str(value.get("role") or "").strip()
    kind = str(value.get("kind") or "").strip()
    content = str(value.get("content") or "")
    if not item_id or not role or not kind:
        return None
    normalized = dict(value)
    normalized["item_id"] = item_id
    normalized["role"] = role
    normalized["kind"] = kind
    normalized["content"] = content
    normalized["status"] = str(normalized.get("status") or "streaming").strip() or "streaming"
    return normalized


def set_live_item(metadata: dict[str, Any], item: dict[str, Any] | None) -> bool:
    normalized = normalize_live_item(item)
    current = normalize_live_item(metadata.get("live_item"))
    if current == normalized:
        return False
    metadata["live_item"] = normalized
    return True


def clear_live_item(metadata: dict[str, Any], item_id: str | None = None) -> bool:
    current = normalize_live_item(metadata.get("live_item"))
    if current is None:
        return False
    if item_id and current.get("item_id") != item_id:
        return False
    metadata["live_item"] = None
    return True


def extract_timeline_item_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    item_id = str(payload.get("item_id") or "").strip()
    role = str(payload.get("role") or "").strip()
    kind = str(payload.get("kind") or "").strip()
    if not item_id or not role or not kind:
        return None
    return dict(payload)
