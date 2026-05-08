from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.chat import ChatResponse, ModelResponse, ToolCall, ToolCallFunction
import app.services.agent_runner as agent_runner_module
import app.services.chat_orchestrator as orchestrator_module
from app.services.conversation_store import conversation_store


def _plan_payload(summary: str) -> dict[str, object]:
    return {
        "summary": summary,
        "steps": ["Do the requested work."],
        "open_questions": [],
        "assumptions": [],
    }


def _final_payload(result: str) -> dict[str, object]:
    return {
        "kind": "final",
        "thought": "Done",
        "summary": result,
        "result": result,
    }


def _native_finalize_response(model: str, result: str) -> ModelResponse:
    return ModelResponse(
        model=model,
        content="Done",
        usage=None,
        tool_calls=[
            ToolCall(
                id="call_task_finalize",
                function=ToolCallFunction(
                    name="task_finalize",
                    arguments=json.dumps({"summary": result, "result": result}),
                ),
            )
        ],
        finish_reason="tool_calls",
    )


def _native_plan_response(model: str, summary: str) -> ModelResponse:
    return ModelResponse(
        model=model,
        content="Plan drafted.",
        usage=None,
        tool_calls=[
            ToolCall(
                id="call_task_plan",
                function=ToolCallFunction(
                    name="task_plan",
                    arguments=json.dumps(
                        {
                            "summary": summary,
                            "steps": ["Do the requested work."],
                            "open_questions": [],
                            "assumptions": [],
                        }
                    ),
                ),
            )
        ],
        finish_reason="tool_calls",
    )


@pytest.mark.asyncio
async def test_initial_message_persists_user_and_assistant_message(monkeypatch):
    responses = ["Plan initial conversation response.", "Initial response complete."]

    async def _fake_chat_completion(messages, model, temperature=0.7, max_tokens=4096, response_format=None, tools=None, tool_choice=None, session_id=None, prompt_segments=None):
        if len(responses) == 2:
            return _native_plan_response(model, responses.pop(0))
        return _native_finalize_response(model, responses.pop(0))

    monkeypatch.setattr(agent_runner_module, "chat_completion", _fake_chat_completion)
    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_chat_completion)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": "dev"})

        created = await client.post(
            "/api/conversations/",
            json={"initial_message": "first durable turn"},
        )
        assert created.status_code == 201
        conversation_id = created.json()["conversation"]["id"]

        detail = await client.get(f"/api/conversations/{conversation_id}")
        assert detail.status_code == 200
        messages = detail.json()["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "first durable turn"
        assert messages[1]["role"] == "assistant"
        assert isinstance(messages[1]["content"], str)
        assert messages[1]["content"]

@pytest.mark.asyncio
async def test_single_model_stream_returns_done_and_persists_assistant_message(monkeypatch):
    responses = ["Plan stream response.", "Streamed response complete."]

    async def _fake_chat_completion(messages, model, temperature=0.7, max_tokens=4096, response_format=None, tools=None, tool_choice=None, session_id=None, prompt_segments=None):
        if len(responses) == 2:
            return _native_plan_response(model, responses.pop(0))
        return _native_finalize_response(model, responses.pop(0))

    monkeypatch.setattr(agent_runner_module, "chat_completion", _fake_chat_completion)
    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_chat_completion)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": "dev"})

        async with client.stream(
            "POST",
            "/api/chat/",
            json={
                "messages": [{"role": "user", "content": "stream me"}],
                "stream": True,
                "council_mode": False,
            },
        ) as streamed:
            payloads_stream = []
            async for line in streamed.aiter_lines():
                if line.startswith("data: "):
                    payloads_stream.append(json.loads(line[6:]))

        assert payloads_stream[0]["type"] == "metadata"
        assert payloads_stream[-1]["type"] in {"done", "error"}
        if payloads_stream[-1]["type"] == "done":
            streamed_text = "".join(item["content"] for item in payloads_stream if item["type"] == "chunk")
            assert "Streamed response complete" in streamed_text

        conversation_id = payloads_stream[0]["conversation_id"]
        detail = await client.get(f"/api/conversations/{conversation_id}")
        assert detail.status_code == 200
        assert any(message["role"] == "assistant" and message["content"] for message in detail.json()["messages"])

