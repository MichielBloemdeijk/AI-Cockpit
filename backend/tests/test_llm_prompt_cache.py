from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

import app.services.llm as llm_module
from app.models.chat import Message, ModelResponse
from app.services.chat_orchestrator import ChatOrchestrator
from app.services.llm import PromptSegment, render_prompt_messages, render_prompt_segments, resolve_prompt_cache_policy


def test_prompt_cache_policy_is_provider_aware():
    anthropic_policy = resolve_prompt_cache_policy("anthropic/claude-sonnet-4.5")
    assert anthropic_policy.strategy == "anthropic_explicit_top_level"
    assert anthropic_policy.top_level_cache_control is not None
    assert anthropic_policy.top_level_cache_control["type"] == "ephemeral"

    openai_policy = resolve_prompt_cache_policy("openai/gpt-4.1")
    assert openai_policy.strategy == "implicit_prefix_stability"
    assert openai_policy.top_level_cache_control is None

    deepseek_policy = resolve_prompt_cache_policy("deepseek/deepseek-chat-v3")
    assert deepseek_policy.strategy == "implicit_prefix_stability"
    assert deepseek_policy.top_level_cache_control is None


def test_prompt_segments_render_explicit_breakpoint_for_anthropic():
    stable_block = "A" * 5000
    rendered = render_prompt_segments(
        [
            PromptSegment(role="system", text=stable_block, cache_candidate=True, stable=True),
            PromptSegment(role="user", text="What changed?"),
        ],
        "anthropic/claude-sonnet-4.5",
    )
    assert isinstance(rendered[0]["content"], list)
    assert rendered[0]["content"][0]["cache_control"]["type"] == "ephemeral"


def test_prompt_segments_fall_back_to_plain_strings_for_openai_and_deepseek():
    segments = [
        PromptSegment(role="system", text="Stable instructions", cache_candidate=True, stable=True),
        PromptSegment(role="user", text="Question"),
    ]
    openai_rendered = render_prompt_segments(segments, "openai/gpt-4.1")
    deepseek_rendered = render_prompt_segments(segments, "deepseek/deepseek-chat-v3")
    assert openai_rendered == [
        {"role": "system", "content": "Stable instructions"},
        {"role": "user", "content": "Question"},
    ]
    assert deepseek_rendered == openai_rendered


def test_chat_orchestrator_segments_preserve_provider_role_values():
    orchestrator = ChatOrchestrator()

    rendered = render_prompt_messages(
        [Message(role="user", content="Hello")],
        "anthropic/claude-sonnet-4.6",
        prompt_segments=[orchestrator._message_to_segment(Message(role="user", content="Hello"))],
    )

    assert rendered == [{"role": "user", "content": "Hello"}]


def test_prompt_segments_still_render_block_content_for_anthropic_explicit_breakpoints():
    orchestrator = ChatOrchestrator()

    rendered = render_prompt_segments(
        [orchestrator._message_to_segment(Message(role="user", content="Hello"))],
        "anthropic/claude-sonnet-4.6",
    )

    assert rendered == [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}]


def test_render_prompt_messages_falls_back_when_prompt_segments_are_empty():
    rendered = render_prompt_messages(
        [Message(role="user", content="Hello")],
        "anthropic/claude-sonnet-4.6",
        prompt_segments=[],
    )

    assert rendered == [{"role": "user", "content": "Hello"}]


