"""SQLAlchemy tables for durable conversation persistence."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
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
    running = "running"
    completed = "completed"
    failed = "failed"
    interrupted = "interrupted"


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


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mode_hint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    workspace_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
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


Index("ix_conversations_updated_at", Conversation.updated_at)
Index("ix_conversation_runs_conversation_started", ConversationRun.conversation_id, ConversationRun.started_at)
Index("ix_conversation_runs_status", ConversationRun.status)
Index("ix_conversation_branches_conversation_created", ConversationBranch.conversation_id, ConversationBranch.created_at)
Index("ix_conversation_events_conversation_created", ConversationEvent.conversation_id, ConversationEvent.created_at)
Index("ix_conversation_events_run_sequence", ConversationEvent.run_id, ConversationEvent.sequence)
Index("ix_conversation_events_event_type", ConversationEvent.event_type)
Index("ix_conversation_messages_conversation_created", ConversationMessage.conversation_id, ConversationMessage.created_at)
Index("ix_conversation_artifacts_conversation_created", ConversationArtifact.conversation_id, ConversationArtifact.created_at)
Index("ix_memory_items_conversation_created", MemoryItem.source_conversation_id, MemoryItem.created_at)
Index("ix_generated_apps_updated_at", GeneratedApp.updated_at)
Index("ix_generated_apps_status", GeneratedApp.status)
Index("ix_generated_apps_lease_task", GeneratedApp.lease_task_run_id)
