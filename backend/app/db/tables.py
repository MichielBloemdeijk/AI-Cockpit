"""SQLAlchemy tables for durable conversation persistence."""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid_str() -> str:
    return str(uuid4())


class RunKind(str, Enum):
    assistant = "assistant"
    council = "council"
    tool = "tool"
    task = "task"
    memory_extraction = "memory_extraction"


class RunStatus(str, Enum):
    queued = "queued"
    running = "running"
    paused = "paused"
    completed = "completed"
    failed = "failed"
    interrupted = "interrupted"
    cancelled = "cancelled"


class ActorKind(str, Enum):
    user = "user"
    assistant = "assistant"
    council_model = "council_model"
    synthesizer = "synthesizer"
    system = "system"
    task = "task"


class MessageRole(str, Enum):
    system = "system"
    user = "user"
    assistant = "assistant"


class ContentFormat(str, Enum):
    markdown = "markdown"
    text = "text"
    json = "json"


class MemoryScope(str, Enum):
    preference = "preference"
    note = "note"
    decision = "decision"


class MemoryKind(str, Enum):
    preference = "preference"
    lesson = "lesson"
    decision = "decision"


class MemoryStatus(str, Enum):
    proposed = "proposed"
    approved = "approved"
    rejected = "rejected"
    deleted = "deleted"


class GeneratedAppStatus(str, Enum):
    draft = "draft"
    building = "building"
    ready_for_test = "ready_for_test"
    verified = "verified"
    failed = "failed"


class ConversationKind(str, Enum):
    standard = "standard"
    jarvis_daily = "jarvis_daily"


class ConversationLifecycle(str, Enum):
    open = "open"
    closed = "closed"


class TaskStatus(str, Enum):
    queued = "queued"
    waiting_for_worker = "waiting_for_worker"
    running = "running"
    waiting_for_input = "waiting_for_input"
    waiting_until = "waiting_until"
    paused = "paused"
    completed = "completed"
    failed = "failed"
    interrupted = "interrupted"
    cancelled = "cancelled"


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "(conversation_kind = 'standard' AND jarvis_day_key IS NULL AND jarvis_day_sequence IS NULL) "
            "OR (conversation_kind = 'jarvis_daily' AND jarvis_day_key IS NOT NULL "
            "AND jarvis_day_sequence > 0)",
            name="ck_conversations_kind_day_fields",
        ),
        CheckConstraint(
            "(lifecycle_status = 'open' AND closed_at IS NULL) "
            "OR (lifecycle_status = 'closed' AND closed_at IS NOT NULL)",
            name="ck_conversations_lifecycle_closed_at",
        ),
        UniqueConstraint(
            "jarvis_day_key",
            "jarvis_day_sequence",
            name="uq_conversations_jarvis_day_sequence",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mode_hint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    workspace_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    conversation_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ConversationKind.standard.value, server_default="standard"
    )
    lifecycle_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ConversationLifecycle.open.value, server_default="open"
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    jarvis_day_key: Mapped[date | None] = mapped_column(Date(), nullable=True)
    jarvis_day_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    runs: Mapped[list[ConversationRun]] = relationship(back_populates="conversation")
    messages: Mapped[list[ConversationMessage]] = relationship(back_populates="conversation")
    events: Mapped[list[ConversationEvent]] = relationship(back_populates="conversation")
    artifacts: Mapped[list[ConversationArtifact]] = relationship(back_populates="conversation")
    branches: Mapped[list[ConversationBranch]] = relationship(back_populates="conversation")


