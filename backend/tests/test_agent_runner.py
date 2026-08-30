from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import pytest

from app.config import settings
import app.services.agent_runner as agent_runner_module
import app.services.agent_run_support as agent_run_support_module
import app.services.agent_tools.execution as agent_execution_module
import app.services.llm as llm_module
from app.models.chat import ModelResponse, ToolCall, ToolCallFunction
from tests.agent_runner_fixtures import (
    create_agent_run as _create_agent_run,
    finalize_response as _finalize_response,
    no_tool_response as _no_tool_response,
    tool_response as _tool_response,
)
from app.services.agent_prompt_builder import _native_control_tool_definitions
from app.services.agent_tools import ToolExecutionContext
from app.services.agent_tools import ToolExecutionResult
from app.services.agent_tools import build_tool_context
from app.services.agent_tools import run_file_read
from app.services.agent_tools import run_file_write
from app.services.agent_tools import run_python_execution
from app.services.agent_tools import run_shell_command
from app.services.agent_tools import run_workspace_search
from app.services.app_verifier import AppVerificationInfrastructureError
from app.services.conversation_store import conversation_store
from app.services.chat_orchestrator import goal_targets_app_context
from app.services.run_lifecycle import run_lifecycle
from app.db.repositories.conversations import ConversationRepository


def test_finalize_tool_requires_a_user_facing_result_and_internal_summary():
    definitions = {
        definition["function"]["name"]: definition["function"]
        for definition in _native_control_tool_definitions()
    }

    finalize_parameters = definitions["task_finalize"]["parameters"]
    assert set(definitions) == {"task_ask_user", "task_finalize"}
    assert finalize_parameters["required"] == ["summary", "final_text"]
    assert set(finalize_parameters["properties"]) == {"summary", "final_text"}


def test_native_tool_turns_have_enough_output_budget_for_source_files():
    assert llm_module.native_decision_max_tokens("openai/gpt-5.6-luna") == 8192
    assert llm_module.native_decision_attempt_max_tokens("openai/gpt-5.6-luna", 1) == 16_384


@pytest.mark.parametrize(
    ("goal", "expected"),
    [
        ("Write a short story about philosophy.", False),
        ("Can you hear me in a different app?", False),
        ("Fix the broken button in Legion App.", True),
        ("Update the frontend page layout.", True),
    ],
)
def test_recent_app_context_is_attached_only_to_app_work(goal: str, expected: bool) -> None:
    assert goal_targets_app_context(goal, app_title="Legion App", app_slug="legion-app") is expected


@pytest.mark.asyncio
async def test_cancellation_during_model_decision_prevents_delegation(monkeypatch) -> None:
    conversation, run = await _create_agent_run()
    cancel_event = asyncio.Event()

    async def _fake_stream(messages, model, **kwargs):
        cancel_event.set()
        return ModelResponse(
            model=model,
            content="I'll take care of that in the background.",
            usage=None,
            tool_calls=[
                ToolCall(
                    id="call_delegate",
                    function=ToolCallFunction(
                        name="delegate_background_task",
                        arguments=json.dumps({"goal": "Do not start this", "notify_policy": "important", "priority": "normal"}),
                    ),
                )
            ],
            finish_reason="tool_calls",
        )

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)

    await agent_runner_module.agent_runner.continue_run(run.id, cancel_event=cancel_event)

    refreshed = await conversation_store.get_run(run.id)
    assert refreshed is not None
    assert refreshed.status == "cancelled"
    assert len(await conversation_store.list_runs(conversation.id)) == 1
    events = await conversation_store.list_events_for_run(conversation.id, run.id)
    assert not any(event.event_type in {"task.queued", "agent.run.completed"} for event in events)


@pytest.mark.asyncio
async def test_cancelled_run_cannot_be_overwritten_by_late_completion() -> None:
    conversation, run = await _create_agent_run()
    assert (await run_lifecycle.cancel(run.id)).changed is True

    completed = await agent_run_support_module._complete_run(
        conversation_id=conversation.id,
        run_id=run.id,
        metadata=dict(run.metadata_json or {}),
        summary="Too late",
        final_text="This must not be delivered.",
        final_activity_id=f"assistant:{run.id}:late:text",
    )

    assert completed is False
    refreshed = await conversation_store.get_run(run.id)
    assert refreshed is not None
    assert refreshed.status == "cancelled"
    events = await conversation_store.list_events_for_run(conversation.id, run.id)
    assert not any(event.event_type == "agent.run.completed" for event in events)
    messages = await conversation_store.list_messages(conversation.id, final_only=True)
    assert not any(message.content == "This must not be delivered." for message in messages)


@pytest.mark.asyncio
async def test_completion_projection_failure_rolls_back_terminal_status(monkeypatch) -> None:
    conversation, run = await _create_agent_run()

    async def fail_artifact(self, **kwargs):
        raise RuntimeError("artifact write failed")

    monkeypatch.setattr(ConversationRepository, "create_artifact", fail_artifact)

    with pytest.raises(RuntimeError, match="artifact write failed"):
        await agent_run_support_module._complete_run(
            conversation_id=conversation.id,
            run_id=run.id,
            metadata=dict(run.metadata_json or {}),
            summary="Done",
            final_text="Durable result",
            final_activity_id=f"assistant:{run.id}:atomic:text",
        )

    refreshed = await conversation_store.get_run(run.id)
    assert refreshed is not None
    assert refreshed.status == "running"
    events = await conversation_store.list_events_for_run(conversation.id, run.id)
    assert not any(event.event_type in {"agent.run.completed", "conversation.assistant.message.completed"} for event in events)
    messages = await conversation_store.list_messages(conversation.id, final_only=True)
    assert not any(message.content == "Durable result" for message in messages)


@pytest.mark.asyncio
async def test_finalize_summary_is_used_when_provider_omits_assistant_text(monkeypatch):
    _, run = await _create_agent_run()

    async def _fake_stream(messages, model, **kwargs):
        return ModelResponse(
            model=model,
            content="",
            usage=None,
            tool_calls=[
                ToolCall(
                    id="call_finalize",
                    function=ToolCallFunction(
                        name="task_finalize",
                        arguments=json.dumps({"summary": "The requested inspection is complete."}),
                    ),
                )
            ],
            finish_reason="tool_calls",
        )

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)

    await agent_runner_module.agent_runner.continue_run(run.id)

    messages = await conversation_store.list_messages(run.conversation_id, final_only=True)
    assert messages[-1].content == "The requested inspection is complete."


