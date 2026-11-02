"""Jarvis daily-log state and durable typed-turn endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
import asyncio
import time
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sse_starlette.sse import EventSourceResponse
from fastapi.responses import JSONResponse

from app.config import settings
from app.presenters.tasks import present_task_summary
from app.models.jarvis import (
    JarvisFreshLogRequest,
    JarvisFreshLogResponse,
    JarvisConversationEventReplayResponse,
    JarvisDomainCursors,
    JarvisFakeProviderEventRequest,
    JarvisPendingQuestionView,
    JarvisPresentationView,
    JarvisPreferencesPatch,
    JarvisPreferencesView,
    JarvisLiveCallRequest,
    JarvisLiveActivationContext,
    JarvisLiveCallResponse,
    JarvisLiveTelemetryAccepted,
    JarvisLiveTelemetryBatch,
    JarvisPlaybackBatch,
    JarvisLiveEventReplayResponse,
    JarvisLiveEventView,
    JarvisSessionEventReplayResponse,
    JarvisSessionEventView,
    JarvisSessionView,
    JarvisStateResponse,
    JarvisTextTurnRequest,
    JarvisTurnAcceptedResponse,
)
from app.presenters.conversations import present_conversation_message
from app.presenters.conversations import present_conversation_event
from app.services.jarvis.devices import require_jarvis_auth, jarvis_device_service
from app.services.auth import SESSION_COOKIE_NAME, verify_session_token, authorization_scheme
from app.services.conversations.read_model import conversation_read_model
from app.services.jarvis.coordinator import StaleSessionError, jarvis_coordinator
from app.services.jarvis.daily import jarvis_daily_service
from app.services.jarvis.live import jarvis_live_service
from app.services.jarvis.live_telemetry import jarvis_live_telemetry
from app.services.jarvis.preferences import jarvis_preferences_service
from app.services.jarvis.situation import jarvis_situation_projector
from app.services.jarvis.openai_live_adapter import OpenAILiveConfigurationError
from app.services.updates.presentations import task_presentation_service
from app.services.tasks.service import task_service
from app.services.tasks.vocabulary import ACTIVE_TASK_STATUSES
from app.models.tasks import WorkerHealthView


router = APIRouter(prefix="/api/jarvis", tags=["jarvis"], dependencies=[Depends(require_jarvis_auth)])


@router.get("/runtime-candidate")
async def runtime_candidate(request: Request):
    """Authenticated development acceptance identity; no credentials or paths."""
    from app.jarvis_tasks.acceptance import candidate_identity, database_identity
    runtime = getattr(request.app.state, "acceptance_runtime", None)
    if runtime is None:
        raise HTTPException(503, "Backend startup identity is unavailable")
    current = await asyncio.to_thread(candidate_identity)
    if current != runtime["candidate"] or database_identity(settings.database_path) != runtime["database_identity"]:
        raise HTTPException(409, "Backend source/configuration changed after startup")
    return JSONResponse(runtime, headers={"Cache-Control": "no-store"})


def _session_view(session) -> JarvisSessionView | None:
    return None if session is None else JarvisSessionView.model_validate(session, from_attributes=True)



def _http_error(exc: ValueError) -> HTTPException:
    if isinstance(exc, StaleSessionError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, OpenAILiveConfigurationError):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(
        status_code=404 if "not found" in str(exc).lower() else 409,
        detail=str(exc),
    )


@router.get("/state", response_model=JarvisStateResponse)
async def get_jarvis_state() -> JarvisStateResponse:
    # Capture this before reading projections. Events racing with the state read
    # are replayed (at worst redundantly); no event can be skipped unseen.
    live_event_cursor = await jarvis_live_service.latest_cursor()
    state = await jarvis_daily_service.get_state()
    situation = None
    detail = None
    conversation_cursor = 0
    if state.current_log is not None:
        situation = await jarvis_situation_projector.project(
            conversation_id=state.current_log.id,
            advance_revision=False,
        )
        detail = await conversation_read_model.get_conversation_detail(state.current_log.id)
        conversation_cursor, _ = await jarvis_coordinator.replay_conversation_events(
            state.current_log.id,
            after_sequence=0,
            limit=1,
        )
    tasks = await task_service.list_tasks(statuses=ACTIVE_TASK_STATUSES)
    questions: list[JarvisPendingQuestionView] = []
    for snapshot in tasks:
        task_detail = await task_service.get_task(snapshot.task.id)
        if task_detail is None:
            continue
        questions.extend(
            JarvisPendingQuestionView(
                task_id=snapshot.task.id,
                question_id=question.id,
                question=question.question,
                asked_at=question.asked_at,
            )
            for question in task_detail.questions
            if question.state == "asked"
        )
    presentations = await task_presentation_service.list_pending()
    active_session = (
        None
        if state.current_log is None
        else await jarvis_coordinator.get_latest_session(conversation_id=state.current_log.id)
    )
    latest_task_cursor, _ = await task_service.replay_events(after_sequence=0, limit=1)
    worker = await task_service.worker_health()
    return JarvisStateResponse(
        operational_day=state.operational_day,
        state_revision=state.state_revision,
        situation_revision=0 if situation is None else situation.revision,
        live_event_cursor=live_event_cursor,
        current_log=detail,
        tasks=[present_task_summary(item.task, item.queue_position) for item in tasks],
        pending_questions=questions,
        pending_presentations=[
            JarvisPresentationView(
                id=item.id,
                task_id=item.task_id,
                candidate_type=item.candidate_type,
                importance=item.importance,
                state=item.state,
                required_facts=item.required_facts_json,
                created_at=item.created_at,
                handled_at=item.handled_at,
                handled_by=item.handled_by,
            )
            for item in presentations
        ],
        session=_session_view(active_session),
        worker_health=WorkerHealthView(
            status=worker.status,
            worker_id=worker.worker_id,
            heartbeat_at=worker.heartbeat_at,
        ),
        component_health={} if situation is None else situation.payload["component_health"],
        cursors=JarvisDomainCursors(
            conversation=conversation_cursor,
            task=latest_task_cursor,
            session=0 if active_session is None else active_session.last_event_sequence,
        ),
        focused_task_id=state.focused_task_id,
        recent_task_refs=list(state.recent_task_refs),
    )


@router.get("/preferences", response_model=JarvisPreferencesView)
async def get_jarvis_preferences() -> JarvisPreferencesView:
    preferences = await jarvis_preferences_service.get()
    return JarvisPreferencesView(
        voice=preferences.voice,
        concision=preferences.concision,
        wake_behavior=preferences.wake_behavior,
        developer_mode=preferences.developer_mode,
        android_privacy=preferences.android_privacy,
        time_zone=preferences.time_zone,
        updated_at=preferences.updated_at,
    )


@router.patch("/preferences", response_model=JarvisPreferencesView)
async def patch_jarvis_preferences(request: JarvisPreferencesPatch) -> JarvisPreferencesView:
    preferences, _voice_changed = await jarvis_preferences_service.patch(
        request.model_dump(exclude_unset=True)
    )
    return JarvisPreferencesView(
        voice=preferences.voice,
        concision=preferences.concision,
        wake_behavior=preferences.wake_behavior,
        developer_mode=preferences.developer_mode,
        android_privacy=preferences.android_privacy,
        time_zone=preferences.time_zone,
        updated_at=preferences.updated_at,
    )


@router.get("/live")
async def stream_jarvis_live_events(
    request: Request,
    cursor: int = Query(default=0, ge=0),
) -> EventSourceResponse:
    return EventSourceResponse(
        jarvis_live_service.stream(request, cursor=cursor),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        ping=None,
    )


@router.get("/live/replay", response_model=JarvisLiveEventReplayResponse)
async def replay_jarvis_live_events(
    request: Request,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=250, ge=1, le=1000),
) -> JarvisLiveEventReplayResponse:
    latest, rows = await jarvis_live_service.replay(after=after, limit=limit)
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    browser_owner = bool(cookie and verify_session_token(cookie))
    device = None if browser_owner else await jarvis_device_service.authenticate(request)
    viewer_device_id = None if device is None else device.id
    return JarvisLiveEventReplayResponse(
        latest_sequence=latest,
        events=[
            JarvisLiveEventView.model_validate(jarvis_live_service.event_payload(row, native_device=device is not None and authorization_scheme(request) == "bearer"))
            for row in rows
            if row.target_device_id is None or browser_owner or row.target_device_id == viewer_device_id
        ],
    )


@router.post(
    "/turns/text",
    response_model=JarvisTurnAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def accept_jarvis_text_turn(request: JarvisTextTurnRequest) -> JarvisTurnAcceptedResponse:
    try:
        accepted = await jarvis_coordinator.accept_text_turn(
            request.content,
            idempotency_key=request.idempotency_key or str(uuid4()),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JarvisTurnAcceptedResponse(
        conversation=await conversation_read_model.present_summary(accepted.conversation),
        message=present_conversation_message(accepted.message),
        event_sequence=accepted.event_sequence,
    )


@router.post("/logs/new", response_model=JarvisFreshLogResponse)
async def start_fresh_jarvis_log(request: JarvisFreshLogRequest) -> JarvisFreshLogResponse:
    current_log = await jarvis_coordinator.start_fresh_log(
        idempotency_key=request.idempotency_key or str(uuid4()),
    )
    return JarvisFreshLogResponse(
        current_log=await conversation_read_model.present_summary(current_log)
    )


@router.get(
    "/logs/{conversation_id}/events",
    response_model=JarvisConversationEventReplayResponse,
)
async def replay_jarvis_log_events(
    conversation_id: str,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=250, ge=1, le=1000),
) -> JarvisConversationEventReplayResponse:
    try:
        latest, events = await jarvis_coordinator.replay_conversation_events(
            conversation_id,
            after_sequence=after,
            limit=limit,
        )
    except ValueError as exc:
        raise _http_error(exc) from exc
    return JarvisConversationEventReplayResponse(
        conversation_id=conversation_id,
        latest_sequence=latest,
        events=[present_conversation_event(item) for item in events],
    )


@router.post("/live/calls", response_model=JarvisLiveCallResponse)
async def create_live_call(request: JarvisLiveCallRequest, http_request: Request) -> JarvisLiveCallResponse:
    # A paired browser's random tab identifier is not a screenshot device.
    # Derive ownership from authentication, never from client-supplied IDs.
    device = await jarvis_device_service.authenticate(http_request)
    phone_browser = device is not None and authorization_scheme(http_request) != "bearer"
    if device is not None:
        request = request.model_copy(update={"controller_id": device.id})
    if phone_browser and request.activation_context is None:
        request = request.model_copy(update={"activation_context": JarvisLiveActivationContext(
            source="manual", activation_id=str(uuid4()))})
    request_id = str(uuid4())
    request_started = time.monotonic()
    server_received_at = datetime.now(timezone.utc)
    activation_id = None if request.activation_context is None else request.activation_context.activation_id
    trace_id = activation_id or request_id
    jarvis_live_telemetry.record({
        "trace_id": trace_id,
        "activation_id": activation_id,
        "session_id": None,
        "generation": None,
        "request_id": request_id,
        "provider_response_id": None,
        "source": "backend",
        "event_type": "call.request_received",
        "observed_at": datetime.now(timezone.utc),
        "monotonic_ms": None,
        "duration_ms": None,
        "detail_json": {"controller_id": request.controller_id, "role": request.role},
    })
    if request.role == "observer":
        observed_record = await jarvis_coordinator.observe()
        observed_session = _session_view(observed_record)
        return JarvisLiveCallResponse(
            role="observer",
            request_id=request_id,
            session=observed_session,
            server_received_at=server_received_at,
            server_responded_at=datetime.now(timezone.utc),
        )
    state = await jarvis_daily_service.get_state()
    if state.current_log is None:
        await jarvis_daily_service.start_fresh_log(
            idempotency_key=f"voice-bootstrap:{request.controller_id}"
        )
        state = await jarvis_daily_service.get_state()
    if state.current_log is None:
        raise HTTPException(status_code=409, detail="Unable to prepare the Jarvis log")
    try:
        bootstrap = await jarvis_coordinator.bootstrap_controller(
            conversation_id=state.current_log.id,
            controller_id=request.controller_id,
            adapter_name=request.provider_adapter,
            sdp_offer=request.sdp_offer,
            client_context=request.client_context,
            activation_context=request.activation_context,
        )
    except ValueError as exc:
        raise _http_error(exc) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Live provider connection failed",
        ) from exc
    if phone_browser:
        from app.services.tasks.capture import task_capture_service
        await task_capture_service.request_browser_activation(
            session_id=bootstrap.session.id, generation=bootstrap.session.generation)
    server_responded_at = datetime.now(timezone.utc)
    result = JarvisLiveCallResponse(
        role="controller",
        request_id=request_id,
        session=_session_view(bootstrap.session),
        provider_capabilities={**bootstrap.provider_capabilities, "playback_evidence": True},
        sdp_answer=bootstrap.sdp_answer,
        server_received_at=server_received_at,
        server_responded_at=server_responded_at,
    )
    jarvis_live_telemetry.record({
        "trace_id": trace_id,
        "activation_id": activation_id,
        "session_id": bootstrap.session.id,
        "generation": bootstrap.session.generation,
        "request_id": request_id,
        "provider_response_id": None,
        "source": "backend",
        "event_type": "call.response_ready",
        "observed_at": datetime.now(timezone.utc),
        "monotonic_ms": None,
        "duration_ms": round((time.monotonic() - request_started) * 1_000, 3),
        "detail_json": {
            "provider_adapter": request.provider_adapter,
        },
    })
    return result


@router.post("/live/telemetry", response_model=JarvisLiveTelemetryAccepted, status_code=202)
async def ingest_live_telemetry(
    request: JarvisLiveTelemetryBatch,
) -> JarvisLiveTelemetryAccepted:
    accepted = jarvis_live_telemetry.record_many([
        {
            "trace_id": event.trace_id,
            "client_sequence": event.client_sequence,
            "activation_id": event.activation_id,
            "session_id": event.session_id,
            "generation": event.generation,
            "request_id": event.request_id,
            "provider_response_id": event.provider_response_id,
            "provider_input_item_id": event.provider_input_item_id,
            "source": event.source,
            "event_type": event.event_type,
            "observed_at": event.observed_at,
            "monotonic_ms": event.monotonic_ms,
            "duration_ms": event.duration_ms,
            "detail_json": event.detail,
        }
        for event in request.events
    ])
    return JarvisLiveTelemetryAccepted(accepted=accepted)


@router.post("/live/sessions/{session_id}/playback", status_code=202)
async def ingest_playback(session_id: str, request: JarvisPlaybackBatch, http_request: Request):
    from app.services.jarvis.observer import conversation_observer
    device = await jarvis_device_service.authenticate(http_request)
    if device is not None and request.controller_id != device.id:
        raise HTTPException(403, "Playback belongs to another controller")
    try:
        await conversation_observer.ingest(session_id=session_id, generation=request.generation,
            controller_id=request.controller_id, sequence=request.sequence,
            reports=[r.model_dump() for r in request.reports])
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"accepted": len(request.reports)}


@router.get("/live/observer")
async def observer_health():
    from app.services.jarvis.observer import conversation_observer
    return {"failure": conversation_observer.failure,
            "running": conversation_observer.task is not None and not conversation_observer.task.done()}


@router.get("/diagnostics")
async def diagnostics():
    """Read-only fault findings with proposed fixes (see app/services/diagnostics.py)."""
    from app.services.jarvis.observer import conversation_observer
    from app.db.session import session_scope
    from app.services.diagnostics import diagnose
    async with session_scope() as db:
        findings = await diagnose(db, observer=conversation_observer)
    return {"status": "degraded" if any(f["severity"] == "high" for f in findings) else "ok", "findings": findings}


@router.get(
    "/live/sessions/{session_id}/events",
    response_model=JarvisSessionEventReplayResponse,
)
async def replay_session_events(
    session_id: str,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=250, ge=1, le=1000),
) -> JarvisSessionEventReplayResponse:
    try:
        latest, events = await jarvis_coordinator.replay_session_events(
            session_id,
            after_sequence=after,
            limit=limit,
        )
    except ValueError as exc:
        raise _http_error(exc) from exc
    return JarvisSessionEventReplayResponse(
        session_id=session_id,
        latest_sequence=latest,
        events=[
            JarvisSessionEventView(
                id=item.id,
                session_id=item.session_id,
                sequence=item.sequence,
                event_type=item.event_type,
                payload=item.payload_json,
                created_at=item.created_at,
            )
            for item in events
        ],
    )


@router.post("/fake/sessions/{session_id}/events", status_code=202)
async def inject_fake_provider_event(
    session_id: str,
    request: JarvisFakeProviderEventRequest,
) -> dict[str, bool]:
    try:
        await jarvis_coordinator.inject_fake_event(
            session_id=session_id,
            generation=request.generation,
            event_id=request.event_id,
            kind=request.kind,
            payload=request.payload,
        )
    except ValueError as exc:
        raise _http_error(exc) from exc
    return {"accepted": True}


@router.delete("/live/sessions/{session_id}", status_code=204)
async def close_live_session(
    session_id: str,
    generation: int = Query(gt=0),
) -> None:
    try:
        await jarvis_coordinator.close_session(session_id, generation=generation)
    except ValueError as exc:
        raise _http_error(exc) from exc
