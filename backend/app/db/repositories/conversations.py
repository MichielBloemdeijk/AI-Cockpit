"""Focused repository queries for conversation persistence."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timezone

from sqlalchemy import delete, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.tables import (
    AppSettings,
    Conversation,
    ConversationArtifact,
    ConversationBranch,
    ConversationEvent,
    ConversationMessage,
    ConversationRun,
    JarvisState,
    MemoryItem,
    LiveSession,
    RunStatus,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ConversationRepository:
    """Query helpers for conversations, runs, events, messages, and artifacts."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def assert_content_mutable(self, conversation_id: str) -> Conversation:
        conversation = await self.get_conversation(conversation_id)
        if conversation is None:
            raise ValueError(f"Conversation not found: {conversation_id}")
        if (
            conversation.conversation_kind == "jarvis_daily"
            and conversation.lifecycle_status == "closed"
        ):
            raise ValueError(f"Closed Jarvis log is read-only: {conversation_id}")
        return conversation

    async def create_conversation(
        self,
        *,
        title: str | None = None,
        mode_hint: str | None = None,
        session_metadata_json: dict | None = None,
        workspace_path: str | None = None,
        conversation_kind: str = "standard",
        lifecycle_status: str = "open",
        jarvis_day_key: date | None = None,
        jarvis_day_sequence: int | None = None,
    ) -> Conversation:
        conversation = Conversation(
            title=title,
            mode_hint=mode_hint,
            session_metadata_json=session_metadata_json,
            workspace_path=workspace_path,
            conversation_kind=conversation_kind,
            lifecycle_status=lifecycle_status,
            jarvis_day_key=jarvis_day_key,
            jarvis_day_sequence=jarvis_day_sequence,
        )
        self.session.add(conversation)
        await self.session.flush()
        return conversation

    async def touch_conversation(
        self,
        conversation_id: str,
        *,
        title: str | None = None,
        mode_hint: str | None = None,
        session_metadata_json: dict | None = None,
        workspace_path: str | None = None,
        updated_at: datetime | None = None,
    ) -> None:
        values: dict[str, object] = {"updated_at": updated_at or utc_now()}
        if title is not None:
            values["title"] = title
        if mode_hint is not None:
            values["mode_hint"] = mode_hint
        if session_metadata_json is not None:
            values["session_metadata_json"] = session_metadata_json
        if workspace_path is not None:
            values["workspace_path"] = workspace_path
        stmt = update(Conversation).where(Conversation.id == conversation_id).values(**values)
        await self.session.execute(stmt)

    async def get_conversation(self, conversation_id: str) -> Conversation | None:
        return await self.session.get(Conversation, conversation_id)

    async def get_jarvis_state(self) -> JarvisState | None:
        return await self.session.get(JarvisState, "current")

    async def has_live_session(self, conversation_id: str) -> bool:
        statement = (
            select(LiveSession.id)
            .where(LiveSession.conversation_id == conversation_id)
            .where(LiveSession.lifecycle_state.in_(["connecting", "ready"]))
            .limit(1)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def list_conversations(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        include_archived: bool = False,
        archived_only: bool = False,
        conversation_kind: str | None = None,
        lifecycle_status: str | None = None,
        include_empty_jarvis: bool = False,
    ) -> Sequence[Conversation]:
        stmt = select(Conversation)
        if archived_only:
            stmt = stmt.where(Conversation.archived_at.is_not(None))
        elif not include_archived:
            stmt = stmt.where(Conversation.archived_at.is_(None))
        if conversation_kind is not None:
            stmt = stmt.where(Conversation.conversation_kind == conversation_kind)
        if lifecycle_status is not None:
            stmt = stmt.where(Conversation.lifecycle_status == lifecycle_status)
        if not include_empty_jarvis:
            has_messages = exists(
                select(ConversationMessage.id).where(
                    ConversationMessage.conversation_id == Conversation.id
                )
            )
            stmt = stmt.where(
                or_(Conversation.conversation_kind != "jarvis_daily", has_messages)
            )
        stmt = (
            stmt
            .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def archive_conversation(self, conversation_id: str) -> None:
        stmt = (
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(archived_at=utc_now(), updated_at=utc_now())
        )
        await self.session.execute(stmt)

    async def unarchive_conversation(self, conversation_id: str) -> None:
        stmt = (
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(archived_at=None, updated_at=utc_now())
        )
        await self.session.execute(stmt)

    async def create_branch(
        self,
        *,
        conversation_id: str,
        branch_key: str,
        label: str | None = None,
        parent_branch_key: str | None = None,
        branched_from_message_id: str | None = None,
    ) -> ConversationBranch:
        await self.assert_content_mutable(conversation_id)
        branch = ConversationBranch(
            conversation_id=conversation_id,
            branch_key=branch_key,
            label=label,
            parent_branch_key=parent_branch_key,
            branched_from_message_id=branched_from_message_id,
        )
        self.session.add(branch)
        await self.session.flush()
        return branch

    async def get_branch(self, conversation_id: str, branch_key: str) -> ConversationBranch | None:
        stmt = (
            select(ConversationBranch)
            .where(ConversationBranch.conversation_id == conversation_id)
            .where(ConversationBranch.branch_key == branch_key)
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_branches(self, conversation_id: str) -> Sequence[ConversationBranch]:
        stmt = (
            select(ConversationBranch)
            .where(ConversationBranch.conversation_id == conversation_id)
            .order_by(ConversationBranch.created_at.asc(), ConversationBranch.id.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def create_run(
        self,
        *,
        conversation_id: str,
        kind: str,
        status: str = RunStatus.running.value,
        parent_run_id: str | None = None,
        metadata_json: dict | None = None,
    ) -> ConversationRun:
        await self.assert_content_mutable(conversation_id)
        run = ConversationRun(
            conversation_id=conversation_id,
            kind=kind,
            status=status,
            parent_run_id=parent_run_id,
            metadata_json=metadata_json,
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def get_run(self, run_id: str) -> ConversationRun | None:
        return await self.session.get(ConversationRun, run_id)

    async def get_event(self, event_id: str) -> ConversationEvent | None:
        return await self.session.get(ConversationEvent, event_id)

    async def list_runs(self, conversation_id: str) -> Sequence[ConversationRun]:
        stmt = (
            select(ConversationRun)
            .where(ConversationRun.conversation_id == conversation_id)
            .order_by(ConversationRun.started_at.desc(), ConversationRun.id.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_runs_by_kind(self, kind: str) -> Sequence[ConversationRun]:
        stmt = (
            select(ConversationRun)
            .where(ConversationRun.kind == kind)
            .order_by(ConversationRun.started_at.desc(), ConversationRun.id.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_latest_run(self, conversation_id: str) -> ConversationRun | None:
        stmt = (
            select(ConversationRun)
            .where(ConversationRun.conversation_id == conversation_id)
            .order_by(ConversationRun.started_at.desc(), ConversationRun.id.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_run_status(
        self,
        run_id: str,
        *,
        status: str,
        error: str | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        stmt = (
            update(ConversationRun)
            .where(ConversationRun.id == run_id)
            .values(status=status, error=error, finished_at=finished_at)
        )
        await self.session.execute(stmt)

    async def close_conversation(self, conversation_id: str, *, closed_at: datetime) -> None:
        stmt = (
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .where(Conversation.lifecycle_status == "open")
            .values(lifecycle_status="closed", closed_at=closed_at, updated_at=closed_at)
        )
        result = await self.session.execute(stmt)
        if result.rowcount != 1:
            raise ValueError(f"Open conversation not found: {conversation_id}")

    async def claim_run_for_execution(self, run_id: str) -> bool:
        """Atomically claim a queued run so cancellation cannot race startup."""
        stmt = (
            update(ConversationRun)
            .where(ConversationRun.id == run_id)
            .where(ConversationRun.status == RunStatus.queued.value)
            .values(status=RunStatus.running.value, error=None, finished_at=None)
        )
        result = await self.session.execute(stmt)
        return bool(result.rowcount)

    async def transition_run_status(
        self,
        run_id: str,
        *,
        expected_statuses: set[str],
        status: str,
        error: str | None = None,
        finished_at: datetime | None = None,
    ) -> bool:
        if not expected_statuses:
            return False
        stmt = (
            update(ConversationRun)
            .where(ConversationRun.id == run_id)
            .where(ConversationRun.status.in_(sorted(expected_statuses)))
            .values(status=status, error=error, finished_at=finished_at)
        )
        result = await self.session.execute(stmt)
        return bool(result.rowcount)

    async def cancel_queued_run(self, run_id: str) -> bool:
        stmt = (
            update(ConversationRun)
            .where(ConversationRun.id == run_id)
            .where(ConversationRun.status == RunStatus.queued.value)
            .values(status=RunStatus.cancelled.value, error="Task cancelled by user", finished_at=utc_now())
        )
        result = await self.session.execute(stmt)
        return bool(result.rowcount)

    async def update_run_metadata(self, run_id: str, metadata_json: dict | None) -> None:
        stmt = (
            update(ConversationRun)
            .where(ConversationRun.id == run_id)
            .values(metadata_json=metadata_json)
        )
        await self.session.execute(stmt)

    async def delete_run(self, run_id: str) -> int:
        stmt = delete(ConversationRun).where(ConversationRun.id == run_id)
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def mark_running_runs_interrupted(self) -> int:
        finished_at = utc_now()
        stmt = (
            update(ConversationRun)
            .where(ConversationRun.status.in_([RunStatus.running.value, RunStatus.queued.value, RunStatus.paused.value]))
            .values(status=RunStatus.interrupted.value, finished_at=finished_at)
        )
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def next_event_sequence(self, conversation_id: str) -> int:
        """Atomically reserve the next cursor for a conversation.

        Event writers run in separate async sessions.  Allocating from a
        conversation counter prevents concurrent voice/background writes from
        receiving the same sequence (which would make cursor replay lossy).
        """
        update_stmt = (
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .where(
                or_(
                    Conversation.conversation_kind != "jarvis_daily",
                    Conversation.lifecycle_status == "open",
                )
            )
            .values(last_event_sequence=Conversation.last_event_sequence + 1)
        )
        result = await self.session.execute(update_stmt)
        if result.rowcount != 1:
            await self.assert_content_mutable(conversation_id)
            raise ValueError(f"Conversation not found: {conversation_id}")
        sequence_stmt = select(Conversation.last_event_sequence).where(Conversation.id == conversation_id)
        sequence_result = await self.session.execute(sequence_stmt)
        return int(sequence_result.scalar_one())

    async def append_event(
        self,
        *,
        conversation_id: str,
        run_id: str | None,
        actor_kind: str,
        event_type: str,
        payload_json: dict | None = None,
        branch_key: str | None = None,
        parent_event_id: str | None = None,
        schema_version: int = 1,
        idempotency_key: str | None = None,
    ) -> ConversationEvent:
        if idempotency_key is not None:
            # Take the conversation row's SQLite write lock before checking the
            # key. Concurrent retries then serialize without consuming a cursor
            # for the losing writer.
            lock_stmt = (
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .where(
                    or_(
                        Conversation.conversation_kind != "jarvis_daily",
                        Conversation.lifecycle_status == "open",
                    )
                )
                .values(last_event_sequence=Conversation.last_event_sequence)
            )
            lock_result = await self.session.execute(lock_stmt)
            if lock_result.rowcount != 1:
                await self.assert_content_mutable(conversation_id)
                raise ValueError(f"Conversation not found: {conversation_id}")
            existing_stmt = (
                select(ConversationEvent)
                .where(ConversationEvent.conversation_id == conversation_id)
                .where(ConversationEvent.idempotency_key == idempotency_key)
                .limit(1)
            )
            existing_result = await self.session.execute(existing_stmt)
            existing = existing_result.scalar_one_or_none()
            if existing is not None:
                return existing

        event = ConversationEvent(
            conversation_id=conversation_id,
            run_id=run_id,
            sequence=await self.next_event_sequence(conversation_id),
            branch_key=branch_key,
            parent_event_id=parent_event_id,
            actor_kind=actor_kind,
            event_type=event_type,
            schema_version=schema_version,
            payload_json=payload_json,
            idempotency_key=idempotency_key,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def create_message(
        self,
        *,
        conversation_id: str,
        role: str,
        content: str,
        run_id: str | None = None,
        source_event_id: str | None = None,
        author_label: str | None = None,
        content_format: str = "markdown",
        is_final: bool = True,
        modality: str = "text",
        execution_mode: str = "agent",
        communication_state: str = "completed",
        source_task_id: str | None = None,
        source_task_event_id: str | None = None,
        presented_in_turn_id: str | None = None,
    ) -> ConversationMessage:
        await self.assert_content_mutable(conversation_id)
        message = ConversationMessage(
            conversation_id=conversation_id,
            run_id=run_id,
            source_event_id=source_event_id,
            role=role,
            author_label=author_label,
            content=content,
            content_format=content_format,
            is_final=is_final,
            modality=modality,
            execution_mode=execution_mode,
            communication_state=communication_state,
            source_task_id=source_task_id,
            source_task_event_id=source_task_event_id,
            presented_in_turn_id=presented_in_turn_id,
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def list_messages(self, conversation_id: str, *, final_only: bool = False) -> Sequence[ConversationMessage]:
        stmt = (
            select(ConversationMessage)
            .outerjoin(
                ConversationEvent,
                ConversationMessage.source_event_id == ConversationEvent.id,
            )
            .where(ConversationMessage.conversation_id == conversation_id)
        )
        if final_only:
            stmt = stmt.where(ConversationMessage.is_final.is_(True))
        stmt = (
            stmt
            .order_by(
                ConversationMessage.created_at.asc(),
                func.coalesce(ConversationEvent.sequence, 0).asc(),
                ConversationMessage.id.asc(),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_last_message(self, conversation_id: str) -> ConversationMessage | None:
        stmt = (
            select(ConversationMessage)
            .outerjoin(
                ConversationEvent,
                ConversationMessage.source_event_id == ConversationEvent.id,
            )
            .where(ConversationMessage.conversation_id == conversation_id)
            .where(ConversationMessage.is_final.is_(True))
            .order_by(
                ConversationMessage.created_at.desc(),
                func.coalesce(ConversationEvent.sequence, 0).desc(),
                ConversationMessage.id.desc(),
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_message(self, message_id: str) -> ConversationMessage | None:
        return await self.session.get(ConversationMessage, message_id)

    async def list_events(self, conversation_id: str, *, limit: int = 500) -> Sequence[ConversationEvent]:
        stmt = (
            select(ConversationEvent)
            .where(ConversationEvent.conversation_id == conversation_id)
            .order_by(ConversationEvent.sequence.asc(), ConversationEvent.created_at.asc(), ConversationEvent.id.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_latest_event_sequence(self, conversation_id: str) -> int:
        stmt = select(func.max(ConversationEvent.sequence)).where(ConversationEvent.conversation_id == conversation_id)
        result = await self.session.execute(stmt)
        return int(result.scalar_one_or_none() or 0)

    async def list_unfinished_runs(self) -> Sequence[ConversationRun]:
        stmt = (
            select(ConversationRun)
            .where(ConversationRun.status.in_([RunStatus.running.value, RunStatus.queued.value, RunStatus.paused.value]))
            .order_by(ConversationRun.started_at.asc(), ConversationRun.id.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_events_after(
        self,
        conversation_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 250,
    ) -> Sequence[ConversationEvent]:
        stmt = (
            select(ConversationEvent)
            .where(ConversationEvent.conversation_id == conversation_id)
            .where(ConversationEvent.sequence > max(0, int(after_sequence)))
            .order_by(ConversationEvent.sequence.asc())
            .limit(max(1, min(int(limit), 1000)))
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_events_for_run(self, conversation_id: str, run_id: str, *, limit: int = 5000) -> Sequence[ConversationEvent]:
        stmt = (
            select(ConversationEvent)
            .where(ConversationEvent.conversation_id == conversation_id)
            .where(ConversationEvent.run_id == run_id)
            .order_by(ConversationEvent.sequence.asc(), ConversationEvent.created_at.asc(), ConversationEvent.id.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def create_artifact(
        self,
        *,
        conversation_id: str,
        artifact_type: str,
        mime_type: str,
        run_id: str | None = None,
        source_event_id: str | None = None,
        content_text: str | None = None,
        content_json: dict | None = None,
    ) -> ConversationArtifact:
        await self.assert_content_mutable(conversation_id)
        artifact = ConversationArtifact(
            conversation_id=conversation_id,
            run_id=run_id,
            source_event_id=source_event_id,
            artifact_type=artifact_type,
            mime_type=mime_type,
            content_text=content_text,
            content_json=content_json,
        )
        self.session.add(artifact)
        await self.session.flush()
        return artifact

    async def list_artifacts(self, conversation_id: str) -> Sequence[ConversationArtifact]:
        stmt = (
            select(ConversationArtifact)
            .where(ConversationArtifact.conversation_id == conversation_id)
            .order_by(ConversationArtifact.created_at.asc(), ConversationArtifact.id.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_artifacts_for_run(self, conversation_id: str, run_id: str) -> Sequence[ConversationArtifact]:
        stmt = (
            select(ConversationArtifact)
            .where(ConversationArtifact.conversation_id == conversation_id)
            .where(ConversationArtifact.run_id == run_id)
            .order_by(ConversationArtifact.created_at.asc(), ConversationArtifact.id.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def create_memory_item(
        self,
        *,
        scope: str,
        kind: str,
        title: str,
        content: str,
        source_conversation_id: str,
        status: str = "proposed",
        confidence: float | None = None,
        source_event_id: str | None = None,
    ) -> MemoryItem:
        memory_item = MemoryItem(
            scope=scope,
            kind=kind,
            title=title,
            content=content,
            status=status,
            confidence=confidence,
            source_conversation_id=source_conversation_id,
            source_event_id=source_event_id,
        )
        self.session.add(memory_item)
        await self.session.flush()
        return memory_item

    async def list_memory_items(
        self,
        conversation_id: str | None = None,
        *,
        status: str | None = None,
    ) -> Sequence[MemoryItem]:
        stmt = select(MemoryItem)
        if conversation_id is not None:
            stmt = stmt.where(MemoryItem.source_conversation_id == conversation_id)
        if status is not None:
            stmt = stmt.where(MemoryItem.status == status)
        stmt = stmt.order_by(MemoryItem.created_at.desc(), MemoryItem.id.desc())
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_memory_item(self, memory_item_id: str) -> MemoryItem | None:
        return await self.session.get(MemoryItem, memory_item_id)

    async def update_memory_item_status(
        self,
        memory_item_id: str,
        *,
        status: str,
        reviewed_at: datetime | None = None,
        knowledge_path: str | None = None,
    ) -> None:
        values: dict[str, object] = {"status": status, "reviewed_at": reviewed_at or utc_now()}
        if knowledge_path is not None:
            values["knowledge_path"] = knowledge_path
        stmt = (
            update(MemoryItem)
            .where(MemoryItem.id == memory_item_id)
            .values(**values)
        )
        await self.session.execute(stmt)

    async def delete_memory_item(self, memory_item_id: str) -> None:
        stmt = (
            update(MemoryItem)
            .where(MemoryItem.id == memory_item_id)
            .values(status="deleted", deleted_at=utc_now(), reviewed_at=utc_now())
        )
        await self.session.execute(stmt)

    async def get_app_settings(self, settings_id: str = "default") -> AppSettings | None:
        return await self.session.get(AppSettings, settings_id)

    async def upsert_app_settings(
        self,
        *,
        chat_defaults_json: dict,
        settings_id: str = "default",
    ) -> AppSettings:
        settings_row = await self.get_app_settings(settings_id)
        if settings_row is None:
            settings_row = AppSettings(id=settings_id, chat_defaults_json=chat_defaults_json)
            self.session.add(settings_row)
        else:
            settings_row.chat_defaults_json = chat_defaults_json
            settings_row.updated_at = utc_now()
        await self.session.flush()
        return settings_row