@pytest.mark.asyncio
async def test_chat_completion_uses_top_level_cache_control_for_anthropic_automatic_caching(monkeypatch):
    calls: list[dict[str, object]] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            calls.append(json)
            request = httpx.Request("POST", f"https://example.test{url}")
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
                },
            )

    monkeypatch.setattr(llm_module, "_build_client", lambda: FakeClient())

    response = await llm_module.chat_completion(
        [Message(role="user", content="Hello")],
        model="anthropic/claude-sonnet-4.6",
        prompt_segments=[PromptSegment(role="user", text="Hello")],
    )

    assert response.error is None
    assert calls[0]["messages"] == [{"role": "user", "content": "Hello"}]
    assert calls[0]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_chat_completion_omits_top_level_cache_control_when_using_explicit_breakpoints(monkeypatch):
    calls: list[dict[str, object]] = []
    stable_block = "A" * 5000

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            calls.append(json)
            request = httpx.Request("POST", f"https://example.test{url}")
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
                },
            )

    monkeypatch.setattr(llm_module, "_build_client", lambda: FakeClient())

    response = await llm_module.chat_completion(
        [Message(role="user", content=stable_block + "\nQuestion")],
        model="anthropic/claude-sonnet-4.6",
        prompt_segments=[
            PromptSegment(role="user", text=stable_block, cache_candidate=True, stable=True),
            PromptSegment(role="user", text="\nQuestion"),
        ],
    )

    assert response.error is None
    assert "cache_control" not in calls[0]
    content = calls[0]["messages"][0]["content"]
    assert isinstance(content, list)
    assert any(isinstance(block, dict) and block.get("cache_control") for block in content)


def test_prompt_metrics_track_stable_prefix_changes():
    llm_module.reset_prompt_session_tracking()

    first = llm_module.ensure_prompt_metrics(
        messages=[
            Message(role="system", content="Stable instructions"),
            Message(role="user", content="Question one"),
        ],
        model="openai/gpt-4.1",
        session_id="session-prefix",
    )
    second = llm_module.ensure_prompt_metrics(
        messages=[
            Message(role="system", content="Stable instructions"),
            Message(role="user", content="Question two"),
        ],
        model="openai/gpt-4.1",
        session_id="session-prefix",
    )
    third = llm_module.ensure_prompt_metrics(
        messages=[
            Message(role="system", content="Updated instructions"),
            Message(role="user", content="Question three"),
        ],
        model="openai/gpt-4.1",
        session_id="session-prefix",
    )

    assert first.cache_break_detected is False
    assert second.cache_break_detected is False
    assert third.cache_break_detected is True
    assert "stable_prefix_changed" in third.cache_break_reasons


def test_session_cache_status_reports_cold_after_ttl():
    llm_module.reset_prompt_session_tracking()
    llm_module.ensure_prompt_metrics(
        messages=[Message(role="user", content="Hello")],
        model="openai/gpt-4.1",
        session_id="session-cold",
    )

    llm_module._PROMPT_SESSION_STATES["session-cold"].recorded_at = datetime.now(timezone.utc) - timedelta(seconds=301)
    status = llm_module.get_session_prompt_cache_status("session-cold", "openai/gpt-4.1")

    assert status.cache_cold is True
    assert status.seconds_since_last_request is not None
    assert status.seconds_since_last_request >= 301


def test_prompt_session_tracking_prunes_oldest_entries_when_limit_exceeded(monkeypatch):
    llm_module.reset_prompt_session_tracking()
    monkeypatch.setattr(llm_module, "_PROMPT_SESSION_STATE_LIMIT", 2)

    llm_module.ensure_prompt_metrics(
        messages=[Message(role="user", content="First")],
        model="openai/gpt-4.1",
        session_id="session-1",
    )
    llm_module.ensure_prompt_metrics(
        messages=[Message(role="user", content="Second")],
        model="openai/gpt-4.1",
        session_id="session-2",
    )
    llm_module._PROMPT_SESSION_STATES["session-1"].recorded_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    llm_module._PROMPT_SESSION_STATES["session-2"].recorded_at = datetime.now(timezone.utc) - timedelta(seconds=5)

    llm_module.ensure_prompt_metrics(
        messages=[Message(role="user", content="Third")],
        model="openai/gpt-4.1",
        session_id="session-3",
    )

    assert set(llm_module._PROMPT_SESSION_STATES) == {"session-2", "session-3"}


