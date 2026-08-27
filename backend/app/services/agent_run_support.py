from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from time import perf_counter
from typing import Any, Awaitable, Callable
from uuid import uuid4

from app.config import settings
from app.models.chat import PromptMetrics
from app.services.agent_metadata import _build_run_summary, _infer_app_file_read_path, _serialize_agent_metadata
from app.services.agent_prompt_builder import _result_snippet, summarize_tool_start_content
from app.services.agent_tools import ToolExecutionContext, build_tool_context, execute_agent_tool
from app.services.app_registry import app_registry_service
from app.services.conversation_store import conversation_store
from app.services.conversation_timeline import (
    build_background_task_timeline_item,
    build_question_timeline_item,
    build_timeline_item,
    build_tool_activity_id,
    build_visible_text_timeline_item,
    clear_live_item,
    humanize_tool_name,
    summarize_tool_progress_content,
    set_live_item,
)
from app.services.llm import ensure_prompt_metrics
from app.services.voice import sanitize_voice_response

AGENT_METADATA_CHECKPOINT_INTERVAL_SECONDS = 5
AGENT_METADATA_CHECKPOINT_STEP_INTERVAL = 10
AgentStreamCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
timing_logger = logging.getLogger("app.agent_timing")


@dataclass(slots=True)
class AgentMetadataCheckpointState:
    last_persisted_step: int
    last_persisted_at: datetime


@dataclass
class AgentTimingScope:
    phase: str
    conversation_id: str
    run_id: str
    step: int | None = None
    details: dict[str, Any] = field(default_factory=dict)
    started_at: datetime = field(init=False)
    started_perf_counter: float = field(init=False, repr=False)

    async def __aenter__(self) -> AgentTimingScope:
        self.started_at = _utc_now()
        self.started_perf_counter = perf_counter()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        finished_at = _utc_now()
        payload: dict[str, Any] = {
            "phase": self.phase,
            "conversation_id": self.conversation_id,
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": round((perf_counter() - self.started_perf_counter) * 1000, 3),
            "status": "error" if exc is not None else "ok",
        }
        if self.step is not None:
            payload["step"] = self.step
        for key, value in self.details.items():
            if value is not None:
                payload[key] = value
        if exc is not None:
            payload["error"] = str(exc)
        timing_logger.info("agent_timing %s", json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str))
        return False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


def _timed_agent_phase(
    *,
    phase: str,
    conversation_id: str,
    run_id: str,
    step: int | None = None,
    **details: Any,
) -> AgentTimingScope:
    return AgentTimingScope(
        phase=phase,
        conversation_id=conversation_id,
        run_id=run_id,
        step=step,
        details=details,
    )


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


def _metadata_branch_key(metadata: dict[str, Any]) -> str:
    return str(metadata.get("branch_key") or "main")


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


async def _set_live_timeline_item(
    run_id: str,
    metadata: dict[str, Any],
    checkpoint_state: AgentMetadataCheckpointState | None,
    item: dict[str, Any],
    *,
    force: bool = False,
) -> bool:
    payload = dict(item)
    payload.setdefault("created_at", _iso_now())
    payload.setdefault("sequence", int(metadata.get("current_step", 0) or 0))
    payload.setdefault("status", "streaming")
    changed = set_live_item(metadata, payload)
    if not changed:
        return False
    metadata["updated_at"] = _iso_now()
    if checkpoint_state is not None:
        await _maybe_checkpoint_run_metadata(run_id, metadata, checkpoint_state, force=force)
    return True


