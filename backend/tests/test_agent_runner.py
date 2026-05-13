from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.services.agent_runner as agent_runner_module
import app.services.agent_run_support as agent_run_support_module
from app.models.chat import ModelResponse, ToolCall, ToolCallFunction
from app.services.agent_tools import ToolExecutionContext
from app.services.agent_tools import ToolExecutionResult
from app.services.agent_tools import build_tool_context
from app.services.conversation_store import conversation_store


APPROVED_PLAN = {
    "summary": "Inspect the target file and report back.",
    "steps": ["Inspect the target file.", "Summarize the outcome."],
    "open_questions": [],
    "assumptions": [],
    "approved": True,
    "feedback_skipped": True,
    "approved_at": "2026-05-06T00:00:00Z",
    "feedback": None,
}


async def _create_agent_run(model: str = "anthropic/claude-sonnet-4-5", *, current_step: int = 0):
    conversation = await conversation_store.create_conversation(mode_hint="single")
    run = await conversation_store.start_run(
        conversation.id,
        kind="assistant",
        metadata_json={
            "task_type": "agent",
            "title": "Agent run",
            "goal": "Inspect frontend/lib/hooks.ts and summarize the result.",
            "plan": dict(APPROVED_PLAN),
            "skip_plan_feedback": True,
            "model": model,
            "current_step": current_step,
            "history": [],
            "payload": {},
            "branch_key": "main",
        },
    )
    return conversation, run


async def _create_unplanned_agent_run(model: str = "anthropic/claude-haiku-4.5"):
    conversation = await conversation_store.create_conversation(mode_hint="single")
    run = await conversation_store.start_run(
        conversation.id,
        kind="assistant",
        metadata_json={
            "task_type": "agent",
            "title": "Agent run",
            "goal": "Create Hello World App 9.",
            "skip_plan_feedback": True,
            "model": model,
            "history": [],
            "payload": {},
            "branch_key": "main",
        },
    )
    return conversation, run


def _tool_response(tool_name: str, arguments: dict[str, object], *, content: str = "", reasoning: str = "") -> ModelResponse:
    return ModelResponse(
        model="anthropic/claude-sonnet-4-5",
        content=content,
        reasoning=reasoning,
        usage=None,
        tool_calls=[
            ToolCall(
                id="call_tool",
                function=ToolCallFunction(
                    name=tool_name,
                    arguments=json.dumps(arguments),
                ),
            )
        ],
        finish_reason="tool_calls",
    )


def _finalize_response(result: str = "Finished") -> ModelResponse:
    return ModelResponse(
        model="anthropic/claude-sonnet-4-5",
        content="",
        usage=None,
        tool_calls=[
            ToolCall(
                id="call_finalize",
                function=ToolCallFunction(
                    name="task_finalize",
                    arguments=json.dumps({"summary": result, "result": result}),
                ),
            )
        ],
        finish_reason="tool_calls",
    )


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
        stream_calls.append(model)
        current = responses.pop(0)
        for delta in current["deltas"]:
            if on_content_delta is not None:
                await on_content_delta(delta)
        return current["response"]

    async def _fake_summary(messages, model, **kwargs):
        return ModelResponse(
            model=model,
            content="Inspecting hooks live status",
            usage=None,
            tool_calls=[],
            finish_reason="stop",
        )

    async def _fake_execute(*, context, tool, arguments):
        return ToolExecutionResult(tool=tool, output="hooks inspected", metadata={})

    async def _stream_callback(payload):
        stream_updates.append(payload)

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)
    monkeypatch.setattr(agent_runner_module, "chat_completion", _fake_summary)
    monkeypatch.setattr(agent_runner_module, "execute_agent_tool", _fake_execute)

    await agent_runner_module.agent_runner.continue_run(run.id, stream_callback=_stream_callback)

    events = await conversation_store.list_events_for_run(run.conversation_id, run.id)
    artifacts = await conversation_store.list_artifacts_for_run(run.conversation_id, run.id)
    thought_events = [event for event in events if event.event_type == "agent.thought.summary"]
    progress_events = [event for event in events if event.event_type == "agent.progress.summary"]
    visible_output_artifacts = [
        artifact for artifact in artifacts
        if artifact.artifact_type == "llm.response.visible_output"
        and isinstance(artifact.content_json, dict)
        and artifact.content_json.get("request_kind") == "task.agent.native_decision"
    ]

    assert len(stream_calls) == 2
    assert thought_events[0].payload_json == {
        "step": 1,
        "thought": "Inspecting frontend/lib/hooks.ts for the live status flow.",
    }
    assert any(
        event.payload_json == {"step": 1, "summary": event.payload_json.get("summary")}
        and isinstance(event.payload_json.get("summary"), str)
        and event.payload_json.get("summary")
        and not str(event.payload_json.get("summary")).lower().startswith("use tool ")
        for event in progress_events
    )
    assert any(update == {
        "kind": "thought_delta",
        "run_id": run.id,
        "step": 1,
        "delta": "Inspecting ",
    } for update in stream_updates)
    assert any(
        update.get("kind") == "progress"
        and update.get("run_id") == run.id
        and update.get("step") == 1
        and isinstance(update.get("content"), str)
        and update.get("content")
        and not str(update.get("content")).lower().startswith("use tool ")
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
async def test_continue_run_generates_progress_summary_when_native_decision_has_no_visible_text(monkeypatch):
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

    assert summary_calls == 1
    assert thought_events == []
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
    _, run = await _create_agent_run(model="anthropic/claude-haiku-4.5")
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
    _, run = await _create_agent_run(model="anthropic/claude-haiku-4.5")
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
        current = responses.pop(0)
        for delta in current["reasoning_deltas"]:
            if on_reasoning_delta is not None:
                await on_reasoning_delta(delta)
        return current["response"]

    async def _fake_summary(messages, model, **kwargs):
        return ModelResponse(
            model=model,
            content="Reading lib/hooks.ts",
            usage=None,
            tool_calls=[],
            finish_reason="stop",
        )

    async def _fake_execute(*, context, tool, arguments):
        return ToolExecutionResult(tool=tool, output="hooks inspected", metadata={})

    async def _stream_callback(payload):
        stream_updates.append(payload)

    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_stream)
    monkeypatch.setattr(agent_runner_module, "chat_completion", _fake_summary)
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
        event.payload_json == {
            "step": 1,
            "thought": "Checking the current hooks flow. Then I will inspect the file.",
        }
        for event in thought_events
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

    monkeypatch.setattr(agent_runner_module, "chat_completion", _fake_plan_completion)

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

    restored = await agent_runner_module._restore_runtime_metadata_from_events(
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
