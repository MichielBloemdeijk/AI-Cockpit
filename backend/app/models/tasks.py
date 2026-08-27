"""Typed API contracts for first-class global tasks."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TaskSummaryView(BaseModel):
    id: str
    origin_surface: str
    origin_conversation_id: str | None = None
    origin_turn_id: str | None = None
    goal: str
    status: str
    creation_order: int
    current_attempt_id: str | None = None
    cancellation_requested_at: datetime | None = None
    importance: str
    presentation_policy: str
    terminal_summary: str | None = None
    queue_position: int | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None


class TaskAttemptView(BaseModel):
    id: str
    attempt_number: int
    worker_id: str | None = None
    workspace_path: str | None = None
    checkpoint: dict[str, Any] | None = None
    claimed_at: datetime | None = None
    heartbeat_at: datetime | None = None
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
    event_type: str
    payload: dict[str, Any] | None = None
    causality: dict[str, Any] | None = None
    created_at: datetime


class TaskQuestionView(BaseModel):
    id: str
    asked_attempt_id: str | None = None
    answered_attempt_id: str | None = None
    source_task_event_id: str | None = None
    question: str
    answer: str | None = None
    state: str
    asked_at: datetime
    answered_at: datetime | None = None


class TaskArtifactView(BaseModel):
    id: str
    attempt_id: str | None = None
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
    state: str
    source_task_event_id: str | None = None
    created_at: datetime
    presented_at: datetime | None = None


class WorkerHealthView(BaseModel):
    status: str
    worker_id: str | None = None
    heartbeat_at: datetime | None = None


class TaskDetailView(TaskSummaryView):
    attempts: list[TaskAttemptView] = Field(default_factory=list)
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
    presentation_policy: str = "natural"
    targets: list["TaskTargetRequest"] = Field(default_factory=list)


class TaskTargetRequest(BaseModel):
    resource_type: str = Field(min_length=1, max_length=64)
    resource_key: str = Field(min_length=1, max_length=1024)
    mutable: bool = True


class TaskActionRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=255)


class TaskQuestionCreateRequest(TaskActionRequest):
    question: str = Field(min_length=1)


class TaskQuestionAnswerRequest(TaskActionRequest):
    answer: str = Field(min_length=1)


class TaskEventReplayResponse(BaseModel):
    latest_sequence: int
    events: list[TaskEventView] = Field(default_factory=list)
