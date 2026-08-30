"""High-level write service for durable conversation persistence."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from uuid import uuid4

from app.db.repositories.conversations import ConversationRepository
from app.db.session import session_scope
from app.db.tables import ActorKind, MemoryStatus, MessageRole, RunStatus
from app.services.conversation_title_service import conversation_title_service
from app.services.conversation_timeline import (
    TIMELINE_ITEM_EVENT_TYPE,
    build_assistant_message_timeline_item,
    build_background_task_timeline_item,
    build_tool_result_timeline_item,
    build_user_message_timeline_item,
)
from app.services.conversation_workspace import (
    MAIN_BRANCH_KEY,
    ensure_conversation_workspace,
    list_workspace_files,
    workspace_relative_path,
)

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class ConversationRecord:
    id: str
    title: str | None
    mode_hint: str | None
    session_metadata_json: dict | None
    workspace_path: str | None
    is_primary_voice: bool
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    conversation_kind: str = "standard"
    lifecycle_status: str = "open"
    closed_at: datetime | None = None
    jarvis_day_key: date | None = None
    jarvis_day_sequence: int | None = None


@dataclass(slots=True)
class BranchRecord:
    id: str
    conversation_id: str
    branch_key: str
    label: str | None
    parent_branch_key: str | None
    branched_from_message_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class RunRecord:
    id: str
    conversation_id: str
    kind: str
    status: str
    parent_run_id: str | None
    started_at: datetime
    finished_at: datetime | None
    error: str | None
    metadata_json: dict | None


@dataclass(slots=True)
class EventRecord:
    id: str
    conversation_id: str
    run_id: str | None
    sequence: int
    branch_key: str | None
    parent_event_id: str | None
    actor_kind: str
    event_type: str
    created_at: datetime
    schema_version: int
    payload_json: dict | None


@dataclass(slots=True)
class MessageRecord:
    id: str
    conversation_id: str
    run_id: str | None
    source_event_id: str | None
    role: str
    author_label: str | None
    content: str
    content_format: str
    is_final: bool
    created_at: datetime
    modality: str = "text"
    execution_mode: str = "agent"
    communication_state: str = "completed"
    source_task_id: str | None = None
    source_task_event_id: str | None = None
    presented_in_turn_id: str | None = None


@dataclass(slots=True)
class ArtifactRecord:
    id: str
    conversation_id: str
    run_id: str | None
    source_event_id: str | None
    artifact_type: str
    mime_type: str
    content_text: str | None
    content_json: dict | None
    created_at: datetime


@dataclass(slots=True)
class AgentToolPersistenceRecord:
    started_event: EventRecord
    completed_event: EventRecord
    artifact: ArtifactRecord | None


@dataclass(slots=True)
class MemoryItemRecord:
    id: str
    scope: str
    kind: str
    title: str
    content: str
    status: str
    confidence: float | None
    source_conversation_id: str
    source_event_id: str | None
    knowledge_path: str | None
    created_at: datetime
    reviewed_at: datetime | None
    deleted_at: datetime | None


@dataclass(slots=True)
class ChatDefaultsRecord:
    id: str
    chat_defaults_json: dict | None
    created_at: datetime
    updated_at: datetime


def _conversation_record(model) -> ConversationRecord:
    return ConversationRecord(
        id=model.id,
        title=model.title,
        mode_hint=model.mode_hint,
        session_metadata_json=model.session_metadata_json,
        workspace_path=model.workspace_path,
        is_primary_voice=bool(model.is_primary_voice),
        conversation_kind=model.conversation_kind,
        lifecycle_status=model.lifecycle_status,
        closed_at=model.closed_at,
        jarvis_day_key=model.jarvis_day_key,
        jarvis_day_sequence=model.jarvis_day_sequence,
        created_at=model.created_at,
        updated_at=model.updated_at,
        archived_at=model.archived_at,
    )


def _branch_record(model) -> BranchRecord:
    return BranchRecord(
        id=model.id,
        conversation_id=model.conversation_id,
        branch_key=model.branch_key,
        label=model.label,
        parent_branch_key=model.parent_branch_key,
        branched_from_message_id=model.branched_from_message_id,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _run_record(model) -> RunRecord:
    return RunRecord(
        id=model.id,
        conversation_id=model.conversation_id,
        kind=model.kind,
        status=model.status,
        parent_run_id=model.parent_run_id,
        started_at=model.started_at,
        finished_at=model.finished_at,
        error=model.error,
        metadata_json=model.metadata_json,
    )


def _event_record(model) -> EventRecord:
    return EventRecord(
        id=model.id,
        conversation_id=model.conversation_id,
        run_id=model.run_id,
        sequence=model.sequence,
        branch_key=model.branch_key,
        parent_event_id=model.parent_event_id,
        actor_kind=model.actor_kind,
        event_type=model.event_type,
        created_at=model.created_at,
        schema_version=model.schema_version,
        payload_json=model.payload_json,
    )


def _message_record(model) -> MessageRecord:
    return MessageRecord(
        id=model.id,
        conversation_id=model.conversation_id,
        run_id=model.run_id,
        source_event_id=model.source_event_id,
        role=model.role,
        author_label=model.author_label,
        content=model.content,
        content_format=model.content_format,
        is_final=model.is_final,
        modality=model.modality,
        execution_mode=model.execution_mode,
        communication_state=model.communication_state,
        source_task_id=model.source_task_id,
        source_task_event_id=model.source_task_event_id,
        presented_in_turn_id=model.presented_in_turn_id,
        created_at=model.created_at,
    )


def _artifact_record(model) -> ArtifactRecord:
    return ArtifactRecord(
        id=model.id,
        conversation_id=model.conversation_id,
        run_id=model.run_id,
        source_event_id=model.source_event_id,
        artifact_type=model.artifact_type,
        mime_type=model.mime_type,
        content_text=model.content_text,
        content_json=model.content_json,
        created_at=model.created_at,
    )


def _memory_item_record(model) -> MemoryItemRecord:
    return MemoryItemRecord(
        id=model.id,
        scope=model.scope,
        kind=model.kind,
        title=model.title,
        content=model.content,
        status=model.status,
        confidence=model.confidence,
        source_conversation_id=model.source_conversation_id,
        source_event_id=model.source_event_id,
        knowledge_path=model.knowledge_path,
        created_at=model.created_at,
        reviewed_at=model.reviewed_at,
        deleted_at=model.deleted_at,
    )


def _chat_defaults_record(model) -> ChatDefaultsRecord:
    return ChatDefaultsRecord(
        id=model.id,
        chat_defaults_json=model.chat_defaults_json,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class ConversationStore:
    """Orchestrates append-only event writes and transcript projections."""

    async def _ensure_conversation_defaults(self, repo: ConversationRepository, conversation) -> bool:
        updated = False
        if not conversation.workspace_path:
            conversation.workspace_path = workspace_relative_path(conversation.id)
            await repo.touch_conversation(conversation.id, workspace_path=conversation.workspace_path)
            ensure_conversation_workspace(conversation.workspace_path)
            updated = True

        branches = await repo.list_branches(conversation.id)
        if not branches:
            await repo.create_branch(
                conversation_id=conversation.id,
                branch_key=MAIN_BRANCH_KEY,
                label="Main",
            )
            updated = True

        if updated:
            await repo.touch_conversation(conversation.id)
            await repo.session.refresh(conversation)

        return updated

    @staticmethod
    def _message_branch_key(message: MessageRecord, event_by_id: dict[str, EventRecord]) -> str:
        if not message.source_event_id:
            return MAIN_BRANCH_KEY
        event = event_by_id.get(message.source_event_id)
        if event is None or not event.branch_key:
            return MAIN_BRANCH_KEY
        return event.branch_key

    @staticmethod
    def _event_branch_key(event: EventRecord) -> str:
        return event.branch_key or MAIN_BRANCH_KEY

    def _build_branch_messages(
        self,
        *,
        branch_key: str,
        messages: list[MessageRecord],
        event_by_id: dict[str, EventRecord],
        branch_by_key: dict[str, BranchRecord],
    ) -> list[MessageRecord]:
        normalized_branch_key = branch_key or MAIN_BRANCH_KEY
        if normalized_branch_key == MAIN_BRANCH_KEY or normalized_branch_key not in branch_by_key:
            return [
                message for message in messages
                if self._message_branch_key(message, event_by_id) == MAIN_BRANCH_KEY
            ]

        branch = branch_by_key[normalized_branch_key]
        parent_messages = self._build_branch_messages(
            branch_key=branch.parent_branch_key or MAIN_BRANCH_KEY,
            messages=messages,
            event_by_id=event_by_id,
            branch_by_key=branch_by_key,
        )
        if branch.branched_from_message_id:
            cutoff = next(
                (index for index, message in enumerate(parent_messages) if message.id == branch.branched_from_message_id),
                len(parent_messages),
            )
            parent_messages = parent_messages[:cutoff]

        branch_messages = [
            message for message in messages
            if self._message_branch_key(message, event_by_id) == normalized_branch_key
        ]
        return parent_messages + branch_messages

    def _build_branch_events(
        self,
        *,
        branch_key: str,
        events: list[EventRecord],
        messages_by_id: dict[str, MessageRecord],
        branch_by_key: dict[str, BranchRecord],
    ) -> list[EventRecord]:
        normalized_branch_key = branch_key or MAIN_BRANCH_KEY
        ordered_events = sorted(events, key=lambda item: (item.sequence, item.created_at, item.id))
        if normalized_branch_key == MAIN_BRANCH_KEY or normalized_branch_key not in branch_by_key:
            return [event for event in ordered_events if self._event_branch_key(event) == MAIN_BRANCH_KEY]

        branch = branch_by_key[normalized_branch_key]
        parent_events = self._build_branch_events(
            branch_key=branch.parent_branch_key or MAIN_BRANCH_KEY,
            events=ordered_events,
            messages_by_id=messages_by_id,
            branch_by_key=branch_by_key,
        )

        if branch.branched_from_message_id:
            branched_message = messages_by_id.get(branch.branched_from_message_id)
            cutoff_sequence = None
            if branched_message and branched_message.source_event_id:
                cutoff_event = next((event for event in ordered_events if event.id == branched_message.source_event_id), None)
                if cutoff_event is not None:
                    cutoff_sequence = cutoff_event.sequence
            if cutoff_sequence is not None:
                parent_events = [event for event in parent_events if event.sequence < cutoff_sequence]

        branch_events = [
            event for event in ordered_events
            if self._event_branch_key(event) == normalized_branch_key
        ]
        return parent_events + branch_events

    def _build_branch_artifacts(
        self,
        *,
        artifacts: list[ArtifactRecord],
        visible_events: list[EventRecord],
        runs_by_id: dict[str, RunRecord],
        branch_key: str,
        branch_by_key: dict[str, BranchRecord],
    ) -> list[ArtifactRecord]:
        visible_event_ids = {event.id for event in visible_events}
        visible_run_ids = {event.run_id for event in visible_events if event.run_id}
        visible_branch_chain = {branch_key or MAIN_BRANCH_KEY}
        current_branch_key = branch_key or MAIN_BRANCH_KEY
        while current_branch_key in branch_by_key:
            parent_branch_key = branch_by_key[current_branch_key].parent_branch_key
            if not parent_branch_key:
                break
            visible_branch_chain.add(parent_branch_key)
            current_branch_key = parent_branch_key

        filtered: list[ArtifactRecord] = []
        for artifact in sorted(artifacts, key=lambda item: (item.created_at, item.id)):
            if artifact.source_event_id:
                if artifact.source_event_id in visible_event_ids:
                    filtered.append(artifact)
                continue
            if not artifact.run_id or artifact.run_id not in visible_run_ids:
                continue
            run = runs_by_id.get(artifact.run_id)
            run_branch_key = MAIN_BRANCH_KEY
            if run and isinstance(run.metadata_json, dict):
                run_branch_key = str(run.metadata_json.get("branch_key") or MAIN_BRANCH_KEY)
            if run_branch_key in visible_branch_chain:
                filtered.append(artifact)
        return filtered

    async def create_conversation(
        self,
        *,
        title: str | None = None,
        mode_hint: str | None = None,
        session_metadata_json: dict | None = None,
        is_primary_voice: bool = False,
        conversation_kind: str = "standard",
        lifecycle_status: str = "open",
        jarvis_day_key: date | None = None,
        jarvis_day_sequence: int | None = None,
    ) -> ConversationRecord:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            conversation = await repo.create_conversation(
                title=title,
                mode_hint=mode_hint,
                session_metadata_json=session_metadata_json,
                is_primary_voice=is_primary_voice,
                conversation_kind=conversation_kind,
                lifecycle_status=lifecycle_status,
                jarvis_day_key=jarvis_day_key,
                jarvis_day_sequence=jarvis_day_sequence,
            )
            relative_workspace_path = workspace_relative_path(conversation.id)
            await repo.touch_conversation(conversation.id, workspace_path=relative_workspace_path)
            await repo.create_branch(
                conversation_id=conversation.id,
                branch_key=MAIN_BRANCH_KEY,
                label="Main",
            )
            conversation.workspace_path = relative_workspace_path
            ensure_conversation_workspace(relative_workspace_path)
            return _conversation_record(conversation)

    async def get_primary_voice_conversation(self) -> ConversationRecord | None:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            conversation = await repo.get_primary_voice_conversation()
            if conversation is None:
                return None
            await self._ensure_conversation_defaults(repo, conversation)
            return _conversation_record(conversation)

    async def set_primary_voice(self, conversation_id: str, value: bool) -> ConversationRecord:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            await repo.set_primary_voice(conversation_id, value)
            conversation = await repo.get_conversation(conversation_id)
            if conversation is None:
                raise ValueError(f"Conversation not found: {conversation_id}")
            return _conversation_record(conversation)

    async def get_conversation(self, conversation_id: str) -> ConversationRecord | None:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            conversation = await repo.get_conversation(conversation_id)
            if conversation is None:
                return None
            if await self._ensure_conversation_defaults(repo, conversation):
                conversation = await repo.get_conversation(conversation_id)
                if conversation is None:
                    return None
            return _conversation_record(conversation)

    async def get_jarvis_history_state(self, conversation_id: str) -> tuple[bool, bool]:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            state = await repo.get_jarvis_state()
            is_current = state is not None and state.current_log_id == conversation_id
            is_live = is_current and await repo.has_live_realtime_session(conversation_id)
            return is_current, is_live

    async def list_conversations(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        include_archived: bool = False,
        archived_only: bool = False,
        conversation_kind: str | None = None,
        lifecycle_status: str | None = None,
    ) -> list[ConversationRecord]:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            conversations = await repo.list_conversations(
                limit=limit,
                offset=offset,
                include_archived=include_archived,
                archived_only=archived_only,
                conversation_kind=conversation_kind,
                lifecycle_status=lifecycle_status,
            )
            records: list[ConversationRecord] = []
            for conversation in conversations:
                if await self._ensure_conversation_defaults(repo, conversation):
                    refreshed_conversation = await repo.get_conversation(conversation.id)
                    if refreshed_conversation is None:
                        continue
                    conversation = refreshed_conversation
                records.append(_conversation_record(conversation))
            return records

    async def archive_conversation(self, conversation_id: str) -> ConversationRecord | None:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            conversation = await repo.get_conversation(conversation_id)
            if conversation is None:
                return None
            await repo.archive_conversation(conversation_id)
            conversation = await repo.get_conversation(conversation_id)
            if conversation is None:
                return None
            return _conversation_record(conversation)

    async def unarchive_conversation(self, conversation_id: str) -> ConversationRecord | None:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            conversation = await repo.get_conversation(conversation_id)
            if conversation is None:
                return None
            await repo.unarchive_conversation(conversation_id)
            conversation = await repo.get_conversation(conversation_id)
            if conversation is None:
                return None
            return _conversation_record(conversation)

    async def get_branch(self, conversation_id: str, branch_key: str) -> BranchRecord | None:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            branch = await repo.get_branch(conversation_id, branch_key)
            if branch is None:
                return None
            return _branch_record(branch)

    async def list_branches(self, conversation_id: str) -> list[BranchRecord]:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            return [_branch_record(item) for item in await repo.list_branches(conversation_id)]

    async def create_branch(
        self,
        conversation_id: str,
        *,
        parent_branch_key: str,
        branched_from_message_id: str,
        label: str | None = None,
    ) -> BranchRecord:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            branch_key = f"branch-{uuid4()}"
            branch = await repo.create_branch(
                conversation_id=conversation_id,
                branch_key=branch_key,
                label=label or "Edited branch",
                parent_branch_key=parent_branch_key,
                branched_from_message_id=branched_from_message_id,
            )
            await repo.touch_conversation(conversation_id)
            return _branch_record(branch)

    async def get_workspace_files(self, conversation_id: str) -> list[dict[str, str | int | None]]:
        conversation = await self.get_conversation(conversation_id)
        if conversation is None or not conversation.workspace_path:
            return []
        return list_workspace_files(conversation.workspace_path)

    async def get_chat_defaults(self) -> ChatDefaultsRecord | None:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            settings_row = await repo.get_app_settings()
            if settings_row is None:
                return None
            return _chat_defaults_record(settings_row)

    async def upsert_chat_defaults(self, chat_defaults_json: dict) -> ChatDefaultsRecord:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            settings_row = await repo.upsert_app_settings(chat_defaults_json=chat_defaults_json)
            return _chat_defaults_record(settings_row)

    async def start_run(
        self,
        conversation_id: str,
        *,
        kind: str,
        status: str = RunStatus.running.value,
        parent_run_id: str | None = None,
        metadata_json: dict | None = None,
    ) -> RunRecord:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            run = await repo.create_run(
                conversation_id=conversation_id,
                kind=kind,
                status=status,
                parent_run_id=parent_run_id,
                metadata_json=metadata_json,
            )
            await repo.touch_conversation(conversation_id)
            return _run_record(run)

    async def get_run(self, run_id: str) -> RunRecord | None:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            run = await repo.get_run(run_id)
            if run is None:
                return None
            return _run_record(run)

    async def get_event(self, event_id: str) -> EventRecord | None:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            event = await repo.get_event(event_id)
            return _event_record(event) if event is not None else None

    async def delete_run(self, run_id: str) -> RunRecord | None:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            run = await repo.get_run(run_id)
            if run is None:
                return None
            record = _run_record(run)
            await repo.delete_run(run_id)
            return record

    async def list_runs(self, conversation_id: str) -> list[RunRecord]:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            return [_run_record(item) for item in await repo.list_runs(conversation_id)]

    async def list_runs_by_kind(self, kind: str) -> list[RunRecord]:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            return [_run_record(item) for item in await repo.list_runs_by_kind(kind)]

    async def update_run_metadata(self, run_id: str, metadata_json: dict | None) -> RunRecord:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            await repo.update_run_metadata(run_id, metadata_json)
            run = await repo.get_run(run_id)
            if run is None:
                raise ValueError(f"Run not found: {run_id}")
            await repo.touch_conversation(run.conversation_id)
            return _run_record(run)

    async def append_user_message(
        self,
        conversation_id: str,
        *,
        run_id: str,
        content: str,
        content_format: str = "markdown",
        author_label: str = "You",
        branch_key: str | None = None,
        parent_event_id: str | None = None,
    ) -> tuple[EventRecord, MessageRecord]:
        should_generate_title = False
        event_record: EventRecord | None = None
        message_record: MessageRecord | None = None
        async with session_scope() as session:
            repo = ConversationRepository(session)
            conversation = await repo.get_conversation(conversation_id)
            should_generate_title = bool(conversation and not conversation.title)
            event = await repo.append_event(
                conversation_id=conversation_id,
                run_id=run_id,
                actor_kind=ActorKind.user.value,
                event_type="conversation.user_message.accepted",
                payload_json={"content": content, "role": MessageRole.user.value},
                branch_key=branch_key,
                parent_event_id=parent_event_id,
            )
            message = await repo.create_message(
                conversation_id=conversation_id,
                run_id=run_id,
                source_event_id=event.id,
                role=MessageRole.user.value,
                author_label=author_label,
                content=content,
                content_format=content_format,
            )
            await repo.append_event(
                conversation_id=conversation_id,
                run_id=run_id,
                actor_kind=ActorKind.user.value,
                event_type=TIMELINE_ITEM_EVENT_TYPE,
                payload_json=build_user_message_timeline_item(message_id=message.id, content=content),
                branch_key=branch_key,
                parent_event_id=event.id,
            )
            await repo.touch_conversation(conversation_id)
            event_record = _event_record(event)
            message_record = _message_record(message)

        if should_generate_title:
            conversation_title_service.schedule_title_generation(conversation_id, content)

        assert event_record is not None
        assert message_record is not None
        return event_record, message_record

    async def answer_paused_run(
        self,
        conversation_id: str,
        *,
        run_id: str,
        answer: str,
        question_id: str | None,
        metadata_json: dict,
        branch_key: str | None = None,
    ) -> RunRecord | None:
        """Persist a question answer and paused->queued transition atomically."""
        async with session_scope() as session:
            repo = ConversationRepository(session)
            run = await repo.get_run(run_id)
            if run is None or run.conversation_id != conversation_id:
                return None
            transitioned = await repo.transition_run_status(
                run_id,
                expected_statuses={RunStatus.paused.value},
                status=RunStatus.queued.value,
                error=None,
                finished_at=None,
            )
            if not transitioned:
                return None
            accepted = await repo.append_event(
                conversation_id=conversation_id,
                run_id=run_id,
                actor_kind=ActorKind.user.value,
                event_type="conversation.user_message.accepted",
                payload_json={"content": answer, "role": MessageRole.user.value},
                branch_key=branch_key,
            )
            message = await repo.create_message(
                conversation_id=conversation_id,
                run_id=run_id,
                source_event_id=accepted.id,
                role=MessageRole.user.value,
                author_label="You",
                content=answer,
            )
            await repo.append_event(
                conversation_id=conversation_id,
                run_id=run_id,
                actor_kind=ActorKind.user.value,
                event_type=TIMELINE_ITEM_EVENT_TYPE,
                payload_json=build_user_message_timeline_item(message_id=message.id, content=answer),
                branch_key=branch_key,
                parent_event_id=accepted.id,
            )
            await repo.append_event(
                conversation_id=conversation_id,
                run_id=run_id,
                actor_kind=ActorKind.user.value,
                event_type="agent.question.answered",
                payload_json={"question_id": question_id, "answer": answer},
                branch_key=branch_key,
            )
            await repo.update_run_metadata(run_id, metadata_json)
            await repo.touch_conversation(conversation_id)
            await session.refresh(run)
            return _run_record(run)

    async def append_event(
        self,
        conversation_id: str,
        *,
        run_id: str | None,
        actor_kind: str,
        event_type: str,
        payload_json: dict | None = None,
        branch_key: str | None = None,
        parent_event_id: str | None = None,
        schema_version: int = 1,
    ) -> EventRecord:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            event = await repo.append_event(
                conversation_id=conversation_id,
                run_id=run_id,
                actor_kind=actor_kind,
                event_type=event_type,
                payload_json=payload_json,
                branch_key=branch_key,
                parent_event_id=parent_event_id,
                schema_version=schema_version,
            )
            await repo.touch_conversation(conversation_id)
            return _event_record(event)

    async def append_timeline_item(
        self,
        conversation_id: str,
        *,
        run_id: str | None,
        item_payload: dict,
        actor_kind: str = ActorKind.assistant.value,
        branch_key: str | None = None,
        parent_event_id: str | None = None,
    ) -> EventRecord:
        return await self.append_event(
            conversation_id,
            run_id=run_id,
            actor_kind=actor_kind,
            event_type=TIMELINE_ITEM_EVENT_TYPE,
            payload_json=item_payload,
            branch_key=branch_key,
            parent_event_id=parent_event_id,
        )

    async def complete_message(
        self,
        conversation_id: str,
        *,
        run_id: str,
        role: str,
        content: str,
        actor_kind: str,
        event_type: str,
        author_label: str | None = None,
        content_format: str = "markdown",
        is_final: bool = True,
        payload_json: dict | None = None,
        branch_key: str | None = None,
        timeline_item_id: str | None = None,
    ) -> tuple[EventRecord, MessageRecord]:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            event = await repo.append_event(
                conversation_id=conversation_id,
                run_id=run_id,
                actor_kind=actor_kind,
                event_type=event_type,
                payload_json=payload_json or {"content": content, "role": role},
                branch_key=branch_key,
            )
            message = await repo.create_message(
                conversation_id=conversation_id,
                run_id=run_id,
                source_event_id=event.id,
                role=role,
                author_label=author_label,
                content=content,
                content_format=content_format,
                is_final=is_final,
            )
            timeline_payload = None
            if role == MessageRole.assistant.value:
                timeline_payload = build_assistant_message_timeline_item(
                    message_id=message.id,
                    content=content,
                    item_id=timeline_item_id,
                )
            elif role == MessageRole.user.value:
                timeline_payload = build_user_message_timeline_item(message_id=message.id, content=content)
            if timeline_payload is not None:
                await repo.append_event(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    actor_kind=actor_kind,
                    event_type=TIMELINE_ITEM_EVENT_TYPE,
                    payload_json=timeline_payload,
                    branch_key=branch_key,
                    parent_event_id=event.id,
                )
            await repo.touch_conversation(conversation_id)
            return _event_record(event), _message_record(message)

    async def append_event_with_assistant_message(
        self,
        conversation_id: str,
        *,
        run_id: str,
        event_type: str,
        content: str,
        payload_json: dict | None = None,
        branch_key: str | None = None,
        importance: str = "normal",
        timeline_item_payload: dict | None = None,
    ) -> tuple[EventRecord, MessageRecord]:
        """Persist a durable task event and its spoken assistant projection together."""
        async with session_scope() as session:
            repo = ConversationRepository(session)
            event_payload = dict(payload_json or {})
            event_payload.setdefault("speech_text", content)
            event_payload.setdefault("importance", importance)
            event_payload.setdefault(
                "voice_delivery",
                {
                    "speak": True,
                    "kind": "task_update",
                    "speech_text": event_payload.get("speech_text") or content,
                    "importance": event_payload.get("importance") or importance,
                },
            )
            event = await repo.append_event(
                conversation_id=conversation_id,
                run_id=run_id,
                actor_kind=ActorKind.assistant.value,
                event_type=event_type,
                payload_json=event_payload,
                branch_key=branch_key,
            )
            message = await repo.create_message(
                conversation_id=conversation_id,
                run_id=run_id,
                source_event_id=event.id,
                role=MessageRole.assistant.value,
                author_label="Jarvis",
                content=content,
                content_format="text",
            )
            await repo.append_event(
                conversation_id=conversation_id,
                run_id=run_id,
                actor_kind=ActorKind.assistant.value,
                event_type=TIMELINE_ITEM_EVENT_TYPE,
                payload_json=timeline_item_payload or build_assistant_message_timeline_item(
                    message_id=message.id,
                    content=content,
                    item_id=f"assistant:{run_id}:{event.sequence}:text",
                ),
                branch_key=branch_key,
                parent_event_id=event.id,
            )
            await repo.touch_conversation(conversation_id)
            return _event_record(event), _message_record(message)

    async def append_voice_acknowledgement(self, conversation_id: str) -> tuple[EventRecord, MessageRecord]:
        """Record the fixed wake-word acknowledgement in the voice transcript.

        The browser synthesizes this response directly after the user's wake
        word.  Deliberately omit ``voice_delivery`` here so replaying the
        durable event cannot make a second "Yes?" play.
        """
        content = "Yes?"
        async with session_scope() as session:
            repo = ConversationRepository(session)
            event = await repo.append_event(
                conversation_id=conversation_id,
                run_id=None,
                actor_kind=ActorKind.assistant.value,
                event_type="voice.acknowledgement",
                payload_json={"content": content, "speech_text": content},
                branch_key=MAIN_BRANCH_KEY,
            )
            message = await repo.create_message(
                conversation_id=conversation_id,
                run_id=None,
                source_event_id=event.id,
                role=MessageRole.assistant.value,
                author_label="Jarvis",
                content=content,
                content_format="text",
            )
            await repo.append_event(
                conversation_id=conversation_id,
                run_id=None,
                actor_kind=ActorKind.assistant.value,
                event_type=TIMELINE_ITEM_EVENT_TYPE,
                payload_json=build_assistant_message_timeline_item(
                    message_id=message.id,
                    content=content,
                    item_id=f"assistant:voice-acknowledgement:{event.sequence}:text",
                ),
                branch_key=MAIN_BRANCH_KEY,
                parent_event_id=event.id,
            )
            await repo.touch_conversation(conversation_id)
            return _event_record(event), _message_record(message)

    async def attach_artifact(
        self,
        conversation_id: str,
        *,
        run_id: str | None,
        artifact_type: str,
        mime_type: str,
        source_event_id: str | None = None,
        content_text: str | None = None,
        content_json: dict | None = None,
    ) -> ArtifactRecord:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            artifact = await repo.create_artifact(
                conversation_id=conversation_id,
                run_id=run_id,
                source_event_id=source_event_id,
                artifact_type=artifact_type,
                mime_type=mime_type,
                content_text=content_text,
                content_json=content_json,
            )
            await repo.touch_conversation(conversation_id)
            return _artifact_record(artifact)

    async def record_agent_tool_result(
        self,
        conversation_id: str,
        *,
        run_id: str,
        step: int,
        tool_name: str,
        arguments: dict,
        ok: bool,
        tool_call_id: str | None = None,
        output: str | None = None,
        error: str | None = None,
        result_metadata: dict | None = None,
        artifact_type: str | None = None,
        artifact_mime_type: str = "text/plain",
        artifact_content_text: str | None = None,
        artifact_content_json: dict | None = None,
        run_metadata_json: dict | None = None,
        branch_key: str | None = None,
        emit_timeline_item: bool = True,
    ) -> AgentToolPersistenceRecord:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            started_payload: dict[str, object] = {"step": step, "tool": tool_name, "arguments": arguments}
            if tool_call_id:
                started_payload["tool_call_id"] = tool_call_id
            started_event = await repo.append_event(
                conversation_id=conversation_id,
                run_id=run_id,
                actor_kind=ActorKind.task.value,
                event_type="agent.tool.called",
                payload_json=started_payload,
                branch_key=branch_key,
            )

            artifact = None
            if artifact_type is not None:
                artifact = await repo.create_artifact(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    source_event_id=started_event.id,
                    artifact_type=artifact_type,
                    mime_type=artifact_mime_type,
                    content_text=artifact_content_text,
                    content_json=artifact_content_json,
                )

            completed_payload: dict[str, object] = {
                "step": step,
                "tool": tool_name,
                "ok": ok,
            }
            if tool_call_id:
                completed_payload["tool_call_id"] = tool_call_id
            if ok:
                completed_payload["output"] = output or ""
                completed_payload["metadata"] = result_metadata
                if artifact is not None:
                    completed_payload["artifact_id"] = artifact.id
            else:
                completed_payload["error"] = error or "Tool failed"

            completed_event = await repo.append_event(
                conversation_id=conversation_id,
                run_id=run_id,
                actor_kind=ActorKind.task.value,
                event_type="agent.tool.completed",
                payload_json=completed_payload,
                branch_key=branch_key,
            )
            if emit_timeline_item:
                await repo.append_event(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    actor_kind=ActorKind.assistant.value,
                    event_type=TIMELINE_ITEM_EVENT_TYPE,
                    payload_json=build_tool_result_timeline_item(
                        run_id=run_id,
                        step=step,
                        tool_name=tool_name,
                        arguments=arguments,
                        ok=ok,
                        output=output,
                        error=error,
                        result_metadata=result_metadata,
                        tool_call_id=tool_call_id,
                    ),
                    branch_key=branch_key,
                    parent_event_id=completed_event.id,
                )
            if run_metadata_json is not None:
                await repo.update_run_metadata(run_id, run_metadata_json)
            await repo.touch_conversation(conversation_id)
            return AgentToolPersistenceRecord(
                started_event=_event_record(started_event),
                completed_event=_event_record(completed_event),
                artifact=_artifact_record(artifact) if artifact is not None else None,
            )

    async def mark_run_completed(self, run_id: str) -> None:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            await repo.update_run_status(run_id, status=RunStatus.completed.value, finished_at=utc_now())

    async def update_run_status(
        self,
        run_id: str,
        *,
        status: str,
        error: str | None = None,
        finished_at: datetime | None = None,
    ) -> RunRecord:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            await repo.update_run_status(run_id, status=status, error=error, finished_at=finished_at)
            run = await repo.get_run(run_id)
            if run is None:
                raise ValueError(f"Run not found: {run_id}")
            await repo.touch_conversation(run.conversation_id)
            return _run_record(run)

    async def claim_run_for_execution(self, run_id: str) -> RunRecord | None:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            claimed = await repo.claim_run_for_execution(run_id)
            run = await repo.get_run(run_id)
            if run is None:
                return None
            if claimed:
                await repo.touch_conversation(run.conversation_id)
            return _run_record(run) if claimed else None

    async def cancel_queued_run(self, run_id: str) -> bool:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            run = await repo.get_run(run_id)
            if run is None:
                return False
            cancelled = await repo.cancel_queued_run(run_id)
            if cancelled:
                await repo.touch_conversation(run.conversation_id)
            return cancelled

    async def transition_run_status(
        self,
        run_id: str,
        *,
        expected_statuses: set[str],
        status: str,
        error: str | None = None,
        finished_at: datetime | None = None,
    ) -> RunRecord | None:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            transitioned = await repo.transition_run_status(
                run_id,
                expected_statuses=expected_statuses,
                status=status,
                error=error,
                finished_at=finished_at,
            )
            run = await repo.get_run(run_id)
            if run is None or not transitioned:
                return None
            await repo.touch_conversation(run.conversation_id)
            return _run_record(run)

    async def complete_agent_run(
        self,
        conversation_id: str,
        *,
        run_id: str,
        metadata_json: dict,
        final_text: str,
        final_summary: str,
        run_summary: dict,
        final_activity_id: str,
        background: bool,
        importance: str = "normal",
    ) -> EventRecord | None:
        """Commit terminal status, result projection, metadata, and summary in one transaction."""
        async with session_scope() as session:
            repo = ConversationRepository(session)
            run = await repo.get_run(run_id)
            if run is None or run.conversation_id != conversation_id:
                return None
            transitioned = await repo.transition_run_status(
                run_id,
                expected_statuses={RunStatus.running.value},
                status=RunStatus.completed.value,
                error=None,
                finished_at=utc_now(),
            )
            if not transitioned:
                return None
            branch_key = str(metadata_json.get("branch_key") or MAIN_BRANCH_KEY)
            if background:
                completion_payload = {
                    "task_run_id": run_id,
                    "title": metadata_json.get("title") or "Background task",
                    "summary": final_text or final_summary,
                    "speech_text": final_text or final_summary,
                    "importance": importance,
                    "notify_policy": metadata_json.get("notify_policy", "important"),
                    "requested_notification": bool(metadata_json.get("requested_notification")),
                    "status": RunStatus.completed.value,
                    "voice_delivery": {
                        "speak": True,
                        "kind": "task_update",
                        "speech_text": final_text or final_summary,
                        "importance": importance,
                    },
                }
                event_type = "task.completed"
                author_label = "Jarvis"
                content_format = "text"
                timeline_item_id = f"assistant:{run_id}:completed"
            else:
                completion_payload = {
                    "content": final_text,
                    "role": MessageRole.assistant.value,
                    "voice_delivery": {
                        "speak": True,
                        "kind": "reply",
                        "speech_text": final_text,
                        "importance": importance,
                    },
                }
                event_type = "conversation.assistant.message.completed"
                author_label = "agent"
                content_format = "markdown"
                timeline_item_id = final_activity_id
            completion_event = await repo.append_event(
                conversation_id=conversation_id,
                run_id=run_id,
                actor_kind=ActorKind.assistant.value,
                event_type=event_type,
                payload_json=completion_payload,
                branch_key=branch_key,
            )
            message = await repo.create_message(
                conversation_id=conversation_id,
                run_id=run_id,
                source_event_id=completion_event.id,
                role=MessageRole.assistant.value,
                author_label=author_label,
                content=final_text,
                content_format=content_format,
            )
            task_timeline_payload = build_background_task_timeline_item(
                run_id=run_id,
                task_id=str(metadata_json.get("global_task_id") or "").strip() or None,
                title=str(metadata_json.get("title") or "Background task"),
                goal=str(metadata_json.get("goal") or ""),
                status=RunStatus.completed.value,
                summary=final_text or final_summary,
                active_step="Completed",
                updates=[*list(metadata_json.get("task_updates") or []), final_text or final_summary],
                notify_policy=str(metadata_json.get("notify_policy") or "important"),
                requested_notification=bool(metadata_json.get("requested_notification")),
            ) if background else None
            await repo.append_event(
                conversation_id=conversation_id,
                run_id=run_id,
                actor_kind=ActorKind.assistant.value,
                event_type=TIMELINE_ITEM_EVENT_TYPE,
                payload_json=task_timeline_payload or build_assistant_message_timeline_item(
                    message_id=message.id,
                    content=final_text,
                    item_id=timeline_item_id,
                ),
                branch_key=branch_key,
                parent_event_id=completion_event.id,
            )
            await repo.append_event(
                conversation_id=conversation_id,
                run_id=run_id,
                actor_kind=ActorKind.assistant.value,
                event_type="agent.run.completed",
                payload_json={"summary": final_summary, "run_summary": run_summary},
                branch_key=branch_key,
            )
            await repo.create_artifact(
                conversation_id=conversation_id,
                run_id=run_id,
                source_event_id=completion_event.id,
                artifact_type="agent.run.summary",
                mime_type="application/json",
                content_json=run_summary,
            )
            await repo.update_run_metadata(run_id, metadata_json)
            await repo.touch_conversation(conversation_id)
            return _event_record(completion_event)

    async def cancel_run_with_projection(
        self,
        run_id: str,
        *,
        summary: str = "The task was cancelled.",
    ) -> tuple[RunRecord, EventRecord | None] | None:
        """Atomically cancel an active run and persist its observable projection."""
        async with session_scope() as session:
            repo = ConversationRepository(session)
            run = await repo.get_run(run_id)
            if run is None:
                return None
            transitioned = await repo.transition_run_status(
                run_id,
                expected_statuses={RunStatus.queued.value, RunStatus.running.value, RunStatus.paused.value},
                status=RunStatus.cancelled.value,
                error="Task cancelled by user",
                finished_at=utc_now(),
            )
            if not transitioned:
                return None
            metadata = run.metadata_json if isinstance(run.metadata_json, dict) else {}
            branch_key = str(metadata.get("branch_key") or MAIN_BRANCH_KEY)
            execution_mode = metadata.get("execution_mode")
            projected_event = None
            if execution_mode == "background":
                content = "The background task was cancelled."
                payload = {
                    "task_run_id": run_id,
                    "title": metadata.get("title") or "Background task",
                    "summary": content,
                    "speech_text": content,
                    "importance": "normal",
                    "notify_policy": metadata.get("notify_policy", "important"),
                    "requested_notification": bool(metadata.get("requested_notification")),
                    "status": RunStatus.cancelled.value,
                    "voice_delivery": {
                        "speak": True,
                        "kind": "task_update",
                        "speech_text": content,
                        "importance": "normal",
                    },
                }
                projected_event = await repo.append_event(
                    conversation_id=run.conversation_id,
                    run_id=run_id,
                    actor_kind=ActorKind.assistant.value,
                    event_type="task.cancelled",
                    payload_json=payload,
                    branch_key=branch_key,
                )
                message = await repo.create_message(
                    conversation_id=run.conversation_id,
                    run_id=run_id,
                    source_event_id=projected_event.id,
                    role=MessageRole.assistant.value,
                    author_label="Jarvis",
                    content=content,
                    content_format="text",
                )
                await repo.append_event(
                    conversation_id=run.conversation_id,
                    run_id=run_id,
                    actor_kind=ActorKind.assistant.value,
                    event_type=TIMELINE_ITEM_EVENT_TYPE,
                    payload_json=build_background_task_timeline_item(
                        run_id=run_id,
                        task_id=str(metadata.get("global_task_id") or "").strip() or None,
                        title=str(metadata.get("title") or "Background task"),
                        goal=str(metadata.get("goal") or ""),
                        status=RunStatus.cancelled.value,
                        summary=content,
                        active_step="Cancelled",
                        updates=[*list(metadata.get("task_updates") or []), content],
                        notify_policy=str(metadata.get("notify_policy") or "important"),
                        requested_notification=bool(metadata.get("requested_notification")),
                    ),
                    branch_key=branch_key,
                    parent_event_id=projected_event.id,
                )
            elif execution_mode == "voice_interactive" or metadata.get("voice_response"):
                payload = {
                    "run_id": run_id,
                    "status": RunStatus.cancelled.value,
                    "summary": summary,
                    "speech_text": summary,
                    "importance": "normal",
                    "voice_delivery": {
                        "speak": True,
                        "kind": "task_update",
                        "speech_text": summary,
                        "importance": "normal",
                    },
                }
                projected_event = await repo.append_event(
                    conversation_id=run.conversation_id,
                    run_id=run_id,
                    actor_kind=ActorKind.assistant.value,
                    event_type="run.cancelled",
                    payload_json=payload,
                    branch_key=branch_key,
                )
                message = await repo.create_message(
                    conversation_id=run.conversation_id,
                    run_id=run_id,
                    source_event_id=projected_event.id,
                    role=MessageRole.assistant.value,
                    author_label="Jarvis",
                    content=summary,
                    content_format="text",
                )
                await repo.append_event(
                    conversation_id=run.conversation_id,
                    run_id=run_id,
                    actor_kind=ActorKind.assistant.value,
                    event_type=TIMELINE_ITEM_EVENT_TYPE,
                    payload_json=build_assistant_message_timeline_item(
                        message_id=message.id,
                        content=summary,
                        item_id=f"assistant:{run_id}:cancelled",
                    ),
                    branch_key=branch_key,
                    parent_event_id=projected_event.id,
                )
            else:
                projected_event = await repo.append_event(
                    conversation_id=run.conversation_id,
                    run_id=run_id,
                    actor_kind=ActorKind.system.value,
                    event_type="run.cancelled",
                    payload_json={"run_id": run_id, "status": RunStatus.cancelled.value, "summary": summary},
                    branch_key=branch_key,
                )
            await repo.touch_conversation(run.conversation_id)
            await session.refresh(run)
            return _run_record(run), _event_record(projected_event) if projected_event is not None else None

    async def fail_agent_run(
        self,
        conversation_id: str,
        *,
        run_id: str,
        metadata_json: dict,
        error: str,
        voice_failure_text: str,
    ) -> EventRecord | None:
        """Commit failure status, observable failure, and metadata atomically."""
        async with session_scope() as session:
            repo = ConversationRepository(session)
            run = await repo.get_run(run_id)
            if run is None or run.conversation_id != conversation_id:
                return None
            transitioned = await repo.transition_run_status(
                run_id,
                expected_statuses={RunStatus.queued.value, RunStatus.running.value, RunStatus.paused.value},
                status=RunStatus.failed.value,
                error=error,
                finished_at=utc_now(),
            )
            if not transitioned:
                return None
            branch_key = str(metadata_json.get("branch_key") or MAIN_BRANCH_KEY)
            execution_mode = metadata_json.get("execution_mode")
            if execution_mode == "background":
                content = "The background task failed: " + error
                primary_event = await repo.append_event(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    actor_kind=ActorKind.assistant.value,
                    event_type="task.failed",
                    payload_json={
                        "task_run_id": run_id,
                        "title": metadata_json.get("title") or "Background task",
                        "summary": content,
                        "speech_text": content,
                        "importance": "urgent",
                        "notify_policy": metadata_json.get("notify_policy", "important"),
                        "requested_notification": bool(metadata_json.get("requested_notification")),
                        "status": RunStatus.failed.value,
                        "voice_delivery": {
                            "speak": True,
                            "kind": "task_update",
                            "speech_text": content,
                            "importance": "urgent",
                        },
                    },
                    branch_key=branch_key,
                )
                message = await repo.create_message(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    source_event_id=primary_event.id,
                    role=MessageRole.assistant.value,
                    author_label="Jarvis",
                    content=content,
                    content_format="text",
                )
                await repo.append_event(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    actor_kind=ActorKind.assistant.value,
                    event_type=TIMELINE_ITEM_EVENT_TYPE,
                    payload_json=build_background_task_timeline_item(
                        run_id=run_id,
                        task_id=str(metadata_json.get("global_task_id") or "").strip() or None,
                        title=str(metadata_json.get("title") or "Background task"),
                        goal=str(metadata_json.get("goal") or ""),
                        status=RunStatus.failed.value,
                        summary=content,
                        active_step="Failed",
                        updates=[*list(metadata_json.get("task_updates") or []), content],
                        notify_policy=str(metadata_json.get("notify_policy") or "important"),
                        requested_notification=bool(metadata_json.get("requested_notification")),
                    ),
                    branch_key=branch_key,
                    parent_event_id=primary_event.id,
                )
            elif metadata_json.get("voice_response"):
                primary_event = await repo.append_event(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    actor_kind=ActorKind.assistant.value,
                    event_type="conversation.assistant.message.completed",
                    payload_json={
                        "content": voice_failure_text,
                        "error": error,
                        "voice_delivery": {
                            "speak": True,
                            "kind": "reply",
                            "speech_text": voice_failure_text,
                            "importance": "urgent",
                        },
                    },
                    branch_key=branch_key,
                )
                message = await repo.create_message(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    source_event_id=primary_event.id,
                    role=MessageRole.assistant.value,
                    author_label="agent",
                    content=voice_failure_text,
                    content_format="text",
                )
                await repo.append_event(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    actor_kind=ActorKind.assistant.value,
                    event_type=TIMELINE_ITEM_EVENT_TYPE,
                    payload_json=build_assistant_message_timeline_item(
                        message_id=message.id,
                        content=voice_failure_text,
                        item_id=f"assistant:{run_id}:failed",
                    ),
                    branch_key=branch_key,
                    parent_event_id=primary_event.id,
                )
                await repo.append_event(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    actor_kind=ActorKind.system.value,
                    event_type="agent.run.failed",
                    payload_json={"error": error},
                    branch_key=branch_key,
                )
            else:
                primary_event = await repo.append_event(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    actor_kind=ActorKind.system.value,
                    event_type="agent.run.failed",
                    payload_json={"error": error},
                    branch_key=branch_key,
                )
            await repo.append_event(
                conversation_id=conversation_id,
                run_id=run_id,
                actor_kind=ActorKind.system.value,
                event_type="run.failed",
                payload_json={"run_id": run_id, "error": error},
                branch_key=branch_key,
            )
            await repo.update_run_metadata(run_id, metadata_json)
            await repo.touch_conversation(conversation_id)
            return _event_record(primary_event)

    async def mark_run_interrupted(self, run_id: str) -> EventRecord:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            run = await repo.get_run(run_id)
            if run is None:
                raise ValueError(f"Run not found: {run_id}")
            await repo.update_run_status(run_id, status=RunStatus.interrupted.value, finished_at=utc_now())
            event = await repo.append_event(
                conversation_id=run.conversation_id,
                run_id=run.id,
                actor_kind=ActorKind.system.value,
                event_type="run.interrupted",
                payload_json={"run_id": run.id},
            )
            await repo.touch_conversation(run.conversation_id)
            return _event_record(event)

    async def mark_run_failed(self, run_id: str, error: str) -> EventRecord:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            run = await repo.get_run(run_id)
            if run is None:
                raise ValueError(f"Run not found: {run_id}")
            await repo.update_run_status(
                run_id,
                status=RunStatus.failed.value,
                error=error,
                finished_at=utc_now(),
            )
            event = await repo.append_event(
                conversation_id=run.conversation_id,
                run_id=run.id,
                actor_kind=ActorKind.system.value,
                event_type="run.failed",
                payload_json={"run_id": run.id, "error": error},
            )
            await repo.touch_conversation(run.conversation_id)
            return _event_record(event)

    async def mark_running_runs_interrupted(self) -> int:
        interruption_summary_event_ids: list[str] = []
        interrupted_global_tasks: list[tuple[str, str]] = []
        async with session_scope() as session:
            repo = ConversationRepository(session)
            runs = list(await repo.list_unfinished_runs())
            interrupted_count = 0
            interrupted_background_by_conversation: dict[str, dict[str, object]] = {}
            for run in runs:
                metadata = run.metadata_json if isinstance(run.metadata_json, dict) else {}
                branch_key = str(metadata.get("branch_key") or MAIN_BRANCH_KEY)
                is_background = metadata.get("execution_mode") == "background"
                global_task_id = str(metadata.get("global_task_id") or "").strip()
                # First-class tasks are owned by the separate worker. An API
                # restart must not alter either their task state or their
                # compatibility child-run projection.
                if is_background and global_task_id:
                    continue
                interrupted_count += 1
                if is_background and global_task_id:
                    interrupted_global_tasks.append((global_task_id, run.id))
                await repo.update_run_status(
                    run.id,
                    status=RunStatus.interrupted.value,
                    error="AI-Cockpit restarted before this run completed",
                    finished_at=utc_now(),
                )
                event = await repo.append_event(
                    conversation_id=run.conversation_id,
                    run_id=run.id,
                    actor_kind=ActorKind.system.value,
                    event_type="task.interrupted" if metadata.get("execution_mode") == "background" else "run.interrupted",
                    payload_json={
                        "run_id": run.id,
                        "task_run_id": run.id,
                        "task_id": global_task_id or None,
                        "title": metadata.get("title") or "Background task",
                        "goal": metadata.get("goal") or "",
                        "status": RunStatus.interrupted.value,
                        "summary": "AI-Cockpit restarted before this run completed.",
                        "speech_text": "AI-Cockpit restarted before this task completed.",
                        "importance": "urgent",
                        "notify_policy": metadata.get("notify_policy", "important"),
                        "requested_notification": bool(metadata.get("requested_notification")),
                        "voice_delivery": {
                            "speak": False,
                            "kind": "task_update",
                            "speech_text": "AI-Cockpit restarted before this task completed.",
                            "importance": "urgent",
                        },
                    },
                    branch_key=branch_key,
                )
                if is_background:
                    group = interrupted_background_by_conversation.setdefault(
                        run.conversation_id,
                        {"count": 0, "branch_key": branch_key},
                    )
                    group["count"] = int(group["count"]) + 1
                    await repo.append_event(
                        conversation_id=run.conversation_id,
                        run_id=run.id,
                        actor_kind=ActorKind.assistant.value,
                        event_type=TIMELINE_ITEM_EVENT_TYPE,
                        payload_json=build_background_task_timeline_item(
                            run_id=run.id,
                            task_id=str(metadata.get("global_task_id") or "").strip() or None,
                            title=str(metadata.get("title") or "Background task"),
                            goal=str(metadata.get("goal") or ""),
                            status=RunStatus.interrupted.value,
                            summary="AI-Cockpit restarted before this task completed.",
                            active_step="Interrupted",
                            updates=[
                                *list(metadata.get("task_updates") or []),
                                "AI-Cockpit restarted before this task completed.",
                            ],
                            notify_policy=str(metadata.get("notify_policy") or "important"),
                            requested_notification=bool(metadata.get("requested_notification")),
                        ),
                        branch_key=branch_key,
                        parent_event_id=event.id,
                    )
            for conversation_id, group in interrupted_background_by_conversation.items():
                count = int(group["count"])
                content = (
                    "AI-Cockpit restarted before one background task completed."
                    if count == 1
                    else f"AI-Cockpit restarted before {count} background tasks completed."
                )
                branch_key = str(group["branch_key"] or MAIN_BRANCH_KEY)
                summary_event = await repo.append_event(
                    conversation_id=conversation_id,
                    run_id=None,
                    actor_kind=ActorKind.assistant.value,
                    event_type="task.interruptions_summarized",
                    payload_json={
                        "count": count,
                        "title": "Interrupted background work",
                        "summary": content,
                        "speech_text": content,
                        "importance": "urgent",
                        "notify_policy": "important",
                        "requested_notification": True,
                        "voice_delivery": {
                            "speak": True,
                            "kind": "task_update",
                            "speech_text": content,
                            "importance": "urgent",
                        },
                    },
                    branch_key=branch_key,
                )
                message = await repo.create_message(
                    conversation_id=conversation_id,
                    run_id=None,
                    source_event_id=summary_event.id,
                    role=MessageRole.assistant.value,
                    author_label="Jarvis",
                    content=content,
                    content_format="text",
                )
                await repo.append_event(
                    conversation_id=conversation_id,
                    run_id=None,
                    actor_kind=ActorKind.assistant.value,
                    event_type=TIMELINE_ITEM_EVENT_TYPE,
                    payload_json=build_assistant_message_timeline_item(
                        message_id=message.id,
                        content=content,
                    ),
                    branch_key=branch_key,
                    parent_event_id=summary_event.id,
                )
                interruption_summary_event_ids.append(summary_event.id)
        if interruption_summary_event_ids:
            from app.services.notifications import notify_event

            for event_id in interruption_summary_event_ids:
                await notify_event(event_id)
        if interrupted_global_tasks:
            from app.services.task_service import task_service

            for task_id, run_id in interrupted_global_tasks:
                await task_service.complete(
                    task_id,
                    status="interrupted",
                    summary="AI-Cockpit restarted before this task completed.",
                    idempotency_key=f"legacy-restart-interrupted:{run_id}",
                )
        return interrupted_count

    async def get_latest_run(self, conversation_id: str) -> RunRecord | None:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            run = await repo.get_latest_run(conversation_id)
            if run is None:
                return None
            return _run_record(run)

    async def get_last_message(self, conversation_id: str) -> MessageRecord | None:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            message = await repo.get_last_message(conversation_id)
            if message is None:
                return None
            return _message_record(message)

    async def get_message(self, message_id: str) -> MessageRecord | None:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            message = await repo.get_message(message_id)
            if message is None:
                return None
            return _message_record(message)

    async def list_messages(self, conversation_id: str, *, final_only: bool = False) -> list[MessageRecord]:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            return [_message_record(item) for item in await repo.list_messages(conversation_id, final_only=final_only)]

    async def list_messages_for_branch(
        self,
        conversation_id: str,
        *,
        branch_key: str = MAIN_BRANCH_KEY,
        final_only: bool = False,
    ) -> list[MessageRecord]:
        messages = await self.list_messages(conversation_id, final_only=final_only)
        events = await self.list_events(conversation_id, limit=5000)
        branches = await self.list_branches(conversation_id)
        event_by_id = {event.id: event for event in events}
        branch_by_key = {branch.branch_key: branch for branch in branches}
        return self._build_branch_messages(
            branch_key=branch_key,
            messages=messages,
            event_by_id=event_by_id,
            branch_by_key=branch_by_key,
        )

    async def list_events(self, conversation_id: str, *, limit: int = 500) -> list[EventRecord]:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            return [_event_record(item) for item in await repo.list_events(conversation_id, limit=limit)]

    async def get_latest_event_sequence(self, conversation_id: str) -> int:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            return await repo.get_latest_event_sequence(conversation_id)

    async def list_events_after(
        self,
        conversation_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 250,
    ) -> list[EventRecord]:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            return [
                _event_record(item)
                for item in await repo.list_events_after(
                    conversation_id,
                    after_sequence=after_sequence,
                    limit=limit,
                )
            ]

    async def list_events_for_run(self, conversation_id: str, run_id: str, *, limit: int = 5000) -> list[EventRecord]:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            return [_event_record(item) for item in await repo.list_events_for_run(conversation_id, run_id, limit=limit)]

    async def list_artifacts(self, conversation_id: str) -> list[ArtifactRecord]:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            return [_artifact_record(item) for item in await repo.list_artifacts(conversation_id)]

    async def list_artifacts_for_run(self, conversation_id: str, run_id: str) -> list[ArtifactRecord]:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            return [_artifact_record(item) for item in await repo.list_artifacts_for_run(conversation_id, run_id)]

    async def list_events_for_branch(
        self,
        conversation_id: str,
        *,
        branch_key: str = MAIN_BRANCH_KEY,
        limit: int = 5000,
    ) -> list[EventRecord]:
        events = await self.list_events(conversation_id, limit=limit)
        messages = await self.list_messages(conversation_id, final_only=False)
        branches = await self.list_branches(conversation_id)
        messages_by_id = {message.id: message for message in messages}
        branch_by_key = {branch.branch_key: branch for branch in branches}
        return self._build_branch_events(
            branch_key=branch_key,
            events=events,
            messages_by_id=messages_by_id,
            branch_by_key=branch_by_key,
        )

    async def list_artifacts_for_branch(
        self,
        conversation_id: str,
        *,
        branch_key: str = MAIN_BRANCH_KEY,
    ) -> list[ArtifactRecord]:
        visible_events = await self.list_events_for_branch(conversation_id, branch_key=branch_key, limit=5000)
        artifacts = await self.list_artifacts(conversation_id)
        runs = await self.list_runs(conversation_id)
        branches = await self.list_branches(conversation_id)
        runs_by_id = {run.id: run for run in runs}
        branch_by_key = {branch.branch_key: branch for branch in branches}
        return self._build_branch_artifacts(
            artifacts=artifacts,
            visible_events=visible_events,
            runs_by_id=runs_by_id,
            branch_key=branch_key,
            branch_by_key=branch_by_key,
        )

    async def create_memory_item(
        self,
        *,
        scope: str,
        kind: str,
        title: str,
        content: str,
        source_conversation_id: str,
        source_event_id: str | None = None,
        confidence: float | None = None,
    ) -> MemoryItemRecord:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            memory_item = await repo.create_memory_item(
                scope=scope,
                kind=kind,
                title=title,
                content=content,
                source_conversation_id=source_conversation_id,
                source_event_id=source_event_id,
                confidence=confidence,
            )
            return _memory_item_record(memory_item)

    async def list_memory_items(self, conversation_id: str) -> list[MemoryItemRecord]:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            return [
                _memory_item_record(item)
                for item in await repo.list_memory_items(conversation_id, status=None)
                if item.status != MemoryStatus.deleted.value
            ]

    async def list_all_memory_items(self, *, status: str | None = None) -> list[MemoryItemRecord]:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            items = await repo.list_memory_items(status=status)
            return [
                _memory_item_record(item)
                for item in items
                if status == MemoryStatus.deleted.value or item.status != MemoryStatus.deleted.value
            ]

    async def get_memory_item(self, memory_item_id: str) -> MemoryItemRecord | None:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            memory_item = await repo.get_memory_item(memory_item_id)
            if memory_item is None:
                return None
            return _memory_item_record(memory_item)

    async def approve_memory_item(self, memory_item_id: str, *, knowledge_path: str) -> MemoryItemRecord:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            await repo.update_memory_item_status(
                memory_item_id,
                status=MemoryStatus.approved.value,
                knowledge_path=knowledge_path,
            )
            memory_item = await repo.get_memory_item(memory_item_id)
            if memory_item is None:
                raise ValueError(f"Memory item not found: {memory_item_id}")
            return _memory_item_record(memory_item)

    async def reject_memory_item(self, memory_item_id: str) -> MemoryItemRecord:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            await repo.update_memory_item_status(memory_item_id, status=MemoryStatus.rejected.value)
            memory_item = await repo.get_memory_item(memory_item_id)
            if memory_item is None:
                raise ValueError(f"Memory item not found: {memory_item_id}")
            return _memory_item_record(memory_item)

    async def delete_memory_item(self, memory_item_id: str) -> MemoryItemRecord:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            await repo.delete_memory_item(memory_item_id)
            memory_item = await repo.get_memory_item(memory_item_id)
            if memory_item is None:
                raise ValueError(f"Memory item not found: {memory_item_id}")
            return _memory_item_record(memory_item)


conversation_store = ConversationStore()