@pytest.mark.asyncio
async def test_stream_single_response_emits_metadata_before_run_completion(monkeypatch):
    gate = asyncio.Event()

    async def _fake_continue_run(run_id: str):
        run = await conversation_store.get_run(run_id)
        assert run is not None
        await conversation_store.append_event(
            run.conversation_id,
            run_id=run.id,
            actor_kind="assistant",
            event_type="agent.plan.created",
            payload_json={
                "summary": "Plan while running",
                "steps": ["Keep streaming progress"],
                "open_questions": [],
                "assumptions": [],
            },
        )
        await gate.wait()
        await conversation_store.complete_message(
            run.conversation_id,
            run_id=run.id,
            role="assistant",
            content="Completed after metadata",
            actor_kind="assistant",
            event_type="conversation.assistant.message.completed",
            author_label="agent",
            payload_json={"model": "anthropic/claude-sonnet-4-5", "content": "Completed after metadata"},
            branch_key="main",
        )
        await conversation_store.mark_run_completed(run.id)

    monkeypatch.setattr(orchestrator_module, "agent_runner", type("_Runner", (), {"continue_run": staticmethod(_fake_continue_run)})())

    session_metadata = {
        "mode": "single",
        "single_model": "anthropic/claude-sonnet-4-5",
        "council_models": ["anthropic/claude-sonnet-4-5"],
        "synthesizer_model": "anthropic/claude-sonnet-4-5",
        "tool_flags": {"workspace_search": True, "python_execution": True},
    }

    envelope = await orchestrator_module.chat_orchestrator.stream_single_response(
        messages=[orchestrator_module.Message(role="user", content="stream progress")],
        conversation_id=None,
        session_metadata=orchestrator_module.ConversationSessionMetadata.model_validate(session_metadata),
        model="anthropic/claude-sonnet-4-5",
        temperature=0.7,
        max_tokens=256,
    )

    event_iterator = envelope.events.__aiter__()
    metadata = await anext(event_iterator)

    assert metadata["type"] == "metadata"
    assert metadata["conversation_id"] == envelope.conversation_id
    assert metadata["run_id"] == envelope.run_id
    persisted_conversation = await conversation_store.get_conversation(envelope.conversation_id)
    assert persisted_conversation is not None

    progress_event = await anext(event_iterator)
    assert progress_event["type"] == "event"
    assert progress_event["event"]["event_type"] == "agent.plan.created"

    gate.set()
    remaining_events = []
    async for event in event_iterator:
        remaining_events.append(event)

    assert remaining_events[-1]["type"] == "done"
    assert any(event.get("type") == "chunk" and event.get("content") == "Completed after metadata" for event in remaining_events)


@pytest.mark.asyncio
async def test_stream_single_response_emits_transient_agent_stream_updates(monkeypatch):
    gate = asyncio.Event()

    async def _fake_continue_run(run_id: str, stream_callback=None):
        run = await conversation_store.get_run(run_id)
        assert run is not None
        if stream_callback is not None:
            await stream_callback({
                "kind": "thought_delta",
                "run_id": run.id,
                "step": 1,
                "delta": "Inspecting ",
            })
            await stream_callback({
                "kind": "progress",
                "run_id": run.id,
                "step": 1,
                "content": "Inspecting the workspace",
            })
        await gate.wait()
        await conversation_store.complete_message(
            run.conversation_id,
            run_id=run.id,
            role="assistant",
            content="Completed after transient updates",
            actor_kind="assistant",
            event_type="conversation.assistant.message.completed",
            author_label="agent",
            payload_json={"model": "anthropic/claude-sonnet-4-5", "content": "Completed after transient updates"},
            branch_key="main",
        )
        await conversation_store.mark_run_completed(run.id)

    monkeypatch.setattr(orchestrator_module, "agent_runner", type("_Runner", (), {"continue_run": staticmethod(_fake_continue_run)})())

    session_metadata = {
        "mode": "single",
        "single_model": "anthropic/claude-sonnet-4-5",
        "council_models": ["anthropic/claude-sonnet-4-5"],
        "synthesizer_model": "anthropic/claude-sonnet-4-5",
        "tool_flags": {"workspace_search": True, "python_execution": True},
    }

    envelope = await orchestrator_module.chat_orchestrator.stream_single_response(
        messages=[orchestrator_module.Message(role="user", content="stream transient progress")],
        conversation_id=None,
        session_metadata=orchestrator_module.ConversationSessionMetadata.model_validate(session_metadata),
        model="anthropic/claude-sonnet-4-5",
        temperature=0.7,
        max_tokens=256,
    )

    event_iterator = envelope.events.__aiter__()
    metadata = await anext(event_iterator)
    agent_stream_frame = await anext(event_iterator)

    assert metadata["type"] == "metadata"
    assert agent_stream_frame == {
        "type": "agent_stream",
        "stream": {
            "kind": "thought_delta",
            "run_id": envelope.run_id,
            "step": 1,
            "delta": "Inspecting ",
        },
    }

    progress_stream_frame = await anext(event_iterator)
    assert progress_stream_frame == {
        "type": "agent_stream",
        "stream": {
            "kind": "progress",
            "run_id": envelope.run_id,
            "step": 1,
            "content": "Inspecting the workspace",
        },
    }

    gate.set()
    remaining_events = []
    async for event in event_iterator:
        remaining_events.append(event)

    assert remaining_events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_conversation_message_returns_error_when_single_response_fails(monkeypatch):
    async def _failing_single_response(**kwargs):
        return ChatResponse(
            conversation_id=kwargs["conversation_id"],
            run_id="run-failed",
            model="anthropic/claude-sonnet-4-5",
            content="",
            error="provider failed",
        )

    monkeypatch.setattr(orchestrator_module.chat_orchestrator, "run_single_response", _failing_single_response)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": "dev"})
        created = await client.post("/api/conversations", json={"title": "Single failure"})
        conversation_id = created.json()["conversation"]["id"]

        response = await client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "hello", "branch_key": "main"},
        )

        assert response.status_code == 502
        assert response.json()["detail"] == "provider failed"


