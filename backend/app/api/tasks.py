"""HTTP boundary for first-class global tasks and their durable trace."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.config import settings
from app.presenters.tasks import present_task_summary
from app.db.tables import (
    Task,
    TaskArtifact,
    TaskAttempt,
    TaskControlCommand,
    TaskExecutionRun,
    TaskEvent,
    TaskOperation,
    TaskPresentation,
    TaskQuestion,
    TaskTarget,
    TaskTurn,
)
from app.models.tasks import (
    TaskActionRequest,
    TaskArtifactView,
    TaskAttemptView,
    TaskControlRequest,
    TaskControlResponse,
    TaskControlView,
    TaskDetailView,
    TaskDispatchRequest,
    TaskEventReplayResponse,
    TaskEventView,
    TaskExecutionRunView,
    TaskOperationView,
    TaskPresentationView,
    TaskPoolView,
    TaskPoolWorkerView,
    TaskPoolControlRequest,
    TaskPoolControlView,
    TaskQuestionAnswerRequest,
    TaskQuestionCreateRequest,
    TaskQuestionView,
    TaskSummaryView,
    TaskTargetView,
    TaskTurnAdmissionView,
    TaskTurnCreateRequest,
    TaskTurnView,
    WorkerHealthView,
)
from app.services.jarvis_devices import require_task_access
from app.services.task_service import TaskDetailSnapshot, TaskSnapshot, task_service
from app.services.worker_coordinator import worker_coordinator
from app.services.task_pool_supervisor import task_pool_manager
from app.db.session import session_scope
from app.services.task_result_contract import result_links, admission_result_url


router = APIRouter(prefix="/api/tasks", tags=["tasks"], dependencies=[Depends(require_task_access)])



def _event(event: TaskEvent) -> TaskEventView:
    return TaskEventView(
        id=event.id,
        sequence=event.sequence,
        task_id=event.task_id,
        attempt_id=event.attempt_id,
        execution_run_id=event.execution_run_id,
        native_item_id=event.native_item_id,
        event_type=event.event_type,
        payload=event.payload_json,
        causality=event.causality_json,
        created_at=event.created_at,
    )


def _attempt(attempt: TaskAttempt) -> TaskAttemptView:
    return TaskAttemptView(
        id=attempt.id,
        attempt_number=attempt.attempt_number,
        execution_run_id=attempt.execution_run_id,
        supervisor_generation=attempt.supervisor_generation,
        parent_attempt_id=attempt.parent_attempt_id,
        resume_reason=attempt.resume_reason,
        worker_id=attempt.worker_id,
        workspace_path=attempt.workspace_path,
        checkpoint=attempt.checkpoint_json,
        checkpoint_version=attempt.checkpoint_version,
        claimed_at=attempt.claimed_at,
        heartbeat_at=attempt.heartbeat_at,
        last_progress_at=attempt.last_progress_at,
        active_operation=attempt.active_operation,
        operation_started_at=attempt.operation_started_at,
        operation_deadline=attempt.operation_deadline,
        recovery_metadata=attempt.recovery_metadata_json,
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
        asking_run_id=question.asking_run_id,
        resolution_revision=question.resolution_revision,
        asked_attempt_id=question.asked_attempt_id,
        answered_attempt_id=question.answered_attempt_id,
        source_task_event_id=question.source_task_event_id,
        question=question.question,
        permission=question.permission_json,
        answer=question.answer,
        state=question.state,
        asked_at=question.asked_at,
        answered_at=question.answered_at,
    )


def _artifact(artifact: TaskArtifact) -> TaskArtifactView:
    return TaskArtifactView(
        id=artifact.id,
        attempt_id=artifact.attempt_id,
        execution_run_id=artifact.execution_run_id,
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


def _turn(turn: TaskTurn) -> TaskTurnView:
    return TaskTurnView(
        id=turn.id,
        sequence=turn.sequence,
        role=turn.role,
        content=turn.content,
        artifact_reference=turn.artifact_reference,
        origin=turn.origin,
        request_identity=turn.request_identity,
        execution_run_id=turn.execution_run_id,
        question_id=turn.question_id,
        created_at=turn.created_at,
    )


def _run(run: TaskExecutionRun) -> TaskExecutionRunView:
    return TaskExecutionRunView(
        id=run.id,
        ordinal=run.ordinal,
        input_turn_id=run.input_turn_id,
        status=run.status,
        execution_kind=run.execution_kind,
        configuration=run.configuration_json,
        workspace_generation=run.workspace_generation,
        current_attempt_id=run.current_attempt_id,
        context_reference=run.context_reference,
        result_reference=run.result_reference,
        terminal_summary=run.terminal_summary,
        queue_entered_at=run.queue_entered_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _control(command: TaskControlCommand) -> TaskControlView:
    return TaskControlView(
        id=command.id,
        execution_run_id=command.execution_run_id,
        attempt_id=command.attempt_id,
        action=command.action,
        parameters=command.parameters_json,
        requester=command.requester,
        reason=command.reason,
        provider_call_id=command.provider_call_id,
        state=command.state,
        requested_at=command.requested_at,
        confirmed_at=command.confirmed_at,
        rejected_at=command.rejected_at,
        detail=command.detail,
    )


def _operation(operation: TaskOperation) -> TaskOperationView:
    return TaskOperationView(
        id=operation.id,
        execution_run_id=operation.execution_run_id,
        attempt_id=operation.attempt_id,
        operation_key=operation.operation_key,
        operation_type=operation.operation_type,
        state=operation.state,
        input_digest=operation.input_digest,
        result_reference=operation.result_reference,
        result=operation.result_json,
        error=operation.error,
        started_at=operation.started_at,
        completed_at=operation.completed_at,
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
        handled_at=presentation.handled_at,
        handled_by=presentation.handled_by,
        presented_at=presentation.handled_at,
    )


def _detail(snapshot: TaskDetailSnapshot) -> TaskDetailView:
    base = present_task_summary(snapshot.task, snapshot.queue_position)
    return TaskDetailView(
        **base.model_dump(),
        attempts=[_attempt(item) for item in snapshot.attempts],
        turns=[_turn(item) for item in snapshot.turns],
        runs=[_run(item) for item in snapshot.runs],
        controls=[_control(item) for item in snapshot.controls],
        operations=[_operation(item) for item in snapshot.operations],
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
    return [present_task_summary(item.task, item.queue_position) for item in snapshots]


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
            execution_kind=request.execution_kind,
            execution_config=request.execution_config,
            result_request=request.result.model_dump(exclude_none=True) if request.result else None,
            targets=tuple(
                (target.resource_type, target.resource_key, target.mutable)
                for target in request.targets
            ),
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc
    view = present_task_summary(snapshot.task, snapshot.queue_position)
    async with session_scope() as session:
        view.result_url = await admission_result_url(session, snapshot.task.id, request.idempotency_key)
    return view


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


@router.get("/pool", response_model=TaskPoolView)
async def get_task_pool() -> TaskPoolView:
    snapshot = await worker_coordinator.pool_snapshot()
    workers = await worker_coordinator.list_pool_workers()
    return TaskPoolView(
        enabled=settings.task_pool_enabled,
        supervisor_generation=snapshot.supervisor_generation,
        supervisor_heartbeat_at=snapshot.supervisor_heartbeat_at,
        busy=snapshot.busy,
        standby=snapshot.standby,
        starting=snapshot.starting,
        stale=snapshot.stale,
        queue_depth=snapshot.queue_depth,
        waiting_for_worker=snapshot.waiting_for_worker,
        max_busy=settings.worker_max_busy,
        saturated=snapshot.saturated,
        workers=[
            TaskPoolWorkerView(
                id=worker.id,
                state=worker.state,
                assignment_attempt_id=worker.assignment_attempt_id,
                heartbeat_at=worker.heartbeat_at,
            ) for worker in workers
        ],
    )


@router.post("/pool/control", response_model=TaskPoolControlView)
async def control_task_pool(request: TaskPoolControlRequest) -> TaskPoolControlView:
    if not settings.task_pool_enabled:
        raise HTTPException(status_code=409, detail="The supervised task pool is disabled")
    try:
        command = await worker_coordinator.control_pool(
            action=request.action,
            idempotency_key=request.idempotency_key,
            requester="tasks_ui",
            reason=request.reason,
            worker_id=request.worker_id,
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc
    task_pool_manager.repair()
    return TaskPoolControlView(
        id=command.id,
        action=command.action,
        state=command.state,
        affected_worker_ids=list(command.affected_worker_ids_json or []),
        requested_at=command.requested_at,
        confirmed_at=command.confirmed_at,
    )


@router.get("/{task_id}", response_model=TaskDetailView)
async def get_task(task_id: str) -> TaskDetailView:
    snapshot = await task_service.get_task(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Task not found")
    view = _detail(snapshot)
    async with session_scope() as session:
        view.results = await result_links(session, task_id)
    return view


@router.get("/{task_id}/events", response_model=TaskEventReplayResponse)
async def replay_one_task_events(
    task_id: str,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=250, ge=1, le=1000),
) -> TaskEventReplayResponse:
    replay = await task_service.replay_task_events(
        task_id, after_sequence=after, limit=limit,
    )
    if replay is None:
        raise HTTPException(status_code=404, detail="Task not found")
    latest_sequence, events = replay
    return TaskEventReplayResponse(
        latest_sequence=latest_sequence,
        events=[_event(item) for item in events],
    )


@router.post("/{task_id}/turns", response_model=TaskTurnAdmissionView, status_code=201)
async def continue_task(task_id: str, request: TaskTurnCreateRequest) -> TaskTurnAdmissionView:
    try:
        admission = await task_service.continue_task(
            task_id,
            content=request.content,
            idempotency_key=request.idempotency_key,
            origin="tasks_ui",
            execution_kind=request.execution_kind,
            execution_config=request.execution_config,
            result_request=request.result.model_dump(exclude_none=True) if request.result else None,
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc
    async with session_scope() as session:
        result_url = await admission_result_url(session, task_id, request.idempotency_key)
    return TaskTurnAdmissionView(
        task_id=admission.task.id,
        turn_id=admission.turn.id,
        run_id=admission.run.id,
        status=admission.task.status,
        queue_position=admission.queue_position,
        result_reference=admission.run.result_reference,
        result_url=result_url,
    )


@router.post("/{task_id}/control", response_model=TaskControlResponse)
async def control_task(task_id: str, request: TaskControlRequest) -> TaskControlResponse:
    try:
        controlled = await task_service.control(
            task_id,
            action=request.action,
            idempotency_key=request.idempotency_key,
            parameters=request.parameters,
            requester="tasks_ui",
            reason=request.reason,
            expected_run_id=request.expected_run_id,
            expected_control_revision=request.expected_control_revision,
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return TaskControlResponse(
        task=present_task_summary(controlled.task, controlled.queue_position),
        command=_control(controlled.command),
    )


@router.post("/{task_id}/cancel", response_model=TaskSummaryView)
async def cancel_task(task_id: str, request: TaskActionRequest) -> TaskSummaryView:
    try:
        snapshot = await task_service.cancel(task_id, idempotency_key=request.idempotency_key)
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return present_task_summary(snapshot.task, snapshot.queue_position)


@router.post("/{task_id}/retry", response_model=TaskSummaryView)
async def retry_task(task_id: str, request: TaskActionRequest) -> TaskSummaryView:
    try:
        snapshot = await task_service.retry(task_id, idempotency_key=request.idempotency_key)
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return present_task_summary(snapshot.task, snapshot.queue_position)


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
    return present_task_summary(snapshot.task, snapshot.queue_position)