async def _clear_live_timeline_item(
    run_id: str,
    metadata: dict[str, Any],
    checkpoint_state: AgentMetadataCheckpointState | None,
    *,
    item_id: str | None = None,
    force: bool = True,
) -> bool:
    changed = clear_live_item(metadata, item_id=item_id)
    if not changed:
        return False
    metadata["updated_at"] = _iso_now()
    if checkpoint_state is not None:
        await _maybe_checkpoint_run_metadata(run_id, metadata, checkpoint_state, force=force)
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
) -> bool:
    if not question:
        raise ValueError("Question action requires a question")
    transitioned = await conversation_store.transition_run_status(
        run_id,
        expected_statuses={"running"},
        status="paused",
        error=None,
        finished_at=None,
    )
    if transitioned is None:
        return False
    pending_question = {
        "id": str(uuid4()),
        "kind": kind,
        "question": question,
        "asked_at": _iso_now(),
        "answer": None,
    }
    global_task_id = str(metadata.get("global_task_id") or "").strip()
    if global_task_id:
        from app.services.task_service import task_service

        saved_question = await task_service.ask_question(
            global_task_id,
            question=question,
            idempotency_key=f"agent-question:{run_id}:{pending_question['id']}",
        )
        pending_question["global_task_question_id"] = saved_question.id
    metadata["pending_question"] = pending_question
    metadata["task_status"] = "paused"
    metadata["current_action"] = current_action
    await _maybe_checkpoint_run_metadata(run_id, metadata, checkpoint_state, force=True)
    question_payload = dict(pending_question)
    if metadata.get("voice_response") or metadata.get("execution_mode") == "voice_interactive":
        question_payload["voice_delivery"] = {
            "speak": True,
            "kind": "question",
            "speech_text": question,
            "importance": "high" if kind != "question" else "normal",
        }
    interactive_voice_question = bool(metadata.get("voice_response") or metadata.get("execution_mode") == "voice_interactive")
    if interactive_voice_question:
        question_event, _message = await conversation_store.append_event_with_assistant_message(
            conversation_id,
            run_id=run_id,
            event_type="agent.question.asked",
            content=question,
            importance="high" if kind != "question" else "normal",
            payload_json=question_payload,
            branch_key=_metadata_branch_key(metadata),
            timeline_item_payload=build_question_timeline_item(run_id=run_id, pending_question=pending_question),
        )
    else:
        question_event = await conversation_store.append_event(
            conversation_id,
            run_id=run_id,
            actor_kind="assistant",
            event_type="agent.question.asked",
            payload_json=question_payload,
            branch_key=_metadata_branch_key(metadata),
        )
        await conversation_store.append_timeline_item(
            conversation_id,
            run_id=run_id,
            item_payload=build_question_timeline_item(run_id=run_id, pending_question=pending_question),
            branch_key=_metadata_branch_key(metadata),
            parent_event_id=question_event.id,
        )
    current_run = await conversation_store.get_run(run_id)
    if current_run is not None and metadata.get("execution_mode") == "background":
        requires_input = await conversation_store.append_event_with_assistant_message(
            conversation_id,
            run_id=run_id,
            event_type="task.requires_input",
            content=question,
            importance="high",
            branch_key=_metadata_branch_key(metadata),
            payload_json={
                **pending_question,
                "task_run_id": run_id,
                "title": metadata.get("title") or "Background task",
                "summary": question,
                "speech_text": question,
                "notify_policy": metadata.get("notify_policy", "important"),
                "requested_notification": bool(metadata.get("requested_notification")),
                "status": "paused",
            },
        )
        from app.services.notifications import notify_event
        await notify_event(requires_input[0].id)
    await _clear_live_timeline_item(run_id, metadata, checkpoint_state)
    return True


