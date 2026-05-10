from __future__ import annotations

import json
from typing import Any

from app.services.agent_tools import get_agent_tool_definitions

MAX_TOOL_SNIPPET_CHARS = 4_000
NATIVE_AGENT_ASK_USER_TOOL = "task_ask_user"
NATIVE_AGENT_FINALIZE_TOOL = "task_finalize"
NATIVE_AGENT_PLAN_TOOL = "task_plan"


def _tool_spec_block() -> str:
    lines = []
    for tool in get_agent_tool_definitions():
        arg_text = ", ".join(f"{name}: {kind}" for name, kind in tool.arguments.items())
        lines.append(f"- {tool.name}: {tool.description} Arguments: {arg_text or 'none'}")
    return "\n".join(lines)


def _native_control_tool_definitions() -> list[dict[str, Any]]:
    return [
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
                "name": NATIVE_AGENT_FINALIZE_TOOL,
                "description": "Finish the current run after the task is complete and provide the final user-facing result.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "A short summary of what was completed.",
                        },
                        "result": {
                            "type": "string",
                            "description": "The final assistant message that should be shown to the user.",
                        },
                    },
                    "required": ["summary", "result"],
                    "additionalProperties": False,
                },
            },
        },
    ]


def _native_plan_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": NATIVE_AGENT_PLAN_TOOL,
            "description": "Draft the execution plan for the current task before tool execution begins.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Short summary of the execution plan.",
                    },
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Ordered concrete steps to complete the task.",
                    },
                    "open_questions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Questions that remain unresolved before execution.",
                    },
                    "assumptions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Explicit assumptions the plan relies on.",
                    },
                },
                "required": ["summary", "steps", "open_questions", "assumptions"],
                "additionalProperties": False,
            },
        },
    }


def _parse_native_tool_arguments(raw_arguments: str) -> dict[str, Any]:
    normalized = str(raw_arguments or "").strip()
    if not normalized:
        return {}
    parsed = json.loads(normalized)
    if not isinstance(parsed, dict):
        raise ValueError("Native tool arguments must decode to a JSON object")
    return parsed


def _normalize_plan_items(items: Any, *, limit: int) -> list[str]:
    if not isinstance(items, list):
        return []
    normalized: list[str] = []
    for item in items:
        text = ""
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            for key in ("description", "summary", "title", "question", "text", "label", "name"):
                candidate = item.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    text = candidate.strip()
                    break
        elif item is not None:
            text = str(item).strip()
        if text:
            normalized.append(text)
        if len(normalized) >= limit:
            break
    return normalized


def _validate_execution_plan_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Execution plan payload must be a JSON object")

    normalized = dict(payload)
    summary = normalized.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        for fallback_key in ("goal", "title", "description"):
            fallback = normalized.get(fallback_key)
            if isinstance(fallback, str) and fallback.strip():
                summary = fallback.strip()
                break
    summary_text = str(summary or "").strip()
    if not summary_text:
        raise ValueError("Execution plan requires a summary")

    return {
        "summary": summary_text,
        "steps": _normalize_plan_items(normalized.get("steps"), limit=12),
        "open_questions": _normalize_plan_items(normalized.get("open_questions"), limit=8),
        "assumptions": _normalize_plan_items(normalized.get("assumptions"), limit=8),
    }


