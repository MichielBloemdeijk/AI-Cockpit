"""Provider-neutral OpenAI-compatible LLM service."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import inspect
from dataclasses import dataclass
from decimal import Decimal
from math import ceil
from typing import Any, Awaitable, Callable, List

import httpx

from app.config import settings
from app.models.chat import Message, ModelResponse, PromptMetrics, ToolCall, ToolCallFunction

logger = logging.getLogger(__name__)

OPENROUTER_HEADERS = {
    "HTTP-Referer": "https://ai-cockpit.local",
    "X-Title": "AI Cockpit",
}



@dataclass(frozen=True, slots=True)
class ProviderProfile:
    """How one OpenRouter provider family caches prompts."""

    cache_strategy: str
    explicit_segment_cache: bool = False


# Keyed by the model's provider prefix (`anthropic/...`). Unlisted providers keep their defaults.
PROVIDER_PROFILES = {
    "anthropic": ProviderProfile("anthropic_explicit_top_level", explicit_segment_cache=True),
    "google": ProviderProfile("implicit_prefix_stability", explicit_segment_cache=True),
    "openai": ProviderProfile("implicit_prefix_stability"),
    "deepseek": ProviderProfile("implicit_prefix_stability"),
    "x-ai": ProviderProfile("implicit_prefix_stability"),
    "moonshot": ProviderProfile("implicit_prefix_stability"),
    "groq": ProviderProfile("implicit_prefix_stability"),
}
DEFAULT_PROVIDER_PROFILE = ProviderProfile("provider_default")


@dataclass(frozen=True, slots=True)
class PromptCachePolicy:
    strategy: str
    top_level_cache_control: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PromptSegment:
    role: str
    text: str
    cache_candidate: bool = False
    stable: bool = False


@dataclass(frozen=True, slots=True)
class SessionPromptCacheStatus:
    ttl_seconds: int
    seconds_since_last_request: int | None = None
    cache_cold: bool = False


@dataclass(slots=True)
class _PromptSessionState:
    model: str
    cache_strategy: str
    prompt_fingerprint: str
    prompt_prefix_fingerprint: str
    recorded_at: datetime


_PROMPT_SESSION_STATES: dict[str, _PromptSessionState] = {}
_PROMPT_SESSION_STATE_LIMIT = 256


class LocalLLMPriorityGate:
    """Serialize local generations while letting interactive turns jump the queue."""

    def __init__(self, max_concurrency: int = 1) -> None:
        self.max_concurrency = max(1, int(max_concurrency or 1))
        self._condition = asyncio.Condition()
        self._active = 0
        self._sequence = 0
        self._waiters: list[tuple[int, int, asyncio.Event]] = []

    @staticmethod
    def _priority_rank(priority: str) -> int:
        return 0 if str(priority or "").strip().lower() in {"interactive", "voice", "urgent"} else 1

    @asynccontextmanager
    async def slot(self, priority: str = "interactive"):
        waiter = asyncio.Event()
        entry: tuple[int, int, asyncio.Event] | None = None
        acquired = False
        async with self._condition:
            entry = (self._priority_rank(priority), self._sequence, waiter)
            self._sequence += 1
            self._waiters.append(entry)
            self._waiters.sort(key=lambda item: (item[0], item[1]))
            try:
                while self._active >= self.max_concurrency or self._waiters[0][2] is not waiter:
                    await self._condition.wait()
                self._waiters.pop(0)
                self._active += 1
                acquired = True
            except BaseException:
                if entry in self._waiters:
                    self._waiters.remove(entry)
                self._condition.notify_all()
                raise

        try:
            yield
        finally:
            if acquired:
                async with self._condition:
                    self._active -= 1
                    self._condition.notify_all()


_LOCAL_LLM_PRIORITY_GATE = LocalLLMPriorityGate(settings.local_llm_max_concurrency)


def _prompt_content_chars(messages: List[Message]) -> int:
    return sum(len(message.content) for message in messages)


def _estimate_prompt_tokens(prompt_bytes: int) -> int:
    if prompt_bytes <= 0:
        return 0
    return ceil(prompt_bytes / 4)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _prune_prompt_session_states(*, now: datetime | None = None) -> None:
    if not _PROMPT_SESSION_STATES:
        return

    current = now or _utc_now()
    expired_session_ids = [
        session_id
        for session_id, state in _PROMPT_SESSION_STATES.items()
        if prompt_cache_ttl_seconds(state.model) > 0
        and (current - state.recorded_at).total_seconds() >= prompt_cache_ttl_seconds(state.model)
    ]
    for session_id in expired_session_ids:
        _PROMPT_SESSION_STATES.pop(session_id, None)

    overflow = len(_PROMPT_SESSION_STATES) - _PROMPT_SESSION_STATE_LIMIT
    if overflow <= 0:
        return

    oldest_sessions = sorted(
        _PROMPT_SESSION_STATES.items(),
        key=lambda item: item[1].recorded_at,
    )
    for session_id, _state in oldest_sessions[:overflow]:
        _PROMPT_SESSION_STATES.pop(session_id, None)


def _fingerprint_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _prefix_rendered_messages(serialized_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prefix: list[dict[str, Any]] = []
    for message in serialized_messages:
        if str(message.get("role") or "") != "system":
            break
        prefix.append(message)
    return prefix


def _shape_descriptor(serialized_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    descriptor: list[dict[str, Any]] = []
    for message in serialized_messages:
        content = message.get("content")
        if isinstance(content, list):
            parts = []
            total_chars = 0
            for block in content:
                if not isinstance(block, dict):
                    continue
                text = str(block.get("text") or "")
                total_chars += len(text)
                parts.append({
                    "type": str(block.get("type") or "text"),
                    "text_len": len(text),
                    "cache_control": bool(block.get("cache_control")),
                })
            descriptor.append({
                "role": str(message.get("role") or ""),
                "content_type": "blocks",
                "parts": parts,
                "text_len": total_chars,
            })
        else:
            text = str(content or "")
            descriptor.append({
                "role": str(message.get("role") or ""),
                "content_type": "text",
                "text_len": len(text),
            })
    return descriptor


def prompt_cache_ttl_seconds(model: str) -> int:
    cache_policy = resolve_prompt_cache_policy(model)
    if cache_policy.strategy == "disabled":
        return 0
    return 3600 if settings.openrouter_prompt_cache_ttl == "1h" else 300


def get_session_prompt_cache_status(session_id: str | None, model: str) -> SessionPromptCacheStatus:
    ttl_seconds = prompt_cache_ttl_seconds(model)
    if not session_id or ttl_seconds <= 0:
        return SessionPromptCacheStatus(ttl_seconds=ttl_seconds)

    state = _PROMPT_SESSION_STATES.get(session_id)
    if state is None:
        return SessionPromptCacheStatus(ttl_seconds=ttl_seconds)

    seconds_since = max(0, int((_utc_now() - state.recorded_at).total_seconds()))
    return SessionPromptCacheStatus(
        ttl_seconds=ttl_seconds,
        seconds_since_last_request=seconds_since,
        cache_cold=seconds_since >= ttl_seconds,
    )


def reset_prompt_session_tracking() -> None:
    _PROMPT_SESSION_STATES.clear()


def _remember_prompt_session(
    *,
    session_id: str | None,
    model: str,
    cache_strategy: str,
    prompt_fingerprint: str | None,
    prompt_prefix_fingerprint: str | None,
) -> None:
    if not session_id or not prompt_fingerprint or not prompt_prefix_fingerprint:
        return
    _prune_prompt_session_states()
    _PROMPT_SESSION_STATES[session_id] = _PromptSessionState(
        model=model,
        cache_strategy=cache_strategy,
        prompt_fingerprint=prompt_fingerprint,
        prompt_prefix_fingerprint=prompt_prefix_fingerprint,
        recorded_at=_utc_now(),
    )
    _prune_prompt_session_states()


def _is_retryable_http_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429} or 500 <= status_code < 600


def _retry_delay_seconds(retry_index: int) -> float:
    base_delay = max(100, settings.llm_transport_retry_base_delay_ms) / 1000
    return base_delay * (2 ** retry_index)


def _safe_response_text(response: httpx.Response) -> str:
    try:
        return response.text
    except httpx.ResponseNotRead:
        return ""


def _extract_message_content(data: dict[str, Any]) -> str | None:
    message = _extract_choice_message(data)
    if message is None:
        return None

    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        combined = "".join(parts)
        return combined or None
    return None


def _extract_reasoning_details_blocks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _reasoning_text_from_details(details: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in details:
        item_type = str(item.get("type") or "").strip().lower()
        if item_type == "reasoning.encrypted":
            continue
        if item_type == "reasoning.summary":
            summary = item.get("summary")
            if isinstance(summary, str) and summary.strip():
                parts.append(summary.strip())
            continue
        if item_type == "reasoning.text":
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
            continue
        for key in ("text", "summary"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
                break
    return "\n\n".join(parts).strip()


def _extract_message_reasoning(data: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    message = _extract_choice_message(data)
    if message is None:
        return "", []

    raw_reasoning = message.get("reasoning")
    if not isinstance(raw_reasoning, str) or not raw_reasoning.strip():
        # llama.cpp's OpenAI-compatible endpoint uses reasoning_content for
        # extracted thinking, while OpenRouter commonly uses reasoning or
        # reasoning_details.  Accept all three representations.
        raw_reasoning = message.get("reasoning_content")
    reasoning = raw_reasoning.strip() if isinstance(raw_reasoning, str) else ""
    reasoning_details = _extract_reasoning_details_blocks(message.get("reasoning_details"))
    if not reasoning:
        reasoning = _reasoning_text_from_details(reasoning_details)
    return reasoning, reasoning_details


def _extract_first_choice(data: dict[str, Any]) -> dict[str, Any] | None:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return None
    return first_choice


def _extract_choice_message(data: dict[str, Any]) -> dict[str, Any] | None:
    first_choice = _extract_first_choice(data)
    if first_choice is None:
        return None
    message = first_choice.get("message")
    if not isinstance(message, dict):
        return None
    return message


def _extract_tool_calls(data: dict[str, Any]) -> list[ToolCall]:
    message = _extract_choice_message(data)
    if message is None:
        return []

    raw_calls = message.get("tool_calls")
    if not isinstance(raw_calls, list):
        return []

    tool_calls: list[ToolCall] = []
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict):
            continue
        function = raw_call.get("function")
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            rendered_arguments = arguments
        elif arguments is None:
            rendered_arguments = ""
        else:
            rendered_arguments = json.dumps(arguments, ensure_ascii=False)
        tool_calls.append(
            ToolCall(
                id=None if raw_call.get("id") in (None, "") else str(raw_call.get("id")),
                type=str(raw_call.get("type") or "function"),
                function=ToolCallFunction(name=name, arguments=rendered_arguments),
            )
        )
    return tool_calls


def _extract_finish_reason(data: dict[str, Any]) -> str | None:
    first_choice = _extract_first_choice(data)
    if first_choice is None:
        return None
    finish_reason = first_choice.get("finish_reason")
    if finish_reason in (None, ""):
        return None
    return str(finish_reason)


def supports_explicit_segment_cache(model: str) -> bool:
    return provider_profile(model).explicit_segment_cache


def _segment_cache_control() -> dict[str, Any]:
    cache_control: dict[str, Any] = {"type": "ephemeral"}
    if settings.openrouter_prompt_cache_ttl == "1h":
        cache_control["ttl"] = "1h"
    return cache_control


def render_prompt_segments(segments: list[PromptSegment], model: str) -> list[dict[str, Any]]:
    if not segments:
        return []

    if not supports_explicit_segment_cache(model):
        rendered: list[dict[str, Any]] = []
        current_role = segments[0].role
        current_parts: list[str] = []
        for segment in segments:
            if segment.role != current_role:
                rendered.append({"role": current_role, "content": "".join(current_parts)})
                current_role = segment.role
                current_parts = []
            current_parts.append(segment.text)
        rendered.append({"role": current_role, "content": "".join(current_parts)})
        return rendered

    eligible_indexes = [
        index
        for index, segment in enumerate(segments)
        if segment.cache_candidate
        and segment.stable
        and len(segment.text.encode("utf-8")) >= settings.openrouter_explicit_cache_breakpoint_min_bytes
    ]
    explicit_index = eligible_indexes[-1] if eligible_indexes else None

    rendered_messages: list[dict[str, Any]] = []
    current_role = segments[0].role
    current_blocks: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        if segment.role != current_role:
            rendered_messages.append({"role": current_role, "content": current_blocks})
            current_role = segment.role
            current_blocks = []
        block: dict[str, Any] = {"type": "text", "text": segment.text}
        if explicit_index is not None and index == explicit_index:
            block["cache_control"] = _segment_cache_control()
        current_blocks.append(block)
    rendered_messages.append({"role": current_role, "content": current_blocks})
    return rendered_messages


def _rendered_messages_have_explicit_cache_control(rendered_messages: list[dict[str, Any]]) -> bool:
    for message in rendered_messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("cache_control"):
                return True
    return False


def render_prompt_messages(
    messages: List[Message],
    model: str,
    *,
    prompt_segments: list[PromptSegment] | None = None,
) -> list[dict[str, Any]]:
    if prompt_segments:
        rendered = render_prompt_segments(prompt_segments, model)
        if model_provider_prefix(model) == "anthropic" and not _rendered_messages_have_explicit_cache_control(rendered):
            return [message.model_dump() for message in messages]
        return rendered
    return [message.model_dump() for message in messages]


def build_prompt_metrics(
    *,
    messages: List[Message],
    model: str,
    cache_policy: PromptCachePolicy,
    response_format: dict[str, Any] | None,
    session_id: str | None,
    usage: dict[str, Any] | None = None,
    rendered_messages: list[dict[str, Any]] | None = None,
    retry_count: int = 0,
) -> PromptMetrics:
    serialized_messages = rendered_messages if rendered_messages is not None else [message.model_dump() for message in messages]
    prompt_json = json.dumps(serialized_messages, ensure_ascii=False, separators=(",", ":"))
    prompt_prefix_json = json.dumps(_prefix_rendered_messages(serialized_messages), ensure_ascii=False, separators=(",", ":"))
    shape_json = json.dumps(_shape_descriptor(serialized_messages), ensure_ascii=False, separators=(",", ":"))
    prompt_bytes = len(prompt_json.encode("utf-8"))
    prompt_chars = _prompt_content_chars(messages)
    prompt_tokens_details = usage.get("prompt_tokens_details") if isinstance(usage, dict) else None
    prompt_fingerprint = _fingerprint_text(prompt_json)
    prompt_prefix_fingerprint = _fingerprint_text(prompt_prefix_json + shape_json)
    cache_status = get_session_prompt_cache_status(session_id, model)
    previous_state = None if not session_id else _PROMPT_SESSION_STATES.get(session_id)
    cache_break_reasons: list[str] = []
    if previous_state is not None:
        if previous_state.model != model:
            cache_break_reasons.append("model_changed")
        if previous_state.cache_strategy != cache_policy.strategy:
            cache_break_reasons.append("cache_strategy_changed")
        if previous_state.prompt_prefix_fingerprint != prompt_prefix_fingerprint:
            cache_break_reasons.append("stable_prefix_changed")
        if cache_status.cache_cold:
            cache_break_reasons.append("cache_ttl_expired")

    metrics = PromptMetrics(
        provider_family=model_provider_prefix(model),
        cache_strategy=cache_policy.strategy,
        explicit_cache_control=cache_policy.top_level_cache_control is not None,
        cache_ttl=None if not cache_policy.top_level_cache_control else cache_policy.top_level_cache_control.get("ttl", "5m"),
        session_id=session_id,
        response_format_type=None if not response_format else str(response_format.get("type") or "unknown"),
        message_count=len(messages),
        system_message_count=sum(1 for message in messages if message.role == "system"),
        user_message_count=sum(1 for message in messages if message.role == "user"),
        assistant_message_count=sum(1 for message in messages if message.role == "assistant"),
        prompt_chars=prompt_chars,
        prompt_bytes=prompt_bytes,
        estimated_prompt_tokens=_estimate_prompt_tokens(prompt_bytes),
        cached_tokens=None if not isinstance(prompt_tokens_details, dict) else int(prompt_tokens_details.get("cached_tokens", 0) or 0),
        cache_write_tokens=None if not isinstance(prompt_tokens_details, dict) else int(prompt_tokens_details.get("cache_write_tokens", 0) or 0),
        prompt_tokens=None if not isinstance(usage, dict) else usage.get("prompt_tokens"),
        completion_tokens=None if not isinstance(usage, dict) else usage.get("completion_tokens"),
        total_tokens=None if not isinstance(usage, dict) else usage.get("total_tokens"),
        prompt_fingerprint=prompt_fingerprint,
        prompt_prefix_fingerprint=prompt_prefix_fingerprint,
        cache_break_detected=bool(cache_break_reasons),
        cache_break_reasons=cache_break_reasons,
        cache_cold=cache_status.cache_cold,
        time_since_previous_request_seconds=cache_status.seconds_since_last_request,
        retry_count=retry_count,
    )
    _remember_prompt_session(
        session_id=session_id,
        model=model,
        cache_strategy=cache_policy.strategy,
        prompt_fingerprint=metrics.prompt_fingerprint,
        prompt_prefix_fingerprint=metrics.prompt_prefix_fingerprint,
    )
    return metrics


def ensure_prompt_metrics(
    *,
    messages: List[Message],
    model: str,
    session_id: str | None,
    response_format: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    prompt_metrics: PromptMetrics | None = None,
    rendered_messages: list[dict[str, Any]] | None = None,
) -> PromptMetrics:
    if prompt_metrics is not None:
        return prompt_metrics
    return build_prompt_metrics(
        messages=messages,
        model=model,
        cache_policy=resolve_prompt_cache_policy(model),
        response_format=response_format,
        session_id=session_id,
        usage=usage,
        rendered_messages=rendered_messages,
    )


def model_provider_prefix(model: str) -> str:
    prefix, _, _ = model.partition("/")
    return prefix.strip().lower()


def is_local_model(model: str) -> bool:
    normalized = str(model or "").strip().lower()
    alias = settings.local_llm_alias.strip().lower()
    return bool(alias and normalized == alias) or normalized.startswith("local/")


def resolve_upstream_model(model: str) -> str:
    """Map a public model alias to the ID expected by its provider."""
    if is_local_model(model):
        return settings.local_llm_model
    return model


def provider_profile(model: str) -> ProviderProfile:
    return PROVIDER_PROFILES.get(model_provider_prefix(model), DEFAULT_PROVIDER_PROFILE)


def provider_label(model: str) -> str:
    return "local model server" if is_local_model(model) else "OpenRouter"


def resolve_prompt_cache_policy(model: str) -> PromptCachePolicy:
    if is_local_model(model):
        return PromptCachePolicy(strategy="disabled")
    if not settings.openrouter_prompt_caching_enabled:
        return PromptCachePolicy(strategy="disabled")

    strategy = provider_profile(model).cache_strategy
    if strategy == "anthropic_explicit_top_level":
        return PromptCachePolicy(strategy=strategy, top_level_cache_control=_segment_cache_control())
    return PromptCachePolicy(strategy=strategy)


def _build_client(model: str) -> httpx.AsyncClient:
    if is_local_model(model):
        if not settings.local_llm_enabled:
            raise RuntimeError("The local LLM provider is disabled")
        return httpx.AsyncClient(
            base_url=settings.local_llm_base_url,
            headers={
                "Authorization": f"Bearer {settings.local_llm_api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(120.0, connect=10.0),
        )
    return httpx.AsyncClient(
        base_url=settings.openrouter_base_url,
        headers={
            **OPENROUTER_HEADERS,
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
        },
        timeout=httpx.Timeout(120.0, connect=10.0),
    )


@asynccontextmanager
async def _model_client(model: str, *, priority: str = "interactive"):
    if is_local_model(model):
        async with _LOCAL_LLM_PRIORITY_GATE.slot(priority):
            async with _build_client(model) as client:
                yield client
    else:
        async with _build_client(model) as client:
            yield client


def aggregate_usage(usages: list[dict[str, Any] | None]) -> dict[str, Any] | None:
    """Merge OpenRouter usage payloads exactly as returned by the API."""
    present = [usage for usage in usages if usage]
    if not present:
        return None

    aggregate: dict[str, Any] = {}
    numeric_fields = ("prompt_tokens", "completion_tokens", "total_tokens")
    decimal_fields = ("cost",)
    nested_fields = (
        "prompt_tokens_details",
        "completion_tokens_details",
        "cost_details",
        "server_tool_use",
    )

    for field in numeric_fields:
        total = sum(int(usage.get(field, 0) or 0) for usage in present)
        if total:
            aggregate[field] = total

    for field in decimal_fields:
        total = sum(Decimal(str(usage.get(field, 0) or 0)) for usage in present)
        if total:
            aggregate[field] = float(total)

    for field in nested_fields:
        combined: dict[str, Any] = {}
        for usage in present:
            nested = usage.get(field)
            if not isinstance(nested, dict):
                continue
            for key, value in nested.items():
                if isinstance(value, bool):
                    combined[key] = combined.get(key, False) or value
                elif isinstance(value, int):
                    combined[key] = int(combined.get(key, 0)) + value
                elif isinstance(value, float):
                    current = Decimal(str(combined.get(key, 0) or 0))
                    combined[key] = float(current + Decimal(str(value)))
                else:
                    combined[key] = value
        if combined:
            aggregate[field] = combined

    if any(usage.get("is_byok") for usage in present):
        aggregate["is_byok"] = True

    return aggregate


def _merge_request_overrides(payload: dict[str, Any], request_overrides: dict[str, Any] | None) -> dict[str, Any]:
    if not request_overrides:
        return payload

    merged = dict(payload)
    for key, value in request_overrides.items():
        if value is not None:
            merged[key] = value
    return merged


def _build_chat_completion_payload(
    *,
    rendered_messages: list[dict[str, Any]],
    model: str,
    temperature: float,
    max_tokens: int,
    cache_policy: PromptCachePolicy,
    response_format: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    session_id: str | None = None,
    request_overrides: dict[str, Any] | None = None,
    stream: bool = False,
) -> dict[str, Any]:
    payload = {
        # The public alias is useful to the rest of AI-Cockpit, but providers
        # receive the concrete model identifier they expose.  This keeps the
        # local llama.cpp alias stable even when its checkpoint changes.
        "model": resolve_upstream_model(model),
        "messages": rendered_messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if stream:
        payload["stream"] = True
    if cache_policy.top_level_cache_control is not None and not _rendered_messages_have_explicit_cache_control(rendered_messages):
        payload["cache_control"] = cache_policy.top_level_cache_control
    if response_format is not None:
        payload["response_format"] = response_format
    if tools:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    if session_id:
        payload["session_id"] = session_id
    return _merge_request_overrides(payload, request_overrides)


def _extract_stream_usage(chunk: dict[str, Any], choice: dict[str, Any] | None) -> dict[str, Any] | None:
    usage = chunk.get("usage")
    if isinstance(usage, dict):
        return usage
    if isinstance(choice, dict):
        choice_usage = choice.get("usage")
        if isinstance(choice_usage, dict):
            return choice_usage
    return None


def _extract_stream_content_parts(delta_content: Any) -> list[str]:
    if isinstance(delta_content, str):
        return [delta_content] if delta_content else []
    if isinstance(delta_content, dict):
        text = delta_content.get("text")
        return [text] if isinstance(text, str) and text else []
    if not isinstance(delta_content, list):
        return []

    parts: list[str] = []
    for item in delta_content:
        if isinstance(item, str) and item:
            parts.append(item)
            continue
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
    return parts


def _append_reasoning_detail_blocks(
    reasoning_state: list[dict[str, Any]],
    delta_reasoning_details: Any,
) -> tuple[bool, list[str]]:
    blocks = _extract_reasoning_details_blocks(delta_reasoning_details)
    if not blocks:
        return False, []

    observed = False
    text_deltas: list[str] = []
    for block in blocks:
        item_type = str(block.get("type") or "").strip().lower()
        index = block.get("index")
        text_key: str | None = None
        if item_type == "reasoning.text":
            text_key = "text"
        elif item_type == "reasoning.summary":
            text_key = "summary"
        elif item_type != "reasoning.encrypted":
            if isinstance(block.get("text"), str):
                text_key = "text"
            elif isinstance(block.get("summary"), str):
                text_key = "summary"

        if isinstance(index, int) and index >= 0:
            while len(reasoning_state) <= index:
                reasoning_state.append({})
            state = reasoning_state[index]
            for key, value in block.items():
                if key == text_key and isinstance(value, str) and value:
                    state[key] = str(state.get(key) or "") + value
                    text_deltas.append(value)
                    observed = True
                elif key != text_key:
                    state[key] = value
                    observed = True
            reasoning_state[index] = state
            continue

        reasoning_state.append(dict(block))
        if text_key and isinstance(block.get(text_key), str) and block.get(text_key):
            text_deltas.append(str(block.get(text_key)))
        observed = True
    return observed, text_deltas


def _materialize_stream_tool_call(raw_call: dict[str, Any]) -> ToolCall | None:
    if not isinstance(raw_call, dict):
        return None
    function = raw_call.get("function")
    if not isinstance(function, dict):
        return None
    name = str(function.get("name") or "").strip()
    if not name:
        return None
    arguments = function.get("arguments")
    rendered_arguments = arguments if isinstance(arguments, str) else ""
    return ToolCall(
        id=str(raw_call.get("id") or "").strip() or None,
        type=str(raw_call.get("type") or "function"),
        function=ToolCallFunction(name=name, arguments=rendered_arguments),
    )


def _collect_stream_tool_calls(
    tool_call_state: list[dict[str, Any]],
    delta_tool_calls: Any,
) -> tuple[bool, list[ToolCall]]:
    if not isinstance(delta_tool_calls, list):
        return False, []

    observed = False
    updated_calls: list[ToolCall] = []
    for raw_call in delta_tool_calls:
        if not isinstance(raw_call, dict):
            continue
        index = raw_call.get("index")
        if not isinstance(index, int) or index < 0:
            continue
        while len(tool_call_state) <= index:
            tool_call_state.append({})
        state = tool_call_state[index]
        call_id = raw_call.get("id")
        if isinstance(call_id, str) and call_id:
            state["id"] = call_id
            observed = True
        call_type = raw_call.get("type")
        if isinstance(call_type, str) and call_type:
            state["type"] = call_type
            observed = True
        function = raw_call.get("function")
        if not isinstance(function, dict):
            tool_call_state[index] = state
            continue
        state_function = state.setdefault("function", {})
        name = function.get("name")
        if isinstance(name, str) and name:
            state_function["name"] = name
            observed = True
        arguments = function.get("arguments")
        if isinstance(arguments, str) and arguments:
            state_function["arguments"] = str(state_function.get("arguments") or "") + arguments
            observed = True
        tool_call_state[index] = state
        partial_call = _materialize_stream_tool_call(state)
        if partial_call is not None:
            updated_calls.append(partial_call)
    return observed, updated_calls


def _materialize_stream_tool_calls(tool_call_state: list[dict[str, Any]]) -> list[ToolCall]:
    tool_calls: list[ToolCall] = []
    for raw_call in tool_call_state:
        partial_call = _materialize_stream_tool_call(raw_call)
        if partial_call is not None:
            tool_calls.append(partial_call)
    return tool_calls


class _EmptyResponse(Exception):
    """The provider answered with no content, reasoning or tool call."""


@dataclass(slots=True)
class _ModelRequest:
    """One logical request: its rendered prompt, metrics inputs and retry state."""

    messages: List[Message]
    model: str
    cache_policy: PromptCachePolicy
    response_format: dict[str, Any] | None
    session_id: str | None
    rendered_messages: list[dict[str, Any]]
    retry_count: int = 0
    # A streamed attempt that already produced output is never retried.
    observed_output: bool = False

    def metrics(self, usage: dict[str, Any] | None = None) -> PromptMetrics:
        return build_prompt_metrics(
            messages=self.messages,
            model=self.model,
            cache_policy=self.cache_policy,
            response_format=self.response_format,
            session_id=self.session_id,
            usage=usage,
            rendered_messages=self.rendered_messages,
            retry_count=self.retry_count,
        )

    def failure(self, error: str) -> ModelResponse:
        return ModelResponse(model=self.model, content="", error=error, prompt_metrics=self.metrics())


async def _http_error_text(error: httpx.HTTPStatusError) -> str:
    try:
        response_text = (await error.response.aread()).decode("utf-8", errors="replace").strip()
    except Exception:
        response_text = _safe_response_text(error.response).strip()
    return f"{error}: {response_text}" if response_text else str(error)


async def _with_retries(
    request: _ModelRequest,
    attempt_once: Callable[[], Awaitable[ModelResponse]],
    *,
    kind: str,
) -> ModelResponse:
    """The one retry and error policy for every transport: retry empty answers, retryable
    HTTP statuses and transport errors with backoff; anything else becomes an error response."""
    label = provider_label(request.model)
    attempts = settings.llm_transport_retry_attempts
    for attempt in range(attempts + 1):
        request.observed_output = False
        can_retry = attempt < attempts
        try:
            return await attempt_once()
        except _EmptyResponse:
            if can_retry:
                logger.warning("Retrying %s empty %s for model %s", label, kind, request.model)
            else:
                return request.failure(f"{label} returned empty {kind} content")
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if can_retry and not request.observed_output and _is_retryable_http_status(status):
                logger.warning("Retrying %s %s HTTP error %s for model %s", label, kind, status, request.model)
            else:
                error_text = await _http_error_text(e)
                logger.error("%s %s HTTP error %s for model %s: %s", label, kind, status, request.model, error_text)
                return request.failure(error_text)
        except (httpx.TransportError, httpx.TimeoutException) as e:
            if can_retry and not request.observed_output:
                logger.warning("Retrying %s %s transport error for model %s: %s", label, kind, request.model, e)
            else:
                logger.error("%s %s transport error for model %s: %s", label, kind, request.model, e)
                return request.failure(str(e))
        except Exception as e:
            logger.error("%s %s error for model %s: %s", label, kind, request.model, e)
            return request.failure(str(e))
        request.retry_count += 1
        await asyncio.sleep(_retry_delay_seconds(attempt))
    raise AssertionError("unreachable: the last attempt always returns")


async def _notify(callback: Callable[[Any], Awaitable[None] | None] | None, value: Any) -> None:
    if callback is not None:
        maybe_awaitable = callback(value)
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable


async def chat_completion(
    messages: List[Message],
    model: str,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    response_format: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    session_id: str | None = None,
    prompt_segments: list[PromptSegment] | None = None,
    request_overrides: dict[str, Any] | None = None,
    priority: str = "interactive",
) -> ModelResponse:
    """Single non-streaming chat completion."""
    cache_policy = resolve_prompt_cache_policy(model)
    rendered_messages = render_prompt_messages(messages, model, prompt_segments=prompt_segments)
    payload = _build_chat_completion_payload(
        rendered_messages=rendered_messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        cache_policy=cache_policy,
        response_format=response_format,
        tools=tools,
        tool_choice=tool_choice,
        session_id=session_id,
        request_overrides=request_overrides,
    )
    request = _ModelRequest(messages, model, cache_policy, response_format, session_id, rendered_messages)
    async with _model_client(model, priority=priority) as client:

        async def attempt_once() -> ModelResponse:
            resp = await client.post("/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = _extract_message_content(data)
            reasoning, reasoning_details = _extract_message_reasoning(data)
            tool_calls = _extract_tool_calls(data)
            if not content and not tool_calls and not reasoning:
                raise _EmptyResponse
            usage = data.get("usage")
            return ModelResponse(
                model=model,
                content=content or "",
                reasoning=reasoning,
                reasoning_details=reasoning_details,
                usage=usage,
                tool_calls=tool_calls,
                finish_reason=_extract_finish_reason(data),
                prompt_metrics=request.metrics(usage),
            )

        return await _with_retries(request, attempt_once, kind="message")


async def chat_completion_stream_response(
    messages: List[Message],
    model: str,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    response_format: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    session_id: str | None = None,
    prompt_segments: list[PromptSegment] | None = None,
    request_overrides: dict[str, Any] | None = None,
    priority: str = "interactive",
    on_content_delta: Callable[[str], Awaitable[None] | None] | None = None,
    on_reasoning_delta: Callable[[str], Awaitable[None] | None] | None = None,
    on_tool_call_delta: Callable[[ToolCall], Awaitable[None] | None] | None = None,
) -> ModelResponse:
    """Streaming chat completion parsed back into a full ModelResponse."""
    cache_policy = resolve_prompt_cache_policy(model)
    rendered_messages = render_prompt_messages(messages, model, prompt_segments=prompt_segments)
    payload = _build_chat_completion_payload(
        rendered_messages=rendered_messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        cache_policy=cache_policy,
        response_format=response_format,
        tools=tools,
        tool_choice=tool_choice,
        session_id=session_id,
        request_overrides=request_overrides,
        stream=True,
    )
    request = _ModelRequest(messages, model, cache_policy, response_format, session_id, rendered_messages)
    async with _model_client(model, priority=priority) as client:

        async def attempt_once() -> ModelResponse:
            content_parts: list[str] = []
            reasoning_state: list[dict[str, Any]] = []
            reasoning_content_parts: list[str] = []
            tool_call_state: list[dict[str, Any]] = []
            finish_reason: str | None = None
            usage: dict[str, Any] | None = None
            async with client.stream("POST", "/chat/completions", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:]
                    if raw.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    choice = _extract_first_choice(chunk)
                    delta = choice.get("delta") if isinstance(choice, dict) else None
                    if isinstance(delta, dict):
                        reasoning_observed, reasoning_deltas = _append_reasoning_detail_blocks(
                            reasoning_state,
                            delta.get("reasoning_details"),
                        )
                        if reasoning_observed:
                            request.observed_output = True
                        for reasoning_delta in reasoning_deltas:
                            await _notify(on_reasoning_delta, reasoning_delta)
                        raw_reasoning_delta = delta.get("reasoning")
                        if not isinstance(raw_reasoning_delta, str) or not raw_reasoning_delta:
                            raw_reasoning_delta = delta.get("reasoning_content")
                        if isinstance(raw_reasoning_delta, str) and raw_reasoning_delta:
                            reasoning_content_parts.append(raw_reasoning_delta)
                            request.observed_output = True
                            if not reasoning_deltas:
                                await _notify(on_reasoning_delta, raw_reasoning_delta)
                        for content_delta in _extract_stream_content_parts(delta.get("content")):
                            content_parts.append(content_delta)
                            request.observed_output = True
                            await _notify(on_content_delta, content_delta)
                        tool_calls_observed, streamed_tool_calls = _collect_stream_tool_calls(
                            tool_call_state,
                            delta.get("tool_calls"),
                        )
                        if tool_calls_observed:
                            request.observed_output = True
                        for streamed_tool_call in streamed_tool_calls:
                            await _notify(on_tool_call_delta, streamed_tool_call)
                    if isinstance(choice, dict):
                        raw_finish_reason = choice.get("finish_reason")
                        if isinstance(raw_finish_reason, str) and raw_finish_reason:
                            finish_reason = raw_finish_reason
                    usage = _extract_stream_usage(chunk, choice) or usage
            content = "".join(content_parts)
            reasoning = "".join(reasoning_content_parts).strip() or _reasoning_text_from_details(reasoning_state)
            tool_calls = _materialize_stream_tool_calls(tool_call_state)
            if not content and not tool_calls and not reasoning:
                raise _EmptyResponse
            return ModelResponse(
                model=model,
                content=content,
                reasoning=reasoning,
                reasoning_details=[dict(item) for item in reasoning_state],
                usage=usage,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
                prompt_metrics=request.metrics(usage),
            )

        return await _with_retries(request, attempt_once, kind="streaming message")


async def chat_completion_structured(
    messages: List[Message],
    model: str,
    *,
    schema_name: str,
    json_schema: dict[str, Any],
    temperature: float = 0.2,
    max_tokens: int = 4096,
    session_id: str | None = None,
    prompt_segments: list[PromptSegment] | None = None,
) -> tuple[dict[str, Any], ModelResponse]:
    """Single non-streaming completion that requests structured JSON when supported."""

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": schema_name,
            "strict": True,
            "schema": json_schema,
        },
    }
    for attempt in range(2):
        response = await chat_completion(
            messages,
            model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            session_id=session_id,
            prompt_segments=prompt_segments,
        )
        if response.error:
            lowered = response.error.lower()
            unsupported = (
                "response_format" in lowered
                or "json_schema" in lowered
                or "unsupported" in lowered
                or "not supported" in lowered
            )
            if unsupported:
                response = await chat_completion(
                    messages,
                    model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    session_id=session_id,
                    prompt_segments=prompt_segments,
                )
            if response.error:
                raise ValueError(response.error)

        content = response.content.strip()
        if content.startswith("```"):
            lines = content.splitlines()
            if len(lines) >= 2 and lines[-1].strip() == "```":
                content = "\n".join(lines[1:-1]).strip()

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            if attempt == 0:
                logger.warning("Retrying malformed structured JSON response for model %s: %s", model, exc)
                continue
            raise ValueError(f"Structured completion did not return valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            if attempt == 0:
                logger.warning("Retrying non-object structured response for model %s", model)
                continue
            raise ValueError("Structured completion did not return a JSON object")
        return parsed, response

    raise ValueError("Structured completion did not return a valid JSON object")


