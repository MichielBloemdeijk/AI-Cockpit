"""Durable provider-neutral coordinator for the live Jarvis workspace."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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
    RealtimeSession,
    SpeechDelivery,
    Task,
    TaskPresentation,
    TaskQuestion,
    VoiceSessionEvent,
)
from app.services.jarvis_brain import restricted_brain_gateway
from app.services.jarvis_realtime_telemetry import jarvis_realtime_telemetry
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
    "user_stopped": "thinking",
    "user_transcript_delta": "user_speaking",
    "user_transcript": "thinking",
    "assistant_generating": "thinking",
    "assistant_speaking": "assistant_speaking",
    "assistant_transcript_delta": "assistant_speaking",
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
    return detail[:500] or "Realtime provider connection failed"


class StaleSessionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SessionRecord:
    id: str
    conversation_id: str
    generation: int
    provider_adapter: str
    lifecycle_state: str
    controller_id: str | None
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


@dataclass(slots=True)
class _ResponseAttempt:
    request_id: str
    reason: str
    presentation_ids: tuple[str, ...]
    state: str
    started_monotonic: float
    last_activity_monotonic: float
    provider_response_id: str | None = None
    cancel_requested_monotonic: float | None = None
    watchdog_task: asyncio.Task[None] | None = None


@dataclass(slots=True)
class _ResponseGate:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: set[str] = field(default_factory=set)
    attempt: _ResponseAttempt | None = None
    barge_in_task: asyncio.Task[None] | None = None


@dataclass(slots=True)
class _WakeActivation:
    wake_score: float
    activation_id: str | None = None
    verification_state: str = "pending"
    speech_stopped: bool = False


_RESPONSE_PRIORITY = {
    "tool_continuation": 0,
    "user_turn": 1,
    "task_update": 2,
}
_WAKE_VERIFICATION_GRACE_SECONDS = 0.65


class JarvisCoordinator:
    def __init__(self) -> None:
        self._response_gates: dict[tuple[str, int], _ResponseGate] = {}
        self._wake_activations: dict[tuple[str, int], _WakeActivation] = {}
        self._wake_verification_tasks: dict[tuple[str, int], asyncio.Task[None]] = {}
        self._provider_cleanup_tasks: set[asyncio.Task[None]] = set()
        self._background_response_tasks: set[asyncio.Task[None]] = set()
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
        jarvis_realtime_telemetry.record({
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
        activation_id = str(raw_activation_id).strip() if raw_activation_id else None
        return _WakeActivation(wake_score=wake_score, activation_id=activation_id)

    def _schedule_wake_verification(self, session_id: str, generation: int) -> None:
        key = (session_id, generation)
        existing = self._wake_verification_tasks.get(key)
        if existing is not None and not existing.done():
            return

        async def accept_after_grace() -> None:
            try:
                await asyncio.sleep(_WAKE_VERIFICATION_GRACE_SECONDS)
                activation = self._wake_activations.get(key)
                if (
                    activation is None
                    or activation.verification_state != "pending"
                    or not activation.speech_stopped
                ):
                    return
                activation.verification_state = "accepted"
                async with session_scope() as session:
                    realtime = await session.get(RealtimeSession, session_id)
                    if (
                        realtime is None
                        or realtime.generation != generation
                        or realtime.lifecycle_state not in ACTIVE_SESSION_STATES
                    ):
                        return
                    await JarvisDomainRepository(session).append_session_event(
                        session_id=session_id,
                        event_type="wake.verification.accepted",
                        payload_json={
                            "reason": "transcript_grace_elapsed",
                            "wake_score": activation.wake_score,
                            "session_id": session_id,
                            "generation": generation,
                        },
                        idempotency_key=f"wake-verification-timeout:{session_id}:{generation}",
                    )
                await self._queue_controlled_response(session_id, generation, "user_turn")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._trace_response_gate(
                    "response.arbiter.failed",
                    session_id=session_id,
                    generation=generation,
                    reason="wake_verification_fallback",
                    detail={"failure_type": type(exc).__name__},
                )
                logger.exception(
                    "Wake verification fallback failed for session %s generation %s",
                    session_id,
                    generation,
                )
                # A failed verification fallback cannot bring down provider event handling.
                return
            finally:
                if self._wake_verification_tasks.get(key) is asyncio.current_task():
                    self._wake_verification_tasks.pop(key, None)

        self._wake_verification_tasks[key] = asyncio.create_task(
            accept_after_grace(),
            name=f"jarvis-wake-verification:{session_id}:{generation}",
        )

    def _cancel_wake_verification(self, session_id: str, generation: int) -> None:
        task = self._wake_verification_tasks.pop((session_id, generation), None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _clear_wake_activation(self, session_id: str, generation: int) -> None:
        self._cancel_wake_verification(session_id, generation)
        self._wake_activations.pop((session_id, generation), None)

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
                        pending = await task_presentation_service.list_pending()
                        if pending:
                            await self._queue_controlled_response(
                                active.id,
                                active.generation,
                                "task_update",
                            )
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

    async def request_voice_rotation(self, voice: str) -> None:
        """Signal the active controller to reconnect at its next safe boundary."""
        active = await self.get_active_session()
        if active is None:
            return
        async with session_scope() as session:
            realtime = await session.get(RealtimeSession, active.id)
            if realtime is None or realtime.lifecycle_state not in ACTIVE_SESSION_STATES:
                return
            floor = await self._floor_state(session, realtime.id)
            await JarvisDomainRepository(session).append_session_event(
                session_id=realtime.id,
                event_type="session.rotation_requested",
                payload_json={"reason": "voice_changed", "voice": voice, "safe_boundary": floor == "listening"},
                idempotency_key=f"voice-rotation:{realtime.id}:{realtime.generation}:{voice}",
            )

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

    async def _queue_controlled_response(
        self,
        session_id: str,
        generation: int,
        reason: str,
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
        gate = self._response_gates.setdefault(key, _ResponseGate())
        adapter = None
        reservation = None
        context = None
        selected_reason = reason
        self._trace_response_gate(
            "response.arbiter.requested",
            session_id=session_id,
            generation=generation,
            reason=reason,
        )
        try:
            async with gate.lock:
                gate_acquired = time.monotonic()
                gate.pending.add(reason)
                if gate.attempt is not None:
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
                    if not adapter.is_connected(session_id=session_id, generation=generation):
                        self._trace_response_gate(
                            "response.arbiter.deferred",
                            session_id=session_id,
                            generation=generation,
                            reason=selected_reason,
                            detail={"blocked_by": "provider_not_connected", "floor": floor},
                        )
                        return
                    if floor in {"user_speaking", "assistant_speaking", "connecting", "text_fallback"}:
                        self._trace_response_gate(
                            "response.arbiter.deferred",
                            session_id=session_id,
                            generation=generation,
                            reason=selected_reason,
                            detail={"blocked_by": "floor", "floor": floor},
                        )
                        return
                    if selected_reason == "task_update" and floor != "listening":
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
                    reservation_started = situation_finished
                    reservation = await task_presentation_service.reserve_in_session(
                        session,
                        session_id=session_id,
                        generation=generation,
                        user_owns_floor=False,
                        limit=3,
                    )
                    reservation_finished = time.monotonic()
                    if selected_reason == "task_update" and reservation is None:
                        gate.pending.discard(selected_reason)
                        self._trace_response_gate(
                            "response.arbiter.skipped",
                            session_id=session_id,
                            generation=generation,
                            reason=selected_reason,
                            detail={"blocked_by": "no_pending_updates"},
                        )
                        return
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
            await self._queue_controlled_response(session_id, generation, pending_reason)

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
                select(RealtimeSession)
                .where(RealtimeSession.lifecycle_state.in_(sorted(ACTIVE_SESSION_STATES)))
                .order_by(RealtimeSession.generation.asc())
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
                select(func.max(RealtimeSession.generation))
            )).scalar_one_or_none() or 0)
            preferences = await session.get(JarvisPreference, "current")
            if preferences is None:
                preferences = JarvisPreference(id="current")
                session.add(preferences)
                await session.flush()
            temporal = normalize_client_context(
                client_context,
                fallback_time_zone=preferences.time_zone,
            )
            preferences.time_zone = temporal.time_zone
            realtime = RealtimeSession(
                id=str(uuid4()),
                conversation_id=conversation_id,
                generation=latest_generation + 1,
                provider_adapter=adapter.name,
                lifecycle_state="connecting",
                controller_id=controller_id,
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
            record = self._record(realtime, floor_state="connecting")
            context = await self._context_in_session(
                session,
                conversation_id=conversation_id,
                session_id=realtime.id,
            )
            if wake_activation is not None and wake_activation.activation_id:
                context = {**context, "activation_id": wake_activation.activation_id}

        if wake_activation is not None:
            self._wake_activations[(record.id, record.generation)] = wake_activation

        for old_session_id, old_generation, old_adapter in superseded_adapters:
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
            self._clear_wake_activation(record.id, record.generation)
            failure_type = type(exc).__name__
            failure_detail = _safe_connection_failure(exc)
            logger.exception(
                "Realtime provider connection failed for session %s generation %s: %s",
                record.id,
                record.generation,
                failure_detail,
            )
            async with session_scope() as session:
                failed = await session.get(RealtimeSession, record.id)
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
            raise RuntimeError("Realtime session disappeared after connection")
        return ControllerBootstrap(session=refreshed, sdp_answer=connection.sdp_answer)

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
                select(RealtimeSession)
                .where(RealtimeSession.lifecycle_state.in_(sorted(ACTIVE_SESSION_STATES)))
                .order_by(RealtimeSession.generation.desc())
                .limit(1)
            )).scalar_one_or_none()
            if realtime is None:
                return None
            return self._record(realtime, floor_state=await self._floor_state(session, realtime.id))

    async def get_session(self, session_id: str) -> SessionRecord | None:
        async with session_scope() as session:
            realtime = await session.get(RealtimeSession, session_id)
            if realtime is None:
                return None
            return self._record(realtime, floor_state=await self._floor_state(session, realtime.id))

    async def get_latest_session(self, *, conversation_id: str) -> SessionRecord | None:
        async with session_scope() as session:
            realtime = (await session.execute(
                select(RealtimeSession)
                .where(RealtimeSession.conversation_id == conversation_id)
                .order_by(RealtimeSession.generation.desc())
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
            self._schedule_barge_in_cancel(session_id, generation)
        shell_call: tuple[str, dict[str, Any], str] | None = None
        presentation_action: tuple[tuple[str, ...], DeliveryRecord, str, str] | None = None
        requeue_presentations: tuple[str, ...] = ()
        release_reservations = False
        response_finished = False
        queue_user_response = False
        queue_task_updates = False
        schedule_wake_fallback = False
        cancel_wake_fallback = False
        clear_wake_activation = False
        async with session_scope() as session:
            realtime = await self._require_current_generation(session, session_id, generation)
            repo = JarvisDomainRepository(session)
            event_type = f"provider.{event.kind}"
            payload = dict(event.payload)
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
            if not appended.created:
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

            if event.kind == "user_transcript":
                await self._persist_user_transcript(session, realtime, event)
                activation = self._wake_activations.get((session_id, generation))
                if activation is not None and activation.verification_state == "pending":
                    decision = verify_wake_transcript(
                        str(event.payload.get("transcript") or ""),
                        activation.wake_score,
                    )
                    activation.verification_state = "accepted" if decision.accepted else "rejected"
                    cancel_wake_fallback = True
                    await repo.append_session_event(
                        session_id=session_id,
                        event_type=f"wake.verification.{activation.verification_state}",
                        payload_json={
                            "reason": decision.reason,
                            "wake_score": activation.wake_score,
                            "session_id": session_id,
                            "generation": generation,
                        },
                        idempotency_key=f"wake-verification:{session_id}:{generation}:{event.event_id}",
                    )
                    queue_user_response = decision.accepted and activation.speech_stopped
            elif event.kind in {"user_stopped", "wake_audio_committed"}:
                activation = self._wake_activations.get((session_id, generation))
                if activation is None:
                    queue_user_response = event.kind == "user_stopped"
                else:
                    activation.speech_stopped = True
                    if activation.verification_state == "accepted":
                        queue_user_response = True
                    elif activation.verification_state == "pending":
                        schedule_wake_fallback = True
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
                response_finished = True
                tool_name = str(payload.get("name") or "")
                arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
                call_id = str(payload.get("call_id") or event.event_id)
                shell_call = (tool_name, dict(arguments), call_id)
                raw_presentation_ids = payload.get("presentation_ids")
                requeue_presentations = tuple(
                    str(item)
                    for item in (raw_presentation_ids if isinstance(raw_presentation_ids, list) else [])
                    if item
                )

        if cancel_wake_fallback:
            self._cancel_wake_verification(session_id, generation)
        if schedule_wake_fallback:
            self._schedule_wake_verification(session_id, generation)
        if clear_wake_activation:
            self._clear_wake_activation(session_id, generation)

        if presentation_action is not None:
            presentation_ids, delivery, heard_transcript, response_kind = presentation_action
            await task_presentation_service.settle_delivery(
                presentation_ids=presentation_ids,
                session_id=session_id,
                generation=generation,
                turn_id=delivery.turn_id,
                delivery_id=delivery.id,
                heard_transcript=heard_transcript,
                response_kind=response_kind,
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

        if response_finished:
            await self._finish_controlled_response(
                session_id,
                generation,
                request_id=str(event.payload.get("request_id") or "") or None,
                response_id=str(event.payload.get("response_id") or "") or None,
            )

        if queue_user_response:
            await self._queue_controlled_response(session_id, generation, "user_turn")

        if queue_task_updates:
            if bool(event.payload.get("defer_initial_updates")):
                self._schedule_background_response(session_id, generation, "task_update")
            else:
                await self._queue_controlled_response(session_id, generation, "task_update")

        if shell_call is not None:
            tool_name, arguments, call_id = shell_call
            await self._execute_shell_call(
                session_id=session_id,
                generation=generation,
                tool_name=tool_name,
                arguments=arguments,
                call_id=call_id,
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

    def _schedule_barge_in_cancel(self, session_id: str, generation: int) -> None:
        gate = self._response_gates.get((session_id, generation))
        if gate is None or (gate.barge_in_task is not None and not gate.barge_in_task.done()):
            return

        async def cancel_after_client_grace() -> None:
            try:
                await asyncio.sleep(0.3)
                response_id: str | None = None
                async with gate.lock:
                    attempt = gate.attempt
                    if attempt is None or attempt.cancel_requested_monotonic is not None:
                        return
                    attempt.state = "cancelling"
                    attempt.cancel_requested_monotonic = time.monotonic()
                    response_id = attempt.provider_response_id
                session = await self.get_session(session_id)
                if session is None:
                    return
                adapter = get_voice_session_adapter(session.provider_adapter)
                await adapter.cancel_response(
                    session_id=session_id,
                    generation=generation,
                    response_id=response_id,
                )
            except (StaleSessionError, ValueError):
                return
            finally:
                if gate.barge_in_task is asyncio.current_task():
                    gate.barge_in_task = None

        gate.barge_in_task = asyncio.create_task(
            cancel_after_client_grace(),
            name=f"jarvis-barge-in-fallback:{session_id}:{generation}",
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
            raise ValueError("Realtime session not found")
        await self._assert_generation(session_id, generation)
        adapter = get_voice_session_adapter(session.provider_adapter)
        await adapter.close(
            session_id=session_id,
            generation=generation,
            reason=reason,
            emit_event=True,
        )
        await self._drop_response_gate(session_id, generation)
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

    async def replay_session_events(
        self,
        session_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 250,
    ) -> tuple[int, tuple[VoiceSessionEvent, ...]]:
        async with session_scope() as session:
            realtime = await session.get(RealtimeSession, session_id)
            if realtime is None:
                raise ValueError("Realtime session not found")
            result = await session.execute(
                select(VoiceSessionEvent)
                .where(VoiceSessionEvent.session_id == session_id)
                .where(VoiceSessionEvent.sequence > after_sequence)
                .order_by(VoiceSessionEvent.sequence.asc())
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
    ) -> None:
        async with session_scope() as session:
            realtime = await self._require_current_generation(session, session_id, generation)
            controller_device = (
                None if not realtime.controller_id else await session.get(JarvisDevice, realtime.controller_id)
            )
            controller_locked = controller_device is not None and controller_device.lock_state == "locked"
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
        try:
            if controller_locked and tool_name in {
                "dispatch_task", "answer_task_question", "cancel_task",
                "set_task_update_policy", "set_jarvis_voice",
            }:
                raise ValueError("That action is unavailable while the device is locked")
            result = await restricted_brain_gateway.execute(
                tool_name=tool_name,
                arguments=arguments,
                call_id=call_id,
                conversation_id=conversation_id,
                origin_turn_id=last_turn_id,
                active_time_zone=active_time_zone or "Europe/Amsterdam",
                now=utc_now(),
            )
            output = result.output
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
            await JarvisDomainRepository(session).append_session_event(
                session_id=session_id,
                event_type="brain.tool.result",
                payload_json={
                    "call_id": call_id,
                    "name": tool_name,
                    "ok": ok,
                    "output": durable_output,
                },
                idempotency_key=f"brain-result:{session_id}:{generation}:{call_id}",
            )
        if control is None:
            if ok and tool_name == "set_jarvis_voice":
                await self.request_voice_rotation(str(output.get("voice") or "marin"))
            adapter = get_voice_session_adapter(realtime.provider_adapter)
            await adapter.send_tool_result(
                session_id=session_id,
                generation=generation,
                call_id=call_id,
                output={"ok": ok, "result": output},
            )
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
        realtime: RealtimeSession,
        event: VoiceProviderEvent,
    ) -> DeliveryRecord:
        payload = event.payload
        response_id = str(payload.get("response_id") or event.event_id)
        item_id = str(payload.get("item_id") or response_id)
        existing = (await session.execute(
            select(SpeechDelivery)
            .where(SpeechDelivery.realtime_session_id == realtime.id)
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
            realtime_session_id=realtime.id,
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

    @staticmethod
    async def _persist_user_transcript(
        session,
        realtime: RealtimeSession,
        event: VoiceProviderEvent,
    ) -> None:
        transcript = str(event.payload.get("transcript") or "").strip()
        if not transcript:
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
            },
            branch_key="main",
            idempotency_key=f"provider-transcript:{realtime.id}:{item_id}",
        )
        existing_turn_id = str((conversation_event.payload_json or {}).get("turn_id") or "")
        if existing_turn_id:
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
    async def _require_current_generation(session, session_id: str, generation: int) -> RealtimeSession:
        realtime = await session.get(RealtimeSession, session_id)
        if (
            realtime is None
            or realtime.generation != generation
            or realtime.lifecycle_state not in ACTIVE_SESSION_STATES
        ):
            raise StaleSessionError("Realtime session generation is stale")
        newer = (await session.execute(
            select(RealtimeSession.id)
            .where(RealtimeSession.generation > generation)
            .where(RealtimeSession.lifecycle_state.in_(sorted(ACTIVE_SESSION_STATES)))
            .limit(1)
        )).scalar_one_or_none()
        if newer is not None:
            raise StaleSessionError("Realtime session generation is stale")
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
            select(VoiceSessionEvent)
            .where(VoiceSessionEvent.session_id == session_id)
            .order_by(VoiceSessionEvent.sequence.desc())
            .limit(50)
        )).scalars().all())
        for index, event in enumerate(events):
            event_type = event.event_type
            if event_type == "provider.event.ignored":
                continue
            if event_type.startswith("provider."):
                provider_kind = event_type.removeprefix("provider.")
                if provider_kind in {"assistant_response", "assistant_interrupted"}:
                    for older_event in events[index + 1 :]:
                        older_event_type = older_event.event_type
                        older_kind = older_event_type.removeprefix("provider.")
                        if older_kind in {"user_speaking", "user_transcript_delta"}:
                            return "user_speaking", older_event.created_at
                        if older_kind in {"user_stopped", "user_transcript"}:
                            break
                return FLOOR_EVENT_TYPES.get(provider_kind, "thinking"), event.created_at
            if event_type == "session.connecting":
                return "connecting", event.created_at
            if event_type == "session.superseded":
                return "text_fallback", event.created_at
        return "thinking", None

    @staticmethod
    async def _is_late_delivery_state(session, realtime: RealtimeSession, event: VoiceProviderEvent) -> bool:
        if event.kind not in {"assistant_generating", "assistant_speaking"}:
            return False
        response_id = str(event.payload.get("response_id") or "")
        if not response_id:
            return False
        existing = (await session.execute(
            select(SpeechDelivery.id)
            .where(SpeechDelivery.realtime_session_id == realtime.id)
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
    def _record(realtime: RealtimeSession, *, floor_state: str) -> SessionRecord:
        return SessionRecord(
            id=realtime.id,
            conversation_id=realtime.conversation_id,
            generation=realtime.generation,
            provider_adapter=realtime.provider_adapter,
            lifecycle_state=realtime.lifecycle_state,
            controller_id=realtime.controller_id,
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
