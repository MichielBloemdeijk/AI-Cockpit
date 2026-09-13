"""OpenAI GPT-Live WebRTC bootstrap and managed Responses sideband router."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
import hashlib
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
    origin_item_id: str | None = None
    origin_correlation_id: str = ""
    timeline_start_ms: int | None = None
    timeline_end_ms: int | None = None


@dataclass(slots=True)
class _Delegation:
    delegation_id: str
    origin_correlation_id: str
    offset_ms: int | None = None
    response_id: str | None = None
    origin_item_id: str | None = None


@dataclass(frozen=True, slots=True)
class _TranscriptFragment:
    delta: str
    start_ms: int | None
    end_ms: int | None
    arrival: int


@dataclass(frozen=True, slots=True)
class _TranscriptSegment:
    item_id: str
    start_ms: int | None
    end_ms: int | None


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
    delegations: dict[str, _Delegation] = field(default_factory=dict)
    responses: dict[tuple[str, str], _DelegatedResponse] = field(default_factory=dict)
    call_index: dict[str, tuple[str, str]] = field(default_factory=dict)
    handled_calls: set[tuple[str, str, str]] = field(default_factory=set)
    pending_commentary: dict[str, tuple[str, tuple[str, ...], str]] = field(default_factory=dict)
    user_fragments: list[_TranscriptFragment] = field(default_factory=list)
    assistant_fragments: list[_TranscriptFragment] = field(default_factory=list)
    user_segments: list[_TranscriptSegment] = field(default_factory=list)
    user_transcript_flush_task: asyncio.Task[None] | None = None
    assistant_transcript_flush_task: asyncio.Task[None] | None = None
    transcript_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    transcript_arrival: int = 0
    user_transcript_segment: int = 0
    assistant_transcript_segment: int = 0
    last_activity_monotonic: float = field(default_factory=time.monotonic)
    connect_started: float = 0.0
    provider_call_started: float = 0.0
    provider_call_finished: float = 0.0
    sideband_started: float = 0.0
    sideband_finished: float = 0.0


@dataclass(slots=True)
class _PendingLiveConnection:
    session_id: str
    generation: int
    provider_session_id: str
    api_key: str
    context: dict[str, Any]
    on_event: ProviderEventHandler
    connect_started: float
    provider_call_started: float
    provider_call_finished: float
    task: asyncio.Task[None] | None = None
    socket: LiveSocket | None = None
    closing: bool = False


_TRANSCRIPT_GAP_SECONDS = 0.75
_TRANSCRIPT_TIMELINE_GAP_MS = 750
_DELEGATION_MATCH_WINDOW_MS = 2_500


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
        self._pending_connections: dict[tuple[str, int], _PendingLiveConnection] = {}

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
        connect_started = time.monotonic()
        provider_call_started = time.monotonic()
        created = await self._session_creator(
            api_key=api_key,
            sdp_offer=sdp_offer,
            session_config=self._session_config(context),
        )
        provider_call_finished = time.monotonic()
        provider_session_id, sdp_answer = self._parse_creation_response(created)
        pending = _PendingLiveConnection(
            session_id=session_id,
            generation=generation,
            provider_session_id=provider_session_id,
            api_key=api_key,
            context=context,
            on_event=on_event,
            connect_started=connect_started,
            provider_call_started=provider_call_started,
            provider_call_finished=provider_call_finished,
        )
        key = (session_id, generation)
        previous_pending = self._pending_connections.pop(key, None)
        if previous_pending is not None:
            await self._close_pending(previous_pending, reason="replaced")
        previous = self._sessions.pop(key, None)
        if previous is not None:
            await self._close_session(previous, reason="replaced", emit_event=False)
        self._pending_connections[key] = pending
        pending.task = asyncio.create_task(
            self._establish_sideband(pending),
            name=f"jarvis-openai-live-setup-{session_id}-{generation}",
        )
        return VoiceSessionConnection(sdp_answer=sdp_answer, provider_session_id=provider_session_id)

    async def _establish_sideband(self, pending: _PendingLiveConnection) -> None:
        key = (pending.session_id, pending.generation)
        provider_session: _LiveSession | None = None
        try:
            sideband_started = time.monotonic()
            socket = await self._websocket_connector(
                self._sideband_url(pending.provider_session_id),
                additional_headers={"Authorization": f"Bearer {pending.api_key}"},
                open_timeout=settings.openai_realtime_connect_timeout_seconds,
            )
            pending.socket = socket
            sideband_finished = time.monotonic()
            if pending.closing or self._pending_connections.get(key) is not pending:
                await socket.close(code=1000, reason="connection_cancelled")
                return
            provider_session = _LiveSession(
                session_id=pending.session_id,
                generation=pending.generation,
                provider_session_id=pending.provider_session_id,
                socket=socket,
                on_event=pending.on_event,
                activation_id=str(pending.context.get("activation_id") or "") or None,
                connect_started=pending.connect_started,
                provider_call_started=pending.provider_call_started,
                provider_call_finished=pending.provider_call_finished,
                sideband_started=sideband_started,
                sideband_finished=sideband_finished,
            )
            self._sessions[key] = provider_session
            self._pending_connections.pop(key, None)
            provider_session.receiver_task = asyncio.create_task(
                self._receive(provider_session),
                name=f"jarvis-openai-live-{pending.session_id}-{pending.generation}",
            )
            provider_session.idle_task = asyncio.create_task(
                self._watch_idle(provider_session),
                name=f"jarvis-openai-live-idle-{pending.session_id}-{pending.generation}",
            )
        except asyncio.CancelledError:
            if pending.socket is not None:
                await pending.socket.close(code=1000, reason="connection_cancelled")
            raise
        except Exception as exc:
            self._pending_connections.pop(key, None)
            self._sessions.pop(key, None)
            if pending.socket is not None:
                await pending.socket.close(code=1011, reason="sideband_setup_failed")
            if not pending.closing:
                await self._notify_pending_best_effort(
                    pending,
                    VoiceProviderEvent(
                        event_id=f"openai-live-sideband-setup-error:{uuid4()}",
                        kind="provider_error",
                        payload={"reason": type(exc).__name__, "stage": "sideband_setup"},
                    ),
                )

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
        key = (session_id, generation)
        pending = self._pending_connections.pop(key, None)
        if pending is not None:
            await self._close_pending(pending, reason=reason)
        provider_session = self._sessions.pop(key, None)
        if provider_session is not None:
            await self._close_session(provider_session, reason=reason, emit_event=emit_event)

    async def shutdown(self) -> None:
        pending_connections = list(self._pending_connections.values())
        self._pending_connections.clear()
        for pending in pending_connections:
            await self._close_pending(pending, reason="backend_shutdown")
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
        pending = self._pending_connections.get((session_id, generation))
        if pending is not None and pending.task is not None:
            await asyncio.shield(pending.task)
        provider_session = self._sessions.get((session_id, generation))
        if provider_session is not None and not provider_session.closing:
            await self._handle_event(provider_session, event)

    async def _receive(self, provider_session: _LiveSession) -> None:
        key = (provider_session.session_id, provider_session.generation)
        try:
            async for raw_message in provider_session.socket:
                raw = raw_message.decode("utf-8") if isinstance(raw_message, bytes) else raw_message
                event = json.loads(raw)
                if isinstance(event, dict):
                    await self._handle_event(provider_session, event)
            if self._sessions.get(key) is provider_session and not provider_session.closing:
                await self._emit(
                    provider_session,
                    "provider_error",
                    {"reason": "sideband_closed", "stage": "sideband_receive"},
                )
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
                        "timing_ms": {
                            "provider_call": round(
                                (provider_session.provider_call_finished - provider_session.provider_call_started)
                                * 1_000,
                                3,
                            ),
                            "sideband_connect": round(
                                (provider_session.sideband_finished - provider_session.sideband_started) * 1_000,
                                3,
                            ),
                            "total_to_sideband_ready": round(
                                (time.monotonic() - provider_session.connect_started) * 1_000,
                                3,
                            ),
                        },
                    },
                    event_id=str(outer.get("event_id") or "") or None,
                )
            return
        if event_type == "session.closed":
            await self._flush_transcript(provider_session, "user")
            await self._flush_transcript(provider_session, "assistant")
            usage = outer.get("usage")
            provider_session.final_usage = usage if isinstance(usage, dict) else None
            provider_session.closed.set()
            if provider_session.final_usage is not None:
                await self._emit(provider_session, "usage", {"usage": provider_session.final_usage})
            await self._emit(provider_session, "disconnected", {"reason": "session_closed"})
            return
        if event_type == "session.input_transcript.delta":
            await self._handle_transcript_delta(provider_session, "user", outer)
            return
        if event_type == "session.output_transcript.delta":
            await self._handle_transcript_delta(provider_session, "assistant", outer)
            return
        if event_type == "session.commentary.appended":
            client_event_id = str(outer.get("client_event_id") or "")
            pending = provider_session.pending_commentary.pop(client_event_id, None)
            if pending is not None:
                request_id, presentation_ids, transcript = pending
                self._mark_meaningful_activity(provider_session)
                await self._emit(
                    provider_session,
                    "assistant_update_submitted",
                    {
                        "request_id": request_id,
                        "presentation_ids": list(presentation_ids),
                        "response_kind": "task_update",
                        "submitted_transcript": transcript,
                        "submission_ack": "context_appended",
                    },
                    event_id=f"live-commentary-ack:{client_event_id}",
                )
            return
        if event_type == "error":
            await self._emit(provider_session, "provider_error", {"error": outer.get("error")})
            return
        if event_type == "session.delegation.created":
            delegation = (
                outer.get("delegation")
                if isinstance(outer.get("delegation"), dict)
                else {}
            )
            delegation_id = str(delegation.get("id") or "")
            if not delegation_id:
                await self._emit(
                    provider_session,
                    "provider_protocol_warning",
                    {"reason": "invalid_delegation_created"},
                )
                return
            observed = self._delegation(provider_session, delegation_id)
            offset_ms = _optional_int(outer.get("offset_ms"))
            if observed.offset_ms is None and offset_ms is not None:
                observed.offset_ms = offset_ms
            response_id = str(
                delegation.get("response_id") or outer.get("response_id") or ""
            )
            if response_id:
                observed.response_id = response_id
                self._observe_delegation(
                    provider_session, delegation_id, response_id
                )
            self._apply_delegation_context(provider_session, observed)
            self._mark_meaningful_activity(provider_session)
            await self._resolve_delegation_origins(provider_session)
            return
        if event_type != "response.event":
            return

        delegation_id = str(outer.get("delegation_id") or "")
        inner = outer.get("event")
        if not delegation_id or not isinstance(inner, dict):
            await self._emit(provider_session, "provider_protocol_warning", {"reason": "invalid_response_event"})
            return
        inner_type = str(inner.get("type") or "")
        if inner_type:
            self._mark_meaningful_activity(provider_session)
        response = inner.get("response") if isinstance(inner.get("response"), dict) else {}
        response_id = str(response.get("id") or inner.get("response_id") or "")
        if not response_id:
            response_id = self._response_id_for_delegation(provider_session, delegation_id)
        if inner_type == "response.created" and response_id:
            delegated = self._observe_delegation(
                provider_session, delegation_id, response_id
            )
            await self._resolve_delegation_origins(provider_session)
            await self._emit(provider_session, "assistant_generating", {"delegation_id": delegation_id, "response_id": response_id})
            return
        if inner_type == "response.output_item.done":
            item = inner.get("item") if isinstance(inner.get("item"), dict) else {}
            if item.get("type") == "function_call" and response_id:
                self._observe_delegation(
                    provider_session, delegation_id, response_id
                )
                await self._resolve_delegation_origins(provider_session)
                await self._collect_function_call(
                    provider_session,
                    delegation_id=delegation_id,
                    response_id=response_id,
                    item=item,
                )
            return
        if inner_type in {"response.completed", "response.failed", "response.cancelled"} and response_id:
            delegated = self._observe_delegation(
                provider_session, delegation_id, response_id
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
        delegated = provider_session.responses[response_key]
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
                "origin_item_id": delegated.origin_item_id,
                "origin_correlation_id": delegated.origin_correlation_id,
                "origin_timeline": {
                    "offset_ms": delegated.timeline_start_ms,
                    "start_ms": delegated.timeline_start_ms,
                    "end_ms": delegated.timeline_end_ms,
                },
            },
            event_id=f"live-tool:{delegation_id}:{response_id}:{call_id}",
        )

    async def _handle_transcript_delta(
        self,
        provider_session: _LiveSession,
        speaker: str,
        event: dict[str, Any],
    ) -> None:
        delta = str(event.get("delta") or "")
        start_ms = _optional_int(event.get("start_ms"))
        end_ms = _optional_int(event.get("end_ms"))
        if delta:
            self._mark_meaningful_activity(provider_session)
            provider_session.transcript_arrival += 1
            fragments = (
                provider_session.user_fragments
                if speaker == "user"
                else provider_session.assistant_fragments
            )
            fragments.append(
                _TranscriptFragment(
                    delta=delta,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    arrival=provider_session.transcript_arrival,
                )
            )
            task = (
                provider_session.user_transcript_flush_task
                if speaker == "user"
                else provider_session.assistant_transcript_flush_task
            )
            if task is not None and not task.done():
                task.cancel()
            task = asyncio.create_task(
                self._flush_transcript_after_gap(provider_session, speaker),
                name=(
                    f"jarvis-live-{speaker}-transcript-"
                    f"{provider_session.session_id}-{provider_session.generation}"
                ),
            )
            if speaker == "user":
                provider_session.user_transcript_flush_task = task
            else:
                provider_session.assistant_transcript_flush_task = task
        await self._emit(
            provider_session,
            f"{speaker}_transcript_delta",
            {"delta": delta, "start_ms": start_ms, "end_ms": end_ms},
        )

    async def _flush_transcript_after_gap(
        self, provider_session: _LiveSession, speaker: str
    ) -> None:
        try:
            await asyncio.sleep(_TRANSCRIPT_GAP_SECONDS)
            await self._flush_transcript(provider_session, speaker)
        except asyncio.CancelledError:
            raise

    async def _flush_transcript(
        self, provider_session: _LiveSession, speaker: str
    ) -> tuple[str, ...]:
        emissions: list[tuple[str, dict[str, Any]]] = []
        async with provider_session.transcript_lock:
            current_task = asyncio.current_task()
            task = (
                provider_session.user_transcript_flush_task
                if speaker == "user"
                else provider_session.assistant_transcript_flush_task
            )
            if task is not None and task is not current_task and not task.done():
                task.cancel()
            if speaker == "user":
                provider_session.user_transcript_flush_task = None
                fragments = provider_session.user_fragments
                provider_session.user_fragments = []
            else:
                provider_session.assistant_transcript_flush_task = None
                fragments = provider_session.assistant_fragments
                provider_session.assistant_fragments = []
            if not fragments:
                return ()
            for group in _group_transcript_fragments(fragments):
                transcript = "".join(fragment.delta for fragment in group)
                if not transcript.strip():
                    continue
                if speaker == "user":
                    provider_session.user_transcript_segment += 1
                    segment_number = provider_session.user_transcript_segment
                else:
                    provider_session.assistant_transcript_segment += 1
                    segment_number = provider_session.assistant_transcript_segment
                item_id = (
                    f"live-{speaker}:{provider_session.provider_session_id}:"
                    f"{segment_number}"
                )
                starts = [fragment.start_ms for fragment in group if fragment.start_ms is not None]
                ends = [fragment.end_ms for fragment in group if fragment.end_ms is not None]
                start_ms = min(starts) if starts else None
                end_ms = max(ends) if ends else None
                payload = {
                    "item_id": item_id,
                    "transcript": transcript,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "fragments": [
                        {
                            "delta": fragment.delta,
                            "start_ms": fragment.start_ms,
                            "end_ms": fragment.end_ms,
                        }
                        for fragment in group
                    ],
                }
                emissions.append((item_id, payload))
                if speaker == "user":
                    provider_session.user_segments.append(
                        _TranscriptSegment(item_id=item_id, start_ms=start_ms, end_ms=end_ms)
                    )
        for item_id, payload in emissions:
            await self._emit(
                provider_session,
                f"{speaker}_transcript",
                payload,
                event_id=f"live-{speaker}-transcript:{item_id}",
            )
        if speaker == "user" and emissions:
            await self._resolve_delegation_origins(provider_session)
        return tuple(item_id for item_id, _payload in emissions)

    def _observe_delegation(
        self,
        provider_session: _LiveSession,
        delegation_id: str,
        response_id: str,
    ) -> _DelegatedResponse:
        delegation = self._delegation(provider_session, delegation_id)
        delegation.response_id = response_id
        delegated = provider_session.responses.setdefault(
            (delegation_id, response_id),
            _DelegatedResponse(
                delegation_id=delegation_id,
                response_id=response_id,
                origin_correlation_id=delegation.origin_correlation_id,
            ),
        )
        delegated.origin_correlation_id = delegation.origin_correlation_id
        delegated.origin_item_id = delegation.origin_item_id
        delegated.timeline_start_ms = delegation.offset_ms
        return delegated

    def _delegation(
        self, provider_session: _LiveSession, delegation_id: str
    ) -> _Delegation:
        return provider_session.delegations.setdefault(
            delegation_id,
            _Delegation(
                delegation_id=delegation_id,
                origin_correlation_id=(
                    "live-delegation:"
                    + hashlib.sha256(
                        f"{provider_session.provider_session_id}\0{delegation_id}".encode("utf-8")
                    ).hexdigest()[:32]
                ),
            ),
        )

    @staticmethod
    def _apply_delegation_context(
        provider_session: _LiveSession, delegation: _Delegation
    ) -> None:
        for (delegation_id, _response_id), response in provider_session.responses.items():
            if delegation_id != delegation.delegation_id:
                continue
            response.origin_correlation_id = delegation.origin_correlation_id
            response.origin_item_id = delegation.origin_item_id
            response.timeline_start_ms = delegation.offset_ms

    async def _resolve_delegation_origins(self, provider_session: _LiveSession) -> None:
        by_correlation: dict[str, list[_DelegatedResponse]] = {}
        for delegated in provider_session.responses.values():
            by_correlation.setdefault(delegated.origin_correlation_id, []).append(delegated)
        used_segments = {
            delegated.origin_item_id
            for delegated in provider_session.responses.values()
            if delegated.origin_item_id is not None
        }
        candidate_rows: list[tuple[int, str, _TranscriptSegment]] = []
        ambiguous: set[str] = set()
        top_claims: dict[tuple[str, int], list[str]] = {}
        for correlation_id, responses in by_correlation.items():
            if any(response.origin_item_id is not None for response in responses):
                continue
            starts = [
                response.timeline_start_ms
                for response in responses
                if response.timeline_start_ms is not None
            ]
            if not starts:
                continue
            anchor = min(starts)
            candidates: list[tuple[int, _TranscriptSegment]] = []
            for segment in provider_session.user_segments:
                if segment.item_id in used_segments or segment.end_ms is None:
                    continue
                if segment.start_ms is not None and segment.start_ms > anchor + 250:
                    continue
                distance = abs(anchor - segment.end_ms)
                if distance <= _DELEGATION_MATCH_WINDOW_MS:
                    candidates.append((distance, segment))
            candidates.sort(key=lambda item: (item[0], item[1].item_id))
            if not candidates:
                continue
            if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
                ambiguous.add(correlation_id)
                continue
            top_claims.setdefault(
                (candidates[0][1].item_id, candidates[0][0]), []
            ).append(correlation_id)
            candidate_rows.extend(
                (distance, correlation_id, segment)
                for distance, segment in candidates
            )
        for correlations in top_claims.values():
            if len(correlations) > 1:
                ambiguous.update(correlations)
        assigned_correlations: set[str] = set()
        for _distance, correlation_id, segment in sorted(
            candidate_rows, key=lambda item: (item[0], item[1], item[2].item_id)
        ):
            if correlation_id in ambiguous or correlation_id in assigned_correlations:
                continue
            if segment.item_id in used_segments:
                continue
            responses = by_correlation[correlation_id]
            delegation = provider_session.delegations.get(responses[0].delegation_id)
            if delegation is not None:
                delegation.origin_item_id = segment.item_id
            for response in responses:
                response.origin_item_id = segment.item_id
            used_segments.add(segment.item_id)
            assigned_correlations.add(correlation_id)
            starts = [
                response.timeline_start_ms
                for response in responses
                if response.timeline_start_ms is not None
            ]
            ends = [
                response.timeline_end_ms
                for response in responses
                if response.timeline_end_ms is not None
            ]
            await self._emit(
                provider_session,
                "delegation_origin_resolved",
                {
                    "delegation_id": responses[0].delegation_id,
                    "origin_correlation_id": correlation_id,
                    "origin_item_id": segment.item_id,
                    "delegation_timeline": {
                        "offset_ms": min(starts) if starts else None,
                        "start_ms": min(starts) if starts else None,
                        "end_ms": max(ends) if ends else None,
                    },
                    "transcript_timeline": {
                        "start_ms": segment.start_ms,
                        "end_ms": segment.end_ms,
                    },
                },
                event_id=f"live-origin:{correlation_id}:{segment.item_id}",
            )

    @staticmethod
    def _mark_meaningful_activity(provider_session: _LiveSession) -> None:
        provider_session.last_activity_monotonic = time.monotonic()

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
        await self._flush_transcript(provider_session, "user")
        await self._flush_transcript(provider_session, "assistant")
        provider_session.closing = True
        if provider_session.started and not provider_session.closed.is_set():
            try:
                await provider_session.socket.send(json.dumps({"type": "session.close", "event_id": f"jarvis-live-close:{uuid4()}"}))
                await asyncio.wait_for(provider_session.closed.wait(), timeout=2.0)
            except Exception:
                pass
        task = provider_session.receiver_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        idle_task = provider_session.idle_task
        if idle_task is not None and idle_task is not asyncio.current_task() and not idle_task.done():
            idle_task.cancel()
            await asyncio.gather(idle_task, return_exceptions=True)
        for transcript_task in (
            provider_session.user_transcript_flush_task,
            provider_session.assistant_transcript_flush_task,
        ):
            if transcript_task is not None and transcript_task is not asyncio.current_task() and not transcript_task.done():
                transcript_task.cancel()
                await asyncio.gather(transcript_task, return_exceptions=True)
        await provider_session.socket.close(code=1000, reason=reason[:120])
        if emit_event and not provider_session.closed.is_set():
            await self._emit(provider_session, "disconnected", {"reason": reason})

    async def _close_pending(self, pending: _PendingLiveConnection, *, reason: str) -> None:
        pending.closing = True
        task = pending.task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if pending.socket is not None:
            await pending.socket.close(code=1000, reason=reason[:120])

    @staticmethod
    async def _notify_pending_best_effort(
        pending: _PendingLiveConnection,
        event: VoiceProviderEvent,
    ) -> None:
        try:
            await pending.on_event(pending.session_id, pending.generation, event)
        except Exception:
            return

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
        delegation = provider_session.delegations.get(delegation_id)
        if delegation is not None and delegation.response_id:
            return delegation.response_id
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


def _group_transcript_fragments(
    fragments: list[_TranscriptFragment],
) -> list[list[_TranscriptFragment]]:
    ordered = sorted(
        fragments,
        key=lambda fragment: (
            fragment.start_ms is None,
            fragment.start_ms if fragment.start_ms is not None else fragment.arrival,
            fragment.arrival,
        ),
    )
    groups: list[list[_TranscriptFragment]] = []
    for fragment in ordered:
        if not groups:
            groups.append([fragment])
            continue
        previous_ends = [
            item.end_ms for item in groups[-1] if item.end_ms is not None
        ]
        previous_end = max(previous_ends) if previous_ends else None
        if (
            fragment.start_ms is not None
            and previous_end is not None
            and fragment.start_ms - previous_end > _TRANSCRIPT_TIMELINE_GAP_MS
        ):
            groups.append([fragment])
        else:
            groups[-1].append(fragment)
    return groups


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


openai_live_adapter = OpenAILiveAdapter()
