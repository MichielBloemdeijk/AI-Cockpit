from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.chat import ChatResponse, ModelResponse
import app.services.conversations.chat_orchestrator as orchestrator_module
from app.services.conversations.store import conversation_store


def _streamed_reply(text: str):
    async def _fake_stream(messages, model, **kwargs):
        on_delta = kwargs.get("on_content_delta")
        if on_delta is not None:
            midpoint = len(text) // 2
            await on_delta(text[:midpoint])
            await on_delta(text[midpoint:])
        return ModelResponse(model=model, content=text, usage=None)
    return _fake_stream


@pytest.mark.asyncio
async def test_initial_message_persists_user_and_assistant_message(monkeypatch):
    _fake_chat_completion = _streamed_reply("Initial response complete.")

    monkeypatch.setattr(orchestrator_module, "chat_completion_stream_response", _fake_chat_completion)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": ""})

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

        events = await client.get(f"/api/conversations/{conversation_id}/events")
        assert events.status_code == 200
        timeline_items = events.json()["timeline_items"]
        assert [item["content"] for item in timeline_items] == ["first durable turn", messages[1]["content"]]
        assert events.json()["active_run"] is None

@pytest.mark.asyncio
async def test_single_model_stream_returns_done_and_persists_assistant_message(monkeypatch):
    _fake_chat_completion = _streamed_reply("Streamed response complete.")

    monkeypatch.setattr(orchestrator_module, "chat_completion_stream_response", _fake_chat_completion)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": ""})

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
        assert payloads_stream[-1]["type"] == "done"
        final_items = [
            item["event"]["payload_json"]
            for item in payloads_stream
            if item["type"] == "event"
            and item["event"]["event_type"] == "chat.timeline.item.committed"
            and item["event"]["payload_json"].get("kind") == "message"
        ]
        assert any(item.get("content") == "Streamed response complete." for item in final_items)
        streamed_text = "".join(item["content"] for item in payloads_stream if item["type"] == "chunk")
        assert streamed_text == "Streamed response complete."

        conversation_id = payloads_stream[0]["conversation_id"]
        detail = await client.get(f"/api/conversations/{conversation_id}")
        assert detail.status_code == 200
        assert any(message["role"] == "assistant" and message["content"] for message in detail.json()["messages"])


