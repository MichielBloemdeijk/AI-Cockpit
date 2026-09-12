"""OpenAI GPT-Live WebRTC bootstrap and managed Responses sideband router."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
import json
import time
from typing import Any, Protocol
from urllib.parse import quote
from uuid import uuid4

import httpx

from app.config import settings
from app.services.jarvis_tool_catalog import get_jarvis_tool_definitions
from app.services.voice_session_adapter import (
    ProviderEventHandler,
    VoiceProviderCapabilities,
    VoiceProviderEvent,
    VoiceSessionConnection,
)


class OpenAILiveConfigurationError(ValueError):
    """Raised when a Live session cannot be created safely."""


class LiveSocket(Protocol):
    def __aiter__(self) -> AsyncIterator[str | bytes]: ...
    async def send(self, message: str) -> None: ...
    async def close(self, code: int = 1000, reason: str = "") -> None: ...


LiveSocketConnector = Callable[..., Awaitable[LiveSocket]]
LiveSessionCreator = Callable[..., Awaitable[dict[str, Any]]]
HttpClientFactory = Callable[[], httpx.AsyncClient]


@dataclass(slots=True)
class _DelegatedResponse:
    delegation_id: str
    response_id: str
    calls: set[str] = field(default_factory=set)
    results: set[str] = field(default_factory=set)
    terminal: bool = False
    continued: bool = False


@dataclass(slots=True)
class _LiveSession:
    session_id: str
    generation: int
    provider_session_id: str
    socket: LiveSocket
    on_event: ProviderEventHandler
    activation_id: str | None = None
    receiver_task: asyncio.Task[None] | None = None
    idle_task: asyncio.Task[None] | None = None
    started: bool = False
    closing: bool = False
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    final_usage: dict[str, Any] | None = None
    responses: dict[tuple[str, str], _DelegatedResponse] = field(default_factory=dict)
    call_index: dict[str, tuple[str, str]] = field(default_factory=dict)
    handled_calls: set[tuple[str, str, str]] = field(default_factory=set)
    pending_commentary: dict[str, tuple[str, tuple[str, ...], str]] = field(default_factory=dict)
    last_activity_monotonic: float = field(default_factory=time.monotonic)


class OpenAILiveAdapter:
    """Keeps media on WebRTC and executes only authorized tools on sideband."""

    name = "openai_live"
    capabilities = VoiceProviderCapabilities(
        continuous_conversation=True,
        application_controlled_turns=False,
        supports_candidate_audio_append=False,
        supports_manual_response=False,
        supports_sideband=True,
    )

    def __init__(
        self,
        *,
        websocket_connector: LiveSocketConnector | None = None,
        session_creator: LiveSessionCreator | None = None,
        http_client_factory: HttpClientFactory | None = None,
    ) -> None:
        self._websocket_connector = websocket_connector or self._default_websocket_connector
        self._session_creator = session_creator or self._default_session_creator
        self._http_client_factory = http_client_factory or self._default_http_client
        self._http_client: httpx.AsyncClient | None = None
        self._http_client_lock = asyncio.Lock()
        self._sessions: dict[tuple[str, int], _LiveSession] = {}

    async def connect(
        self,
        *,
        session_id: str,
        generation: int,
        context: dict[str, Any],
        on_event: ProviderEventHandler,
        sdp_offer: str | None = None,
    ) -> VoiceSessionConnection:
        api_key = settings.openai_api_key.strip()
        if not api_key:
            raise OpenAILiveConfigurationError("OPENAI_API_KEY is not configured on the backend")
        if not (sdp_offer or "").strip():
            raise OpenAILiveConfigurationError("An SDP offer is required for GPT-Live")
        created = await self._session_creator(
            api_key=api_key,
            sdp_offer=sdp_offer,
            session_config=self._session_config(context),
        )
        provider_session_id, sdp_answer = self._parse_creation_response(created)
        socket = await self._websocket_connector(
            self._sideband_url(provider_session_id),
            additional_headers={"Authorization": f"Bearer {api_key}"},
            open_timeout=settings.openai_realtime_connect_timeout_seconds,
        )
        provider_session = _LiveSession(
            session_id=session_id,
            generation=generation,
            provider_session_id=provider_session_id,
            socket=socket,
            on_event=on_event,
            activation_id=str(context.get("activation_id") or "") or None,
        )
        key = (session_id, generation)
        previous = self._sessions.pop(key, None)
        if previous is not None:
            await self._close_session(previous, reason="replaced", emit_event=False)
        self._sessions[key] = provider_session
        provider_session.receiver_task = asyncio.create_task(
            self._receive(provider_session),
            name=f"jarvis-openai-live-{session_id}-{generation}",
        )
        provider_session.idle_task = asyncio.create_task(
            self._watch_idle(provider_session),
            name=f"jarvis-openai-live-idle-{session_id}-{generation}",
        )
        return VoiceSessionConnection(sdp_answer=sdp_answer, provider_session_id=provider_session_id)

    async def send_user_text(self, *, session_id: str, generation: int, turn_id: str, text: str) -> None:
        provider_session = self._require_session(session_id, generation)
        await self._send(
            provider_session,
            {
                "type": "response.item.create",
                "event_id": f"jarvis-user-text:{turn_id}",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            },
        )
        await self._send(
            provider_session,
            {"type": "response.create", "event_id": f"jarvis-user-text-response:{turn_id}"},
        )

    async def send_tool_result(
        self,
        *,
        session_id: str,
        generation: int,
        call_id: str,
        output: dict[str, Any],
    ) -> None:
        provider_session = self._require_session(session_id, generation)
        response_key = provider_session.call_index.get(call_id)
        if response_key is None:
            raise ValueError("Unknown GPT-Live function call")
        delegated = provider_session.responses[response_key]
        if call_id in delegated.results:
            return
        await self._send(
            provider_session,
            {
                "type": "response.item.create",
                "event_id": f"jarvis-live-tool-result:{uuid4()}",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(output, separators=(",", ":"), ensure_ascii=False),
                },
            },
        )
        delegated.results.add(call_id)
        await self._continue_if_ready(provider_session, delegated)

    async def create_response(
        self,
        *,
        session_id: str,
        generation: int,
        request_id: str,
        activation_id: str | None = None,
        input_item_id: str | None = None,
        response_kind: str,
        presentation_ids: tuple[str, ...],
        facts: tuple[dict[str, Any], ...],
        context: dict[str, Any],
    ) -> None:
        provider_session = self._require_session(session_id, generation)
        if response_kind != "task_update":
            return
        phrases = [
            str(fact.get("required_spoken_phrase") or fact.get("summary") or fact.get("question") or fact.get("status") or "").strip()
            for fact in facts
        ]
        verified = " ".join(phrase for phrase in phrases if phrase)
        if not verified:
            return
        event_id = f"jarvis-live-update:{request_id}"
        provider_session.pending_commentary[event_id] = (request_id, presentation_ids, verified)
        await self._send(
            provider_session,
            {
                "type": "session.commentary.append",
                "event_id": event_id,
                "delegation_id": None,
                "content": "Give this verified application update naturally and concisely. Do not add unverified claims: " + verified,
            },
        )

    async def cancel_response(self, *, session_id: str, generation: int, response_id: str | None = None) -> None:
        # Full-duplex Live handles speech interruption. Explicit task cancellation
        # remains an authorized gateway tool and is intentionally separate.
        self._require_session(session_id, generation)

    async def discard_candidate_items(
        self, *, session_id: str, generation: int, item_ids: tuple[str, ...]
    ) -> None:
        self._require_session(session_id, generation)

    def is_connected(self, *, session_id: str, generation: int) -> bool:
        provider_session = self._sessions.get((session_id, generation))
        return bool(provider_session is not None and not provider_session.closing)

    async def close(
        self, *, session_id: str, generation: int, reason: str, emit_event: bool = True
    ) -> None:
        provider_session = self._sessions.pop((session_id, generation), None)
        if provider_session is not None:
            await self._close_session(provider_session, reason=reason, emit_event=emit_event)

    async def shutdown(self) -> None:
        sessions = list(self._sessions.values())
        self._sessions.clear()
        for provider_session in sessions:
            await self._close_session(provider_session, reason="backend_shutdown", emit_event=False)
        client = self._http_client
        self._http_client = None
        if client is not None and not client.is_closed:
            await client.aclose()

    async def handle_event(
        self, session_id: str, generation: int, event: dict[str, Any]
    ) -> None:
        provider_session = self._sessions.get((session_id, generation))
        if provider_session is not None and not provider_session.closing:
            await self._handle_event(provider_session, event)

    async def _receive(self, provider_session: _LiveSession) -> None:
        key = (provider_session.session_id, provider_session.generation)
        try:
            async for raw_message in provider_session.socket:
                provider_session.last_activity_monotonic = time.monotonic()
                raw = raw_message.decode("utf-8") if isinstance(raw_message, bytes) else raw_message
                event = json.loads(raw)
                if isinstance(event, dict):
                    await self._handle_event(provider_session, event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._sessions.get(key) is provider_session and not provider_session.closing:
                await self._emit(
                    provider_session,
                    "provider_error",
                    {"reason": type(exc).__name__, "stage": "sideband_receive"},
                )

    async def _handle_event(self, provider_session: _LiveSession, outer: dict[str, Any]) -> None:
        event_type = str(outer.get("type") or "")
        if event_type == "session.started":
            if not provider_session.started:
                provider_session.started = True
                await self._emit(
                    provider_session,
                    "ready",
                    {
                        "provider_session_id": provider_session.provider_session_id,
                        "activation_id": provider_session.activation_id,
                        "defer_initial_updates": True,
                    },
                    event_id=str(outer.get("event_id") or "") or None,
                )
            return
        if event_type == "session.closed":
            usage = outer.get("usage")
            provider_session.final_usage = usage if isinstance(usage, dict) else None
            provider_session.closed.set()
            if provider_session.final_usage is not None:
                await self._emit(provider_session, "usage", {"usage": provider_session.final_usage})
            await self._emit(provider_session, "disconnected", {"reason": "session_closed"})
            return
        if event_type == "session.input_transcript.delta":
            await self._emit(provider_session, "user_transcript_delta", {"delta": str(outer.get("delta") or "")})
            return
        if event_type == "session.output_transcript.delta":
            await self._emit(provider_session, "assistant_transcript_delta", {"delta": str(outer.get("delta") or "")})
            return
        if event_type == "session.commentary.appended":
            client_event_id = str(outer.get("client_event_id") or "")
            pending = provider_session.pending_commentary.pop(client_event_id, None)
            if pending is not None:
                request_id, presentation_ids, transcript = pending
                await self._emit(
                    provider_session,
                    "assistant_response",
                    {
                        "response_id": f"live-commentary:{client_event_id}",
                        "item_id": client_event_id,
                        "request_id": request_id,
                        "presentation_ids": list(presentation_ids),
                        "response_kind": "task_update",
                        "transcript": transcript,
                        "delivery_ack": "context_appended",
                    },
                    event_id=f"live-commentary-ack:{client_event_id}",
                )
            return
        if event_type == "error":
            await self._emit(provider_session, "provider_error", {"error": outer.get("error")})
            return
        if event_type != "response.event":
            return

        delegation_id = str(outer.get("delegation_id") or "")
        inner = outer.get("event")
        if not delegation_id or not isinstance(inner, dict):
            await self._emit(provider_session, "provider_protocol_warning", {"reason": "invalid_response_event"})
            return
        inner_type = str(inner.get("type") or "")
        response = inner.get("response") if isinstance(inner.get("response"), dict) else {}
        response_id = str(response.get("id") or inner.get("response_id") or "")
        if not response_id:
            response_id = self._response_id_for_delegation(provider_session, delegation_id)
        if inner_type == "response.created" and response_id:
            provider_session.responses.setdefault(
                (delegation_id, response_id),
                _DelegatedResponse(delegation_id=delegation_id, response_id=response_id),
            )
            await self._emit(provider_session, "assistant_generating", {"delegation_id": delegation_id, "response_id": response_id})
            return
        if inner_type == "response.output_item.done":
            item = inner.get("item") if isinstance(inner.get("item"), dict) else {}
            if item.get("type") == "function_call" and response_id:
                await self._collect_function_call(
                    provider_session,
                    delegation_id=delegation_id,
                    response_id=response_id,
                    item=item,
                )
            return
        if inner_type in {"response.completed", "response.failed", "response.cancelled"} and response_id:
            delegated = provider_session.responses.setdefault(
                (delegation_id, response_id),
                _DelegatedResponse(delegation_id=delegation_id, response_id=response_id),
            )
            delegated.terminal = True
            await self._continue_if_ready(provider_session, delegated)
            await self._emit(
                provider_session,
                "assistant_response",
                {
                    "delegation_id": delegation_id,
                    "response_id": response_id,
                    "response_status": inner_type.removeprefix("response."),
                },
            )

    async def _collect_function_call(
        self,
        provider_session: _LiveSession,
        *,
        delegation_id: str,
        response_id: str,
        item: dict[str, Any],
    ) -> None:
        call_id = str(item.get("call_id") or item.get("id") or "")
        name = str(item.get("name") or "")
        if not call_id or not name:
            await self._emit(provider_session, "provider_protocol_warning", {"reason": "incomplete_function_call"})
            return
        execution_key = (delegation_id, response_id, call_id)
        if execution_key in provider_session.handled_calls:
            return
        provider_session.handled_calls.add(execution_key)
        response_key = (delegation_id, response_id)
        delegated = provider_session.responses.setdefault(
            response_key,
            _DelegatedResponse(delegation_id=delegation_id, response_id=response_id),
        )
        delegated.calls.add(call_id)
        provider_session.call_index[call_id] = response_key
        await self._emit(
            provider_session,
            "shell_call",
            {
                "delegation_id": delegation_id,
                "response_id": response_id,
                "call_id": call_id,
                "name": name,
                "arguments": _parse_arguments(item.get("arguments")),
            },
            event_id=f"live-tool:{delegation_id}:{response_id}:{call_id}",
        )

    async def _continue_if_ready(
        self, provider_session: _LiveSession, delegated: _DelegatedResponse
    ) -> None:
        if (
            delegated.continued
            or not delegated.terminal
            or not delegated.calls
            or not delegated.calls.issubset(delegated.results)
        ):
            return
        delegated.continued = True
        await self._send(
            provider_session,
            {"type": "response.create", "event_id": f"jarvis-live-continue:{uuid4()}"},
        )

    async def _close_session(
        self, provider_session: _LiveSession, *, reason: str, emit_event: bool
    ) -> None:
        if provider_session.closing:
            return
        provider_session.closing = True
        if provider_session.started and not provider_session.closed.is_set():
            try:
                await provider_session.socket.send(json.dumps({"type": "session.close", "event_id": f"jarvis-live-close:{uuid4()}"}))
                await asyncio.wait_for(provider_session.closed.wait(), timeout=2.0)
            except (TimeoutError, OSError):
                pass
        task = provider_session.receiver_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        idle_task = provider_session.idle_task
        if idle_task is not None and idle_task is not asyncio.current_task() and not idle_task.done():
            idle_task.cancel()
            await asyncio.gather(idle_task, return_exceptions=True)
        await provider_session.socket.close(code=1000, reason=reason[:120])
        if emit_event and not provider_session.closed.is_set():
            await self._emit(provider_session, "disconnected", {"reason": reason})

    async def _send(self, provider_session: _LiveSession, payload: dict[str, Any]) -> None:
        if provider_session.closing:
            raise ValueError("GPT-Live session is closing")
        await provider_session.socket.send(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
        provider_session.last_activity_monotonic = time.monotonic()

    async def _watch_idle(self, provider_session: _LiveSession) -> None:
        idle_seconds = max(1, settings.openai_live_idle_timeout_seconds)
        try:
            while not provider_session.closing:
                await asyncio.sleep(min(5.0, idle_seconds / 2))
                settled = not provider_session.pending_commentary and all(
                    delegated.terminal and delegated.calls.issubset(delegated.results)
                    for delegated in provider_session.responses.values()
                )
                if (
                    settled
                    and time.monotonic() - provider_session.last_activity_monotonic >= idle_seconds
                ):
                    await self.close(
                        session_id=provider_session.session_id,
                        generation=provider_session.generation,
                        reason="idle_timeout",
                        emit_event=True,
                    )
                    return
        except asyncio.CancelledError:
            raise

    async def _emit(
        self,
        provider_session: _LiveSession,
        kind: str,
        payload: dict[str, Any],
        *,
        event_id: str | None = None,
    ) -> None:
        await provider_session.on_event(
            provider_session.session_id,
            provider_session.generation,
            VoiceProviderEvent(event_id=event_id or f"openai-live:{kind}:{uuid4()}", kind=kind, payload=payload),
        )

    def _require_session(self, session_id: str, generation: int) -> _LiveSession:
        provider_session = self._sessions.get((session_id, generation))
        if provider_session is None or provider_session.closing:
            raise ValueError("GPT-Live session is not connected")
        return provider_session

    @staticmethod
    def _response_id_for_delegation(provider_session: _LiveSession, delegation_id: str) -> str:
        matches = [key[1] for key in provider_session.responses if key[0] == delegation_id]
        return matches[-1] if matches else ""

    @staticmethod
    def _parse_creation_response(created: dict[str, Any]) -> tuple[str, str]:
        session = created.get("session") if isinstance(created.get("session"), dict) else {}
        transport = created.get("transport") if isinstance(created.get("transport"), dict) else {}
        provider_session_id = str(session.get("id") or "")
        sdp_answer = str(transport.get("sdp") or "")
        if not provider_session_id or not sdp_answer:
            raise RuntimeError("OpenAI GPT-Live returned incomplete session material")
        return provider_session_id, sdp_answer

    @staticmethod
    def _sideband_url(provider_session_id: str) -> str:
        return f"wss://api.openai.com/v1/live/sessions/{quote(provider_session_id, safe='')}/attach"

    @staticmethod
    def _session_config(context: dict[str, Any]) -> dict[str, Any]:
        preferences = context.get("preferences") if isinstance(context.get("preferences"), dict) else {}
        voice = str(preferences.get("voice") or settings.openai_live_voice)
        if voice not in {"marin", "cedar"}:
            voice = settings.openai_live_voice
        conversation_prompt = (
            "You are Jarvis, a concise spoken assistant. Keep listening while speaking. "
            "Acknowledge actionable requests quickly, then delegate work needing tools or "
            "authoritative application state. Never claim success until the backend returns a "
            "verified result. Speech interruption does not cancel durable work."
        )
        router_prompt = (
            "Interpret actionable requests and use only the supplied Jarvis tools. The application "
            "owns authorization, execution, and durable state. Prefer dispatch_task for substantial "
            "work. Return concise speech-ready facts and never invent task state or success."
        )
        situation = context.get("situation")
        if isinstance(situation, dict) and situation:
            router_prompt += " Current application context: " + json.dumps(
                situation, separators=(",", ":"), ensure_ascii=False
            )[:8_000]
        return {
            "model": settings.openai_live_model,
            "instructions": conversation_prompt,
            "audio": {"output": {"voice": voice}},
            "delegation": {
                "type": "responses",
                "responses": {
                    "model": settings.openai_live_responses_model,
                    "instructions": router_prompt,
                    "reasoning": {"effort": settings.openai_live_reasoning_effort},
                    "tools": get_jarvis_tool_definitions("openai_live"),
                    "tool_choice": "auto",
                    "parallel_tool_calls": False,
                },
            },
        }

    async def _default_session_creator(
        self, *, api_key: str, sdp_offer: str, session_config: dict[str, Any]
    ) -> dict[str, Any]:
        client = await self._shared_http_client()
        response = await client.post(
            f"{settings.openai_live_base_url.rstrip('/')}/live/sessions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"session": session_config, "transport": {"type": "webrtc", "sdp": sdp_offer}},
        )
        if response.status_code != 201:
            raise RuntimeError(f"OpenAI GPT-Live session creation failed with HTTP {response.status_code}")
        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError("OpenAI GPT-Live returned an invalid response")
        return result

    async def _shared_http_client(self) -> httpx.AsyncClient:
        client = self._http_client
        if client is not None and not client.is_closed:
            return client
        async with self._http_client_lock:
            client = self._http_client
            if client is None or client.is_closed:
                client = self._http_client_factory()
                self._http_client = client
            return client

    @staticmethod
    def _default_http_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=15.0)

    @staticmethod
    async def _default_websocket_connector(url: str, **kwargs: Any) -> LiveSocket:
        from websockets.asyncio.client import connect
        return await connect(url, **kwargs)


def _parse_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


openai_live_adapter = OpenAILiveAdapter()
