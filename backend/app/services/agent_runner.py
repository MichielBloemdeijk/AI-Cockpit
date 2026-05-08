"""Minimal multi-step agent loop backed by durable conversation runs."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import inspect
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from app.config import settings
from app.models.chat import Message, PromptMetrics
from app.services.agent_tools import (
    ToolExecutionContext,
    build_tool_context,
    execute_agent_tool,
    get_agent_tool_definitions,
    get_agent_tool_provider_definitions,
)
from app.services.chat_settings import chat_settings_service
from app.services.app_registry import app_registry_service
from app.services.conversation_store import conversation_store
from app.services.llm import PromptSegment, chat_completion, chat_completion_stream_response, ensure_prompt_metrics, get_session_prompt_cache_status, render_prompt_segments, supports_native_tool_calls

logger = logging.getLogger(__name__)

MAX_AGENT_STEPS = 8
MAX_TOOL_SNIPPET_CHARS = 4_000
AGENT_PROGRESS_SUMMARY_INTERVAL_STEPS = 3
AGENT_PROGRESS_SUMMARY_MAX_TOKENS = 192
TASK_KIND = "agent"
TASK_CONTEXT_COMPACTION_TOMBSTONE = "[Old tool result content cleared; see task artifacts for the full output.]"
NATIVE_AGENT_ASK_USER_TOOL = "task_ask_user"
NATIVE_AGENT_FINALIZE_TOOL = "task_finalize"
NATIVE_AGENT_PLAN_TOOL = "task_plan"
AgentStreamCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


@dataclass(slots=True)
class AgentStepPresentation:
    thought: str
    progress_summary: str


def _native_agent_request_overrides(model: str) -> dict[str, Any] | None:
    normalized = str(model or "").strip().lower()
    overrides: dict[str, Any] = {"parallel_tool_calls": False}
    if normalized.startswith("anthropic/"):
        overrides["reasoning"] = {"max_tokens": 1024}
    elif normalized.startswith("google/"):
        overrides["reasoning"] = {"effort": "low", "exclude": False}
    elif normalized.startswith("moonshotai/kimi-k2.6"):
        overrides["reasoning"] = {"enabled": True, "exclude": False}
        overrides["provider"] = {
            "require_parameters": True,
            "sort": "throughput",
        }
    return overrides or None


def _summary_request_overrides(model: str) -> dict[str, Any] | None:
    normalized = str(model or "").strip().lower()
    if normalized.startswith(("anthropic/", "google/", "moonshotai/", "openai/", "x-ai/")):
        return {"reasoning": {"exclude": True, "effort": "none"}}
    return None


def _use_streaming_native_decision(model: str) -> bool:
    return supports_native_tool_calls(model)


def _native_plan_max_tokens(model: str) -> int:
    return 1200


def _native_decision_max_tokens(model: str) -> int:
    if str(model or "").strip().lower().startswith("anthropic/"):
        return 2500
    return 1200


def _native_decision_attempt_max_tokens(model: str, attempt: int) -> int:
    base = _native_decision_max_tokens(model)
    if attempt <= 0:
        return base
    return max(base * 4, 4096)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


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


def _resolve_context_root_value(value: str, context: ToolExecutionContext) -> Path | None:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate.resolve()

    candidate_bases = [context.workspace_root]
    if candidate.parts:
        repo_root = settings.backend_root.parent.resolve()
        repo_anchor = repo_root / candidate.parts[0]
        if repo_anchor.is_dir() or repo_anchor.exists():
            candidate_bases = [repo_root, context.workspace_root, *context.read_roots]
        else:
            for allowed in context.read_roots:
                anchor = allowed / candidate.parts[0]
                if anchor.is_dir() or anchor.exists():
                    candidate_bases = [*context.read_roots, context.workspace_root]
                    break
    seen: set[str] = set()
    for base in candidate_bases:
        key = base.as_posix()
        if key in seen:
            continue
        seen.add(key)
        resolved = (base / candidate).resolve()
        for allowed in context.read_roots:
            try:
                resolved.relative_to(allowed)
                return resolved
            except ValueError:
                continue
    return None


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


def _build_run_summary(metadata: dict[str, Any], final_summary: str) -> dict[str, Any]:
    history = list(metadata.get("history", []))
    tools_used: list[str] = []
    changed_files: list[str] = []
    commands_executed: list[str] = []
    questions_answered: list[str] = []
    for item in history:
        kind = item.get("kind")
        if kind == "tool":
            tool_name = str(item.get("tool") or "").strip()
            if tool_name and tool_name not in tools_used:
                tools_used.append(tool_name)
            arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
            if tool_name == "file_write":
                path = str(arguments.get("path") or "").strip()
                if path and path not in changed_files:
                    changed_files.append(path)
            elif tool_name == "shell_command":
                command = str(arguments.get("command") or "").strip()
                if command:
                    commands_executed.append(command)
            elif tool_name == "python_execution":
                code = str(arguments.get("code") or "").strip()
                if code:
                    commands_executed.append(code.splitlines()[0][:160])
        elif kind == "question_answer":
            question = str(item.get("question") or "").strip()
            answer = str(item.get("answer") or "").strip()
            if question or answer:
                questions_answered.append(f"Q: {question} A: {answer}".strip())

    return {
        "summary": final_summary,
        "tools_used": tools_used,
        "changed_files": changed_files,
        "commands_executed": commands_executed,
        "questions_answered": questions_answered,
        "completed_steps": int(metadata.get("current_step", 0) or 0),
    }


def _microcompact_history_entries(history: list[dict[str, Any]], *, aggressive: bool = False) -> list[dict[str, Any]]:
    if not history:
        return []

    preserve_recent = max(
        0,
        settings.conversation_cache_cold_keep_recent_messages if aggressive else settings.conversation_microcompact_keep_recent_messages,
    )
    max_chars = max(1, settings.conversation_microcompact_max_chars)
    guardrail_limit = max(max_chars, settings.conversation_compaction_guardrail_max_chars)
    compact_before_index = max(0, len(history) - preserve_recent)
    compacted: list[dict[str, Any]] = []
    for index, item in enumerate(history):
        rendered = dict(item)
        if (
            index < compact_before_index
            and rendered.get("kind") == "tool"
            and isinstance(rendered.get("output"), str)
            and (aggressive or len(str(rendered.get("output") or "")) > max_chars or len(str(rendered.get("output") or "")) > guardrail_limit)
        ):
            rendered["output"] = TASK_CONTEXT_COMPACTION_TOMBSTONE
        elif isinstance(rendered.get("output"), str) and len(str(rendered.get("output") or "")) > guardrail_limit:
            rendered["output"] = str(rendered.get("output") or "")[:guardrail_limit] + "\n...[truncated]"
        compacted.append(rendered)
    return compacted


def _history_summary_source(history: list[dict[str, Any]]) -> str:
    compacted = _microcompact_history_entries(history)
    lines: list[str] = []
    for index, item in enumerate(compacted, start=1):
        kind = item.get("kind", "step")
        if kind == "tool":
            output = str(item.get("output", "")).strip()
            guardrail_limit = max(settings.conversation_microcompact_max_chars, settings.conversation_compaction_guardrail_max_chars)
            if len(output) > guardrail_limit:
                output = output[:guardrail_limit] + "\n...[truncated]"
            lines.append(
                f"{index}. tool {item.get('tool')} args={json.dumps(item.get('arguments', {}), ensure_ascii=True)} ok={item.get('ok', True)} output={output}"
            )
        elif kind == "question_answer":
            lines.append(
                f"{index}. question '{item.get('question', '')}' answer='{item.get('answer', '')}'"
            )
        else:
            lines.append(f"{index}. {json.dumps(item, ensure_ascii=True)}")
    return "\n".join(lines) if lines else "No prior steps yet."


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


async def _emit_agent_stream_update(
    callback: AgentStreamCallback | None,
    payload: dict[str, Any],
) -> None:
    if callback is None:
        return
    result = callback(payload)
    if asyncio.iscoroutine(result):
        await result


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


def _infer_app_file_read_path(metadata: dict[str, Any]) -> str | None:
    payload = metadata.get("payload") if isinstance(metadata.get("payload"), dict) else {}
    app = payload.get("app") if isinstance(payload, dict) else None
    if not isinstance(app, dict):
        return None

    candidates: list[str] = []
    for key in ("frontend_entry_path", "frontend_layout_path", "manifest_path"):
        value = str(app.get(key) or "").strip()
        if value and value not in candidates:
            candidates.append(value)
    scaffolded = app.get("scaffolded_files") if isinstance(app.get("scaffolded_files"), list) else []
    styles_path = next((str(path).strip() for path in scaffolded if str(path).strip().endswith("/styles.css")), None)
    if styles_path and styles_path not in candidates:
        candidates.insert(2, styles_path)
    for value in scaffolded:
        normalized = str(value).strip()
        if normalized and normalized not in candidates:
            candidates.append(normalized)

    history = metadata.get("history") if isinstance(metadata.get("history"), list) else []
    completed_reads = {
        str((item.get("arguments") or {}).get("path") or "").strip()
        for item in history
        if item.get("kind") == "tool"
        and item.get("tool") == "file_read"
        and item.get("ok") is True
        and isinstance(item.get("arguments"), dict)
    }
    for candidate in candidates:
        if candidate and candidate not in completed_reads:
            return candidate
    return candidates[0] if candidates else None


def _normalize_task_metadata(run_metadata: dict[str, Any] | None) -> dict[str, Any]:
    metadata = dict(run_metadata or {})
    metadata.setdefault("run_kind", metadata.get("task_type", TASK_KIND))
    metadata.setdefault("active_step_index", metadata.get("current_step", 0))
    metadata.setdefault("active_step", metadata.get("current_action", "Queued"))
    metadata.setdefault("summary", metadata.get("run_summary"))
    metadata.setdefault("app_context", None)
    metadata.setdefault("agent_status", metadata.get("task_status", "pending"))
    metadata.setdefault("task_type", TASK_KIND)
    metadata.setdefault("current_step", 0)
    metadata.setdefault("current_action", "Queued")
    metadata.setdefault("history", [])
    metadata.setdefault("allowed_roots", [])
    metadata.setdefault("payload", {})
    metadata.setdefault("write_roots", [])
    metadata.setdefault("skip_plan_feedback", False)
    metadata.setdefault("plan", None)
    metadata.setdefault("run_summary", None)
    metadata.setdefault("task_status", "pending")
    metadata.setdefault("created_at", iso_now())
    metadata.setdefault("updated_at", metadata["created_at"])
    return _sync_agent_metadata_aliases(metadata)


def _serialize_agent_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    serialized = _sync_agent_metadata_aliases(dict(metadata))
    payload = dict(serialized.get("payload") or {}) if isinstance(serialized.get("payload"), dict) else {}
    payload.pop("task_mode", None)
    serialized["payload"] = payload
    serialized.pop("task_type", None)
    serialized.pop("task_status", None)
    serialized.pop("current_step", None)
    serialized.pop("current_action", None)
    serialized.pop("run_summary", None)
    return serialized


def _sync_agent_metadata_aliases(metadata: dict[str, Any]) -> dict[str, Any]:
    run_kind = str(metadata.get("task_type") or metadata.get("run_kind") or TASK_KIND).strip() or TASK_KIND
    metadata["run_kind"] = run_kind
    metadata["task_type"] = run_kind

    agent_status = str(metadata.get("task_status") or metadata.get("agent_status") or "pending").strip() or "pending"
    metadata["agent_status"] = agent_status
    metadata["task_status"] = agent_status

    current_step_value = metadata.get("current_step", metadata.get("active_step_index", 0))
    try:
        active_step_index = int(current_step_value or 0)
    except (TypeError, ValueError):
        active_step_index = 0
    metadata["active_step_index"] = active_step_index
    metadata["current_step"] = active_step_index

    active_step = str(metadata.get("current_action") or metadata.get("active_step") or "Queued").strip() or "Queued"
    metadata["active_step"] = active_step
    metadata["current_action"] = active_step

    summary_payload = metadata.get("run_summary")
    if summary_payload is None:
        summary_payload = metadata.get("summary")
    metadata["summary"] = summary_payload
    metadata["run_summary"] = summary_payload

    payload = dict(metadata.get("payload") or {}) if isinstance(metadata.get("payload"), dict) else {}
    app_context = dict(metadata.get("app_context") or {}) if isinstance(metadata.get("app_context"), dict) else {}

    mode = str(payload.get("task_mode") or app_context.get("mode") or "").strip()
    app = app_context.get("app") if isinstance(app_context.get("app"), dict) else None
    if isinstance(payload.get("app"), dict):
        app = dict(payload.get("app") or {})

    if mode:
        app_context["mode"] = mode
        payload["task_mode"] = mode
    else:
        payload.pop("task_mode", None)

    if isinstance(app, dict) and app:
        app_context["app"] = app
        payload["app"] = app
    else:
        app_context.pop("app", None)
        payload.pop("app", None)

    metadata["app_context"] = app_context if app_context else None
    metadata["payload"] = payload
    return metadata


class AgentRunner:
    def _structured_model_is_deprioritized(self, model: str) -> bool:
        return False

    async def _structured_model_candidates(self, primary_model: str) -> list[str]:
        defaults = await chat_settings_service.get_defaults()
        if self._structured_model_is_deprioritized(primary_model):
            candidates = [
                defaults.single_model,
                defaults.synthesizer_model,
                *defaults.council_models,
                primary_model,
            ]
        else:
            candidates = [
                primary_model,
                defaults.single_model,
                defaults.synthesizer_model,
                *defaults.council_models,
            ]
        ordered: list[str] = []
        seen: set[str] = set()
        for value in candidates:
            normalized = str(value or "").strip()
            if normalized and normalized not in seen:
                ordered.append(normalized)
                seen.add(normalized)
        return ordered

    def _normalize_tool_arguments(self, metadata: dict[str, Any], tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(arguments)
        if tool_name == "file_read" and not str(normalized.get("path") or "").strip():
            inferred_path = _infer_app_file_read_path(metadata)
            if inferred_path:
                normalized["path"] = inferred_path
        return normalized

    async def _update_run_metadata(self, run_id: str, metadata: dict[str, Any]) -> None:
        await conversation_store.update_run_metadata(run_id, _serialize_agent_metadata(metadata))

    async def _record_llm_metrics(
        self,
        *,
        conversation_id: str,
        run_id: str,
        model: str,
        metrics: PromptMetrics | None,
        request_kind: str = "task.agent.decision",
    ) -> str | None:
        if metrics is None:
            return None
        payload = {
            "request_kind": request_kind,
            "model": model,
            **metrics.model_dump(),
        }
        event = await conversation_store.append_event(
            conversation_id,
            run_id=run_id,
            actor_kind="system",
            event_type="llm.request.completed",
            payload_json=payload,
        )
        await conversation_store.attach_artifact(
            conversation_id,
            run_id=run_id,
            source_event_id=event.id,
            artifact_type="llm.prompt.metrics",
            mime_type="application/json",
            content_json=payload,
        )
        return event.id

    async def _record_llm_visible_output(
        self,
        *,
        conversation_id: str,
        run_id: str,
        source_event_id: str | None,
        request_kind: str,
        response: Any,
        streamed_visible_deltas: list[str] | None = None,
    ) -> None:
        if source_event_id is None:
            return
        await conversation_store.attach_artifact(
            conversation_id,
            run_id=run_id,
            source_event_id=source_event_id,
            artifact_type="llm.response.visible_output",
            mime_type="application/json",
            content_json={
                "request_kind": request_kind,
                "model": getattr(response, "model", None),
                "content": getattr(response, "content", ""),
                "reasoning": getattr(response, "reasoning", ""),
                "reasoning_details": [dict(item) for item in getattr(response, "reasoning_details", [])],
                "error": getattr(response, "error", None),
                "finish_reason": getattr(response, "finish_reason", None),
                "streamed_visible_deltas": list(streamed_visible_deltas or []),
                "tool_calls": [tool_call.model_dump() for tool_call in getattr(response, "tool_calls", [])],
            },
        )

    async def _generate_progress_summary(
        self,
        *,
        conversation_id: str,
        run_id: str,
        metadata: dict[str, Any],
        action: dict[str, Any],
        step_number: int,
        thought: str,
        default_summary: str,
    ) -> str:
        defaults = await chat_settings_service.get_defaults()
        model = str(defaults.synthesizer_model or metadata.get("model") or "").strip()
        if not model:
            return default_summary

        system_prompt = (
            "You write concise live progress updates for AI-Cockpit. "
            "Respond with exactly one short sentence in present tense, ideally 3-9 words. "
            "Mention the file, function, query, or command when useful. "
            "Do not mention tools, JSON, or schemas. Do not use first person."
        )
        user_prompt = (
            f"Task goal:\n{metadata.get('goal', '')}\n\n"
            f"Approved plan:\n{_plan_block(metadata.get('plan'))}\n\n"
            f"Upcoming action:\n{json.dumps(action, ensure_ascii=True)}\n\n"
            f"Visible assistant text, if any:\n{thought or '[none]'}\n\n"
            f"Fallback summary:\n{default_summary}\n\n"
            "Return only the progress update."
        )
        prompt_segments = [
            PromptSegment(role="system", text=system_prompt, cache_candidate=True, stable=True),
            PromptSegment(role="user", text=user_prompt),
        ]
        response = await chat_completion(
            [Message(role="system", content=system_prompt), Message(role="user", content=user_prompt)],
            model,
            temperature=0.1,
            max_tokens=AGENT_PROGRESS_SUMMARY_MAX_TOKENS,
            session_id=f"task:{conversation_id}:progress:{step_number}",
            prompt_segments=prompt_segments,
            request_overrides=_summary_request_overrides(model),
        )
        prompt_metrics = ensure_prompt_metrics(
            messages=[Message(role="system", content=system_prompt), Message(role="user", content=user_prompt)],
            model=response.model,
            session_id=f"task:{conversation_id}:progress:{step_number}",
            usage=getattr(response, "usage", None),
            prompt_metrics=getattr(response, "prompt_metrics", None),
            rendered_messages=render_prompt_segments(prompt_segments, response.model),
        )
        metrics_event_id = await self._record_llm_metrics(
            conversation_id=conversation_id,
            run_id=run_id,
            model=response.model,
            metrics=prompt_metrics,
            request_kind="task.agent.progress.summary",
        )
        await self._record_llm_visible_output(
            conversation_id=conversation_id,
            run_id=run_id,
            source_event_id=metrics_event_id,
            request_kind="task.agent.progress.summary",
            response=response,
        )
        if response.error:
            return default_summary
        if str(getattr(response, "finish_reason", "") or "").strip().lower() == "length":
            return default_summary

        summary = " ".join(response.content.split()).strip().strip("\"'")
        if not summary or _is_low_signal_tool_intent_summary(summary) or _is_low_quality_progress_summary(summary):
            return default_summary
        return summary

    async def _build_step_presentation(
        self,
        *,
        conversation_id: str,
        run_id: str,
        metadata: dict[str, Any],
        action: dict[str, Any],
        step_number: int,
    ) -> AgentStepPresentation:
        thought = str(action.get("thought") or "").strip()
        default_summary = _fallback_progress_summary(action)
        should_generate_summary = str(action.get("kind") or "").strip() == "tool" and (
            not thought
            or _is_low_signal_tool_intent_summary(thought)
            or (step_number > 1 and step_number % AGENT_PROGRESS_SUMMARY_INTERVAL_STEPS == 0)
        )
        if should_generate_summary:
            progress_summary = await self._generate_progress_summary(
                conversation_id=conversation_id,
                run_id=run_id,
                metadata=metadata,
                action=action,
                step_number=step_number,
                thought=thought or _fallback_action_thought(action),
                default_summary=default_summary,
            )
        else:
            progress_summary = default_summary
        return AgentStepPresentation(thought=thought, progress_summary=progress_summary)

    async def _ensure_history_compaction_summary(
        self,
        *,
        conversation_id: str,
        run_id: str,
        metadata: dict[str, Any],
        model: str,
    ) -> str | None:
        history = list(metadata.get("history", []))
        if (
            not settings.conversation_compaction_enabled
            or settings.conversation_compaction_trigger_messages <= 0
            or len(history) <= settings.conversation_compaction_trigger_messages
        ):
            return None

        keep_tail = min(max(1, settings.conversation_compaction_keep_tail_messages), len(history))
        prefix_history = history[:-keep_tail]
        if not prefix_history:
            return None

        through_step = len(prefix_history)
        cached = metadata.get("history_compaction") or {}
        if cached.get("through_step") == through_step and isinstance(cached.get("summary"), str):
            return str(cached["summary"])

        system_prompt = (
            "You are the AI-Cockpit task-progress compactor. Summarize earlier agent steps faithfully so later agent decisions can keep working context without replaying the full task history. "
            "Keep concrete findings, decisions, files touched, failures, and unresolved questions. Omit repetition and verbose tool output."
        )
        user_prompt = (
            "Summarize the following earlier task history for continued execution. "
            "Write a concise but information-dense summary that can replace the original steps.\n\n"
            f"Task goal:\n{metadata.get('goal', '')}\n\n"
            f"Earlier steps:\n{_history_summary_source(prefix_history)}"
        )
        summary_messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]
        response = await chat_completion(
            summary_messages,
            model,
            temperature=0.2,
            max_tokens=settings.conversation_compaction_summary_max_tokens,
            session_id=f"task:{conversation_id}:compact",
            prompt_segments=[
                PromptSegment(role="system", text=system_prompt, cache_candidate=True, stable=True),
                PromptSegment(role="user", text=user_prompt),
            ],
        )
        prompt_metrics = ensure_prompt_metrics(
            messages=summary_messages,
            model=model,
            session_id=f"task:{conversation_id}:compact",
            usage=getattr(response, "usage", None),
            prompt_metrics=getattr(response, "prompt_metrics", None),
        )
        metrics_event_id = await self._record_llm_metrics(
            conversation_id=conversation_id,
            run_id=run_id,
            model=model,
            metrics=prompt_metrics,
            request_kind="task.agent.context.compaction.summary",
        )
        await self._record_llm_visible_output(
            conversation_id=conversation_id,
            run_id=run_id,
            source_event_id=metrics_event_id,
            request_kind="task.agent.context.compaction.summary",
            response=response,
        )
        if response.error:
            return None

        summary = response.content.strip()
        if not summary:
            return None

        metadata["history_compaction"] = {
            "through_step": through_step,
            "summary": summary,
            "tail_count": len(history) - through_step,
        }
        metadata["updated_at"] = iso_now()
        await self._update_run_metadata(run_id, metadata)
        compacted_event = await conversation_store.append_event(
            conversation_id,
            run_id=run_id,
            actor_kind="system",
            event_type="context.compacted",
            payload_json={
                "scope": "task.agent.history",
                "through_step": through_step,
                "source_item_count": len(prefix_history),
                "tail_item_count": len(history) - through_step,
            },
        )
        await conversation_store.attach_artifact(
            conversation_id,
            run_id=run_id,
            source_event_id=compacted_event.id,
            artifact_type="context.compaction.summary",
            mime_type="application/json",
            content_json={
                "scope": "task.agent.history",
                "through_step": through_step,
                "source_item_count": len(prefix_history),
                "tail_item_count": len(history) - through_step,
                "summary": summary,
            },
        )
        return summary

    def _plan_exists(self, metadata: dict[str, Any]) -> bool:
        plan = metadata.get("plan")
        return isinstance(plan, dict) and bool(str(plan.get("summary") or "").strip())

    async def _request_native_turn(
        self,
        *,
        metadata: dict[str, Any],
        context: ToolExecutionContext,
        conversation_id: str,
        run_id: str,
        plan_required: bool,
        step_number: int,
        stream_callback: AgentStreamCallback | None = None,
    ) -> dict[str, Any]:
        model = str(metadata.get("model") or await chat_settings_service.get_task_agent_model())
        recent_messages = await conversation_store.list_messages(conversation_id, final_only=True)
        history = list(metadata.get("history", []))

        history_summary: str | None = None
        rendered_history = history
        if history:
            cache_status = get_session_prompt_cache_status(f"task:{conversation_id}", model)
            if cache_status.cache_cold:
                keep_tail = min(max(1, settings.conversation_cache_cold_keep_recent_messages), len(history))
                cached_summary = metadata.get("history_compaction") or {}
                through_step = max(0, len(history) - keep_tail)
                if cached_summary.get("through_step") == through_step:
                    history_summary = str(cached_summary.get("summary") or "").strip() or None
                history_tail = history[-keep_tail:]
                rendered_history = _microcompact_history_entries(history_tail, aggressive=True)
            else:
                history_summary = await self._ensure_history_compaction_summary(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    metadata=metadata,
                    model=model,
                )
                if history_summary:
                    keep_tail = min(max(1, settings.conversation_compaction_keep_tail_messages), len(history))
                    history_tail = history[-keep_tail:]
                else:
                    history_tail = history
                rendered_history = _microcompact_history_entries(history_tail)

        common_user_prompt = (
            f"Goal:\n{metadata.get('goal', '')}\n\n"
            f"Current plan state:\n{_plan_block(metadata.get('plan'))}\n\n"
            f"App context:\n{_app_context_block(metadata)}\n\n"
            f"Conversation workspace: {context.workspace_root.as_posix()}\n"
            f"Allowed read roots: {json.dumps([root.as_posix() for root in context.read_roots])}\n\n"
            f"Allowed write roots: {json.dumps([root.as_posix() for root in context.write_roots])}\n\n"
            f"Recent conversation context:\n{_conversation_context_block(recent_messages)}\n\n"
            f"Recent runtime transcript:\n{_history_block(rendered_history)}"
        )

        if not supports_native_tool_calls(model):
            raise ValueError(f"Task agent model does not support the native tool loop: {model}")

        if plan_required:
            system_prompt = (
                "You are the AI-Cockpit task agent. When no approved plan exists yet, your next turn must draft the execution plan by making exactly one native tool call. "
                f"Use only the {NATIVE_AGENT_PLAN_TOOL} tool in this turn. Keep the plan concise, concrete, and grounded in the goal and current context. "
                "Do not add exploratory tool steps when the required next action is already obvious. For a new generated app request with a clear title, prefer app_initialize directly instead of app_list unless the user explicitly asked you to inspect existing apps first."
            )
            user_prompt = common_user_prompt + "\n\nMake exactly one task_plan tool call now."
            tools = [_native_plan_tool_definition()]
            session_id = f"task:{conversation_id}:plan"
            request_kind = "task.agent.plan"
        else:
            system_prompt = (
                "You are the AI-Cockpit task agent. Continue the run by making exactly one native tool call this turn. "
                f"Use {NATIVE_AGENT_FINALIZE_TOOL} when the task is complete. Use {NATIVE_AGENT_ASK_USER_TOOL} when user input is required. "
                "Use at most one tool per turn. Prefer reading or searching before writing. Use shell only when the task genuinely needs an executable invocation. "
                "Never write outside the allowed write roots. The provided native tool schemas are authoritative. "
                "For generated app work, use the app_initialize tool before editing app route files if no app is attached yet. "
                "When creating a new app, pass a clear title because that becomes the app's visible name. Pass app_slug only when you need an exact URL segment or when attaching an existing app. Never call app_initialize without title or app_slug. "
                "Before each non-final tool call, include a brief one-sentence progress update in the assistant text so the user can follow what you are about to do. "
                "If the latest successful tool result already satisfies the listed success criteria and no further external work is needed, call task_finalize immediately. For a clearly named new generated app request, do not call app_list unless the user explicitly asked to inspect existing apps first."
            )
            user_prompt = common_user_prompt + "\n\nMake exactly one tool call now."
            tools = [*get_agent_tool_provider_definitions("openai"), *_native_control_tool_definitions()]
            session_id = f"task:{conversation_id}"
            request_kind = "task.agent.native_decision"

        prompt_segments = [PromptSegment(role="system", text=system_prompt, cache_candidate=True, stable=True)]
        request_messages = [Message(role="system", content=system_prompt)]
        if history_summary:
            summary_text = "Summary of earlier task progress:\n" + history_summary
            request_messages.append(Message(role="system", content=summary_text))
            prompt_segments.append(PromptSegment(role="system", text=summary_text, cache_candidate=True, stable=True))
        request_messages.append(Message(role="user", content=user_prompt))
        prompt_segments.append(PromptSegment(role="user", text=user_prompt))

        last_error: Exception | None = None
        for attempt in range(2):
            completion_call = chat_completion if plan_required else (chat_completion_stream_response if _use_streaming_native_decision(model) else chat_completion)
            stream_buffer: list[str] = []
            reasoning_buffer: list[str] = []

            async def _handle_content_delta(delta: str) -> None:
                stream_buffer.append(delta)
                if reasoning_buffer:
                    return
                await _emit_agent_stream_update(
                    stream_callback,
                    {
                        "kind": "thought_delta",
                        "run_id": run_id,
                        "step": step_number,
                        "delta": delta,
                    },
                )

            async def _handle_reasoning_delta(delta: str) -> None:
                reasoning_buffer.append(delta)
                await _emit_agent_stream_update(
                    stream_callback,
                    {
                        "kind": "thought_delta",
                        "run_id": run_id,
                        "step": step_number,
                        "delta": delta,
                    },
                )

            completion_kwargs = {
                "temperature": 0.2,
                "max_tokens": _native_plan_max_tokens(model) if plan_required else _native_decision_attempt_max_tokens(model, attempt),
                "tools": tools,
                "tool_choice": "required",
                "session_id": session_id,
                "prompt_segments": prompt_segments,
                "request_overrides": _native_agent_request_overrides(model),
            }
            if not plan_required and "on_content_delta" in inspect.signature(completion_call).parameters:
                completion_kwargs["on_content_delta"] = _handle_content_delta
                completion_kwargs["on_reasoning_delta"] = _handle_reasoning_delta

            response = await completion_call(request_messages, model, **completion_kwargs)
            prompt_metrics = ensure_prompt_metrics(
                messages=request_messages,
                model=response.model,
                session_id=session_id,
                usage=getattr(response, "usage", None),
                prompt_metrics=getattr(response, "prompt_metrics", None),
                rendered_messages=render_prompt_segments(prompt_segments, response.model),
            )
            metrics_event_id = await self._record_llm_metrics(
                conversation_id=conversation_id,
                run_id=run_id,
                model=response.model,
                metrics=prompt_metrics,
                request_kind=request_kind,
            )
            await self._record_llm_visible_output(
                conversation_id=conversation_id,
                run_id=run_id,
                source_event_id=metrics_event_id,
                request_kind=request_kind,
                response=response,
                streamed_visible_deltas=stream_buffer,
            )
            if response.error:
                raise ValueError(response.error)

            tool_call_count = len(response.tool_calls)
            if tool_call_count != 1:
                last_error = ValueError(f"Native tool decision must return exactly one tool call, got {tool_call_count}")
                if attempt == 0:
                    retry_feedback = _native_retry_feedback(last_error, tool_call_count=tool_call_count)
                    request_messages.append(Message(role="system", content=retry_feedback))
                    prompt_segments.append(PromptSegment(role="system", text=retry_feedback))
                    continue
                raise last_error

            tool_call = response.tool_calls[0]
            await conversation_store.append_event(
                conversation_id,
                run_id=run_id,
                actor_kind="assistant",
                event_type="agent.response.tool_call",
                payload_json={
                    "tool_call_id": tool_call.id,
                    "tool": tool_call.function.name,
                    "arguments": tool_call.function.arguments,
                    "finish_reason": response.finish_reason,
                    "content": response.content,
                    "reasoning": response.reasoning,
                },
            )
            thought = response.reasoning.strip() or "".join(reasoning_buffer).strip() or response.content.strip() or "".join(stream_buffer).strip()
            try:
                arguments = _parse_native_tool_arguments(tool_call.function.arguments)
                if tool_call.function.name == NATIVE_AGENT_PLAN_TOOL:
                    if not plan_required:
                        raise ValueError("Unexpected task_plan tool during execution")
                    return _build_plan_action_payload(
                        thought=thought,
                        plan=_validate_execution_plan_payload(arguments),
                    )
                if plan_required:
                    raise ValueError(f"Native plan generation requires the {NATIVE_AGENT_PLAN_TOOL} tool")
                if tool_call.function.name == NATIVE_AGENT_ASK_USER_TOOL:
                    return _build_question_action_payload(
                        thought=thought,
                        question=str(arguments.get("question") or "").strip(),
                    )
                if tool_call.function.name == NATIVE_AGENT_FINALIZE_TOOL:
                    return _build_final_action_payload(
                        thought=thought,
                        summary=str(arguments.get("summary") or "").strip(),
                        result=str(arguments.get("result") or "").strip(),
                    )
                return _build_tool_action_payload(
                    thought=thought,
                    tool_name=tool_call.function.name,
                    arguments=arguments,
                )
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    retry_feedback = _native_retry_feedback(
                        exc,
                        tool_name=tool_call.function.name,
                        raw_arguments=tool_call.function.arguments,
                        finish_reason=response.finish_reason,
                    )
                    request_messages.append(Message(role="system", content=retry_feedback))
                    prompt_segments.append(PromptSegment(role="system", text=retry_feedback))
                    continue
                raise ValueError(str(exc)) from exc

        raise ValueError(str(last_error) if last_error is not None else "Native tool decision failed")

    async def _handle_plan_action(
        self,
        *,
        conversation_id: str,
        run_id: str,
        metadata: dict[str, Any],
        action: dict[str, Any],
    ) -> dict[str, Any]:
        plan = dict(action.get("plan") or {})
        if not plan:
            raise ValueError("Plan action requires a validated plan payload")

        plan["approved"] = bool(metadata.get("skip_plan_feedback"))
        plan["feedback_skipped"] = bool(metadata.get("skip_plan_feedback"))
        plan["feedback"] = None
        plan["approved_at"] = iso_now() if plan["approved"] else None
        metadata["plan"] = plan
        metadata["current_action"] = "Plan ready"
        metadata["updated_at"] = iso_now()
        await self._update_run_metadata(run_id, metadata)

        plan_event = await conversation_store.append_event(
            conversation_id,
            run_id=run_id,
            actor_kind="assistant",
            event_type="agent.plan.created",
            payload_json=plan,
        )
        await conversation_store.attach_artifact(
            conversation_id,
            run_id=run_id,
            source_event_id=plan_event.id,
            artifact_type="agent.plan",
            mime_type="application/json",
            content_json=plan,
        )

        if metadata.get("skip_plan_feedback"):
            metadata["current_action"] = "Plan approved; starting execution"
            metadata["task_status"] = "running"
            metadata["updated_at"] = iso_now()
            await self._update_run_metadata(run_id, metadata)
            await conversation_store.append_event(
                conversation_id,
                run_id=run_id,
                actor_kind="assistant",
                event_type="agent.plan.feedback.skipped",
                payload_json={"reason": "explicit_user_request"},
            )
            return metadata

        await self._pause_for_question(
            conversation_id=conversation_id,
            run_id=run_id,
            metadata=metadata,
            question=_plan_feedback_question(plan),
            kind="plan_feedback",
            current_action="Waiting for plan feedback",
        )
        return metadata

    async def continue_run(
        self,
        run_id: str,
        cancel_event: asyncio.Event | None = None,
        stream_callback: AgentStreamCallback | None = None,
    ) -> None:
        run = await conversation_store.get_run(run_id)
        if run is None:
            raise ValueError(f"Task run not found: {run_id}")

        metadata = _normalize_task_metadata(run.metadata_json)
        conversation = await conversation_store.get_conversation(run.conversation_id)
        if conversation is None or not conversation.workspace_path:
            raise ValueError(f"Conversation not found for task run: {run_id}")

        context = self._build_context(run.conversation_id, run.id, conversation.workspace_path, metadata)
        if not metadata.get("started_event_recorded"):
            await conversation_store.append_event(
                run.conversation_id,
                run_id=run.id,
                actor_kind="task",
                event_type="agent.run.started",
                payload_json={
                    "title": metadata.get("title"),
                    "goal": metadata.get("goal"),
                    "workspace_path": conversation.workspace_path,
                    "allowed_roots": metadata.get("allowed_roots", []),
                    "write_roots": metadata.get("write_roots", []),
                    "app": (metadata.get("payload") or {}).get("app") if isinstance(metadata.get("payload"), dict) else None,
                },
            )
            metadata["started_event_recorded"] = True

        metadata["task_status"] = "running"
        metadata["current_action"] = metadata.get("current_action") or "Planning next step"
        metadata["updated_at"] = iso_now()
        await self._update_run_metadata(run.id, metadata)
        await conversation_store.update_run_status(run.id, status="running", error=None, finished_at=None)

        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    await self._cancel_run(run.conversation_id, run.id, metadata)
                    return

                if not self._plan_exists(metadata):
                    metadata = await self._handle_plan_action(
                        conversation_id=run.conversation_id,
                        run_id=run.id,
                        metadata=metadata,
                        action=await self._request_native_turn(
                            metadata=metadata,
                            context=context,
                            conversation_id=run.conversation_id,
                            run_id=run.id,
                            plan_required=True,
                            step_number=0,
                        ),
                    )
                    if metadata.get("task_status") == "paused":
                        return
                    continue

                plan = metadata.get("plan") if isinstance(metadata.get("plan"), dict) else {}
                if plan and not bool(plan.get("approved")) and not metadata.get("skip_plan_feedback"):
                    metadata["task_status"] = "paused"
                    metadata["current_action"] = "Waiting for plan feedback"
                    metadata["updated_at"] = iso_now()
                    await self._update_run_metadata(run.id, metadata)
                    await conversation_store.update_run_status(run.id, status="paused", error=None, finished_at=None)
                    return

                current_step = int(metadata.get("current_step", 0) or 0)
                if current_step >= MAX_AGENT_STEPS:
                    raise ValueError(f"Agent step limit reached ({MAX_AGENT_STEPS})")

                action = await self._request_native_turn(
                    metadata=metadata,
                    context=context,
                    conversation_id=run.conversation_id,
                    run_id=run.id,
                    plan_required=False,
                    step_number=current_step + 1,
                    stream_callback=stream_callback,
                )
                presentation = await self._build_step_presentation(
                    conversation_id=run.conversation_id,
                    run_id=run.id,
                    metadata=metadata,
                    action=action,
                    step_number=current_step + 1,
                )
                if presentation.progress_summary:
                    metadata["current_action"] = presentation.progress_summary
                    metadata["updated_at"] = iso_now()
                    await self._update_run_metadata(run.id, metadata)
                    await _emit_agent_stream_update(
                        stream_callback,
                        {
                            "kind": "progress",
                            "run_id": run.id,
                            "step": current_step + 1,
                            "content": presentation.progress_summary,
                        },
                    )
                    await conversation_store.append_event(
                        run.conversation_id,
                        run_id=run.id,
                        actor_kind="assistant",
                        event_type="agent.progress.summary",
                        payload_json={"step": current_step + 1, "summary": presentation.progress_summary},
                    )
                if presentation.thought:
                    await _emit_agent_stream_update(
                        stream_callback,
                        {
                            "kind": "thought_done",
                            "run_id": run.id,
                            "step": current_step + 1,
                            "content": presentation.thought,
                        },
                    )
                    await conversation_store.append_event(
                        run.conversation_id,
                        run_id=run.id,
                        actor_kind="assistant",
                        event_type="agent.thought.summary",
                        payload_json={"step": current_step + 1, "thought": presentation.thought},
                    )

                action_kind = str(action.get("kind", "")).strip()
                if action_kind == "tool":
                    metadata = await self._handle_tool_action(
                        conversation_id=run.conversation_id,
                        run_id=run.id,
                        metadata=metadata,
                        action=action,
                        context=context,
                    )
                    continue

                if action_kind == "ask_user":
                    await self._pause_for_question(
                        conversation_id=run.conversation_id,
                        run_id=run.id,
                        metadata=metadata,
                        question=str(action.get("question", "")).strip(),
                    )
                    return

                if action_kind == "final":
                    await self._complete_run(
                        conversation_id=run.conversation_id,
                        run_id=run.id,
                        metadata=metadata,
                        summary=str(action.get("summary", "")).strip(),
                        result=str(action.get("result", "")).strip(),
                    )
                    return

                raise ValueError(f"Unsupported agent action kind: {action_kind or '<missing>'}")
        except Exception as exc:
            logger.exception("Agent run %s failed", run_id)
            await self._fail_run(run.conversation_id, run.id, metadata, str(exc))
            raise

    async def _ensure_execution_plan(
        self,
        *,
        conversation_id: str,
        run_id: str,
        metadata: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        if self._plan_exists(metadata):
            return metadata
        return await self._handle_plan_action(
            conversation_id=conversation_id,
            run_id=run_id,
            metadata=metadata,
            action=await self._request_native_turn(
                metadata=metadata,
                context=context,
                conversation_id=conversation_id,
                run_id=run_id,
                plan_required=True,
                step_number=0,
            ),
        )

    def _build_context(self, conversation_id: str, run_id: str, workspace_path: str, metadata: dict[str, Any]) -> ToolExecutionContext:
        base_context = build_tool_context(conversation_id, workspace_path)
        allowed_root_values = metadata.get("allowed_roots") or []
        write_root_values = metadata.get("write_roots") or []
        if not allowed_root_values:
            metadata["allowed_roots"] = [root.as_posix() for root in base_context.read_roots]
            allowed_roots = base_context.read_roots
        else:
            resolved_roots: list[Path] = []
            for value in allowed_root_values:
                resolved = _resolve_context_root_value(str(value), base_context)
                if resolved is None:
                    continue
                for allowed in base_context.read_roots:
                    try:
                        resolved.relative_to(allowed)
                        resolved_roots.append(resolved)
                        break
                    except ValueError:
                        continue
            if not resolved_roots:
                allowed_roots = base_context.read_roots
                metadata["allowed_roots"] = [root.as_posix() for root in allowed_roots]
            else:
                allowed_roots = resolved_roots

        resolved_write_roots: list[Path] = []
        for value in write_root_values:
            resolved = _resolve_context_root_value(str(value), base_context)
            if resolved is None:
                continue
            for allowed in base_context.read_roots:
                try:
                    resolved.relative_to(allowed)
                    resolved_write_roots.append(resolved)
                    break
                except ValueError:
                    continue
        if not resolved_write_roots:
            resolved_write_roots = base_context.write_roots
            metadata["write_roots"] = [root.as_posix() for root in resolved_write_roots]
        return ToolExecutionContext(
            conversation_id=base_context.conversation_id,
            run_id=run_id,
            workspace_path=base_context.workspace_path,
            workspace_root=base_context.workspace_root,
            read_roots=allowed_roots,
            write_roots=resolved_write_roots,
        )

    async def _decide_next_action(
        self,
        *,
        metadata: dict[str, Any],
        context: ToolExecutionContext,
        conversation_id: str,
        run_id: str,
        step_number: int,
        stream_callback: AgentStreamCallback | None = None,
    ) -> dict[str, Any]:
        return await self._request_native_turn(
            metadata=metadata,
            context=context,
            conversation_id=conversation_id,
            run_id=run_id,
            plan_required=False,
            step_number=step_number,
            stream_callback=stream_callback,
        )

    async def _handle_tool_action(
        self,
        *,
        conversation_id: str,
        run_id: str,
        metadata: dict[str, Any],
        action: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        tool_name = str(action.get("tool", "")).strip()
        arguments = action.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be a JSON object")
        arguments = self._normalize_tool_arguments(metadata, tool_name, arguments)

        next_step = int(metadata.get("current_step", 0) or 0) + 1
        metadata["current_action"] = f"Running {tool_name}"
        metadata["updated_at"] = iso_now()
        await self._update_run_metadata(run_id, metadata)
        started_event = await conversation_store.append_event(
            conversation_id,
            run_id=run_id,
            actor_kind="task",
            event_type="agent.tool.called",
            payload_json={"step": next_step, "tool": tool_name, "arguments": arguments},
        )

        try:
            result = await execute_agent_tool(context=context, tool=tool_name, arguments=arguments)
            if tool_name == "app_initialize":
                await self._apply_app_initialize_result(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    metadata=metadata,
                    context=context,
                    tool_metadata=result.metadata,
                )
            artifact = await conversation_store.attach_artifact(
                conversation_id,
                run_id=run_id,
                source_event_id=started_event.id,
                artifact_type=f"agent.tool.{tool_name}.output",
                mime_type="text/plain",
                content_text=result.output,
                content_json={"metadata": result.metadata},
            )
            snippet = _result_snippet(tool_name, result.output, result.metadata)
            await conversation_store.append_event(
                conversation_id,
                run_id=run_id,
                actor_kind="task",
                event_type="agent.tool.completed",
                payload_json={
                    "step": next_step,
                    "tool": tool_name,
                    "ok": True,
                    "output": snippet,
                    "artifact_id": artifact.id,
                    "metadata": result.metadata,
                },
            )
            history_entry = {
                "kind": "tool",
                "tool": tool_name,
                "arguments": arguments,
                "ok": True,
                "output": snippet,
                "artifact_id": artifact.id,
            }
            metadata["current_action"] = f"Completed {tool_name}"
        except Exception as exc:
            error_text = str(exc)
            await conversation_store.append_event(
                conversation_id,
                run_id=run_id,
                actor_kind="task",
                event_type="agent.tool.completed",
                payload_json={
                    "step": next_step,
                    "tool": tool_name,
                    "ok": False,
                    "error": error_text,
                },
            )
            history_entry = {
                "kind": "tool",
                "tool": tool_name,
                "arguments": arguments,
                "ok": False,
                "output": error_text,
            }
            metadata["current_action"] = f"Tool failed: {tool_name}"

        history = list(metadata.get("history", []))
        history.append(history_entry)
        metadata["history"] = history
        metadata["current_step"] = next_step
        metadata["task_status"] = "running"
        metadata["updated_at"] = iso_now()
        await self._update_run_metadata(run_id, metadata)
        return metadata

    async def _apply_app_initialize_result(
        self,
        *,
        conversation_id: str,
        run_id: str,
        metadata: dict[str, Any],
        context: ToolExecutionContext,
        tool_metadata: dict[str, Any],
    ) -> None:
        app = tool_metadata.get("app") if isinstance(tool_metadata, dict) else None
        if not isinstance(app, dict):
            return

        payload = dict(metadata.get("payload") or {})
        payload["app"] = app
        metadata["payload"] = payload
        metadata["app_context"] = {
            "mode": "app_builder",
            "app": app,
        }

        next_write_roots: list[str] = []
        for value in tool_metadata.get("write_roots") or []:
            normalized = str(value).strip()
            if normalized and normalized not in next_write_roots:
                next_write_roots.append(normalized)
        if next_write_roots:
            metadata["write_roots"] = next_write_roots
            resolved_write_roots: list[Path] = []
            for value in next_write_roots:
                resolved = _resolve_context_root_value(value, context)
                if resolved is not None:
                    resolved_write_roots.append(resolved)
            if resolved_write_roots:
                context.write_roots = resolved_write_roots

        app_id = str(app.get("app_id") or "").strip()
        if app_id:
            await app_registry_service.update_app(
                app_id,
                source_task_run_id=run_id,
                source_conversation_id=conversation_id,
                status="building",
                last_error=None,
            )

    async def _sync_bound_app_state(
        self,
        *,
        metadata: dict[str, Any],
        conversation_id: str,
        run_id: str,
        status: str,
        verification_status: str | None = None,
        last_error: str | None = None,
    ) -> None:
        payload = metadata.get("payload") if isinstance(metadata.get("payload"), dict) else {}
        app = payload.get("app") if isinstance(payload, dict) else None
        if not isinstance(app, dict):
            return
        app_id = str(app.get("app_id") or "").strip()
        if not app_id:
            return
        await app_registry_service.update_app(
            app_id,
            status=status,
            verification_status=verification_status,
            last_error=last_error,
            source_task_run_id=run_id,
            source_conversation_id=conversation_id,
        )

    async def _pause_for_question(
        self,
        *,
        conversation_id: str,
        run_id: str,
        metadata: dict[str, Any],
        question: str,
        kind: str = "question",
        current_action: str = "Waiting for user input",
    ) -> None:
        if not question:
            raise ValueError("Question action requires a question")
        pending_question = {
            "id": str(uuid4()),
            "kind": kind,
            "question": question,
            "asked_at": iso_now(),
            "answer": None,
        }
        metadata["pending_question"] = pending_question
        metadata["task_status"] = "paused"
        metadata["current_action"] = current_action
        metadata["updated_at"] = iso_now()
        await self._update_run_metadata(run_id, metadata)
        await conversation_store.update_run_status(run_id, status="paused", error=None, finished_at=None)
        await conversation_store.append_event(
            conversation_id,
            run_id=run_id,
            actor_kind="assistant",
            event_type="agent.question.asked",
            payload_json=pending_question,
        )

    async def _complete_run(
        self,
        *,
        conversation_id: str,
        run_id: str,
        metadata: dict[str, Any],
        summary: str,
        result: str,
    ) -> None:
        final_text = result or summary or "Task completed."
        final_summary = summary or final_text
        run_summary = _build_run_summary(metadata, final_summary)
        metadata["final_summary"] = final_summary
        metadata["result"] = {"output": final_text}
        metadata["run_summary"] = run_summary
        metadata["task_status"] = "completed"
        metadata["pending_question"] = None
        metadata["current_action"] = "Completed"
        metadata["updated_at"] = iso_now()
        await conversation_store.complete_message(
            conversation_id,
            run_id=run_id,
            role="assistant",
            content=final_text,
            actor_kind="assistant",
            event_type="conversation.assistant.message.completed",
            author_label="agent",
        )
        await conversation_store.append_event(
            conversation_id,
            run_id=run_id,
            actor_kind="assistant",
            event_type="agent.run.completed",
            payload_json={"summary": final_summary, "run_summary": run_summary},
        )
        await conversation_store.attach_artifact(
            conversation_id,
            run_id=run_id,
            artifact_type="agent.run.summary",
            mime_type="application/json",
            content_json=run_summary,
        )
        await self._sync_bound_app_state(
            metadata=metadata,
            conversation_id=conversation_id,
            run_id=run_id,
            status="ready_for_test",
            verification_status="not_started",
            last_error=None,
        )
        await self._update_run_metadata(run_id, metadata)
        await conversation_store.mark_run_completed(run_id)

    async def _fail_run(
        self,
        conversation_id: str,
        run_id: str,
        metadata: dict[str, Any],
        error: str,
    ) -> None:
        metadata["task_status"] = "failed"
        metadata["error"] = error
        metadata["current_action"] = "Failed"
        metadata["updated_at"] = iso_now()
        await self._update_run_metadata(run_id, metadata)
        await conversation_store.append_event(
            conversation_id,
            run_id=run_id,
            actor_kind="system",
            event_type="agent.run.failed",
            payload_json={"error": error},
        )
        await self._sync_bound_app_state(
            metadata=metadata,
            conversation_id=conversation_id,
            run_id=run_id,
            status="failed",
            last_error=error,
        )
        await conversation_store.mark_run_failed(run_id, error)

    async def _cancel_run(
        self,
        conversation_id: str,
        run_id: str,
        metadata: dict[str, Any],
    ) -> None:
        metadata["task_status"] = "cancelled"
        metadata["current_action"] = "Cancelled"
        metadata["updated_at"] = iso_now()
        await self._update_run_metadata(run_id, metadata)
        await conversation_store.append_event(
            conversation_id,
            run_id=run_id,
            actor_kind="system",
            event_type="agent.run.failed",
            payload_json={"error": "Task cancelled by user"},
        )
        await self._sync_bound_app_state(
            metadata=metadata,
            conversation_id=conversation_id,
            run_id=run_id,
            status="failed",
            last_error="Task cancelled by user",
        )
        await conversation_store.update_run_status(run_id, status="cancelled", error="Task cancelled by user", finished_at=utc_now())


agent_runner = AgentRunner()