@pytest.mark.asyncio
async def test_conversation_events_expose_active_run_live_item_snapshot():
    conversation = await conversation_store.create_conversation(mode_hint="single")
    run = await conversation_store.start_run(
        conversation.id,
        kind="assistant",
        metadata_json={
            "branch_key": "main",
            "live_item": {
                "item_id": "tool:run-1:1:file_read",
                "role": "assistant",
                "kind": "tool_call",
                "title": "file read",
                "label": "Tool call",
                "content": "Preparing to read hooks.ts",
                "status": "streaming",
                "sequence": 1,
                "created_at": "2026-05-19T00:00:00Z",
            },
        },
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": ""})
        response = await client.get(f"/api/conversations/{conversation.id}/events")

    assert response.status_code == 200
    active_run = response.json()["active_run"]
    assert active_run["run_id"] == run.id
    assert active_run["status"] == "running"
    assert active_run["live_item"]["item_id"] == "tool:run-1:1:file_read"
    assert active_run["live_item"]["content"] == "Preparing to read hooks.ts"


@pytest.mark.asyncio
async def test_conversation_events_support_lightweight_cursor_catch_up():
    conversation = await conversation_store.create_conversation(mode_hint="voice")
    first = await conversation_store.append_event(
        conversation.id,
        run_id=None,
        actor_kind="system",
        event_type="test.first",
        payload_json={"value": 1},
        branch_key="main",
    )
    second = await conversation_store.append_event(
        conversation.id,
        run_id=None,
        actor_kind="system",
        event_type="test.second",
        payload_json={"value": 2},
        branch_key="main",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": ""})
        response = await client.get(
            f"/api/conversations/{conversation.id}/events",
            params={"branch_key": "main", "after_sequence": first.sequence},
        )

    assert response.status_code == 200
    body = response.json()
    assert [event["id"] for event in body["events"]] == [second.id]
    assert body["artifacts"] == []
    assert body["timeline_items"] == []

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
        await client.post("/api/auth/login", json={"password": ""})
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
    _fake_chat_completion = _streamed_reply("slashless ok")

    monkeypatch.setattr(orchestrator_module, "chat_completion_stream_response", _fake_chat_completion)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": ""})

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
        assert tasks.status_code == 200


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
        await client.post("/api/auth/login", json={"password": ""})

        created = await client.post(
            "/api/conversations/",
            json={
                "initial_message": "Original question",
                "session_metadata": {
                    "mode": "council",
                    "single_model": "openai/gpt-4o",
                    "council_models": ["openai/gpt-4o"],
                    "synthesizer_model": "openai/gpt-4o",
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

        active_listing = await client.get("/api/conversations?include_archived=false")
        archived_listing = await client.get("/api/conversations?archived_only=true")
        assert conversation_id not in {item["id"] for item in active_listing.json()}
        assert {item["id"] for item in archived_listing.json()} == {conversation_id}


@pytest.mark.asyncio
async def test_council_failure_after_run_start_fails_the_run(monkeypatch):
    async def _raising_completion(messages, model, *args, **kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(orchestrator_module, "chat_completion", _raising_completion)
    conversation = await conversation_store.create_conversation(mode_hint="council")
    with pytest.raises(RuntimeError, match="provider exploded"):
        await orchestrator_module.chat_orchestrator.run_council_response(
            messages=[orchestrator_module.Message(role="user", content="Council question")],
            conversation_id=conversation.id,
            session_metadata=None,
            temperature=0.5,
            max_tokens=64,
            models=["openai/gpt-4o"],
            synthesizer_model="openai/gpt-4o",
        )
    runs = await conversation_store.list_runs(conversation.id)
    assert [(run.kind, run.status, run.error) for run in runs] == [("council", "failed", "provider exploded")]


@pytest.mark.asyncio
async def test_council_synthesis_error_ends_the_run_once(monkeypatch):
    async def _completion(messages, model, *args, **kwargs):
        if model == "model/synth":
            return ModelResponse(model=model, content="", error="synthesis unavailable")
        return ModelResponse(model=model, content=f"{model} answer")

    monkeypatch.setattr(orchestrator_module, "chat_completion", _completion)
    conversation = await conversation_store.create_conversation(mode_hint="council")
    result = await orchestrator_module.chat_orchestrator.run_council_response(
        messages=[orchestrator_module.Message(role="user", content="Council question")],
        conversation_id=conversation.id,
        session_metadata=None,
        temperature=0.5,
        max_tokens=64,
        models=["model/a"],
        synthesizer_model="model/synth",
    )
    assert result["error"] == "synthesis unavailable"
    run_id = result["run_id"]
    # The first outcome wins: a late completion or cancellation cannot rewrite an ended run.
    assert await conversation_store.end_run(run_id, "completed") is None
    assert await conversation_store.end_run(run_id, "cancelled") is None
    run = await conversation_store.get_run(run_id)
    assert run is not None and (run.status, run.error) == ("failed", "synthesis unavailable")


@pytest.mark.asyncio
async def test_turn_preparation_failure_does_not_strand_a_running_run(monkeypatch):
    async def _failing_append(*args, **kwargs):
        raise RuntimeError("append failed")

    monkeypatch.setattr(conversation_store, "append_user_message", _failing_append)
    conversation = await conversation_store.create_conversation(mode_hint="single")
    with pytest.raises(RuntimeError, match="append failed"):
        await orchestrator_module.chat_orchestrator.run_single_response(
            messages=[orchestrator_module.Message(role="user", content="Hello")],
            conversation_id=conversation.id,
            session_metadata=None,
            model=None,
            temperature=0.5,
            max_tokens=64,
        )
    runs = await conversation_store.list_runs(conversation.id)
    assert [(run.status, run.error) for run in runs] == [("failed", "append failed")]


@pytest.mark.asyncio
async def test_reloaded_council_timeline_carries_model_answers_and_usage(monkeypatch):
    async def _fake_completion(messages, model, *args, **kwargs):
        return ModelResponse(model=model, content=f"answer from {model}",
                             usage={"total_tokens": 10, "cost": 0.01}, error=None)

    monkeypatch.setattr(orchestrator_module, "chat_completion", _fake_completion)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": ""})
        created = await client.post("/api/conversations/", json={
            "initial_message": "Council question",
            "session_metadata": {
                "mode": "council",
                "single_model": "openai/gpt-4o",
                "council_models": ["model/a", "model/b"],
                "synthesizer_model": "model/synth",
            },
        })
        assert created.status_code == 201
        conversation_id = created.json()["conversation"]["id"]
        events = await client.get(f"/api/conversations/{conversation_id}/events")

    answer = events.json()["timeline_items"][-1]
    assert answer["role"] == "assistant" and answer["content"] == "answer from model/synth"
    council = answer["council"]
    assert [(response["model"], response["content"]) for response in council["model_responses"]] == [
        ("model/a", "answer from model/a"), ("model/b", "answer from model/b"),
    ]
    assert council["synthesizer_model"] == "model/synth"
    assert answer["usage"] == council["total_usage"]
    assert answer["usage"]["total_tokens"] == 30
