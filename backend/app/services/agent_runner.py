"""Minimal multi-step agent loop backed by durable conversation runs."""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import Any

from app.models.chat import Message
from app.services.agent_tools import (
    ToolExecutionContext,
    execute_agent_tool,
    get_agent_tool_provider_definitions,
)
from app.services.agent_metadata import (
    _normalize_task_metadata,
)
from app.services.agent_runner_helpers import (
    persist_visible_text_event,
    restore_runtime_metadata_from_events,
)
from app.services.agent_prompt_builder import (
    NATIVE_AGENT_ASK_USER_TOOL,
    NATIVE_AGENT_FINALIZE_TOOL,
    _app_context_block,
    _build_final_action_payload,
    _build_question_action_payload,
    _build_tool_action_payload,
    _conversation_context_block,
    _history_block,
    _native_control_tool_definitions,
    _native_retry_feedback,
    _parse_native_tool_arguments,
)
from app.services.agent_run_support import (
    AgentStreamCallback,
    _build_metadata_checkpoint_state,
    _build_context,
    _clear_live_timeline_item,
    _cancel_run,
    _complete_run,
    _emit_agent_stream_update,
    _fail_run,
    _handle_tool_action,
    _maybe_checkpoint_run_metadata,
    _pause_for_question,
    _record_llm_metrics,
    _record_llm_visible_output,
    _set_live_timeline_item,
    _timed_agent_phase,
    _update_run_metadata,
)
from app.services.chat_settings import chat_settings_service
from app.services.conversation_store import conversation_store
from app.services.conversation_compaction import TASK_HISTORY_SUMMARY_PREFIX, build_agent_history_context
from app.services.conversation_timeline import (
    build_thought_timeline_item,
    build_timeline_item,
)
from app.services.llm import (
    PromptSegment,
    chat_completion,
    chat_completion_stream_response,
    ensure_prompt_metrics,
    native_agent_request_overrides,
    native_decision_attempt_max_tokens,
    render_prompt_segments,
    supports_native_tool_calls,
    use_streaming_native_decision,
)

logger = logging.getLogger(__name__)

MAX_AGENT_STEPS = 50

_NATIVE_CONTROL_TOOL_NAMES = {
    NATIVE_AGENT_ASK_USER_TOOL,
    NATIVE_AGENT_FINALIZE_TOOL,
}


def _text_completion_summary(final_text: str) -> str:
    first_line = next((line.strip() for line in final_text.splitlines() if line.strip()), "Task complete")
    return first_line[:240]


def _attached_app_slug(metadata: dict[str, Any]) -> str:
    payload = metadata.get("payload") if isinstance(metadata.get("payload"), dict) else {}
    app = payload.get("app") if isinstance(payload, dict) else None
    if not isinstance(app, dict):
        return ""
    return str(app.get("slug") or "").strip()


def _app_verification_required(metadata: dict[str, Any]) -> bool:
    if not _attached_app_slug(metadata):
        return False
    history = metadata.get("history") if isinstance(metadata.get("history"), list) else []
    last_mutation_index = -1
    last_successful_verification_index = -1
    for index, item in enumerate(history):
        if not isinstance(item, dict) or item.get("kind") != "tool":
            continue
        tool_name = str(item.get("tool") or "").strip()
        if tool_name in {"app_initialize", "file_write"} and item.get("ok") is not False:
            last_mutation_index = index
        if tool_name == "app_verify" and item.get("ok") is True:
            last_successful_verification_index = index
    return last_successful_verification_index < last_mutation_index


def _app_verification_failed_since_last_mutation(metadata: dict[str, Any]) -> bool:
    history = metadata.get("history") if isinstance(metadata.get("history"), list) else []
    last_mutation_index = -1
    last_failed_verification_index = -1
    for index, item in enumerate(history):
        if not isinstance(item, dict) or item.get("kind") != "tool":
            continue
        tool_name = str(item.get("tool") or "").strip()
        if tool_name in {"app_initialize", "file_write"} and item.get("ok") is not False:
            last_mutation_index = index
        if tool_name == "app_verify" and item.get("ok") is False:
            last_failed_verification_index = index
    return last_failed_verification_index > last_mutation_index