@pytest.mark.asyncio
async def test_chat_completion_retries_retryable_http_errors(monkeypatch):
    llm_module.reset_prompt_session_tracking()
    calls = {"count": 0}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            calls["count"] += 1
            request = httpx.Request("POST", f"https://example.test{url}")
            if calls["count"] == 1:
                return httpx.Response(429, request=request, json={"error": "rate limited"})
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
                },
            )

    monkeypatch.setattr(llm_module, "_build_client", lambda: FakeClient())
    monkeypatch.setattr(llm_module.settings, "llm_transport_retry_attempts", 1)
    monkeypatch.setattr(llm_module.settings, "llm_transport_retry_base_delay_ms", 1)

    response = await llm_module.chat_completion(
        [Message(role="user", content="Say ok")],
        model="openai/gpt-4.1",
        session_id="retry-session",
    )

    assert response.error is None
    assert response.content == "ok"
    assert response.prompt_metrics is not None
    assert response.prompt_metrics.retry_count == 1
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_chat_completion_retries_empty_message_content(monkeypatch):
    llm_module.reset_prompt_session_tracking()
    calls = {"count": 0}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            calls["count"] += 1
            request = httpx.Request("POST", f"https://example.test{url}")
            if calls["count"] == 1:
                return httpx.Response(
                    200,
                    request=request,
                    json={
                        "choices": [{"message": {"content": None}}],
                        "usage": {"prompt_tokens": 12, "completion_tokens": 0, "total_tokens": 12},
                    },
                )
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
                },
            )

    monkeypatch.setattr(llm_module, "_build_client", lambda: FakeClient())
    monkeypatch.setattr(llm_module.settings, "llm_transport_retry_attempts", 1)
    monkeypatch.setattr(llm_module.settings, "llm_transport_retry_base_delay_ms", 1)

    response = await llm_module.chat_completion(
        [Message(role="user", content="Say ok")],
        model="moonshotai/kimi-k2.6",
        session_id="empty-content-retry",
    )

    assert response.error is None
    assert response.content == "ok"
    assert response.prompt_metrics is not None
    assert response.prompt_metrics.retry_count == 1
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_chat_completion_sends_tools_and_accepts_tool_only_response(monkeypatch):
    calls: list[dict[str, object]] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            calls.append(json)
            request = httpx.Request("POST", f"https://example.test{url}")
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "file_read",
                                            "arguments": '{"path":"README.md"}',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
                },
            )

    monkeypatch.setattr(llm_module, "_build_client", lambda: FakeClient())

    tools = [
        {
            "type": "function",
            "function": {
                "name": "file_read",
                "description": "Read a file.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        }
    ]

    response = await llm_module.chat_completion(
        [Message(role="user", content="Read the README")],
        model="moonshotai/kimi-k2.6",
        tools=tools,
        tool_choice="auto",
        session_id="tool-call-response",
    )

    assert response.error is None
    assert response.content == ""
    assert response.finish_reason == "tool_calls"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id == "call_1"
    assert response.tool_calls[0].function.name == "file_read"
    assert response.tool_calls[0].function.arguments == '{"path":"README.md"}'
    assert calls[0]["tools"] == tools
    assert calls[0]["tool_choice"] == "auto"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_chat_completion_structured_falls_back_when_provider_rejects_json_schema(monkeypatch):
    llm_module.reset_prompt_session_tracking()
    calls: list[dict[str, object]] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            calls.append(json)
            request = httpx.Request("POST", f"https://example.test{url}")
            if len(calls) == 1:
                return httpx.Response(
                    400,
                    request=request,
                    json={"error": {"message": "response_format json_schema is not supported for this model"}},
                )
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [{"message": {"content": '{"kind":"final"}'}}],
                    "usage": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11},
                },
            )

    monkeypatch.setattr(llm_module, "_build_client", lambda: FakeClient())

    payload, response = await llm_module.chat_completion_structured(
        [Message(role="user", content="Return a final response")],
        model="anthropic/claude-sonnet-4.5",
        schema_name="agent_decision",
        json_schema={
            "type": "object",
            "properties": {"kind": {"type": "string"}},
            "required": ["kind"],
            "additionalProperties": False,
        },
        session_id="structured-fallback",
    )

    assert payload == {"kind": "final"}
    assert response.error is None
    assert len(calls) == 2
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]


