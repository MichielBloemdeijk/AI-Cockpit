from __future__ import annotations

import json
from typing import Any

from app.services.agent_tools import get_agent_tool_definitions

MAX_TOOL_SNIPPET_CHARS = 4_000
NATIVE_AGENT_ASK_USER_TOOL = "task_ask_user"
NATIVE_AGENT_FINALIZE_TOOL = "task_finalize"
NATIVE_AGENT_DELEGATE_TOOL = "delegate_background_task"
NATIVE_AGENT_WAIT_UNTIL_TOOL = "task_wait_until"


def _tool_spec_block() -> str:
    lines = []
    for tool in get_agent_tool_definitions():
        arg_text = ", ".join(f"{name}: {kind}" for name, kind in tool.arguments.items())
        lines.append(f"- {tool.name}: {tool.description} Arguments: {arg_text or 'none'}")
    return "\n".join(lines)


def _native_control_tool_definitions(
    *, include_delegate: bool = False, include_wait: bool = False
) -> list[dict[str, Any]]:
    definitions = [
        {
            "type": "function",
            "function": {
                "name": NATIVE_AGENT_WAIT_UNTIL_TOOL,
                "description": (
                    "Pause this background task until an exact future instant. Use only when the task itself "
                    "must wait before continuing; ordinary conversation reminders use Jarvis reminder tools."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "resume_at": {
                            "type": "string",
                            "description": "ISO-8601 future date-time. Times without an offset use Europe/Amsterdam.",
                        },
                        "reason": {"type": "string", "description": "Concise reason for waiting."},
                    },
                    "required": ["resume_at", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": NATIVE_AGENT_ASK_USER_TOOL,
                "description": "Pause the run and ask the user a single clarifying question when required information is missing.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The exact question the user should answer before the task can continue.",
                        }
                    },
                    "required": ["question"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": NATIVE_AGENT_DELEGATE_TOOL,
                "description": (
                    "Delegate a multi-step, mutating, executable, waiting, or explicitly background goal. "
                    "The current conversation run ends immediately after the first-class task is queued. Put a short acknowledgement "
                    "for the user in assistant text and do not duplicate the full task details."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "goal": {"type": "string", "description": "The complete goal for the background task."},
                        "notify_policy": {
                            "type": "string",
                            "enum": ["important", "completion", "all", "none"],
                            "description": "When the user should receive an away notification.",
                        },
                        "priority": {
                            "type": "string",
                            "enum": ["normal", "high"],
                            "description": "Whether completion is important enough for default alerts.",
                        },
                    },
                    "required": ["goal", "notify_policy", "priority"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": NATIVE_AGENT_FINALIZE_TOOL,
                "description": (
                    "Finish the current run after the task is complete. Put the exact final user-facing response in "
                    "final_text and a compact internal completion summary in summary. "
                    "Attached generated apps are production-build verified before completion is accepted, so describe "
                    "successful verification in the past tense."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "A short summary of what was completed.",
                        },
                        "final_text": {
                            "type": "string",
                            "description": "The complete response to show or speak to the user, including the actual answer or result.",
                        },
                    },
                    "required": ["summary", "final_text"],
                    "additionalProperties": False,
                },
            },
        },
    ]
    if not include_delegate:
        definitions = [
            item for item in definitions
            if item.get("function", {}).get("name") != NATIVE_AGENT_DELEGATE_TOOL
        ]
    if not include_wait:
        definitions = [
            item for item in definitions
            if item.get("function", {}).get("name") != NATIVE_AGENT_WAIT_UNTIL_TOOL
        ]
    return definitions


def _extract_string_field(raw_arguments: str, field_name: str) -> str | None:
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
            return "".join(value_chars)
        value_chars.append(char)
        index += 1
    return "".join(value_chars)


def _extract_json_string_at(raw_arguments: str, start_index: int) -> tuple[str | None, int]:
    if start_index >= len(raw_arguments) or raw_arguments[start_index] != '"':
        return None, start_index
    index = start_index + 1
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
            return "".join(value_chars), index + 1
        value_chars.append(char)
        index += 1
    return "".join(value_chars), index


