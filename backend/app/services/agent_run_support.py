from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from app.config import settings
from app.models.chat import Message, PromptMetrics
from app.services.agent_metadata import _build_run_summary, _infer_app_file_read_path, _serialize_agent_metadata
from app.services.agent_prompt_builder import (
    _fallback_action_thought,
    _fallback_progress_summary,
    _is_low_quality_progress_summary,
    _is_low_signal_tool_intent_summary,
    _plan_block,
    _plan_feedback_question,
    _result_snippet,
)
from app.services.agent_tools import ToolExecutionContext, build_tool_context, execute_agent_tool
from app.services.app_registry import app_registry_service
from app.services.chat_settings import chat_settings_service
from app.services.conversation_store import conversation_store
from app.services.llm import PromptSegment, chat_completion, ensure_prompt_metrics, render_prompt_segments, summary_request_overrides

AGENT_PROGRESS_SUMMARY_INTERVAL_STEPS = 3
AGENT_PROGRESS_SUMMARY_MAX_TOKENS = 192
AGENT_METADATA_CHECKPOINT_INTERVAL_SECONDS = 5
AGENT_METADATA_CHECKPOINT_STEP_INTERVAL = 10
AgentStreamCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


@dataclass(slots=True)
class AgentStepPresentation:
    thought: str
    progress_summary: str


@dataclass(slots=True)
class AgentMetadataCheckpointState:
    last_persisted_step: int
    last_persisted_at: datetime


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


def _parse_metadata_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _build_metadata_checkpoint_state(metadata: dict[str, Any]) -> AgentMetadataCheckpointState:
    return AgentMetadataCheckpointState(
        last_persisted_step=int(metadata.get("current_step", 0) or 0),
        last_persisted_at=_parse_metadata_timestamp(metadata.get("updated_at")) or _utc_now(),
    )


def _mark_metadata_checkpoint_persisted(
    metadata: dict[str, Any],
    checkpoint_state: AgentMetadataCheckpointState,
) -> None:
    checkpoint_state.last_persisted_step = int(metadata.get("current_step", 0) or 0)
    checkpoint_state.last_persisted_at = _parse_metadata_timestamp(metadata.get("updated_at")) or _utc_now()


def _should_checkpoint_run_metadata(
    metadata: dict[str, Any],
    checkpoint_state: AgentMetadataCheckpointState,
    *,
    force: bool = False,
) -> bool:
    if force:
        return True
    current_step = int(metadata.get("current_step", 0) or 0)
    if current_step >= checkpoint_state.last_persisted_step + AGENT_METADATA_CHECKPOINT_STEP_INTERVAL:
        return True
    elapsed_seconds = (_utc_now() - checkpoint_state.last_persisted_at).total_seconds()
    return elapsed_seconds >= AGENT_METADATA_CHECKPOINT_INTERVAL_SECONDS


async def _emit_agent_stream_update(
    callback: AgentStreamCallback | None,
    payload: dict[str, Any],
) -> None:
    if callback is None:
        return
    result = callback(payload)
    if asyncio.iscoroutine(result):
        await result


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


def _build_context(conversation_id: str, run_id: str, workspace_path: str, metadata: dict[str, Any]) -> ToolExecutionContext:
    generated_app_contract_override = metadata.get("generated_app_contract_override")
    if not isinstance(generated_app_contract_override, dict):
        generated_app_contract_override = None
    base_context = build_tool_context(
        conversation_id,
        workspace_path,
        generated_app_contract_override=generated_app_contract_override,
    )
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
        generated_app_contract_override=base_context.generated_app_contract_override,
    )


