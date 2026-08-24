from __future__ import annotations

import asyncio

import httpx
import pytest

import app.services.llm as llm_module
from app.config import settings
from app.models.chat import Message
from app.services.llm import (
    PromptCachePolicy,
    LocalLLMPriorityGate,
    _build_chat_completion_payload,
    _build_client,
    _extract_message_reasoning,
    is_local_model,
    native_decision_max_tokens,
    resolve_prompt_cache_policy,
    resolve_upstream_model,
    supports_native_tool_calls,
)


def test_llama_cpp_reasoning_content_is_preserved() -> None:
    reasoning, details = _extract_message_reasoning(
        {"choices": [{"message": {"content": "", "reasoning_content": "considering options"}}]}
    )

    assert reasoning == "considering options"
    assert details == []


def test_local_alias_resolves_to_native_tool_model_with_short_budget() -> None:
    alias = settings.local_llm_alias

    assert is_local_model(alias)
    assert resolve_upstream_model(alias) == settings.local_llm_model
    assert supports_native_tool_calls(alias)
    assert native_decision_max_tokens(alias) == 1024


def test_local_provider_disables_openrouter_prompt_cache_controls() -> None:
    policy = resolve_prompt_cache_policy(settings.local_llm_alias)

    assert policy.strategy == "disabled"
    assert policy.top_level_cache_control is None


def test_local_payload_uses_upstream_checkpoint_id() -> None:
    payload = _build_chat_completion_payload(
        rendered_messages=[{"role": "user", "content": "hello"}],
        model=settings.local_llm_alias,
        temperature=0.2,
        max_tokens=128,
        cache_policy=PromptCachePolicy(strategy="disabled"),
    )

    assert payload["model"] == settings.local_llm_model
    assert "cache_control" not in payload


@pytest.mark.asyncio
async def test_local_chat_completion_routes_through_compatible_server(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            calls.append(json)
            return httpx.Response(
                200,
                request=httpx.Request("POST", f"https://example.test{url}"),
                json={"choices": [{"message": {"content": "ready"}}]},
            )

    monkeypatch.setattr(llm_module, "_build_client", lambda: FakeClient())

    response = await llm_module.chat_completion(
        [Message(role="user", content="hello")],
        model=settings.local_llm_alias,
        max_tokens=32,
    )

    assert response.error is None
    assert response.content == "ready"
    assert calls[0]["model"] == settings.local_llm_model


def test_local_client_uses_loopback_base_url_and_local_auth(monkeypatch) -> None:
    monkeypatch.setattr(settings, "local_llm_base_url", "http://127.0.0.1:9090/v1")
    monkeypatch.setattr(settings, "local_llm_api_key", "test-local-key")
    token = llm_module._CURRENT_REQUEST_MODEL.set(settings.local_llm_alias)
    try:
        client = _build_client()
        assert str(client.base_url).rstrip("/") == "http://127.0.0.1:9090/v1"
        assert client.headers["Authorization"] == "Bearer test-local-key"
        assert "HTTP-Referer" not in client.headers
    finally:
        llm_module._CURRENT_REQUEST_MODEL.reset(token)
        # The client has no requests in this unit test, but still owns a
        # connection pool that should be released explicitly.
        import asyncio

        asyncio.run(client.aclose())


def test_cloud_client_remains_openrouter(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openrouter_base_url", "https://router.example/v1")
    monkeypatch.setattr(settings, "openrouter_api_key", "test-cloud-key")
    token = llm_module._CURRENT_REQUEST_MODEL.set("openai/gpt-5.6-luna")
    try:
        client = _build_client()
        assert str(client.base_url).rstrip("/") == "https://router.example/v1"
        assert client.headers["Authorization"] == "Bearer test-cloud-key"
        assert client.headers["HTTP-Referer"] == "https://ai-cockpit.local"
    finally:
        llm_module._CURRENT_REQUEST_MODEL.reset(token)
        import asyncio

        asyncio.run(client.aclose())


@pytest.mark.asyncio
async def test_local_priority_gate_promotes_interactive_waiters() -> None:
    gate = LocalLLMPriorityGate(max_concurrency=1)
    release_background = asyncio.Event()
    order: list[str] = []

    async def background() -> None:
        async with gate.slot("background"):
            order.append("background-start")
            await release_background.wait()
            order.append("background-end")

    async def interactive() -> None:
        async with gate.slot("interactive"):
            order.append("interactive")

    background_task = asyncio.create_task(background())
    await asyncio.sleep(0)
    background_two = asyncio.create_task(_record_slot(gate, "background", order))
    interactive_task = asyncio.create_task(interactive())
    await asyncio.sleep(0)
    release_background.set()
    await asyncio.gather(background_task, interactive_task, background_two)

    assert order == ["background-start", "background-end", "interactive", "background"]


async def _record_slot(gate: LocalLLMPriorityGate, priority: str, order: list[str]) -> None:
    async with gate.slot(priority):
        order.append(priority)