@pytest.mark.asyncio
async def test_finalize_argument_is_used_when_provider_omits_assistant_text(monkeypatch):
    _, run = await _create_agent_run()

    async def _fake_stream(messages, model, **kwargs):
        return ModelResponse(
            model=model,
            content="",
            usage=None,
            tool_calls=[
                ToolCall(
                    id="call_finalize",
                    function=ToolCallFunction(
                        name="task_finalize",
                        arguments=json.dumps({
                            "summary": "Answered the arithmetic question.",
                            "final_text": "Two plus two is four.",
                        }),
                    ),
                )
            ],
            finish_reason="tool_calls",
        )

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)

    await agent_runner_module.agent_runner.continue_run(run.id)

    messages = await conversation_store.list_messages(run.conversation_id, final_only=True)
    assert messages[-1].content == "Two plus two is four."


def test_run_summary_counts_files_created_by_app_initialize():
    metadata = {
        "current_step": 1,
        "history": [
            {
                "kind": "tool",
                "tool": "app_initialize",
                "arguments": {"title": "Hello World"},
                "ok": True,
            }
        ],
        "payload": {
            "app": {
                "scaffolded_files": [
                    "frontend/generated-apps/hello-world/page.tsx",
                    "frontend/generated-apps/hello-world/styles.css",
                ]
            }
        },
    }

    summary = agent_run_support_module._build_run_summary(metadata, "Created the app.")

    assert summary["changed_files"] == [
        "frontend/generated-apps/hello-world/page.tsx",
        "frontend/generated-apps/hello-world/styles.css",
    ]


@pytest.mark.asyncio
async def test_continue_run_pauses_for_confirmation_after_fifty_steps():
    _, run = await _create_agent_run(current_step=50)

    await agent_runner_module.agent_runner.continue_run(run.id)

    stored_run = await conversation_store.get_run(run.id)
    assert stored_run is not None
    assert stored_run.status == "paused"
    metadata = dict(stored_run.metadata_json or {})
    pending_question = metadata.get("pending_question")
    assert isinstance(pending_question, dict)
    assert pending_question.get("kind") == "continue_confirmation"
    assert "50 steps" in str(pending_question.get("question") or "")

    events = await conversation_store.list_events_for_run(run.conversation_id, run.id)
    question_events = [event for event in events if event.event_type == "agent.question.asked"]
    assert len(question_events) == 1
    assert question_events[0].payload_json == pending_question


@pytest.mark.asyncio
async def test_continue_run_uses_streaming_native_decision_for_anthropic_and_separates_progress(monkeypatch):
    _, run = await _create_agent_run()
    stream_calls: list[str] = []
    tool_choices: list[object] = []
    stream_updates: list[dict[str, object]] = []
    responses = [
        {
            "deltas": ["Inspecting ", "frontend/lib/hooks.ts for the live status flow."],
            "response": _tool_response("file_read", {"path": "frontend/lib/hooks.ts"}),
        },
        {
            "deltas": [],
            "response": _finalize_response("Inspection complete."),
        },
    ]

    async def _fake_stream(messages, model, on_content_delta=None, **kwargs):
        if kwargs.get("tools") is None:
            if on_content_delta is not None:
                await on_content_delta("Inspecting ")
                await on_content_delta("hooks live status")
            return ModelResponse(
                model=model,
                content="Inspecting hooks live status",
                usage=None,
                tool_calls=[],
                finish_reason="stop",
            )
        stream_calls.append(model)
        tool_choices.append(kwargs.get("tool_choice"))
        current = responses.pop(0)
        for delta in current["deltas"]:
            if on_content_delta is not None:
                await on_content_delta(delta)
        return current["response"]

    async def _fake_execute(*, context, tool, arguments):
        return ToolExecutionResult(tool=tool, output="hooks inspected", metadata={})

    async def _stream_callback(payload):
        stream_updates.append(payload)

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)
    monkeypatch.setattr(agent_runner_module, "execute_agent_tool", _fake_execute)

    await agent_runner_module.agent_runner.continue_run(run.id, stream_callback=_stream_callback)

    events = await conversation_store.list_events_for_run(run.conversation_id, run.id)
    artifacts = await conversation_store.list_artifacts_for_run(run.conversation_id, run.id)
    thought_events = [event for event in events if event.event_type == "agent.thought.summary"]
    text_events = [event for event in events if event.event_type == "agent.text.output"]
    progress_events = [event for event in events if event.event_type == "agent.progress.summary"]
    visible_output_artifacts = [
        artifact for artifact in artifacts
        if artifact.artifact_type == "llm.response.visible_output"
        and isinstance(artifact.content_json, dict)
        and artifact.content_json.get("request_kind") == "task.agent.native_decision"
    ]

    assert len(stream_calls) == 2
    assert tool_choices == ["auto", "auto"]
    assert thought_events == []
    assert any(
        event.payload_json == {"step": 1, "text": "Inspecting frontend/lib/hooks.ts for the live status flow."}
        for event in text_events
    )
    assert not any(event.payload_json.get("step") == 1 for event in progress_events)
    assert any(update == {
        "kind": "text_delta",
        "run_id": run.id,
        "step": 1,
        "delta": "Inspecting ",
    } for update in stream_updates)
    assert any(update == {
        "kind": "text_done",
        "run_id": run.id,
        "step": 1,
        "content": "Inspecting frontend/lib/hooks.ts for the live status flow.",
    } for update in stream_updates)
    assert not any(update.get("kind") == "progress" and update.get("step") == 1 for update in stream_updates)
    assert any(
        update == {
            "kind": "tool_done",
            "run_id": run.id,
            "step": 1,
            "tool": "file_read",
            "activity_id": f"tool:{run.id}:call_tool",
            "tool_call_id": "call_tool",
            "ok": True,
            "content": "Read lib/hooks.ts",
        }
        for update in stream_updates
    )
    assert any(
        artifact.content_json == {
            "request_kind": "task.agent.native_decision",
            "model": "anthropic/claude-sonnet-4-5",
            "content": "",
            "reasoning": "",
            "reasoning_details": [],
            "error": None,
            "finish_reason": "tool_calls",
            "streamed_visible_deltas": ["Inspecting ", "frontend/lib/hooks.ts for the live status flow."],
            "tool_calls": [
                {
                    "id": "call_tool",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": '{"path": "frontend/lib/hooks.ts"}',
                    },
                }
            ],
        }
        for artifact in visible_output_artifacts
    )


