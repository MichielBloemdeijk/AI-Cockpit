"""Jarvis daily-log state and durable typed-turn endpoints."""
from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.models.jarvis import (
    JarvisFreshLogRequest,
    JarvisFreshLogResponse,
    JarvisConversationEventReplayResponse,
    JarvisDomainCursors,
    JarvisFakeProviderEventRequest,
    JarvisPendingQuestionView,
    JarvisPresentationReservationResponse,
    JarvisPresentationReserveRequest,
    JarvisPresentationView,
    JarvisRealtimeCallRequest,
    JarvisRealtimeCallResponse,
    JarvisSessionEventReplayResponse,
    JarvisSessionEventView,
    JarvisSessionView,
    JarvisStateResponse,
    JarvisTextTurnRequest,
    JarvisTurnAcceptedResponse,
)
from app.presenters.conversations import present_conversation_message
from app.presenters.conversations import present_conversation_event
from app.services.auth import require_auth
from app.services.conversation_read_model import conversation_read_model
from app.services.jarvis_coordinator import StaleSessionError, jarvis_coordinator
from app.services.jarvis_daily import jarvis_daily_service
from app.services.task_presentations import task_presentation_service
from app.services.task_service import ACTIVE_TASK_STATUSES, task_service
from app.models.tasks import TaskSummaryView, WorkerHealthView


router = APIRouter(prefix="/api/jarvis", tags=["jarvis"], dependencies=[Depends(require_auth)])


def _session_view(session) -> JarvisSessionView | None:
    return None if session is None else JarvisSessionView.model_validate(session, from_attributes=True)


def _task_summary(snapshot) -> TaskSummaryView:
    task = snapshot.task
    return TaskSummaryView(
        id=task.id,
        origin_surface=task.origin_surface,
        origin_conversation_id=task.origin_conversation_id,
        origin_turn_id=task.origin_turn_id,
        goal=task.goal,
        status=task.status,
        creation_order=task.creation_order,
        current_attempt_id=task.current_attempt_id,
        cancellation_requested_at=task.cancellation_requested_at,
        importance=task.importance,
        presentation_policy=task.presentation_policy,
        terminal_summary=task.terminal_summary,
        queue_position=snapshot.queue_position,
        created_at=task.created_at,
        updated_at=task.updated_at,
        finished_at=task.finished_at,
    )


def _http_error(exc: ValueError) -> HTTPException:
    if isinstance(exc, StaleSessionError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(
        status_code=404 if "not found" in str(exc).lower() else 409,
        detail=str(exc),
    )


@router.get("/state", response_model=JarvisStateResponse)
async def get_jarvis_state() -> JarvisStateResponse:
    state = await jarvis_daily_service.get_state()
    detail = None
    conversation_cursor = 0
    if state.current_log is not None:
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
        current_log=detail,
        tasks=[_task_summary(item) for item in tasks],
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
            )
            for item in presentations
        ],
        session=_session_view(active_session),
        worker_health=WorkerHealthView(
            status=worker.status,
            worker_id=worker.worker_id,
            heartbeat_at=worker.heartbeat_at,
        ),
        cursors=JarvisDomainCursors(
            conversation=conversation_cursor,
            task=latest_task_cursor,
            session=0 if active_session is None else active_session.last_event_sequence,
        ),
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


@router.post("/realtime/calls", response_model=JarvisRealtimeCallResponse)
async def create_realtime_call(request: JarvisRealtimeCallRequest) -> JarvisRealtimeCallResponse:
    if request.role == "observer":
        return JarvisRealtimeCallResponse(role="observer", session=_session_view(await jarvis_coordinator.observe()))
    state = await jarvis_daily_service.get_state()
    if state.current_log is None:
        raise HTTPException(status_code=409, detail="Accept a Jarvis turn before starting a controller")
    try:
        session = await jarvis_coordinator.open_controller(
            conversation_id=state.current_log.id,
            controller_id=request.controller_id,
            adapter_name=request.provider_adapter,
        )
    except ValueError as exc:
        raise _http_error(exc) from exc
    return JarvisRealtimeCallResponse(role="controller", session=_session_view(session))


@router.get(
    "/sessions/{session_id}/events",
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


@router.post(
    "/sessions/{session_id}/presentations/reserve",
    response_model=JarvisPresentationReservationResponse,
)
async def reserve_presentations(
    session_id: str,
    request: JarvisPresentationReserveRequest,
) -> JarvisPresentationReservationResponse:
    try:
        reservation = await jarvis_coordinator.reserve_presentations(
            session_id=session_id,
            generation=request.generation,
        )
    except ValueError as exc:
        raise _http_error(exc) from exc
    if reservation is None:
        return JarvisPresentationReservationResponse()
    return JarvisPresentationReservationResponse(
        presentation_ids=list(reservation.presentation_ids),
        facts=list(reservation.facts),
    )


@router.delete("/realtime/sessions/{session_id}", status_code=204)
async def close_realtime_session(
    session_id: str,
    generation: int = Query(gt=0),
) -> None:
    try:
        await jarvis_coordinator.close_session(session_id, generation=generation)
    except ValueError as exc:
        raise _http_error(exc) from exc
