"""OpenAI GPT-Live WebRTC bootstrap and managed Responses sideband router."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
import hashlib
import json
import logging
import time
from typing import Any, Protocol
from urllib.parse import quote
from uuid import uuid4

import httpx

from app.config import settings
from app.services.jarvis_tool_catalog import get_jarvis_tool_definitions
from app.services.live_model_diagnostics import LiveModelDiagnostics, tool_metadata
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
    selector_response_observed_ns: int | None = None


@dataclass(slots=True)
class _Delegation:
    delegation_id: str
    origin_correlation_id: str
    offset_ms: int | None = None
    response_id: str | None = None
    origin_item_id: str | None = None
    requested_tier: str = "priority"
    tier_fallback: bool = False
    started_monotonic: float = field(default_factory=time.monotonic)
    received_emitted: bool = False


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
    tool_definitions: list[dict[str, Any]] = field(default_factory=list)
    receiver_task: asyncio.Task[None] | None = None
    idle_task: asyncio.Task[None] | None = None
    started: bool = False
    priority_unsupported: bool = False
    closing: bool = False
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    final_usage: dict[str, Any] | None = None
    delegations: dict[str, _Delegation] = field(default_factory=dict)
    responses: dict[tuple[str, str], _DelegatedResponse] = field(default_factory=dict)
    call_index: dict[str, tuple[str, str]] = field(default_factory=dict)
    handled_calls: set[tuple[str, str, str]] = field(default_factory=set)
    tool_tasks: set[asyncio.Task[None]] = field(default_factory=set)
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
    diagnostics: LiveModelDiagnostics | None = None
    notes_signature: str = ""


@dataclass(slots=True)
class _PendingLiveConnection:
    session_id: str
    generation: int
    provider_session_id: str
    api_key: str
    context: dict[str, Any]
    tool_definitions: list[dict[str, Any]]
    on_event: ProviderEventHandler
    connect_started: float
    provider_call_started: float
    provider_call_finished: float
    priority_rejection: str | None = None
    task: asyncio.Task[None] | None = None
    socket: LiveSocket | None = None
    closing: bool = False
    diagnostics: LiveModelDiagnostics | None = None


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
        session_configuration = self._session_config(context)
        tool_definitions = session_configuration["delegation"]["responses"]["tools"]
        diagnostics = LiveModelDiagnostics(settings.openai_live_diagnostics_dir, session_id=session_id, generation=generation,
            configuration={"voice_model": settings.openai_live_model, "selector_model": settings.openai_live_responses_model,
                "delegation_mode": "responses", "reasoning_effort": settings.openai_live_reasoning_effort,
                "reasoning_summary_requested": settings.openai_live_reasoning_summary,
                "requested_tools": tool_metadata(tool_definitions)}) if settings.openai_live_diagnostics_dir else None
        created = await self._session_creator(
            api_key=api_key,
            sdp_offer=sdp_offer,
            session_config=session_configuration,
        )
        provider_call_finished = time.monotonic()
        provider_session_id, sdp_answer = self._parse_creation_response(created)
        pending = _PendingLiveConnection(
            session_id=session_id,
            generation=generation,
            provider_session_id=provider_session_id,
            api_key=api_key,
            context=context,
            tool_definitions=tool_definitions,
            on_event=on_event,
            connect_started=connect_started,
            provider_call_started=provider_call_started,
            provider_call_finished=provider_call_finished,
            priority_rejection=created.get("_jarvis_priority_rejection"),
            diagnostics=diagnostics,
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
            logger = logging.getLogger(__name__)
            logger.info("Live sideband attaching for session %s generation %s", *key)
            socket = await self._websocket_connector(
                self._sideband_url(pending.provider_session_id),
                additional_headers={"Authorization": f"Bearer {pending.api_key}"},
                open_timeout=settings.openai_live_connect_timeout_seconds,
            )
            pending.socket = socket
            sideband_finished = time.monotonic()
            logger.info("Live sideband attached for session %s generation %s", *key)
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
                tool_definitions=pending.tool_definitions,
                connect_started=pending.connect_started,
                provider_call_started=pending.provider_call_started,
                provider_call_finished=pending.provider_call_finished,
                sideband_started=sideband_started,
                sideband_finished=sideband_finished,
                priority_unsupported=pending.priority_rejection is not None,
                diagnostics=pending.diagnostics,
                notes_signature=str((pending.context.get("assistant_notes") or {}).get("signature") or ""),
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
            # An authenticated sideband attaches to an already started Live
            # session. Do not require replay of the primary's session.started
            # event: a late attachment can miss that event entirely.
            await self._announce_ready(provider_session)
            if pending.priority_rejection:
                await self._emit(provider_session, "delegation_tier_fallback", {
                    "requested_tier": "priority", "fallback_tier": "default",
                    "reason": pending.priority_rejection, "stage": "session_creation"})
        except asyncio.CancelledError:
            if pending.socket is not None:
                await pending.socket.close(code=1000, reason="connection_cancelled")
            raise
        except Exception as exc:
            self._pending_connections.pop(key, None)
            self._sessions.pop(key, None)
            if provider_session is not None:
                await self._close_session(provider_session, reason="sideband_setup_failed", emit_event=False)
            elif pending.socket is not None:
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
        # Timing metadata stays in durable events/SSE. It is not model context
        # and would otherwise add tokens to every subsequent selector request.
        output = dict(output)
        output.pop("admission_timing", None)
        if isinstance(output.get("result"), dict):
            output["result"] = {key: value for key, value in output["result"].items() if key != "admission_timing"}
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
        attention_context = (
            context.get("attention_context")
            if isinstance(context.get("attention_context"), dict)
            else {"items": [], "total_pending": 0, "next_cursor": None, "deltas": []}
        )
        # Keep the delegated Responses model's checklist view authoritative
        # without replacing its fixed routing instructions.
        await self._send(
            provider_session,
            {
                "type": "session.update",
                "event_id": f"jarvis-live-attention-context:{request_id}",
                "session": {
                    "delegation": {
                        "type": "responses",
                        "responses": {
                            "instructions": self._router_instructions(context)
                        }
                    }
                },
            },
        )
        await self._send_notes_update(provider_session, request_id=request_id, facts=facts,
            notes=context["assistant_notes"], deltas=attention_context.get("deltas") or [])

    async def _send_notes_update(self, provider_session: _LiveSession, *, request_id: str,
                                 facts: tuple[dict[str, Any], ...], notes: dict[str, Any], deltas: list) -> None:
        """Requested outcomes as spoken-update commentary; everything else as refreshed notes."""
        from app.services.live_task_updates import announcement_text
        appends: list[tuple[str, str, tuple[str, ...], str]] = []
        quiet_ids: list[str] = []
        for fact in facts:
            presentation_id = str(fact.get("presentation_id") or "")
            verified = json.dumps(fact, separators=(",", ":"), ensure_ascii=False)
            if fact.get("commentary"):
                appends.append(("session.commentary.append", announcement_text(fact), (presentation_id,), verified))
            elif presentation_id:
                quiet_ids.append(presentation_id)
        if quiet_ids or deltas or notes.get("signature") != provider_session.notes_signature:
            verified = json.dumps({"assistant_notes": notes.get("signature"), "presentation_ids": quiet_ids},
                separators=(",", ":"))
            appends.append(("session.thinking.append", "(Private context, not a message to respond to.)\n" + notes["text"],
                tuple(quiet_ids), verified))
            provider_session.notes_signature = str(notes.get("signature") or "")
        if not appends:
            await self._emit(provider_session, "assistant_update_submitted", {"request_id": request_id,
                "presentation_ids": [], "response_kind": "task_update", "submission_ack": "no_context_to_append"},
                event_id=f"live-empty-update:{request_id}")
            return
        for index, (append_type, content, item_ids, verified) in enumerate(appends):
            event_id = f"jarvis-live-update:{request_id}:{index}"
            provider_session.pending_commentary[event_id] = (request_id, tuple(i for i in item_ids if i), verified)
            await self._send(provider_session, {"type": append_type, "event_id": event_id,
                "delegation_id": None, "content": content})

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
        elif pending is not None and emit_event:
            # A cancellation before sideband startup has no receiver to report
            # closure. The explicit API close must still commit its final state.
            await pending.on_event(session_id, generation, VoiceProviderEvent(
                kind="disconnected", payload={"reason": reason},
                event_id=f"live-pending-close:{uuid4()}"))

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
                    if provider_session.closed.is_set():
                        return
            if self._sessions.get(key) is provider_session and not provider_session.closing and not provider_session.closed.is_set():
                await self._report_transport_error(provider_session, {"reason": "sideband_closed", "stage": "sideband_receive"})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._sessions.get(key) is provider_session and not provider_session.closing and not provider_session.closed.is_set():
                await self._report_transport_error(provider_session, {"reason": type(exc).__name__, "stage": "sideband_receive"})
        finally:
            # A terminal sideband cannot serve another turn. Retire its socket,
            # idle watcher and tools even if durable event reporting was fenced
            # out by a replacement generation.
            if self._sessions.get(key) is provider_session and not provider_session.closing:
                try:
                    await self.close(session_id=provider_session.session_id,
                        generation=provider_session.generation, reason="sideband_ended", emit_event=False)
                except Exception as exc:
                    logging.getLogger(__name__).warning("Live terminal cleanup report rejected (%s)", type(exc).__name__)

    async def _report_transport_error(self, provider_session: _LiveSession, payload: dict[str, Any]) -> None:
        if provider_session.diagnostics:
            provider_session.diagnostics.record("voice", {"type": "selector.transport.closed", **payload})
            await provider_session.diagnostics.checkpoint("provider_transport_closed")
        try:
            await self._emit(provider_session, "provider_error", payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A superseded generation can reject the final socket-close report.
            # The durable coordinator fence remains authoritative; do not send
            # another error recursively or leave an unobserved receiver exception.
            logging.getLogger(__name__).warning("Live transport error report rejected (%s)", type(exc).__name__)

    async def _handle_event(self, provider_session: _LiveSession, outer: dict[str, Any]) -> None:
        event_type = str(outer.get("type") or "")
        if provider_session.diagnostics:
            if event_type == "response.event" and isinstance(outer.get("event"), dict):
                inner = outer["event"]
                delegation_id = str(outer.get("delegation_id") or "")
                response = inner.get("response") or {}
                provider_session.diagnostics.record("selector_managed", inner, delegation_id=delegation_id,
                    response_id=response.get("id") or inner.get("response_id") or self._response_id_for_delegation(provider_session, delegation_id))
            else:
                provider_session.diagnostics.record("voice", outer)
        if event_type == "session.started":
            await self._announce_ready(provider_session)
            return
        if event_type == "session.closed":
            if provider_session.diagnostics:
                await provider_session.diagnostics.checkpoint("provider_session_closed")
            await self._flush_transcript(provider_session, "user")
            await self._flush_transcript(provider_session, "assistant")
            usage = outer.get("usage")
            provider_session.final_usage = usage if isinstance(usage, dict) else None
            if provider_session.final_usage is not None:
                await self._emit(provider_session, "usage", {"usage": provider_session.final_usage})
            await self._emit(provider_session, "disconnected", {"reason": "session_closed"})
            # The close waiter may cancel the sideband receiver. Release it
            # only after the coordinator has committed the disconnected state.
            provider_session.closed.set()
            return
        if event_type == "session.input_transcript.delta":
            await self._handle_transcript_delta(provider_session, "user", outer)
            return
        if event_type == "session.input_transcript.done":
            await self._flush_transcript(provider_session, "user")
            return
        if event_type == "session.output_transcript.delta":
            await self._handle_transcript_delta(provider_session, "assistant", outer)
            return
        if event_type in {"session.commentary.appended", "session.thinking.appended"}:
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
                        "append_type": event_type,
                    },
                    event_id=f"live-commentary-ack:{client_event_id}",
                )
            return
        if event_type == "error":
            error = outer.get("error") or {}
            if (isinstance(error, dict) and error.get("code") in {"unsupported_service_tier", "service_tier_not_supported"}
                    and not provider_session.priority_unsupported):
                provider_session.priority_unsupported = True
                await self._send(provider_session, {"type": "session.update", "session": {
                    "delegation": {"type": "responses", "responses": {"service_tier": "default"}}}})
                await self._emit(provider_session, "delegation_tier_fallback", {
                    "requested_tier": "priority", "fallback_tier": "default", "reason": error["code"]})
                return
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
            if not observed.received_emitted:
                observed.received_emitted = True
                await self._emit(provider_session, "delegation_received", {
                    "delegation_id": delegation_id, "origin_correlation_id": observed.origin_correlation_id,
                    "stage": "delegation_received", "server_monotonic_ns": time.perf_counter_ns()})
            # A delegation offset beyond every fragment's end establishes a
            # timeline boundary without a fixed settling delay. Incomplete or
            # overlapping fragments still use the bounded gap fallback.
            fragments = provider_session.user_fragments
            if (observed.offset_ms is not None and fragments and
                    all(fragment.end_ms is not None and fragment.end_ms <= observed.offset_ms for fragment in fragments)):
                await self._flush_transcript(provider_session, "user")
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
            delegated.selector_response_observed_ns = time.perf_counter_ns()
            await self._resolve_delegation_origins(provider_session)
            await self._emit(provider_session, "admission_stage", {"stage": "selector_response_started",
                "server_monotonic_ns": time.perf_counter_ns(), "delegation_id": delegation_id,
                "origin_correlation_id": delegated.origin_correlation_id, "response_id": response_id,
                "clock": "server_monotonic", "request_start_observable": False})
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
        if inner_type in {"response.completed", "response.done", "response.failed", "response.cancelled"} and response_id:
            delegated = self._observe_delegation(
                provider_session, delegation_id, response_id
            )
            if delegated.terminal:
                return
            delegated.terminal = True
            delegation = self._delegation(provider_session, delegation_id)
            error = response.get("error") or inner.get("error") or {}
            unsupported = (isinstance(error, dict) and error.get("code") in {
                "unsupported_service_tier", "service_tier_not_supported"})
            if (unsupported and not provider_session.priority_unsupported and not delegated.calls
                    and not any(isinstance(item, dict) and item.get("type") == "function_call" for item in response.get("output") or [])):
                provider_session.priority_unsupported = True
                delegation.tier_fallback = True
                await self._send(provider_session, {"type": "session.update", "session": {
                    "delegation": {"type": "responses", "responses": {"service_tier": "default"}}}})
                await self._emit(provider_session, "delegation_tier_fallback", {
                    "delegation_id": delegation_id, "requested_tier": "priority", "fallback_tier": "default",
                    "reason": error.get("code")})
                await self._send(provider_session, {"type": "response.create",
                    "event_id": f"jarvis-tier-fallback:{delegation_id}"})
                return
            await self._emit(provider_session, "delegation_usage", {
                "delegation_id": delegation_id, "response_id": response_id,
                "requested_tier": "priority", "actual_tier": response.get("service_tier"),
                "fallback": provider_session.priority_unsupported, "usage": response.get("usage"),
                "cost": None, "cost_status": "unavailable",
                "latency_ms": (time.monotonic() - delegation.started_monotonic) * 1000})
            await self._continue_if_ready(provider_session, delegated)
            # This ends a nested Responses delegation, not the Live spoken turn.
            # Output transcript/audio may continue after this event.

    async def _collect_function_call(
        self,
        provider_session: _LiveSession,
        *,
        delegation_id: str,
        response_id: str,
        item: dict[str, Any],
    ) -> None:
        item_received_ns = time.perf_counter_ns()
        call_id = str(item.get("call_id") or item.get("id") or "")
        name = str(item.get("name") or "")
        if not call_id or not name:
            await self._emit(provider_session, "provider_protocol_warning", {"reason": "incomplete_function_call"})
            return
        from jsonschema import Draft202012Validator
        definition = next((tool for tool in provider_session.tool_definitions if tool["name"] == name), None)
        if definition is None:
            raise ValueError("Selector called a tool outside its frozen catalog")
        arguments = _parse_arguments(item.get("arguments"))
        Draft202012Validator(definition["parameters"]).validate(arguments)
        delegation = self._delegation(provider_session, delegation_id)
        observed = provider_session.responses[(delegation_id, response_id)]
        await self._emit(provider_session, "admission_stage", {
            "stage": "tool_arguments_complete", "server_monotonic_ns": time.perf_counter_ns(),
            "delegation_id": delegation_id, "origin_correlation_id": delegation.origin_correlation_id,
            "provider_call_id": item.get("call_id"), "tool_item_received_ns": item_received_ns,
            "selector_response_observed_ns": observed.selector_response_observed_ns,
            "tool_validation_ms": (time.perf_counter_ns() - item_received_ns) / 1_000_000})
        execution_key = (delegation_id, response_id, call_id)
        if execution_key in provider_session.handled_calls:
            return
        provider_session.handled_calls.add(execution_key)
        response_key = (delegation_id, response_id)
        delegated = provider_session.responses[response_key]
        delegated.calls.add(call_id)
        provider_session.call_index[call_id] = response_key
        payload = {
                "delegation_id": delegation_id,
                "response_id": response_id,
                "call_id": call_id,
                "name": name,
                "arguments": arguments,
                "origin_item_id": delegated.origin_item_id,
                "origin_correlation_id": delegated.origin_correlation_id,
                "origin_timeline": {
                    "offset_ms": delegated.timeline_start_ms,
                    "start_ms": delegated.timeline_start_ms,
                    "end_ms": delegated.timeline_end_ms,
                },
            }
        event_id = f"live-tool:{delegation_id}:{response_id}:{call_id}"
        if name in {"start_workflow", "continue_task", "dispatch_task"}:
            # Capture preflight can legitimately wait two seconds. Keep the
            # sideband receive loop free to observe transcript and terminal
            # events while the guarded admission runs.
            async def route_workflow() -> None:
                try:
                    await self._emit(provider_session, "shell_call", payload, event_id=event_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if not provider_session.closing:
                        await self._emit(provider_session, "provider_error", {"reason": "workflow_tool_route_failed"})

            task = asyncio.create_task(route_workflow(), name=f"live-workflow:{call_id}")
            provider_session.tool_tasks.add(task)
            task.add_done_callback(provider_session.tool_tasks.discard)
        else:
            await self._emit(provider_session, "shell_call", payload, event_id=event_id)

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
            {"delta": delta, "start_ms": start_ms, "end_ms": end_ms,
                "provider_event_id": event.get("event_id"),
                "server_monotonic_ns": time.perf_counter_ns(), "stage": f"{speaker}_transcript_fragment"},
        )

    async def _flush_transcript_after_gap(
        self, provider_session: _LiveSession, speaker: str
    ) -> None:
        try:
            await asyncio.sleep(_TRANSCRIPT_GAP_SECONDS)
            await self._flush_transcript(provider_session, speaker)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._report_transport_error(provider_session, {
                "reason": type(exc).__name__, "stage": "transcript_flush", "speaker": speaker})

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
                    "stage": "transcript_ready", "server_monotonic_ns": time.perf_counter_ns(),
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
        try:
            await self._flush_transcript(provider_session, "user")
            await self._flush_transcript(provider_session, "assistant")
        finally:
            # A superseded generation can reject transcript persistence. The
            # rejection must not strand its socket or background work after
            # close() has removed the session from the adapter's registry.
            provider_session.closing = True
            task = provider_session.receiver_task
            if provider_session.started and not provider_session.closed.is_set():
                try:
                    await provider_session.socket.send(json.dumps({"type": "session.close", "event_id": f"jarvis-live-close:{uuid4()}"}))
                    # An ended receiver (or this receiver's own finalizer)
                    # cannot consume a closing acknowledgment.
                    if task is not None and task is not asyncio.current_task() and not task.done():
                        await asyncio.wait_for(provider_session.closed.wait(), timeout=2.0)
                except Exception:
                    pass
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
            pending_tools = tuple(task for task in provider_session.tool_tasks if not task.done())
            for task in pending_tools:
                task.cancel()
            if pending_tools:
                await asyncio.gather(*pending_tools, return_exceptions=True)
            try:
                await provider_session.socket.close(code=1000, reason=reason[:120])
                if emit_event and not provider_session.closed.is_set():
                    await self._emit(provider_session, "disconnected", {"reason": reason})
            finally:
                if provider_session.diagnostics:
                    await provider_session.diagnostics.flush(reason)

    async def _close_pending(self, pending: _PendingLiveConnection, *, reason: str) -> None:
        pending.closing = True
        task = pending.task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if pending.socket is not None:
            await pending.socket.close(code=1000, reason=reason[:120])
        if pending.diagnostics:
            await pending.diagnostics.flush(reason)

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

    async def _announce_ready(self, provider_session: _LiveSession) -> None:
        if provider_session.started or provider_session.closing or provider_session.closed.is_set():
            return
        provider_session.started = True
        await self._emit(provider_session, "ready", {
            "provider_session_id": provider_session.provider_session_id,
            "activation_id": provider_session.activation_id,
            "defer_initial_updates": True,
            "delegation_mode": "responses",
            "readiness_source": "authenticated_sideband_attachment",
            "timing_ms": {
                "provider_call": round((provider_session.provider_call_finished
                    - provider_session.provider_call_started) * 1_000, 3),
                "sideband_connect": round((provider_session.sideband_finished
                    - provider_session.sideband_started) * 1_000, 3),
                "total_to_sideband_ready": round((time.monotonic()
                    - provider_session.connect_started) * 1_000, 3),
            },
        })

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
        from app.services.live_task_updates import NATURAL_UPDATE_PROMPT
        conversation_prompt = NATURAL_UPDATE_PROMPT + (
            " An explicit request to create, save, start or continue a task requires backend tools. "
            "When the requested outcome is undefined, ask a short clarification before creating a task. "
            "A request to save something useful does not specify which result the user wants; do not choose it for them. "
            "A summary, report, plan, checklist or recipe is a defined outcome even without a file format. "
            "Use the matching workflow's result type, or a Document for ordinary prose; do not ask note versus document. "
            "The spoken answer must not replace the requested durable task. "
            "Existing completed or cancelled tasks do not satisfy a request to create new tasks. "
            "Acknowledging an update never creates, continues or cancels a task. "
            "Handled or superseded items are not announced proactively again."
        )
        if context.get("activation_source") == "wake_word":
            conversation_prompt += (
                " The Android interface has already acknowledged this wake activation. "
                "A wake name or greeting alone is an activation cue. Keep listening quietly "
                "until the user gives a request; do not speak a startup greeting over their first command. "
                "Answer substantive conversation normally; listen to the complete actionable request before delegating."
            )
        catalog = get_jarvis_tool_definitions("openai_live")
        workflow = next((tool for tool in catalog if tool["name"] == "start_workflow"), None)
        if workflow:
            conversation_prompt += " Screen work requires backend delegation. Available workflows: " + workflow["description"]
        if settings.task_orchestration_enabled:
            conversation_prompt += (
                " Background orchestration is the sole automatic worker-feedback owner. "
                "Task updates are facts for briefing, not requests to autonomously answer, retry or repair tasks. "
                "Only a direct user request authorizes answering worker questions, retrying or repairing tasks; "
                "recording communicated outcomes is separate delivery bookkeeping. User answers and controls remain immediate."
            )
        notes = context.get("assistant_notes")
        if isinstance(notes, dict) and notes.get("text"):
            # Live reads one readable notes view; the router keeps the JSON checklist.
            conversation_prompt += "\n\n" + notes["text"]
        return {
            "model": settings.openai_live_model,
            "instructions": conversation_prompt,
            "audio": {"output": {"voice": voice}},
            "delegation": {
                "type": "responses",
                "responses": OpenAILiveAdapter._router_configuration(context),
            },
        }

    @staticmethod
    def _router_configuration(context: dict[str, Any]) -> dict[str, Any]:
        return {
            "model": settings.openai_live_responses_model,
            "service_tier": "priority",
            "instructions": OpenAILiveAdapter._router_instructions(context),
            "reasoning": {"effort": settings.openai_live_reasoning_effort,
                **({"summary": "auto"} if settings.openai_live_reasoning_summary else {})},
            "tools": get_jarvis_tool_definitions("openai_live"),
            "tool_choice": "auto",
            "parallel_tool_calls": False,
        }

    @staticmethod
    def _router_instructions(context: dict[str, Any]) -> str:
        router_prompt = (
            "Interpret actionable requests and use only the supplied Jarvis tools. The application "
            "owns authorization, execution, and durable state. Prefer dispatch_task for substantial "
            "work. Return concise speech-ready facts and never invent task state or success. "
            "If the user explicitly asks to create or save a task, call dispatch_task or the matching workflow. "
            "First clarify an undefined requested outcome; do not invent a result or workflow for a vague request to save something useful. "
            "A summary, report, plan, checklist or recipe is a defined outcome even without a file format. "
            "Use the matching workflow's result type, or a Document for ordinary prose; do not ask note versus document. "
            "Do not substitute inline advice or a spoken comparison for that durable outcome. "
            "For each explicit requested outcome choose a stable intent_id within this user turn. "
            "Reuse it on replay and never submit the same intent through another admission tool. "
            "Use separate IDs for distinct outcomes. Follow-ups continue the existing task with retained evidence. "
            "For straightforward requests submit the minimum required arguments in one decision. "
            "Only fields listed in the tool schema's required array are mandatory. "
            "Keep goal concise and faithful; the worker develops the detailed solution. "
            "Omit optional result, execution_preferences, targets, importance and presentation_policy unless the request or context needs them. "
            "For a screen task call start_workflow directly: the backend prepares invocation and latest screenshots in parallel. "
            "The workflow worker inspects those images after admission; you do not need to see or identify their contents to route a defined outcome. "
            "A request to save a recipe from visible ingredients or research the visible topic specifies an outcome; admit it without reconfirmation. "
            "An explicit current-screen request selects screen_context_mode fresh in start_workflow or continue_task; "
            "no separate capture call is needed. Ordinary continuations omit screen_context_mode. "
            "Choose optional execution_preferences in this delegation; changed model or effort needs override_reason. "
            "Use get_attention_checklist for pending outcomes. Do not read identifiers aloud. "
            "Existing completed or cancelled tasks do not satisfy a request to create new tasks. "
            "Acknowledging an update never creates, continues or cancels a task."
        )
        if settings.task_orchestration_enabled:
            router_prompt += (
                " Background orchestration exclusively handles automatic worker questions and recovery. "
                "Do not initiate answers/retries/repairs from a task update or quiet context. "
                "Apply these tools immediately when the user explicitly requests an answer or control."
            )
        situation = context.get("situation")
        if isinstance(situation, dict) and situation:
            router_prompt += " Current application context: " + json.dumps(
                situation, separators=(",", ":"), ensure_ascii=False
            )[:8_000]
        attention_context = context.get("attention_context")
        if isinstance(attention_context, dict):
            router_prompt += " CURRENT_ATTENTION_CHECKLIST=" + json.dumps(
                attention_context,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        from app.services.live_task_updates import backend_policy
        return backend_policy(router_prompt)

    async def _default_session_creator(
        self, *, api_key: str, sdp_offer: str, session_config: dict[str, Any]
    ) -> dict[str, Any]:
        client = await self._shared_http_client()
        response = await client.post(
            f"{settings.openai_live_base_url.rstrip('/')}/live/sessions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"session": session_config, "transport": {"type": "webrtc", "sdp": sdp_offer}},
        )
        priority_rejection = None
        if response.status_code in {400, 422}:
            try:
                error = response.json().get("error", {})
            except (ValueError, AttributeError):
                error = {}
            code = error.get("code") if isinstance(error, dict) else None
            if (code in {"unsupported_service_tier", "service_tier_not_supported"}
                    and session_config.get("delegation", {}).get("responses", {}).get("service_tier") == "priority"):
                priority_rejection = code
                fallback = deepcopy(session_config)
                fallback["delegation"]["responses"]["service_tier"] = "default"
                response = await client.post(
                    f"{settings.openai_live_base_url.rstrip('/')}/live/sessions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"session": fallback, "transport": {"type": "webrtc", "sdp": sdp_offer}},
                )
        if response.status_code != 201:
            raise RuntimeError(f"OpenAI GPT-Live session creation failed with HTTP {response.status_code}")
        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError("OpenAI GPT-Live returned an invalid response")
        if priority_rejection:
            result["_jarvis_priority_rejection"] = priority_rejection
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
    except (TypeError, ValueError) as exc:
        raise ValueError("Tool arguments are not complete JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Tool arguments must be a JSON object")
    return parsed


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
