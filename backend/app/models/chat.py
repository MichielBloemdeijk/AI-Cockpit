"""Pydantic models for chat / LLM council."""
from __future__ import annotations

from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from app.models.settings import ConversationSessionMetadata


class Role(str, Enum):
    system = "system"
    user = "user"
    assistant = "assistant"


class Message(BaseModel):
    role: Role
    content: str


UsageStats = dict[str, Any]


class PromptMetrics(BaseModel):
    provider_family: str
    cache_strategy: str
    explicit_cache_control: bool = False
    cache_ttl: str | None = None
    session_id: str | None = None
    response_format_type: str | None = None
    message_count: int = 0
    system_message_count: int = 0
    user_message_count: int = 0
    assistant_message_count: int = 0
    prompt_chars: int = 0
    prompt_bytes: int = 0
    estimated_prompt_tokens: int = 0
    cached_tokens: int | None = None
    cache_write_tokens: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    prompt_fingerprint: str | None = None
    prompt_prefix_fingerprint: str | None = None
    cache_break_detected: bool = False
    cache_break_reasons: list[str] = Field(default_factory=list)
    cache_cold: bool = False
    time_since_previous_request_seconds: int | None = None
    retry_count: int = 0
    time_based_microcompact_applied: bool = False


class ChatRequest(BaseModel):
    messages: List[Message]
    conversation_id: Optional[str] = None
    branch_key: str = "main"
    session_metadata: Optional[ConversationSessionMetadata] = None
    model: Optional[str] = None  # None → use default / council
    council_mode: bool = False
    stream: bool = True
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1, le=32768)


class ToolCallFunction(BaseModel):
    name: str
    arguments: str


class ToolCall(BaseModel):
    id: str | None = None
    type: str = "function"
    function: ToolCallFunction


class ModelResponse(BaseModel):
    model: str
    content: str
    reasoning: str = ""
    reasoning_details: List[dict[str, Any]] = Field(default_factory=list)
    usage: Optional[UsageStats] = None
    error: Optional[str] = None
    prompt_metrics: Optional[PromptMetrics] = None
    tool_calls: List[ToolCall] = Field(default_factory=list)
    finish_reason: Optional[str] = None


class CouncilResponse(BaseModel):
    conversation_id: Optional[str] = None
    run_id: Optional[str] = None
    model_responses: List[ModelResponse]
    synthesized: str
    synthesizer_model: str
    synthesizer_usage: Optional[UsageStats] = None
    total_usage: Optional[UsageStats] = None


class ChatResponse(BaseModel):
    conversation_id: str
    run_id: str
    model: str
    content: str
    usage: Optional[UsageStats] = None
    error: Optional[str] = None
    prompt_metrics: Optional[PromptMetrics] = None
