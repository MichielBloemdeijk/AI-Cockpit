"""Minimal multi-step agent loop backed by durable conversation runs."""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import datetime, timezone
import inspect
import json
import logging
from typing import Any

from app.config import settings
from app.models.chat import Message
from app.services.agent_tools import (
    ToolExecutionContext,
    execute_agent_tool,
    get_agent_tool_provider_definitions,
)
from app.services.agent_metadata import (
    _normalize_task_metadata,
)
from app.services.agent_prompt_builder import (
    NATIVE_AGENT_ASK_USER_TOOL,
    NATIVE_AGENT_FINALIZE_TOOL,
    NATIVE_AGENT_PLAN_TOOL,
    _app_context_block,
    _build_final_action_payload,
    _build_plan_action_payload,
    _build_question_action_payload,
    _build_tool_action_payload,
    _conversation_context_block,
    _history_block,
    _native_control_tool_definitions,
    _native_plan_tool_definition,
    _native_retry_feedback,
    _parse_native_tool_arguments,
    _plan_block,
    _validate_execution_plan_payload,
)
from app.services.agent_run_support import (
    AgentStreamCallback,
    _build_metadata_checkpoint_state,
    _build_context,
    _build_step_presentation,
    _cancel_run,
    _complete_run,
    _emit_agent_stream_update,
    _fail_run,
    _handle_plan_action,
    _handle_tool_action,
    _maybe_checkpoint_run_metadata,
    _pause_for_question,
    _record_llm_metrics,
    _record_llm_visible_output,
    _timed_agent_phase,
    _update_run_metadata,
)
from app.services.chat_settings import chat_settings_service
from app.services.conversation_store import conversation_store
from app.services.conversation_compaction import TASK_HISTORY_SUMMARY_PREFIX, build_agent_history_context
from app.services.llm import (
    PromptSegment,
    chat_completion,
    chat_completion_stream_response,
    ensure_prompt_metrics,
    native_agent_request_overrides,
    native_decision_attempt_max_tokens,
    native_plan_max_tokens,
    render_prompt_segments,
    supports_native_tool_calls,
    use_streaming_native_decision,
)

logger = logging.getLogger(__name__)

MAX_AGENT_STEPS = 50

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