@pytest.mark.asyncio
async def test_continue_run_promotes_visible_text_to_the_final_message(monkeypatch):
    _, run = await _create_agent_run()

    async def _fake_stream(messages, model, on_content_delta=None, **kwargs):
        if on_content_delta is not None:
            await on_content_delta("All done")
        return ModelResponse(
            model=model,
            content="All done",
            usage=None,
            tool_calls=[
                ToolCall(
                    id="call_finalize",
                    function=ToolCallFunction(
                        name="task_finalize",
                        arguments=json.dumps({"summary": "All done"}),
                    ),
                )
            ],
            finish_reason="tool_calls",
        )

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)

    await agent_runner_module.agent_runner.continue_run(run.id)

    events = await conversation_store.list_events_for_run(run.conversation_id, run.id)
    text_events = [event for event in events if event.event_type == "agent.text.output"]
    timeline_events = [event for event in events if event.event_type == "chat.timeline.item.committed"]

    assert text_events == []
    assert any(
        (event.payload_json or {}).get("item_id") == f"assistant:{run.id}:1:text"
        and (event.payload_json or {}).get("kind") == "message"
        and (event.payload_json or {}).get("content") == "All done"
        for event in timeline_events
    )
    assert not any(
        event.event_type == "agent.progress.summary"
        and (event.payload_json or {}).get("summary") == "Preparing final response"
        for event in events
    )
    assert any(
        (event.payload_json or {}).get("role") == "assistant"
        and (event.payload_json or {}).get("kind") == "message"
        and (event.payload_json or {}).get("content") == "All done"
        for event in timeline_events
    )


@pytest.mark.asyncio
async def test_continue_run_does_not_call_a_secondary_progress_model(monkeypatch):
    _, run = await _create_agent_run(model="anthropic/claude-haiku-4.5")
    responses = [
        {"response": _tool_response("file_read", {"path": "frontend/lib/hooks.ts"})},
        {"response": _finalize_response("Inspection complete.")},
    ]
    summary_calls = 0

    async def _fake_stream(messages, model, on_content_delta=None, **kwargs):
        return responses.pop(0)["response"]

    async def _fake_summary(messages, model, **kwargs):
        nonlocal summary_calls
        summary_calls += 1
        return ModelResponse(
            model=model,
            content="Inspecting hooks status flow",
            usage=None,
            tool_calls=[],
            finish_reason="stop",
        )

    async def _fake_execute(*, context, tool, arguments):
        return ToolExecutionResult(tool=tool, output="hooks inspected", metadata={})

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)
    monkeypatch.setattr(agent_runner_module, "chat_completion", _fake_summary)
    monkeypatch.setattr(agent_runner_module, "execute_agent_tool", _fake_execute)

    await agent_runner_module.agent_runner.continue_run(run.id)

    events = await conversation_store.list_events_for_run(run.conversation_id, run.id)
    artifacts = await conversation_store.list_artifacts_for_run(run.conversation_id, run.id)
    thought_events = [event for event in events if event.event_type == "agent.thought.summary"]
    progress_events = [event for event in events if event.event_type == "agent.progress.summary"]
    metric_events = [
        event for event in events
        if event.event_type == "llm.request.completed"
        and isinstance(event.payload_json, dict)
        and event.payload_json.get("request_kind") == "task.agent.progress.summary"
    ]
    visible_summary_artifacts = [
        artifact for artifact in artifacts
        if artifact.artifact_type == "llm.response.visible_output"
        and isinstance(artifact.content_json, dict)
        and artifact.content_json.get("request_kind") == "task.agent.progress.summary"
    ]

    assert summary_calls == 0
    assert thought_events == []
    assert not any(event.payload_json.get("step") == 1 for event in progress_events)
    assert metric_events == []
    assert visible_summary_artifacts == []


@pytest.mark.asyncio
async def test_continue_run_uses_hidden_reasoning_for_thoughts(monkeypatch):
    _, run = await _create_agent_run(model="anthropic/claude-haiku-4.5")
    responses = [
        {
            "reasoning_deltas": ["Checking the current hooks flow.", " Then I will inspect the file."],
            "response": _tool_response(
                "file_read",
                {"path": "frontend/lib/hooks.ts"},
                content="I will read the hooks file next.",
                reasoning="Checking the current hooks flow. Then I will inspect the file.",
            ),
        },
        {"reasoning_deltas": [], "response": _finalize_response("Inspection complete.")},
    ]
    stream_updates: list[dict[str, object]] = []

    async def _fake_stream(messages, model, on_content_delta=None, on_reasoning_delta=None, **kwargs):
        if kwargs.get("tools") is None:
            return ModelResponse(
                model=model,
                content="Reading lib/hooks.ts",
                usage=None,
                tool_calls=[],
                finish_reason="stop",
            )
        current = responses.pop(0)
        for delta in current["reasoning_deltas"]:
            if on_reasoning_delta is not None:
                await on_reasoning_delta(delta)
        return current["response"]

    async def _fake_execute(*, context, tool, arguments):
        return ToolExecutionResult(tool=tool, output="hooks inspected", metadata={})

    async def _stream_callback(payload):
        stream_updates.append(payload)

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)
    monkeypatch.setattr(agent_runner_module, "execute_agent_tool", _fake_execute)

    await agent_runner_module.agent_runner.continue_run(run.id, stream_callback=_stream_callback)

    events = await conversation_store.list_events_for_run(run.conversation_id, run.id)
    artifacts = await conversation_store.list_artifacts_for_run(run.conversation_id, run.id)
    thought_events = [event for event in events if event.event_type == "agent.thought.summary"]
    visible_output_artifacts = [
        artifact for artifact in artifacts
        if artifact.artifact_type == "llm.response.visible_output"
        and isinstance(artifact.content_json, dict)
        and artifact.content_json.get("request_kind") == "task.agent.native_decision"
    ]

    assert any(
        update == {
            "kind": "thought_delta",
            "run_id": run.id,
            "step": 1,
            "delta": "Checking the current hooks flow.",
        }
        for update in stream_updates
    )
    assert any(
        update == {
            "kind": "text_done",
            "run_id": run.id,
            "step": 1,
            "content": "I will read the hooks file next.",
        }
        for update in stream_updates
    )
    assert any(
        event.payload_json == {
            "step": 1,
            "thought": "Checking the current hooks flow. Then I will inspect the file.",
        }
        for event in thought_events
    )
    assert any(
        update == {
            "kind": "tool_done",
            "run_id": run.id,
            "step": 1,
            "tool": "file_read",
            "activity_id": f"tool:{run.id}:call_tool",
            "tool_call_id": "call_tool",
            "ok": True,
            "content": "Read lib/hooks.ts",
        }
        for update in stream_updates
    )
    assert any(
        artifact.content_json == {
            "request_kind": "task.agent.native_decision",
            "model": "anthropic/claude-sonnet-4-5",
            "content": "I will read the hooks file next.",
            "reasoning": "Checking the current hooks flow. Then I will inspect the file.",
            "reasoning_details": [],
            "error": None,
            "finish_reason": "tool_calls",
            "streamed_visible_deltas": [],
            "tool_calls": [
                {
                    "id": "call_tool",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": '{"path": "frontend/lib/hooks.ts"}',
                    },
                }
            ],
        }
        for artifact in visible_output_artifacts
    )


