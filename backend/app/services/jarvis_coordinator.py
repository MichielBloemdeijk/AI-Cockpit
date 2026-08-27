"""Durable provider-neutral coordinator for the live Jarvis workspace."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from app.db.repositories.conversations import ConversationRepository
from app.db.repositories.jarvis import JarvisDomainRepository
from app.db.session import session_scope
from app.db.tables import (
    Conversation,
    ConversationEvent,
    ConversationMessage,
    JarvisState,
    RealtimeSession,
    SpeechDelivery,
    Task,
    TaskPresentation,
    TaskQuestion,
    VoiceSessionEvent,
)
from app.services.jarvis_brain import restricted_brain_gateway
from app.services.jarvis_daily import JarvisAcceptedTurn, jarvis_daily_service
from app.services.task_presentations import PresentationReservation, task_presentation_service
from app.services.voice_session_adapter import (
    VoiceProviderEvent,
    fake_voice_session_adapter,
    get_voice_session_adapter,
)


ACTIVE_SESSION_STATES = {"connecting", "ready"}
FLOOR_EVENT_TYPES = {
    "ready": "listening",
    "user_speaking": "user_speaking",
    "user_stopped": "thinking",
    "assistant_generating": "thinking",
    "assistant_speaking": "assistant_speaking",
    "assistant_response": "listening",
    "assistant_interrupted": "listening",
    "disconnected": "text_fallback",
    "provider_error": "text_fallback",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StaleSessionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SessionRecord:
    id: str
    conversation_id: str
    generation: int
    provider_adapter: str
    lifecycle_state: str
    controller_id: str | None
    floor_state: str
    last_event_sequence: int
    created_at: datetime
    ready_at: datetime | None
    disconnected_at: datetime | None


@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    id: str
    turn_id: str | None
    state: str


class JarvisCoordinator:
    async def accept_text_turn(
        self,
        content: str,
        *,
        idempotency_key: str,
    ) -> JarvisAcceptedTurn:
        accepted = await jarvis_daily_service.accept_text_turn(
            content,
            idempotency_key=idempotency_key,
        )
        active = await self.get_active_session()
        if active is None or active.conversation_id != accepted.conversation.id:
            active = await self.open_controller(
                conversation_id=accepted.conversation.id,
                controller_id="typed-bridge",
                adapter_name="fake",
            )
        adapter = get_voice_session_adapter(active.provider_adapter)
        await adapter.send_user_text(
            session_id=active.id,
            generation=active.generation,
            turn_id=accepted.message.id,
            text=accepted.message.content,
        )
        return accepted

    async def start_fresh_log(self, *, idempotency_key: str):
        current = await self.get_active_session()
        new_log = await jarvis_daily_service.start_fresh_log(idempotency_key=idempotency_key)
        if current is not None:
            await self.supersede_session(
                current.id,
                generation=current.generation,
                reason="explicit_fresh_start",
            )
        return new_log

    async def open_controller(
        self,
        *,
        conversation_id: str,
        controller_id: str,
        adapter_name: str = "fake",
    ) -> SessionRecord:
        adapter = get_voice_session_adapter(adapter_name)
        superseded_adapters: list[tuple[str, int, str]] = []
        await task_presentation_service.sync_candidates()
        async with session_scope() as session:
            conversation = await session.get(Conversation, conversation_id)
            jarvis_state = await session.get(JarvisState, "current")
            if (
                conversation is None
                or conversation.conversation_kind != "jarvis_daily"
                or conversation.lifecycle_status != "open"
                or jarvis_state is None
                or jarvis_state.current_log_id != conversation_id
            ):
                raise ValueError("A controller requires the current open Jarvis log")

            active = list((await session.execute(
                select(RealtimeSession)
                .where(RealtimeSession.lifecycle_state.in_(sorted(ACTIVE_SESSION_STATES)))
                .order_by(RealtimeSession.generation.asc())
            )).scalars().all())
            repo = JarvisDomainRepository(session)
            for old in active:
                superseded_adapters.append((old.id, old.generation, old.provider_adapter))
                old.lifecycle_state = "superseded"
                old.disconnected_at = utc_now()
                await repo.append_session_event(
                    session_id=old.id,
                    event_type="session.superseded",
                    payload_json={"superseded_by_controller": controller_id},
                    idempotency_key=f"superseded:{old.id}:{controller_id}",
                )

            latest_generation = int((await session.execute(
                select(func.max(RealtimeSession.generation))
            )).scalar_one_or_none() or 0)
            realtime = RealtimeSession(
                id=str(uuid4()),
                conversation_id=conversation_id,
                generation=latest_generation + 1,
                provider_adapter=adapter.name,
                lifecycle_state="connecting",
                controller_id=controller_id,
            )
            session.add(realtime)
            await session.flush()
            await repo.append_session_event(
                session_id=realtime.id,
                event_type="session.connecting",
                payload_json={"controller_id": controller_id, "adapter": adapter.name},
                idempotency_key=f"connecting:{realtime.id}",
            )
            snapshot = await repo.read_snapshot(
                conversation_id=conversation_id,
                session_id=realtime.id,
            )
            record = self._record(realtime, floor_state="connecting")
            context = await self._context_in_session(session, snapshot)

        for old_session_id, old_generation, old_adapter in superseded_adapters:
            if old_adapter == "fake":
                fake_voice_session_adapter.detach(
                    session_id=old_session_id,
                    generation=old_generation,
                )

        try:
            await adapter.connect(
                session_id=record.id,
                generation=record.generation,
                context=context,
                on_event=self.handle_provider_event,
            )
        except Exception:
            async with session_scope() as session:
                failed = await session.get(RealtimeSession, record.id)
                if failed is not None:
                    failed.lifecycle_state = "disconnected"
                    failed.disconnected_at = utc_now()
            raise
        refreshed = await self.get_session(record.id)
        if refreshed is None:
            raise RuntimeError("Realtime session disappeared after connection")
        return refreshed

    async def observe(self) -> SessionRecord | None:
        """Return the active controller projection without taking ownership."""
        return await self.get_active_session()

    async def get_active_session(self) -> SessionRecord | None:
        async with session_scope() as session:
            realtime = (await session.execute(
                select(RealtimeSession)
                .where(RealtimeSession.lifecycle_state.in_(sorted(ACTIVE_SESSION_STATES)))
                .order_by(RealtimeSession.generation.desc())
                .limit(1)
            )).scalar_one_or_none()
            if realtime is None:
                return None
            return self._record(realtime, floor_state=await self._floor_state(session, realtime.id))

    async def get_session(self, session_id: str) -> SessionRecord | None:
        async with session_scope() as session:
            realtime = await session.get(RealtimeSession, session_id)
            if realtime is None:
                return None
            return self._record(realtime, floor_state=await self._floor_state(session, realtime.id))

    async def get_latest_session(self, *, conversation_id: str) -> SessionRecord | None:
        async with session_scope() as session:
            realtime = (await session.execute(
                select(RealtimeSession)
                .where(RealtimeSession.conversation_id == conversation_id)
                .order_by(RealtimeSession.generation.desc())
                .limit(1)
            )).scalar_one_or_none()
            if realtime is None:
                return None
            return self._record(realtime, floor_state=await self._floor_state(session, realtime.id))

    async def handle_provider_event(
        self,
        session_id: str,
        generation: int,
        event: VoiceProviderEvent,
    ) -> None:
        shell_call: tuple[str, dict[str, Any], str] | None = None
        presentation_action: tuple[str, tuple[str, ...], DeliveryRecord] | None = None
        async with session_scope() as session:
            realtime = await self._require_current_generation(session, session_id, generation)
            repo = JarvisDomainRepository(session)
            event_type = f"provider.{event.kind}"
            payload = dict(event.payload)
            if await self._is_late_delivery_state(session, realtime, event):
                event_type = "provider.event.ignored"
                payload = {**payload, "kind": event.kind, "reason": "delivery_already_terminal"}
            appended = await repo.append_session_event_once(
                session_id=session_id,
                event_type=event_type,
                payload_json=payload,
                idempotency_key=f"provider:{event.event_id}",
            )
            if not appended.created:
                return

            if event.kind == "ready":
                realtime.lifecycle_state = "ready"
                realtime.ready_at = realtime.ready_at or utc_now()
            elif event.kind in {"disconnected", "provider_error"}:
                realtime.lifecycle_state = "disconnected"
                realtime.disconnected_at = utc_now()

            if event.kind in {"assistant_response", "assistant_interrupted"}:
                delivery = await self._persist_delivery(session, realtime, event)
                raw_presentation_ids = payload.get("presentation_ids")
                presentation_ids = tuple(
                    str(item)
                    for item in (raw_presentation_ids if isinstance(raw_presentation_ids, list) else [])
                    if item
                )
                if presentation_ids:
                    action = "present" if delivery.state == "completed" or bool(payload.get("facts_heard")) else "defer"
                    presentation_action = (action, presentation_ids, delivery)
            elif event.kind == "shell_call":
                tool_name = str(payload.get("name") or "")
                arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
                call_id = str(payload.get("call_id") or event.event_id)
                shell_call = (tool_name, dict(arguments), call_id)

        if presentation_action is not None:
            action, presentation_ids, delivery = presentation_action
            if action == "present":
                await task_presentation_service.mark_presented(
                    presentation_ids=presentation_ids,
                    session_id=session_id,
                    generation=generation,
                    turn_id=delivery.turn_id,
                    delivery_id=delivery.id,
                )
            else:
                await task_presentation_service.defer(
                    presentation_ids=presentation_ids,
                    session_id=session_id,
                    generation=generation,
                    error="delivery_interrupted_before_required_facts",
                )

        if shell_call is not None:
            tool_name, arguments, call_id = shell_call
            await self._execute_shell_call(
                session_id=session_id,
                generation=generation,
                tool_name=tool_name,
                arguments=arguments,
                call_id=call_id,
            )

    async def inject_fake_event(
        self,
        *,
        session_id: str,
        generation: int,
        event_id: str,
        kind: str,
        payload: dict[str, Any],
    ) -> None:
        session = await self.get_session(session_id)
        if session is None or session.provider_adapter != "fake":
            raise ValueError("Fake provider session not found")
        await fake_voice_session_adapter.inject(
            session_id=session_id,
            generation=generation,
            event=VoiceProviderEvent(event_id=event_id, kind=kind, payload=payload),
        )

    async def reserve_presentations(
        self,
        *,
        session_id: str,
        generation: int,
    ) -> PresentationReservation | None:
        async with session_scope() as session:
            await self._require_current_generation(session, session_id, generation)
            floor = await self._floor_state(session, session_id)
        reservation = await task_presentation_service.reserve(
            session_id=session_id,
            generation=generation,
            user_owns_floor=floor == "user_speaking",
        )
        if reservation is not None:
            async with session_scope() as session:
                await JarvisDomainRepository(session).append_session_event(
                    session_id=session_id,
                    event_type="presentation.reserved",
                    payload_json={
                        "presentation_ids": list(reservation.presentation_ids),
                        "facts": list(reservation.facts),
                    },
                    idempotency_key=f"presentation-reserved:{session_id}:{generation}:{':'.join(reservation.presentation_ids)}",
                )
        return reservation

    async def supersede_session(self, session_id: str, *, generation: int, reason: str) -> None:
        async with session_scope() as session:
            realtime = await self._require_current_generation(session, session_id, generation)
            realtime.lifecycle_state = "superseded"
            realtime.disconnected_at = utc_now()
            await JarvisDomainRepository(session).append_session_event(
                session_id=session_id,
                event_type="session.superseded",
                payload_json={"reason": reason},
                idempotency_key=f"session-superseded:{session_id}:{generation}:{reason}",
            )
        if realtime.provider_adapter == "fake":
            fake_voice_session_adapter.detach(session_id=session_id, generation=generation)

    async def close_session(self, session_id: str, *, generation: int, reason: str = "requested") -> None:
        session = await self.get_session(session_id)
        if session is None:
            raise ValueError("Realtime session not found")
        await self._assert_generation(session_id, generation)
        adapter = get_voice_session_adapter(session.provider_adapter)
        await adapter.close(session_id=session_id, generation=generation, reason=reason)

    async def replay_session_events(
        self,
        session_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 250,
    ) -> tuple[int, tuple[VoiceSessionEvent, ...]]:
        async with session_scope() as session:
            realtime = await session.get(RealtimeSession, session_id)
            if realtime is None:
                raise ValueError("Realtime session not found")
            result = await session.execute(
                select(VoiceSessionEvent)
                .where(VoiceSessionEvent.session_id == session_id)
                .where(VoiceSessionEvent.sequence > after_sequence)
                .order_by(VoiceSessionEvent.sequence.asc())
                .limit(limit)
            )
            return realtime.last_event_sequence, tuple(result.scalars().all())

    async def replay_conversation_events(
        self,
        conversation_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 250,
    ) -> tuple[int, tuple[ConversationEvent, ...]]:
        async with session_scope() as session:
            conversation = await session.get(Conversation, conversation_id)
            if conversation is None or conversation.conversation_kind != "jarvis_daily":
                raise ValueError("Jarvis log not found")
            result = await session.execute(
                select(ConversationEvent)
                .where(ConversationEvent.conversation_id == conversation_id)
                .where(ConversationEvent.sequence > after_sequence)
                .order_by(ConversationEvent.sequence.asc())
                .limit(limit)
            )
            return conversation.last_event_sequence, tuple(result.scalars().all())

    async def context(self, session_id: str | None = None) -> dict[str, Any] | None:
        state = await jarvis_daily_service.get_state()
        if state.current_log is None:
            return None
        async with session_scope() as session:
            snapshot = await JarvisDomainRepository(session).read_snapshot(
                conversation_id=state.current_log.id,
                session_id=session_id,
            )
            return await self._context_in_session(session, snapshot)

    async def _execute_shell_call(
        self,
        *,
        session_id: str,
        generation: int,
        tool_name: str,
        arguments: dict[str, Any],
        call_id: str,
    ) -> None:
        async with session_scope() as session:
            realtime = await self._require_current_generation(session, session_id, generation)
            last_turn_id = (await session.execute(
                select(ConversationMessage.id)
                .join(
                    ConversationEvent,
                    ConversationMessage.source_event_id == ConversationEvent.id,
                )
                .where(ConversationMessage.conversation_id == realtime.conversation_id)
                .where(ConversationMessage.role == "user")
                .order_by(ConversationEvent.sequence.desc())
                .limit(1)
            )).scalar_one_or_none()
            conversation_id = realtime.conversation_id
        try:
            result = await restricted_brain_gateway.execute(
                tool_name=tool_name,
                arguments=arguments,
                call_id=call_id,
                conversation_id=conversation_id,
                origin_turn_id=last_turn_id,
            )
            output = result.output
            control = result.control
            ok = True
        except Exception as exc:
            output = {"error": str(exc)}
            control = None
            ok = False

        async with session_scope() as session:
            await self._require_current_generation(session, session_id, generation)
            await JarvisDomainRepository(session).append_session_event(
                session_id=session_id,
                event_type="brain.tool.result",
                payload_json={
                    "call_id": call_id,
                    "name": tool_name,
                    "ok": ok,
                    "output": output,
                },
                idempotency_key=f"brain-result:{session_id}:{generation}:{call_id}",
            )
        if control == "start_new_jarvis_log":
            await self.start_fresh_log(idempotency_key=f"brain:{call_id}:fresh")
        elif control == "end_voice_session":
            await self.close_session(session_id, generation=generation, reason="shell_requested")

    async def _persist_delivery(
        self,
        session,
        realtime: RealtimeSession,
        event: VoiceProviderEvent,
    ) -> DeliveryRecord:
        payload = event.payload
        response_id = str(payload.get("response_id") or event.event_id)
        item_id = str(payload.get("item_id") or response_id)
        existing = (await session.execute(
            select(SpeechDelivery)
            .where(SpeechDelivery.realtime_session_id == realtime.id)
            .where(SpeechDelivery.provider_response_id == response_id)
            .where(SpeechDelivery.provider_item_id == item_id)
            .limit(1)
        )).scalar_one_or_none()
        if existing is not None:
            return DeliveryRecord(existing.id, existing.assistant_turn_id, existing.state)

        generated = str(payload.get("transcript") or "")
        interrupted = event.kind == "assistant_interrupted" or bool(payload.get("interrupted"))
        heard = str((payload.get("heard_transcript") or "") if interrupted else generated)
        delivery_state = "interrupted" if interrupted else ("completed" if heard else "unheard")
        delivery = SpeechDelivery(
            id=str(uuid4()),
            realtime_session_id=realtime.id,
            provider_response_id=response_id,
            provider_item_id=item_id,
            generated_transcript=generated,
            heard_transcript=heard or None,
            state=delivery_state,
            generation_started_at=utc_now(),
            interrupted_at=utc_now() if interrupted else None,
            completed_at=utc_now(),
            source_task_id=str(payload.get("source_task_id") or "") or None,
            source_task_event_id=str(payload.get("source_task_event_id") or "") or None,
            presentation_id=self._first_presentation_id(payload),
            provider_causality_json={"provider_event_id": event.event_id},
        )
        session.add(delivery)
        await session.flush()

        repo = ConversationRepository(session)
        conversation_event = await repo.append_event(
            conversation_id=realtime.conversation_id,
            run_id=None,
            actor_kind="assistant",
            event_type=f"conversation.assistant.message.{delivery_state}",
            payload_json={
                "delivery_id": delivery.id,
                "generated_transcript": generated,
                "heard_transcript": heard,
            },
            branch_key="main",
            idempotency_key=f"provider-delivery:{response_id}:{item_id}",
        )
        existing_turn_id = str((conversation_event.payload_json or {}).get("turn_id") or "")
        if existing_turn_id:
            delivery.assistant_turn_id = existing_turn_id
            return DeliveryRecord(delivery.id, existing_turn_id, delivery_state)
        turn_id = None
        if heard:
            message = await repo.create_message(
                conversation_id=realtime.conversation_id,
                source_event_id=conversation_event.id,
                role="assistant",
                author_label="Jarvis",
                content=heard,
                modality=str(payload.get("modality") or "voice"),
                execution_mode="jarvis_shell",
                communication_state=delivery_state,
                source_task_id=delivery.source_task_id,
                source_task_event_id=delivery.source_task_event_id,
            )
            delivery.assistant_turn_id = message.id
            conversation_event.payload_json = {
                **(conversation_event.payload_json or {}),
                "turn_id": message.id,
            }
            turn_id = message.id
        return DeliveryRecord(delivery.id, turn_id, delivery_state)

    @staticmethod
    def _first_presentation_id(payload: dict[str, Any]) -> str | None:
        value = payload.get("presentation_ids")
        if not isinstance(value, list) or not value:
            return None
        return str(value[0]) or None

    async def _assert_generation(self, session_id: str, generation: int) -> None:
        async with session_scope() as session:
            await self._require_current_generation(session, session_id, generation)

    @staticmethod
    async def _require_current_generation(session, session_id: str, generation: int) -> RealtimeSession:
        realtime = await session.get(RealtimeSession, session_id)
        if (
            realtime is None
            or realtime.generation != generation
            or realtime.lifecycle_state not in ACTIVE_SESSION_STATES
        ):
            raise StaleSessionError("Realtime session generation is stale")
        newer = (await session.execute(
            select(RealtimeSession.id)
            .where(RealtimeSession.generation > generation)
            .where(RealtimeSession.lifecycle_state.in_(sorted(ACTIVE_SESSION_STATES)))
            .limit(1)
        )).scalar_one_or_none()
        if newer is not None:
            raise StaleSessionError("Realtime session generation is stale")
        return realtime

    @staticmethod
    async def _floor_state(session, session_id: str) -> str:
        event_types = list((await session.execute(
            select(VoiceSessionEvent.event_type)
            .where(VoiceSessionEvent.session_id == session_id)
            .order_by(VoiceSessionEvent.sequence.desc())
            .limit(50)
        )).scalars().all())
        for event_type in event_types:
            if event_type == "provider.event.ignored":
                continue
            if event_type.startswith("provider."):
                return FLOOR_EVENT_TYPES.get(event_type.removeprefix("provider."), "thinking")
            if event_type == "session.connecting":
                return "connecting"
            if event_type == "session.superseded":
                return "text_fallback"
        return "thinking"

    @staticmethod
    async def _is_late_delivery_state(session, realtime: RealtimeSession, event: VoiceProviderEvent) -> bool:
        if event.kind not in {"assistant_generating", "assistant_speaking"}:
            return False
        response_id = str(event.payload.get("response_id") or "")
        if not response_id:
            return False
        existing = (await session.execute(
            select(SpeechDelivery.id)
            .where(SpeechDelivery.realtime_session_id == realtime.id)
            .where(SpeechDelivery.provider_response_id == response_id)
            .where(SpeechDelivery.state.in_(["completed", "interrupted", "unheard"]))
            .limit(1)
        )).scalar_one_or_none()
        return existing is not None

    @staticmethod
    async def _context_in_session(session, snapshot) -> dict[str, Any]:
        messages = list((await session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == snapshot.conversation.id)
            .where(ConversationMessage.communication_state.in_(["completed", "interrupted"]))
            .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
            .limit(60)
        )).scalars().all())
        messages.reverse()
        dialogue: list[dict[str, str]] = []
        remaining = 30_000
        for message in reversed(messages):
            content = message.content[-remaining:]
            dialogue.append(
                {
                    "role": message.role,
                    "content": content,
                    "communication_state": message.communication_state,
                }
            )
            remaining -= len(content)
            if remaining <= 0:
                break
        dialogue.reverse()

        task_ids = [task.id for task in snapshot.tasks]
        questions: list[TaskQuestion] = []
        if task_ids:
            questions = list((await session.execute(
                select(TaskQuestion)
                .where(TaskQuestion.task_id.in_(task_ids))
                .where(TaskQuestion.state == "asked")
                .order_by(TaskQuestion.asked_at.asc())
            )).scalars().all())
        presentations = list((await session.execute(
            select(TaskPresentation)
            .where(TaskPresentation.state.in_(["pending", "deferred", "reserved"]))
            .order_by(TaskPresentation.created_at.asc())
        )).scalars().all())
        return {
            "conversation_id": snapshot.conversation.id,
            "dialogue": dialogue,
            "tasks": [
                {
                    "task_id": task.id,
                    "goal": task.goal,
                    "status": task.status,
                    "summary": task.terminal_summary,
                }
                for task in snapshot.tasks
            ],
            "questions": [
                {"task_id": item.task_id, "question_id": item.id, "question": item.question}
                for item in questions
            ],
            "presentations": [
                {
                    "presentation_id": item.id,
                    "task_id": item.task_id,
                    "type": item.candidate_type,
                    "facts": item.required_facts_json,
                }
                for item in presentations
            ],
            "cursors": {
                "conversation": snapshot.conversation_cursor,
                "task": snapshot.task_cursor,
                "session": snapshot.session_cursor,
            },
        }

    @staticmethod
    def _record(realtime: RealtimeSession, *, floor_state: str) -> SessionRecord:
        return SessionRecord(
            id=realtime.id,
            conversation_id=realtime.conversation_id,
            generation=realtime.generation,
            provider_adapter=realtime.provider_adapter,
            lifecycle_state=realtime.lifecycle_state,
            controller_id=realtime.controller_id,
            floor_state=floor_state,
            last_event_sequence=realtime.last_event_sequence,
            created_at=realtime.created_at,
            ready_at=realtime.ready_at,
            disconnected_at=realtime.disconnected_at,
        )


jarvis_coordinator = JarvisCoordinator()
