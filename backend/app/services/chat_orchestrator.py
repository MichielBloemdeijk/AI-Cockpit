"""Conversation-aware chat orchestration above the transport-only LLM client."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
import re
from typing import AsyncIterator

from app.config import settings
from app.models.chat import ChatResponse, Message, ModelResponse, PromptMetrics
from app.models.settings import ConversationSessionMetadata
from app.services.agent_tools import build_tool_context
from app.services.agent_runner import agent_runner
from app.services.app_registry import app_registry_service, generated_app_contract
from app.services.chat_settings import chat_settings_service
from app.services.conversation_read_model import conversation_read_model
from app.services.conversation_store import EventRecord, MessageRecord, RunRecord, conversation_store
from app.services.conversation_workspace import MAIN_BRANCH_KEY
from app.services.llm import PromptSegment, aggregate_usage, chat_completion, chat_completion_stream, ensure_prompt_metrics, get_session_prompt_cache_status


CONTEXT_COMPACTION_TOOL_TOMBSTONE = "[Old tool result content cleared; see the conversation trace for the full output.]"
_APP_CONTEXT_HINT_RE = re.compile(r"(?:/apps/|started app task|app root:|entry page:|allowed write roots:|app has been updated)", re.IGNORECASE)
STREAMED_AGENT_EVENT_TYPES = {
    "conversation.task.started",
    "conversation.task.start_failed",
    "agent.run.started",
    "agent.plan.created",
    "agent.plan.feedback.skipped",
    "agent.thought.summary",
    "agent.progress.summary",
    "agent.tool.called",
    "agent.tool.completed",
    "agent.question.asked",
    "agent.question.answered",
    "agent.run.resumed",
    "agent.run.completed",
    "agent.run.failed",
}


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
        _, resolved_session_metadata = await self._resolve_session_metadata(
            conversation_id=conversation_id,
            session_metadata=session_metadata,
            council_mode=False,
            model=model,
        )
        resolved_model = resolved_session_metadata.single_model
        user_message = _latest_user_message(messages)
        goal = user_message.content.strip() or "Continue"

        existing_conversation = await conversation_store.get_conversation(conversation_id) if conversation_id else None
        effective_conversation_id = existing_conversation.id if existing_conversation is not None else None
        if effective_conversation_id is None:
            created = await conversation_store.create_conversation(
                mode_hint=resolved_session_metadata.mode,
                session_metadata_json=resolved_session_metadata.model_dump(),
            )
            effective_conversation_id = created.id

        agent_metadata = await self._build_agent_metadata(
            conversation_id=effective_conversation_id,
            goal=goal,
            session_metadata=resolved_session_metadata,
        )

        prepared = await self.prepare_turn(
            messages=messages,
            conversation_id=effective_conversation_id,
            run_kind="assistant",
            session_metadata=resolved_session_metadata,
            metadata_json=agent_metadata,
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
                author_label="agent",
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
        run_metadata = dict(run.metadata_json or {}) if run is not None else {}
        pending_question = run_metadata.get("pending_question") if isinstance(run_metadata.get("pending_question"), dict) else None
        if assistant_message is None and pending_question:
            question_text = str(pending_question.get("question") or "Please clarify how to proceed.").strip()
            _, created_message = await conversation_store.complete_message(
                prepared.conversation_id,
                run_id=prepared.run.id,
                role="assistant",
                content=question_text,
                actor_kind="assistant",
                event_type="conversation.assistant.message.completed",
                author_label="agent",
                payload_json={"model": resolved_model, "content": question_text},
                branch_key=branch_key,
            )
            assistant_message = created_message

        if assistant_message is None:
            fallback = "Agent run completed with no final message."
            if run is not None and str(run.status or "").lower() == "failed":
                fallback = str(run.error or "Agent run failed.")
            _, created_message = await conversation_store.complete_message(
                prepared.conversation_id,
                run_id=prepared.run.id,
                role="assistant",
                content=fallback,
                actor_kind="assistant",
                event_type="conversation.assistant.message.completed",
                author_label="agent",
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

    async def _conversation_has_recent_app_context(self, conversation_id: str, branch_key: str) -> bool:
        records = await conversation_read_model.list_messages_for_branch(
            conversation_id,
            branch_key=branch_key,
            final_only=True,
        )
        for record in reversed(records[-8:]):
            if _APP_CONTEXT_HINT_RE.search(record.content or ""):
                return True
        return False

    async def _build_agent_metadata(
        self,
        *,
        conversation_id: str,
        goal: str,
        session_metadata: ConversationSessionMetadata,
    ) -> dict[str, object]:
        task_agent_model = await chat_settings_service.get_task_agent_model()
        conversation = await conversation_store.get_conversation(conversation_id)
        workspace_path = conversation.workspace_path if conversation and conversation.workspace_path else ".cockpit/conversations"
        context = build_tool_context(conversation_id, workspace_path)
        allowed_roots = [root.as_posix() for root in context.read_roots]
        default_write_roots = [root.as_posix() for root in context.write_roots]

        payload: dict[str, object] = {}
        app_context: dict[str, object] | None = None
        write_roots = list(default_write_roots)
        recent_app = await app_registry_service.get_recent_app_for_conversation(conversation_id)
        if recent_app is not None:
            contract = generated_app_contract(recent_app.slug)
            app_payload = {
                "app_id": recent_app.id,
                "slug": recent_app.slug,
                "title": recent_app.title,
                "description": recent_app.description,
                "route_path": recent_app.route_path,
                "frontend_root": recent_app.frontend_root,
                "frontend_entry_path": recent_app.frontend_entry_path,
                "frontend_layout_path": contract.frontend_layout_path,
                "manifest_path": contract.manifest_path,
                "asset_root": contract.asset_root,
                "allowed_write_roots": contract.allowed_write_roots,
                "lease_conversation_id": recent_app.lease_conversation_id,
            }
            payload["app"] = app_payload
            app_context = {
                "mode": "app_builder",
                "app": app_payload,
            }
            write_roots = list(contract.allowed_write_roots)

        return {
            "run_kind": "agent",
            "title": "Agent chat run",
            "goal": goal,
            "payload": payload,
            "app_context": app_context,
            "skip_plan_feedback": True,
            "allowed_roots": allowed_roots,
            "write_roots": write_roots,
            "active_step": "Queued",
            "active_step_index": 0,
            "agent_status": "pending",
            "history": [],
            "plan": None,
            "summary": None,
            "pending_question": None,
            "final_summary": None,
            "result": None,
            "error": None,
            "model": task_agent_model,
            "session_metadata": session_metadata.model_dump(),
        }

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

    def _is_tool_like_message(self, message: MessageRecord) -> bool:
        return message.role == "assistant" and bool(message.author_label) and "/" not in str(message.author_label)

    def _microcompact_message_records(self, messages: list[MessageRecord], *, aggressive: bool = False) -> list[Message]:
        preserve_recent = max(
            0,
            settings.conversation_cache_cold_keep_recent_messages if aggressive else settings.conversation_microcompact_keep_recent_messages,
        )
        microcompact_limit = max(1, settings.conversation_microcompact_max_chars)
        guardrail_limit = max(microcompact_limit, settings.conversation_compaction_guardrail_max_chars)
        compact_before_index = max(0, len(messages) - preserve_recent)
        compacted: list[Message] = []
        for index, message in enumerate(messages):
            content = message.content
            if (
                index < compact_before_index
                and self._is_tool_like_message(message)
                and (aggressive or len(content) > microcompact_limit or len(content) > guardrail_limit)
            ):
                content = CONTEXT_COMPACTION_TOOL_TOMBSTONE
            elif len(content) > guardrail_limit:
                content = content[:guardrail_limit] + "\n...[truncated]"
            compacted.append(Message(role=message.role, content=content))
        return compacted

    def _format_compaction_source(self, messages: list[MessageRecord]) -> str:
        max_chars = max(200, settings.conversation_compaction_guardrail_max_chars)
        lines: list[str] = []
        for message in messages:
            content = message.content.strip()
            if self._is_tool_like_message(message) and len(content) > max_chars:
                content = CONTEXT_COMPACTION_TOOL_TOMBSTONE
            elif len(content) > max_chars:
                content = content[:max_chars] + "\n...[truncated]"
            author = f" ({message.author_label})" if message.author_label else ""
            lines.append(f"{message.role}{author}: {content}")
        return "\n\n".join(lines)

    async def _load_cached_compaction_summary(
        self,
        *,
        conversation_id: str,
        branch_key: str,
        through_message_id: str,
    ) -> str | None:
        artifacts = await conversation_read_model.list_artifacts_for_branch(conversation_id, branch_key=branch_key)
        summary_candidates = [
            artifact
            for artifact in artifacts
            if artifact.artifact_type == "context.compaction.summary"
            and isinstance(artifact.content_json, dict)
            and artifact.content_json.get("branch_key") == branch_key
            and artifact.content_json.get("through_message_id") == through_message_id
        ]
        if not summary_candidates:
            return None
        latest = max(summary_candidates, key=lambda item: item.created_at)
        payload = latest.content_json or {}
        summary = payload.get("summary")
        return None if not isinstance(summary, str) or not summary.strip() else summary.strip()

    async def _generate_compaction_summary(
        self,
        *,
        conversation_id: str,
        run_id: str,
        branch_key: str,
        model: str,
        source_messages: list[MessageRecord],
        through_message_id: str,
        tail_message_count: int,
    ) -> str | None:
        system_prompt = (
            "You are the AI-Cockpit conversation compactor. Summarize earlier conversation turns faithfully so later model calls can keep working context without replaying the full transcript. "
            "Keep concrete requirements, decisions, constraints, file paths, tool findings, and unresolved questions. Omit repetition and low-value chatter."
        )
        user_prompt = (
            "Summarize the following earlier conversation history for continued work. "
            "Write a concise but information-dense summary that can replace the original turns.\n\n"
            f"Transcript:\n{self._format_compaction_source(source_messages)}"
        )
        summary_messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]
        response = await chat_completion(
            summary_messages,
            model,
            temperature=0.2,
            max_tokens=settings.conversation_compaction_summary_max_tokens,
            session_id=f"{_message_session_id(conversation_id, branch_key)}:compact",
            prompt_segments=[
                PromptSegment(role="system", text=system_prompt, cache_candidate=True, stable=True),
                PromptSegment(role="user", text=user_prompt),
            ],
        )
        prompt_metrics = ensure_prompt_metrics(
            messages=summary_messages,
            model=model,
            session_id=f"{_message_session_id(conversation_id, branch_key)}:compact",
            usage=getattr(response, "usage", None),
            prompt_metrics=getattr(response, "prompt_metrics", None),
        )
        await self._record_llm_metrics(
            conversation_id=conversation_id,
            run_id=run_id,
            branch_key=branch_key,
            model=model,
            metrics=prompt_metrics,
            request_kind="context.compaction.summary",
        )
        if response.error:
            return None

        summary_text = response.content.strip()
        if not summary_text:
            return None

        compaction_event = await conversation_store.append_event(
            conversation_id,
            run_id=run_id,
            actor_kind="system",
            event_type="context.compacted",
            payload_json={
                "branch_key": branch_key,
                "through_message_id": through_message_id,
                "source_message_count": len(source_messages),
                "tail_message_count": tail_message_count,
            },
            branch_key=branch_key,
        )
        await conversation_store.attach_artifact(
            conversation_id,
            run_id=run_id,
            source_event_id=compaction_event.id,
            artifact_type="context.compaction.summary",
            mime_type="application/json",
            content_json={
                "branch_key": branch_key,
                "through_message_id": through_message_id,
                "source_message_count": len(source_messages),
                "tail_message_count": tail_message_count,
                "summary": summary_text,
            },
        )
        return summary_text

    async def _ensure_compaction_summary(
        self,
        *,
        conversation_id: str,
        run_id: str,
        branch_key: str,
        model: str,
        prefix_messages: list[MessageRecord],
        tail_message_count: int,
    ) -> str | None:
        if not prefix_messages:
            return None
        through_message_id = prefix_messages[-1].id
        cached = await self._load_cached_compaction_summary(
            conversation_id=conversation_id,
            branch_key=branch_key,
            through_message_id=through_message_id,
        )
        if cached is not None:
            return cached
        return await self._generate_compaction_summary(
            conversation_id=conversation_id,
            run_id=run_id,
            branch_key=branch_key,
            model=model,
            source_messages=prefix_messages,
            through_message_id=through_message_id,
            tail_message_count=tail_message_count,
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
        cache_status = get_session_prompt_cache_status(_message_session_id(conversation_id, branch_key), model)

        if cache_status.cache_cold and persisted_records:
            persisted_messages = self._microcompact_message_records(persisted_records, aggressive=True)
            cold_limit = max(1, settings.conversation_cache_cold_message_limit)
            if cold_limit > 0 and len(persisted_messages) > cold_limit:
                persisted_messages = persisted_messages[-cold_limit:]
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
                time_based_microcompact_applied=True,
            )

        if (
            settings.conversation_compaction_enabled
            and settings.conversation_compaction_trigger_messages > 0
            and len(persisted_records) > settings.conversation_compaction_trigger_messages
        ):
            keep_tail = min(max(1, settings.conversation_compaction_keep_tail_messages), len(persisted_records))
            prefix_records = persisted_records[:-keep_tail]
            tail_records = persisted_records[-keep_tail:]
            summary = await self._ensure_compaction_summary(
                conversation_id=conversation_id,
                run_id=run_id,
                branch_key=branch_key,
                model=model,
                prefix_messages=prefix_records,
                tail_message_count=len(tail_records),
            )
            if summary:
                tail_messages = self._microcompact_message_records(tail_records)
                messages = [
                    *transient_system_messages,
                    Message(role="system", content="Conversation summary for earlier turns:\n" + summary),
                    *tail_messages,
                ]
                prompt_segments = [
                    *[
                        self._message_to_segment(message, cache_candidate=True, stable=True)
                        for message in transient_system_messages
                    ],
                    PromptSegment(
                        role="system",
                        text="Conversation summary for earlier turns:\n" + summary,
                        cache_candidate=True,
                        stable=True,
                    ),
                    *[self._message_to_segment(message) for message in tail_messages],
                ]
                return PromptBuildResult(messages=messages, prompt_segments=prompt_segments)

        persisted_messages = self._microcompact_message_records(persisted_records)
        if settings.conversation_context_message_limit > 0 and len(persisted_messages) > settings.conversation_context_message_limit:
            persisted_messages = persisted_messages[-settings.conversation_context_message_limit :]
        messages = [*transient_system_messages, *persisted_messages]
        prompt_segments = [
            *[
                self._message_to_segment(message, cache_candidate=True, stable=True)
                for message in transient_system_messages
            ],
            *[self._message_to_segment(message) for message in persisted_messages],
        ]
        return PromptBuildResult(messages=messages, prompt_segments=prompt_segments)

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

    async def _resolve_session_metadata(
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

            continue_run_kwargs = {}
            if "stream_callback" in inspect.signature(agent_runner.continue_run).parameters:
                continue_run_kwargs["stream_callback"] = _emit_stream_update
            run_task = asyncio.create_task(agent_runner.continue_run(prepared.run.id, **continue_run_kwargs))
            last_event_sequence = 0

            async def _drain_run_events() -> AsyncIterator[dict]:
                nonlocal last_event_sequence
                run_events = await conversation_store.list_events_for_run(prepared.conversation_id, prepared.run.id)
                for event in run_events:
                    if event.sequence <= last_event_sequence:
                        continue
                    last_event_sequence = event.sequence
                    if event.event_type not in STREAMED_AGENT_EVENT_TYPES:
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
                    yield {
                        "type": "agent_stream",
                        "stream": update,
                    }

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
                        error_text = str(exc) or "Agent run failed."
                    break

                try:
                    await asyncio.wait_for(asyncio.shield(run_task), timeout=0.15)
                except asyncio.TimeoutError:
                    continue
                except Exception as exc:
                    error_text = str(exc) or "Agent run failed."
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

            if response.error:
                yield {
                    "type": "error",
                    "message": response.error,
                    "conversation_id": response.conversation_id,
                    "run_id": response.run_id,
                }
                return
            if response.content:
                yield {"type": "chunk", "content": response.content}
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
            await agent_runner.continue_run(prepared.run.id)
        except Exception as exc:
            error_text = str(exc) or "Agent run failed."

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
        _, resolved_session_metadata = await self._resolve_session_metadata(
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
            [Message(role="user", content=synthesis_prompt)],
            synth_model,
            0.5,
            max_tokens,
            session_id=_message_session_id(prepared.conversation_id, branch_key),
        )
        synthesis_prompt_metrics = ensure_prompt_metrics(
            messages=[Message(role="user", content=synthesis_prompt)],
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
            await conversation_store.mark_run_failed(prepared.run.id, synthesized.error)
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
        await conversation_store.mark_run_completed(prepared.run.id)
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