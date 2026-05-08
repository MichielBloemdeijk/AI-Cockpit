"""High-level write service for durable conversation persistence."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from app.db.repositories.conversations import ConversationRepository
from app.db.session import session_scope
from app.db.tables import ActorKind, MemoryStatus, MessageRole, RunStatus
from app.services.conversation_title_service import conversation_title_service
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
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


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
    ) -> ConversationRecord:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            conversation = await repo.create_conversation(
                title=title,
                mode_hint=mode_hint,
                session_metadata_json=session_metadata_json,
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

    async def list_conversations(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        include_archived: bool = False,
    ) -> list[ConversationRecord]:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            conversations = await repo.list_conversations(
                limit=limit,
                offset=offset,
                include_archived=include_archived,
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
        parent_run_id: str | None = None,
        metadata_json: dict | None = None,
    ) -> RunRecord:
        async with session_scope() as session:
            repo = ConversationRepository(session)
            run = await repo.create_run(
                conversation_id=conversation_id,
                kind=kind,
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
            await repo.touch_conversation(conversation_id)
            event_record = _event_record(event)
            message_record = _message_record(message)

        if should_generate_title:
            conversation_title_service.schedule_title_generation(conversation_id, content)

        assert event_record is not None
        assert message_record is not None
        return event_record, message_record

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
        async with session_scope() as session:
            repo = ConversationRepository(session)
            return await repo.mark_running_runs_interrupted()

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