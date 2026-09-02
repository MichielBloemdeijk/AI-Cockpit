"""Typed API contracts for the Jarvis daily-log lifecycle."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.conversations import ConversationDetail, ConversationSummary, ConversationMessageView
from app.models.conversations import ConversationEventView
from app.models.tasks import TaskSummaryView, WorkerHealthView


class JarvisDomainCursors(BaseModel):
    conversation: int = 0
    task: int = 0
    session: int = 0


class JarvisSessionView(BaseModel):
    id: str
    conversation_id: str
    generation: int
    provider_adapter: str
    lifecycle_state: str
    controller_id: str | None = None
    floor_state: str
    last_event_sequence: int
    created_at: datetime
    ready_at: datetime | None = None
    disconnected_at: datetime | None = None


class JarvisPendingQuestionView(BaseModel):
    task_id: str
    question_id: str
    question: str
    asked_at: datetime


class JarvisPresentationView(BaseModel):
    id: str
    task_id: str
    candidate_type: str
    importance: str
    state: str
    required_facts: dict[str, Any] | None = None
    created_at: datetime


class JarvisSessionEventView(BaseModel):
    id: str
    session_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any] | None = None
    created_at: datetime


class JarvisConversationEventReplayResponse(BaseModel):
    conversation_id: str
    latest_sequence: int
    events: list[ConversationEventView] = Field(default_factory=list)


class JarvisSessionEventReplayResponse(BaseModel):
    session_id: str
    latest_sequence: int
    events: list[JarvisSessionEventView] = Field(default_factory=list)


class JarvisStateResponse(BaseModel):
    operational_day: date
    state_revision: int
    current_log: ConversationDetail | None = None
    tasks: list[TaskSummaryView] = Field(default_factory=list)
    pending_questions: list[JarvisPendingQuestionView] = Field(default_factory=list)
    pending_presentations: list[JarvisPresentationView] = Field(default_factory=list)
    session: JarvisSessionView | None = None
    worker_health: WorkerHealthView = Field(default_factory=lambda: WorkerHealthView(status="offline"))
    cursors: JarvisDomainCursors = Field(default_factory=JarvisDomainCursors)
    focused_task_id: str | None = None
    recent_task_refs: list[str] = Field(default_factory=list)


class JarvisTextTurnRequest(BaseModel):
    content: str = Field(min_length=1)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)


class JarvisTurnAcceptedResponse(BaseModel):
    conversation: ConversationSummary
    message: ConversationMessageView
    event_sequence: int


class JarvisFreshLogRequest(BaseModel):
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)


class JarvisFreshLogResponse(BaseModel):
    current_log: ConversationSummary


class JarvisRealtimeCallRequest(BaseModel):
    controller_id: str = Field(min_length=1, max_length=255)
    role: Literal["controller", "observer"] = "controller"
    provider_adapter: Literal["fake", "openai"] = "fake"
    sdp_offer: str | None = Field(default=None, min_length=1, max_length=1_000_000)


class JarvisRealtimeCallResponse(BaseModel):
    role: Literal["controller", "observer"]
    session: JarvisSessionView | None = None
    sdp_answer: str | None = None
    rotate_after_seconds: int | None = None


class JarvisFakeProviderEventRequest(BaseModel):
    generation: int = Field(gt=0)
    event_id: str = Field(min_length=1, max_length=255)
    kind: Literal[
        "ready",
        "user_speaking",
        "user_stopped",
        "user_transcript_delta",
        "user_transcript",
        "assistant_generating",
        "assistant_speaking",
        "assistant_transcript_delta",
        "assistant_response",
        "assistant_interrupted",
        "shell_call",
        "disconnected",
        "provider_error",
    ]
    payload: dict[str, Any] = Field(default_factory=dict)


class JarvisPreferencesView(BaseModel):
    voice: Literal["marin", "cedar"] = "marin"
    concision: Literal["concise", "balanced"] = "concise"
    wake_behavior: Literal["manual", "wake_word", "follow_up"] = "follow_up"
    developer_mode: bool = False
    android_privacy: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime | None = None


class JarvisPreferencesPatch(BaseModel):
    voice: Literal["marin", "cedar"] | None = None
    concision: Literal["concise", "balanced"] | None = None
    wake_behavior: Literal["manual", "wake_word", "follow_up"] | None = None
    developer_mode: bool | None = None
    android_privacy: dict[str, Any] | None = None


class JarvisLiveEventView(BaseModel):
    id: str
    sequence: int
    event_type: str
    source_domain: str
    source_id: str | None = None
    source_sequence: int | None = None
    payload: dict[str, Any] | None = None
    created_at: datetime


class ReminderCreateRequest(BaseModel):
    text: str | None = Field(default=None, max_length=2000)
    due_at: datetime
    idempotency_key: str = Field(min_length=1, max_length=255)
    allow_duplicate: bool = False


class ReminderSnoozeRequest(BaseModel):
    due_at: datetime
    idempotency_key: str = Field(min_length=1, max_length=255)


class TaskReferenceRequest(BaseModel):
    reference: str = Field(min_length=1, max_length=500)
    active_only: bool = False


class TaskReferenceCandidate(BaseModel):
    title: str
    status: str
    created_at: datetime


class TaskResolutionView(BaseModel):
    resolved: bool
    ambiguous: bool = False
    internal_task_id: str | None = None
    task: TaskReferenceCandidate | None = None
    candidates: list[TaskReferenceCandidate] = Field(default_factory=list)
    clarification: str | None = None


class PairingCodeView(BaseModel):
    code: str
    expires_at: datetime


class DevicePairRequest(BaseModel):
    code: str = Field(min_length=6, max_length=12)
    label: str = Field(min_length=1, max_length=255)


class DevicePairResponse(BaseModel):
    device_id: str
    credential: str
    event_cursor: int = Field(ge=0)


class DeviceStatePatch(BaseModel):
    lock_state: Literal["locked", "unlocked", "unknown"] | None = None
    event_cursor: int | None = Field(default=None, ge=0)
    privacy: dict[str, Any] | None = None