@pytest.mark.asyncio
async def test_continue_run_retries_when_native_decision_returns_zero_tool_calls(monkeypatch):
    _, run = await _create_agent_run()
    captured_messages: list[list[object]] = []
    responses = [
        _no_tool_response("I am checking one more detail before I act."),
        _tool_response("file_read", {"path": "frontend/lib/hooks.ts"}),
        _finalize_response("Inspection complete."),
    ]

    async def _fake_stream(messages, model, **kwargs):
        if kwargs.get("tools") is not None:
            captured_messages.append(list(messages))
            return responses.pop(0)
        return _no_tool_response("Inspecting the requested file")

    async def _fake_execute(*, context, tool, arguments):
        return ToolExecutionResult(tool=tool, output="hooks inspected", metadata={})

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)
    monkeypatch.setattr(agent_runner_module, "execute_agent_tool", _fake_execute)

    await agent_runner_module.agent_runner.continue_run(run.id)

    events = await conversation_store.list_events_for_run(run.conversation_id, run.id)
    text_events = [event for event in events if event.event_type == "agent.text.output"]

    assert len(captured_messages) >= 2
    retry_feedback = str(captured_messages[1][-1].content)
    assert "produced zero tool calls" in retry_feedback
    assert "An actionable native tool call is required now" in retry_feedback
    assert not any(event.payload_json.get("text") == "I am checking one more detail before I act." for event in text_events)


@pytest.mark.asyncio
async def test_continue_run_accepts_text_only_completion_after_control_call_retry(monkeypatch):
    _, run = await _create_agent_run()
    responses = [
        _no_tool_response("I am checking one more detail before I act."),
        _no_tool_response(""),
    ]
    async def _fake_stream(messages, model, **kwargs):
        if kwargs.get("tools") is not None:
            return responses.pop(0)
        return _no_tool_response("Inspecting the requested file")

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)

    await agent_runner_module.agent_runner.continue_run(run.id)

    stored_run = await conversation_store.get_run(run.id)
    messages = await conversation_store.list_messages(run.conversation_id, final_only=True)
    events = await conversation_store.list_events_for_run(run.conversation_id, run.id)
    text_events = [event for event in events if event.event_type == "agent.text.output"]
    failure_events = [event for event in events if event.event_type == "agent.run.failed"]

    assert stored_run is not None
    assert stored_run.status == "completed"
    assert messages[-1].content == "I am checking one more detail before I act."
    assert text_events == []
    assert failure_events == []


async def _attach_demo_app(run) -> None:
    metadata = dict(run.metadata_json or {})
    app_payload = {
        "app_id": "missing-test-app-id",
        "slug": "demo-app",
        "title": "Demo App",
        "route_path": "/apps/demo-app",
        "frontend_root": "frontend/generated-apps/demo-app",
        "frontend_entry_path": "frontend/generated-apps/demo-app/page.tsx",
        "frontend_layout_path": "frontend/generated-apps/demo-app/layout.tsx",
        "manifest_path": "frontend/generated-apps/demo-app/cockpit-app.json",
        "asset_root": "frontend/public/apps/demo-app",
        "allowed_write_roots": ["frontend/generated-apps/demo-app", "frontend/public/apps/demo-app"],
    }
    metadata["payload"] = {"app": app_payload}
    metadata["app_context"] = {"mode": "app_builder", "app": app_payload}
    metadata["history"] = [
        {
            "kind": "tool",
            "tool": "file_write",
            "arguments": {"path": "frontend/generated-apps/demo-app/page.tsx", "content": "broken"},
            "ok": True,
            "output": "Updated page.tsx",
        }
    ]
    await conversation_store.update_run_metadata(run.id, metadata)


@pytest.mark.asyncio
async def test_generated_app_completion_verifies_and_returns_build_errors_to_the_agent(monkeypatch):
    _, run = await _create_agent_run()
    await _attach_demo_app(run)

    responses = [
        _finalize_response("Demo App is complete."),
        _tool_response(
            "file_write",
            {"path": "frontend/generated-apps/demo-app/page.tsx", "content": "fixed"},
        ),
        _finalize_response("Demo App is fixed and verified."),
    ]
    executed_tools: list[str] = []
    captured_prompts: list[str] = []
    verification_attempts = 0

    async def _fake_stream(messages, model, **kwargs):
        captured_prompts.append(str(messages[-1].content))
        return responses.pop(0)

    async def _fake_execute(*, context, tool, arguments):
        nonlocal verification_attempts
        executed_tools.append(tool)
        if tool == "app_verify":
            verification_attempts += 1
            if verification_attempts == 1:
                raise ValueError("TypeScript error: demo-app/page.tsx:7")
            return ToolExecutionResult(
                tool=tool,
                output="Verified demo-app",
                metadata={
                    "app_id": "missing-test-app-id",
                    "slug": "demo-app",
                    "verification_status": "passed",
                    "status": "verified",
                },
            )
        return ToolExecutionResult(tool=tool, output="Updated demo app", metadata={})

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)
    monkeypatch.setattr(agent_runner_module, "execute_agent_tool", _fake_execute)

    await agent_runner_module.agent_runner.continue_run(run.id)

    stored_run = await conversation_store.get_run(run.id)
    messages = await conversation_store.list_messages(run.conversation_id, final_only=True)
    assert stored_run is not None
    assert stored_run.status == "completed"
    assert messages[-1].content == "Demo App is fixed and verified."
    assert executed_tools == ["app_verify", "file_write", "app_verify"]
    assert any("TypeScript error: demo-app/page.tsx:7" in prompt for prompt in captured_prompts[1:])
    assert (stored_run.metadata_json or {}).get("app_verification", {}).get("verification_status") == "passed"


@pytest.mark.asyncio
async def test_generated_app_verification_infrastructure_failure_stops_the_run_once(monkeypatch):
    _, run = await _create_agent_run()
    await _attach_demo_app(run)
    executed_tools: list[str] = []

    async def _fake_stream(messages, model, **kwargs):
        return _finalize_response("Demo App is complete.")

    async def _fake_execute(*, context, tool, arguments):
        executed_tools.append(tool)
        raise AppVerificationInfrastructureError("Generated app verification could not start npm")

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)
    monkeypatch.setattr(agent_runner_module, "execute_agent_tool", _fake_execute)

    with pytest.raises(RuntimeError, match="could not start npm"):
        await agent_runner_module.agent_runner.continue_run(run.id)

    stored_run = await conversation_store.get_run(run.id)
    assert stored_run is not None
    assert stored_run.status == "failed"
    assert executed_tools == ["app_verify"]