async def _handle_tool_action(
    *,
    conversation_id: str,
    run_id: str,
    metadata: dict[str, Any],
    action: dict[str, Any],
    context: ToolExecutionContext,
    tool_executor: Callable[..., Awaitable[Any]] = execute_agent_tool,
    checkpoint_state: AgentMetadataCheckpointState | None = None,
    stream_callback: AgentStreamCallback | None = None,
) -> dict[str, Any]:
    tool_name = str(action.get("tool", "")).strip()
    tool_call_id = str(action.get("tool_call_id") or "").strip() or None
    arguments = action.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be a JSON object")
    arguments = _normalize_tool_arguments(metadata, tool_name, arguments)
    show_tool_timeline = metadata.get("execution_mode") != "background"

    next_step = int(metadata.get("current_step", 0) or 0) + 1
    activity_id = build_tool_activity_id(
        run_id=run_id,
        step=next_step,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
    )
    previous_action = str(metadata.get("current_action") or "").strip()
    tool_start_summary = summarize_tool_start_content(tool_name, arguments)
    if tool_start_summary and tool_start_summary != previous_action:
        await _emit_agent_stream_update(
            stream_callback,
            {
                "kind": "tool_started",
                "run_id": run_id,
                "step": next_step,
                "tool": tool_name,
                "activity_id": activity_id,
                "tool_call_id": tool_call_id,
                "content": tool_start_summary,
            },
        )
        if show_tool_timeline:
            await _set_live_timeline_item(
                run_id,
                metadata,
                checkpoint_state,
                build_timeline_item(
                    item_id=activity_id,
                    role="assistant",
                    kind="tool_call",
                    label="Tool call",
                    title=humanize_tool_name(tool_name),
                    content=tool_start_summary,
                    tone="info",
                    step=next_step,
                    tool_name=tool_name,
                    badges=[f"step {next_step}"],
                    status="streaming",
                ),
                force=True,
            )
    metadata["current_action"] = f"Running {tool_name}"
    metadata["updated_at"] = _iso_now()
    previous_progress_callback = context.progress_callback

    async def _handle_tool_progress(message: str) -> None:
        await _emit_agent_stream_update(
            stream_callback,
            {
                "kind": "tool_progress",
                "run_id": run_id,
                "step": next_step,
                "tool": tool_name,
                "activity_id": activity_id,
                "tool_call_id": tool_call_id,
                "content": message,
            },
        )
        if show_tool_timeline:
            await _set_live_timeline_item(
                run_id,
                metadata,
                checkpoint_state,
                build_timeline_item(
                    item_id=activity_id,
                    role="assistant",
                    kind="tool_call",
                    label="Tool call",
                    title=humanize_tool_name(tool_name),
                    content=message,
                    tone="info",
                    step=next_step,
                    tool_name=tool_name,
                    badges=[f"step {next_step}"],
                    status="streaming",
                ),
                force=False,
            )

    context.progress_callback = _handle_tool_progress

    try:
        async with _timed_agent_phase(
            phase="tool.execute",
            conversation_id=conversation_id,
            run_id=run_id,
            step=next_step,
            tool=tool_name,
        ) as timing:
            result = await tool_executor(context=context, tool=tool_name, arguments=arguments)
            timing.details["has_metadata"] = bool(result.metadata)
            timing.details["output_chars"] = len(result.output or "")
        if tool_name == "app_initialize":
            async with _timed_agent_phase(
                phase="tool.apply_app_initialize",
                conversation_id=conversation_id,
                run_id=run_id,
                step=next_step,
                tool=tool_name,
            ):
                await _apply_app_initialize_result(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    metadata=metadata,
                    context=context,
                    tool_metadata=result.metadata,
                )
        if tool_name == "app_verify" and isinstance(result.metadata, dict):
            metadata["app_verification"] = dict(result.metadata)
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
        async with _timed_agent_phase(
            phase="tool.persist",
            conversation_id=conversation_id,
            run_id=run_id,
            step=next_step,
            tool=tool_name,
            ok=True,
            persist_metadata=should_persist_metadata,
        ):
            persisted_tool_result = await conversation_store.record_agent_tool_result(
                conversation_id,
                run_id=run_id,
                step=next_step,
                tool_name=tool_name,
                arguments=arguments,
                ok=True,
                tool_call_id=tool_call_id,
                output=snippet,
                result_metadata=result.metadata,
                artifact_type=f"agent.tool.{tool_name}.output",
                artifact_content_text=result.output,
                artifact_content_json={"metadata": result.metadata},
                run_metadata_json=_serialize_agent_metadata(metadata) if should_persist_metadata else None,
                branch_key=_metadata_branch_key(metadata),
                emit_timeline_item=show_tool_timeline,
            )
        if should_persist_metadata and checkpoint_state is not None:
            _mark_metadata_checkpoint_persisted(metadata, checkpoint_state)
        history_entry["artifact_id"] = persisted_tool_result.artifact.id if persisted_tool_result.artifact is not None else None
        if not show_tool_timeline:
            await conversation_store.append_timeline_item(
                conversation_id,
                run_id=run_id,
                item_payload=build_background_task_timeline_item(
                    run_id=run_id,
                    task_id=str(metadata.get("global_task_id") or "").strip() or None,
                    title=str(metadata.get("title") or "Background task"),
                    goal=str(metadata.get("goal") or ""),
                    status="running",
                    active_step=summarize_tool_progress_content(tool_name, arguments, True),
                    updates=list(metadata.get("task_updates") or []),
                    notify_policy=str(metadata.get("notify_policy") or "important"),
                    requested_notification=bool(metadata.get("requested_notification")),
                ),
                branch_key=_metadata_branch_key(metadata),
                parent_event_id=persisted_tool_result.completed_event.id,
            )
        await _emit_agent_stream_update(
            stream_callback,
            {
                "kind": "tool_done",
                "run_id": run_id,
                "step": next_step,
                "tool": tool_name,
                "activity_id": activity_id,
                "tool_call_id": tool_call_id,
                "ok": True,
                "content": summarize_tool_progress_content(tool_name, arguments, True),
            },
        )
        if show_tool_timeline:
            await _clear_live_timeline_item(run_id, metadata, checkpoint_state, item_id=activity_id)
    except Exception as exc:
        error_text = str(exc)
        fatal = bool(getattr(exc, "fatal", False))
        history_entry = {
            "kind": "tool",
            "tool": tool_name,
            "arguments": arguments,
            "ok": False,
            "output": error_text,
            "fatal": fatal,
        }
        history = list(metadata.get("history", []))
        history.append(history_entry)
        metadata["history"] = history
        metadata["current_step"] = next_step
        metadata["task_status"] = "running"
        metadata["current_action"] = f"Tool failed: {tool_name}"
        metadata["updated_at"] = _iso_now()
        async with _timed_agent_phase(
            phase="tool.persist",
            conversation_id=conversation_id,
            run_id=run_id,
            step=next_step,
            tool=tool_name,
            ok=False,
            persist_metadata=True,
        ):
            persisted_tool_result = await conversation_store.record_agent_tool_result(
                conversation_id,
                run_id=run_id,
                step=next_step,
                tool_name=tool_name,
                arguments=arguments,
                ok=False,
                tool_call_id=tool_call_id,
                error=error_text,
                run_metadata_json=_serialize_agent_metadata(metadata),
                branch_key=_metadata_branch_key(metadata),
                emit_timeline_item=show_tool_timeline,
            )
        if checkpoint_state is not None:
            _mark_metadata_checkpoint_persisted(metadata, checkpoint_state)
        if not show_tool_timeline:
            await conversation_store.append_timeline_item(
                conversation_id,
                run_id=run_id,
                item_payload=build_background_task_timeline_item(
                    run_id=run_id,
                    task_id=str(metadata.get("global_task_id") or "").strip() or None,
                    title=str(metadata.get("title") or "Background task"),
                    goal=str(metadata.get("goal") or ""),
                    status="running",
                    active_step=summarize_tool_progress_content(tool_name, arguments, False),
                    updates=list(metadata.get("task_updates") or []),
                    notify_policy=str(metadata.get("notify_policy") or "important"),
                    requested_notification=bool(metadata.get("requested_notification")),
                ),
                branch_key=_metadata_branch_key(metadata),
                parent_event_id=persisted_tool_result.completed_event.id,
            )
        await _emit_agent_stream_update(
            stream_callback,
            {
                "kind": "tool_done",
                "run_id": run_id,
                "step": next_step,
                "tool": tool_name,
                "activity_id": activity_id,
                "tool_call_id": tool_call_id,
                "ok": False,
                "content": summarize_tool_progress_content(tool_name, arguments, False),
            },
        )
        if show_tool_timeline:
            await _clear_live_timeline_item(run_id, metadata, checkpoint_state, item_id=activity_id)
    finally:
        context.progress_callback = previous_progress_callback
    return metadata


