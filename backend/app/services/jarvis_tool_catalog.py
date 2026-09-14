"""Authorization-reviewed tools shared by Jarvis voice providers."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.jarvis_brain import ALLOWED_SHELL_TOOLS


def get_jarvis_tool_definitions(provider: str = "openai") -> list[dict[str, Any]]:
    """Return an isolated copy of the canonical restricted tool catalog."""
    if provider not in {"openai", "openai_live"}:
        raise ValueError(f"Unsupported Jarvis voice provider: {provider}")
    return deepcopy(_canonical_tools())


def _canonical_tools() -> list[dict[str, Any]]:
    object_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    task_reference = {
        "type": "string",
        "description": "Natural reference such as this task, last completed task, or wake-word task. Never ask for an ID.",
    }
    reminder_timing = {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["after"]},
                    "seconds": {"type": "integer", "minimum": 1, "maximum": 31_536_000},
                },
                "required": ["kind", "seconds"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["local"]},
                    "date": {"type": "string", "description": "Optional YYYY-MM-DD local date."},
                    "time": {"type": "string", "description": "Local wall-clock time, for example 14:30."},
                    "time_zone": {"type": "string", "description": "Optional IANA timezone when different from the active device."},
                },
                "required": ["kind", "time"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["instant"]},
                    "due_at": {"type": "string", "description": "ISO-8601 instant with an explicit UTC offset."},
                },
                "required": ["kind", "due_at"],
                "additionalProperties": False,
            },
        ]
    }
    tools = [
        {
            "type": "function",
            "name": "dispatch_task",
            "description": "Delegate substantial work to a durable background task and acknowledge it immediately.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "importance": {"type": "string", "enum": ["low", "normal", "high"]},
                    "presentation_policy": {"type": "string", "enum": ["critical_only", "follow_progress", "silent"]},
                    "targets": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "resource_type": {"type": "string"},
                                "resource_key": {"type": "string"},
                                "mutable": {"type": "boolean"},
                            },
                            "required": ["resource_type", "resource_key"],
                        },
                    },
                },
                "required": ["goal"],
            },
        },
        {"type": "function", "name": "list_tasks", "description": "List recent active or historical tasks.", "parameters": object_schema},
        {
            "type": "function",
            "name": "get_task",
            "description": "Read a task using a natural reference.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_reference": task_reference,
                    "include_diagnostics": {
                        "type": "boolean",
                        "description": "True only when the user explicitly asks for technical failure details or the raw error.",
                    },
                },
                "required": ["task_reference"],
            },
        },
        {
            "type": "function",
            "name": "set_task_update_policy",
            "description": "Change whether a task proactively reports ordinary progress. Critical outcomes remain enabled unless silent is selected.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_reference": task_reference,
                    "presentation_policy": {"type": "string", "enum": ["critical_only", "follow_progress", "silent"]},
                },
                "required": ["task_reference", "presentation_policy"],
            },
        },
        {
            "type": "function",
            "name": "answer_task_question",
            "description": "Answer a pending question using a natural task reference.",
            "parameters": {
                "type": "object",
                "properties": {"task_reference": task_reference, "question": {"type": "string"}, "answer": {"type": "string"}},
                "required": ["task_reference", "answer"],
            },
        },
        {
            "type": "function",
            "name": "cancel_task",
            "description": "Cancel an unambiguous task using a natural reference.",
            "parameters": {"type": "object", "properties": {"task_reference": task_reference}, "required": ["task_reference"]},
        },
        {
            "type": "function",
            "name": "create_reminder",
            "description": "Create a one-shot durable reminder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The reminder subject. Omit when the user gave only a time."},
                    "timing": reminder_timing,
                    "allow_duplicate": {"type": "boolean", "description": "True only when the user explicitly asks for another separate identical reminder."},
                },
                "required": ["timing"],
            },
        },
        {"type": "function", "name": "list_reminders", "description": "List durable reminders.", "parameters": object_schema},
        {
            "type": "function",
            "name": "get_attention_checklist",
            "description": (
                "Read pending Jarvis updates. Mention an item naturally before acknowledging it; "
                "do not read internal identifiers aloud. Quiet context does not require an immediate announcement."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cursor": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                },
            },
        },
        {
            "type": "function",
            "name": "acknowledge_task_update",
            "description": (
                "Mark exactly one pending update handled after communicating it. Use the opaque presentation_id "
                "and exposure_token returned by get_attention_checklist. Bookkeeping does not require spoken confirmation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "presentation_id": {"type": "string"},
                    "exposure_token": {"type": "string"},
                },
                "required": ["presentation_id", "exposure_token"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "history_grep",
            "description": "Search local conversation, task, and approved-memory history with restricted rg syntax.",
            "parameters": {"type": "object", "properties": {"argv": {"type": "string"}}, "required": ["argv"]},
        },
        {
            "type": "function",
            "name": "set_jarvis_voice",
            "description": "Select Jarvis voice; Marin is feminine and Cedar masculine.",
            "parameters": {"type": "object", "properties": {"voice": {"type": "string", "enum": ["marin", "cedar"]}}, "required": ["voice"]},
        },
        {"type": "function", "name": "start_new_jarvis_log", "description": "Close the current Jarvis daily log and start a fresh one.", "parameters": object_schema},
        {"type": "function", "name": "end_voice_session", "description": "End the active voice session when the user asks Jarvis to sleep or disconnect.", "parameters": object_schema},
    ]
    if {str(item["name"]) for item in tools} != set(ALLOWED_SHELL_TOOLS):
        raise RuntimeError("Jarvis voice tools do not match the restricted gateway allowlist")
    return tools


__all__ = ["get_jarvis_tool_definitions"]