@pytest.mark.asyncio
async def test_generated_app_cannot_repeat_finalize_after_build_failure_without_a_change(monkeypatch):
    _, run = await _create_agent_run()
    await _attach_demo_app(run)
    responses = [
        _finalize_response("Demo App is complete."),
        _finalize_response("Demo App is complete now."),
    ]
    executed_tools: list[str] = []

    async def _fake_stream(messages, model, **kwargs):
        return responses.pop(0)

    async def _fake_execute(*, context, tool, arguments):
        executed_tools.append(tool)
        raise ValueError("TypeScript error: demo-app/page.tsx:7")

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)
    monkeypatch.setattr(agent_runner_module, "execute_agent_tool", _fake_execute)

    with pytest.raises(ValueError, match="was not changed"):
        await agent_runner_module.agent_runner.continue_run(run.id)

    stored_run = await conversation_store.get_run(run.id)
    assert stored_run is not None
    assert stored_run.status == "failed"
    assert executed_tools == ["app_verify"]


@pytest.mark.asyncio
async def test_continue_run_executes_multiple_external_tool_calls_in_one_native_turn(monkeypatch):
    _, run = await _create_agent_run(model="deepseek/deepseek-v4-flash")
    executed_tools: list[tuple[str, dict[str, object]]] = []
    responses = [
        ModelResponse(
            model="deepseek/deepseek-v4-flash",
            content="",
            usage=None,
            tool_calls=[
                ToolCall(
                    id="call_readme",
                    function=ToolCallFunction(name="file_read", arguments=json.dumps({"path": "README.md"})),
                ),
                ToolCall(
                    id="call_architecture",
                    function=ToolCallFunction(name="file_read", arguments=json.dumps({"path": "docs/ARCHITECTURE.md"})),
                ),
            ],
            finish_reason="tool_calls",
        ),
        _finalize_response("Two facts recovered."),
    ]

    async def _fake_stream(messages, model, **kwargs):
        if kwargs.get("tools") is not None:
            return responses.pop(0)
        return _no_tool_response("Reading the requested docs")

    async def _fake_execute(*, context, tool, arguments):
        executed_tools.append((tool, dict(arguments)))
        return ToolExecutionResult(tool=tool, output="file contents", metadata={})

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)
    monkeypatch.setattr(agent_runner_module, "execute_agent_tool", _fake_execute)

    await agent_runner_module.agent_runner.continue_run(run.id)

    stored_run = await conversation_store.get_run(run.id)
    events = await conversation_store.list_events_for_run(run.conversation_id, run.id)
    tool_call_events = [event for event in events if event.event_type == "agent.response.tool_call"]
    tool_timeline_events = [
        event
        for event in events
        if event.event_type == "chat.timeline.item.committed"
        and event.payload_json.get("kind") == "tool_result"
    ]

    assert stored_run is not None
    assert stored_run.status == "completed"
    assert executed_tools == [
        ("file_read", {"path": "README.md"}),
        ("file_read", {"path": "docs/ARCHITECTURE.md"}),
    ]
    assert any(
        event.payload_json == {
            "tool_call_id": "call_readme",
            "tool": "file_read",
            "arguments": '{"path": "README.md"}',
            "batch_index": 1,
            "batch_size": 2,
            "finish_reason": "tool_calls",
            "content": "",
            "reasoning": "",
        }
        for event in tool_call_events
    )
    assert any(
        event.payload_json == {
            "tool_call_id": "call_architecture",
            "tool": "file_read",
            "arguments": '{"path": "docs/ARCHITECTURE.md"}',
            "batch_index": 2,
            "batch_size": 2,
            "finish_reason": "tool_calls",
            "content": "",
            "reasoning": "",
        }
        for event in tool_call_events
    )
    assert [event.payload_json["item_id"] for event in tool_timeline_events] == [
        f"tool:{run.id}:call_readme",
        f"tool:{run.id}:call_architecture",
    ]


@pytest.mark.asyncio
async def test_batched_tools_respect_the_step_limit_between_calls(monkeypatch):
    _, run = await _create_agent_run(current_step=49)
    executed_paths: list[str] = []

    async def _fake_stream(messages, model, **kwargs):
        return ModelResponse(
            model=model,
            content="",
            usage=None,
            tool_calls=[
                ToolCall(
                    id="call_one",
                    function=ToolCallFunction(name="file_read", arguments=json.dumps({"path": "README.md"})),
                ),
                ToolCall(
                    id="call_two",
                    function=ToolCallFunction(name="file_read", arguments=json.dumps({"path": "docs/ARCHITECTURE.md"})),
                ),
            ],
            finish_reason="tool_calls",
        )

    async def _fake_execute(*, context, tool, arguments):
        executed_paths.append(str(arguments["path"]))
        return ToolExecutionResult(tool=tool, output="file contents", metadata={})

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)
    monkeypatch.setattr(agent_runner_module, "execute_agent_tool", _fake_execute)

    await agent_runner_module.agent_runner.continue_run(run.id)

    stored_run = await conversation_store.get_run(run.id)
    assert stored_run is not None
    assert stored_run.status == "paused"
    assert executed_paths == ["README.md"]


@pytest.mark.asyncio
async def test_batched_tool_retry_identifies_the_call_with_invalid_arguments(monkeypatch):
    _, run = await _create_agent_run()
    captured_messages: list[list[object]] = []
    responses = [
        ModelResponse(
            model="anthropic/claude-sonnet-4-5",
            content="",
            usage=None,
            tool_calls=[
                ToolCall(
                    id="call_valid",
                    function=ToolCallFunction(name="file_read", arguments=json.dumps({"path": "README.md"})),
                ),
                ToolCall(
                    id="call_invalid",
                    function=ToolCallFunction(name="workspace_search", arguments="{"),
                ),
            ],
            finish_reason="tool_calls",
        ),
        _finalize_response("Recovered from the malformed batch."),
    ]

    async def _fake_stream(messages, model, **kwargs):
        captured_messages.append(list(messages))
        return responses.pop(0)

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)

    await agent_runner_module.agent_runner.continue_run(run.id)

    retry_feedback = str(captured_messages[1][-1].content)
    assert "workspace_search" in retry_feedback
    assert "file_read" not in retry_feedback


