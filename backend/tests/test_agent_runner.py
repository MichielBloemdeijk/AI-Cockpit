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
from app.models.chat import ModelResponse, ToolCall, ToolCallFunction
from tests.agent_runner_fixtures import (
    APPROVED_PLAN,
    create_agent_run as _create_agent_run,
    create_unplanned_agent_run as _create_unplanned_agent_run,
    finalize_response as _finalize_response,
    no_tool_response as _no_tool_response,
    tool_response as _tool_response,
)
from app.services.agent_prompt_builder import NATIVE_AGENT_PLAN_TOOL, _parse_native_tool_arguments
from app.services.agent_tools import ToolExecutionContext
from app.services.agent_tools import ToolExecutionResult
from app.services.agent_tools import build_tool_context
from app.services.agent_tools import run_file_read
from app.services.agent_tools import run_file_write
from app.services.agent_tools import run_python_execution
from app.services.agent_tools import run_shell_command
from app.services.agent_tools import run_workspace_search
from app.services.conversation_store import conversation_store


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
            "ok": True,
            "content": "Finished reading lib/hooks.ts",
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
async def test_continue_run_does_not_commit_visible_text_timeline_item_before_finalize(monkeypatch):
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
                        arguments=json.dumps({"summary": "All done", "result": "All done"}),
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
    assert not any((event.payload_json or {}).get("item_id") == f"assistant:{run.id}:1:text" for event in timeline_events)
    assert any(
        (event.payload_json or {}).get("role") == "assistant"
        and (event.payload_json or {}).get("kind") == "message"
        and (event.payload_json or {}).get("content") == "All done"
        for event in timeline_events
    )


def test_parse_native_tool_arguments_recovers_malformed_task_plan_json():
    raw_arguments = (
        '{"summary":"Update the hello world text",'
        '"steps":["Inspect the app entry file" "Change the displayed text","Verify the preview"],'
        '"open_questions":["Should the punctuation change?"],'
        '"assumptions":["The app already exists"]}'
    )

    parsed = _parse_native_tool_arguments(raw_arguments, tool_name=NATIVE_AGENT_PLAN_TOOL)

    assert parsed == {
        "summary": "Update the hello world text",
        "steps": [
            "Inspect the app entry file",
            "Change the displayed text",
            "Verify the preview",
        ],
        "open_questions": ["Should the punctuation change?"],
        "assumptions": ["The app already exists"],
    }


@pytest.mark.asyncio
async def test_continue_run_skips_progress_summary_until_third_silent_tool_call(monkeypatch):
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
async def test_continue_run_generates_progress_summary_after_third_silent_tool_call(monkeypatch):
    _, run = await _create_agent_run(
        model="anthropic/claude-haiku-4.5",
        silent_tool_call_streak=2,
    )
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

    assert summary_calls == 1
    assert any(
        event.payload_json == {"step": 1, "summary": "Inspecting hooks status flow"}
        for event in progress_events
    )
    assert len(metric_events) == 1
    assert any(
        artifact.content_json == {
            "request_kind": "task.agent.progress.summary",
            "model": artifact.content_json.get("model"),
            "content": "Inspecting hooks status flow",
            "reasoning": "",
            "reasoning_details": [],
            "error": None,
            "finish_reason": "stop",
            "streamed_visible_deltas": [],
            "tool_calls": [],
        }
        for artifact in visible_summary_artifacts
    )


@pytest.mark.asyncio
async def test_continue_run_falls_back_when_progress_summary_is_incomplete(monkeypatch):
    _, run = await _create_agent_run(model="anthropic/claude-haiku-4.5", silent_tool_call_streak=2)
    responses = [
        {"response": _tool_response("file_read", {"path": "frontend/lib/hooks.ts"})},
        {"response": _finalize_response("Inspection complete.")},
    ]

    async def _fake_stream(messages, model, on_content_delta=None, **kwargs):
        return responses.pop(0)["response"]

    async def _low_quality_summary(messages, model, **kwargs):
        return ModelResponse(
            model=model,
            content="Reading the",
            usage=None,
            tool_calls=[],
            finish_reason="stop",
        )

    async def _fake_execute(*, context, tool, arguments):
        return ToolExecutionResult(tool=tool, output="hooks inspected", metadata={})

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)
    monkeypatch.setattr(agent_runner_module, "chat_completion", _low_quality_summary)
    monkeypatch.setattr(agent_runner_module, "execute_agent_tool", _fake_execute)

    await agent_runner_module.agent_runner.continue_run(run.id)

    events = await conversation_store.list_events_for_run(run.conversation_id, run.id)
    progress_events = [event for event in events if event.event_type == "agent.progress.summary"]

    assert any(
        event.payload_json == {"step": 1, "summary": "Reading lib/hooks.ts"}
        for event in progress_events
    )


