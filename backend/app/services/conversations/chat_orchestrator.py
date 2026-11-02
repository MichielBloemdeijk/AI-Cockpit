"""Conversation-aware chat orchestration above the transport-only LLM client."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
from typing import Any, AsyncIterator, Awaitable, Callable

from app.config import settings
from app.models.chat import ChatResponse, Message, ModelResponse, PromptMetrics, Role
from app.models.settings import ConversationSessionMetadata
from app.services.conversations.chat_settings import chat_settings_service
from app.services.conversations.compaction import build_conversation_prompt_context
from app.services.conversations.read_model import conversation_read_model
from app.db.tables import ConversationRunStatus
from app.services.conversations.store import EventRecord, RunRecord, conversation_store
from app.services.conversations.timeline import TIMELINE_ITEM_EVENT_TYPE
from app.services.conversations.workspace import MAIN_BRANCH_KEY
from app.services.conversations.run_supervisor import run_supervisor
from app.services.llm import (
    PromptSegment,
    aggregate_usage,
    chat_completion,
    chat_completion_stream_response,
    ensure_prompt_metrics,
)


logger = logging.getLogger(__name__)

# Durable events forwarded on a single-chat stream; content deltas travel as chunk frames.
STREAMED_CHAT_EVENT_TYPES = {TIMELINE_ITEM_EVENT_TYPE}
StreamCallback = Callable[[dict[str, Any]], Awaitable[None]]


@asynccontextmanager
async def _owned_run(run_id: str, label: str) -> AsyncIterator[None]:
    """Own a started run: any failure or cancellation ends it instead of stranding it as running."""
    try:
        yield
    except asyncio.CancelledError:
        await asyncio.shield(conversation_store.end_run(run_id, ConversationRunStatus.cancelled))
        raise
    except Exception as exc:
        logger.exception("%s run %s failed", label, run_id)
        await conversation_store.end_run(
            run_id, ConversationRunStatus.failed, error=str(exc) or f"The {label.lower()} reply failed.",
        )
        raise


def _latest_user_message(messages: list[Message]) -> Message:
    for message in reversed(messages):
        if message.role == "user":
            return message
    raise ValueError("Conversation turn requires a user message")


def _serialize_messages(messages: list[Message]) -> list[dict[str, str]]:
    return [message.model_dump() for message in messages]


def _transient_system_messages(messages: list[Message]) -> list[Message]:
    return [message for message in messages if message.role == "system"]


def _message_session_id(conversation_id: str, branch_key: str) -> str:
    return f"conversation:{conversation_id}:{branch_key}"


def _build_synthesis_prompt(messages: list[Message], responses: list[ModelResponse]) -> str:
    synthesis_parts = []
    for response in responses:
        if response.error:
            synthesis_parts.append(f"**{response.model}**: [Error: {response.error}]")
        else:
            synthesis_parts.append(f"**{response.model}**:\n{response.content}")

    return (
        "You are a synthesis AI. Multiple AI models were asked the same question. "
        "Your job is to:\n"
        "1. Identify key agreements between the models\n"
        "2. Note any significant disagreements or unique insights\n"
        "3. Produce a unified, high-quality answer that incorporates the best of all responses\n\n"
        "The original conversation:\n"
        + "\n".join(f"{m.role}: {m.content}" for m in messages)
        + "\n\n---\nModel responses:\n\n"
        + "\n\n---\n\n".join(synthesis_parts)
        + "\n\n---\nNow provide your synthesized answer:"
    )


@dataclass(slots=True)
class PreparedTurn:
    conversation_id: str
    run: RunRecord
    session_metadata: ConversationSessionMetadata


@dataclass(slots=True)
class StreamEnvelope:
    conversation_id: str
    run_id: str
    events: AsyncIterator[dict]


def _conversation_event_payload(event: EventRecord) -> dict[str, object | None]:
    return {
        "id": event.id,
        "run_id": event.run_id,
        "sequence": event.sequence,
        "branch_key": event.branch_key,
        "parent_event_id": event.parent_event_id,
        "actor_kind": event.actor_kind,
        "event_type": event.event_type,
        "created_at": event.created_at.isoformat(),
        "schema_version": event.schema_version,
        "payload_json": event.payload_json,
    }


@dataclass(slots=True)
class PromptBuildResult:
    messages: list[Message]
    prompt_segments: list[PromptSegment]
    time_based_microcompact_applied: bool = False


class ChatOrchestrator:
    async def _prepare_single_run(
        self,
        *,
        messages: list[Message],
        conversation_id: str | None,
        session_metadata: ConversationSessionMetadata | None,
        model: str | None,
        branch_key: str = MAIN_BRANCH_KEY,
        parent_event_id: str | None = None,
    ) -> tuple[PreparedTurn, str]:
        _, resolved_session_metadata = await self.resolve_session_metadata(
            conversation_id=conversation_id,
            session_metadata=session_metadata,
            council_mode=False,
            model=model,
        )
        resolved_model = resolved_session_metadata.single_model
        existing_conversation = await conversation_store.get_conversation(conversation_id) if conversation_id else None
        effective_conversation_id = existing_conversation.id if existing_conversation is not None else None
        if effective_conversation_id is None:
            created = await conversation_store.create_conversation(
                mode_hint=resolved_session_metadata.mode,
                session_metadata_json=resolved_session_metadata.model_dump(),
            )
            effective_conversation_id = created.id

        prepared = await self.prepare_turn(
            messages=messages,
            conversation_id=effective_conversation_id,
            run_kind="assistant",
            session_metadata=resolved_session_metadata,
            metadata_json={"model": resolved_model, "session_metadata": resolved_session_metadata.model_dump()},
            branch_key=branch_key,
            parent_event_id=parent_event_id,
        )
        return prepared, resolved_model

    async def _finalize_single_run_response(
        self,
        *,
        prepared: PreparedTurn,
        resolved_model: str,
        branch_key: str,
        error_text: str | None = None,
    ) -> ChatResponse:
        if error_text:
            await conversation_store.complete_message(
                prepared.conversation_id,
                run_id=prepared.run.id,
                role="assistant",
                content=error_text,
                actor_kind="assistant",
                event_type="conversation.assistant.message.completed",
                author_label=resolved_model,
                payload_json={"model": resolved_model, "content": error_text},
                branch_key=branch_key,
            )
            return ChatResponse(
                conversation_id=prepared.conversation_id,
                run_id=prepared.run.id,
                model=resolved_model,
                content=error_text,
                error=error_text,
            )

        branch_messages = await conversation_read_model.list_messages_for_branch(
            prepared.conversation_id,
            branch_key=branch_key,
            final_only=True,
        )
        assistant_message = next(
            (
                message
                for message in reversed(branch_messages)
                if message.run_id == prepared.run.id and message.role == "assistant"
            ),
            None,
        )
        run = await conversation_store.get_run(prepared.run.id)
        if assistant_message is None:
            fallback = "The model returned no reply."
            if run is not None and run.status == ConversationRunStatus.failed:
                fallback = str(run.error or "The chat reply failed.")
            _, created_message = await conversation_store.complete_message(
                prepared.conversation_id,
                run_id=prepared.run.id,
                role="assistant",
                content=fallback,
                actor_kind="assistant",
                event_type="conversation.assistant.message.completed",
                author_label=resolved_model,
                payload_json={"model": resolved_model, "content": fallback},
                branch_key=branch_key,
            )
            assistant_message = created_message

        return ChatResponse(
            conversation_id=prepared.conversation_id,
            run_id=prepared.run.id,
            model=resolved_model,
            content=assistant_message.content,
        )

    def _message_to_segment(self, message: Message, *, cache_candidate: bool = False, stable: bool = False) -> PromptSegment:
        role = message.role.value if hasattr(message.role, "value") else str(message.role)
        return PromptSegment(role=role, text=message.content, cache_candidate=cache_candidate, stable=stable)

    async def _record_llm_metrics(
        self,
        *,
        conversation_id: str,
        run_id: str,
        branch_key: str,
        model: str,
        metrics: PromptMetrics | None,
        request_kind: str,
    ) -> None:
        if metrics is None:
            return
        payload = {
            "request_kind": request_kind,
            "model": model,
            **metrics.model_dump(),
        }
        metrics_event = await conversation_store.append_event(
            conversation_id,
            run_id=run_id,
            actor_kind="system",
            event_type="llm.request.completed",
            payload_json=payload,
            branch_key=branch_key,
        )
        await conversation_store.attach_artifact(
            conversation_id,
            run_id=run_id,
            source_event_id=metrics_event.id,
            artifact_type="llm.prompt.metrics",
            mime_type="application/json",
            content_json=payload,
        )

    async def _build_model_prompt(
        self,
        *,
        conversation_id: str,
        branch_key: str,
        transient_messages: list[Message],
        model: str,
        run_id: str,
    ) -> PromptBuildResult:
        transient_system_messages = _transient_system_messages(transient_messages)
        stored_messages = await conversation_read_model.list_messages_for_branch(
            conversation_id,
            branch_key=branch_key,
            final_only=True,
        )
        persisted_records = [
            message
            for message in stored_messages
            if message.role in {"system", "user", "assistant"}
        ]
        async def _record_compaction_metrics(**kwargs) -> None:
            await self._record_llm_metrics(
                conversation_id=kwargs["conversation_id"],
                run_id=kwargs["run_id"],
                branch_key=branch_key,
                model=kwargs["model"],
                metrics=kwargs["metrics"],
                request_kind=kwargs["request_kind"],
            )

        prompt_context = await build_conversation_prompt_context(
            conversation_id=conversation_id,
            run_id=run_id,
            branch_key=branch_key,
            model=model,
            persisted_records=persisted_records,
            record_llm_metrics=_record_compaction_metrics,
            chat_completion_fn=chat_completion,
        )
        persisted_messages = prompt_context.messages
        messages = [*transient_system_messages, *persisted_messages]
        prompt_segments = [
            *[
                self._message_to_segment(message, cache_candidate=True, stable=True)
                for message in transient_system_messages
            ],
            *[self._message_to_segment(message) for message in persisted_messages],
        ]
        return PromptBuildResult(
            messages=messages,
            prompt_segments=prompt_segments,
            time_based_microcompact_applied=prompt_context.time_based_microcompact_applied,
        )

    async def _record_prompt_snapshot(
        self,
        *,
        conversation_id: str,
        run_id: str,
        branch_key: str,
        messages: list[Message],
    ) -> None:
        snapshot_event = await conversation_store.append_event(
            conversation_id,
            run_id=run_id,
            actor_kind="system",
            event_type="context.snapshot.created",
            payload_json={"message_count": len(messages), "branch_key": branch_key},
            branch_key=branch_key,
        )
        await conversation_store.attach_artifact(
            conversation_id,
            run_id=run_id,
            source_event_id=snapshot_event.id,
            artifact_type="prompt.snapshot",
            mime_type="application/json",
            content_json={"messages": _serialize_messages(messages)},
        )

    async def prepare_turn(
        self,
        *,
        messages: list[Message],
        conversation_id: str | None,
        run_kind: str,
        session_metadata: ConversationSessionMetadata,
        metadata_json: dict | None = None,
        branch_key: str = MAIN_BRANCH_KEY,
        parent_event_id: str | None = None,
    ) -> PreparedTurn:
        conversation = await conversation_store.get_conversation(conversation_id) if conversation_id else None
        if conversation is None:
            conversation = await conversation_store.create_conversation(
                mode_hint=session_metadata.mode,
                session_metadata_json=session_metadata.model_dump(),
            )

        run = await conversation_store.start_run(
            conversation.id,
            kind=run_kind,
            metadata_json={**(metadata_json or {}), "branch_key": branch_key},
        )
        async with _owned_run(run.id, "Turn preparation"):
            user_message = _latest_user_message(messages)
            await conversation_store.append_user_message(
                conversation.id,
                run_id=run.id,
                content=user_message.content,
                branch_key=branch_key,
                parent_event_id=parent_event_id,
            )
        return PreparedTurn(
            conversation_id=conversation.id,
            run=run,
            session_metadata=session_metadata,
        )

    async def continue_run(self, run_id: str, *, cancel_event: asyncio.Event | None = None,
                           stream_callback: StreamCallback | None = None) -> None:
        """Produce the assistant reply for a prepared single-model run (run_supervisor entry)."""
        run = await conversation_store.get_run(run_id)
        if run is None or run.status != ConversationRunStatus.running:
            return
        metadata = dict(run.metadata_json or {})
        branch_key = str(metadata.get("branch_key") or MAIN_BRANCH_KEY)
        model = str(metadata.get("model") or settings.task_agent_model)
        async with _owned_run(run_id, "Chat"):
            prompt = await self._build_model_prompt(
                conversation_id=run.conversation_id,
                branch_key=branch_key,
                transient_messages=[],
                model=model,
                run_id=run_id,
            )

            async def on_delta(text: str) -> None:
                if stream_callback is not None and text:
                    await stream_callback({"type": "chunk", "content": text})

            response = await chat_completion_stream_response(
                prompt.messages,
                model,
                session_id=_message_session_id(run.conversation_id, branch_key),
                prompt_segments=prompt.prompt_segments,
                on_content_delta=on_delta,
            )
            await self._record_llm_metrics(
                conversation_id=run.conversation_id,
                run_id=run_id,
                branch_key=branch_key,
                model=model,
                metrics=response.prompt_metrics,
                request_kind="chat.single",
            )
            if response.error:
                raise RuntimeError(response.error)
            if not response.content.strip():
                raise RuntimeError("The model returned no reply.")
            await conversation_store.complete_message(
                run.conversation_id,
                run_id=run_id,
                role="assistant",
                content=response.content,
                actor_kind="assistant",
                event_type="conversation.assistant.message.completed",
                author_label=model,
                payload_json={"model": model, "content": response.content, "usage": response.usage},
                branch_key=branch_key,
            )
            await conversation_store.end_run(run_id, ConversationRunStatus.completed)

    async def resolve_session_metadata(
        self,
        *,
        conversation_id: str | None,
        session_metadata: ConversationSessionMetadata | None,
        council_mode: bool,
        model: str | None,
    ) -> tuple[str | None, ConversationSessionMetadata]:
        existing_conversation_id = conversation_id
        defaults = await chat_settings_service.get_defaults()
        if conversation_id:
            conversation = await conversation_store.get_conversation(conversation_id)
            if conversation is not None:
                resolved = chat_settings_service.resolve_conversation_metadata(conversation, defaults)
                return existing_conversation_id, resolved

        resolved = chat_settings_service.build_requested_metadata(
            defaults=defaults,
            session_metadata=session_metadata,
            council_mode=council_mode,
            model=model,
        )
        return existing_conversation_id, resolved

    async def stream_single_response(
        self,
        *,
        messages: list[Message],
        conversation_id: str | None,
        session_metadata: ConversationSessionMetadata | None,
        model: str | None,
        temperature: float,
        max_tokens: int,
        branch_key: str = MAIN_BRANCH_KEY,
        parent_event_id: str | None = None,
    ) -> StreamEnvelope:
        prepared, resolved_model = await self._prepare_single_run(
            messages=messages,
            conversation_id=conversation_id,
            session_metadata=session_metadata,
            model=model,
            branch_key=branch_key,
            parent_event_id=parent_event_id,
        )

        async def _event_stream() -> AsyncIterator[dict]:
            stream_updates: asyncio.Queue[dict] = asyncio.Queue()

            async def _emit_stream_update(payload: dict) -> None:
                await stream_updates.put(payload)

            run_task = run_supervisor.start(prepared.run.id, runner=self, stream_callback=_emit_stream_update)
            last_event_sequence = 0

            async def _drain_run_events() -> AsyncIterator[dict]:
                nonlocal last_event_sequence
                run_events = await conversation_store.list_events_for_run(prepared.conversation_id, prepared.run.id)
                for event in run_events:
                    if event.sequence <= last_event_sequence:
                        continue
                    last_event_sequence = event.sequence
                    if event.event_type not in STREAMED_CHAT_EVENT_TYPES:
                        continue
                    yield {
                        "type": "event",
                        "event": _conversation_event_payload(event),
                    }

            async def _drain_stream_updates() -> AsyncIterator[dict]:
                while True:
                    try:
                        update = stream_updates.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    yield update

            yield {
                "type": "metadata",
                "conversation_id": prepared.conversation_id,
                "run_id": prepared.run.id,
                "model": resolved_model,
            }
            error_text: str | None = None
            while True:
                async for stream_frame in _drain_stream_updates():
                    yield stream_frame

                async for event_frame in _drain_run_events():
                    yield event_frame

                if run_task.done():
                    try:
                        await run_task
                    except Exception as exc:
                        error_text = str(exc) or "The chat reply failed."
                    break

                try:
                    await asyncio.wait_for(asyncio.shield(run_task), timeout=0.15)
                except asyncio.TimeoutError:
                    continue
                except Exception as exc:
                    error_text = str(exc) or "The chat reply failed."
                    break

            async for stream_frame in _drain_stream_updates():
                yield stream_frame

            async for event_frame in _drain_run_events():
                yield event_frame

            response = await self._finalize_single_run_response(
                prepared=prepared,
                resolved_model=resolved_model,
                branch_key=branch_key,
                error_text=error_text,
            )

            async for event_frame in _drain_run_events():
                yield event_frame

            if response.error:
                yield {
                    "type": "error",
                    "message": response.error,
                    "conversation_id": response.conversation_id,
                    "run_id": response.run_id,
                }
                return
            yield {
                "type": "done",
                "conversation_id": response.conversation_id,
                "run_id": response.run_id,
            }

        return StreamEnvelope(
            conversation_id=prepared.conversation_id,
            run_id=prepared.run.id,
            events=_event_stream(),
        )

    async def run_single_response(
        self,
        *,
        messages: list[Message],
        conversation_id: str | None,
        session_metadata: ConversationSessionMetadata | None,
        model: str | None,
        temperature: float,
        max_tokens: int,
        branch_key: str = MAIN_BRANCH_KEY,
        parent_event_id: str | None = None,
    ) -> ChatResponse:
        prepared, resolved_model = await self._prepare_single_run(
            messages=messages,
            conversation_id=conversation_id,
            session_metadata=session_metadata,
            model=model,
            branch_key=branch_key,
            parent_event_id=parent_event_id,
        )
        error_text: str | None = None
        try:
            await asyncio.shield(run_supervisor.start(prepared.run.id, runner=self))
        except Exception as exc:
            error_text = str(exc) or "The chat reply failed."

        return await self._finalize_single_run_response(
            prepared=prepared,
            resolved_model=resolved_model,
            branch_key=branch_key,
            error_text=error_text,
        )

    async def run_council_response(
        self,
        *,
        messages: list[Message],
        conversation_id: str | None,
        session_metadata: ConversationSessionMetadata | None,
        temperature: float,
        max_tokens: int,
        models: list[str] | None = None,
        synthesizer_model: str | None = None,
        branch_key: str = MAIN_BRANCH_KEY,
        parent_event_id: str | None = None,
    ) -> dict:
        _, resolved_session_metadata = await self.resolve_session_metadata(
            conversation_id=conversation_id,
            session_metadata=session_metadata,
            council_mode=True,
            model=None,
        )
        council_models = models or resolved_session_metadata.council_models or settings.council_model_list
        synth_model = synthesizer_model or resolved_session_metadata.synthesizer_model or settings.synthesizer_model
        prepared = await self.prepare_turn(
            messages=messages,
            conversation_id=conversation_id,
            run_kind="council",
            session_metadata=resolved_session_metadata,
            metadata_json={
                "models": council_models,
                "synthesizer_model": synth_model,
                "session_metadata": resolved_session_metadata.model_dump(),
            },
            branch_key=branch_key,
            parent_event_id=parent_event_id,
        )
        async with _owned_run(prepared.run.id, "Council"):
            return await self._council_turn(
                prepared,
                messages=messages,
                council_models=council_models,
                synth_model=synth_model,
                temperature=temperature,
                max_tokens=max_tokens,
                branch_key=branch_key,
            )

    async def _council_turn(
        self,
        prepared: PreparedTurn,
        *,
        messages: list[Message],
        council_models: list[str],
        synth_model: str,
        temperature: float,
        max_tokens: int,
        branch_key: str,
    ) -> dict:
        prompt_result = await self._build_model_prompt(
            conversation_id=prepared.conversation_id,
            branch_key=branch_key,
            transient_messages=messages,
            model=council_models[0],
            run_id=prepared.run.id,
        )
        await self._record_prompt_snapshot(
            conversation_id=prepared.conversation_id,
            run_id=prepared.run.id,
            branch_key=branch_key,
            messages=prompt_result.messages,
        )

        async def _complete_model(model_name: str) -> ModelResponse:
            await conversation_store.append_event(
                prepared.conversation_id,
                run_id=prepared.run.id,
                actor_kind="council_model",
                event_type="council.model.started",
                payload_json={"model": model_name},
                branch_key=branch_key,
            )
            response = await chat_completion(
                prompt_result.messages,
                model_name,
                temperature,
                max_tokens,
                session_id=_message_session_id(prepared.conversation_id, branch_key),
                prompt_segments=prompt_result.prompt_segments,
            )
            prompt_metrics = ensure_prompt_metrics(
                messages=prompt_result.messages,
                model=model_name,
                session_id=_message_session_id(prepared.conversation_id, branch_key),
                usage=getattr(response, "usage", None),
                prompt_metrics=getattr(response, "prompt_metrics", None),
            )
            if prompt_result.time_based_microcompact_applied:
                prompt_metrics = prompt_metrics.model_copy(update={"time_based_microcompact_applied": True})
            await self._record_llm_metrics(
                conversation_id=prepared.conversation_id,
                run_id=prepared.run.id,
                branch_key=branch_key,
                model=model_name,
                metrics=prompt_metrics,
                request_kind="council.model",
            )
            completed_event = await conversation_store.append_event(
                prepared.conversation_id,
                run_id=prepared.run.id,
                actor_kind="council_model",
                event_type="council.model.completed",
                payload_json=response.model_dump(),
                branch_key=branch_key,
            )
            await conversation_store.attach_artifact(
                prepared.conversation_id,
                run_id=prepared.run.id,
                source_event_id=completed_event.id,
                artifact_type="council.model.response",
                mime_type="application/json",
                content_json=response.model_dump(),
            )
            return response

        responses = list(await asyncio.gather(*[_complete_model(model) for model in council_models]))

        synthesis_prompt = _build_synthesis_prompt(prompt_result.messages, responses)
        synthesis_started = await conversation_store.append_event(
            prepared.conversation_id,
            run_id=prepared.run.id,
            actor_kind="synthesizer",
            event_type="council.synthesis.started",
            payload_json={"model": synth_model},
            branch_key=branch_key,
        )
        await conversation_store.attach_artifact(
            prepared.conversation_id,
            run_id=prepared.run.id,
            source_event_id=synthesis_started.id,
            artifact_type="council.synthesis.prompt",
            mime_type="text/plain",
            content_text=synthesis_prompt,
        )
        synthesized = await chat_completion(
            [Message(role=Role.user, content=synthesis_prompt)],
            synth_model,
            0.5,
            max_tokens,
            session_id=_message_session_id(prepared.conversation_id, branch_key),
        )
        synthesis_prompt_metrics = ensure_prompt_metrics(
            messages=[Message(role=Role.user, content=synthesis_prompt)],
            model=synth_model,
            session_id=_message_session_id(prepared.conversation_id, branch_key),
            usage=getattr(synthesized, "usage", None),
            prompt_metrics=getattr(synthesized, "prompt_metrics", None),
        )
        await self._record_llm_metrics(
            conversation_id=prepared.conversation_id,
            run_id=prepared.run.id,
            branch_key=branch_key,
            model=synth_model,
            metrics=synthesis_prompt_metrics,
            request_kind="council.synthesis",
        )
        total_usage = aggregate_usage([
            *[response.usage for response in responses],
            synthesized.usage,
        ])
        if synthesized.error:
            await conversation_store.end_run(prepared.run.id, ConversationRunStatus.failed, error=synthesized.error)
            return {
                "conversation_id": prepared.conversation_id,
                "run_id": prepared.run.id,
                "model_responses": [response.model_dump() for response in responses],
                "synthesized": "",
                "synthesizer_model": synth_model,
                "synthesizer_usage": synthesized.usage,
                "total_usage": aggregate_usage([response.usage for response in responses]),
                "error": synthesized.error,
            }

        completed_event, _ = await conversation_store.complete_message(
            prepared.conversation_id,
            run_id=prepared.run.id,
            role="assistant",
            content=synthesized.content,
            actor_kind="synthesizer",
            event_type="council.synthesis.completed",
            author_label=synth_model,
            payload_json={
                "model": synth_model,
                "content": synthesized.content,
                "usage": synthesized.usage,
            },
            branch_key=branch_key,
            council={
                "run_id": prepared.run.id,
                "model_responses": [
                    response.model_dump(include={"model", "content", "usage", "error"}) for response in responses
                ],
                "synthesized": synthesized.content,
                "synthesizer_model": synth_model,
                "synthesizer_usage": synthesized.usage,
                "total_usage": total_usage,
            },
        )
        await conversation_store.attach_artifact(
            prepared.conversation_id,
            run_id=prepared.run.id,
            source_event_id=completed_event.id,
            artifact_type="council.synthesis.response",
            mime_type="application/json",
            content_json={
                "model": synth_model,
                "content": synthesized.content,
                "usage": synthesized.usage,
            },
        )
        await conversation_store.end_run(prepared.run.id, ConversationRunStatus.completed)
        return {
            "conversation_id": prepared.conversation_id,
            "run_id": prepared.run.id,
            "model_responses": [response.model_dump() for response in responses],
            "synthesized": synthesized.content,
            "synthesizer_model": synth_model,
            "synthesizer_usage": synthesized.usage,
            "total_usage": total_usage,
        }


chat_orchestrator = ChatOrchestrator()