@pytest.mark.asyncio
async def test_continue_run_uses_tool_lifecycle_without_a_secondary_progress_model(monkeypatch):
    _, run = await _create_agent_run(model="anthropic/claude-haiku-4.5", silent_tool_call_streak=2)
    responses = [
        {"response": _tool_response("file_write", {"path": "frontend/app/page.tsx"})},
        {"response": _finalize_response("Updated the page.")},
    ]
    stream_updates: list[dict[str, object]] = []

    async def _fake_stream(messages, model, on_content_delta=None, **kwargs):
        if kwargs.get("tools") is None:
            if on_content_delta is not None:
                await on_content_delta("Editing")
                await on_content_delta(" the landing page")
            return ModelResponse(
                model=model,
                content="Editing the landing page",
                usage=None,
                tool_calls=[],
                finish_reason="stop",
            )
        return responses.pop(0)["response"]

    async def _fake_execute(*, context, tool, arguments):
        return ToolExecutionResult(tool=tool, output="page updated", metadata={})

    async def _stream_callback(payload):
        stream_updates.append(payload)

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)
    monkeypatch.setattr(agent_runner_module, "execute_agent_tool", _fake_execute)

    await agent_runner_module.agent_runner.continue_run(run.id, stream_callback=_stream_callback)

    artifacts = await conversation_store.list_artifacts_for_run(run.conversation_id, run.id)
    visible_summary_artifacts = [
        artifact for artifact in artifacts
        if artifact.artifact_type == "llm.response.visible_output"
        and isinstance(artifact.content_json, dict)
        and artifact.content_json.get("request_kind") == "task.agent.progress.summary"
    ]

    assert not any(update.get("kind") in {"summary_delta", "summary_done"} for update in stream_updates)
    assert any(
        update == {
            "kind": "tool_done",
            "run_id": run.id,
            "step": 1,
            "tool": "file_write",
            "activity_id": f"tool:{run.id}:call_tool",
            "tool_call_id": "call_tool",
            "ok": True,
            "content": "Updated app/page.tsx",
        }
        for update in stream_updates
    )
    assert visible_summary_artifacts == []


@pytest.mark.asyncio
async def test_continue_run_native_decision_prompt_requests_delta_only_updates(monkeypatch):
    _, run = await _create_agent_run()
    captured_messages: list[list[object]] = []
    responses = [
        _tool_response("file_read", {"path": "frontend/lib/hooks.ts"}, content="Checking the target file now."),
        _finalize_response("Inspection complete."),
    ]

    async def _fake_stream(messages, model, **kwargs):
        if kwargs.get("tools") is not None:
            captured_messages.append(messages)
            return responses.pop(0)
        return ModelResponse(
            model=model,
            content="Inspecting the requested file",
            usage=None,
            tool_calls=[],
            finish_reason="stop",
        )

    async def _fake_execute(*, context, tool, arguments):
        return ToolExecutionResult(tool=tool, output="hooks inspected", metadata={})

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)
    monkeypatch.setattr(agent_runner_module, "execute_agent_tool", _fake_execute)

    await agent_runner_module.agent_runner.continue_run(run.id)

    assert captured_messages
    first_messages = captured_messages[0]
    system_prompt = first_messages[0].content
    user_prompt = first_messages[-1].content
    assert "On turns that call external tools, normally emit no assistant text" in system_prompt
    assert "You may batch independent external tool calls" in system_prompt
    assert "at most one" not in system_prompt
    assert "Do not announce what you are about to do or narrate the tool call" in system_prompt
    assert "must add net-new information" in system_prompt
    assert "must not restate the goal, approved plan, recent conversation context, or recent runtime transcript" in system_prompt
    assert "were already shown to the user" in user_prompt
    assert "Continue the task now from the latest completed step" in user_prompt


@pytest.mark.asyncio
async def test_continue_run_starts_tool_activity_only_when_execution_begins(monkeypatch):
    _, run = await _create_agent_run(model="anthropic/claude-haiku-4.5")
    responses = [
        {
            "response": _tool_response(
                "file_write",
                {
                    "path": "frontend/generated-apps/demo/page.tsx",
                    "content": "export default function DemoPage() {\n  return <main>Hello world</main>;\n}",
                },
            )
        },
        {"response": _finalize_response("Updated the generated app.")},
    ]
    stream_updates: list[dict[str, object]] = []

    async def _fake_stream(messages, model, on_tool_call_delta=None, **kwargs):
        if kwargs.get("tools") is None:
            return ModelResponse(
                model=model,
                content="",
                usage=None,
                tool_calls=[],
                finish_reason="stop",
            )
        current = responses.pop(0)
        tool_call = current["response"].tool_calls[0]
        if on_tool_call_delta is not None and tool_call.function.name == "file_write":
            await on_tool_call_delta(
                ToolCall(
                    id=tool_call.id,
                    function=ToolCallFunction(
                        name="file_write",
                        arguments='{"path":"frontend/generated-apps/demo/page.tsx","content":"export default function DemoPage() {',
                    ),
                )
            )
            await on_tool_call_delta(
                ToolCall(
                    id=tool_call.id,
                    function=ToolCallFunction(
                        name="file_write",
                        arguments='{"path":"frontend/generated-apps/demo/page.tsx","content":"export default function DemoPage() {\\n  return <main>Hello world</main>;\\n}',
                    ),
                )
            )
        return current["response"]

    async def _fake_execute(*, context, tool, arguments):
        return ToolExecutionResult(tool=tool, output="page updated", metadata={})

    async def _stream_callback(payload):
        stream_updates.append(payload)

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)
    monkeypatch.setattr(agent_runner_module, "execute_agent_tool", _fake_execute)

    await agent_runner_module.agent_runner.continue_run(run.id, stream_callback=_stream_callback)

    assert not any("Preparing" in str(update.get("content") or "") for update in stream_updates)
    assert any(
        update.get("kind") == "tool_started"
        and update.get("tool") == "file_write"
        and update.get("content") == "Writing demo/page.tsx (3 lines)"
        for update in stream_updates
    )
    assert any(
        update.get("kind") == "tool_done"
        and update.get("run_id") == run.id
        and update.get("step") == 1
        and update.get("tool") == "file_write"
        and update.get("ok") is True
        and "Updated demo/page.tsx" in str(update.get("content") or "")
        for update in stream_updates
    )


@pytest.mark.asyncio
async def test_continue_run_persists_live_item_snapshot_during_tool_execution(monkeypatch):
    _, run = await _create_agent_run(model="anthropic/claude-haiku-4.5")
    responses = [
        {"response": _tool_response("file_read", {"path": "frontend/lib/hooks.ts"})},
        {"response": _finalize_response("Done")},
    ]
    tool_started = asyncio.Event()
    allow_finish = asyncio.Event()

    async def _fake_stream(messages, model, **kwargs):
        if kwargs.get("tools") is None:
            return ModelResponse(model=model, content="", usage=None, tool_calls=[], finish_reason="stop")
        return responses.pop(0)["response"]

    async def _fake_execute(*, context, tool, arguments):
        tool_started.set()
        await allow_finish.wait()
        return ToolExecutionResult(tool=tool, output="hooks inspected", metadata={})

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)
    monkeypatch.setattr(agent_runner_module, "execute_agent_tool", _fake_execute)

    task = asyncio.create_task(agent_runner_module.agent_runner.continue_run(run.id))
    await tool_started.wait()

    mid_run = await conversation_store.get_run(run.id)
    assert mid_run is not None
    live_item = dict((mid_run.metadata_json or {}).get("live_item") or {})
    assert live_item["item_id"] == f"tool:{run.id}:call_tool"
    assert live_item["kind"] == "tool_call"
    assert live_item["content"] == "Reading lib/hooks.ts"

    allow_finish.set()
    await task

    completed_run = await conversation_store.get_run(run.id)
    assert completed_run is not None
    assert (completed_run.metadata_json or {}).get("live_item") is None