def _repair_native_control_tool_arguments(raw_arguments: str, tool_name: str) -> dict[str, Any] | None:
    if tool_name == NATIVE_AGENT_ASK_USER_TOOL:
        question = _extract_string_field(raw_arguments, "question")
        if question:
            return {"question": question}
        return None
    if tool_name == NATIVE_AGENT_FINALIZE_TOOL:
        summary = _extract_string_field(raw_arguments, "summary") or _extract_string_field(raw_arguments, "result")
        if summary:
            final_text = _extract_string_field(raw_arguments, "final_text")
            return {"summary": summary, "final_text": final_text or summary}
        return None
    if tool_name == NATIVE_AGENT_DELEGATE_TOOL:
        goal = _extract_string_field(raw_arguments, "goal")
        if goal:
            policy = _extract_string_field(raw_arguments, "notify_policy") or "important"
            priority = _extract_string_field(raw_arguments, "priority") or "normal"
            return {"goal": goal, "notify_policy": policy, "priority": priority}
        return None
    return None


def _parse_native_tool_arguments(raw_arguments: str, *, tool_name: str | None = None) -> dict[str, Any]:
    normalized = str(raw_arguments or "").strip()
    if not normalized:
        return {}
    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError:
        repaired = _repair_native_control_tool_arguments(normalized, str(tool_name or "").strip())
        if repaired is None:
            raise
        parsed = repaired
    if not isinstance(parsed, dict):
        raise ValueError("Native tool arguments must decode to a JSON object")
    return parsed


def _build_tool_action_payload(
    *,
    thought: str,
    tool_name: str,
    arguments: dict[str, Any],
    tool_call_id: str | None = None,
) -> dict[str, Any]:
    normalized_tool = str(tool_name or "").strip()
    if not normalized_tool:
        raise ValueError("Tool decisions require a tool name")
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be a JSON object")
    emitted_thought = str(thought or "").strip()
    normalized_thought = emitted_thought or f"Use tool {normalized_tool}"
    if not normalized_thought.strip():
        raise ValueError("Tool decisions require thought text")
    normalized_arguments = dict(arguments)
    if normalized_tool == "app_initialize":
        title = str(normalized_arguments.get("title") or "").strip()
        app_slug = str(normalized_arguments.get("app_slug") or "").strip()
        if not title and not app_slug:
            raise ValueError("app_initialize requires title or app_slug")
    payload = {
        "kind": "tool",
        "thought": emitted_thought,
        "tool": normalized_tool,
        "arguments": normalized_arguments,
    }
    if tool_call_id:
        payload["tool_call_id"] = tool_call_id
    return payload


def _build_question_action_payload(*, thought: str, question: str) -> dict[str, Any]:
    normalized_question = str(question or "").strip()
    if not normalized_question:
        raise ValueError("Native ask-user tool requires a question")
    emitted_thought = str(thought or "").strip()
    normalized_thought = emitted_thought or "Need clarification from the user"
    if not normalized_thought.strip():
        raise ValueError("Question decisions require thought text")
    return {
        "kind": "ask_user",
        "thought": emitted_thought,
        "question": normalized_question,
    }


def _build_final_action_payload(*, thought: str, summary: str, final_text: str) -> dict[str, Any]:
    normalized_summary = str(summary or "").strip()
    if not normalized_summary:
        raise ValueError("Native finalize tool requires a summary")
    normalized_final_text = str(final_text or "").strip() or normalized_summary
    emitted_thought = str(thought or "").strip()
    normalized_thought = emitted_thought or "Task complete"
    if not normalized_thought.strip():
        raise ValueError("Final decisions require thought text")
    return {
        "kind": "final",
        "thought": emitted_thought,
        "summary": normalized_summary,
        "final_text": normalized_final_text,
    }


def _build_delegate_action_payload(
    *,
    thought: str,
    goal: str,
    notify_policy: str,
    priority: str,
    acknowledgement: str,
) -> dict[str, Any]:
    normalized_goal = " ".join(str(goal or "").split()).strip()
    if not normalized_goal:
        raise ValueError("Background delegation requires a goal")
    policy = str(notify_policy or "important").strip().lower()
    if policy not in {"important", "completion", "all", "none"}:
        policy = "important"
    task_priority = "high" if str(priority or "").strip().lower() == "high" else "normal"
    text = " ".join(str(acknowledgement or "").split()).strip()
    if not text:
        text = "I’m on it. I’ll let you know when there’s a useful update."
    return {
        "kind": "delegate",
        "thought": str(thought or "").strip(),
        "goal": normalized_goal,
        "notify_policy": policy,
        "priority": task_priority,
        "acknowledgement": text,
    }


