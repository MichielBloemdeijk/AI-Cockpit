"""Pydantic models for conversation-first APIs."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.settings import ConversationSessionMetadata


class ConversationSummary(BaseModel):
    id: str
    title: str | None = None
    mode_hint: str | None = None
    session_metadata: ConversationSessionMetadata | None = None
    workspace_path: str | None = None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
    last_message_preview: str | None = None
    latest_run_status: str | None = None


class ConversationBranchView(BaseModel):
    branch_key: str
    label: str | None = None
    parent_branch_key: str | None = None
    branched_from_message_id: str | None = None
    created_at: datetime


class WorkspaceFileView(BaseModel):
    path: str
    size: int
    updated_at: str | None = None


class ConversationWorkspaceView(BaseModel):
    path: str
    files: list[WorkspaceFileView] = Field(default_factory=list)


class ConversationDetail(ConversationSummary):
    messages: list["ConversationMessageView"] = Field(default_factory=list)
    active_branch_key: str = "main"
    branches: list[ConversationBranchView] = Field(default_factory=list)
    workspace: ConversationWorkspaceView | None = None


class ConversationMessageView(BaseModel):
    id: str
    run_id: str | None = None
    source_event_id: str | None = None
    role: str
    author_label: str | None = None
    content: str
    content_format: str
    is_final: bool
    created_at: datetime


class ConversationEventView(BaseModel):
    id: str
    run_id: str | None = None
    sequence: int
    branch_key: str | None = None
    parent_event_id: str | None = None
    actor_kind: str
    event_type: str
    created_at: datetime
    schema_version: int
    payload_json: dict[str, Any] | None = None


class ConversationArtifactView(BaseModel):
    id: str
    run_id: str | None = None
    source_event_id: str | None = None
    artifact_type: str
    mime_type: str
    content_text: str | None = None
    content_json: dict[str, Any] | None = None
    created_at: datetime


class ConversationEventsResponse(BaseModel):
    conversation_id: str
    active_branch_key: str = "main"
    events: list[ConversationEventView] = Field(default_factory=list)
    artifacts: list[ConversationArtifactView] = Field(default_factory=list)


class ConversationCreateRequest(BaseModel):
    title: str | None = None
    mode_hint: str | None = None
    session_metadata: ConversationSessionMetadata | None = None
    initial_message: str | None = None
    council_mode: bool = False
    model: str | None = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1, le=32768)


class ConversationCreateResponse(BaseModel):
    conversation: ConversationSummary
    run_id: str | None = None


class ConversationMessageCreateRequest(BaseModel):
    content: str
    branch_key: str = "main"
    session_metadata: ConversationSessionMetadata | None = None
    council_mode: bool = False
    model: str | None = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1, le=32768)


class ConversationTurnResponse(BaseModel):
    conversation_id: str
    run_id: str
    message: ConversationMessageView
    council_data: dict[str, Any] | None = None


class ConversationToolRequest(BaseModel):
    tool: str
    branch_key: str = "main"
    query: str | None = None
    code: str | None = None
    working_directory: str | None = None
    goal: str | None = None
    title: str | None = None
    app_slug: str | None = None
    description: str | None = None


class ConversationToolResult(BaseModel):
    tool: str
    output: str
    metadata: dict[str, Any] | None = None


class ConversationToolResponse(BaseModel):
    conversation_id: str
    run_id: str
    message: ConversationMessageView
    result: ConversationToolResult


class MemoryProposalInput(BaseModel):
    scope: str
    kind: str
    title: str
    content: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class MemoryExtractionRequest(BaseModel):
    start_message_id: str | None = None
    end_message_id: str | None = None
    proposals: list[MemoryProposalInput] = Field(default_factory=list)


class MemoryItemView(BaseModel):
    id: str
    scope: str
    kind: str
    title: str
    content: str
    status: str
    confidence: float | None = None
    source_conversation_id: str
    source_event_id: str | None = None
    knowledge_path: str | None = None
    created_at: datetime
    reviewed_at: datetime | None = None
    deleted_at: datetime | None = None


class MemoryApprovalResponse(BaseModel):
    memory_item: MemoryItemView
    knowledge_path: str


class ConversationBranchResendRequest(BaseModel):
    source_message_id: str
    content: str
    parent_branch_key: str = "main"
    label: str | None = None


class ConversationBranchResendResponse(BaseModel):
    conversation_id: str
    branch: ConversationBranchView
    run_id: str
    message: ConversationMessageView
    council_data: dict[str, Any] | None = None