@pytest.mark.asyncio
async def test_filesystem_tools_emit_live_progress_callbacks():
    progress_updates: list[str] = []

    async def _capture_progress(message: str) -> None:
        progress_updates.append(message)

    with TemporaryDirectory(dir=settings.backend_root.parent) as temp_dir:
        workspace_root = Path(temp_dir)
        source_path = workspace_root / "notes.txt"
        source_path.write_text("alpha\nlive progress marker\n", encoding="utf-8")
        context = build_tool_context("conv-progress", str(workspace_root))
        context.read_roots = [workspace_root]
        context.write_roots = [workspace_root]
        context.progress_callback = _capture_progress

        await run_file_read("notes.txt", context=context)
        await run_file_write("draft.txt", "hello", context=context)
        await run_workspace_search("live progress marker", context=context)

    assert any(update == "Resolving notes.txt" for update in progress_updates)
    assert any(update.startswith("Reading ") and update.endswith("notes.txt") for update in progress_updates)
    assert any(update.startswith("Loaded 27 bytes from ") and update.endswith("notes.txt") for update in progress_updates)
    assert any(update == "Resolving destination draft.txt" for update in progress_updates)
    assert any(update.startswith("Writing ") and update.endswith("draft.txt (1 lines)") for update in progress_updates)
    assert any(update.startswith("Updated ") and update.endswith("draft.txt (1 lines)") for update in progress_updates)
    assert any(update == "Searching for 'live progress marker'" for update in progress_updates)
    assert any(update.startswith("Scanning ") for update in progress_updates)
    assert any(update.startswith("Found a match in ") for update in progress_updates)


@pytest.mark.asyncio
async def test_execution_tools_emit_live_progress_callbacks():
    progress_updates: list[str] = []

    async def _capture_progress(message: str) -> None:
        progress_updates.append(message)

    with TemporaryDirectory(dir=settings.backend_root.parent) as temp_dir:
        workspace_root = Path(temp_dir)
        context = build_tool_context("conv-progress", str(workspace_root))
        context.read_roots = [workspace_root]
        context.write_roots = [workspace_root]
        context.progress_callback = _capture_progress

        await run_python_execution("print('live tool output')", context=context)

        if sys.platform.startswith("win"):
            await run_shell_command("cmd /c echo live shell output", context=context)
        else:
            await run_shell_command("echo live shell output", context=context)

    assert any(update == "Starting Python process" for update in progress_updates)
    assert any(update == "stdout: live tool output" for update in progress_updates)
    assert any(update == "Python run completed" for update in progress_updates)
    assert any(update.startswith("Starting ") for update in progress_updates)
    assert any(update == "stdout: live shell output" for update in progress_updates)
    assert any(update == "Command finished with exit code 0" for update in progress_updates)


@pytest.mark.asyncio
async def test_shell_tool_resolves_windows_command_wrappers(monkeypatch):
    observed_args: list[str] = []

    async def _fake_process(*, args, cwd, timeout_seconds, context):
        observed_args.extend(args)
        return 0, "10.0.0", ""

    monkeypatch.setattr(agent_execution_module.shutil, "which", lambda executable: "C:/node/npm.cmd")
    monkeypatch.setattr(agent_execution_module, "_run_subprocess_with_live_progress", _fake_process)

    with TemporaryDirectory(dir=settings.backend_root.parent) as temp_dir:
        context = build_tool_context("conv-shell-resolution", temp_dir)
        result = await run_shell_command("npm --version", context=context)

    assert observed_args == ["C:/node/npm.cmd", "--version"]
    assert result.metadata["returncode"] == 0


@pytest.mark.asyncio
async def test_continue_run_writes_agent_metadata_aliases(monkeypatch):
    _, run = await _create_agent_run(model="anthropic/claude-haiku-4.5")
    responses = [
        {"response": _tool_response("file_read", {"path": "frontend/lib/hooks.ts"})},
        {"response": _finalize_response("Inspection complete.")},
    ]

    async def _fake_stream(messages, model, on_content_delta=None, **kwargs):
        return responses.pop(0)["response"]

    async def _fake_summary(messages, model, **kwargs):
        return ModelResponse(
            model=model,
            content="Inspecting hooks status flow",
            usage=None,
            tool_calls=[],
            finish_reason="stop",
        )

    async def _fake_execute(*, context, tool, arguments):
        return ToolExecutionResult(tool=tool, output="hooks inspected", metadata={})

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)
    monkeypatch.setattr(agent_runner_module, "chat_completion", _fake_summary)
    monkeypatch.setattr(agent_runner_module, "execute_agent_tool", _fake_execute)

    await agent_runner_module.agent_runner.continue_run(run.id)

    stored_run = await conversation_store.get_run(run.id)
    assert stored_run is not None
    metadata = dict(stored_run.metadata_json or {})

    assert metadata["run_kind"] == "agent"
    assert metadata["agent_status"] == "completed"
    assert metadata["active_step"] == "Completed"
    assert metadata["active_step_index"] == 1
    assert isinstance(metadata["summary"], dict)
    assert metadata["summary"]["summary"] == "Inspection complete."
    assert "task_type" not in metadata
    assert "task_status" not in metadata
    assert "current_action" not in metadata
    assert "current_step" not in metadata
    assert "run_summary" not in metadata


