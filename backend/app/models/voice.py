from __future__ import annotations

from pydantic import BaseModel, Field
from datetime import datetime


class VoiceHealthResponse(BaseModel):
    enabled: bool
    wake_word: str
    llm: dict[str, object]
    stt: dict[str, object]
    tts: dict[str, object]


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    voice: str | None = Field(default=None, max_length=64)
    speed: float | None = Field(default=None, ge=0.5, le=2.0)


class VoiceTurnRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    cloud_next_turn: bool = False


class VoiceTurnAccepted(BaseModel):
    conversation_id: str
    run_id: str
    status: str
    accepted_sequence: int
    model: str


class VoiceConversationResponse(BaseModel):
    conversation_id: str
    title: str
    is_primary_voice: bool
    latest_sequence: int


class VoicePendingQuestion(BaseModel):
    run_id: str
    question_id: str
    question: str
    kind: str
    asked_at: str | None = None


class VoiceAnswerRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=8000)


class VoiceRunControlResponse(BaseModel):
    run_id: str
    conversation_id: str
    status: str


class VoiceTaskView(BaseModel):
    run_id: str
    conversation_id: str
    parent_run_id: str | None = None
    title: str
    goal: str
    status: str
    priority: str
    notify_policy: str
    requested_notification: bool
    summary: str | None = None
    error: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class VoiceStateResponse(BaseModel):
    conversation: VoiceConversationResponse
    latest_sequence: int
    tasks: list[VoiceTaskView]
    pending_questions: list[VoicePendingQuestion]
