"""Pydantic models for persisted chat defaults and session metadata."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatToolFlags(BaseModel):
    workspace_search: bool = True
    python_execution: bool = True


class ConversationSessionMetadata(BaseModel):
    mode: Literal["single", "council"] = "single"
    single_model: str = Field(min_length=1)
    council_models: list[str] = Field(default_factory=list, min_length=1)
    synthesizer_model: str = Field(min_length=1)
    tool_flags: ChatToolFlags = Field(default_factory=ChatToolFlags)


class ChatSettingsUpdateRequest(BaseModel):
    defaults: ConversationSessionMetadata
    task_agent_model: str = Field(min_length=1)


class ChatSettingsResponse(BaseModel):
    available_models: list[str] = Field(default_factory=list)
    defaults: ConversationSessionMetadata
    task_agent_model: str = Field(min_length=1)
