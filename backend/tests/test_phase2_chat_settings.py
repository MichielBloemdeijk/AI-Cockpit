from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
import app.services.agent_runner as agent_runner_module
import app.services.chat_orchestrator as orchestrator_module
from app.models.chat import ChatResponse, ModelResponse, ToolCall, ToolCallFunction
from app.services.conversation_store import conversation_store


async def _fake_completion(messages, model, temperature=0.7, max_tokens=4096, session_id=None, prompt_segments=None):
    class _Response:
        def __init__(self):
            self.model = model
            self.content = f"reply:{model}:{messages[-1].content}"
            self.usage = None
            self.error = None

    return _Response()


@pytest.mark.asyncio
async def test_chat_settings_apply_to_future_sessions_only(monkeypatch):
    monkeypatch.setattr(orchestrator_module, "chat_completion", _fake_completion)

    async def _fake_run_single_response(*, messages, conversation_id, session_metadata, model, temperature, max_tokens, branch_key="main", parent_event_id=None):
        conversation = await conversation_store.create_conversation(
            mode_hint=session_metadata.mode,
            session_metadata_json=session_metadata.model_dump(),
        )
        return ChatResponse(
            conversation_id=conversation.id,
            run_id="run-settings-test",
            model=model,
            content="Finished",
        )

    monkeypatch.setattr(orchestrator_module.chat_orchestrator, "run_single_response", _fake_run_single_response)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": "dev"})

        updated_defaults = {
            "defaults": {
                "mode": "single",
                "single_model": "openai/gpt-4o",
                "council_models": ["openai/gpt-4o", "anthropic/claude-sonnet-4-5"],
                "synthesizer_model": "anthropic/claude-sonnet-4-5",
                "tool_flags": {"workspace_search": True, "python_execution": True},
            },
            "task_agent_model": "moonshotai/kimi-k2.6",
        }
        update = await client.put("/api/chat/settings", json=updated_defaults)
        assert update.status_code == 200
        assert update.json()["task_agent_model"] == "moonshotai/kimi-k2.6"

        settings_response = await client.get("/api/chat/settings")
        assert settings_response.status_code == 200
        assert settings_response.json()["task_agent_model"] == "moonshotai/kimi-k2.6"

        first = await client.post(
            "/api/conversations/",
            json={
                "initial_message": "First session",
                "session_metadata": {
                    "mode": "single",
                    "single_model": "google/gemini-pro-1.5",
                    "council_models": ["google/gemini-pro-1.5"],
                    "synthesizer_model": "anthropic/claude-sonnet-4-5",
                    "tool_flags": {"workspace_search": True, "python_execution": True},
                },
            },
        )
        assert first.status_code == 201
        first_conversation_id = first.json()["conversation"]["id"]

        second = await client.post(
            "/api/conversations/",
            json={"initial_message": "Second session"},
        )
        assert second.status_code == 201
        second_conversation_id = second.json()["conversation"]["id"]

        first_detail = await client.get(f"/api/conversations/{first_conversation_id}")
        second_detail = await client.get(f"/api/conversations/{second_conversation_id}")
        assert first_detail.status_code == 200
        assert second_detail.status_code == 200
        assert first_detail.json()["session_metadata"]["single_model"] == "google/gemini-pro-1.5"
        assert second_detail.json()["session_metadata"]["single_model"] == "openai/gpt-4o"


@pytest.mark.asyncio
async def test_task_agent_model_setting_applies_to_future_chat_agent_runs(monkeypatch):
    seen_models: list[str] = []
    responses = ["plan", "final"]

    async def _fake_chat_completion(
        messages,
        model,
        temperature=0.7,
        max_tokens=4096,
        response_format=None,
        tools=None,
        tool_choice=None,
        session_id=None,
        prompt_segments=None,
        request_overrides=None,
        **kwargs,
    ):
        seen_models.append(model)
        if responses.pop(0) == "plan":
            return ModelResponse(
                model=model,
                content="Plan drafted.",
                usage=None,
                tool_calls=[
                    ToolCall(
                        id="call_task_plan",
                        function=ToolCallFunction(
                            name="task_plan",
                            arguments=json.dumps({"summary": "Use the configured task model.", "steps": ["Finish immediately."], "open_questions": [], "assumptions": []}),
                        ),
                    )
                ],
                finish_reason="tool_calls",
            )
        return ModelResponse(
            model=model,
            content="Done",
            usage=None,
            tool_calls=[
                ToolCall(
                    id="call_task_finalize",
                    function=ToolCallFunction(
                        name="task_finalize",
                        arguments=json.dumps({"summary": "Finished", "result": "Used the configured task model."}),
                    ),
                )
            ],
            finish_reason="tool_calls",
        )

    monkeypatch.setattr(agent_runner_module, "chat_completion", _fake_chat_completion)
    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_chat_completion)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": "dev"})
        update = await client.put(
            "/api/chat/settings",
            json={
                "defaults": {
                    "mode": "single",
                    "single_model": "openai/gpt-4o",
                    "council_models": ["openai/gpt-4o", "anthropic/claude-sonnet-4-5"],
                    "synthesizer_model": "anthropic/claude-sonnet-4-5",
                    "tool_flags": {"workspace_search": True, "python_execution": True},
                },
                "task_agent_model": "moonshotai/kimi-k2.6",
            },
        )
        assert update.status_code == 200

        created = await client.post(
            "/api/conversations/",
            json={
                "initial_message": "verify configured agent model",
                "session_metadata": {
                    "mode": "single",
                    "single_model": "openai/gpt-4o",
                    "council_models": ["openai/gpt-4o"],
                    "synthesizer_model": "openai/gpt-4o",
                    "tool_flags": {"workspace_search": True, "python_execution": True},
                },
            },
        )
        assert created.status_code == 201

    assert seen_models
    assert all(model == "moonshotai/kimi-k2.6" for model in seen_models)
