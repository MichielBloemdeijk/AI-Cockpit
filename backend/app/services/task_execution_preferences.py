"""Shared, administrator-bounded model preferences; never permission overrides."""
from app.config import settings
from pydantic import BaseModel, ConfigDict


class ExecutionPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model: str | None = None
    reasoning_effort: str | None = None
    override_reason: str | None = None
    tools: dict | None = None


def preferences_schema() -> dict:
    return {"type": "object", "properties": {
        "model": {"type": "string", "enum": list(permitted_models())},
        "reasoning_effort": {"type": "string", "enum": ["low", "medium", "high"]},
        "override_reason": {"type": "string", "minLength": 1},
        "tools": {"type": "object", "properties": {
            "add": {"type": "array", "items": {"type": "string", "enum": ["native.local", "native.research", "native.subagents", "native.skills"]}},
            "remove": {"type": "array", "items": {"type": "string", "enum": ["native.local", "native.research", "native.subagents", "native.skills"]}}},
            "additionalProperties": False}}, "additionalProperties": False}


def permitted_models() -> tuple[str, ...]:
    configured = settings.task_execution_model_allowlist.strip()
    return tuple(sorted({value.strip() for value in configured.split(",") if value.strip()})) if configured else (settings.codex_task_model,)


def normalize_agent_preferences(config: dict | None, *, inherited: dict | None = None) -> dict:
    request = dict(config or {})
    if set(request) - {"model", "reasoning_effort", "override_reason", "tools"}:
        raise ValueError("Agent preferences cannot change execution capabilities or authentication")
    if not request:
        return dict(inherited or {})
    ExecutionPreferences.model_validate(request)
    from app.services.worker_tool_policy import normalize_overrides
    tools = normalize_overrides(request.get("tools", (inherited or {}).get("tools")))
    if settings.task_executor != "codex":
        raise ValueError("Explicit execution preferences require the Codex task route")
    defaults = {"model": settings.codex_task_model, "reasoning_effort": settings.codex_task_reasoning_effort}
    baseline = defaults | (inherited or {})
    model = request.get("model", baseline["model"])
    effort = request.get("reasoning_effort", baseline["reasoning_effort"])
    if not isinstance(model, str) or model.strip() not in permitted_models():
        raise ValueError("Execution model is not in the backend allowlist")
    if not isinstance(effort, str) or effort not in {"low", "medium", "high"}:
        raise ValueError("Execution reasoning effort must be low, medium or high")
    reason = request.get("override_reason")
    if (model.strip() != baseline["model"] or effort != baseline["reasoning_effort"]) and (not reason or not reason.strip()):
        raise ValueError("Changing execution settings requires a nonempty override_reason")
    if reason is not None and not reason.strip():
        raise ValueError("override_reason cannot be empty")
    result = {"model": model.strip(), "reasoning_effort": effort}
    if reason:
        result["override_reason"] = reason.strip()
    if "tools" in request or "tools" in (inherited or {}):
        result["tools"] = tools
    return result