@pytest.mark.asyncio
async def test_continue_run_falls_back_when_progress_summary_hits_length_limit(monkeypatch):
    _, run = await _create_agent_run(model="anthropic/claude-haiku-4.5", silent_tool_call_streak=2)
    responses = [
        {"response": _tool_response("file_read", {"path": "frontend/lib/hooks.ts"})},
        {"response": _finalize_response("Inspection complete.")},
    ]

    async def _fake_stream(messages, model, on_content_delta=None, **kwargs):
        return responses.pop(0)["response"]

    async def _truncated_summary(messages, model, **kwargs):
        return ModelResponse(
            model=model,
            content="Writing",
            usage=None,
            tool_calls=[],
            finish_reason="length",
        )

    async def _fake_execute(*, context, tool, arguments):
        return ToolExecutionResult(tool=tool, output="hooks inspected", metadata={})

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)
    monkeypatch.setattr(agent_runner_module, "chat_completion", _truncated_summary)
    monkeypatch.setattr(agent_runner_module, "execute_agent_tool", _fake_execute)

    await agent_runner_module.agent_runner.continue_run(run.id)

    events = await conversation_store.list_events_for_run(run.conversation_id, run.id)
    progress_events = [event for event in events if event.event_type == "agent.progress.summary"]

    assert any(
        event.payload_json == {"step": 1, "summary": "Reading lib/hooks.ts"}
        for event in progress_events
    )


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
            "ok": True,
            "content": "Finished reading lib/hooks.ts",
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
    assert "A native tool call is required now" in retry_feedback
    assert any(
        event.payload_json == {"step": 1, "text": "I am checking one more detail before I act."}
        for event in text_events
    )


@pytest.mark.asyncio
async def test_continue_run_preserves_visible_text_before_zero_tool_call_failure(monkeypatch):
    _, run = await _create_agent_run()
    responses = [
        _no_tool_response("I am checking one more detail before I act."),
        _no_tool_response(""),
    ]
    stream_updates: list[dict[str, object]] = []

    async def _fake_stream(messages, model, **kwargs):
        if kwargs.get("tools") is not None:
            return responses.pop(0)
        return _no_tool_response("Inspecting the requested file")

    async def _stream_callback(payload):
        stream_updates.append(payload)

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)

    with pytest.raises(ValueError, match="Native tool decision must return exactly one tool call, got 0"):
        await agent_runner_module.agent_runner.continue_run(run.id, stream_callback=_stream_callback)

    stored_run = await conversation_store.get_run(run.id)
    events = await conversation_store.list_events_for_run(run.conversation_id, run.id)
    text_events = [event for event in events if event.event_type == "agent.text.output"]
    failure_events = [event for event in events if event.event_type == "agent.run.failed"]

    assert stored_run is not None
    assert stored_run.status == "failed"
    assert any(
        update == {
            "kind": "text_done",
            "run_id": run.id,
            "step": 1,
            "content": "I am checking one more detail before I act.",
        }
        for update in stream_updates
    )
    assert any(
        event.payload_json == {"step": 1, "text": "I am checking one more detail before I act."}
        for event in text_events
    )
    assert any(
        event.payload_json == {"error": "Native tool decision must return exactly one tool call, got 0"}
        for event in failure_events
    )


@pytest.mark.asyncio
async def test_ensure_execution_plan_records_visible_output_artifact(monkeypatch):
    conversation, run = await _create_unplanned_agent_run()

    async def _fake_plan_completion(messages, model, **kwargs):
        return ModelResponse(
            model=model,
            content="",
            usage=None,
            tool_calls=[
                ToolCall(
                    id="call_plan",
                    function=ToolCallFunction(
                        name="task_plan",
                        arguments=json.dumps(
                            {
                                "summary": "Create Hello World App 9.",
                                "steps": ["Initialize the app.", "Summarize the result."],
                                "open_questions": [],
                                "assumptions": [],
                            }
                        ),
                    ),
                )
            ],
            finish_reason="tool_calls",
        )

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_plan_completion)

    context = ToolExecutionContext(
        conversation_id=conversation.id,
        run_id=run.id,
        workspace_path=str(Path(".cockpit/conversations") / conversation.id),
        workspace_root=Path("c:/Users/Sander/Documents/Repos/AI-Cockpit"),
        read_roots=[Path("c:/Users/Sander/Documents/Repos")],
        write_roots=[Path("c:/Users/Sander/Documents/Repos/AI-Cockpit")],
    )
    metadata = {
        "task_type": "agent",
        "title": "Agent run",
        "goal": "Create Hello World App 9.",
        "skip_plan_feedback": True,
        "model": "anthropic/claude-haiku-4.5",
        "history": [],
        "payload": {},
        "branch_key": "main",
    }

    await agent_runner_module.agent_runner._ensure_execution_plan(
        conversation_id=conversation.id,
        run_id=run.id,
        metadata=metadata,
        context=context,
    )

    artifacts = await conversation_store.list_artifacts_for_run(conversation.id, run.id)
    assert any(
        artifact.artifact_type == "llm.response.visible_output"
        and isinstance(artifact.content_json, dict)
        and artifact.content_json == {
            "request_kind": "task.agent.plan",
            "model": "anthropic/claude-haiku-4.5",
            "content": "",
            "reasoning": "",
            "reasoning_details": [],
            "error": None,
            "finish_reason": "tool_calls",
            "streamed_visible_deltas": [],
            "tool_calls": [
                {
                    "id": "call_plan",
                    "type": "function",
                    "function": {
                        "name": "task_plan",
                        "arguments": '{"summary": "Create Hello World App 9.", "steps": ["Initialize the app.", "Summarize the result."], "open_questions": [], "assumptions": []}',
                    },
                }
            ],
        }
        for artifact in artifacts
    )