class ConversationBranch(Base):
    __tablename__ = "conversation_branches"
    __table_args__ = (
        UniqueConstraint("conversation_id", "branch_key", name="uq_conversation_branches_conversation_branch_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    branch_key: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    parent_branch_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    branched_from_message_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversation_messages.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="branches")


class ConversationRun(Base):
    __tablename__ = "conversation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=RunStatus.running.value)
    parent_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversation_runs.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="runs", foreign_keys=[conversation_id])
    parent_run: Mapped[ConversationRun | None] = relationship(remote_side=[id])
    events: Mapped[list[ConversationEvent]] = relationship(back_populates="run")
    messages: Mapped[list[ConversationMessage]] = relationship(back_populates="run")
    artifacts: Mapped[list[ConversationArtifact]] = relationship(back_populates="run")


class ConversationEvent(Base):
    __tablename__ = "conversation_events"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "idempotency_key",
            name="uq_conversation_events_conversation_idempotency",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversation_runs.id", ondelete="CASCADE"), nullable=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    branch_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parent_event_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversation_events.id", ondelete="SET NULL"), nullable=True
    )
    actor_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="events")
    run: Mapped[ConversationRun | None] = relationship(back_populates="events")


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversation_runs.id", ondelete="CASCADE"), nullable=True
    )
    source_event_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversation_events.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    author_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_format: Mapped[str] = mapped_column(String(32), nullable=False, default=ContentFormat.markdown.value)
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    modality: Mapped[str] = mapped_column(String(16), nullable=False, default="text", server_default="text")
    execution_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="agent", server_default="agent"
    )
    communication_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="completed", server_default="completed"
    )
    source_task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    source_task_event_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("task_events.id", ondelete="SET NULL"), nullable=True
    )
    presented_in_turn_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversation_messages.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    run: Mapped[ConversationRun | None] = relationship(back_populates="messages")


class ConversationArtifact(Base):
    __tablename__ = "conversation_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversation_runs.id", ondelete="CASCADE"), nullable=True
    )
    source_event_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversation_events.id", ondelete="SET NULL"), nullable=True
    )
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="artifacts")
    run: Mapped[ConversationRun | None] = relationship(back_populates="artifacts")


class JarvisState(Base):
    """Singleton pointer to the current open Jarvis daily log."""

    __tablename__ = "jarvis_state"
    __table_args__ = (
        CheckConstraint("id = 'current'", name="ck_jarvis_state_singleton_id"),
        CheckConstraint(
            "(current_log_id IS NULL AND jarvis_day_key IS NULL AND jarvis_day_sequence IS NULL) "
            "OR (current_log_id IS NOT NULL AND jarvis_day_key IS NOT NULL "
            "AND jarvis_day_sequence > 0)",
            name="ck_jarvis_state_pointer_fields",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="current")
    current_log_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="SET NULL"), unique=True, nullable=True
    )
    jarvis_day_key: Mapped[date | None] = mapped_column(Date(), nullable=True)
    jarvis_day_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    situation_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    conversation_summary: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    focused_task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    recent_task_refs_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    live_event_cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class LiveSession(Base):
    __tablename__ = "live_sessions"
    __table_args__ = (
        CheckConstraint("generation > 0", name="ck_live_sessions_generation_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    provider_adapter: Mapped[str] = mapped_column(String(64), nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(32), nullable=False, default="connecting")
    controller_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    activation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    activation_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    workflow_catalog_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_call_id_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    timing_summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    usage_summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    client_time_zone: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_utc_offset_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    client_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class LiveSessionEvent(Base):
    __tablename__ = "live_session_events"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_live_session_events_session_sequence"),
        UniqueConstraint(
            "session_id", "idempotency_key", name="uq_live_session_events_session_idempotency"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("live_sessions.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class JarvisLiveTraceEvent(Base):
    """Short-lived, metadata-only timing events for Live diagnosis."""

    __tablename__ = "jarvis_live_trace_events"
    __table_args__ = (
        Index("ix_jarvis_live_trace_events_trace", "trace_id", "sequence"),
        Index("ix_jarvis_live_trace_events_created", "created_at"),
    )

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    client_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    activation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_response_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_input_item_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    monotonic_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    detail_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SpeechDelivery(Base):
    __tablename__ = "live_speech_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "live_session_id",
            "provider_response_id",
            "provider_item_id",
            name="uq_live_speech_deliveries_provider_item",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    assistant_turn_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversation_messages.id", ondelete="SET NULL"), nullable=True
    )
    live_session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("live_sessions.id", ondelete="CASCADE"), nullable=False
    )
    provider_response_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_item_id: Mapped[str] = mapped_column(String(255), nullable=False)
    generated_transcript: Mapped[str] = mapped_column(Text, nullable=False, default="")
    heard_transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="generating")
    generation_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    playback_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    interrupted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    source_task_event_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("task_events.id", ondelete="SET NULL"), nullable=True
    )
    presentation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("task_presentations.id", ondelete="SET NULL"), nullable=True
    )
    provider_causality_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    timing_summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    usage_summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class MemoryItem(Base):
    __tablename__ = "memory_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=MemoryStatus.proposed.value)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    source_conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    source_event_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversation_events.id", ondelete="SET NULL"), nullable=True
    )
    knowledge_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AppSettings(Base):
    __tablename__ = "app_settings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    chat_defaults_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class GeneratedApp(Base):
    __tablename__ = "generated_apps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    cockpit_app_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("cockpit_apps.id"), nullable=True, unique=True)
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=GeneratedAppStatus.draft.value)
    route_path: Mapped[str] = mapped_column(String(255), nullable=False)
    frontend_root: Mapped[str] = mapped_column(String(512), nullable=False)
    frontend_entry_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    icon_asset_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    cover_asset_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    verification_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_task_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversation_runs.id", ondelete="SET NULL"), nullable=True
    )
    source_conversation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    lease_task_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversation_runs.id", ondelete="SET NULL"), nullable=True
    )
    lease_conversation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    lease_acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    manifest_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    source_task_run: Mapped[ConversationRun | None] = relationship(foreign_keys=[source_task_run_id])
    source_conversation: Mapped[Conversation | None] = relationship(foreign_keys=[source_conversation_id])
    lease_task_run: Mapped[ConversationRun | None] = relationship(foreign_keys=[lease_task_run_id])
    lease_conversation: Mapped[Conversation | None] = relationship(foreign_keys=[lease_conversation_id])