def _build_tool_action_payload(*, thought: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
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
    return {
        "kind": "tool",
        "thought": emitted_thought,
        "tool": normalized_tool,
        "arguments": normalized_arguments,
    }


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


def _build_final_action_payload(*, thought: str, summary: str, result: str) -> dict[str, Any]:
    normalized_summary = str(summary or "").strip()
    normalized_result = str(result or "").strip()
    if not normalized_summary or not normalized_result:
        raise ValueError("Native finalize tool requires summary and result")
    emitted_thought = str(thought or "").strip()
    normalized_thought = emitted_thought or "Task complete"
    if not normalized_thought.strip():
        raise ValueError("Final decisions require thought text")
    return {
        "kind": "final",
        "thought": emitted_thought,
        "summary": normalized_summary,
        "result": normalized_result,
    }


def _build_plan_action_payload(*, thought: str, plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "plan",
        "thought": str(thought or "").strip(),
        "plan": dict(plan),
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


def _plan_block(plan: dict[str, Any] | None) -> str:
    if not plan:
        return "No approved execution plan yet."

    sections = [f"Plan summary: {plan.get('summary', '').strip()}"]
    steps = [str(step).strip() for step in plan.get("steps", []) if str(step).strip()]
    if steps:
        sections.append("Steps:\n" + "\n".join(f"- {step}" for step in steps))
    open_questions = [str(item).strip() for item in plan.get("open_questions", []) if str(item).strip()]
    if open_questions:
        sections.append("Open points:\n" + "\n".join(f"- {item}" for item in open_questions))
    assumptions = [str(item).strip() for item in plan.get("assumptions", []) if str(item).strip()]
    if assumptions:
        sections.append("Assumptions:\n" + "\n".join(f"- {item}" for item in assumptions))
    feedback = str(plan.get("feedback") or "").strip()
    if feedback:
        sections.append("Latest user feedback:\n" + feedback)
    return "\n\n".join(sections)


def _plan_feedback_question(plan: dict[str, Any]) -> str:
    lines = [
        "Review this execution plan before the agent starts:",
        "",
        f"Summary: {str(plan.get('summary') or '').strip()}",
    ]
    steps = [str(step).strip() for step in plan.get("steps", []) if str(step).strip()]
    if steps:
        lines.append("")
        lines.append("Planned steps:")
        lines.extend(f"- {step}" for step in steps)
    open_points = [str(item).strip() for item in plan.get("open_questions", []) if str(item).strip()]
    if open_points:
        lines.append("")
        lines.append("Open points:")
        lines.extend(f"- {item}" for item in open_points)
    assumptions = [str(item).strip() for item in plan.get("assumptions", []) if str(item).strip()]
    if assumptions:
        lines.append("")
        lines.append("Current assumptions:")
        lines.extend(f"- {item}" for item in assumptions)
    lines.append("")
    lines.append("Reply with approval or the corrections the agent should apply before continuing.")
    return "\n".join(lines)


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


def _is_low_quality_progress_summary(text: str) -> bool:
    normalized = " ".join(str(text or "").split()).strip().strip(".?!")
    if not normalized:
        return True
    trailing_word = normalized.split()[-1].lower()
    return trailing_word in {
        "a",
        "an",
        "and",
        "at",
        "for",
        "from",
        "in",
        "into",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }


def _fallback_progress_summary(action: dict[str, Any]) -> str:
    kind = str(action.get("kind") or "").strip()
    if kind == "ask_user":
        return "Waiting for input"
    if kind == "final":
        return "Preparing final response"
    if kind != "tool":
        return "Continuing the task"

    tool_name = str(action.get("tool") or "tool").strip()
    arguments = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}

    if tool_name == "file_read":
        path = _compact_path_label(str(arguments.get("path") or ""))
        return f"Reading {path}" if path else "Reading a file"
    if tool_name == "workspace_search":
        query = str(arguments.get("query") or "").strip()
        return f"Searching for {query}" if query else "Searching the workspace"
    if tool_name == "file_write":
        path = _compact_path_label(str(arguments.get("path") or ""))
        return f"Updating {path}" if path else "Updating a file"
    if tool_name == "shell_command":
        command = str(arguments.get("command") or "").strip()
        return f"Running {command}" if command else "Running a command"
    if tool_name == "python_execution":
        return "Running a Python check"
    if tool_name == "app_initialize":
        title = str(arguments.get("title") or arguments.get("app_slug") or "the app").strip()
        return f"Initializing {title}" if title else "Initializing the app"
    return f"Running {tool_name}"


def _fallback_action_thought(action: dict[str, Any]) -> str:
    kind = str(action.get("kind") or "").strip()
    if kind == "ask_user":
        return "I need one detail from you before I can continue."
    if kind == "final":
        return "I have enough information to wrap this up."
    if kind != "tool":
        return ""

    tool_name = str(action.get("tool") or "tool").strip()
    arguments = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}

    if tool_name == "file_read":
        path = str(arguments.get("path") or "").strip()
        return f"I will read {path} next and pull out the useful details." if path else "I will inspect a file next and pull out the useful details."
    if tool_name == "workspace_search":
        query = str(arguments.get("query") or "").strip()
        return f"I will search the workspace for '{query}' to find the next clue." if query else "I will search the workspace to find the next clue."
    if tool_name == "file_write":
        path = str(arguments.get("path") or "").strip()
        return f"I am updating {path} now." if path else "I am writing the next change now."
    if tool_name == "shell_command":
        command = str(arguments.get("command") or "").strip()
        return f"I am running '{command}' to verify the next step." if command else "I am running a shell command to verify the next step."
    if tool_name == "python_execution":
        return "I am running a small Python check to inspect the current state."
    if tool_name == "app_initialize":
        title = str(arguments.get("title") or arguments.get("app_slug") or "the app").strip()
        return f"I am preparing {title} so I can work inside the generated app files."

    return f"I am using {tool_name} for the next step."


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
            "Retry with exactly one corrected tool call. If you are creating a new app, include title. "
            "If you are attaching an existing app, include app_slug."
        )
        if arguments_preview:
            return f"{base} Previous invalid arguments: {arguments_preview}"
        return base
    if tool_call_count is not None:
        return (
            f"Your previous response was invalid because it produced {tool_call_count} tool calls. "
            "Retry by making exactly one tool call."
        )
    if truncated_by_length and tool_name:
        if arguments_preview:
            return (
                f"Your previous tool call for {tool_name} was truncated because the response hit the token limit. "
                f"Retry with exactly one complete tool call containing valid JSON arguments. Previous truncated arguments: {arguments_preview}"
            )
        return (
            f"Your previous tool call for {tool_name} was truncated because the response hit the token limit. "
            "Retry with exactly one complete tool call containing valid JSON arguments."
        )
    if tool_name:
        if arguments_preview:
            return (
                f"Your previous tool call for {tool_name} was invalid: {error_text}. "
                f"Retry with exactly one corrected tool call. Previous invalid arguments: {arguments_preview}"
            )
        return f"Your previous tool call for {tool_name} was invalid: {error_text}. Retry with exactly one corrected tool call."
    return f"Your previous response was invalid: {error_text}. Retry by making exactly one corrected tool call."