@pytest.mark.asyncio
async def test_continue_run_streams_planning_turn_updates(monkeypatch):
    _, run = await _create_unplanned_agent_run()
    stream_updates: list[dict[str, object]] = []
    tool_choices: list[object] = []
    responses = [
        {
            "deltas": ["Drafting ", "a concrete plan."],
            "response": ModelResponse(
                model="anthropic/claude-haiku-4.5",
                content="",
                usage=None,
                tool_calls=[
                    ToolCall(
                        id="call_plan",
                        function=ToolCallFunction(
                            name="task_plan",
                            arguments=json.dumps(
                                {
                                    "summary": "Create a simple tic tac toe app.",
                                    "steps": ["Initialize the app.", "Implement the game.", "Summarize the result."],
                                    "open_questions": [],
                                    "assumptions": [],
                                }
                            ),
                        ),
                    )
                ],
                finish_reason="tool_calls",
            ),
        },
        {
            "deltas": [],
            "response": _finalize_response("Done."),
        },
    ]

    async def _fake_stream(messages, model, on_content_delta=None, **kwargs):
        tool_choices.append(kwargs.get("tool_choice"))
        current = responses.pop(0)
        for delta in current["deltas"]:
            if on_content_delta is not None:
                await on_content_delta(delta)
        return current["response"]

    async def _stream_callback(payload):
        stream_updates.append(payload)

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)

    await agent_runner_module.agent_runner.continue_run(run.id, stream_callback=_stream_callback)

    assert any(
        update == {
            "kind": "progress",
            "run_id": run.id,
            "step": 0,
            "content": "Drafting execution plan",
        }
        for update in stream_updates
    )
    assert any(
        update == {
            "kind": "text_delta",
            "run_id": run.id,
            "step": 0,
            "delta": "Drafting ",
        }
        for update in stream_updates
    )
    assert any(
        update == {
            "kind": "text_done",
            "run_id": run.id,
            "step": 0,
            "content": "Drafting a concrete plan.",
        }
        for update in stream_updates
    )
    assert tool_choices == ["auto", "auto"]


@pytest.mark.asyncio
async def test_continue_run_streams_progress_summary_deltas_and_tool_lifecycle(monkeypatch):
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

    assert any(
        update == {
            "kind": "summary_delta",
            "run_id": run.id,
            "step": 1,
            "delta": "Editing",
        }
        for update in stream_updates
    )
    assert any(
        update == {
            "kind": "summary_done",
            "run_id": run.id,
            "step": 1,
            "content": "Editing the landing page",
        }
        for update in stream_updates
    )
    assert any(
        update == {
            "kind": "tool_done",
            "run_id": run.id,
            "step": 1,
            "tool": "file_write",
            "ok": True,
            "content": "Updated app/page.tsx",
        }
        for update in stream_updates
    )
    assert any(
        artifact.content_json == {
            "request_kind": "task.agent.progress.summary",
            "model": artifact.content_json.get("model"),
            "content": "Editing the landing page",
            "reasoning": "",
            "reasoning_details": [],
            "error": None,
            "finish_reason": "stop",
            "streamed_visible_deltas": ["Editing", " the landing page"],
            "tool_calls": [],
        }
        for artifact in visible_summary_artifacts
    )


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
    assert "shown directly to the user as a progress update" in system_prompt
    assert "Write like you are updating the user, not thinking to yourself" in system_prompt
    assert "must add net-new information only" in system_prompt
    assert "Do not restate the goal, approved plan, recent conversation context, or recent runtime transcript" in system_prompt
    assert "were already shown to the user" in user_prompt
    assert "Continue the task now from the latest completed step" in user_prompt