def _history_block(history: list[dict[str, Any]]) -> str:
    if not history:
        return "No prior steps yet."

    rendered: list[str] = []
    for index, item in enumerate(history[-12:], start=max(1, len(history) - 11)):
        kind = item.get("kind", "step")
        if kind == "tool":
            rendered.append(
                f"{index}. tool {item.get('tool')} args={json.dumps(item.get('arguments', {}), ensure_ascii=True)}"
                f" ok={item.get('ok', True)} output={item.get('output', '')}"
            )
        elif kind == "question_answer":
            rendered.append(f"{index}. user answered question '{item.get('question', '')}' with '{item.get('answer', '')}'")
        else:
            rendered.append(f"{index}. {json.dumps(item, ensure_ascii=True)}")
    return "\n".join(rendered)


def _conversation_context_block(messages: list[Any]) -> str:
    if not messages:
        return "No prior conversation messages."
    tail = messages[-6:]
    return "\n".join(f"- {message.role}: {message.content}" for message in tail)


def _app_context_block(metadata: dict[str, Any]) -> str:
    app_context = metadata.get("app_context") if isinstance(metadata.get("app_context"), dict) else {}
    app = app_context.get("app") if isinstance(app_context, dict) else None
    if not isinstance(app, dict):
        payload = metadata.get("payload") if isinstance(metadata.get("payload"), dict) else {}
        app = payload.get("app") if isinstance(payload, dict) else None
    if not isinstance(app, dict):
        return "No app is attached yet. Use the app_initialize tool before editing a generated app route."

    scaffolded_files = app.get("scaffolded_files") if isinstance(app.get("scaffolded_files"), list) else []
    sections = [
        f"Attached app: {app.get('title', 'Untitled')} ({app.get('slug', 'unknown')})",
        f"Route root: {app.get('route_path', '')}",
        f"App directory: {app.get('frontend_root', '')}",
        f"Main entry file: {app.get('frontend_entry_path', '')}",
        f"App layout file: {app.get('frontend_layout_path', '')}",
        f"Manifest file: {app.get('manifest_path', '')}",
        f"Asset root: {app.get('asset_root', '')}",
        f"Allowed write roots: {json.dumps(app.get('allowed_write_roots', []), ensure_ascii=True)}",
        "Styling rule: prefer the app's existing styles.css. If you add a CSS Module, every selector must include a local class or id; bare element selectors like h1 and keyframe percentages such as 0% or 100% inside module files are invalid and can break the frontend build. Keep animations and @keyframes in styles.css.",
        "Completion rule: the runtime will run an isolated Next.js production build before accepting completion. Do not run npm build yourself. If automatic verification fails, fix the returned error and finalize again. Because an app final response is only committed after verification passes, describe verification as completed rather than pending.",
        "To add more pages for this app, create nested page.tsx files under the app directory.",
    ]
    if scaffolded_files:
        sections.append(f"Scaffolded files: {len(scaffolded_files)} created during initialization.")
    return "\n".join(sections)


def _result_snippet(tool_name: str, output: str, metadata: dict[str, Any] | None = None) -> str:
    if tool_name == "app_list" and isinstance(metadata, dict):
        apps = metadata.get("apps") if isinstance(metadata.get("apps"), list) else []
        count = metadata.get("count")
        preview: list[str] = []
        for app in apps[:5]:
            if not isinstance(app, dict):
                continue
            title = str(app.get("title") or app.get("slug") or "unknown").strip()
            slug = str(app.get("slug") or "unknown").strip()
            status = str(app.get("status") or "unknown").strip()
            preview.append(f"{title} ({slug}) [{status}]")
        rendered_count = int(count) if isinstance(count, int) else len(apps)
        summary = f"Registered apps: {rendered_count} total"
        if preview:
            summary += "; preview: " + "; ".join(preview)
            if rendered_count > len(preview):
                summary += "; ..."
        return summary

    compact = output.strip()
    if len(compact) <= MAX_TOOL_SNIPPET_CHARS:
        return compact
    return compact[:MAX_TOOL_SNIPPET_CHARS] + "\n...[truncated]"