async def _complete_run(
    *,
    conversation_id: str,
    run_id: str,
    metadata: dict[str, Any],
    summary: str,
    final_text: str,
    final_activity_id: str,
    checkpoint_state: AgentMetadataCheckpointState | None = None,
) -> bool:
    if metadata.get("voice_response"):
        final_text = sanitize_voice_response(final_text)
        summary = sanitize_voice_response(summary or final_text)
    final_summary = summary or final_text
    final_text = final_text or final_summary
    run_summary = _build_run_summary(metadata, final_summary)
    await _clear_live_timeline_item(run_id, metadata, checkpoint_state)
    metadata["final_summary"] = final_summary
    metadata["result"] = {"output": final_text}
    metadata["run_summary"] = run_summary
    metadata["task_status"] = "completed"
    metadata["pending_question"] = None
    metadata["current_action"] = "Completed"
    metadata["updated_at"] = _iso_now()
    importance = "high" if str(metadata.get("task_priority")) == "high" else "normal"
    completion_event = await conversation_store.complete_agent_run(
        conversation_id,
        run_id=run_id,
        metadata_json=_serialize_agent_metadata(metadata),
        final_text=final_text,
        final_summary=final_summary,
        run_summary=run_summary,
        final_activity_id=final_activity_id,
        background=metadata.get("execution_mode") == "background",
        importance=importance,
    )
    if completion_event is None:
        return False
    global_task_id = str(metadata.get("global_task_id") or "").strip()
    if global_task_id:
        from app.services.task_service import task_service

        await task_service.complete(
            global_task_id,
            summary=final_summary,
            idempotency_key=f"agent-complete:{run_id}",
        )
    if metadata.get("execution_mode") == "background":
        from app.services.notifications import notify_event

        await notify_event(completion_event.id)
    app_verification = metadata.get("app_verification") if isinstance(metadata.get("app_verification"), dict) else {}
    verification_passed = app_verification.get("verification_status") == "passed"
    await _sync_bound_app_state(
        metadata=metadata,
        conversation_id=conversation_id,
        run_id=run_id,
        status="verified" if verification_passed else "ready_for_test",
        verification_status="passed" if verification_passed else "not_started",
        last_error=None,
    )
    return True