class AgentRunner:
    async def _request_native_turn(
        self,
        *,
        metadata: dict[str, Any],
        checkpoint_state,
        context: ToolExecutionContext,
        conversation_id: str,
        run_id: str,
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
            f"App context:\n{_app_context_block(metadata)}\n\n"
            f"Conversation workspace: {context.workspace_root.as_posix()}\n"
            f"Allowed read roots: {json.dumps([root.as_posix() for root in context.read_roots])}\n\n"
            f"Allowed write roots: {json.dumps([root.as_posix() for root in context.write_roots])}\n\n"
            f"Recent conversation context:\n{_conversation_context_block(recent_messages)}\n\n"
            f"Recent runtime transcript:\n{_history_block(rendered_history)}\n\n"
            "Important: The recent conversation context and runtime transcript were already shown to the user. "
            "Use them to continue from the latest state. Do not restate or re-summarize them unless you are surfacing a correction, a new finding, or the final result."
        )

        if not supports_native_tool_calls(model):
            raise ValueError(f"Task agent model does not support the native tool loop: {model}")

        system_prompt = (
            "You are the AI-Cockpit task agent. Continue the run using the native tools that materially advance the task. "
            f"Use {NATIVE_AGENT_FINALIZE_TOOL} when the task is complete. Use {NATIVE_AGENT_ASK_USER_TOOL} when user input is required. "
            "You may batch independent external tool calls. Do not combine a control tool with other calls. Prefer reading or searching before writing. Use shell only when the task genuinely needs an executable invocation. "
            "Never write outside the allowed write roots. The provided native tool schemas are authoritative. "
            "For generated app work, use the app_initialize tool before editing app route files if no app is attached yet. "
            "Generated apps are automatically checked with an isolated Next.js production build when you finalize. Do not run npm build yourself. If automatic verification fails, use the returned build error to fix the app and finalize again. App final text is only committed after verification passes, so phrase successful verification as completed, not as something that will happen later. "
            "When creating a new app, pass a clear title because that becomes the app's visible name. Pass app_slug only when you need an exact URL segment or when attaching an existing app. Never call app_initialize without title or app_slug. "
            "Any assistant text in this turn is shown directly to the user. Assistant text is optional. On turns that call external tools, normally emit no assistant text and call the tools directly. "
            "When finalizing, write the complete final user-facing response as assistant text and call task_finalize with only a short metadata summary. The assistant text is the final answer, so do not duplicate it in tool arguments. "
            "Do not announce what you are about to do or narrate the tool call with phrases such as 'I will', 'I'll', or 'Let me'. The UI already shows tool activity. Emit assistant text before completion only for a material finding, correction, or blocker that the user needs to know. It must add net-new information and must not restate the goal, approved plan, recent conversation context, or recent runtime transcript. "
            "Describe only work actually completed. Do not claim that a file was edited unless a successful file_write appears in the runtime transcript. app_initialize creates a scaffold; it does not prove a later edit occurred. "
            "Continue from the latest completed step or tool result instead of re-summarizing the task. "
            "If the latest successful tool result already satisfies the listed success criteria and no further external work is needed, call task_finalize immediately. For a clearly named new generated app request, do not call app_list unless the user explicitly asked to inspect existing apps first."
        )
        user_prompt = common_user_prompt + "\n\nContinue the task now from the latest completed step. Include assistant text only when it adds net-new information beyond the transcript already shown to the user."
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
        text_only_completion_candidate = ""
        for attempt in range(2):
            completion_call = chat_completion_stream_response if use_streaming_native_decision(model) else chat_completion
            stream_buffer: list[str] = []
            reasoning_buffer: list[str] = []

            async def _handle_content_delta(delta: str) -> None:
                stream_buffer.append(delta)
                await _emit_agent_stream_update(
                    stream_callback,
                    {
                        "kind": "text_delta",
                        "run_id": run_id,
                        "step": step_number,
                        "delta": delta,
                    },
                )
                current_text = "".join(stream_buffer).lstrip()
                if current_text.strip():
                    await _set_live_timeline_item(
                        run_id,
                        metadata,
                        checkpoint_state,
                        build_timeline_item(
                            item_id=f"assistant:{run_id}:{step_number}:text",
                            role="assistant",
                            kind="message",
                            content=current_text,
                            step=step_number,
                            status="streaming",
                        ),
                        force=False,
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
                current_thought = "".join(reasoning_buffer).lstrip()
                if current_thought.strip():
                    await _set_live_timeline_item(
                        run_id,
                        metadata,
                        checkpoint_state,
                        build_timeline_item(
                            item_id=f"thought:{run_id}:{step_number}",
                            role="assistant",
                            kind="thought",
                            label="Thinking",
                            title="Thinking…",
                            content=current_thought,
                            step=step_number,
                            status="streaming",
                        ),
                        force=False,
                    )

            completion_kwargs = {
                "temperature": 0.2,
                "max_tokens": native_decision_attempt_max_tokens(model, attempt),
                "tools": tools,
                "tool_choice": "auto",
                "session_id": session_id,
                "prompt_segments": prompt_segments,
                "request_overrides": native_agent_request_overrides(model),
            }
            completion_signature = inspect.signature(completion_call).parameters
            if "on_content_delta" in completion_signature:
                completion_kwargs["on_content_delta"] = _handle_content_delta
            if "on_reasoning_delta" in completion_signature:
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

            live_thought = response.reasoning.strip() or "".join(reasoning_buffer).strip()
            visible_text = response.content.strip() or "".join(stream_buffer).strip()

            if live_thought:
                await _emit_agent_stream_update(
                    stream_callback,
                    {
                        "kind": "thought_done",
                        "run_id": run_id,
                        "step": step_number,
                        "content": live_thought,
                    },
                )
            raw_tool_calls = [tool_call for tool_call in response.tool_calls if tool_call.function.name.strip()]
            tool_call_count = len(raw_tool_calls)
            is_finalize_turn = (
                tool_call_count == 1
                and raw_tool_calls[0].function.name.strip() == NATIVE_AGENT_FINALIZE_TOOL
            )
            if tool_call_count == 0:
                last_error = ValueError("Native tool decision must return at least one actionable tool call, got 0")
                if visible_text:
                    text_only_completion_candidate = visible_text
                if attempt == 0:
                    retry_feedback = _native_retry_feedback(last_error, tool_call_count=tool_call_count)
                    request_messages.append(Message(role="system", content=retry_feedback))
                    prompt_segments.append(PromptSegment(role="system", text=retry_feedback))
                    continue
                if text_only_completion_candidate:
                    return _build_final_action_payload(
                        thought=live_thought,
                        summary=_text_completion_summary(text_only_completion_candidate),
                        final_text=text_only_completion_candidate,
                    )
                raise last_error
            if visible_text and not is_finalize_turn:
                await _emit_agent_stream_update(
                    stream_callback,
                    {
                        "kind": "text_done",
                        "run_id": run_id,
                        "step": step_number,
                        "content": visible_text,
                    },
                )
                await persist_visible_text_event(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    step_number=step_number,
                    visible_text=visible_text,
                    metadata=metadata,
                    checkpoint_state=checkpoint_state,
                )
            if len(raw_tool_calls) != len(response.tool_calls):
                last_error = ValueError("Native tool decision returned a tool call with no name")
                if attempt == 0:
                    retry_feedback = _native_retry_feedback(last_error)
                    request_messages.append(Message(role="system", content=retry_feedback))
                    prompt_segments.append(PromptSegment(role="system", text=retry_feedback))
                    continue
                raise last_error

            tool_actions: list[dict[str, Any]] = []
            tool_names = [tool_call.function.name.strip() for tool_call in raw_tool_calls]
            has_control_tool = any(tool_name in _NATIVE_CONTROL_TOOL_NAMES for tool_name in tool_names)
            if len(raw_tool_calls) > 1 and has_control_tool:
                last_error = ValueError(
                    "Native tool decision cannot combine task_ask_user or task_finalize with other tool calls"
                )
                if attempt == 0:
                    retry_feedback = _native_retry_feedback(last_error, tool_call_count=len(raw_tool_calls))
                    request_messages.append(Message(role="system", content=retry_feedback))
                    prompt_segments.append(PromptSegment(role="system", text=retry_feedback))
                    continue
                raise last_error

            for index, tool_call in enumerate(raw_tool_calls, start=1):
                await conversation_store.append_event(
                    conversation_id,
                    run_id=run_id,
                    actor_kind="assistant",
                    event_type="agent.response.tool_call",
                    payload_json={
                        "tool_call_id": tool_call.id,
                        "tool": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                        "batch_index": index,
                        "batch_size": len(raw_tool_calls),
                        "finish_reason": response.finish_reason,
                        "content": response.content,
                        "reasoning": response.reasoning,
                    },
                    branch_key=str(metadata.get("branch_key") or "main"),
                )

            failing_tool_call = raw_tool_calls[0]
            try:
                for tool_call in raw_tool_calls:
                    failing_tool_call = tool_call
                    arguments = _parse_native_tool_arguments(
                        tool_call.function.arguments,
                        tool_name=tool_call.function.name,
                    )
                    if tool_call.function.name == NATIVE_AGENT_ASK_USER_TOOL:
                        payload = _build_question_action_payload(
                            thought=live_thought,
                            question=str(arguments.get("question") or "").strip(),
                        )
                        payload["visible_text"] = visible_text
                        return payload
                    if tool_call.function.name == NATIVE_AGENT_FINALIZE_TOOL:
                        payload = _build_final_action_payload(
                            thought=live_thought,
                            summary=str(arguments.get("summary") or "").strip(),
                            final_text=visible_text,
                        )
                        return payload
                    tool_actions.append(
                        _build_tool_action_payload(
                            thought=live_thought,
                            tool_name=tool_call.function.name,
                            arguments=arguments,
                            tool_call_id=tool_call.id,
                        )
                    )
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    retry_feedback = _native_retry_feedback(
                        exc,
                        tool_name=failing_tool_call.function.name,
                        raw_arguments=failing_tool_call.function.arguments,
                        finish_reason=response.finish_reason,
                    )
                    request_messages.append(Message(role="system", content=retry_feedback))
                    prompt_segments.append(PromptSegment(role="system", text=retry_feedback))
                    continue
                raise ValueError(str(exc)) from exc

            if not tool_actions:
                last_error = ValueError("Native tool decision did not produce any executable external tool calls")
                if attempt == 0:
                    retry_feedback = _native_retry_feedback(last_error)
                    request_messages.append(Message(role="system", content=retry_feedback))
                    prompt_segments.append(PromptSegment(role="system", text=retry_feedback))
                    continue
                raise last_error

            primary_tool_action = dict(tool_actions[0])
            batched_tools = [dict(action) for action in tool_actions[1:]]
            primary_tool_action["visible_text"] = visible_text
            if batched_tools:
                primary_tool_action["batched_tools"] = batched_tools
            return primary_tool_action

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
            metadata = await restore_runtime_metadata_from_events(
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
                        checkpoint_state=checkpoint_state,
                        context=context,
                        conversation_id=run.conversation_id,
                        run_id=run.id,
                        step_number=current_step + 1,
                        stream_callback=stream_callback,
                    )
                    turn_timing.details["action_kind"] = str(action.get("kind") or "").strip()
                    turn_timing.details["tool"] = str(action.get("tool") or "").strip() or None
                thought = str(action.get("thought") or "").strip()
                if thought:
                    thought_event = await conversation_store.append_event(
                        run.conversation_id,
                        run_id=run.id,
                        actor_kind="assistant",
                        event_type="agent.thought.summary",
                        payload_json={"step": current_step + 1, "thought": thought},
                        branch_key=str(metadata.get("branch_key") or "main"),
                    )
                    await conversation_store.append_timeline_item(
                        run.conversation_id,
                        run_id=run.id,
                        item_payload=build_thought_timeline_item(
                            run_id=run.id,
                            step=current_step + 1,
                            content=thought,
                        ),
                        branch_key=str(metadata.get("branch_key") or "main"),
                        parent_event_id=thought_event.id,
                    )

                action_kind = str(action.get("kind", "")).strip()
                if action_kind == "tool":
                    batched_tools = action.get("batched_tools") if isinstance(action.get("batched_tools"), list) else []
                    tool_actions = [
                        dict(action),
                        *[
                            dict(item)
                            for item in batched_tools
                            if isinstance(item, dict)
                        ],
                    ]
                    for tool_action in tool_actions:
                        if cancel_event is not None and cancel_event.is_set():
                            await _cancel_run(run.conversation_id, run.id, metadata, checkpoint_state=checkpoint_state)
                            return
                        if int(metadata.get("current_step", 0) or 0) >= MAX_AGENT_STEPS:
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
                        metadata = await _handle_tool_action(
                            conversation_id=run.conversation_id,
                            run_id=run.id,
                            metadata=metadata,
                            action=tool_action,
                            context=context,
                            tool_executor=execute_agent_tool,
                            checkpoint_state=checkpoint_state,
                            stream_callback=stream_callback,
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
                    app_slug = _attached_app_slug(metadata)
                    if app_slug and _app_verification_failed_since_last_mutation(metadata):
                        raise ValueError(
                            "Generated app verification failed, but the app was not changed before finalizing again"
                        )
                    if app_slug and _app_verification_required(metadata):
                        verification_step = int(metadata.get("current_step", 0) or 0) + 1
                        metadata = await _handle_tool_action(
                            conversation_id=run.conversation_id,
                            run_id=run.id,
                            metadata=metadata,
                            action={
                                "kind": "tool",
                                "tool": "app_verify",
                                "arguments": {"app_slug": app_slug},
                                "tool_call_id": f"auto_verify_{verification_step}",
                            },
                            context=context,
                            tool_executor=execute_agent_tool,
                            checkpoint_state=checkpoint_state,
                            stream_callback=stream_callback,
                        )
                        history = metadata.get("history") if isinstance(metadata.get("history"), list) else []
                        verification_entry = history[-1] if history and isinstance(history[-1], dict) else {}
                        if verification_entry.get("tool") != "app_verify" or verification_entry.get("ok") is not True:
                            if verification_entry.get("fatal") is True:
                                raise RuntimeError(str(verification_entry.get("output") or "App verification could not run"))
                            continue
                    await _complete_run(
                        conversation_id=run.conversation_id,
                        run_id=run.id,
                        metadata=metadata,
                        summary=str(action.get("summary", "")).strip(),
                        final_text=str(action.get("final_text", "")).strip(),
                        final_activity_id=f"assistant:{run.id}:{current_step + 1}:text",
                        checkpoint_state=checkpoint_state,
                    )
                    return

                raise ValueError(f"Unsupported agent action kind: {action_kind or '<missing>'}")
        except Exception as exc:
            logger.exception("Agent run %s failed", run_id)
            await _fail_run(run.conversation_id, run.id, metadata, str(exc), checkpoint_state=checkpoint_state)
            raise


agent_runner = AgentRunner()
