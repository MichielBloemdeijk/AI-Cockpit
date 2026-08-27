"""Typed API contracts for the Jarvis daily-log lifecycle."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.models.conversations import ConversationDetail, ConversationSummary, ConversationMessageView


class JarvisStateResponse(BaseModel):
    operational_day: date
    state_revision: int
    current_log: ConversationDetail | None = None


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