@pytest.mark.asyncio
async def test_handle_tool_action_persists_metadata_once_per_tool(monkeypatch):
    conversation, run = await _create_agent_run()
    metadata = dict(run.metadata_json or {})
    context = build_tool_context(conversation.id, ".", run_id=run.id)
    metadata_updates: list[dict[str, object]] = []
    original_update_run_metadata = agent_run_support_module._update_run_metadata

    async def _counting_update_run_metadata(run_id: str, next_metadata: dict[str, object]) -> None:
        metadata_updates.append(dict(next_metadata))
        await original_update_run_metadata(run_id, next_metadata)

    async def _fake_execute(*, context, tool, arguments):
        return ToolExecutionResult(tool=tool, output="hooks inspected", metadata={"path": arguments["path"]})

    monkeypatch.setattr(agent_run_support_module, "_update_run_metadata", _counting_update_run_metadata)

    next_metadata = await agent_run_support_module._handle_tool_action(
        conversation_id=conversation.id,
        run_id=run.id,
        metadata=metadata,
        action={"tool": "file_read", "arguments": {"path": "frontend/lib/hooks.ts"}},
        context=context,
        tool_executor=_fake_execute,
    )

    events = await conversation_store.list_events_for_run(conversation.id, run.id)
    artifacts = await conversation_store.list_artifacts_for_run(conversation.id, run.id)

    stored_run = await conversation_store.get_run(run.id)

    assert len(metadata_updates) == 0
    assert next_metadata["current_step"] == 1
    assert next_metadata["current_action"] == "Completed file_read"
    assert [event.event_type for event in events] == [
        "agent.tool.called",
        "agent.tool.completed",
        "chat.timeline.item.committed",
    ]
    assert len(artifacts) == 1
    assert stored_run is not None
    assert stored_run.metadata_json["active_step_index"] == 1
    assert stored_run.metadata_json["active_step"] == "Completed file_read"


@pytest.mark.asyncio
async def test_handle_tool_action_clears_live_activity_checkpoint_after_completion(monkeypatch):
    conversation, run = await _create_agent_run()
    metadata = dict(run.metadata_json or {})
    context = build_tool_context(conversation.id, ".", run_id=run.id)
    checkpoint_state = agent_run_support_module._build_metadata_checkpoint_state(metadata)

    async def _fake_execute(*, context, tool, arguments):
        return ToolExecutionResult(tool=tool, output="hooks inspected", metadata={"path": arguments["path"]})

    next_metadata = await agent_run_support_module._handle_tool_action(
        conversation_id=conversation.id,
        run_id=run.id,
        metadata=metadata,
        action={"tool": "file_read", "arguments": {"path": "frontend/lib/hooks.ts"}},
        context=context,
        tool_executor=_fake_execute,
        checkpoint_state=checkpoint_state,
    )

    stored_run = await conversation_store.get_run(run.id)
    events = await conversation_store.list_events_for_run(conversation.id, run.id)

    assert next_metadata["current_step"] == 1
    assert stored_run is not None
    assert stored_run.metadata_json.get("current_step", 0) == 0
    assert len(stored_run.metadata_json["history"]) == 1
    assert stored_run.metadata_json.get("live_item") is None
    assert [event.event_type for event in events] == [
        "agent.tool.called",
        "agent.tool.completed",
        "chat.timeline.item.committed",
    ]


@pytest.mark.asyncio
async def test_restore_runtime_metadata_from_events_recovers_deferred_tools():
    conversation, run = await _create_agent_run(current_step=0)
    metadata = dict(run.metadata_json or {})

    await conversation_store.record_agent_tool_result(
        conversation.id,
        run_id=run.id,
        step=1,
        tool_name="file_read",
        arguments={"path": "frontend/lib/hooks.ts"},
        ok=True,
        output="hooks inspected",
        result_metadata={"path": "frontend/lib/hooks.ts"},
        artifact_type="agent.tool.file_read.output",
        artifact_content_text="hooks inspected",
        artifact_content_json={"metadata": {"path": "frontend/lib/hooks.ts"}},
        run_metadata_json=None,
    )
    await conversation_store.record_agent_tool_result(
        conversation.id,
        run_id=run.id,
        step=2,
        tool_name="workspace_search",
        arguments={"query": "active_step"},
        ok=True,
        output="found matches",
        result_metadata={"query": "active_step"},
        artifact_type="agent.tool.workspace_search.output",
        artifact_content_text="found matches",
        artifact_content_json={"metadata": {"query": "active_step"}},
        run_metadata_json=None,
    )

    restored = await agent_runner_module.restore_runtime_metadata_from_events(
        conversation_id=conversation.id,
        run_id=run.id,
        metadata=metadata,
    )

    tool_history = [item for item in restored["history"] if item.get("kind") == "tool"]
    assert restored["current_step"] == 2
    assert restored["current_action"] == "Completed workspace_search"
    assert not restored.get("started_event_recorded")
    assert len(tool_history) == 2
    assert tool_history[0]["arguments"] == {"path": "frontend/lib/hooks.ts"}
    assert tool_history[1]["arguments"] == {"query": "active_step"}


@pytest.mark.asyncio
async def test_continue_run_logs_structured_phase_timings(monkeypatch):
    _, run = await _create_agent_run(model="anthropic/claude-haiku-4.5")
    responses = [
        {"response": _tool_response("file_read", {"path": "frontend/lib/hooks.ts"})},
        {"response": _finalize_response("Inspection complete.")},
    ]
    payloads: list[dict[str, object]] = []

    async def _fake_stream(messages, model, on_content_delta=None, **kwargs):
        return responses.pop(0)["response"]

    async def _fake_summary(messages, model, **kwargs):
        return ModelResponse(
            model=model,
            content="Inspecting hooks status flow",
            usage=None,
            tool_calls=[],
            finish_reason="stop",
        )

    async def _fake_execute(*, context, tool, arguments):
        return ToolExecutionResult(tool=tool, output="hooks inspected", metadata={"path": arguments["path"]})

    def _capture_timing_log(message, *args, **kwargs):
        if message != "agent_timing %s" or not args:
            return
        payloads.append(json.loads(str(args[0])))

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)
    monkeypatch.setattr(agent_runner_module, "chat_completion", _fake_summary)
    monkeypatch.setattr(agent_runner_module, "execute_agent_tool", _fake_execute)
    monkeypatch.setattr(agent_run_support_module.timing_logger, "info", _capture_timing_log)

    await agent_runner_module.agent_runner.continue_run(run.id)

    assert any(payload.get("phase") == "continue.restore_runtime_metadata" for payload in payloads)
    assert any(
        payload.get("phase") == "native_turn.model_call"
        and payload.get("step") == 1
        and payload.get("request_kind") == "task.agent.native_decision"
        for payload in payloads
    )
    assert not any(payload.get("phase") == "progress_summary.model_call" for payload in payloads)
    assert any(
        payload.get("phase") == "tool.execute"
        and payload.get("step") == 1
        and payload.get("tool") == "file_read"
        for payload in payloads
    )
    assert any(
        payload.get("phase") == "tool.persist"
        and payload.get("step") == 1
        and payload.get("tool") == "file_read"
        and payload.get("ok") is True
        for payload in payloads
    )
    assert all("started_at" in payload and "finished_at" in payload for payload in payloads)
    assert all(isinstance(payload.get("duration_ms"), (int, float)) for payload in payloads)
