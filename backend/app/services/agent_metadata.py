from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

TASK_KIND = "agent"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


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
    metadata.setdefault("created_at", _iso_now())
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