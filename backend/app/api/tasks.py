"""HTTP boundary for first-class global tasks and their durable trace."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.db.tables import (
    Task,
    TaskArtifact,
    TaskAttempt,
    TaskEvent,
    TaskPresentation,
    TaskQuestion,
    TaskTarget,
)
from app.models.tasks import (
    TaskActionRequest,
    TaskArtifactView,
    TaskAttemptView,
    TaskDetailView,
    TaskDispatchRequest,
    TaskEventReplayResponse,
    TaskEventView,
    TaskPresentationView,
    TaskQuestionAnswerRequest,
    TaskQuestionCreateRequest,
    TaskQuestionView,
    TaskSummaryView,
    TaskTargetView,
    WorkerHealthView,
)
from app.services.auth import require_auth
from app.services.task_service import TaskDetailSnapshot, TaskSnapshot, task_service


router = APIRouter(prefix="/api/tasks", tags=["tasks"], dependencies=[Depends(require_auth)])


def _summary(task: Task, queue_position: int | None) -> TaskSummaryView:
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
        waiting_until=task.waiting_until,
        resume_at=task.resume_at,
        wait_reason=task.wait_reason,
        scheduled_delivery=task.scheduled_delivery_json,
        scheduled_delivered_at=task.scheduled_delivered_at,
        queue_position=queue_position,
        created_at=task.created_at,
        updated_at=task.updated_at,
        finished_at=task.finished_at,
    )


def _event(event: TaskEvent) -> TaskEventView:
    return TaskEventView(
        id=event.id,
        sequence=event.sequence,
        task_id=event.task_id,
        attempt_id=event.attempt_id,
        event_type=event.event_type,
        payload=event.payload_json,
        causality=event.causality_json,
        created_at=event.created_at,
    )


def _attempt(attempt: TaskAttempt) -> TaskAttemptView:
    return TaskAttemptView(
        id=attempt.id,
        attempt_number=attempt.attempt_number,
        worker_id=attempt.worker_id,
        workspace_path=attempt.workspace_path,
        checkpoint=attempt.checkpoint_json,
        claimed_at=attempt.claimed_at,
        heartbeat_at=attempt.heartbeat_at,
        started_at=attempt.started_at,
        ended_at=attempt.ended_at,
        terminal_reason=attempt.terminal_reason,
        agent_runtime_reference=attempt.agent_runtime_reference,
        usage_summary=attempt.usage_summary_json,
        created_at=attempt.created_at,
    )


def _question(question: TaskQuestion) -> TaskQuestionView:
    return TaskQuestionView(
        id=question.id,
        asked_attempt_id=question.asked_attempt_id,
        answered_attempt_id=question.answered_attempt_id,
        source_task_event_id=question.source_task_event_id,
        question=question.question,
        answer=question.answer,
        state=question.state,
        asked_at=question.asked_at,
        answered_at=question.answered_at,
    )


def _artifact(artifact: TaskArtifact) -> TaskArtifactView:
    return TaskArtifactView(
        id=artifact.id,
        attempt_id=artifact.attempt_id,
        source_task_event_id=artifact.source_task_event_id,
        artifact_type=artifact.artifact_type,
        mime_type=artifact.mime_type,
        title=artifact.title,
        uri=artifact.uri,
        content_text=artifact.content_text,
        content_json=artifact.content_json,
        created_at=artifact.created_at,
    )


def _target(target: TaskTarget) -> TaskTargetView:
    return TaskTargetView(
        id=target.id,
        resource_type=target.resource_type,
        resource_key=target.resource_key,
        mutable=target.mutable,
        discovered_at=target.discovered_at,
    )


def _presentation(presentation: TaskPresentation) -> TaskPresentationView:
    return TaskPresentationView(
        id=presentation.id,
        candidate_type=presentation.candidate_type,
        coalescing_key=presentation.coalescing_key,
        importance=presentation.importance,
        state=presentation.state,
        source_task_event_id=presentation.source_task_event_id,
        created_at=presentation.created_at,
        presented_at=presentation.presented_at,
    )


def _detail(snapshot: TaskDetailSnapshot) -> TaskDetailView:
    base = _summary(snapshot.task, snapshot.queue_position)
    return TaskDetailView(
        **base.model_dump(),
        attempts=[_attempt(item) for item in snapshot.attempts],
        events=[_event(item) for item in snapshot.events],
        questions=[_question(item) for item in snapshot.questions],
        artifacts=[_artifact(item) for item in snapshot.artifacts],
        targets=[_target(item) for item in snapshot.targets],
        presentations=[_presentation(item) for item in snapshot.presentations],
        worker_health=WorkerHealthView(
            status=snapshot.worker_health.status,
            worker_id=snapshot.worker_health.worker_id,
            heartbeat_at=snapshot.worker_health.heartbeat_at,
        ),
    )


def _bad_request(exc: ValueError) -> HTTPException:
    message = str(exc)
    code = status.HTTP_404_NOT_FOUND if "not found" in message.lower() else status.HTTP_409_CONFLICT
    return HTTPException(status_code=code, detail=message)


@router.get("", response_model=list[TaskSummaryView])
async def list_tasks(
    task_status: list[str] | None = Query(default=None, alias="status"),
) -> list[TaskSummaryView]:
    snapshots = await task_service.list_tasks(statuses=set(task_status) if task_status else None)
    return [_summary(item.task, item.queue_position) for item in snapshots]


@router.post("", response_model=TaskSummaryView, status_code=status.HTTP_201_CREATED)
async def dispatch_task(request: TaskDispatchRequest) -> TaskSummaryView:
    try:
        snapshot = await task_service.dispatch(
            goal=request.goal,
            idempotency_key=request.idempotency_key,
            origin_surface=request.origin_surface,
            origin_conversation_id=request.origin_conversation_id,
            origin_turn_id=request.origin_turn_id,
            importance=request.importance,
            presentation_policy=request.presentation_policy,
            targets=tuple(
                (target.resource_type, target.resource_key, target.mutable)
                for target in request.targets
            ),
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return _summary(snapshot.task, snapshot.queue_position)


@router.get("/events", response_model=TaskEventReplayResponse)
async def replay_task_events(
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=250, ge=1, le=1000),
) -> TaskEventReplayResponse:
    latest_sequence, events = await task_service.replay_events(after_sequence=after, limit=limit)
    return TaskEventReplayResponse(
        latest_sequence=latest_sequence,
        events=[_event(item) for item in events],
    )


@router.get("/worker", response_model=WorkerHealthView)
async def get_worker_health() -> WorkerHealthView:
    worker = await task_service.worker_health()
    return WorkerHealthView(
        status=worker.status,
        worker_id=worker.worker_id,
        heartbeat_at=worker.heartbeat_at,
    )


@router.get("/{task_id}", response_model=TaskDetailView)
async def get_task(task_id: str) -> TaskDetailView:
    snapshot = await task_service.get_task(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _detail(snapshot)


@router.post("/{task_id}/cancel", response_model=TaskSummaryView)
async def cancel_task(task_id: str, request: TaskActionRequest) -> TaskSummaryView:
    try:
        snapshot = await task_service.cancel(task_id, idempotency_key=request.idempotency_key)
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return _summary(snapshot.task, snapshot.queue_position)


@router.post("/{task_id}/retry", response_model=TaskSummaryView)
async def retry_task(task_id: str, request: TaskActionRequest) -> TaskSummaryView:
    try:
        snapshot = await task_service.retry(task_id, idempotency_key=request.idempotency_key)
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return _summary(snapshot.task, snapshot.queue_position)


@router.post("/{task_id}/questions", response_model=TaskQuestionView, status_code=201)
async def create_task_question(
    task_id: str,
    request: TaskQuestionCreateRequest,
) -> TaskQuestionView:
    try:
        question = await task_service.ask_question(
            task_id,
            question=request.question,
            idempotency_key=request.idempotency_key,
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return _question(question)


@router.post("/{task_id}/questions/{question_id}/answer", response_model=TaskSummaryView)
async def answer_task_question(
    task_id: str,
    question_id: str,
    request: TaskQuestionAnswerRequest,
) -> TaskSummaryView:
    try:
        snapshot = await task_service.answer_question(
            task_id,
            question_id,
            answer=request.answer,
            idempotency_key=request.idempotency_key,
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return _summary(snapshot.task, snapshot.queue_position)