@pytest.mark.asyncio
async def test_collection_routes_accept_requests_without_trailing_slashes(monkeypatch):
    responses = ["Plan slashless chat response.", "slashless ok"]

    async def _fake_chat_completion(messages, model, temperature=0.7, max_tokens=4096, response_format=None, tools=None, tool_choice=None, session_id=None, prompt_segments=None):
        if len(responses) == 2:
            return _native_plan_response(model, responses.pop(0))
        return _native_finalize_response(model, responses.pop(0))

    monkeypatch.setattr(agent_runner_module, "chat_completion", _fake_chat_completion)
    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_chat_completion)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": "dev"})

        chat = await client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "slashless chat"}],
                "stream": False,
                "council_mode": False,
            },
        )
        assert chat.status_code == 200

        created = await client.post("/api/conversations", json={"title": "Slashless conversation"})
        assert created.status_code == 201

        listing = await client.get("/api/conversations?include_archived=false")
        assert listing.status_code == 200

        tasks = await client.get("/api/tasks")
        assert tasks.status_code == 404


@pytest.mark.asyncio
async def test_conversation_archive_and_branch_resend(monkeypatch):
    async def _fake_completion(messages, model, temperature=0.7, max_tokens=4096, session_id=None, prompt_segments=None):
        return ModelResponse(
            model=model,
            content=f"reply:{model}:{messages[-1].content}",
            usage=None,
            error=None,
        )

    monkeypatch.setattr(orchestrator_module, "chat_completion", _fake_completion)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": "dev"})

        created = await client.post(
            "/api/conversations/",
            json={
                "initial_message": "Original question",
                "session_metadata": {
                    "mode": "council",
                    "single_model": "openai/gpt-4o",
                    "council_models": ["openai/gpt-4o"],
                    "synthesizer_model": "openai/gpt-4o",
                    "tool_flags": {"workspace_search": True, "python_execution": True},
                },
            },
        )
        assert created.status_code == 201
        conversation_id = created.json()["conversation"]["id"]

        detail = await client.get(f"/api/conversations/{conversation_id}")
        assert detail.status_code == 200
        user_message_id = detail.json()["messages"][0]["id"]

        branch = await client.post(
            f"/api/conversations/{conversation_id}/branches/resend",
            json={
                "source_message_id": user_message_id,
                "content": "Edited question",
                "parent_branch_key": "main",
            },
        )
        assert branch.status_code == 200
        branch_key = branch.json()["branch"]["branch_key"]

        main_detail = await client.get(f"/api/conversations/{conversation_id}")
        branch_detail = await client.get(f"/api/conversations/{conversation_id}?branch_key={branch_key}")
        assert any(message["content"] == "Original question" for message in main_detail.json()["messages"])
        assert any(message["content"] == "Edited question" for message in branch_detail.json()["messages"])

        archived = await client.post(f"/api/conversations/{conversation_id}/archive")
        assert archived.status_code == 200
        assert archived.json()["archived_at"] is not None
