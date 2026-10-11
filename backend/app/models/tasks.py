"""Typed API contracts for first-class global tasks."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.services.task_update_policy import CRITICAL_ONLY
from app.services.task_result_contract import ResultRequest


TaskUpdatePolicy = Literal["critical_only", "follow_progress", "silent"]
AttentionState = Literal["pending", "handled", "superseded"]


class TaskSummaryView(BaseModel):
    id: str
    origin_surface: str
    origin_conversation_id: str | None = None
    origin_turn_id: str | None = None
    goal: str
    execution_kind: Literal["agent", "workflow"]
    status: str
    status_reason: str | None = None
    creation_order: int
    current_attempt_id: str | None = None
    current_run_id: str | None = None
    pending_control_id: str | None = None
    goal_revision: int
    control_revision: int
    workspace_generation: int
    queue_entered_at: datetime
    cancellation_requested_at: datetime | None = None
    importance: str
    presentation_policy: TaskUpdatePolicy
    terminal_summary: str | None = None
    waiting_until: datetime | None = None
    resume_at: datetime | None = None
    wait_reason: str | None = None
    scheduled_delivery: dict[str, Any] | None = None
    scheduled_delivered_at: datetime | None = None
    queue_position: int | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    result_url: str | None = None


class TaskAttemptView(BaseModel):
    id: str
    attempt_number: int
    execution_run_id: str | None = None
    supervisor_generation: int | None = None
    parent_attempt_id: str | None = None
    resume_reason: str | None = None
    worker_id: str | None = None
    workspace_path: str | None = None
    checkpoint: dict[str, Any] | None = None
    checkpoint_version: int
    claimed_at: datetime | None = None
    heartbeat_at: datetime | None = None
    last_progress_at: datetime | None = None
    active_operation: str | None = None
    operation_started_at: datetime | None = None
    operation_deadline: datetime | None = None
    recovery_metadata: dict[str, Any] | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    terminal_reason: str | None = None
    agent_runtime_reference: str | None = None
    usage_summary: dict[str, Any] | None = None
    created_at: datetime


class TaskEventView(BaseModel):
    id: str
    sequence: int
    task_id: str
    attempt_id: str | None = None
    execution_run_id: str | None = None
    native_item_id: str | None = None
    event_type: str
    payload: dict[str, Any] | None = None
    causality: dict[str, Any] | None = None
    created_at: datetime


class TaskQuestionView(BaseModel):
    id: str
    asking_run_id: str | None = None
    resolution_revision: int | None = None
    asked_attempt_id: str | None = None
    answered_attempt_id: str | None = None
    source_task_event_id: str | None = None
    question: str
    permission: dict | None = None
    answer: str | None = None
    state: str
    asked_at: datetime
    answered_at: datetime | None = None


class TaskArtifactView(BaseModel):
    id: str
    attempt_id: str | None = None
    execution_run_id: str | None = None
    source_task_event_id: str | None = None
    artifact_type: str
    mime_type: str
    title: str | None = None
    uri: str | None = None
    content_text: str | None = None
    content_json: dict[str, Any] | None = None
    created_at: datetime


class TaskTargetView(BaseModel):
    id: str
    resource_type: str
    resource_key: str
    mutable: bool
    discovered_at: datetime


class TaskPresentationView(BaseModel):
    id: str
    candidate_type: str
    coalescing_key: str
    importance: str
    state: AttentionState
    source_task_event_id: str | None = None
    created_at: datetime
    handled_at: datetime | None = None
    handled_by: str | None = None
    # Deprecated compatibility alias; new clients should use handled_at.
    presented_at: datetime | None = None


class WorkerHealthView(BaseModel):
    status: str
    worker_id: str | None = None
    heartbeat_at: datetime | None = None


class TaskPoolWorkerView(BaseModel):
    id: str
    state: str
    assignment_attempt_id: str | None = None
    heartbeat_at: datetime


class TaskPoolView(BaseModel):
    enabled: bool = False
    supervisor_generation: int | None = None
    supervisor_heartbeat_at: datetime | None = None
    busy: int
    standby: int
    starting: int
    stale: int
    queue_depth: int
    waiting_for_worker: int
    max_busy: int
    saturated: bool
    workers: list[TaskPoolWorkerView] = Field(default_factory=list)


class TaskPoolControlRequest(BaseModel):
    action: Literal["repair_capacity", "replace_worker", "restart_pool"]
    idempotency_key: str
    reason: str
    worker_id: str | None = None


class TaskPoolControlView(BaseModel):
    id: str
    action: str
    state: str
    affected_worker_ids: list[str] = Field(default_factory=list)
    requested_at: datetime
    confirmed_at: datetime | None = None


class TaskTurnView(BaseModel):
    id: str
    sequence: int
    role: str
    content: str | None = None
    artifact_reference: str | None = None
    origin: str
    request_identity: str | None = None
    execution_run_id: str | None = None
    question_id: str | None = None
    created_at: datetime


class TaskExecutionRunView(BaseModel):
    id: str
    ordinal: int
    input_turn_id: str
    status: str
    execution_kind: Literal["agent", "workflow"]
    configuration: dict[str, Any] | None = None
    workspace_generation: int
    current_attempt_id: str | None = None
    context_reference: str | None = None
    result_reference: str | None = None
    terminal_summary: str | None = None
    queue_entered_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TaskControlView(BaseModel):
    id: str
    execution_run_id: str | None = None
    attempt_id: str | None = None
    action: str
    parameters: dict[str, Any] | None = None
    requester: str
    reason: str | None = None
    provider_call_id: str | None = None
    state: str
    requested_at: datetime
    confirmed_at: datetime | None = None
    rejected_at: datetime | None = None
    detail: str | None = None


class TaskOperationView(BaseModel):
    id: str
    execution_run_id: str
    attempt_id: str | None = None
    operation_key: str
    operation_type: str
    state: str
    input_digest: str | None = None
    result_reference: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    started_at: datetime
    completed_at: datetime | None = None


class TaskDetailView(TaskSummaryView):
    results: list[dict[str, Any]] = Field(default_factory=list)
    attempts: list[TaskAttemptView] = Field(default_factory=list)
    turns: list[TaskTurnView] = Field(default_factory=list)
    runs: list[TaskExecutionRunView] = Field(default_factory=list)
    controls: list[TaskControlView] = Field(default_factory=list)
    operations: list[TaskOperationView] = Field(default_factory=list)
    events: list[TaskEventView] = Field(default_factory=list)
    questions: list[TaskQuestionView] = Field(default_factory=list)
    artifacts: list[TaskArtifactView] = Field(default_factory=list)
    targets: list[TaskTargetView] = Field(default_factory=list)
    presentations: list[TaskPresentationView] = Field(default_factory=list)
    worker_health: WorkerHealthView


class TaskDispatchRequest(BaseModel):
    goal: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=255)
    origin_surface: str = Field(default="tasks_ui", min_length=1, max_length=32)
    origin_conversation_id: str | None = None
    origin_turn_id: str | None = None
    importance: str = "normal"
    presentation_policy: TaskUpdatePolicy = CRITICAL_ONLY
    execution_kind: Literal["agent", "workflow"] = "agent"
    execution_config: dict[str, Any] = Field(default_factory=dict)
    result: ResultRequest | None = None
    targets: list["TaskTargetRequest"] = Field(default_factory=list)


class TaskTargetRequest(BaseModel):
    resource_type: str = Field(min_length=1, max_length=64)
    resource_key: str = Field(min_length=1, max_length=1024)
    mutable: bool = True


class TaskActionRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=255)


class TaskTurnCreateRequest(TaskActionRequest):
    content: str = Field(min_length=1)
    execution_kind: Literal["agent", "workflow"] | None = None
    execution_config: dict[str, Any] = Field(default_factory=dict)
    result: ResultRequest | None = None


class TaskTurnAdmissionView(BaseModel):
    task_id: str
    turn_id: str
    run_id: str
    status: str
    queue_position: int | None = None
    result_reference: str | None = None
    result_url: str | None = None


class TaskControlRequest(TaskActionRequest):
    action: Literal[
        "set_importance", "yield", "suspend", "resume", "cancel",
        "restart_execution", "retry", "restart_from_beginning",
    ]
    parameters: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None
    expected_run_id: str | None = None
    expected_control_revision: int | None = Field(default=None, ge=0)


class TaskControlResponse(BaseModel):
    task: TaskSummaryView
    command: TaskControlView


class TaskQuestionCreateRequest(TaskActionRequest):
    question: str = Field(min_length=1)


class TaskQuestionAnswerRequest(TaskActionRequest):
    answer: str = Field(min_length=1)


class TaskEventReplayResponse(BaseModel):
    latest_sequence: int
    events: list[TaskEventView] = Field(default_factory=list)