class Task(Base):
    """Global background-work identity, independent from conversations."""

    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'waiting_for_worker', 'running', 'waiting_for_input', 'waiting_until', 'completed', "
            "'paused', 'failed', 'interrupted', 'cancelled')",
            name="ck_tasks_status",
        ),
        UniqueConstraint("idempotency_key", name="uq_tasks_idempotency_key"),
        UniqueConstraint("id", name="uq_tasks_public_id"),
    )

    creation_order: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(36), nullable=False, default=_uuid_str)
    origin_surface: Mapped[str] = mapped_column(String(32), nullable=False)
    origin_conversation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    origin_turn_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    execution_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="agent", server_default="agent"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=TaskStatus.queued.value, server_default="queued"
    )
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    current_attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    current_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    pending_control_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    goal_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    control_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    workspace_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    queue_entered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    importance: Mapped[str] = mapped_column(String(32), nullable=False, default="normal", server_default="normal")
    presentation_policy: Mapped[str] = mapped_column(
        String(32), nullable=False, default="critical_only", server_default="critical_only"
    )
    terminal_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    waiting_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resume_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    wait_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_delivery_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    scheduled_delivery_key: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    scheduled_delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskTurn(Base):
    """Durable ordered user/assistant history for a persistent task session."""

    __tablename__ = "task_turns"
    __table_args__ = (
        UniqueConstraint("task_id", "sequence", name="uq_task_turns_task_sequence"),
        UniqueConstraint("task_id", "request_identity", name="uq_task_turns_request_identity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_reference: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    origin: Mapped[str] = mapped_column(String(64), nullable=False)
    request_identity: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    question_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TaskExecutionRun(Base):
    """One logical execution request within a persistent task session."""

    __tablename__ = "task_execution_runs"
    __table_args__ = (
        UniqueConstraint("task_id", "ordinal", name="uq_task_execution_runs_task_ordinal"),
        CheckConstraint("ordinal > 0", name="ck_task_execution_runs_ordinal_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    input_turn_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("task_turns.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", server_default="queued")
    execution_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="agent", server_default="agent")
    configuration_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    workspace_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    current_attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    context_reference: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    result_reference: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    terminal_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    queue_entered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class RuntimeCredentialLease(Base):
    """A single subscription credential owner; heartbeat expiry never releases it."""
    __tablename__ = "runtime_credential_leases"
    profile_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
    creation_stamp: Mapped[str] = mapped_column(String(64), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class LiveAdmissionIntent(Base):
    """One durable outcome identity across all voice admission routes."""
    __tablename__ = "live_admission_intents"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("live_sessions.id", ondelete="CASCADE"), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    turn_scope: Mapped[str] = mapped_column(String(255), nullable=False)
    intent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    target_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    output_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TaskContextBundle(Base):
    """Immutable compact context pinned once per execution run."""

    __tablename__ = "task_context_bundles"
    __table_args__ = (
        UniqueConstraint("execution_run_id", name="uq_task_context_bundles_run"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    execution_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("task_execution_runs.id", ondelete="CASCADE"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TaskControlCommand(Base):
    """Idempotent, auditable control requested against a particular run."""

    __tablename__ = "task_control_commands"
    __table_args__ = (
        UniqueConstraint("task_id", "idempotency_key", name="uq_task_control_commands_task_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    execution_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("task_execution_runs.id", ondelete="SET NULL"), nullable=True
    )
    attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    requester: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_call_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="requested", server_default="requested")
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class TaskNativeBinding(Base):
    __tablename__ = "task_native_bindings"
    __table_args__ = (
        UniqueConstraint("execution_run_id", "generation", name="uq_task_native_bindings_run_generation"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    execution_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("task_execution_runs.id", ondelete="CASCADE"), nullable=False
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    native_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    profile_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    runtime_version: Mapped[str] = mapped_column(String(64), nullable=False)
    compatibility_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    storage_reference: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    replacement_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TaskOperation(Base):
    """Durable operation journal; uncertain outcomes are never replayable."""

    __tablename__ = "task_operations"
    __table_args__ = (
        UniqueConstraint("task_id", "operation_key", name="uq_task_operations_task_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    execution_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("task_execution_runs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    operation_key: Mapped[str] = mapped_column(String(255), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="started", server_default="started")
    input_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    intent_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_reference: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class TaskSupervisorLease(Base):
    __tablename__ = "task_supervisor_leases"

    lease_key: Mapped[str] = mapped_column(String(64), primary_key=True, default="task-pool")
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    owner_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TaskPoolCommand(Base):
    """Idempotent, audited administrative pool mutation."""

    __tablename__ = "task_pool_commands"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_task_pool_commands_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_worker_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    affected_worker_ids_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    requester: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="requested")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JarvisPreference(Base):
    """Single-user Jarvis preferences kept server-side for device handoff."""

    __tablename__ = "jarvis_preferences"
    __table_args__ = (CheckConstraint("id = 'current'", name="ck_jarvis_preferences_singleton_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="current")
    voice: Mapped[str] = mapped_column(String(32), nullable=False, default="marin", server_default="marin")
    concision: Mapped[str] = mapped_column(String(32), nullable=False, default="concise", server_default="concise")
    wake_behavior: Mapped[str] = mapped_column(String(32), nullable=False, default="follow_up", server_default="follow_up")
    developer_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    android_privacy_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    time_zone: Mapped[str] = mapped_column(
        String(255), nullable=False, default="Europe/Amsterdam", server_default="Europe/Amsterdam"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class JarvisLiveEvent(Base):
    """Unified, resumable event stream consumed by browser and Android clients."""

    __tablename__ = "jarvis_live_events"
    __table_args__ = (
        UniqueConstraint("id", name="uq_jarvis_live_events_id"),
        UniqueConstraint("idempotency_key", name="uq_jarvis_live_events_idempotency"),
    )

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(36), nullable=False, default=_uuid_str)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    source_domain: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_device_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RecallDocument(Base):
    """User-visible text projected into the local FTS recall indexes."""

    __tablename__ = "recall_documents"
    __table_args__ = (
        UniqueConstraint("id", name="uq_recall_documents_id"),
        UniqueConstraint("virtual_path", name="uq_recall_documents_virtual_path"),
    )

    document_order: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(36), nullable=False, default=_uuid_str)
    virtual_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class JarvisDevice(Base):
    __tablename__ = "jarvis_devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    credential_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="android")
    lock_state: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown", server_default="unknown")
    privacy_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    event_cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JarvisPairingCode(Base):
    __tablename__ = "jarvis_pairing_codes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkerInstance(Base):
    __tablename__ = "worker_instances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="starting")
    process_identity: Mapped[str | None] = mapped_column(String(255), nullable=True)
    supervisor_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    assignment_attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RuntimeComponentHealth(Base):
    """Durable liveness and failure state for Jarvis runtime components."""

    __tablename__ = "runtime_component_health"

    component: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="starting")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class TaskAttempt(Base):
    __tablename__ = "task_attempts"
    __table_args__ = (
        UniqueConstraint("task_id", "attempt_number", name="uq_task_attempts_task_number"),
        CheckConstraint("attempt_number > 0", name="ck_task_attempts_number_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    execution_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("task_execution_runs.id", ondelete="CASCADE"), nullable=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("worker_instances.id", ondelete="SET NULL"), nullable=True
    )
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    supervisor_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parent_attempt_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("task_attempts.id", ondelete="SET NULL"), nullable=True
    )
    resume_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    workspace_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    checkpoint_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    checkpoint_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active_operation: Mapped[str | None] = mapped_column(String(255), nullable=True)
    operation_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    operation_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovery_metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_runtime_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    usage_summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TaskEvent(Base):
    """Global ordered task-domain stream.

    SQLite assigns ``sequence`` transactionally from the rowid, so concurrent
    writers cannot duplicate or advertise an event that did not commit.
    """

    __tablename__ = "task_events"
    __table_args__ = (
        UniqueConstraint("id", name="uq_task_events_public_id"),
        UniqueConstraint("idempotency_key", name="uq_task_events_idempotency_key"),
        UniqueConstraint("execution_run_id", "native_item_id", name="uq_task_events_run_native_item"),
    )

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(36), nullable=False, default=_uuid_str)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    attempt_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("task_attempts.id", ondelete="SET NULL"), nullable=True
    )
    execution_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("task_execution_runs.id", ondelete="SET NULL"), nullable=True
    )
    native_item_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    causality_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TaskProjectionCheckpoint(Base):
    """Durable cursor for replayable task-domain projections."""

    __tablename__ = "task_projection_checkpoints"

    projection_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class OrchestrationDecision(Base):
    """Durable issue-specific work; never a second copy of task status."""

    __tablename__ = "orchestration_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    issue_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    execution_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    question_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    trigger_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_event_ids_json: Mapped[list] = mapped_column(JSON, nullable=False)
    source_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claim_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    context_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    context_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    effort: Mapped[str] = mapped_column(String(16), nullable=False)
    auth_profile: Mapped[str] = mapped_column(String(255), nullable=False)
    native_binding_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    outcome_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrchestrationMetric(Base):
    """Metadata-only timings/usage. Inserting a metric cannot wake model work."""

    __tablename__ = "orchestration_metrics"
    __table_args__ = (Index("ix_orchestration_metric_kind_created", "kind", "created_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    metric_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    decision_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("orchestration_decisions.id", ondelete="CASCADE"))
    claim_generation: Mapped[int | None] = mapped_column(Integer)
    task_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("tasks.id", ondelete="CASCADE"))
    source_sequence: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    values_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class OrchestrationAction(Base):
    """An action and its task mutation commit in the same transaction."""

    __tablename__ = "orchestration_actions"
    __table_args__ = (UniqueConstraint("decision_id", "operation_key", name="uq_orchestration_action_operation"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    decision_id: Mapped[str] = mapped_column(String(36), ForeignKey("orchestration_decisions.id", ondelete="CASCADE"), nullable=False)
    operation_key: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class WorkerToolPolicy(Base):
    __tablename__ = "worker_tool_policy"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    configuration_json: Mapped[dict] = mapped_column(JSON, nullable=False)


class ConnectorToolPermission(Base):
    __tablename__ = "connector_tool_permissions"
    identity: Mapped[str] = mapped_column(String(255), primary_key=True)
    connector: Mapped[str] = mapped_column(String(100), nullable=False)
    account: Mapped[str] = mapped_column(String(100), nullable=False)
    tool: Mapped[str] = mapped_column(String(100), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="disabled")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class TaskQuestion(Base):
    __tablename__ = "task_questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    asking_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("task_execution_runs.id", ondelete="SET NULL"), nullable=True
    )
    resolution_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    asked_attempt_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("task_attempts.id", ondelete="SET NULL"), nullable=True
    )
    answered_attempt_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("task_attempts.id", ondelete="SET NULL"), nullable=True
    )
    source_task_event_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("task_events.id", ondelete="SET NULL"), nullable=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    permission_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="asked", server_default="asked")
    asked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskArtifact(Base):
    __tablename__ = "task_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    attempt_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("task_attempts.id", ondelete="SET NULL"), nullable=True
    )
    execution_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("task_execution_runs.id", ondelete="SET NULL"), nullable=True
    )
    source_task_event_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("task_events.id", ondelete="SET NULL"), nullable=True
    )
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TaskCaptureReport(Base):
    """Device-owned, revisioned capture readiness before task admission."""

    __tablename__ = "task_capture_reports"
    __table_args__ = (
        Index("ix_task_capture_reports_activation", "device_id", "activation_id", "capture_mode"),
    )

    capture_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(36), ForeignKey("jarvis_devices.id"), nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("live_sessions.id"), nullable=True)
    generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    activation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    capture_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    callback_monotonic_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class WorkflowRun(Base):
    """Pinned workflow-specific state for one task execution run."""

    __tablename__ = "workflow_runs"
    __table_args__ = (UniqueConstraint("execution_run_id", name="uq_workflow_runs_execution_run"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    execution_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("task_execution_runs.id", ondelete="CASCADE"), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_version: Mapped[int] = mapped_column(Integer, nullable=False)
    workflow_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_inputs_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    stage: Mapped[str] = mapped_column(String(64), nullable=False, default="admitted")
    context_reference: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    result_reference: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class TaskContextBlob(Base):
    """Private content-addressed validated screenshot, never a public URL."""

    __tablename__ = "task_context_blobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    byte_length: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(64), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TaskContextRequest(Base):
    """One exact capture authorized for one task execution run."""

    __tablename__ = "task_context_requests"
    __table_args__ = (UniqueConstraint("execution_run_id", "capture_id", name="uq_task_context_requests_run_capture"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    execution_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("task_execution_runs.id", ondelete="CASCADE"), nullable=False)
    workflow_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False)
    capture_id: Mapped[str] = mapped_column(String(64), ForeignKey("task_capture_reports.capture_id"), nullable=False)
    capture_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    device_id: Mapped[str] = mapped_column(String(36), ForeignKey("jarvis_devices.id"), nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("live_sessions.id"), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    activation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    capture_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    failure_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    blob_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("task_context_blobs.id"), nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("task_artifacts.id"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CockpitApp(Base):
    """Shared identity; generated build/source detail stays in GeneratedApp."""
    __tablename__ = "cockpit_apps"
    __table_args__ = (
        CheckConstraint("kind IN ('collection', 'generated')", name="ck_cockpit_apps_kind"),
        CheckConstraint("storage_bytes >= 0", name="ck_cockpit_apps_storage_bytes"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    route_path: Mapped[str] = mapped_column(String(255), nullable=False)
    runtime: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    storage_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    capability_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ResultPage(Base):
    """Stable maintained-document address allocated with its workflow task."""

    __tablename__ = "result_pages"
    __table_args__ = (UniqueConstraint("public_id", name="uq_result_pages_public_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    cockpit_app_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("cockpit_apps.id"), nullable=True)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    initial_execution_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("task_execution_runs.id"), nullable=False)
    active_execution_run_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("task_execution_runs.id"), nullable=True)
    app_slug: Mapped[str] = mapped_column(String(64), nullable=False, default="documents")
    result_kind: Mapped[str] = mapped_column(String(64), nullable=False, default="document.v1")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="building")
    current_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    first_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    warning: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ResultMetric(Base):
    """Metadata only: never worker prose, arguments, tokens or client content."""
    __tablename__ = "result_metrics"
    __table_args__ = (Index("ix_result_metrics_page", "result_page_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    metric_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    result_page_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("result_pages.id", ondelete="CASCADE"))
    task_id: Mapped[str | None] = mapped_column(String(36))
    execution_run_id: Mapped[str | None] = mapped_column(String(36))
    attempt_id: Mapped[str | None] = mapped_column(String(36))
    revision_number: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    values_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ResultPageRevision(Base):
    __tablename__ = "result_page_revisions"
    __table_args__ = (
        UniqueConstraint("result_page_id", "revision_number", name="uq_result_page_revisions_number"),
        UniqueConstraint("result_page_id", "publication_key", name="uq_result_revision_publication"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    result_page_id: Mapped[str] = mapped_column(String(36), ForeignKey("result_pages.id", ondelete="CASCADE"), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    base_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    base_revision_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_task_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    publication_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    execution_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("task_execution_runs.id"), nullable=False)
    attempt_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("task_attempts.id"), nullable=True)
    artifact_id: Mapped[str] = mapped_column(String(36), ForeignKey("task_artifacts.id"), nullable=False)
    content_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    provenance_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ResultPageEvent(Base):
    __tablename__ = "result_page_events"
    __table_args__ = (
        UniqueConstraint("result_page_id", "sequence", name="uq_result_event_sequence"),
        UniqueConstraint("result_page_id", "event_key", name="uq_result_event_key"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    result_page_id: Mapped[str] = mapped_column(String(36), ForeignKey("result_pages.id", ondelete="CASCADE"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_task_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AppDataRecord(Base):
    __tablename__ = "app_data_records"
    __table_args__ = (
        # Empty scope means app-level. NOT NULL avoids SQLite's NULL uniqueness hole.
        UniqueConstraint("cockpit_app_id", "scope_key", "namespace", "record_key", name="uq_app_data_scope"),
        CheckConstraint("revision > 0 AND size_bytes >= 0", name="ck_app_data_revision_size"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    cockpit_app_id: Mapped[str] = mapped_column(String(36), ForeignKey("cockpit_apps.id"), nullable=False)
    result_page_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("result_pages.id"), nullable=True)
    scope_key: Mapped[str] = mapped_column(String(36), nullable=False, default="", server_default="")
    namespace: Mapped[str] = mapped_column(String(64), nullable=False)
    record_key: Mapped[str] = mapped_column(String(128), nullable=False)
    value_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class TaskTarget(Base):
    __tablename__ = "task_targets"
    __table_args__ = (
        UniqueConstraint("task_id", "resource_type", "resource_key", name="uq_task_targets_resource"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    mutable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ResourceLease(Base):
    __tablename__ = "resource_leases"
    __table_args__ = (
        UniqueConstraint("resource_type", "resource_key", name="uq_resource_leases_resource"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    canonical_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True
    )
    execution_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("task_execution_runs.id", ondelete="CASCADE"), nullable=True
    )
    owner_attempt_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("task_attempts.id", ondelete="CASCADE"), nullable=False
    )
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    supervisor_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    writer_identity: Mapped[str | None] = mapped_column(String(255), nullable=True)
    release_evidence_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    renewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TaskPresentation(Base):
    __tablename__ = "task_presentations"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_task_presentations_idempotency_key"),
        CheckConstraint(
            "state IN ('pending', 'handled', 'superseded')",
            name="ck_task_presentations_state",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    candidate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    coalescing_key: Mapped[str] = mapped_column(String(255), nullable=False)
    importance: Mapped[str] = mapped_column(String(32), nullable=False, default="normal")
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", server_default="pending")
    reserved_session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("live_sessions.id", ondelete="SET NULL"), nullable=True
    )
    reserved_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    required_facts_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_task_event_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("task_events.id", ondelete="SET NULL"), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    defer_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    handled_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    handled_turn_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversation_messages.id", ondelete="SET NULL"), nullable=True
    )
    handled_event_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("jarvis_live_events.id", ondelete="SET NULL"), nullable=True
    )
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    commentary_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_notification_outbox_dedupe_key"),
        CheckConstraint(
            "status IN ('queued', 'sending', 'delivered', 'failed', 'superseded')",
            name="ck_notification_outbox_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    task_event_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("task_events.id", ondelete="CASCADE"), nullable=True
    )
    question_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("task_questions.id", ondelete="CASCADE"), nullable=True
    )
    event_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    deep_link: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    p256dh: Mapped[str] = mapped_column(Text, nullable=False)
    auth: Mapped[str] = mapped_column(Text, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


Index("ix_conversations_updated_at", Conversation.updated_at)
Index("ix_conversation_runs_conversation_started", ConversationRun.conversation_id, ConversationRun.started_at)
Index("ix_conversation_runs_status", ConversationRun.status)
Index("ix_conversation_branches_conversation_created", ConversationBranch.conversation_id, ConversationBranch.created_at)
Index("ix_conversation_events_conversation_created", ConversationEvent.conversation_id, ConversationEvent.created_at)
Index("uq_conversation_events_conversation_sequence", ConversationEvent.conversation_id, ConversationEvent.sequence, unique=True)
Index("ix_conversation_events_run_sequence", ConversationEvent.run_id, ConversationEvent.sequence)
Index("ix_conversation_events_event_type", ConversationEvent.event_type)
Index("ix_conversation_messages_conversation_created", ConversationMessage.conversation_id, ConversationMessage.created_at)
Index("ix_conversation_artifacts_conversation_created", ConversationArtifact.conversation_id, ConversationArtifact.created_at)
Index("ix_memory_items_conversation_created", MemoryItem.source_conversation_id, MemoryItem.created_at)
Index("ix_generated_apps_updated_at", GeneratedApp.updated_at)
Index("ix_generated_apps_status", GeneratedApp.status)
Index("ix_generated_apps_lease_task", GeneratedApp.lease_task_run_id)
Index("ix_conversations_kind_updated", Conversation.conversation_kind, Conversation.updated_at)
Index("ix_conversations_lifecycle", Conversation.lifecycle_status)
Index("ix_live_sessions_conversation_created", LiveSession.conversation_id, LiveSession.created_at)
Index("ix_live_session_events_session_created", LiveSessionEvent.session_id, LiveSessionEvent.created_at)
Index("ix_live_speech_deliveries_session_created", SpeechDelivery.live_session_id, SpeechDelivery.created_at)
Index("ix_live_speech_deliveries_state", SpeechDelivery.state)
Index("ix_tasks_status_order", Task.status, Task.creation_order)
Index("ix_tasks_resume_at", Task.status, Task.resume_at)
Index("ix_tasks_origin_conversation", Task.origin_conversation_id, Task.creation_order)
Index("ix_task_attempts_task_number", TaskAttempt.task_id, TaskAttempt.attempt_number)
Index("ix_task_attempts_worker_heartbeat", TaskAttempt.worker_id, TaskAttempt.heartbeat_at)
Index("ix_task_events_task_sequence", TaskEvent.task_id, TaskEvent.sequence)
Index("ix_task_events_type_sequence", TaskEvent.event_type, TaskEvent.sequence)
Index("ix_task_questions_task_state", TaskQuestion.task_id, TaskQuestion.state)
Index("ix_task_artifacts_task_created", TaskArtifact.task_id, TaskArtifact.created_at)
Index("ix_task_targets_task_resource", TaskTarget.task_id, TaskTarget.resource_type, TaskTarget.resource_key)
Index("ix_resource_leases_owner", ResourceLease.owner_attempt_id)
Index("ix_resource_leases_expiry", ResourceLease.expires_at)
Index("ix_worker_instances_heartbeat", WorkerInstance.heartbeat_at)
Index("ix_task_presentations_state_created", TaskPresentation.state, TaskPresentation.created_at)
Index("ix_task_presentations_task", TaskPresentation.task_id, TaskPresentation.created_at)
Index(
    "uq_task_presentations_session_lease",
    TaskPresentation.reserved_session_id,
    unique=True,
    sqlite_where=text("state = 'pending' AND lease_token IS NOT NULL"),
)
Index(
    "uq_task_presentations_source_task_event",
    TaskPresentation.source_task_event_id,
    unique=True,
    sqlite_where=text("source_task_event_id IS NOT NULL"),
)
Index("ix_notification_outbox_status_created", NotificationOutbox.status, NotificationOutbox.created_at)
Index(
    "uq_notification_outbox_task_event",
    NotificationOutbox.task_event_id,
    unique=True,
)
Index("ix_jarvis_live_events_created", JarvisLiveEvent.created_at)
Index("ix_recall_documents_source", RecallDocument.source_type, RecallDocument.source_id)
Index("ix_recall_documents_timestamp", RecallDocument.source_timestamp)
Index("ix_jarvis_pairing_codes_expiry", JarvisPairingCode.expires_at)
