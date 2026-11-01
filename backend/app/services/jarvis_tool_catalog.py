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
    # General agents publish Documents (into the Documents app); a workflow's
    # result kind comes from its pinned definition, never from here.
    result_request_schema["description"] = (
        "Use answer for ordinary task completion, new for a Document kept in the Documents app, "
        "or update to revise the task's existing result; result_id and expected_revision are optional."
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
            "description": "After listening to the complete request, delegate substantial work to a durable background task and briefly confirm admission. Only goal and intent_id are required; omit optional fields unless the request needs them. When the user wants a written result to keep, request result mode new: the final Markdown is saved as a record in the Documents app. A quick answer needs no result; it stays in the task history. Never use it to change an existing result; continue that result's task instead.",
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
            "description": "Add a follow-up to the same task. When the task has a result (a recipe, document or other record in one of the user's apps), omitting result revises that record in place (the way to edit, extend or correct an existing result). Select result mode answer for a question that changes nothing, or new for a separate additional result.",
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
        {
            "type": "function",
            "name": "look_at_screen",
            "description": (
                "Take a fresh screenshot of the phone screen and attach it to this conversation, so you can "
                "answer a question about it, for example who or what is shown, what an app says, or what to tap. "
                "Call it when no screenshot is attached yet or the screen may have changed; follow-ups about the "
                "same screen are answered from the attached screenshot. Use start_workflow instead only when "
                "the user wants a saved result such as a recipe, document or research."
            ),
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string", "minLength": 1, "maxLength": 1000,
                    "description": "The user's question about the screen, faithful and self-contained."}},
                "required": ["question"],
            },
        },
        {
            "type": "function",
            "name": "show_on_phone",
            "description": (
                "Switch the phone from whatever app it is in to Jarvis, opened on a task's result in its app (or "
                "the task itself when it has no result yet). Call it when the user asks to show, open or see a result "
                "or task, for example 'show me' or 'open the recipe'. Never open anything unasked."
            ),
            "parameters": {
                "type": "object",
                "properties": {"task_reference": task_reference},
                "required": ["task_reference"],
            },
        },
        {"type": "function", "name": "end_voice_session", "description": (
            "End this voice conversation so Jarvis goes back to waiting for its wake word. Call it whenever the "
            "user asks to stop, end, close or hang up the session or conversation, says that's all or goodbye, "
            "or asks Jarvis to sleep or disconnect. Only this call ends it; never claim it ended without it."),
            "parameters": object_schema},
        {"type": "function", "name": "list_apps", "description": (
            "List the apps installed in Cockpit, with what each one holds. Use it when the user asks which apps "
            "exist, or before opening or reading one whose name you are unsure of."),
            "parameters": object_schema},
        {"type": "function", "name": "open_app", "description": (
            "Open an installed app on the phone, for example 'open my notes'. Never open anything unasked."),
            "parameters": {"type": "object", "properties": {
                "app": {"type": "string", "description": "The app's name or slug as the user said it."},
            }, "required": ["app"], "additionalProperties": False}},
        {"type": "function", "name": "query_app_data", "description": (
            "Read records from an installed app's data to answer a question about it, for example 'what's on my "
            "notes' or 'which notes are pinned'. Filters compare top-level fields for equality."),
            "parameters": {"type": "object", "properties": {
                "app": {"type": "string", "description": "The app's name or slug."},
                "collection": {"type": "string", "description": "Collection name; may be omitted when the app has one."},
                "where": {"type": "object", "description": "Optional field equality filters, for example {\"pinned\": true}."},
                "order": {"type": "string", "description": "Optional field to sort by; prefix '-' for descending."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            }, "required": ["app"], "additionalProperties": False}},
        {"type": "function", "name": "build_app", "description": (
            "Have an app built in the background, or an installed app changed, when the user asks for one, for "
            "example 'make me an app to track my plants' or 'add due dates to my notes'. The builder designs, "
            "tests and installs it; confirm briefly that it has started. Apps can show a UI, store data and files, "
            "and run code on request, on a schedule or when their data changes, with internet access to the "
            "sites they declare."),
            "parameters": {"type": "object", "properties": {
                "request": {"type": "string", "description": "What the app should do or what should change, in the user's words with any details they gave."},
                "app": {"type": "string", "description": "The installed app to change; omit for a new app."},
            }, "required": ["request"], "additionalProperties": False}},
        {"type": "function", "name": "fix_app", "description": (
            "Have an installed app repaired in the background when the user says it is broken, or when "
            "app_status shows errors the user wants fixed. Its reported errors are passed to the builder."),
            "parameters": {"type": "object", "properties": {
                "app": {"type": "string", "description": "The app's name or slug."},
                "problem": {"type": "string", "description": "What the user said is wrong, if anything."},
            }, "required": ["app"], "additionalProperties": False}},
        {"type": "function", "name": "app_status", "description": (
            "An installed app's versions, records, files, functions (with when scheduled ones run next), "
            "recent function runs and errors it reported. Use it when the user asks whether an app works, "
            "what it did or what changed."),
            "parameters": {"type": "object", "properties": {
                "app": {"type": "string", "description": "The app's name or slug."},
            }, "required": ["app"], "additionalProperties": False}},
        {"type": "function", "name": "manage_app", "description": (
            "Roll an app back to its previous version, reset (delete) its data, or uninstall it. Only when the "
            "user asks for exactly that. Reset and uninstall delete data permanently: confirm with the user "
            "first in a short question, then call this."),
            "parameters": {"type": "object", "properties": {
                "app": {"type": "string", "description": "The app's name or slug."},
                "action": {"type": "string", "enum": ["rollback", "reset", "uninstall"]},
                "collection": {"type": "string", "description": "For reset: only this collection's records."},
                "files": {"type": "boolean", "description": "For reset: also delete the app's stored files."},
            }, "required": ["app", "action"], "additionalProperties": False}},
        {"type": "function", "name": "write_app_data", "description": (
            "Add, change or delete one record in an installed app, as its own screen would, when the user asks for "
            "exactly that, for example 'add milk to my shopping list' or 'mark the fern as watered'. Read the "
            "records first with query_app_data to find the key and fields. Records must fit the app's data shape; "
            "an error says what is wrong. Confirm with the user before deleting."),
            "parameters": {"type": "object", "properties": {
                "app": {"type": "string", "description": "The app's name or slug."},
                "collection": {"type": "string", "description": "Collection name; may be omitted when the app has one."},
                "action": {"type": "string", "enum": ["add", "update", "delete"],
                           "description": "add a new record, update fields of an existing one, or delete one."},
                "key": {"type": "string", "description": "The record's key; required for update and delete."},
                "value": {"type": "object", "description": "add: the whole record. update: only the fields to change."},
            }, "required": ["app", "action"], "additionalProperties": False}},
        {"type": "function", "name": "call_app_function", "description": (
            "Run one of an installed app's functions now and get its output, when the user asks the app to do "
            "something its functions do, for example 'import this recipe URL' or 'run the plant reminder now'. "
            "list_apps and app_status show each app's functions."),
            "parameters": {"type": "object", "properties": {
                "app": {"type": "string", "description": "The app's name or slug."},
                "function": {"type": "string", "description": "The function's name."},
                "input": {"description": "The function's input, as the app expects it; omit when it takes none."},
            }, "required": ["app", "function"], "additionalProperties": False}},
    ]
    if screen_definitions:
        tools.append({
            "type": "function", "name": "start_workflow",
            "description": "Start an enabled workflow, including those installed apps provide (their results are saved in that app). Workflows requiring a screen use this invocation's captured screen and require capture readiness. " + " ".join(
                f"{item.id}@{item.version}{f' (app {item.app})' if item.app else ''}: {item.purpose}. Required context: {', '.join(item.required_initial_context) or 'none'}. Examples: {'; '.join(item.routing_examples)}."
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