async def _fail_run(
    conversation_id: str,
    run_id: str,
    metadata: dict[str, Any],
    error: str,
    checkpoint_state: AgentMetadataCheckpointState | None = None,
) -> None:
    await _clear_live_timeline_item(run_id, metadata, checkpoint_state)
    metadata["task_status"] = "failed"
    metadata["error"] = error
    metadata["current_action"] = "Failed"
    failure_text = "The local task brain failed on that turn. I did not switch to cloud automatically."
    failure_event = await conversation_store.fail_agent_run(
        conversation_id,
        run_id=run_id,
        metadata_json=_serialize_agent_metadata(metadata),
        error=error,
        voice_failure_text=failure_text,
    )
    if failure_event is not None and metadata.get("execution_mode") == "background":
        global_task_id = str(metadata.get("global_task_id") or "").strip()
        if global_task_id:
            from app.services.task_service import task_service

            await task_service.complete(
                global_task_id,
                status="failed",
                summary=error,
                idempotency_key=f"agent-failed:{run_id}",
            )
        from app.services.notifications import notify_event

        await notify_event(failure_event.id)


async def _cancel_run(
    conversation_id: str,
    run_id: str,
    metadata: dict[str, Any],
    checkpoint_state: AgentMetadataCheckpointState | None = None,
) -> None:
    metadata["task_status"] = "cancelled"
    metadata["current_action"] = "Cancelled"
    await _clear_live_timeline_item(run_id, metadata, checkpoint_state)
    await _maybe_checkpoint_run_metadata(run_id, metadata, checkpoint_state, force=True)
    # Cancellation can race with the HTTP control path and the supervisor.  The
    # lifecycle service owns the conditional state transition and its durable
    # projection so only the winner emits a terminal event/message.
    from app.services.run_lifecycle import run_lifecycle

    await run_lifecycle.cancel(run_id)
