"""Transactional Jarvis daily-log lifecycle and accepted-turn persistence."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.conversations import ConversationRepository
from app.db.session import session_scope
from app.db.tables import Conversation, ConversationEvent, ConversationMessage, JarvisState
from app.services.conversation_store import ConversationRecord, MessageRecord, _conversation_record, _message_record
from app.services.conversation_workspace import MAIN_BRANCH_KEY, ensure_conversation_workspace, workspace_relative_path


AMSTERDAM = ZoneInfo("Europe/Amsterdam")
JARVIS_STATE_ID = "current"


def operational_day_key(timestamp: datetime) -> date:
    """Return the 04:00 Europe/Amsterdam operational date for a timestamp."""
    if timestamp.tzinfo is None:
        raise ValueError("Jarvis timestamps must be timezone-aware")
    local = timestamp.astimezone(AMSTERDAM)
    return (local - timedelta(hours=4)).date()


def jarvis_log_title(day_key: date, sequence: int) -> str:
    base = f"Jarvis — {day_key.isoformat()}"
    return base if sequence == 1 else f"{base} ({sequence})"


@dataclass(frozen=True, slots=True)
class JarvisStateRecord:
    operational_day: date
    current_log: ConversationRecord | None
    state_revision: int
    focused_task_id: str | None = None
    recent_task_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class JarvisAcceptedTurn:
    conversation: ConversationRecord
    message: MessageRecord
    event_sequence: int
    created: bool = True


class JarvisDailyService:
    async def get_state(self, *, now: datetime | None = None) -> JarvisStateRecord:
        timestamp = now or datetime.now(timezone.utc)
        async with session_scope() as session:
            state = await session.get(JarvisState, JARVIS_STATE_ID)
            if state is None or state.current_log_id is None:
                return JarvisStateRecord(
                    operational_day=operational_day_key(timestamp),
                    current_log=None,
                    state_revision=0 if state is None else state.state_revision,
                    focused_task_id=None if state is None else state.focused_task_id,
                    recent_task_refs=tuple([] if state is None else (state.recent_task_refs_json or [])),
                )
            current_log = await session.get(Conversation, state.current_log_id)
            self._assert_current_pointer(current_log)
            return JarvisStateRecord(
                operational_day=operational_day_key(timestamp),
                current_log=_conversation_record(current_log),
                state_revision=state.state_revision,
                focused_task_id=state.focused_task_id,
                recent_task_refs=tuple(state.recent_task_refs_json or []),
            )

    async def accept_text_turn(
        self,
        content: str,
        *,
        accepted_at: datetime | None = None,
        idempotency_key: str | None = None,
        exchange_active: bool = False,
    ) -> JarvisAcceptedTurn:
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("Jarvis turn content cannot be empty")
        timestamp = accepted_at or datetime.now(timezone.utc)
        request_key = idempotency_key or str(uuid4())

        async with session_scope() as session:
            repo = ConversationRepository(session)
            state = await self._lock_state(session)
            conversation = await self._resolve_current_log(
                session,
                repo,
                state,
                timestamp=timestamp,
                exchange_active=exchange_active,
            )
            event = await repo.append_event(
                conversation_id=conversation.id,
                run_id=None,
                actor_kind="user",
                event_type="conversation.user_message.accepted",
                branch_key=MAIN_BRANCH_KEY,
                payload_json={"content": normalized_content, "modality": "text"},
                idempotency_key=f"jarvis.text:{request_key}",
            )
            existing_turn_id = str((event.payload_json or {}).get("turn_id") or "")
            if existing_turn_id:
                existing_message = await repo.get_message(existing_turn_id)
                if existing_message is not None:
                    await session.refresh(conversation)
                    return JarvisAcceptedTurn(
                        conversation=_conversation_record(conversation),
                        message=_message_record(existing_message),
                        event_sequence=event.sequence,
                        created=False,
                    )

            message = await repo.create_message(
                conversation_id=conversation.id,
                source_event_id=event.id,
                role="user",
                author_label="You",
                content=normalized_content,
                modality="text",
                execution_mode="jarvis_shell",
                communication_state="completed",
            )
            event.payload_json = {
                **(event.payload_json or {}),
                "turn_id": message.id,
            }
            await repo.touch_conversation(conversation.id, updated_at=timestamp)
            await session.flush()
            await session.refresh(conversation)
            return JarvisAcceptedTurn(
                conversation=_conversation_record(conversation),
                message=_message_record(message),
                event_sequence=event.sequence,
                created=True,
            )

    async def start_fresh_log(
        self,
        *,
        requested_at: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> ConversationRecord:
        timestamp = requested_at or datetime.now(timezone.utc)
        request_key = idempotency_key or str(uuid4())
        event_key = f"jarvis.fresh:{request_key}"

        async with session_scope() as session:
            repo = ConversationRepository(session)
            state = await self._lock_state(session)

            existing_result = await session.execute(
                select(ConversationEvent).where(ConversationEvent.idempotency_key == event_key).limit(1)
            )
            if existing_result.scalar_one_or_none() is not None and state.current_log_id is not None:
                existing_current = await session.get(Conversation, state.current_log_id)
                self._assert_current_pointer(existing_current)
                return _conversation_record(existing_current)

            current = None if state.current_log_id is None else await session.get(Conversation, state.current_log_id)
            if current is not None:
                self._assert_current_pointer(current)
                acknowledgement_event = await repo.append_event(
                    conversation_id=current.id,
                    run_id=None,
                    actor_kind="assistant",
                    event_type="conversation.assistant.message.completed",
                    branch_key=MAIN_BRANCH_KEY,
                    payload_json={"content": "Starting a fresh Jarvis log."},
                    idempotency_key=event_key,
                )
                acknowledgement = await repo.create_message(
                    conversation_id=current.id,
                    source_event_id=acknowledgement_event.id,
                    role="assistant",
                    author_label="Jarvis",
                    content="Starting a fresh Jarvis log.",
                    modality="text",
                    execution_mode="jarvis_shell",
                    communication_state="completed",
                )
                acknowledgement_event.payload_json = {
                    **(acknowledgement_event.payload_json or {}),
                    "turn_id": acknowledgement.id,
                }
                await self._close_log(repo, current, closed_at=timestamp, reason="explicit_fresh_start")

            new_log = await self._create_log(session, repo, day_key=operational_day_key(timestamp))
            if current is None:
                await repo.append_event(
                    conversation_id=new_log.id,
                    run_id=None,
                    actor_kind="system",
                    event_type="conversation.jarvis_log.created",
                    branch_key=MAIN_BRANCH_KEY,
                    payload_json={"reason": "explicit_fresh_start"},
                    idempotency_key=event_key,
                )
            self._point_state(state, new_log)
            await session.flush()
            # SQLite server defaults (notably updated_at) are expired after
            # the insert. Refresh before building the detached API record so
            # a brand-new database does not trigger async lazy loading.
            await session.refresh(new_log)
            return _conversation_record(new_log)

    async def _lock_state(self, session: AsyncSession) -> JarvisState:
        await session.execute(
            sqlite_insert(JarvisState)
            .values(id=JARVIS_STATE_ID, state_revision=0)
            .on_conflict_do_nothing(index_elements=["id"])
        )
        lock_result = await session.execute(
            update(JarvisState)
            .where(JarvisState.id == JARVIS_STATE_ID)
            .values(state_revision=JarvisState.state_revision)
        )
        if lock_result.rowcount != 1:
            raise RuntimeError("Unable to lock Jarvis state")
        state = await session.get(JarvisState, JARVIS_STATE_ID)
        if state is None:
            raise RuntimeError("Jarvis state was not created")
        return state

    async def _resolve_current_log(
        self,
        session: AsyncSession,
        repo: ConversationRepository,
        state: JarvisState,
        *,
        timestamp: datetime,
        exchange_active: bool,
    ) -> Conversation:
        day_key = operational_day_key(timestamp)
        current = None if state.current_log_id is None else await session.get(Conversation, state.current_log_id)
        if current is None:
            current = await self._create_log(session, repo, day_key=day_key)
            self._point_state(state, current)
            return current

        self._assert_current_pointer(current)
        if current.jarvis_day_key == day_key or exchange_active:
            return current

        await self._close_log(repo, current, closed_at=timestamp, reason="operational_day_rollover")
        replacement = await self._create_log(session, repo, day_key=day_key)
        self._point_state(state, replacement)
        return replacement

    async def _create_log(
        self,
        session: AsyncSession,
        repo: ConversationRepository,
        *,
        day_key: date,
    ) -> Conversation:
        sequence_result = await session.execute(
            select(func.max(Conversation.jarvis_day_sequence)).where(
                Conversation.conversation_kind == "jarvis_daily",
                Conversation.jarvis_day_key == day_key,
            )
        )
        sequence = int(sequence_result.scalar_one_or_none() or 0) + 1
        conversation = await repo.create_conversation(
            title=jarvis_log_title(day_key, sequence),
            mode_hint="jarvis_shell",
            conversation_kind="jarvis_daily",
            lifecycle_status="open",
            jarvis_day_key=day_key,
            jarvis_day_sequence=sequence,
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
        return conversation

    async def _close_log(
        self,
        repo: ConversationRepository,
        conversation: Conversation,
        *,
        closed_at: datetime,
        reason: str,
    ) -> None:
        await repo.append_event(
            conversation_id=conversation.id,
            run_id=None,
            actor_kind="system",
            event_type="conversation.lifecycle.closed",
            branch_key=MAIN_BRANCH_KEY,
            payload_json={"reason": reason},
            idempotency_key=f"jarvis.close:{conversation.id}",
        )
        await repo.close_conversation(conversation.id, closed_at=closed_at)
        conversation.lifecycle_status = "closed"
        conversation.closed_at = closed_at

    @staticmethod
    def _point_state(state: JarvisState, conversation: Conversation) -> None:
        state.current_log_id = conversation.id
        state.jarvis_day_key = conversation.jarvis_day_key
        state.jarvis_day_sequence = conversation.jarvis_day_sequence
        state.state_revision += 1

    @staticmethod
    def _assert_current_pointer(conversation: Conversation | None) -> None:
        if (
            conversation is None
            or conversation.conversation_kind != "jarvis_daily"
            or conversation.lifecycle_status != "open"
        ):
            raise RuntimeError("Jarvis state does not point to an open daily log")


jarvis_daily_service = JarvisDailyService()
