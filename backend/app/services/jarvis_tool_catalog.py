"""Authorization-reviewed tools shared by Jarvis voice providers."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.jarvis_brain import ALLOWED_SHELL_TOOLS
from app.services.task_workflows import enabled_workflows


def get_jarvis_tool_definitions(provider: str = "openai") -> list[dict[str, Any]]:
    """Return an isolated copy of the canonical restricted tool catalog."""
    if provider not in {"openai", "openai_live"}:
        raise ValueError(f"Unsupported Jarvis voice provider: {provider}")
    return deepcopy(_canonical_tools())


def _canonical_tools() -> list[dict[str, Any]]:
    from app.services.task_execution_preferences import preferences_schema
    from app.services.task_result_contract import ResultRequest
    result_request_schema = ResultRequest.model_json_schema()
    # Generic agents publish Documents. Workflow-specific kinds are selected
    # from their pinned definition by start_workflow, never invented here.
    result_request_schema["properties"]["kind"] = {
        "anyOf": [{"type": "string", "enum": ["document.v1"]}, {"type": "null"}],
        "default": None,
        "description": "Only document.v1 for maintained generic results. Omit for answer mode.",
    }
    result_request_schema["description"] = (
        "Use answer for ordinary task completion, new for a maintained Document, "
        "or update for an existing Document with its exact public_id and expected_revision."
    )
    execution_preferences = {"execution_preferences": preferences_schema(),
        "intent_id": {"type": "string", "minLength": 1, "maxLength": 128,
                      "description": "Stable per-outcome ID within this user turn; share across admission routes."}}
    screen_definitions: list[str] = []
    available = enabled_workflows()
    for loaded in available:
        screen_definitions.append(f"{loaded.id}@{loaded.version}")
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
            "description": "After listening to the complete request, delegate substantial work to a durable background task and briefly confirm admission. Only goal and intent_id are required; omit optional fields unless the request needs them. For a maintained Document, request result mode new; its URL is allocated during admission and the final Markdown is published automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "result": result_request_schema,
                    **execution_preferences,
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
                "required": ["goal", "intent_id"],
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
            "description": "Answer a pending question using a natural task reference. Permission questions require the user's explicit approve or deny decision about the displayed exact action; never approve them yourself.",
            "parameters": {
                "type": "object",
                "properties": {"task_reference": task_reference,
                    "question": {"type": ["string", "null"], "default": None,
                        "description": "Omit/null when this task has one pending question."}, "answer": {"type": "string"}},
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
            "name": "continue_task",
            "description": "Add a follow-up to the same task. For results, explicitly select answer (no revision), update (public_id and expected_revision), or new (additional result). Use get_task to inspect IDs/revisions; never guess them.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_reference": task_reference,
                    "content": {"type": "string"},
                    "result": result_request_schema,
                    **execution_preferences,
                    "screen_context_mode": {"anyOf": [{"type": "string", "enum": ["activation", "fresh"]}, {"type": "null"}],
                        "default": None, "description": "Omit/null to reuse retained context for ordinary follow-ups. Select activation/fresh only when the user explicitly requests newly captured screen context."},
                    "capture_id": {"type": ["string", "null"], "minLength": 1, "default": None},
                },
                "required": ["task_reference", "content", "intent_id"],
            },
        },
        {
            "type": "function",
            "name": "control_task",
            "description": "Change a task's importance, yield, suspend, resume, retry, restart or cancel it. Supply a brief reason for the audit trail.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_reference": task_reference,
                    "action": {
                        "type": "string",
                        "enum": [
                            "set_importance", "yield", "suspend", "resume", "cancel",
                            "retry", "restart_execution", "restart_from_beginning",
                        ],
                    },
                    "importance": {"type": "string", "enum": ["low", "normal", "high"]},
                    "immediate": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["task_reference", "action", "reason"],
            },
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
                "Read pending Jarvis updates. Do not read internal identifiers aloud. "
                "Quiet context does not require an immediate announcement."
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
    if screen_definitions:
        tools.append({
            "type": "function", "name": "start_workflow",
            "description": "Start an enabled workflow. Workflows requiring a screen use this invocation's captured screen and require capture readiness. " + " ".join(
                f"{item.id}@{item.version}: {item.purpose}. Required context: {', '.join(item.required_initial_context) or 'none'}. Examples: {'; '.join(item.routing_examples)}."
                for item in available
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "definition": {"type": "string", "enum": sorted(set(screen_definitions))},
                    "screen_context_mode": {"type": "string", "enum": ["activation", "fresh"]},
                    **execution_preferences,
                    "capture_id": {"type": ["string", "null"], "minLength": 1, "default": None, "description": "Exact capture ID if known; otherwise omit/null."},
                },
                "required": ["goal", "definition", "intent_id"], "additionalProperties": False,
            },
        })
    expected = set(ALLOWED_SHELL_TOOLS) if screen_definitions else set(ALLOWED_SHELL_TOOLS) - {"start_workflow"}
    if {str(item["name"]) for item in tools} != expected:
        raise RuntimeError("Jarvis voice tools do not match the restricted gateway allowlist")
    for tool in tools:
        if tool["name"] in {"dispatch_task","start_workflow","set_task_update_policy"}:
            if tool["name"] == "start_workflow":
                tool["parameters"]["properties"]["presentation_policy"] = {"type":"string","enum":["critical_only","follow_progress","silent"]}
            tool["parameters"]["properties"].update({
                "notification_intent":{"type":"string","enum":["requested","discretionary","suppressed"],
                    "description":"Completion subscription, separate from progress policy: requested = user asked to be told or assistant promised; suppressed = user asked for silence; discretionary = neither request nor promise. 'Tell me when it finishes' MUST select requested."},
                "notification_reason":{"type":"string","minLength":1,"maxLength":160,
                    "description":"Brief user request or assistant promise establishing the preference. Required when intent is supplied."}})
            tool["description"] += " Record notification preferences and later corrections through these fields."
            tool["parameters"]["required"] = list(dict.fromkeys([
                *tool["parameters"].get("required", []), "notification_intent", "notification_reason"]))
        # Responses otherwise normalizes optional fields into required ones.
        # Jarvis still validates every completed call against these parameters.
        tool["strict"] = False
        tool["parameters"].setdefault("additionalProperties", False)
    return tools


__all__ = ["get_jarvis_tool_definitions"]