@pytest.mark.asyncio
async def test_continue_run_streams_live_tool_call_previews_before_tool_execution(monkeypatch):
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

    assert any(
        update == {
            "kind": "tool_progress",
            "run_id": run.id,
            "step": 1,
            "tool": "file_write",
            "content": "Preparing to write frontend/generated-apps/demo/page.tsx (1 lines, 36 bytes buffered)",
        }
        for update in stream_updates
    )
    assert any(
        update.get("kind") == "tool_progress"
        and update.get("run_id") == run.id
        and update.get("step") == 1
        and update.get("tool") == "file_write"
        and "3 lines, 73 bytes buffered" in str(update.get("content") or "")
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
    assert live_item["item_id"] == f"tool:{run.id}:1:file_read"
    assert live_item["kind"] == "tool_call"
    assert live_item["content"] == "Reading lib/hooks.ts"

    allow_finish.set()
    await task

    completed_run = await conversation_store.get_run(run.id)
    assert completed_run is not None
    assert (completed_run.metadata_json or {}).get("live_item") is None


@pytest.mark.asyncio
async def test_continue_run_streams_partial_plan_sections_in_tool_preview(monkeypatch):
    _, run = await _create_unplanned_agent_run(model="anthropic/claude-haiku-4.5")
    responses = [
        {
            "response": _tool_response(
                "task_plan",
                {
                    "summary": "Build a tiny probe app.",
                    "steps": ["Initialize the app", "Render one heading"],
                    "open_questions": ["What heading text should appear?"],
                    "assumptions": ["A default title is acceptable"],
                },
                content="Planning the app steps now.",
            )
        },
        {"response": _finalize_response("Done.")},
    ]
    stream_updates: list[dict[str, object]] = []

    async def _fake_stream(messages, model, on_tool_call_delta=None, **kwargs):
        current = responses.pop(0)
        if kwargs.get("tools") is not None and on_tool_call_delta is not None:
            await on_tool_call_delta(
                ToolCall(
                    id="call_plan",
                    function=ToolCallFunction(
                        name="task_plan",
                        arguments=(
                            '{"summary":"Build a tiny probe app.",'
                            '"steps":["Initialize the app","Render one heading"],'
                            '"open_questions":["What heading text should appear?"],'
                            '"assumptions":["A default title is acceptable"]}'
                        ),
                    ),
                )
            )
        return current["response"]

    async def _stream_callback(payload):
        stream_updates.append(payload)

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)

    await agent_runner_module.agent_runner.continue_run(run.id, stream_callback=_stream_callback)

    assert any(
        update.get("kind") == "tool_progress"
        and update.get("tool") == "task_plan"
        and "Steps" in str(update.get("content") or "")
        and "Initialize the app" in str(update.get("content") or "")
        and "Open points" in str(update.get("content") or "")
        and "Assumptions" in str(update.get("content") or "")
        for update in stream_updates
    )


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
    assert any(update.startswith("Writing 5 bytes to ") and update.endswith("draft.txt") for update in progress_updates)
    assert any(update.startswith("Saved ") and update.endswith("draft.txt") for update in progress_updates)
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

    assert any(update.startswith("Preparing Python run in ") for update in progress_updates)
    assert any(update == "Starting Python process" for update in progress_updates)
    assert any(update == "stdout: live tool output" for update in progress_updates)
    assert any(update == "Python run completed" for update in progress_updates)
    assert any(update.startswith("Preparing command in ") for update in progress_updates)
    assert any(update.startswith("Starting ") for update in progress_updates)
    assert any(update == "stdout: live shell output" for update in progress_updates)
    assert any(update == "Command finished with exit code 0" for update in progress_updates)


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
    assert [event.event_type for event in events] == ["agent.tool.called", "agent.tool.completed"]
    assert len(artifacts) == 1
    assert stored_run is not None
    assert stored_run.metadata_json["active_step_index"] == 1
    assert stored_run.metadata_json["active_step"] == "Completed file_read"


@pytest.mark.asyncio
async def test_handle_tool_action_can_defer_metadata_checkpoint(monkeypatch):
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
    assert stored_run.metadata_json["current_step"] == 0
    assert stored_run.metadata_json["history"] == []
    assert [event.event_type for event in events] == ["agent.tool.called", "agent.tool.completed"]


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
    assert any(
        payload.get("phase") == "progress_summary.model_call"
        and payload.get("step") == 1
        for payload in payloads
    )
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
