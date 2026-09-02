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
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
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
    running = "running"
    waiting_for_input = "waiting_for_input"
    waiting_until = "waiting_until"
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


class RealtimeSession(Base):
    __tablename__ = "realtime_sessions"
    __table_args__ = (
        CheckConstraint("generation > 0", name="ck_realtime_sessions_generation_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    provider_adapter: Mapped[str] = mapped_column(String(64), nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(32), nullable=False, default="connecting")
    controller_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
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


class VoiceSessionEvent(Base):
    __tablename__ = "voice_session_events"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_voice_session_events_session_sequence"),
        UniqueConstraint(
            "session_id", "idempotency_key", name="uq_voice_session_events_session_idempotency"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("realtime_sessions.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SpeechDelivery(Base):
    __tablename__ = "speech_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "realtime_session_id",
            "provider_response_id",
            "provider_item_id",
            name="uq_speech_deliveries_provider_item",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    assistant_turn_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversation_messages.id", ondelete="SET NULL"), nullable=True
    )
    realtime_session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("realtime_sessions.id", ondelete="CASCADE"), nullable=False
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
            "status IN ('queued', 'running', 'waiting_for_input', 'waiting_until', 'completed', "
            "'failed', 'interrupted', 'cancelled')",
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
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=TaskStatus.queued.value, server_default="queued"
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    current_attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
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
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("worker_instances.id", ondelete="SET NULL"), nullable=True
    )
    workspace_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    checkpoint_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    )

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(36), nullable=False, default=_uuid_str)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    attempt_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("task_attempts.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    causality_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TaskQuestion(Base):
    __tablename__ = "task_questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
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
    owner_attempt_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("task_attempts.id", ondelete="CASCADE"), nullable=False
    )
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
        String(36), ForeignKey("realtime_sessions.id", ondelete="SET NULL"), nullable=True
    )
    reserved_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    required_facts_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_task_event_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("task_events.id", ondelete="SET NULL"), nullable=True
    )
    presented_turn_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversation_messages.id", ondelete="SET NULL"), nullable=True
    )
    presented_delivery_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    defer_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    presented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_notification_outbox_dedupe_key"),
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
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledgement_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
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
Index("ix_realtime_sessions_conversation_created", RealtimeSession.conversation_id, RealtimeSession.created_at)
Index("ix_voice_session_events_session_created", VoiceSessionEvent.session_id, VoiceSessionEvent.created_at)
Index("ix_speech_deliveries_session_created", SpeechDelivery.realtime_session_id, SpeechDelivery.created_at)
Index("ix_speech_deliveries_state", SpeechDelivery.state)
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
