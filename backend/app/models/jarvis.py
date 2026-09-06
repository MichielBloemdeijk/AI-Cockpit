"""Typed API contracts for the Jarvis daily-log lifecycle."""
from __future__ import annotations

from datetime import date as DateValue
from datetime import datetime, time as TimeValue
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

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
    client_time_zone: str | None = None
    client_utc_offset_minutes: int | None = None
    client_observed_at: datetime | None = None


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
    operational_day: DateValue
    state_revision: int
    situation_revision: int = 0
    live_event_cursor: int = 0
    current_log: ConversationDetail | None = None
    tasks: list[TaskSummaryView] = Field(default_factory=list)
    pending_questions: list[JarvisPendingQuestionView] = Field(default_factory=list)
    pending_presentations: list[JarvisPresentationView] = Field(default_factory=list)
    session: JarvisSessionView | None = None
    worker_health: WorkerHealthView = Field(default_factory=lambda: WorkerHealthView(status="offline"))
    component_health: dict[str, dict[str, Any]] = Field(default_factory=dict)
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
    client_context: "JarvisClientContext | None" = None
    activation_context: "JarvisRealtimeActivationContext | None" = None


class JarvisRealtimeCallResponse(BaseModel):
    role: Literal["controller", "observer"]
    request_id: str | None = None
    session: JarvisSessionView | None = None
    sdp_answer: str | None = None
    rotate_after_seconds: int | None = None
    server_received_at: datetime
    server_responded_at: datetime


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
    time_zone: str = "Europe/Amsterdam"
    updated_at: datetime | None = None


class JarvisPreferencesPatch(BaseModel):
    voice: Literal["marin", "cedar"] | None = None
    concision: Literal["concise", "balanced"] | None = None
    wake_behavior: Literal["manual", "wake_word", "follow_up"] | None = None
    developer_mode: bool | None = None
    android_privacy: dict[str, Any] | None = None
    time_zone: str | None = Field(default=None, min_length=1, max_length=255)


class JarvisLiveEventView(BaseModel):
    id: str
    sequence: int
    event_type: str
    source_domain: str
    source_id: str | None = None
    source_sequence: int | None = None
    payload: dict[str, Any] | None = None
    created_at: datetime


class JarvisLiveEventReplayResponse(BaseModel):
    latest_sequence: int
    events: list[JarvisLiveEventView] = Field(default_factory=list)


class JarvisClientContext(BaseModel):
    time_zone: str = Field(min_length=1, max_length=255)
    utc_offset_minutes: int = Field(ge=-840, le=840)
    observed_at: datetime


class JarvisRealtimeActivationContext(BaseModel):
    source: Literal["wake_word"]
    wake_score: float = Field(ge=0.0, le=1.0)
    activation_id: str | None = Field(default=None, min_length=1, max_length=64)
    wake_detected_at: datetime | None = None
    local_speech_started_at: datetime | None = None


class JarvisRealtimeTelemetryEvent(BaseModel):
    trace_id: str = Field(min_length=1, max_length=64)
    client_sequence: int | None = Field(default=None, ge=1)
    activation_id: str | None = Field(default=None, min_length=1, max_length=64)
    session_id: str | None = Field(default=None, min_length=1, max_length=36)
    generation: int | None = Field(default=None, gt=0)
    request_id: str | None = Field(default=None, min_length=1, max_length=64)
    provider_response_id: str | None = Field(default=None, min_length=1, max_length=255)
    source: Literal["browser", "android", "backend", "provider"]
    event_type: str = Field(min_length=1, max_length=128)
    observed_at: datetime
    monotonic_ms: float | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    detail: dict[str, Any] | None = None


class JarvisRealtimeTelemetryBatch(BaseModel):
    events: list[JarvisRealtimeTelemetryEvent] = Field(min_length=1, max_length=100)


class JarvisRealtimeTelemetryAccepted(BaseModel):
    accepted: int = Field(ge=0)


class ReminderAfterTiming(BaseModel):
    kind: Literal["after"]
    seconds: int = Field(ge=1, le=31_536_000)


class ReminderLocalTiming(BaseModel):
    kind: Literal["local"]
    date: DateValue | None = None
    time: TimeValue
    time_zone: str | None = Field(default=None, min_length=1, max_length=255)


class ReminderInstantTiming(BaseModel):
    kind: Literal["instant"]
    due_at: datetime


ReminderTiming = Annotated[
    ReminderAfterTiming | ReminderLocalTiming | ReminderInstantTiming,
    Field(discriminator="kind"),
]


class ReminderCreateRequest(BaseModel):
    text: str | None = Field(default=None, max_length=2000)
    timing: ReminderTiming | None = None
    due_at: datetime | None = None
    idempotency_key: str = Field(min_length=1, max_length=255)
    allow_duplicate: bool = False

    @model_validator(mode="after")
    def require_one_timing(self) -> "ReminderCreateRequest":
        if (self.timing is None) == (self.due_at is None):
            raise ValueError("Provide exactly one of timing or the deprecated due_at")
        return self


class ReminderSnoozeRequest(BaseModel):
    timing: ReminderTiming | None = None
    due_at: datetime | None = None
    idempotency_key: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def require_one_timing(self) -> "ReminderSnoozeRequest":
        if (self.timing is None) == (self.due_at is None):
            raise ValueError("Provide exactly one of timing or the deprecated due_at")
        return self


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
