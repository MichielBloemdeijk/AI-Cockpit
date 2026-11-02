"""Pydantic models for conversation-first APIs."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.chat import CouncilResponse
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
    conversation_kind: str = "standard"
    lifecycle_status: str = "open"
    closed_at: datetime | None = None
    jarvis_day_key: str | None = None
    jarvis_day_sequence: int | None = None
    is_current_jarvis: bool = False
    is_live: bool = False
    is_read_only: bool = False
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
    active_run: ActiveRunView | None = None


class ConversationMessageView(BaseModel):
    id: str
    run_id: str | None = None
    source_event_id: str | None = None
    role: str
    author_label: str | None = None
    content: str
    content_format: str
    is_final: bool
    modality: str = "text"
    execution_mode: str = "agent"
    communication_state: str = "completed"
    source_task_id: str | None = None
    source_task_event_id: str | None = None
    presented_in_turn_id: str | None = None
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


class TimelineItemView(BaseModel):
    item_id: str
    run_id: str | None = None
    sequence: int
    role: str
    kind: str
    content: str
    label: str | None = None
    metadata: dict[str, Any] | None = None
    # Committed assistant messages: the reply's token/cost usage and, for a
    # council answer, the per-model responses it was synthesized from.
    usage: dict[str, Any] | None = None
    council: CouncilResponse | None = None
    created_at: datetime


class ActiveRunView(BaseModel):
    run_id: str
    status: str
    latest_sequence: int = 0


class ConversationEventsResponse(BaseModel):
    conversation_id: str
    active_branch_key: str = "main"
    events: list[ConversationEventView] = Field(default_factory=list)
    artifacts: list[ConversationArtifactView] = Field(default_factory=list)
    timeline_items: list[TimelineItemView] = Field(default_factory=list)
    active_run: ActiveRunView | None = None


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