def _compact_path_label(value: str, *, max_segments: int = 2) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    if not normalized:
        return ""
    segments = [segment for segment in normalized.split("/") if segment]
    if len(segments) <= max_segments:
        return "/".join(segments)
    return "/".join(segments[-max_segments:])


def _is_low_signal_tool_intent_summary(text: str) -> bool:
    normalized = str(text or "").strip().lower().rstrip(".!")
    if not normalized:
        return True
    if normalized.startswith("use tool "):
        return True
    return normalized in {"working on it", "continuing", "in progress"}


def summarize_tool_start_content(tool_name: str, arguments: dict[str, Any]) -> str:
    tool_name = str(tool_name or "tool").strip()

    if tool_name == "file_read":
        path = _compact_path_label(str(arguments.get("path") or ""))
        return f"Reading {path}" if path else "Reading a file"
    if tool_name == "workspace_search":
        query = str(arguments.get("query") or "").strip()
        return f"Searching for {query}" if query else "Searching the workspace"
    if tool_name == "file_write":
        path = _compact_path_label(str(arguments.get("path") or ""))
        content = str(arguments.get("content") or "")
        line_count = len(content.splitlines()) if content else 0
        if path and line_count > 0:
            return f"Writing {path} ({line_count} lines)"
        return f"Writing {path}" if path else "Writing a file"
    if tool_name == "shell_command":
        command = str(arguments.get("command") or "").strip()
        return f"Running {command}" if command else "Running a command"
    if tool_name == "python_execution":
        return "Running a Python check"
    if tool_name == "app_initialize":
        title = str(arguments.get("title") or arguments.get("app_slug") or "the app").strip()
        return f"Initializing {title}" if title else "Initializing the app"
    if tool_name == "app_verify":
        slug = str(arguments.get("app_slug") or "").strip()
        return f"Verifying {slug}" if slug else "Verifying the app"
    return f"Running {tool_name}"


def _native_retry_feedback(
    error: Exception,
    *,
    tool_name: str | None = None,
    raw_arguments: str | None = None,
    tool_call_count: int | None = None,
    finish_reason: str | None = None,
) -> str:
    error_text = str(error).strip() or error.__class__.__name__
    arguments_preview = str(raw_arguments or "").strip()
    truncated_by_length = str(finish_reason or "").strip().lower() == "length"
    if tool_name == "app_initialize" and "app_initialize requires title or app_slug" in error_text:
        base = (
            "Your previous tool call called app_initialize without the required arguments. "
            "Retry with a corrected app_initialize call. If you are creating a new app, include title. "
            "If you are attaching an existing app, include app_slug."
        )
        if arguments_preview:
            return f"{base} Previous invalid arguments: {arguments_preview}"
        return base
    if tool_call_count is not None:
        if tool_call_count == 0:
            return (
                "Your previous response was invalid because it produced zero tool calls. "
                "An actionable native tool call is required now. Return a control call or one or more external tool calls."
            )
        return (
            f"Your previous response was invalid because it produced {tool_call_count} tool calls. "
            "Retry by returning either exactly one control tool call or a batch of external tool calls only. "
            "Do not combine task_ask_user or task_finalize with any other tool call."
        )
    if truncated_by_length and tool_name:
        if arguments_preview:
            return (
                f"Your previous tool call for {tool_name} was truncated because the response hit the token limit. "
                f"Retry with a complete {tool_name} call containing valid JSON arguments. Previous truncated arguments: {arguments_preview}"
            )
        return (
            f"Your previous tool call for {tool_name} was truncated because the response hit the token limit. "
            f"Retry with a complete {tool_name} call containing valid JSON arguments."
        )
    if tool_name:
        if arguments_preview:
            return (
                f"Your previous tool call for {tool_name} was invalid: {error_text}. "
                f"Retry with a corrected {tool_name} call. Previous invalid arguments: {arguments_preview}"
            )
        return f"Your previous tool call for {tool_name} was invalid: {error_text}. Retry with a corrected {tool_name} call."
    return f"Your previous response was invalid: {error_text}. Retry with valid control or external tool calls."
