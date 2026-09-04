"""Jarvis daily-log state and durable typed-turn endpoints."""
from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sse_starlette.sse import EventSourceResponse

from app.config import settings
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
    JarvisRealtimeCallRequest,
    JarvisRealtimeCallResponse,
    ReminderCreateRequest,
    ReminderSnoozeRequest,
    JarvisSessionEventReplayResponse,
    JarvisSessionEventView,
    JarvisSessionView,
    JarvisStateResponse,
    JarvisTextTurnRequest,
    JarvisTurnAcceptedResponse,
    TaskReferenceCandidate,
    TaskReferenceRequest,
    TaskResolutionView,
)
from app.presenters.conversations import present_conversation_message
from app.presenters.conversations import present_conversation_event
from app.services.jarvis_devices import require_jarvis_auth
from app.services.conversation_read_model import conversation_read_model
from app.services.jarvis_coordinator import StaleSessionError, jarvis_coordinator
from app.services.jarvis_daily import jarvis_daily_service
from app.services.jarvis_live import jarvis_live_service
from app.services.jarvis_preferences import jarvis_preferences_service
from app.services.jarvis_situation import jarvis_situation_projector
from app.services.natural_task_resolver import natural_task_resolver, task_title
from app.services.reminder_scheduler import reminder_scheduler
from app.services.openai_realtime_adapter import OpenAIRealtimeConfigurationError
from app.services.task_presentations import task_presentation_service
from app.services.task_service import ACTIVE_TASK_STATUSES, task_service
from app.models.tasks import TaskSummaryView, WorkerHealthView


router = APIRouter(prefix="/api/jarvis", tags=["jarvis"], dependencies=[Depends(require_jarvis_auth)])


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
        status_reason=task.status_reason,
        creation_order=task.creation_order,
        current_attempt_id=task.current_attempt_id,
        cancellation_requested_at=task.cancellation_requested_at,
        importance=task.importance,
        presentation_policy=task.presentation_policy,
        terminal_summary=task.terminal_summary,
        waiting_until=task.waiting_until,
        resume_at=task.resume_at,
        wait_reason=task.wait_reason,
        scheduled_delivery=task.scheduled_delivery_json,
        scheduled_delivered_at=task.scheduled_delivered_at,
        queue_position=snapshot.queue_position,
        created_at=task.created_at,
        updated_at=task.updated_at,
        finished_at=task.finished_at,
    )


def _http_error(exc: ValueError) -> HTTPException:
    if isinstance(exc, StaleSessionError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, OpenAIRealtimeConfigurationError):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(
        status_code=404 if "not found" in str(exc).lower() else 409,
        detail=str(exc),
    )


@router.get("/state", response_model=JarvisStateResponse)
async def get_jarvis_state() -> JarvisStateResponse:
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
    preferences, voice_changed = await jarvis_preferences_service.patch(
        request.model_dump(exclude_unset=True)
    )
    if voice_changed:
        await jarvis_coordinator.request_voice_rotation(preferences.voice)
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


@router.post("/tasks/resolve", response_model=TaskResolutionView)
async def resolve_task_reference(request: TaskReferenceRequest) -> TaskResolutionView:
    result = await natural_task_resolver.resolve(request.reference, active_only=request.active_only)
    if result.task is not None:
        await natural_task_resolver.touch(result.task.id)
    return TaskResolutionView(
        resolved=result.task is not None,
        ambiguous=result.ambiguous,
        internal_task_id=None if result.task is None else result.task.id,
        task=None if result.task is None else TaskReferenceCandidate(
            title=task_title(result.task),
            status=result.task.status,
            created_at=result.task.created_at,
        ),
        candidates=[
            TaskReferenceCandidate(title=task_title(item), status=item.status, created_at=item.created_at)
            for item in result.candidates
        ],
        clarification=result.clarification,
    )


@router.get("/reminders", response_model=list[TaskSummaryView])
async def list_reminders() -> list[TaskSummaryView]:
    snapshots = await task_service.list_tasks()
    return [
        _task_summary(item)
        for item in snapshots
        if (item.task.scheduled_delivery_json or {}).get("kind") == "reminder"
    ]


@router.post("/reminders", response_model=TaskSummaryView, status_code=status.HTTP_201_CREATED)
async def create_reminder(request: ReminderCreateRequest) -> TaskSummaryView:
    state = await jarvis_daily_service.get_state()
    preferences = await jarvis_preferences_service.get()
    try:
        snapshot = await reminder_scheduler.create(
            text=request.text,
            due_at=request.due_at,
            timing=request.timing,
            active_time_zone=preferences.time_zone,
            idempotency_key=request.idempotency_key,
            origin_conversation_id=None if state.current_log is None else state.current_log.id,
            allow_duplicate=request.allow_duplicate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await natural_task_resolver.touch(snapshot.task.id)
    return _task_summary(snapshot)


@router.post("/reminders/{task_id}/snooze", response_model=TaskSummaryView)
async def snooze_reminder(task_id: str, request: ReminderSnoozeRequest) -> TaskSummaryView:
    preferences = await jarvis_preferences_service.get()
    try:
        snapshot = await reminder_scheduler.snooze(
            task_id,
            due_at=request.due_at,
            timing=request.timing,
            active_time_zone=preferences.time_zone,
            idempotency_key=request.idempotency_key,
        )
    except ValueError as exc:
        raise _http_error(exc) from exc
    await natural_task_resolver.touch(snapshot.task.id)
    return _task_summary(snapshot)


@router.delete("/reminders/{task_id}", response_model=TaskSummaryView)
async def cancel_reminder(task_id: str) -> TaskSummaryView:
    detail = await task_service.get_task(task_id)
    if detail is None or (detail.task.scheduled_delivery_json or {}).get("kind") != "reminder":
        raise HTTPException(status_code=404, detail="Reminder not found")
    try:
        snapshot = await task_service.cancel(task_id, idempotency_key=f"reminder-api:{uuid4()}")
    except ValueError as exc:
        raise _http_error(exc) from exc
    return _task_summary(snapshot)


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
            detail="Realtime provider connection failed",
        ) from exc
    return JarvisRealtimeCallResponse(
        role="controller",
        session=_session_view(bootstrap.session),
        sdp_answer=bootstrap.sdp_answer,
        rotate_after_seconds=(
            settings.openai_realtime_rotation_minutes * 60
            if request.provider_adapter == "openai"
            else None
        ),
    )


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


@router.delete("/realtime/sessions/{session_id}", status_code=204)
async def close_realtime_session(
    session_id: str,
    generation: int = Query(gt=0),
) -> None:
    try:
        await jarvis_coordinator.close_session(session_id, generation=generation)
    except ValueError as exc:
        raise _http_error(exc) from exc