async def _restore_runtime_metadata_from_events(
    *,
    conversation_id: str,
    run_id: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    events = await conversation_store.list_events_for_run(conversation_id, run_id, limit=5000)
    if not events:
        return metadata

    pending_tool_arguments: dict[tuple[int, str], deque[dict[str, Any]]] = defaultdict(deque)
    restored_tool_entries: list[dict[str, Any]] = []
    latest_completed_action: str | None = None
    latest_completed_step = _safe_int(metadata.get("current_step"), 0)
    started_event_recorded = bool(metadata.get("started_event_recorded"))

    for event in events:
        payload = event.payload_json if isinstance(event.payload_json, dict) else {}
        if event.event_type == "agent.run.started":
            started_event_recorded = True
            continue
        if event.event_type == "agent.tool.called":
            step = _safe_int(payload.get("step"), 0)
            tool_name = str(payload.get("tool") or "").strip()
            arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
            if step > 0 and tool_name:
                pending_tool_arguments[(step, tool_name)].append(dict(arguments))
            continue
        if event.event_type != "agent.tool.completed":
            continue

        step = _safe_int(payload.get("step"), 0)
        tool_name = str(payload.get("tool") or "").strip()
        if step <= 0 or not tool_name:
            continue
        arguments_queue = pending_tool_arguments[(step, tool_name)]
        arguments = arguments_queue.popleft() if arguments_queue else {}
        ok = bool(payload.get("ok"))
        entry = {
            "kind": "tool",
            "tool": tool_name,
            "arguments": arguments,
            "ok": ok,
            "output": payload.get("output") if ok else payload.get("error"),
        }
        artifact_id = payload.get("artifact_id")
        if artifact_id:
            entry["artifact_id"] = artifact_id
        restored_tool_entries.append(entry)
        if step >= latest_completed_step:
            latest_completed_step = step
            latest_completed_action = f"Completed {tool_name}" if ok else f"Tool failed: {tool_name}"

    existing_history = list(metadata.get("history", [])) if isinstance(metadata.get("history"), list) else []
    existing_tool_count = sum(1 for item in existing_history if isinstance(item, dict) and item.get("kind") == "tool")
    if len(restored_tool_entries) > existing_tool_count:
        metadata["history"] = existing_history + restored_tool_entries[existing_tool_count:]

    if latest_completed_step > _safe_int(metadata.get("current_step"), 0):
        metadata["current_step"] = latest_completed_step
        if latest_completed_action and not isinstance(metadata.get("pending_question"), dict):
            metadata["current_action"] = latest_completed_action

    if started_event_recorded:
        metadata["started_event_recorded"] = True
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
        return await _handle_plan_action(
            conversation_id=conversation_id,
            run_id=run_id,
            metadata=metadata,
            checkpoint_state=None,
            action=await self._request_native_turn(
                metadata=metadata,
                context=context,
                conversation_id=conversation_id,
                run_id=run_id,
                plan_required=True,
                step_number=0,
            ),
        )

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
        async with _timed_agent_phase(
            phase="native_turn.history_context",
            conversation_id=conversation_id,
            run_id=run_id,
            step=step_number,
            model=model,
            plan_required=plan_required,
        ):
            history_context = await build_agent_history_context(
                conversation_id=conversation_id,
                run_id=run_id,
                metadata=metadata,
                model=model,
                update_run_metadata=_update_run_metadata,
                record_llm_metrics=_record_llm_metrics,
                record_llm_visible_output=_record_llm_visible_output,
                chat_completion_fn=chat_completion,
            )
        history_summary = history_context.history_summary
        rendered_history = history_context.rendered_history

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
            summary_text = TASK_HISTORY_SUMMARY_PREFIX + history_summary
            request_messages.append(Message(role="system", content=summary_text))
            prompt_segments.append(PromptSegment(role="system", text=summary_text, cache_candidate=True, stable=True))
        request_messages.append(Message(role="user", content=user_prompt))
        prompt_segments.append(PromptSegment(role="user", text=user_prompt))

        last_error: Exception | None = None
        for attempt in range(2):
            completion_call = chat_completion if plan_required else (chat_completion_stream_response if use_streaming_native_decision(model) else chat_completion)
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
                "max_tokens": native_plan_max_tokens(model) if plan_required else native_decision_attempt_max_tokens(model, attempt),
                "tools": tools,
                "tool_choice": "required",
                "session_id": session_id,
                "prompt_segments": prompt_segments,
                "request_overrides": native_agent_request_overrides(model),
            }
            if not plan_required and "on_content_delta" in inspect.signature(completion_call).parameters:
                completion_kwargs["on_content_delta"] = _handle_content_delta
                completion_kwargs["on_reasoning_delta"] = _handle_reasoning_delta

            async with _timed_agent_phase(
                phase="native_turn.model_call",
                conversation_id=conversation_id,
                run_id=run_id,
                step=step_number,
                model=model,
                request_kind=request_kind,
                attempt=attempt + 1,
                streaming=completion_call is chat_completion_stream_response,
            ) as timing:
                response = await completion_call(request_messages, model, **completion_kwargs)
                timing.details["response_model"] = getattr(response, "model", model)
                timing.details["finish_reason"] = getattr(response, "finish_reason", None)
                timing.details["tool_call_count"] = len(getattr(response, "tool_calls", []))
            prompt_metrics = ensure_prompt_metrics(
                messages=request_messages,
                model=response.model,
                session_id=session_id,
                usage=getattr(response, "usage", None),
                prompt_metrics=getattr(response, "prompt_metrics", None),
                rendered_messages=render_prompt_segments(prompt_segments, response.model),
            )
            async with _timed_agent_phase(
                phase="native_turn.persist_output",
                conversation_id=conversation_id,
                run_id=run_id,
                step=step_number,
                model=response.model,
                request_kind=request_kind,
            ):
                metrics_event_id = await _record_llm_metrics(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    model=response.model,
                    metrics=prompt_metrics,
                    request_kind=request_kind,
                )
                await _record_llm_visible_output(
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
        async with _timed_agent_phase(
            phase="continue.restore_runtime_metadata",
            conversation_id=run.conversation_id,
            run_id=run.id,
        ):
            metadata = await _restore_runtime_metadata_from_events(
                conversation_id=run.conversation_id,
                run_id=run.id,
                metadata=metadata,
            )
        conversation = await conversation_store.get_conversation(run.conversation_id)
        if conversation is None or not conversation.workspace_path:
            raise ValueError(f"Conversation not found for task run: {run_id}")

        checkpoint_state = _build_metadata_checkpoint_state(metadata)
        async with _timed_agent_phase(
            phase="continue.build_context",
            conversation_id=run.conversation_id,
            run_id=run.id,
        ):
            context = _build_context(run.conversation_id, run.id, conversation.workspace_path, metadata)
        async with _timed_agent_phase(
            phase="continue.resume_setup",
            conversation_id=run.conversation_id,
            run_id=run.id,
        ):
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
            await _maybe_checkpoint_run_metadata(run.id, metadata, checkpoint_state, force=True)
            await conversation_store.update_run_status(run.id, status="running", error=None, finished_at=None)

        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    await _cancel_run(run.conversation_id, run.id, metadata, checkpoint_state=checkpoint_state)
                    return

                if not self._plan_exists(metadata):
                    metadata = await _handle_plan_action(
                        conversation_id=run.conversation_id,
                        run_id=run.id,
                        metadata=metadata,
                        checkpoint_state=checkpoint_state,
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
                    await _maybe_checkpoint_run_metadata(run.id, metadata, checkpoint_state, force=True)
                    await conversation_store.update_run_status(run.id, status="paused", error=None, finished_at=None)
                    return

                current_step = int(metadata.get("current_step", 0) or 0)
                if current_step >= MAX_AGENT_STEPS:
                    await _pause_for_question(
                        conversation_id=run.conversation_id,
                        run_id=run.id,
                        metadata=metadata,
                        question=(
                            f"I've reached {MAX_AGENT_STEPS} steps in this run. "
                            f"Reply with 'continue' if you want me to keep going for another {MAX_AGENT_STEPS} steps, "
                            "or send new instructions if you want to redirect the work."
                        ),
                        kind="continue_confirmation",
                        current_action="Waiting to continue past the step limit",
                        checkpoint_state=checkpoint_state,
                    )
                    return

                async with _timed_agent_phase(
                    phase="native_turn.total",
                    conversation_id=run.conversation_id,
                    run_id=run.id,
                    step=current_step + 1,
                ) as turn_timing:
                    action = await self._request_native_turn(
                        metadata=metadata,
                        context=context,
                        conversation_id=run.conversation_id,
                        run_id=run.id,
                        plan_required=False,
                        step_number=current_step + 1,
                        stream_callback=stream_callback,
                    )
                    turn_timing.details["action_kind"] = str(action.get("kind") or "").strip()
                    turn_timing.details["tool"] = str(action.get("tool") or "").strip() or None
                async with _timed_agent_phase(
                    phase="step.presentation",
                    conversation_id=run.conversation_id,
                    run_id=run.id,
                    step=current_step + 1,
                    action_kind=str(action.get("kind") or "").strip(),
                ):
                    presentation = await _build_step_presentation(
                        conversation_id=run.conversation_id,
                        run_id=run.id,
                        metadata=metadata,
                        action=action,
                        step_number=current_step + 1,
                        chat_completion_fn=chat_completion,
                    )
                if presentation.progress_summary:
                    metadata["current_action"] = presentation.progress_summary
                    await _maybe_checkpoint_run_metadata(run.id, metadata, checkpoint_state)
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
                    metadata = await _handle_tool_action(
                        conversation_id=run.conversation_id,
                        run_id=run.id,
                        metadata=metadata,
                        action=action,
                        context=context,
                        tool_executor=execute_agent_tool,
                        checkpoint_state=checkpoint_state,
                    )
                    continue

                if action_kind == "ask_user":
                    await _pause_for_question(
                        conversation_id=run.conversation_id,
                        run_id=run.id,
                        metadata=metadata,
                        question=str(action.get("question", "")).strip(),
                        checkpoint_state=checkpoint_state,
                    )
                    return

                if action_kind == "final":
                    await _complete_run(
                        conversation_id=run.conversation_id,
                        run_id=run.id,
                        metadata=metadata,
                        summary=str(action.get("summary", "")).strip(),
                        result=str(action.get("result", "")).strip(),
                        checkpoint_state=checkpoint_state,
                    )
                    return

                raise ValueError(f"Unsupported agent action kind: {action_kind or '<missing>'}")
        except Exception as exc:
            logger.exception("Agent run %s failed", run_id)
            await _fail_run(run.conversation_id, run.id, metadata, str(exc), checkpoint_state=checkpoint_state)
            raise


agent_runner = AgentRunner()