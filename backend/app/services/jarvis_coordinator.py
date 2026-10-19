"""Durable provider-neutral coordinator for the live Jarvis workspace."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import time
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from app.config import settings
from app.db.repositories.conversations import ConversationRepository
from app.db.repositories.jarvis import JarvisDomainRepository
from app.db.session import session_scope
from app.db.tables import (
    Conversation,
    ConversationEvent,
    ConversationMessage,
    JarvisPreference,
    JarvisDevice,
    JarvisState,
    LiveSession,
    SpeechDelivery,
    Task,
    TaskPresentation,
    TaskQuestion,
    LiveSessionEvent,
)
from app.services.jarvis_brain import restricted_brain_gateway
from app.services.jarvis_live_telemetry import jarvis_live_telemetry
from app.services.task_error_messages import public_tool_error
from app.services.jarvis_daily import JarvisAcceptedTurn, jarvis_daily_service
from app.services.jarvis_time import normalize_client_context
from app.services.jarvis_wake import verify_wake_transcript
from app.services.jarvis_situation import jarvis_situation_projector
from app.services.runtime_health import runtime_health_service
from app.services.task_presentations import task_presentation_service
from app.services.task_notifications import route_pending_task_notifications
from app.services.voice_session_adapter import (
    VoiceProviderEvent,
    fake_voice_session_adapter,
    get_voice_session_adapter,
)


logger = logging.getLogger(__name__)

ACTIVE_SESSION_STATES = {"connecting", "ready"}
FLOOR_EVENT_TYPES = {
    "ready": "listening",
    "user_speaking": "user_speaking",
    # Speech has ended, but this event is never a response boundary. Provider
    # commit remains authoritative for user-turn generation; the floor can be
    # released for already-queued task updates.
    "user_stopped": "listening",
    "assistant_generating": "thinking",
    "assistant_speaking": "assistant_speaking",
    "assistant_transcript_delta": "assistant_speaking",
    "assistant_transcript": "listening",
    "assistant_response": "listening",
    "assistant_interrupted": "listening",
    "disconnected": "text_fallback",
    "provider_error": "text_fallback",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_connection_failure(exc: Exception) -> str:
    """Keep enough provider context to diagnose startup without storing credentials."""
    detail = " ".join(str(exc).split()).strip()
    api_key = settings.openai_api_key.strip()
    if api_key:
        detail = detail.replace(api_key, "[redacted]")
    return detail[:500] or "Live provider connection failed"


class StaleSessionError(ValueError):
    pass


class ActivationConflictError(ValueError):
    """An activation id is currently owned by another session generation."""


@dataclass(frozen=True, slots=True)
class SessionRecord:
    id: str
    conversation_id: str
    generation: int
    provider_adapter: str
    lifecycle_state: str
    controller_id: str | None
    activation_id: str | None
    activation_source: str | None
    workflow_catalog_revision: str | None
    floor_state: str
    last_event_sequence: int
    created_at: datetime
    ready_at: datetime | None
    disconnected_at: datetime | None
    client_time_zone: str | None
    client_utc_offset_minutes: int | None
    client_observed_at: datetime | None


@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    id: str
    turn_id: str | None
    state: str


@dataclass(frozen=True, slots=True)
class ControllerBootstrap:
    session: SessionRecord
    sdp_answer: str | None = None
    provider_capabilities: dict[str, bool] = field(default_factory=dict)


@dataclass(slots=True)
class _ResponseAttempt:
    request_id: str
    reason: str
    presentation_ids: tuple[str, ...]
    state: str
    started_monotonic: float
    last_activity_monotonic: float
    provider_response_id: str | None = None
    activation_id: str | None = None
    input_item_id: str | None = None
    cancel_requested_monotonic: float | None = None
    watchdog_task: asyncio.Task[None] | None = None


@dataclass(frozen=True, slots=True)
class _BargeInIntent:
    speech_event_id: str
    speech_item_id: str | None
    request_id: str
    response_id_at_onset: str | None
    scheduled_monotonic: float
    source: str = "backend_fallback"


@dataclass(slots=True)
class _ResponseGate:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: set[str] = field(default_factory=set)
    attempt: _ResponseAttempt | None = None
    barge_in_task: asyncio.Task[None] | None = None


@dataclass(slots=True)
class _WakeActivation:
    activation_id: str
    wake_score: float
    gate_mode: str = "transcript_rules"
    verification_state: str = "pending"
    lifecycle_state: str = "candidate"
    registered_monotonic: float = field(default_factory=time.monotonic)
    committed_monotonic: float | None = None
    primary_input_item_id: str | None = None
    candidate_item_ids: set[str] = field(default_factory=set)
    response_requested_for: set[str] = field(default_factory=set)
    speech_stopped: bool = False
    response_requested: bool = False
    gate_started_monotonic: float = field(default_factory=time.monotonic)
    gate_timeout_started_monotonic: float | None = None
    benchmark_run_id: str | None = None
    benchmark_fixture: str | None = None
    legacy_compat: bool = False


_RESPONSE_PRIORITY = {
    "tool_continuation": 0,
    "user_turn": 1,
    "task_update": 2,
}
_WAKE_GATE_TIMEOUT_SECONDS = 2.0


class JarvisCoordinator:
    def __init__(self) -> None:
        self._response_gates: dict[tuple[str, int], _ResponseGate] = {}
        self._wake_activations: dict[tuple[str, int], _WakeActivation] = {}
        self._wake_activation_seen: set[tuple[str, int]] = set()
        self._wake_verification_tasks: dict[tuple[str, int], asyncio.Task[None]] = {}
        self._provider_cleanup_tasks: set[asyncio.Task[None]] = set()
        self._background_response_tasks: set[asyncio.Task[None]] = set()
        # Process-local identity protection. Durable session events remain the
        # audit trail; the tombstone only prevents a late browser retry from
        # being attached to a different session generation.
        self._activation_index: dict[str, tuple[str, int, float]] = {}
        self._activation_tombstone_seconds = 5 * 60
        self._activation_index_limit = 1_024
        self._handled_input_items: dict[tuple[str, int], dict[str, float]] = {}
        self._provider_input_turns: dict[tuple[str, int, str], str] = {}
        self._provider_correlation_turns: dict[tuple[str, int, str], str] = {}
        self._provider_correlation_tasks: dict[tuple[str, int, str], set[str]] = {}
        self._barge_in_keys: set[tuple[str, int, str, str]] = set()
        self._attention_context_fingerprints: dict[tuple[str, int], str] = {}
        self._attention_context_pending: dict[
            tuple[str, int], tuple[str, float]
        ] = {}
        self._attention_context_items: dict[tuple[str, int], dict[str, str]] = {}
        self._attention_appended_ids: dict[tuple[str, int], set[str]] = {}
        self._delivery_task: asyncio.Task[None] | None = None
        self._delivery_stop: asyncio.Event | None = None

    def _trace_response_gate(
        self,
        event_type: str,
        *,
        session_id: str,
        generation: int,
        reason: str,
        request_id: str | None = None,
        duration_ms: float | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        activation = self._wake_activations.get((session_id, generation))
        jarvis_live_telemetry.record({
            "trace_id": session_id,
            "activation_id": None if activation is None else activation.activation_id,
            "session_id": session_id,
            "generation": generation,
            "request_id": request_id,
            "provider_response_id": None,
            "source": "backend",
            "event_type": event_type,
            "observed_at": utc_now(),
            "monotonic_ms": round(time.monotonic() * 1_000, 3),
            "duration_ms": None if duration_ms is None else round(duration_ms, 3),
            "detail_json": {"reason": reason, **(detail or {})},
        })

    async def start(self) -> None:
        if self._delivery_task is not None and not self._delivery_task.done():
            return
        await task_presentation_service.recover_reservations()
        await runtime_health_service.healthy("response_arbiter", force=True)
        await runtime_health_service.healthy("notification_router", force=True)
        self._delivery_stop = asyncio.Event()
        self._delivery_task = asyncio.create_task(
            self._run_delivery_loop(self._delivery_stop),
            name="jarvis-response-arbiter",
        )

    async def stop(self) -> None:
        if self._delivery_stop is not None:
            self._delivery_stop.set()
        if self._delivery_task is not None:
            await asyncio.gather(self._delivery_task, return_exceptions=True)
        self._delivery_task = None
        self._delivery_stop = None
        verification_tasks = tuple(self._wake_verification_tasks.values())
        for task in verification_tasks:
            task.cancel()
        if verification_tasks:
            await asyncio.gather(*verification_tasks, return_exceptions=True)
        self._wake_verification_tasks.clear()
        self._wake_activations.clear()
        self._wake_activation_seen.clear()
        self._activation_index.clear()
        self._handled_input_items.clear()
        self._provider_input_turns.clear()
        self._provider_correlation_turns.clear()
        self._provider_correlation_tasks.clear()
        self._barge_in_keys.clear()
        self._attention_context_fingerprints.clear()
        self._attention_context_pending.clear()
        self._attention_context_items.clear()
        self._attention_appended_ids.clear()
        response_tasks: list[asyncio.Task[None]] = []
        for gate in self._response_gates.values():
            if gate.barge_in_task is not None and not gate.barge_in_task.done():
                response_tasks.append(gate.barge_in_task)
            if (
                gate.attempt is not None
                and gate.attempt.watchdog_task is not None
                and not gate.attempt.watchdog_task.done()
            ):
                response_tasks.append(gate.attempt.watchdog_task)
        for task in response_tasks:
            task.cancel()
        if response_tasks:
            await asyncio.gather(*response_tasks, return_exceptions=True)
        self._response_gates.clear()
        provider_cleanup_tasks = tuple(self._provider_cleanup_tasks)
        for task in provider_cleanup_tasks:
            task.cancel()
        if provider_cleanup_tasks:
            await asyncio.gather(*provider_cleanup_tasks, return_exceptions=True)
        self._provider_cleanup_tasks.clear()
        background_response_tasks = tuple(self._background_response_tasks)
        for task in background_response_tasks:
            task.cancel()
        if background_response_tasks:
            await asyncio.gather(*background_response_tasks, return_exceptions=True)
        self._background_response_tasks.clear()
        await runtime_health_service.offline("response_arbiter", detail="Response arbiter stopped.")
        await runtime_health_service.offline("notification_router", detail="Notification router stopped.")

    @staticmethod
    def _wake_activation(value: object | None) -> _WakeActivation | None:
        if value is None:
            return None
        if isinstance(value, dict):
            source = value.get("source")
            raw_score = value.get("wake_score")
            raw_activation_id = value.get("activation_id")
        else:
            source = getattr(value, "source", None)
            raw_score = getattr(value, "wake_score", None)
            raw_activation_id = getattr(value, "activation_id", None)
        if source != "wake_word" or raw_score is None:
            return None
        try:
            wake_score = max(0.0, min(1.0, float(raw_score)))
        except (TypeError, ValueError):
            return None
        legacy_compat = not bool(raw_activation_id)
        activation_id = str(raw_activation_id).strip() if raw_activation_id else str(uuid4())
        gate_mode = str(
            value.get("gate_mode", "transcript_rules")
            if isinstance(value, dict)
            else getattr(value, "gate_mode", "transcript_rules")
        )
        if gate_mode not in {"transcript_rules", "immediate"}:
            gate_mode = "transcript_rules"
        benchmark_run_id = (
            value.get("benchmark_run_id") if isinstance(value, dict)
            else getattr(value, "benchmark_run_id", None)
        )
        benchmark_fixture = (
            value.get("benchmark_fixture") if isinstance(value, dict)
            else getattr(value, "benchmark_fixture", None)
        )
        return _WakeActivation(
            activation_id=activation_id,
            wake_score=wake_score,
            gate_mode=gate_mode,
            verification_state="accepted" if gate_mode == "immediate" else "pending",
            benchmark_run_id=(str(benchmark_run_id).strip() or None) if benchmark_run_id else None,
            benchmark_fixture=(str(benchmark_fixture).strip() or None) if benchmark_fixture else None,
            legacy_compat=legacy_compat,
        )

    def _schedule_wake_verification(self, session_id: str, generation: int) -> None:
        key = (session_id, generation)
        existing = self._wake_verification_tasks.get(key)
        if existing is not None and not existing.done():
            return

        async def reject_after_timeout() -> None:
            try:
                await asyncio.sleep(_WAKE_GATE_TIMEOUT_SECONDS)
                activation = self._wake_activations.get(key)
                if (
                    activation is None
                    or activation.verification_state != "pending"
                    or (
                        activation.primary_input_item_id is None
                        and not (activation.legacy_compat and activation.speech_stopped)
                    )
                ):
                    return
                activation.verification_state = "timed_out"
                activation.lifecycle_state = "terminal"
                decision_monotonic = time.monotonic()
                async with session_scope() as session:
                    realtime = await session.get(LiveSession, session_id)
                    if (
                        realtime is None
                        or realtime.generation != generation
                        or realtime.lifecycle_state not in ACTIVE_SESSION_STATES
                    ):
                        return
                    await JarvisDomainRepository(session).append_session_event(
                        session_id=session_id,
                        event_type="wake.gate.timed_out",
                        payload_json={
                            "reason": "gate_timeout",
                            "wake_score": activation.wake_score,
                            "gate_mode": activation.gate_mode,
                            "activation_id": activation.activation_id,
                            "wake_term_present": False,
                            "prefix_classification": "unavailable",
                            "transcript_word_count": 0,
                            "gate_duration_ms": round(
                                (decision_monotonic - activation.gate_started_monotonic) * 1_000,
                                3,
                            ),
                            "registration_to_gate_decision_ms": round(
                                (decision_monotonic - activation.registered_monotonic) * 1_000,
                                3,
                            ),
                            "commit_to_gate_decision_ms": (
                                round(
                                    (decision_monotonic - activation.gate_timeout_started_monotonic) * 1_000,
                                    3,
                                )
                                if activation.gate_timeout_started_monotonic is not None
                                else None
                            ),
                            "session_id": session_id,
                            "generation": generation,
                        },
                        idempotency_key=f"wake-gate-timeout:{session_id}:{generation}",
                    )
                await self._discard_candidate_items(session_id, generation, activation)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._trace_response_gate(
                    "response.arbiter.failed",
                    session_id=session_id,
                    generation=generation,
                    reason="wake_gate_timeout",
                    detail={"failure_type": type(exc).__name__},
                )
                logger.exception(
                    "Wake gate timeout failed for session %s generation %s",
                    session_id,
                    generation,
                )
                # A failed verification fallback cannot bring down provider event handling.
                return
            finally:
                if self._wake_verification_tasks.get(key) is asyncio.current_task():
                    self._wake_verification_tasks.pop(key, None)

        self._wake_verification_tasks[key] = asyncio.create_task(
            reject_after_timeout(),
            name=f"jarvis-wake-gate:{session_id}:{generation}",
        )

    def _cancel_wake_verification(self, session_id: str, generation: int) -> None:
        task = self._wake_verification_tasks.pop((session_id, generation), None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _clear_wake_activation(self, session_id: str, generation: int) -> None:
        self._cancel_wake_verification(session_id, generation)
        self._wake_activations.pop((session_id, generation), None)

    def _tombstone_activation_ids(self, session_id: str, generation: int) -> None:
        expires_at = time.monotonic() + self._activation_tombstone_seconds
        for activation_id, (indexed_session, indexed_generation, _) in tuple(
            self._activation_index.items()
        ):
            if (indexed_session, indexed_generation) == (session_id, generation):
                self._activation_index[activation_id] = (session_id, generation, expires_at)

    def _prune_activation_index(self) -> None:
        now = time.monotonic()
        expired = [key for key, (_, _, expiry) in self._activation_index.items() if expiry <= now]
        for key in expired:
            self._activation_index.pop(key, None)
        if len(self._activation_index) <= self._activation_index_limit:
            return
        overflow = len(self._activation_index) - self._activation_index_limit
        for key, _ in sorted(self._activation_index.items(), key=lambda item: item[1][2])[:overflow]:
            self._activation_index.pop(key, None)

    def _mark_input_item_handled(
        self,
        session_id: str,
        generation: int,
        item_id: str,
    ) -> bool:
        if not item_id:
            return False
        key = (session_id, generation)
        handled = self._handled_input_items.setdefault(key, {})
        if item_id in handled:
            return False
        handled[item_id] = time.monotonic()
        while len(handled) > 128:
            handled.pop(next(iter(handled)))
        return True

    async def _discard_candidate_items(
        self,
        session_id: str,
        generation: int,
        activation: _WakeActivation,
    ) -> None:
        if not activation.candidate_item_ids:
            return
        session = await self.get_session(session_id)
        if session is None or session.generation != generation:
            return
        try:
            await get_voice_session_adapter(session.provider_adapter).discard_candidate_items(
                session_id=session_id,
                generation=generation,
                item_ids=tuple(sorted(activation.candidate_item_ids)),
            )
        except Exception:
            logger.exception(
                "Unable to discard rejected wake candidate items for session %s generation %s",
                session_id,
                generation,
            )

    async def _run_delivery_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            voice_connected = False
            voice_floor_held = False
            stale_voice_floor = False
            try:
                await task_presentation_service.sync_candidates()
                active = await self.get_active_session()
                if active is not None and active.lifecycle_state == "ready":
                    adapter = get_voice_session_adapter(active.provider_adapter)
                    voice_connected = adapter.is_connected(
                        session_id=active.id,
                        generation=active.generation,
                    )
                    if voice_connected:
                        async with session_scope() as session:
                            floor, floor_at = await self._floor_observation(session, active.id)
                        busy_floor = floor in {
                            "user_speaking", "assistant_speaking", "thinking", "connecting"
                        }
                        if busy_floor and floor_at is not None:
                            observed_at = (
                                floor_at
                                if floor_at.tzinfo is not None
                                else floor_at.replace(tzinfo=timezone.utc)
                            )
                            stale_voice_floor = observed_at <= utc_now() - timedelta(
                                seconds=max(
                                    0.0,
                                    settings.jarvis_notification_floor_stale_seconds,
                                )
                            )
                        voice_floor_held = busy_floor and not stale_voice_floor
                        pending = await task_presentation_service.list_pending(include_terminal_context=active.provider_adapter == "openai_live")
                        fingerprint = task_presentation_service.semantic_fingerprint(pending)
                        if settings.jarvis_conversation_observer_mode == "enabled":
                            # Promises are part of Jarvis's notes; a change refreshes them too.
                            from app.services import assistant_notes
                            async with session_scope() as session:
                                fingerprint += ":" + await assistant_notes.promise_signature(session)
                        attention_key = (active.id, active.generation)
                        previous_fingerprint = self._attention_context_fingerprints.get(attention_key)
                        if fingerprint != previous_fingerprint:
                            observed = self._attention_context_pending.get(attention_key)
                            now_monotonic = time.monotonic()
                            if observed is None or observed[0] != fingerprint:
                                # Hold a short coalescing window so a burst of
                                # projection changes becomes one quiet context
                                # append rather than a sequence of stale facts.
                                self._attention_context_pending[attention_key] = (
                                    fingerprint,
                                    now_monotonic,
                                )
                            elif now_monotonic - observed[1] >= 0.25:
                                current_items = {
                                    item.id: task_presentation_service.semantic_item_fingerprint(item)
                                    for item in pending
                                }
                                previously_synced = self._attention_context_items.get(
                                    attention_key, {}
                                )
                                appended = self._attention_appended_ids.setdefault(
                                    attention_key, set()
                                )
                                for presentation_id, item_fingerprint in current_items.items():
                                    if previously_synced.get(presentation_id) != item_fingerprint:
                                        appended.discard(presentation_id)
                                await self._queue_controlled_response(
                                    active.id,
                                    active.generation,
                                    "task_update",
                                )
                                self._attention_context_fingerprints[attention_key] = fingerprint
                                self._attention_context_pending.pop(attention_key, None)
                        else:
                            self._attention_context_pending.pop(attention_key, None)
                attempts = [
                    gate.attempt
                    for gate in self._response_gates.values()
                    if gate.attempt is not None
                ]
                arbiter_detail = "Idle; no response is in flight."
                if attempts:
                    oldest = min(attempts, key=lambda item: item.started_monotonic)
                    age_seconds = max(0.0, time.monotonic() - oldest.started_monotonic)
                    arbiter_detail = (
                        f"{len(attempts)} response in flight; oldest is "
                        f"{oldest.state} for {age_seconds:.1f} seconds."
                    )
                await runtime_health_service.healthy(
                    "response_arbiter",
                    detail=arbiter_detail,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Durable candidates remain pending; the next poll retries them.
                await runtime_health_service.failed("response_arbiter", exc)
            try:
                await route_pending_task_notifications(
                    voice_connected=voice_connected and not stale_voice_floor,
                    voice_floor_held=voice_floor_held,
                )
                await runtime_health_service.healthy(
                    "notification_router",
                    detail="Notification fallback routing is available.",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await runtime_health_service.failed("notification_router", exc)
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=max(0.05, settings.jarvis_update_poll_seconds),
                )
            except TimeoutError:
                pass

    async def accept_text_turn(
        self,
        content: str,
        *,
        idempotency_key: str,
    ) -> JarvisAcceptedTurn:
        accepted = await jarvis_daily_service.accept_text_turn(
            content,
            idempotency_key=idempotency_key,
        )
        if not accepted.created:
            return accepted
        active = await self.get_active_session()
        if active is None or active.conversation_id != accepted.conversation.id:
            active = await self.open_controller(
                conversation_id=accepted.conversation.id,
                controller_id="typed-bridge",
                adapter_name="fake",
            )
        adapter = get_voice_session_adapter(active.provider_adapter)
        await adapter.send_user_text(
            session_id=active.id,
            generation=active.generation,
            turn_id=accepted.message.id,
            text=accepted.message.content,
        )
        await self._queue_controlled_response(active.id, active.generation, "user_turn")
        return accepted

    async def _maybe_queue_wake_response(
        self,
        session_id: str,
        generation: int,
        activation: _WakeActivation,
    ) -> None:
        """Authorize the first wake response from one accepted committed item."""
        current = self._wake_activations.get((session_id, generation))
        item_id = activation.primary_input_item_id
        if (
            current is not activation
            or activation.verification_state != "accepted"
            or activation.lifecycle_state == "terminal"
            or not item_id
            or item_id in activation.response_requested_for
        ):
            return
        gate = self._response_gates.setdefault((session_id, generation), _ResponseGate())
        async with gate.lock:
            current = self._wake_activations.get((session_id, generation))
            item_id = activation.primary_input_item_id
            if (
                current is not activation
                or activation.verification_state != "accepted"
                or activation.lifecycle_state == "terminal"
                or not item_id
                or item_id in activation.response_requested_for
            ):
                return
            activation.response_requested_for.add(item_id)
            activation.response_requested = True
            activation.lifecycle_state = "conversation"
        try:
            await self._queue_controlled_response(
                session_id,
                generation,
                "user_turn",
                activation_id=activation.activation_id,
                input_item_id=item_id,
            )
        except Exception:
            async with gate.lock:
                activation.response_requested_for.discard(item_id)
                activation.response_requested = bool(activation.response_requested_for)
                if not activation.response_requested:
                    activation.lifecycle_state = "candidate"
            raise

    async def _queue_controlled_response(
        self,
        session_id: str,
        generation: int,
        reason: str,
        *,
        enqueue: bool = True,
        activation_id: str | None = None,
        input_item_id: str | None = None,
    ) -> None:
        request_started = time.monotonic()
        gate_acquired = request_started
        snapshot_started = request_started
        snapshot_finished = request_started
        candidate_sync_started = request_started
        candidate_sync_finished = request_started
        situation_started = request_started
        situation_finished = request_started
        reservation_started = request_started
        reservation_finished = request_started
        key = (session_id, generation)
        activation = self._wake_activations.get(key)
        if (
            reason == "user_turn"
            and activation is not None
            and activation.verification_state != "accepted"
        ):
            self._trace_response_gate(
                "response.arbiter.blocked",
                session_id=session_id,
                generation=generation,
                reason=reason,
                detail={"blocked_by": "wake_gate", "gate_state": activation.verification_state},
            )
            return
        gate = self._response_gates.setdefault(key, _ResponseGate())
        adapter = None
        reservation = None
        context = None
        attention_item_fingerprints: dict[str, str] | None = None
        selected_reason = reason
        self._trace_response_gate(
            "response.arbiter.requested" if enqueue else "response.arbiter.retry_requested",
            session_id=session_id,
            generation=generation,
            reason=reason,
        )
        try:
            async with gate.lock:
                gate_acquired = time.monotonic()
                if enqueue:
                    gate.pending.add(reason)
                elif reason not in gate.pending:
                    self._trace_response_gate(
                        "response.arbiter.duplicate_ignored",
                        session_id=session_id,
                        generation=generation,
                        reason=reason,
                        duration_ms=(gate_acquired - request_started) * 1_000,
                    )
                    return
                if gate.attempt is not None:
                    if enqueue:
                        self._trace_response_gate(
                            "response.arbiter.coalesced",
                            session_id=session_id,
                            generation=generation,
                            reason=reason,
                            request_id=gate.attempt.request_id,
                            duration_ms=(gate_acquired - request_started) * 1_000,
                        )
                    return
                selected_reason = min(
                    gate.pending,
                    key=lambda item: _RESPONSE_PRIORITY.get(item, 99),
                )
                activation = self._wake_activations.get(key)
                if (
                    selected_reason == "task_update"
                    and activation is not None
                    and activation.verification_state != "accepted"
                ):
                    self._trace_response_gate(
                        "response.arbiter.deferred",
                        session_id=session_id,
                        generation=generation,
                        reason=selected_reason,
                        detail={"blocked_by": "wake_verification"},
                    )
                    return
                snapshot_started = time.monotonic()
                candidate_sync_started = snapshot_started
                await task_presentation_service.sync_candidates()
                candidate_sync_finished = time.monotonic()
                async with session_scope() as session:
                    realtime = await self._require_current_generation(session, session_id, generation)
                    floor = await self._floor_state(session, session_id)
                    adapter = get_voice_session_adapter(realtime.provider_adapter)
                    quiet_terminal_delivery = selected_reason == "task_update" and realtime.provider_adapter == "openai_live"
                    if not adapter.is_connected(session_id=session_id, generation=generation):
                        self._trace_response_gate(
                            "response.arbiter.deferred",
                            session_id=session_id,
                            generation=generation,
                            reason=selected_reason,
                            detail={"blocked_by": "provider_not_connected", "floor": floor},
                        )
                        return
                    if floor in {"connecting", "text_fallback"} or (floor == "assistant_speaking" and not quiet_terminal_delivery) or (
                        floor == "user_speaking"
                        and not quiet_terminal_delivery
                        and (
                            selected_reason != "user_turn"
                            or (activation is not None and activation.legacy_compat)
                        )
                    ):
                        self._trace_response_gate(
                            "response.arbiter.deferred",
                            session_id=session_id,
                            generation=generation,
                            reason=selected_reason,
                            detail={"blocked_by": "floor", "floor": floor},
                        )
                        return
                    if selected_reason == "task_update" and floor != "listening" and not quiet_terminal_delivery:
                        self._trace_response_gate(
                            "response.arbiter.deferred",
                            session_id=session_id,
                            generation=generation,
                            reason=selected_reason,
                            detail={"blocked_by": "floor", "floor": floor},
                        )
                        return

                    situation_started = time.monotonic()
                    projected = await jarvis_situation_projector.project_in_session(
                        session,
                        conversation_id=realtime.conversation_id,
                        session_id=session_id,
                    )
                    situation_finished = time.monotonic()
                    context = projected.payload
                    if selected_reason == "task_update":
                        attention_update = (
                            await task_presentation_service.build_attention_context_in_session(
                                session,
                                session_id=session_id,
                                generation=generation,
                                limit=20,
                            )
                        )
                        current_presentations = list((await session.execute(
                            select(TaskPresentation).where(TaskPresentation.state == "pending")
                        )).scalars().all())
                        attention_item_fingerprints = {
                            item.id: task_presentation_service.semantic_item_fingerprint(item)
                            for item in current_presentations
                        }
                        prior_ids = set(self._attention_context_items.get(key, {}))
                        removed_ids = prior_ids - set(attention_item_fingerprints)
                        tombstones: list[dict[str, str]] = []
                        if removed_ids:
                            removed = list((await session.execute(
                                select(TaskPresentation).where(TaskPresentation.id.in_(removed_ids))
                            )).scalars().all())
                            tombstones = [
                                {
                                    "presentation_id": item.id,
                                    "state": item.state,
                                    "handled_event_id": item.handled_event_id or "",
                                }
                                for item in removed
                            ]
                        context["attention_context"] = {
                            "items": list(attention_update.items),
                            "total_pending": attention_update.total_pending,
                            "next_cursor": attention_update.next_cursor,
                            "deltas": tombstones,
                        }
                        if settings.jarvis_conversation_observer_mode == "enabled":
                            from app.services import assistant_notes
                            context["assistant_notes"] = await assistant_notes.render(session, session_id=session_id)
                    reservation_started = situation_finished
                    if selected_reason == "task_update":
                        # Live's voice activity treats a mid-sentence pause as the end of the
                        # user's turn. Like a person, wait until they have been quiet briefly.
                        last_user_words = await session.scalar(select(LiveSessionEvent.created_at).where(
                            LiveSessionEvent.session_id == session_id,
                            LiveSessionEvent.event_type == "provider.user_transcript_delta")
                            .order_by(LiveSessionEvent.sequence.desc()).limit(1))
                        if last_user_words is not None and last_user_words.tzinfo is None:
                            last_user_words = last_user_words.replace(tzinfo=timezone.utc)
                        # created_at has one-second resolution: count from its upper bound.
                        user_recently_spoke = floor == "listening" and last_user_words is not None and (
                            utc_now() - (last_user_words + timedelta(seconds=1))) < timedelta(seconds=1.5)
                        reservation = await task_presentation_service.reserve_in_session(
                            session,
                            session_id=session_id,
                            generation=generation,
                            user_owns_floor=floor == "user_speaking" or user_recently_spoke,
                            limit=3,
                            commentary=True,
                            terminal_only=quiet_terminal_delivery and floor != "listening",
                            quiet_context=quiet_terminal_delivery and floor != "listening",
                            exclude_presentation_ids=frozenset(
                                self._attention_appended_ids.get(key, set())
                            ),
                        )
                    reservation_finished = time.monotonic()
                    if reservation is not None:
                        context["pending_update_count"] = reservation.total_pending
                    gate.pending.discard(selected_reason)
                    request_id = str(uuid4())
                    now_monotonic = time.monotonic()
                    gate.attempt = _ResponseAttempt(
                        request_id=request_id,
                        reason=selected_reason,
                        presentation_ids=(
                            () if reservation is None else reservation.presentation_ids
                        ),
                        state="submitting",
                        started_monotonic=now_monotonic,
                        last_activity_monotonic=now_monotonic,
                        activation_id=activation_id,
                        input_item_id=input_item_id,
                    )
                    if not enqueue:
                        self._trace_response_gate(
                            "response.arbiter.resumed",
                            session_id=session_id,
                            generation=generation,
                            reason=selected_reason,
                            request_id=request_id,
                            duration_ms=(time.monotonic() - request_started) * 1_000,
                        )
                snapshot_finished = time.monotonic()
        except Exception as exc:
            self._trace_response_gate(
                "response.arbiter.failed",
                session_id=session_id,
                generation=generation,
                reason=selected_reason,
                duration_ms=(time.monotonic() - request_started) * 1_000,
                detail={"stage": "snapshot", "failure_type": type(exc).__name__},
            )
            raise

        presentation_ids = () if reservation is None else reservation.presentation_ids
        facts = () if reservation is None else reservation.facts
        try:
            assert adapter is not None and context is not None
            provider_submit_started = time.monotonic()
            await adapter.create_response(
                session_id=session_id,
                generation=generation,
                request_id=request_id,
                activation_id=activation_id,
                input_item_id=input_item_id,
                response_kind=selected_reason,
                presentation_ids=presentation_ids,
                facts=facts,
                context=context,
            )
            provider_submit_finished = time.monotonic()
            timing_ms = {
                "gate_wait": round((gate_acquired - request_started) * 1_000, 3),
                "candidate_sync": round(
                    (candidate_sync_finished - candidate_sync_started) * 1_000,
                    3,
                ),
                "state_checks": round((situation_started - candidate_sync_finished) * 1_000, 3),
                "situation_projection": round(
                    (situation_finished - situation_started) * 1_000,
                    3,
                ),
                "presentation_reservation": round(
                    (reservation_finished - reservation_started) * 1_000,
                    3,
                ),
                "situation_snapshot": round(
                    (snapshot_finished - snapshot_started) * 1_000,
                    3,
                ),
                "provider_submit": round(
                    (provider_submit_finished - provider_submit_started) * 1_000,
                    3,
                ),
                "total_to_provider_submit": round(
                    (provider_submit_finished - request_started) * 1_000,
                    3,
                ),
            }
            async with session_scope() as session:
                await self._require_current_generation(session, session_id, generation)
                await JarvisDomainRepository(session).append_session_event(
                    session_id=session_id,
                    event_type="response.arbiter.started",
                    payload_json={
                        "reason": selected_reason,
                        "request_id": request_id,
                        "session_id": session_id,
                        "generation": generation,
                        "presentation_ids": list(presentation_ids),
                        "timing_ms": timing_ms,
                    },
                    idempotency_key=f"response-arbiter:{session_id}:{generation}:{uuid4()}",
                )
            if selected_reason == "task_update" and attention_item_fingerprints is not None:
                self._attention_context_items[key] = attention_item_fingerprints
            self._trace_response_gate(
                "response.arbiter.started",
                session_id=session_id,
                generation=generation,
                reason=selected_reason,
                request_id=request_id,
                duration_ms=(provider_submit_finished - request_started) * 1_000,
                detail={"timing_ms": timing_ms},
            )
        except Exception as exc:
            async with gate.lock:
                if gate.attempt is not None and gate.attempt.request_id == request_id:
                    gate.attempt = None
                    gate.pending.add(selected_reason)
            if reservation is not None:
                await task_presentation_service.requeue(
                    presentation_ids=reservation.presentation_ids,
                    session_id=session_id,
                    generation=generation,
                    error="controlled_response_create_failed",
                )
            self._trace_response_gate(
                "response.arbiter.failed",
                session_id=session_id,
                generation=generation,
                reason=selected_reason,
                request_id=request_id,
                duration_ms=(time.monotonic() - request_started) * 1_000,
                detail={"stage": "provider_submit", "failure_type": type(exc).__name__},
            )
            raise

        await self._ensure_response_watchdog(session_id, generation, request_id)

    async def _finish_controlled_response(
        self,
        session_id: str,
        generation: int,
        *,
        request_id: str | None = None,
        response_id: str | None = None,
    ) -> None:
        gate = self._response_gates.get((session_id, generation))
        if gate is None:
            return
        watchdog: asyncio.Task[None] | None = None
        async with gate.lock:
            attempt = gate.attempt
            if attempt is None:
                return
            if request_id and attempt.request_id != request_id:
                return
            if (
                response_id
                and attempt.provider_response_id
                and attempt.provider_response_id != response_id
            ):
                return
            watchdog = attempt.watchdog_task
            gate.attempt = None
            has_pending = bool(gate.pending)
        if watchdog is not None and watchdog is not asyncio.current_task():
            watchdog.cancel()
        if has_pending:
            pending_reason = min(
                gate.pending,
                key=lambda item: _RESPONSE_PRIORITY.get(item, 99),
            )
            await self._queue_controlled_response(
                session_id,
                generation,
                pending_reason,
                enqueue=False,
            )

    async def _observe_controlled_response(
        self,
        session_id: str,
        generation: int,
        event: VoiceProviderEvent,
    ) -> None:
        gate = self._response_gates.get((session_id, generation))
        if gate is None:
            return
        request_id = str(event.payload.get("request_id") or "")
        response_id = str(event.payload.get("response_id") or "")
        async with gate.lock:
            attempt = gate.attempt
            if attempt is None or (request_id and request_id != attempt.request_id):
                return
            if response_id:
                if attempt.provider_response_id and attempt.provider_response_id != response_id:
                    return
                attempt.provider_response_id = response_id
            attempt.last_activity_monotonic = time.monotonic()
            if event.kind == "assistant_generating":
                attempt.state = "generating"
            elif event.kind in {"assistant_speaking", "assistant_transcript_delta"}:
                attempt.state = "speaking"

    async def _ensure_response_watchdog(
        self,
        session_id: str,
        generation: int,
        request_id: str,
    ) -> None:
        gate = self._response_gates.get((session_id, generation))
        if gate is None:
            return
        async with gate.lock:
            attempt = gate.attempt
            if attempt is None or attempt.request_id != request_id or attempt.watchdog_task is not None:
                return
            attempt.watchdog_task = asyncio.create_task(
                self._watch_response(session_id, generation, request_id),
                name=f"jarvis-response-watchdog:{session_id}:{generation}:{request_id}",
            )

    async def _watch_response(self, session_id: str, generation: int, request_id: str) -> None:
        while True:
            await asyncio.sleep(0.5)
            gate = self._response_gates.get((session_id, generation))
            if gate is None:
                return
            should_cancel = False
            should_close = False
            response_id: str | None = None
            async with gate.lock:
                attempt = gate.attempt
                if attempt is None or attempt.request_id != request_id:
                    return
                now = time.monotonic()
                response_id = attempt.provider_response_id
                if attempt.cancel_requested_monotonic is not None:
                    should_close = now - attempt.cancel_requested_monotonic >= 2.0
                else:
                    timeout = 5.0 if attempt.state == "submitting" else 15.0
                    if now - attempt.last_activity_monotonic >= timeout:
                        attempt.state = "cancelling"
                        attempt.cancel_requested_monotonic = now
                        should_cancel = True
            session = await self.get_session(session_id)
            if session is None:
                return
            adapter = get_voice_session_adapter(session.provider_adapter)
            if should_cancel:
                try:
                    await adapter.cancel_response(
                        session_id=session_id,
                        generation=generation,
                        response_id=response_id,
                    )
                except Exception:
                    should_close = True
            if should_close:
                await adapter.close(
                    session_id=session_id,
                    generation=generation,
                    reason="response_watchdog_timeout",
                    emit_event=True,
                )
                return

    async def start_fresh_log(self, *, idempotency_key: str):
        current = await self.get_active_session()
        new_log = await jarvis_daily_service.start_fresh_log(idempotency_key=idempotency_key)
        if current is not None:
            await self.supersede_session(
                current.id,
                generation=current.generation,
                reason="explicit_fresh_start",
            )
        return new_log

    async def open_controller(
        self,
        *,
        conversation_id: str,
        controller_id: str,
        adapter_name: str = "fake",
    ) -> SessionRecord:
        bootstrap = await self.bootstrap_controller(
            conversation_id=conversation_id,
            controller_id=controller_id,
            adapter_name=adapter_name,
        )
        return bootstrap.session

    async def bootstrap_controller(
        self,
        *,
        conversation_id: str,
        controller_id: str,
        adapter_name: str = "fake",
        sdp_offer: str | None = None,
        client_context: object | None = None,
        activation_context: object | None = None,
    ) -> ControllerBootstrap:
        adapter = get_voice_session_adapter(adapter_name)
        wake_activation = self._wake_activation(activation_context)
        activation_id = (
            activation_context.get("activation_id") if isinstance(activation_context, dict)
            else getattr(activation_context, "activation_id", None)
        )
        activation_source = (
            activation_context.get("source") if isinstance(activation_context, dict)
            else getattr(activation_context, "source", None)
        )
        if wake_activation is not None and adapter_name == "openai_live":
            # The local wake detector already admitted the activation and Live
            # receives it on the one primary microphone path. Realtime's
            # The local wake detector already admitted the activation.
            wake_activation.verification_state = "accepted"
        if wake_activation is not None:
            self._prune_activation_index()
            indexed = self._activation_index.get(wake_activation.activation_id)
            if indexed is not None and indexed[2] > time.monotonic():
                raise ActivationConflictError(
                    "Wake activation id is already active on another session"
                )
        superseded_adapters: list[tuple[str, int, str]] = []
        async with session_scope() as session:
            conversation = await session.get(Conversation, conversation_id)
            jarvis_state = await session.get(JarvisState, "current")
            if (
                conversation is None
                or conversation.conversation_kind != "jarvis_daily"
                or conversation.lifecycle_status != "open"
                or jarvis_state is None
                or jarvis_state.current_log_id != conversation_id
            ):
                raise ValueError("A controller requires the current open Jarvis log")

            active = list((await session.execute(
                select(LiveSession)
                .where(LiveSession.lifecycle_state.in_(sorted(ACTIVE_SESSION_STATES)))
                .order_by(LiveSession.generation.asc())
            )).scalars().all())
            repo = JarvisDomainRepository(session)
            for old in active:
                superseded_adapters.append((old.id, old.generation, old.provider_adapter))
                old.lifecycle_state = "superseded"
                old.disconnected_at = utc_now()
                await repo.append_session_event(
                    session_id=old.id,
                    event_type="session.superseded",
                    payload_json={"superseded_by_controller": controller_id},
                    idempotency_key=f"superseded:{old.id}:{controller_id}",
                )

            latest_generation = int((await session.execute(
                select(func.max(LiveSession.generation))
            )).scalar_one_or_none() or 0)
            preferences = await session.get(JarvisPreference, "current")
            if preferences is None:
                preferences = JarvisPreference(id="current")
                session.add(preferences)
                await session.flush()
            if wake_activation is not None and wake_activation.gate_mode == "immediate" and not preferences.developer_mode:
                raise ValueError("Immediate wake gate is restricted to developer mode")
            if (
                wake_activation is not None
                and (wake_activation.benchmark_run_id or wake_activation.benchmark_fixture)
                and not preferences.developer_mode
            ):
                raise ValueError("Benchmark metadata is restricted to developer mode")
            temporal = normalize_client_context(
                client_context,
                fallback_time_zone=preferences.time_zone,
            )
            preferences.time_zone = temporal.time_zone
            realtime = LiveSession(
                id=str(uuid4()),
                conversation_id=conversation_id,
                generation=latest_generation + 1,
                provider_adapter=adapter.name,
                lifecycle_state="connecting",
                controller_id=controller_id,
                activation_id=str(activation_id) if activation_id else None,
                activation_source=str(activation_source) if activation_source else None,
                workflow_catalog_revision=hashlib.sha256(
                    settings.task_workflow_allowlist.encode("utf-8")
                ).hexdigest(),
                client_time_zone=temporal.time_zone,
                client_utc_offset_minutes=temporal.utc_offset_minutes,
                client_observed_at=temporal.observed_at,
            )
            session.add(realtime)
            await session.flush()
            await repo.append_session_event(
                session_id=realtime.id,
                event_type="session.connecting",
                payload_json={
                    "controller_id": controller_id,
                    "adapter": adapter.name,
                    "activation_source": "wake_word" if wake_activation is not None else "manual",
                },
                idempotency_key=f"connecting:{realtime.id}",
            )
            if wake_activation is not None:
                await repo.append_session_event(
                    session_id=realtime.id,
                    event_type="wake.gate.started",
                    payload_json={
                        "activation_id": wake_activation.activation_id,
                        "wake_score": wake_activation.wake_score,
                        "gate_mode": wake_activation.gate_mode,
                        "session_id": realtime.id,
                        "generation": realtime.generation,
                        "benchmark_run_id": wake_activation.benchmark_run_id,
                        "benchmark_fixture": wake_activation.benchmark_fixture,
                    },
                    idempotency_key=f"wake-gate-started:{realtime.id}:{realtime.generation}",
                )
                if wake_activation.verification_state == "accepted":
                    await repo.append_session_event(
                        session_id=realtime.id,
                        event_type="wake.gate.accepted",
                        payload_json={
                            "activation_id": wake_activation.activation_id,
                            "wake_score": wake_activation.wake_score,
                            "gate_mode": wake_activation.gate_mode,
                            "reason": (
                                "live_primary_audio_path"
                                if adapter_name == "openai_live"
                                else "developer_immediate"
                            ),
                            "session_id": realtime.id,
                            "generation": realtime.generation,
                            "benchmark_run_id": wake_activation.benchmark_run_id,
                            "benchmark_fixture": wake_activation.benchmark_fixture,
                        },
                        idempotency_key=f"wake-gate-accepted:{realtime.id}:{realtime.generation}",
                    )
            record = self._record(realtime, floor_state="connecting")
            context = await self._context_in_session(
                session,
                conversation_id=conversation_id,
                session_id=realtime.id,
            )
            attention_update = await task_presentation_service.build_attention_context_in_session(
                session,
                session_id=realtime.id,
                generation=realtime.generation,
                limit=20,
            )
            initial_presentations = list((await session.execute(
                select(TaskPresentation).where(TaskPresentation.state == "pending")
            )).scalars().all())
            attention_key = (realtime.id, realtime.generation)
            self._attention_context_fingerprints[attention_key] = (
                task_presentation_service.semantic_fingerprint(initial_presentations)
            )
            self._attention_context_items[attention_key] = {
                item.id: task_presentation_service.semantic_item_fingerprint(item)
                for item in initial_presentations
            }
            context["attention_context"] = {
                "items": list(attention_update.items),
                "total_pending": attention_update.total_pending,
                "next_cursor": attention_update.next_cursor,
                "deltas": [],
            }
            if settings.jarvis_conversation_observer_mode == "enabled":
                from app.services import assistant_notes
                context["assistant_notes"] = await assistant_notes.render(session, session_id=realtime.id)
            if wake_activation is not None and wake_activation.activation_id:
                context = {**context, "activation_id": wake_activation.activation_id,
                    "activation_source": "wake_word"}

        if wake_activation is not None:
            self._activation_index[wake_activation.activation_id] = (
                record.id,
                record.generation,
                float("inf"),
            )
            self._prune_activation_index()
            self._wake_activations[(record.id, record.generation)] = wake_activation
            self._wake_activation_seen.add((record.id, record.generation))

        for old_session_id, old_generation, old_adapter in superseded_adapters:
            self._tombstone_activation_ids(old_session_id, old_generation)
            self._clear_wake_activation(old_session_id, old_generation)
            await task_presentation_service.release_session_reservations(
                session_id=old_session_id,
                generation=old_generation,
                error="session_superseded",
            )
            if old_adapter == "fake":
                fake_voice_session_adapter.detach(
                    session_id=old_session_id,
                    generation=old_generation,
                )
            else:
                self._schedule_provider_cleanup(
                    session_id=old_session_id,
                    generation=old_generation,
                    adapter_name=old_adapter,
                )

        try:
            connection = await adapter.connect(
                session_id=record.id,
                generation=record.generation,
                context=context,
                on_event=self.handle_provider_event,
                sdp_offer=sdp_offer,
            )
        except Exception as exc:
            self._tombstone_activation_ids(record.id, record.generation)
            self._clear_wake_activation(record.id, record.generation)
            self._clear_provider_session_mappings(record.id, record.generation)
            failure_type = type(exc).__name__
            failure_detail = _safe_connection_failure(exc)
            logger.exception(
                "Live provider connection failed for session %s generation %s: %s",
                record.id,
                record.generation,
                failure_detail,
            )
            async with session_scope() as session:
                failed = await session.get(LiveSession, record.id)
                if failed is not None:
                    failed.lifecycle_state = "disconnected"
                    failed.disconnected_at = utc_now()
                    await JarvisDomainRepository(session).append_session_event(
                        session_id=record.id,
                        event_type="session.connection_failed",
                        payload_json={
                            "adapter": adapter.name,
                            "failure_type": failure_type,
                            "detail": failure_detail,
                        },
                        idempotency_key=f"connection-failed:{record.id}:{record.generation}",
                    )
            raise
        refreshed = await self.get_session(record.id)
        if refreshed is None:
            raise RuntimeError("Live session disappeared after connection")
        return ControllerBootstrap(
            session=refreshed,
            sdp_answer=connection.sdp_answer,
            provider_capabilities=adapter.capabilities.as_dict(),
        )

    def _schedule_provider_cleanup(
        self,
        *,
        session_id: str,
        generation: int,
        adapter_name: str,
    ) -> None:
        async def close_superseded_provider() -> None:
            try:
                await get_voice_session_adapter(adapter_name).close(
                    session_id=session_id,
                    generation=generation,
                    reason="superseded",
                    emit_event=False,
                )
            except Exception:
                # Durable authority already belongs to the new generation.
                # Provider cleanup cannot affect the replacement connection.
                pass

        task = asyncio.create_task(
            close_superseded_provider(),
            name=f"jarvis-provider-cleanup:{session_id}:{generation}",
        )
        self._provider_cleanup_tasks.add(task)
        task.add_done_callback(self._provider_cleanup_tasks.discard)

    async def observe(self) -> SessionRecord | None:
        """Return the active controller projection without taking ownership."""
        return await self.get_active_session()

    async def get_active_session(self) -> SessionRecord | None:
        async with session_scope() as session:
            realtime = (await session.execute(
                select(LiveSession)
                .where(LiveSession.lifecycle_state.in_(sorted(ACTIVE_SESSION_STATES)))
                .order_by(LiveSession.generation.desc())
                .limit(1)
            )).scalar_one_or_none()
            if realtime is None:
                return None
            return self._record(realtime, floor_state=await self._floor_state(session, realtime.id))

    async def get_session(self, session_id: str) -> SessionRecord | None:
        async with session_scope() as session:
            realtime = await session.get(LiveSession, session_id)
            if realtime is None:
                return None
            return self._record(realtime, floor_state=await self._floor_state(session, realtime.id))

    async def get_latest_session(self, *, conversation_id: str) -> SessionRecord | None:
        async with session_scope() as session:
            realtime = (await session.execute(
                select(LiveSession)
                .where(LiveSession.conversation_id == conversation_id)
                .order_by(LiveSession.generation.desc())
                .limit(1)
            )).scalar_one_or_none()
            if realtime is None:
                return None
            return self._record(realtime, floor_state=await self._floor_state(session, realtime.id))

    async def handle_provider_event(
        self,
        session_id: str,
        generation: int,
        event: VoiceProviderEvent,
    ) -> None:
        if event.kind.startswith("assistant_"):
            await self._observe_controlled_response(session_id, generation, event)
        if event.kind == "user_speaking":
            self._schedule_barge_in_cancel(
                session_id,
                generation,
                speech_event_id=event.event_id,
                speech_item_id=str(event.payload.get("item_id") or "") or None,
            )
        shell_call: tuple[
            str, dict[str, Any], str, str | None, str | None, dict[str, Any], str
        ] | None = None
        presentation_action: tuple[tuple[str, ...], DeliveryRecord, str, str] | None = None
        append_acknowledged_presentations: tuple[str, ...] = ()
        requeue_presentations: tuple[str, ...] = ()
        release_reservations = False
        response_finished = False
        queue_user_response = False
        normal_response_item: str | None = None
        retry_user_response = False
        wake_response: tuple[_WakeActivation, str] | None = None
        queue_task_updates = False
        retry_task_updates = False
        schedule_wake_fallback = False
        cancel_wake_fallback = False
        clear_wake_activation = False
        discard_activation: _WakeActivation | None = None
        replay_tool_result: tuple[bool, dict[str, Any]] | None = None
        atomic_tool_result: tuple[str, bool, dict[str, Any], str] | None = None
        async with session_scope() as session:
            realtime = await self._require_current_generation(session, session_id, generation)
            repo = JarvisDomainRepository(session)
            event_type = f"provider.{event.kind}"
            payload = dict(event.payload)
            activation_for_payload = self._wake_activations.get((session_id, generation))
            if (
                event.kind in {"user_transcript", "user_transcript_delta"}
                and activation_for_payload is not None
                and activation_for_payload.verification_state != "accepted"
            ):
                # Candidate speech stays in memory until the gate accepts it;
                # rejected candidates must not land in durable session events.
                payload.pop("transcript", None)
                payload.pop("delta", None)
                payload["transcript_redacted"] = True
            if activation_for_payload is not None:
                payload.update({
                    "activation_id": activation_for_payload.activation_id,
                    "benchmark_run_id": activation_for_payload.benchmark_run_id,
                    "benchmark_fixture": activation_for_payload.benchmark_fixture,
                })
            # Every live session event is self-identifying so clients can reject
            # stale events without consulting historical session state.
            payload.update({"session_id": session_id, "generation": generation})
            if await self._is_late_delivery_state(session, realtime, event):
                event_type = "provider.event.ignored"
                payload = {**payload, "kind": event.kind, "reason": "delivery_already_terminal"}
            appended = await repo.append_session_event_once(
                session_id=session_id,
                event_type=event_type,
                payload_json=payload,
                idempotency_key=f"provider:{event.event_id}",
            )
            if not appended.created and event.kind == "shell_call":
                call_id = str(event.payload.get("call_id") or event.event_id)
                existing_result = (await session.execute(
                    select(LiveSessionEvent)
                    .where(LiveSessionEvent.session_id == session_id)
                    .where(LiveSessionEvent.event_type == "brain.tool.result")
                    .order_by(LiveSessionEvent.sequence.desc())
                )).scalars().all()
                for result_event in existing_result:
                    result_payload = result_event.payload_json or {}
                    if str(result_payload.get("call_id") or "") != call_id:
                        continue
                    replay_tool_result = (
                        bool(result_payload.get("ok")),
                        dict(result_payload.get("output") or {}),
                    )
                    break
            # A duplicate non-tool callback is already durably represented and
            # needs no further work.  A duplicate shell callback without a
            # stored result is retried so an interrupted handling transaction
            # can recover instead of becoming permanently lost.
            if not appended.created and replay_tool_result is None and event.kind != "shell_call":
                return

            if event.kind == "ready":
                realtime.lifecycle_state = "ready"
                realtime.ready_at = realtime.ready_at or utc_now()
                activation = self._wake_activations.get((session_id, generation))
                queue_task_updates = activation is None or activation.verification_state == "accepted"
            elif event.kind in {"disconnected", "provider_error"}:
                realtime.lifecycle_state = "disconnected"
                realtime.disconnected_at = utc_now()
                release_reservations = True
                response_finished = True
                clear_wake_activation = True

            if event.kind == "user_speaking":
                activation = self._wake_activations.get((session_id, generation))
                if activation is not None and activation.legacy_compat:
                    # Keep the pre-correlation alias runnable while old
                    # clients roll forward.  Phase-2 normalized commits do
                    # not use this speech-stop compatibility gate.
                    activation.speech_stopped = False
                if (
                    activation is not None
                    and activation.verification_state == "accepted"
                    and activation.response_requested
                    and (
                        (
                            activation.legacy_compat
                            and not str(event.payload.get("item_id") or "")
                        )
                        or (
                            str(event.payload.get("item_id") or "")
                            and str(event.payload.get("item_id")) != activation.primary_input_item_id
                        )
                    )
                ):
                    clear_wake_activation = True

            if event.kind == "user_transcript":
                activation = self._wake_activations.get((session_id, generation))
                item_id = str(event.payload.get("item_id") or event.event_id)
                if activation is None:
                    await self._persist_user_transcript(session, realtime, event)
                elif activation.verification_state == "accepted":
                    activation.candidate_item_ids.add(item_id)
                    await self._persist_user_transcript(session, realtime, event)
                elif activation.verification_state == "pending":
                    activation.candidate_item_ids.add(item_id)
                    decision = verify_wake_transcript(
                        str(event.payload.get("transcript") or ""),
                        activation.wake_score,
                    )
                    activation.verification_state = "accepted" if decision.accepted else "rejected"
                    if activation.verification_state == "rejected":
                        activation.lifecycle_state = "terminal"
                    cancel_wake_fallback = True
                    decision_monotonic = time.monotonic()
                    await repo.append_session_event(
                        session_id=session_id,
                        event_type=f"wake.gate.{activation.verification_state}",
                        payload_json={
                            "reason": decision.reason,
                            "wake_score": activation.wake_score,
                            "gate_mode": activation.gate_mode,
                            "gate_duration_ms": round(
                                (decision_monotonic - activation.gate_started_monotonic) * 1_000,
                                3,
                            ),
                            "registration_to_gate_decision_ms": round(
                                (decision_monotonic - activation.registered_monotonic) * 1_000,
                                3,
                            ),
                            "commit_to_gate_decision_ms": (
                                round(
                                    (decision_monotonic - activation.gate_timeout_started_monotonic) * 1_000,
                                    3,
                                )
                                if activation.gate_timeout_started_monotonic is not None
                                else None
                            ),
                            "activation_id": activation.activation_id,
                            "wake_term_present": decision.wake_term_present,
                            "prefix_classification": decision.prefix_classification,
                            "transcript_word_count": decision.transcript_word_count,
                            "session_id": session_id,
                            "generation": generation,
                        },
                        idempotency_key=f"wake-gate:{session_id}:{generation}:{event.event_id}",
                    )
                    if activation.verification_state == "accepted":
                        await self._persist_user_transcript(session, realtime, event)
                    else:
                        discard_activation = activation
                elif activation.verification_state in {"rejected", "timed_out"}:
                    activation.candidate_item_ids.add(item_id)
                    discard_activation = activation
                if activation is not None:
                    if activation.verification_state == "pending":
                        pass
                    elif (
                        activation.verification_state == "accepted"
                        and activation.primary_input_item_id == item_id
                        and not activation.response_requested
                    ):
                        wake_response = (activation, item_id)
                    elif (
                        activation.legacy_compat
                        and activation.verification_state == "accepted"
                        and activation.speech_stopped
                    ):
                        if activation.response_requested:
                            retry_user_response = True
                        else:
                            activation.response_requested = True
                            queue_user_response = True
            elif event.kind == "assistant_transcript":
                await self._persist_assistant_transcript(session, realtime, event)
                # Native Live has no assistant_response boundary. Reconsider
                # queued context when this segment releases the observed floor;
                # the transcript still grants no heard/delivered authority.
                retry_task_updates = True
            elif event.kind == "delegation_origin_resolved":
                correlation_id = str(payload.get("origin_correlation_id") or "")
                origin_item_id = str(payload.get("origin_item_id") or "")
                turn_id = self._provider_input_turns.get(
                    (session_id, generation, origin_item_id)
                )
                if correlation_id and turn_id:
                    correlation_key = (session_id, generation, correlation_id)
                    self._provider_correlation_turns[correlation_key] = turn_id
                    task_ids = self._provider_correlation_tasks.get(correlation_key, set())
                    if task_ids:
                        tasks = list((await session.execute(
                            select(Task).where(Task.id.in_(task_ids))
                        )).scalars().all())
                        for task in tasks:
                            if task.origin_turn_id is None:
                                task.origin_turn_id = turn_id
            elif event.kind == "user_input_committed":
                activation = self._wake_activations.get((session_id, generation))
                if activation is None:
                    item_id = str(event.payload.get("item_id") or "")
                    if (
                        item_id
                        and self._mark_input_item_handled(
                        session_id,
                        generation,
                        item_id,
                        )
                    ):
                        normal_response_item = item_id
                else:
                    item_id = str(event.payload.get("item_id") or "")
                    if item_id:
                        activation.candidate_item_ids.add(item_id)
                    if item_id and activation.primary_input_item_id is None:
                        activation.primary_input_item_id = item_id
                    activation.committed_monotonic = activation.committed_monotonic or time.monotonic()
                    activation.gate_timeout_started_monotonic = (
                        activation.gate_timeout_started_monotonic
                        or activation.committed_monotonic
                    )
                    if activation.verification_state == "pending" and activation.gate_mode == "immediate":
                        activation.verification_state = "accepted"
                        cancel_wake_fallback = True
                        decision_monotonic = time.monotonic()
                        await repo.append_session_event(
                            session_id=session_id,
                            event_type="wake.gate.accepted",
                            payload_json={
                                "reason": "developer_immediate",
                                "wake_score": activation.wake_score,
                                "gate_mode": activation.gate_mode,
                                "gate_duration_ms": round(
                                    (decision_monotonic - activation.gate_started_monotonic) * 1_000,
                                    3,
                                ),
                                "registration_to_gate_decision_ms": round(
                                    (decision_monotonic - activation.registered_monotonic) * 1_000,
                                    3,
                                ),
                                "commit_to_gate_decision_ms": round(
                                    (decision_monotonic - activation.gate_timeout_started_monotonic) * 1_000,
                                    3,
                                ),
                                "activation_id": activation.activation_id,
                                "wake_term_present": False,
                                "prefix_classification": "developer_immediate",
                                "transcript_word_count": 0,
                                "session_id": session_id,
                                "generation": generation,
                            },
                            idempotency_key=f"wake-gate-immediate:{session_id}:{generation}:{event.event_id}",
                        )
                    if activation.verification_state == "accepted":
                        if item_id and not activation.response_requested:
                            wake_response = (activation, item_id)
                        elif (
                            item_id
                            and activation.lifecycle_state == "conversation"
                            and item_id != activation.primary_input_item_id
                            and self._mark_input_item_handled(session_id, generation, item_id)
                        ):
                            normal_response_item = item_id
                    elif activation.verification_state == "pending":
                        schedule_wake_fallback = True
                    elif activation.verification_state in {"rejected", "timed_out"}:
                        activation.lifecycle_state = "terminal"
                        discard_activation = activation
            elif event.kind == "user_stopped":
                activation = self._wake_activations.get((session_id, generation))
                if activation is not None:
                    activation.speech_stopped = True
                    if activation.legacy_compat:
                        if activation.verification_state == "pending":
                            schedule_wake_fallback = True
                        elif activation.verification_state == "accepted" and activation.response_requested:
                            retry_user_response = True
                        elif activation.verification_state in {"rejected", "timed_out"}:
                            discard_activation = activation
                else:
                    # A stop may release a task-update reservation that was
                    # queued while the user owned the floor. It never creates
                    # a conversational response by itself.
                    retry_task_updates = True
            elif event.kind == "assistant_update_submitted":
                # Context submission releases only the short transport lease.
                # It never changes durable attention state.
                raw_presentation_ids = payload.get("presentation_ids")
                append_acknowledged_presentations = tuple(
                    str(item)
                    for item in (raw_presentation_ids if isinstance(raw_presentation_ids, list) else [])
                    if item
                )
                response_finished = True
            elif event.kind in {"assistant_response", "assistant_interrupted"}:
                delivery = await self._persist_delivery(session, realtime, event)
                response_finished = True
                raw_presentation_ids = payload.get("presentation_ids")
                presentation_ids = tuple(
                    str(item)
                    for item in (raw_presentation_ids if isinstance(raw_presentation_ids, list) else [])
                    if item
                )
                if presentation_ids:
                    heard_transcript = str(
                        payload.get("heard_transcript")
                        or (payload.get("transcript") if delivery.state == "completed" else "")
                        or ""
                    )
                    presentation_action = (
                        presentation_ids,
                        delivery,
                        heard_transcript,
                        str(payload.get("response_kind") or ""),
                    )
            elif event.kind == "shell_call":
                activation = self._wake_activations.get((session_id, generation))
                if activation is None or activation.verification_state == "accepted":
                    # The deterministic fake adapter models a tool call as a
                    # complete response. GPT-Live keeps the spoken turn open.
                    response_finished = realtime.provider_adapter != "openai_live"
                    tool_name = str(payload.get("name") or "")
                    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
                    call_id = str(payload.get("call_id") or event.event_id)
                    origin_item_id = str(payload.get("origin_item_id") or "") or None
                    origin_correlation_id = (
                        str(payload.get("origin_correlation_id") or "") or None
                    )
                    origin_timeline = (
                        dict(payload.get("origin_timeline"))
                        if isinstance(payload.get("origin_timeline"), dict)
                        else {}
                    )
                    shell_call = (
                        tool_name,
                        dict(arguments),
                        call_id,
                        origin_item_id,
                        origin_correlation_id,
                        origin_timeline,
                        event.event_id,
                    )
                    if tool_name == "acknowledge_task_update":
                        result = await task_presentation_service.acknowledge_task_update_in_session(
                            session,
                            session_id=session_id,
                            generation=generation,
                            presentation_id=str(arguments.get("presentation_id") or ""),
                            exposure_token=str(arguments.get("exposure_token") or ""),
                            provider_event_id=event.event_id,
                            provider_call_id=call_id,
                        )
                        await repo.append_session_event(
                            session_id=session_id,
                            event_type="brain.tool.result",
                            payload_json={
                                "call_id": call_id,
                                "name": tool_name,
                                "ok": True,
                                "output": result,
                                "origin_item_id": origin_item_id,
                                "origin_correlation_id": origin_correlation_id,
                                "origin_timeline": origin_timeline,
                            },
                            idempotency_key=f"brain-result:{session_id}:{generation}:{call_id}",
                        )
                        atomic_tool_result = (
                            call_id,
                            True,
                            result,
                            realtime.provider_adapter,
                        )
                        shell_call = None
                    raw_presentation_ids = payload.get("presentation_ids")
                    requeue_presentations = tuple(
                        str(item)
                        for item in (raw_presentation_ids if isinstance(raw_presentation_ids, list) else [])
                        if item
                    )

        if event.kind == "delegation_received":
            from app.services.task_capture import task_capture_service
            try:
                await task_capture_service.request_fresh(session_id=session_id, generation=generation,
                    request_identity=event.payload["origin_correlation_id"])
            except ValueError:
                # Browser/audio-only or locked sessions have no usable screen channel.
                pass

        if replay_tool_result is not None:
            active = await self.get_session(session_id)
            if active is not None and active.generation == generation:
                adapter = get_voice_session_adapter(active.provider_adapter)
                await adapter.send_tool_result(
                    session_id=session_id,
                    generation=generation,
                    call_id=str(event.payload.get("call_id") or event.event_id),
                    output={"ok": replay_tool_result[0], "result": replay_tool_result[1]},
                )
            return

        if cancel_wake_fallback:
            self._cancel_wake_verification(session_id, generation)
        if schedule_wake_fallback:
            self._schedule_wake_verification(session_id, generation)
        if discard_activation is not None:
            await self._discard_candidate_items(session_id, generation, discard_activation)
        if clear_wake_activation:
            self._tombstone_activation_ids(session_id, generation)
            self._clear_wake_activation(session_id, generation)

        if presentation_action is not None:
            presentation_ids, delivery, heard_transcript, response_kind = presentation_action
            await task_presentation_service.release_transport_leases(
                presentation_ids=presentation_ids,
                session_id=session_id,
                generation=generation,
                turn_id=delivery.turn_id,
                delivery_id=delivery.id,
                heard_transcript=heard_transcript,
                response_kind=response_kind,
            )
            appended = self._attention_appended_ids.setdefault(
                (session_id, generation), set()
            )
            appended.update(presentation_ids)

        if append_acknowledged_presentations:
            await task_presentation_service.release_append_lease(
                presentation_ids=append_acknowledged_presentations,
                session_id=session_id,
                generation=generation,
                turn_id=None,
                delivery_id=None,
            )
            attention_key = (session_id, generation)
            appended = self._attention_appended_ids.setdefault(attention_key, set())
            appended.update(append_acknowledged_presentations)
            pending_attention = await task_presentation_service.list_pending(include_terminal_context=realtime.provider_adapter == "openai_live")
            if any(item.id not in appended for item in pending_attention):
                await self._queue_controlled_response(
                    session_id,
                    generation,
                    "task_update",
                )

        if requeue_presentations:
            await task_presentation_service.requeue(
                presentation_ids=requeue_presentations,
                session_id=session_id,
                generation=generation,
                error="response_requested_tool_before_speaking_updates",
                allow_superseded=True,
            )

        if release_reservations:
            await task_presentation_service.release_session_reservations(
                session_id=session_id,
                generation=generation,
                error=f"provider_{event.kind}",
            )
            self._clear_provider_session_mappings(session_id, generation)

        if response_finished:
            await self._finish_controlled_response(
                session_id,
                generation,
                request_id=str(event.payload.get("request_id") or "") or None,
                response_id=str(event.payload.get("response_id") or "") or None,
            )
            if presentation_action is not None:
                appended = self._attention_appended_ids.setdefault(
                    (session_id, generation), set()
                )
                pending_attention = await task_presentation_service.list_pending(include_terminal_context=realtime.provider_adapter == "openai_live")
                if any(item.id not in appended for item in pending_attention):
                    await self._queue_controlled_response(
                        session_id,
                        generation,
                        "task_update",
                    )

        if wake_response is not None:
            await self._maybe_queue_wake_response(
                session_id,
                generation,
                wake_response[0],
            )

        if normal_response_item is not None:
            await self._queue_controlled_response(
                session_id,
                generation,
                "user_turn",
                input_item_id=normal_response_item,
            )

        if retry_task_updates:
            gate = self._response_gates.get((session_id, generation))
            if gate is not None and "task_update" in gate.pending:
                await self._queue_controlled_response(
                    session_id,
                    generation,
                    "task_update",
                    enqueue=False,
                )

        if queue_user_response:
            await self._queue_controlled_response(session_id, generation, "user_turn")
        elif retry_user_response:
            await self._queue_controlled_response(
                session_id,
                generation,
                "user_turn",
                enqueue=False,
            )

        if queue_task_updates:
            if bool(event.payload.get("defer_initial_updates")):
                self._schedule_background_response(session_id, generation, "task_update")
            else:
                await self._queue_controlled_response(session_id, generation, "task_update")

        if shell_call is not None:
            (
                tool_name,
                arguments,
                call_id,
                origin_item_id,
                origin_correlation_id,
                origin_timeline,
                provider_event_id,
            ) = shell_call
            await self._execute_shell_call(
                session_id=session_id,
                generation=generation,
                tool_name=tool_name,
                arguments=arguments,
                call_id=call_id,
                origin_item_id=origin_item_id,
                origin_correlation_id=origin_correlation_id,
                origin_timeline=origin_timeline,
                provider_event_id=provider_event_id,
            )
        elif atomic_tool_result is not None:
            call_id, ok, output, provider_adapter = atomic_tool_result
            adapter = get_voice_session_adapter(provider_adapter)
            await adapter.send_tool_result(
                session_id=session_id,
                generation=generation,
                call_id=call_id,
                output={"ok": ok, "result": output},
            )
            if adapter.capabilities.supports_manual_response:
                await self._queue_controlled_response(
                    session_id,
                    generation,
                    "tool_continuation",
                )

    def _schedule_background_response(
        self,
        session_id: str,
        generation: int,
        reason: str,
    ) -> None:
        async def queue_response() -> None:
            try:
                await self._queue_controlled_response(session_id, generation, reason)
            except (StaleSessionError, ValueError):
                return
            except Exception:
                logger.exception(
                    "Unable to queue background Jarvis response for session %s generation %s",
                    session_id,
                    generation,
                )

        task = asyncio.create_task(
            queue_response(),
            name=f"jarvis-background-response:{session_id}:{generation}:{reason}",
        )
        self._background_response_tasks.add(task)
        task.add_done_callback(self._background_response_tasks.discard)

    async def _capture_barge_in_intent(
        self,
        *,
        session_id: str,
        generation: int,
        speech_event_id: str,
        speech_item_id: str | None,
        source: str,
        expected_request_id: str | None = None,
        expected_response_id: str | None = None,
    ) -> _BargeInIntent | None:
        gate = self._response_gates.get((session_id, generation))
        if gate is None:
            return None
        key = (session_id, generation, speech_event_id, expected_request_id or "")
        async with gate.lock:
            if key in self._barge_in_keys:
                return None
            attempt = gate.attempt
            if attempt is None or attempt.state in {"terminal", "cancelling"}:
                return None
            if speech_item_id and attempt.input_item_id == speech_item_id:
                return None
            if expected_request_id is not None and attempt.request_id != expected_request_id:
                return None
            if (
                expected_response_id is not None
                and attempt.provider_response_id is not None
                and attempt.provider_response_id != expected_response_id
            ):
                return None
            intent = _BargeInIntent(
                speech_event_id=speech_event_id,
                speech_item_id=speech_item_id,
                request_id=attempt.request_id,
                response_id_at_onset=expected_response_id or attempt.provider_response_id,
                scheduled_monotonic=time.monotonic(),
                source=source,
            )
            self._barge_in_keys.add(key)
            return intent

    async def _cancel_if_same_attempt(
        self,
        *,
        session_id: str,
        generation: int,
        intent: _BargeInIntent,
    ) -> bool:
        gate = self._response_gates.get((session_id, generation))
        if gate is None:
            return False
        response_id: str | None = None
        async with gate.lock:
            attempt = gate.attempt
            if (
                attempt is None
                or attempt.request_id != intent.request_id
                or attempt.state in {"terminal", "cancelling"}
                or (intent.speech_item_id and attempt.input_item_id == intent.speech_item_id)
            ):
                return False
            attempt.state = "cancelling"
            attempt.cancel_requested_monotonic = time.monotonic()
            response_id = attempt.provider_response_id or intent.response_id_at_onset
        session = await self.get_session(session_id)
        if session is None or session.generation != generation:
            return False
        adapter = get_voice_session_adapter(session.provider_adapter)
        await adapter.cancel_response(
            session_id=session_id,
            generation=generation,
            response_id=response_id,
        )
        self._trace_response_gate(
            "response.cancel.sent",
            session_id=session_id,
            generation=generation,
            request_id=intent.request_id,
            response_id=response_id,
            reason=intent.source,
            detail={
                "speech_event_id": intent.speech_event_id,
                "speech_item_id": intent.speech_item_id,
                "response_id_at_onset": intent.response_id_at_onset,
            },
        )
        return True

    async def capture_barge_in_intent(
        self,
        *,
        session_id: str,
        generation: int,
        speech_event_id: str,
        source: str,
        request_id: str,
        response_id: str | None,
        input_item_id: str | None,
    ) -> bool:
        current = await self.get_session(session_id)
        if current is None or current.generation != generation:
            return False
        intent = await self._capture_barge_in_intent(
            session_id=session_id,
            generation=generation,
            speech_event_id=speech_event_id,
            speech_item_id=input_item_id,
            source=source,
            expected_request_id=request_id,
            expected_response_id=response_id,
        )
        if intent is None:
            return False
        # The browser already confirmed speech and muted playback, so the
        # endpoint has no client grace period. The request identity remains
        # immutable through the adapter call.
        return await self._cancel_if_same_attempt(
            session_id=session_id,
            generation=generation,
            intent=intent,
        )

    def _schedule_barge_in_cancel(
        self,
        session_id: str,
        generation: int,
        *,
        speech_event_id: str,
        speech_item_id: str | None,
        source: str = "backend_fallback",
    ) -> None:
        gate = self._response_gates.get((session_id, generation))
        if gate is None or (gate.barge_in_task is not None and not gate.barge_in_task.done()):
            return

        async def cancel_after_client_grace() -> None:
            try:
                intent = await self._capture_barge_in_intent(
                    session_id=session_id,
                    generation=generation,
                    speech_event_id=speech_event_id,
                    speech_item_id=speech_item_id,
                    source=source,
                )
                if intent is None:
                    return
                await asyncio.sleep(0.3)
                await self._cancel_if_same_attempt(
                    session_id=session_id,
                    generation=generation,
                    intent=intent,
                )
            except (StaleSessionError, ValueError):
                return
            finally:
                if gate.barge_in_task is asyncio.current_task():
                    gate.barge_in_task = None

        gate.barge_in_task = asyncio.create_task(
            cancel_after_client_grace(),
            name=f"jarvis-barge-in-fallback:{session_id}:{generation}:{speech_event_id}",
        )

    async def inject_fake_event(
        self,
        *,
        session_id: str,
        generation: int,
        event_id: str,
        kind: str,
        payload: dict[str, Any],
    ) -> None:
        session = await self.get_session(session_id)
        if session is None or session.provider_adapter != "fake":
            raise ValueError("Fake provider session not found")
        await fake_voice_session_adapter.inject(
            session_id=session_id,
            generation=generation,
            event=VoiceProviderEvent(event_id=event_id, kind=kind, payload=payload),
        )

    async def supersede_session(self, session_id: str, *, generation: int, reason: str) -> None:
        async with session_scope() as session:
            realtime = await self._require_current_generation(session, session_id, generation)
            realtime.lifecycle_state = "superseded"
            realtime.disconnected_at = utc_now()
            await JarvisDomainRepository(session).append_session_event(
                session_id=session_id,
                event_type="session.superseded",
                payload_json={"reason": reason},
                idempotency_key=f"session-superseded:{session_id}:{generation}:{reason}",
            )
        await task_presentation_service.release_session_reservations(
            session_id=session_id,
            generation=generation,
            error=f"session_superseded:{reason}",
        )
        await self._drop_response_gate(session_id, generation)
        self._tombstone_activation_ids(session_id, generation)
        self._clear_wake_activation(session_id, generation)
        if realtime.provider_adapter == "fake":
            fake_voice_session_adapter.detach(session_id=session_id, generation=generation)
        else:
            try:
                await get_voice_session_adapter(realtime.provider_adapter).close(
                    session_id=session_id,
                    generation=generation,
                    reason=reason,
                    emit_event=False,
                )
            except Exception:
                pass

    async def close_session(self, session_id: str, *, generation: int, reason: str = "requested") -> None:
        session = await self.get_session(session_id)
        if session is None:
            raise ValueError("Live session not found")
        await self._assert_generation(session_id, generation)
        adapter = get_voice_session_adapter(session.provider_adapter)
        await adapter.close(
            session_id=session_id,
            generation=generation,
            reason=reason,
            emit_event=True,
        )
        await self._drop_response_gate(session_id, generation)
        self._clear_provider_session_mappings(session_id, generation)
        self._tombstone_activation_ids(session_id, generation)
        self._clear_wake_activation(session_id, generation)

    async def _drop_response_gate(self, session_id: str, generation: int) -> None:
        gate = self._response_gates.pop((session_id, generation), None)
        if gate is None:
            return
        tasks = [
            task for task in (
                gate.barge_in_task,
                None if gate.attempt is None else gate.attempt.watchdog_task,
            )
            if task is not None and not task.done() and task is not asyncio.current_task()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _clear_provider_session_mappings(self, session_id: str, generation: int) -> None:
        for mapping in (
            self._provider_input_turns,
            self._provider_correlation_turns,
            self._provider_correlation_tasks,
            self._attention_context_fingerprints,
            self._attention_context_pending,
            self._attention_context_items,
            self._attention_appended_ids,
        ):
            for key in [
                key for key in mapping
                if key[0] == session_id and key[1] == generation
            ]:
                mapping.pop(key, None)

    async def replay_session_events(
        self,
        session_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 250,
    ) -> tuple[int, tuple[LiveSessionEvent, ...]]:
        async with session_scope() as session:
            realtime = await session.get(LiveSession, session_id)
            if realtime is None:
                raise ValueError("Live session not found")
            result = await session.execute(
                select(LiveSessionEvent)
                .where(LiveSessionEvent.session_id == session_id)
                .where(LiveSessionEvent.sequence > after_sequence)
                .order_by(LiveSessionEvent.sequence.asc())
                .limit(limit)
            )
            return realtime.last_event_sequence, tuple(result.scalars().all())

    async def replay_conversation_events(
        self,
        conversation_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 250,
    ) -> tuple[int, tuple[ConversationEvent, ...]]:
        async with session_scope() as session:
            conversation = await session.get(Conversation, conversation_id)
            if conversation is None or conversation.conversation_kind != "jarvis_daily":
                raise ValueError("Jarvis log not found")
            result = await session.execute(
                select(ConversationEvent)
                .where(ConversationEvent.conversation_id == conversation_id)
                .where(ConversationEvent.sequence > after_sequence)
                .order_by(ConversationEvent.sequence.asc())
                .limit(limit)
            )
            return conversation.last_event_sequence, tuple(result.scalars().all())

    async def context(self, session_id: str | None = None) -> dict[str, Any] | None:
        state = await jarvis_daily_service.get_state()
        if state.current_log is None:
            return None
        async with session_scope() as session:
            return await self._context_in_session(
                session,
                conversation_id=state.current_log.id,
                session_id=session_id,
            )

    async def _execute_shell_call(
        self,
        *,
        session_id: str,
        generation: int,
        tool_name: str,
        arguments: dict[str, Any],
        call_id: str,
        origin_item_id: str | None = None,
        origin_correlation_id: str | None = None,
        origin_timeline: dict[str, Any] | None = None,
        provider_event_id: str | None = None,
    ) -> None:
        shell_started_ns = __import__("time").perf_counter_ns()
        correlation_key = (
            (session_id, generation, origin_correlation_id)
            if origin_correlation_id is not None
            else None
        )
        async with session_scope() as session:
            realtime = await self._require_current_generation(session, session_id, generation)
            controller_device = (
                None if not realtime.controller_id else await session.get(JarvisDevice, realtime.controller_id)
            )
            controller_locked = controller_device is not None and controller_device.lock_state == "locked"
            if correlation_key is not None:
                # Live delegation identity exists before transcript delivery.
                # Missing or ambiguous matches intentionally stay unattributed.
                last_turn_id = self._provider_correlation_turns.get(correlation_key)
                if last_turn_id is None and origin_item_id is not None:
                    last_turn_id = self._provider_input_turns.get(
                        (session_id, generation, origin_item_id)
                    )
            elif origin_item_id is not None:
                # Live supplies the transcript segment that caused this
                # delegated response. Never fall back to an older historical
                # user turn when that correlation is present but unresolved.
                last_turn_id = self._provider_input_turns.get(
                    (session_id, generation, origin_item_id)
                )
            else:
                last_turn_id = (await session.execute(
                    select(ConversationMessage.id)
                    .join(
                        ConversationEvent,
                        ConversationMessage.source_event_id == ConversationEvent.id,
                    )
                    .where(ConversationMessage.conversation_id == realtime.conversation_id)
                    .where(ConversationMessage.role == "user")
                    .order_by(ConversationEvent.sequence.desc())
                    .limit(1)
                )).scalar_one_or_none()
            conversation_id = realtime.conversation_id
            active_time_zone = realtime.client_time_zone
        authority_ready_ns = __import__("time").perf_counter_ns()
        try:
            if controller_locked and tool_name in {
                "dispatch_task", "start_workflow", "answer_task_question", "cancel_task",
                "continue_task", "control_task",
                "set_task_update_policy", "set_jarvis_voice",
            }:
                raise ValueError("That action is unavailable while the device is locked")
            freshness = None
            if settings.jarvis_conversation_observer_mode == "enabled" and tool_name in {"dispatch_task", "start_workflow"}:
                from app.services.live_request_context import advanced_context, admission_reserved
                reserved = await admission_reserved(session_id, generation,
                    origin_item_id or last_turn_id or origin_correlation_id, arguments.get("intent_id"))
                if not reserved:
                    freshness = await advanced_context(session_id, generation,
                        (origin_timeline or {}).get("offset_ms"), origin_correlation_id, intent_id=arguments.get("intent_id"))
                # Input batching yields to transport events. Recheck action
                # authority after that wait, including a newly locked device.
                async with session_scope() as session:
                    current_live = await self._require_current_generation(session, session_id, generation)
                    current_device = await session.get(JarvisDevice, current_live.controller_id) if current_live.controller_id else None
                    if current_device is not None and current_device.lock_state == "locked":
                        raise ValueError("That action is unavailable while the device is locked")
            if freshness:
                from types import SimpleNamespace
                result = SimpleNamespace(output=freshness, control=None)
            else:
                result = await restricted_brain_gateway.execute(
                    tool_name=tool_name,
                    arguments=arguments,
                    call_id=call_id,
                    conversation_id=conversation_id,
                    origin_turn_id=last_turn_id,
                    capture_request_identity=origin_correlation_id or origin_item_id or last_turn_id or call_id,
                    active_time_zone=active_time_zone or "Europe/Amsterdam",
                    now=utc_now(),
                    session_id=session_id,
                    generation=generation,
                    provider_event_id=provider_event_id or f"provider-tool:{session_id}:{generation}:{call_id}",
                    provider_call_id=call_id,
                    turn_scope=origin_item_id or last_turn_id or origin_correlation_id,
                )
            output = result.output
            if (settings.jarvis_conversation_observer_mode == "enabled"
                    and tool_name in {"dispatch_task", "start_workflow"}
                    and isinstance((origin_timeline or {}).get("offset_ms"), int)):
                # Managed delegation can begin before the caller's next clause.
                # Supply newly recognized speech to the admission model through
                # its normal receipt. Interpret nothing here and change no task
                # preference: the model must apply any correction via tools.
                offset = origin_timeline["offset_ms"]
                async with session_scope() as session:
                    await self._require_current_generation(session, session_id, generation)
                    rows = (await session.execute(select(LiveSessionEvent).where(
                        LiveSessionEvent.session_id == session_id,
                        LiveSessionEvent.event_type == "provider.user_transcript_delta")
                        .order_by(LiveSessionEvent.sequence.desc()).limit(64))).scalars().all()
                    later = [{"fragment_id":row.id,"text":p["delta"],"start_ms":p["start_ms"],"end_ms":p.get("end_ms")}
                        for row in reversed(rows) if (p := row.payload_json or {}).get("generation") == generation
                        and not p.get("transcript_redacted") and p.get("delta")
                        and isinstance(p.get("start_ms"), (int,float)) and p["start_ms"] > offset]
                if later:
                    output = {**output,"later_user_context":{"fragments":later,"turn_completion":"unknown"}}
            output.setdefault("admission_timing", {}).update({
                "shell_started_ns": shell_started_ns, "authority_ready_ns": authority_ready_ns})
            durable_output = output
            control = result.control
            ok = True
        except Exception as exc:
            durable_output = {"error": str(exc)}
            output = public_tool_error(str(exc))
            control = None
            ok = False

        async with session_scope() as session:
            await self._require_current_generation(session, session_id, generation)
            internal_task_id = str(durable_output.get("internal_task_id") or "")
            if correlation_key is not None and internal_task_id:
                self._provider_correlation_tasks.setdefault(correlation_key, set()).add(
                    internal_task_id
                )
                resolved_turn_id = self._provider_correlation_turns.get(correlation_key)
                task = (await session.execute(select(Task).where(Task.id == internal_task_id))).scalar_one_or_none()
                if task is not None and task.origin_turn_id is None and resolved_turn_id:
                    task.origin_turn_id = resolved_turn_id
            await JarvisDomainRepository(session).append_session_event(
                session_id=session_id,
                event_type="brain.tool.result",
                payload_json={
                    "call_id": call_id,
                    "name": tool_name,
                    "ok": ok,
                    "output": durable_output,
                    "origin_item_id": origin_item_id,
                    "origin_correlation_id": origin_correlation_id,
                    "origin_timeline": origin_timeline or {},
                    "intent_id": arguments.get("intent_id") if isinstance(arguments, dict) else None,
                    "stage": "result_record_started",
                    "server_monotonic_ns": __import__("time").perf_counter_ns(),
                    "session_id": session_id, "generation": generation,
                },
                idempotency_key=f"brain-result:{session_id}:{generation}:{call_id}",
            )
        if control is None:
            adapter = get_voice_session_adapter(realtime.provider_adapter)
            await adapter.send_tool_result(
                session_id=session_id,
                generation=generation,
                call_id=call_id,
                output={"ok": ok, "result": output},
            )
            if adapter.capabilities.supports_manual_response:
                await self._queue_controlled_response(
                    session_id,
                    generation,
                    "tool_continuation",
                )
        elif control == "start_new_jarvis_log":
            await self.start_fresh_log(idempotency_key=f"brain:{call_id}:fresh")
        elif control == "end_voice_session":
            await self.close_session(session_id, generation=generation, reason="shell_requested")

    async def _persist_delivery(
        self,
        session,
        realtime: LiveSession,
        event: VoiceProviderEvent,
    ) -> DeliveryRecord:
        payload = event.payload
        response_id = str(payload.get("response_id") or event.event_id)
        item_id = str(payload.get("item_id") or response_id)
        existing = (await session.execute(
            select(SpeechDelivery)
            .where(SpeechDelivery.live_session_id == realtime.id)
            .where(SpeechDelivery.provider_response_id == response_id)
            .where(SpeechDelivery.provider_item_id == item_id)
            .limit(1)
        )).scalar_one_or_none()
        if existing is not None:
            return DeliveryRecord(existing.id, existing.assistant_turn_id, existing.state)

        generated = str(payload.get("transcript") or "")
        interrupted = event.kind == "assistant_interrupted" or bool(payload.get("interrupted"))
        heard = str((payload.get("heard_transcript") or "") if interrupted else generated)
        delivery_state = "interrupted" if interrupted else ("completed" if heard else "unheard")
        delivery = SpeechDelivery(
            id=str(uuid4()),
            live_session_id=realtime.id,
            provider_response_id=response_id,
            provider_item_id=item_id,
            generated_transcript=generated,
            heard_transcript=heard or None,
            state=delivery_state,
            generation_started_at=utc_now(),
            interrupted_at=utc_now() if interrupted else None,
            completed_at=utc_now(),
            source_task_id=str(payload.get("source_task_id") or "") or None,
            source_task_event_id=str(payload.get("source_task_event_id") or "") or None,
            presentation_id=self._first_presentation_id(payload),
            provider_causality_json={
                "provider_event_id": event.event_id,
                "provider_response_id": response_id,
                "provider_item_id": item_id,
            },
            timing_summary_json=(
                dict(payload.get("timing_summary"))
                if isinstance(payload.get("timing_summary"), dict)
                else {}
            ),
        )
        session.add(delivery)
        await session.flush()

        repo = ConversationRepository(session)
        conversation_event = await repo.append_event(
            conversation_id=realtime.conversation_id,
            run_id=None,
            actor_kind="assistant",
            event_type=f"conversation.assistant.message.{delivery_state}",
            payload_json={
                "delivery_id": delivery.id,
                "generated_transcript": generated,
                "heard_transcript": heard,
            },
            branch_key="main",
            idempotency_key=f"provider-delivery:{response_id}:{item_id}",
        )
        existing_turn_id = str((conversation_event.payload_json or {}).get("turn_id") or "")
        if existing_turn_id:
            delivery.assistant_turn_id = existing_turn_id
            return DeliveryRecord(delivery.id, existing_turn_id, delivery_state)
        turn_id = None
        if heard:
            message = await repo.create_message(
                conversation_id=realtime.conversation_id,
                source_event_id=conversation_event.id,
                role="assistant",
                author_label="Jarvis",
                content=heard,
                modality=str(payload.get("modality") or "voice"),
                execution_mode="jarvis_shell",
                communication_state=delivery_state,
                source_task_id=delivery.source_task_id,
                source_task_event_id=delivery.source_task_event_id,
            )
            delivery.assistant_turn_id = message.id
            conversation_event.payload_json = {
                **(conversation_event.payload_json or {}),
                "turn_id": message.id,
            }
            turn_id = message.id
        return DeliveryRecord(delivery.id, turn_id, delivery_state)

    async def _persist_user_transcript(
        self,
        session,
        realtime: LiveSession,
        event: VoiceProviderEvent,
    ) -> None:
        transcript = str(event.payload.get("transcript") or "")
        if not transcript.strip():
            return
        item_id = str(event.payload.get("item_id") or event.event_id)
        repo = ConversationRepository(session)
        conversation_event = await repo.append_event(
            conversation_id=realtime.conversation_id,
            run_id=None,
            actor_kind="user",
            event_type="conversation.user_message.accepted",
            payload_json={
                "content": transcript,
                "modality": "voice",
                "provider_item_id": item_id,
                "timeline": {
                    "start_ms": event.payload.get("start_ms"),
                    "end_ms": event.payload.get("end_ms"),
                    "fragments": event.payload.get("fragments", []),
                },
            },
            branch_key="main",
            idempotency_key=f"provider-transcript:{realtime.id}:{item_id}",
        )
        existing_turn_id = str((conversation_event.payload_json or {}).get("turn_id") or "")
        if existing_turn_id:
            existing_message = await session.get(ConversationMessage, existing_turn_id)
            if existing_message is not None:
                existing_message.content = transcript
            conversation_event.payload_json = {
                **(conversation_event.payload_json or {}),
                "content": transcript,
                "timeline": {
                    "start_ms": event.payload.get("start_ms"),
                    "end_ms": event.payload.get("end_ms"),
                    "fragments": event.payload.get("fragments", []),
                },
            }
            self._provider_input_turns[(realtime.id, realtime.generation, item_id)] = existing_turn_id
            return
        message = await repo.create_message(
            conversation_id=realtime.conversation_id,
            source_event_id=conversation_event.id,
            role="user",
            author_label="You",
            content=transcript,
            modality="voice",
            execution_mode="jarvis_shell",
            communication_state="completed",
        )
        conversation_event.payload_json = {
            **(conversation_event.payload_json or {}),
            "turn_id": message.id,
        }
        self._provider_input_turns[(realtime.id, realtime.generation, item_id)] = message.id
        await repo.touch_conversation(realtime.conversation_id, updated_at=utc_now())

    async def _persist_assistant_transcript(
        self,
        session,
        realtime: LiveSession,
        event: VoiceProviderEvent,
    ) -> None:
        transcript = str(event.payload.get("transcript") or "")
        if not transcript.strip():
            return
        item_id = str(event.payload.get("item_id") or event.event_id)
        timeline = {
            "start_ms": event.payload.get("start_ms"),
            "end_ms": event.payload.get("end_ms"),
            "fragments": event.payload.get("fragments", []),
        }
        repo = ConversationRepository(session)
        conversation_event = await repo.append_event(
            conversation_id=realtime.conversation_id,
            run_id=None,
            actor_kind="assistant",
            event_type="conversation.assistant.message.generated",
            payload_json={
                "content": transcript,
                "modality": "voice",
                "provider_item_id": item_id,
                "timeline": timeline,
                "playback_verified": False,
            },
            branch_key="main",
            idempotency_key=f"provider-assistant-transcript:{realtime.id}:{item_id}",
        )
        existing_turn_id = str((conversation_event.payload_json or {}).get("turn_id") or "")
        if existing_turn_id:
            existing_message = await session.get(ConversationMessage, existing_turn_id)
            if existing_message is not None:
                existing_message.content = transcript
            conversation_event.payload_json = {
                **(conversation_event.payload_json or {}),
                "content": transcript,
                "timeline": timeline,
                "playback_verified": False,
            }
            return
        message = await repo.create_message(
            conversation_id=realtime.conversation_id,
            source_event_id=conversation_event.id,
            role="assistant",
            author_label="Jarvis",
            content=transcript,
            modality="voice",
            execution_mode="jarvis_shell",
            communication_state="generated",
        )
        conversation_event.payload_json = {
            **(conversation_event.payload_json or {}),
            "turn_id": message.id,
        }
        await repo.touch_conversation(realtime.conversation_id, updated_at=utc_now())

    @staticmethod
    def _first_presentation_id(payload: dict[str, Any]) -> str | None:
        value = payload.get("presentation_ids")
        if not isinstance(value, list) or not value:
            return None
        return str(value[0]) or None

    async def _assert_generation(self, session_id: str, generation: int) -> None:
        async with session_scope() as session:
            await self._require_current_generation(session, session_id, generation)

    @staticmethod
    async def _require_current_generation(session, session_id: str, generation: int) -> LiveSession:
        realtime = await session.get(LiveSession, session_id)
        if (
            realtime is None
            or realtime.generation != generation
            or realtime.lifecycle_state not in ACTIVE_SESSION_STATES
        ):
            raise StaleSessionError("Live session generation is stale")
        newer = (await session.execute(
            select(LiveSession.id)
            .where(LiveSession.generation > generation)
            .where(LiveSession.lifecycle_state.in_(sorted(ACTIVE_SESSION_STATES)))
            .limit(1)
        )).scalar_one_or_none()
        if newer is not None:
            raise StaleSessionError("Live session generation is stale")
        return realtime

    @staticmethod
    async def _floor_state(session, session_id: str) -> str:
        floor, _observed_at = await JarvisCoordinator._floor_observation(session, session_id)
        return floor

    @staticmethod
    async def _floor_observation(
        session,
        session_id: str,
    ) -> tuple[str, datetime | None]:
        events = list((await session.execute(
            select(LiveSessionEvent)
            .where(LiveSessionEvent.session_id == session_id)
            .order_by(LiveSessionEvent.sequence.desc())
            .limit(50)
        )).scalars().all())
        for index, event in enumerate(events):
            event_type = event.event_type
            if event_type == "provider.event.ignored":
                continue
            if event_type.startswith("provider."):
                provider_kind = event_type.removeprefix("provider.")
                if provider_kind in {"usage", "delegation_usage", "delegation_tier_fallback", "provider_protocol_warning", "assistant_update_submitted"}:
                    continue
                if provider_kind in {"user_transcript_delta", "user_transcript"}:
                    continue
                if provider_kind in {"assistant_response", "assistant_interrupted", "assistant_transcript"}:
                    for older_event in events[index + 1 :]:
                        older_event_type = older_event.event_type
                        older_kind = older_event_type.removeprefix("provider.")
                        if older_kind in {"user_transcript_delta", "user_transcript"}:
                            continue
                        if older_kind == "user_speaking":
                            return "user_speaking", older_event.created_at
                        if older_kind == "user_stopped":
                            break
                return FLOOR_EVENT_TYPES.get(provider_kind, "thinking"), event.created_at
            if event_type == "session.connecting":
                return "connecting", event.created_at
            if event_type == "session.superseded":
                return "text_fallback", event.created_at
        return "thinking", None

    @staticmethod
    async def _is_late_delivery_state(session, realtime: LiveSession, event: VoiceProviderEvent) -> bool:
        if event.kind not in {"assistant_generating", "assistant_speaking"}:
            return False
        response_id = str(event.payload.get("response_id") or "")
        if not response_id:
            return False
        existing = (await session.execute(
            select(SpeechDelivery.id)
            .where(SpeechDelivery.live_session_id == realtime.id)
            .where(SpeechDelivery.provider_response_id == response_id)
            .where(SpeechDelivery.state.in_(["completed", "interrupted", "unheard"]))
            .limit(1)
        )).scalar_one_or_none()
        return existing is not None

    @staticmethod
    async def _context_in_session(
        session,
        *,
        conversation_id: str,
        session_id: str | None,
    ) -> dict[str, Any]:
        projected = await jarvis_situation_projector.project_in_session(
            session,
            conversation_id=conversation_id,
            session_id=session_id,
        )
        return projected.payload

    @staticmethod
    def _record(realtime: LiveSession, *, floor_state: str) -> SessionRecord:
        return SessionRecord(
            id=realtime.id,
            conversation_id=realtime.conversation_id,
            generation=realtime.generation,
            provider_adapter=realtime.provider_adapter,
            lifecycle_state=realtime.lifecycle_state,
            controller_id=realtime.controller_id,
            activation_id=realtime.activation_id,
            activation_source=realtime.activation_source,
            workflow_catalog_revision=realtime.workflow_catalog_revision,
            floor_state=floor_state,
            last_event_sequence=realtime.last_event_sequence,
            created_at=realtime.created_at,
            ready_at=realtime.ready_at,
            disconnected_at=realtime.disconnected_at,
            client_time_zone=realtime.client_time_zone,
            client_utc_offset_minutes=realtime.client_utc_offset_minutes,
            client_observed_at=realtime.client_observed_at,
        )


jarvis_coordinator = JarvisCoordinator()