def _normalize_tool_arguments(metadata: dict[str, Any], tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(arguments)
    if tool_name == "file_read" and not str(normalized.get("path") or "").strip():
        inferred_path = _infer_app_file_read_path(metadata)
        if inferred_path:
            normalized["path"] = inferred_path
    return normalized


async def _update_run_metadata(run_id: str, metadata: dict[str, Any]) -> None:
    await conversation_store.update_run_metadata(run_id, _serialize_agent_metadata(metadata))


async def _maybe_checkpoint_run_metadata(
    run_id: str,
    metadata: dict[str, Any],
    checkpoint_state: AgentMetadataCheckpointState | None,
    *,
    force: bool = False,
) -> bool:
    if checkpoint_state is not None and not _should_checkpoint_run_metadata(metadata, checkpoint_state, force=force):
        return False
    metadata["updated_at"] = _iso_now()
    await _update_run_metadata(run_id, metadata)
    if checkpoint_state is not None:
        _mark_metadata_checkpoint_persisted(metadata, checkpoint_state)
    return True


async def _record_llm_metrics(
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
    *,
    conversation_id: str,
    run_id: str,
    metadata: dict[str, Any],
    action: dict[str, Any],
    step_number: int,
    thought: str,
    default_summary: str,
    chat_completion_fn: Callable[..., Awaitable[Any]] = chat_completion,
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
    response = await chat_completion_fn(
        [Message(role="system", content=system_prompt), Message(role="user", content=user_prompt)],
        model,
        temperature=0.1,
        max_tokens=AGENT_PROGRESS_SUMMARY_MAX_TOKENS,
        session_id=f"task:{conversation_id}:progress:{step_number}",
        prompt_segments=prompt_segments,
        request_overrides=summary_request_overrides(model),
    )
    prompt_metrics = ensure_prompt_metrics(
        messages=[Message(role="system", content=system_prompt), Message(role="user", content=user_prompt)],
        model=response.model,
        session_id=f"task:{conversation_id}:progress:{step_number}",
        usage=getattr(response, "usage", None),
        prompt_metrics=getattr(response, "prompt_metrics", None),
        rendered_messages=render_prompt_segments(prompt_segments, response.model),
    )
    metrics_event_id = await _record_llm_metrics(
        conversation_id=conversation_id,
        run_id=run_id,
        model=response.model,
        metrics=prompt_metrics,
        request_kind="task.agent.progress.summary",
    )
    await _record_llm_visible_output(
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
    *,
    conversation_id: str,
    run_id: str,
    metadata: dict[str, Any],
    action: dict[str, Any],
    step_number: int,
    chat_completion_fn: Callable[..., Awaitable[Any]] = chat_completion,
) -> AgentStepPresentation:
    thought = str(action.get("thought") or "").strip()
    default_summary = _fallback_progress_summary(action)
    should_generate_summary = str(action.get("kind") or "").strip() == "tool" and (
        not thought
        or _is_low_signal_tool_intent_summary(thought)
        or (step_number > 1 and step_number % AGENT_PROGRESS_SUMMARY_INTERVAL_STEPS == 0)
    )
    if should_generate_summary:
        progress_summary = await _generate_progress_summary(
            conversation_id=conversation_id,
            run_id=run_id,
            metadata=metadata,
            action=action,
            step_number=step_number,
            thought=thought or _fallback_action_thought(action),
            default_summary=default_summary,
            chat_completion_fn=chat_completion_fn,
        )
    else:
        progress_summary = default_summary
    return AgentStepPresentation(thought=thought, progress_summary=progress_summary)


async def _apply_app_initialize_result(
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
    *,
    conversation_id: str,
    run_id: str,
    metadata: dict[str, Any],
    question: str,
    kind: str = "question",
    current_action: str = "Waiting for user input",
    checkpoint_state: AgentMetadataCheckpointState | None = None,
) -> None:
    if not question:
        raise ValueError("Question action requires a question")
    pending_question = {
        "id": str(uuid4()),
        "kind": kind,
        "question": question,
        "asked_at": _iso_now(),
        "answer": None,
    }
    metadata["pending_question"] = pending_question
    metadata["task_status"] = "paused"
    metadata["current_action"] = current_action
    await _maybe_checkpoint_run_metadata(run_id, metadata, checkpoint_state, force=True)
    await conversation_store.update_run_status(run_id, status="paused", error=None, finished_at=None)
    await conversation_store.append_event(
        conversation_id,
        run_id=run_id,
        actor_kind="assistant",
        event_type="agent.question.asked",
        payload_json=pending_question,
    )


async def _handle_plan_action(
    *,
    conversation_id: str,
    run_id: str,
    metadata: dict[str, Any],
    action: dict[str, Any],
    checkpoint_state: AgentMetadataCheckpointState | None = None,
) -> dict[str, Any]:
    plan = dict(action.get("plan") or {})
    if not plan:
        raise ValueError("Plan action requires a validated plan payload")

    plan["approved"] = bool(metadata.get("skip_plan_feedback"))
    plan["feedback_skipped"] = bool(metadata.get("skip_plan_feedback"))
    plan["feedback"] = None
    plan["approved_at"] = _iso_now() if plan["approved"] else None
    metadata["plan"] = plan
    metadata["current_action"] = "Plan ready"
    await _maybe_checkpoint_run_metadata(run_id, metadata, checkpoint_state, force=True)

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
        await _maybe_checkpoint_run_metadata(run_id, metadata, checkpoint_state, force=True)
        await conversation_store.append_event(
            conversation_id,
            run_id=run_id,
            actor_kind="assistant",
            event_type="agent.plan.feedback.skipped",
            payload_json={"reason": "explicit_user_request"},
        )
        return metadata

    await _pause_for_question(
        conversation_id=conversation_id,
        run_id=run_id,
        metadata=metadata,
        question=_plan_feedback_question(plan),
        kind="plan_feedback",
        current_action="Waiting for plan feedback",
        checkpoint_state=checkpoint_state,
    )
    return metadata


async def _handle_tool_action(
    *,
    conversation_id: str,
    run_id: str,
    metadata: dict[str, Any],
    action: dict[str, Any],
    context: ToolExecutionContext,
    tool_executor: Callable[..., Awaitable[Any]] = execute_agent_tool,
    checkpoint_state: AgentMetadataCheckpointState | None = None,
) -> dict[str, Any]:
    tool_name = str(action.get("tool", "")).strip()
    arguments = action.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be a JSON object")
    arguments = _normalize_tool_arguments(metadata, tool_name, arguments)

    next_step = int(metadata.get("current_step", 0) or 0) + 1
    metadata["current_action"] = f"Running {tool_name}"
    metadata["updated_at"] = _iso_now()

    try:
        result = await tool_executor(context=context, tool=tool_name, arguments=arguments)
        if tool_name == "app_initialize":
            await _apply_app_initialize_result(
                conversation_id=conversation_id,
                run_id=run_id,
                metadata=metadata,
                context=context,
                tool_metadata=result.metadata,
            )
        snippet = _result_snippet(tool_name, result.output, result.metadata)
        history_entry = {
            "kind": "tool",
            "tool": tool_name,
            "arguments": arguments,
            "ok": True,
            "output": snippet,
        }
        history = list(metadata.get("history", []))
        history.append(history_entry)
        metadata["history"] = history
        metadata["current_step"] = next_step
        metadata["task_status"] = "running"
        metadata["current_action"] = f"Completed {tool_name}"
        metadata["updated_at"] = _iso_now()
        should_persist_metadata = checkpoint_state is None or tool_name == "app_initialize" or _should_checkpoint_run_metadata(metadata, checkpoint_state)
        persisted_tool_result = await conversation_store.record_agent_tool_result(
            conversation_id,
            run_id=run_id,
            step=next_step,
            tool_name=tool_name,
            arguments=arguments,
            ok=True,
            output=snippet,
            result_metadata=result.metadata,
            artifact_type=f"agent.tool.{tool_name}.output",
            artifact_content_text=result.output,
            artifact_content_json={"metadata": result.metadata},
            run_metadata_json=_serialize_agent_metadata(metadata) if should_persist_metadata else None,
        )
        if should_persist_metadata and checkpoint_state is not None:
            _mark_metadata_checkpoint_persisted(metadata, checkpoint_state)
        history_entry["artifact_id"] = persisted_tool_result.artifact.id if persisted_tool_result.artifact is not None else None
    except Exception as exc:
        error_text = str(exc)
        history_entry = {
            "kind": "tool",
            "tool": tool_name,
            "arguments": arguments,
            "ok": False,
            "output": error_text,
        }
        history = list(metadata.get("history", []))
        history.append(history_entry)
        metadata["history"] = history
        metadata["current_step"] = next_step
        metadata["task_status"] = "running"
        metadata["current_action"] = f"Tool failed: {tool_name}"
        metadata["updated_at"] = _iso_now()
        await conversation_store.record_agent_tool_result(
            conversation_id,
            run_id=run_id,
            step=next_step,
            tool_name=tool_name,
            arguments=arguments,
            ok=False,
            error=error_text,
            run_metadata_json=_serialize_agent_metadata(metadata),
        )
        if checkpoint_state is not None:
            _mark_metadata_checkpoint_persisted(metadata, checkpoint_state)
    return metadata


async def _complete_run(
    *,
    conversation_id: str,
    run_id: str,
    metadata: dict[str, Any],
    summary: str,
    result: str,
    checkpoint_state: AgentMetadataCheckpointState | None = None,
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
    metadata["updated_at"] = _iso_now()
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
    await _sync_bound_app_state(
        metadata=metadata,
        conversation_id=conversation_id,
        run_id=run_id,
        status="ready_for_test",
        verification_status="not_started",
        last_error=None,
    )
    await _maybe_checkpoint_run_metadata(run_id, metadata, checkpoint_state, force=True)
    await conversation_store.mark_run_completed(run_id)


async def _fail_run(
    conversation_id: str,
    run_id: str,
    metadata: dict[str, Any],
    error: str,
    checkpoint_state: AgentMetadataCheckpointState | None = None,
) -> None:
    metadata["task_status"] = "failed"
    metadata["error"] = error
    metadata["current_action"] = "Failed"
    await _maybe_checkpoint_run_metadata(run_id, metadata, checkpoint_state, force=True)
    await conversation_store.append_event(
        conversation_id,
        run_id=run_id,
        actor_kind="system",
        event_type="agent.run.failed",
        payload_json={"error": error},
    )
    await _sync_bound_app_state(
        metadata=metadata,
        conversation_id=conversation_id,
        run_id=run_id,
        status="failed",
        last_error=error,
    )
    await conversation_store.mark_run_failed(run_id, error)


async def _cancel_run(
    conversation_id: str,
    run_id: str,
    metadata: dict[str, Any],
    checkpoint_state: AgentMetadataCheckpointState | None = None,
) -> None:
    metadata["task_status"] = "cancelled"
    metadata["current_action"] = "Cancelled"
    await _maybe_checkpoint_run_metadata(run_id, metadata, checkpoint_state, force=True)
    await conversation_store.append_event(
        conversation_id,
        run_id=run_id,
        actor_kind="system",
        event_type="agent.run.failed",
        payload_json={"error": "Task cancelled by user"},
    )
    await _sync_bound_app_state(
        metadata=metadata,
        conversation_id=conversation_id,
        run_id=run_id,
        status="failed",
        last_error="Task cancelled by user",
    )
    await conversation_store.update_run_status(run_id, status="cancelled", error="Task cancelled by user", finished_at=_utc_now())