@pytest.mark.asyncio
async def test_chat_completion_structured_falls_back_when_provider_rejects_schema_keyword(monkeypatch):
    llm_module.reset_prompt_session_tracking()
    calls: list[dict[str, object]] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            calls.append(json)
            request = httpx.Request("POST", f"https://example.test{url}")
            if len(calls) == 1:
                return httpx.Response(
                    400,
                    request=request,
                    json={
                        "error": {
                            "message": "Provider returned error",
                            "metadata": {
                                "raw": '{"type":"error","error":{"type":"invalid_request_error","message":"output_config.format.schema: For \'array\' type, property \'maxItems\' is not supported"}}',
                            },
                        }
                    },
                )
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [{"message": {"content": '{"kind":"ask_user","question":"Proceed?"}'}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
                },
            )

    monkeypatch.setattr(llm_module, "_build_client", lambda: FakeClient())

    payload, response = await llm_module.chat_completion_structured(
        [Message(role="user", content="Return a question as JSON")],
        model="anthropic/claude-sonnet-4.5",
        schema_name="agent_decision",
        json_schema={
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "steps": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
            },
            "required": ["kind"],
            "additionalProperties": False,
        },
        session_id="structured-keyword-fallback",
    )

    assert payload == {"kind": "ask_user", "question": "Proceed?"}
    assert response.error is None
    assert len(calls) == 2
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]


@pytest.mark.asyncio
async def test_chat_completion_structured_retries_malformed_json_response(monkeypatch):
    llm_module.reset_prompt_session_tracking()
    calls = {"count": 0}

    async def fake_completion(messages, model, temperature=0.2, max_tokens=4096, response_format=None, session_id=None, prompt_segments=None):
        calls["count"] += 1
        content = '{"kind":"tool","tool":"file_write","arguments":{"path":"demo.txt","content":"unterminated}'
        if calls["count"] == 2:
            content = '{"kind":"final","summary":"Recovered","result":"ok"}'
        return ModelResponse(
            model=model,
            content=content,
            usage={"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
        )

    monkeypatch.setattr(llm_module, "chat_completion", fake_completion)

    payload, response = await llm_module.chat_completion_structured(
        [Message(role="user", content="Return valid JSON")],
        model="moonshotai/kimi-k2.6",
        schema_name="agent_decision",
        json_schema={
            "type": "object",
            "properties": {"kind": {"type": "string"}},
            "required": ["kind"],
            "additionalProperties": True,
        },
        session_id="structured-malformed-json-retry",
    )

    assert payload == {"kind": "final", "summary": "Recovered", "result": "ok"}
    assert response.error is None
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_chat_completion_stream_handles_unread_http_error_response(monkeypatch):
    class FakeStreamContext:
        async def __aenter__(self):
            request = httpx.Request("POST", "https://example.test/chat/completions")
            response = httpx.Response(400, request=request)
            raise httpx.HTTPStatusError("bad request", request=request, response=response)

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, json):
            return FakeStreamContext()

    monkeypatch.setattr(llm_module, "_build_client", lambda: FakeClient())
    monkeypatch.setattr(llm_module.settings, "llm_transport_retry_attempts", 0)

    with pytest.raises(RuntimeError, match="OpenRouter stream HTTP error: 400"):
        async for _chunk in llm_module.chat_completion_stream(
            [Message(role="user", content="Say hello")],
            model="anthropic/claude-sonnet-4.5",
        ):
            pass