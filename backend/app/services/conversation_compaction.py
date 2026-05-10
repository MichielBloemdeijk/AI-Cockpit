from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Sequence, TypeVar

from app.config import settings
from app.models.chat import Message, PromptMetrics
from app.services.conversation_read_model import conversation_read_model
from app.services.conversation_store import MessageRecord, conversation_store
from app.services.llm import PromptSegment, chat_completion, ensure_prompt_metrics, get_session_prompt_cache_status


TRUNCATION_SUFFIX = "\n...[truncated]"
CONTEXT_COMPACTION_TOOL_TOMBSTONE = "[Old tool result content cleared; see the conversation trace for the full output.]"
TASK_CONTEXT_COMPACTION_TOMBSTONE = "[Old tool result content cleared; see task artifacts for the full output.]"
CONVERSATION_SUMMARY_PREFIX = "Conversation summary for earlier turns:\n"
TASK_HISTORY_SUMMARY_PREFIX = "Summary of earlier task progress:\n"

EntryT = TypeVar("EntryT")
MetricsRecorder = Callable[..., Awaitable[Any]]
VisibleOutputRecorder = Callable[..., Awaitable[None]]
MetadataUpdater = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class ConversationPromptContext:
    messages: list[Message]
    time_based_microcompact_applied: bool = False


@dataclass(slots=True)
class AgentHistoryContext:
    history_summary: str | None
    rendered_history: list[dict[str, Any]]


def truncate_compacted_text(text: str, *, max_chars: int) -> str:
    limit = max(1, max_chars)
    if len(text) <= limit:
        return text
    return text[:limit] + TRUNCATION_SUFFIX


def microcompact_entries(
    entries: Sequence[EntryT],
    *,
    aggressive: bool,
    preserve_recent: int,
    microcompact_limit: int,
    guardrail_limit: int,
    is_tool_entry: Callable[[EntryT], bool],
    get_content: Callable[[EntryT], str | None],
    replace_content: Callable[[EntryT, str], EntryT],
    tombstone: str,
) -> list[EntryT]:
    if not entries:
        return []

    preserved_tail = max(0, preserve_recent)
    compact_before_index = max(0, len(entries) - preserved_tail)
    tool_output_limit = max(1, microcompact_limit)
    truncation_limit = max(tool_output_limit, guardrail_limit)

    compacted: list[EntryT] = []
    for index, entry in enumerate(entries):
        content = get_content(entry)
        if content is None:
            compacted.append(entry)
            continue

        rendered_content = content
        if (
            index < compact_before_index
            and is_tool_entry(entry)
            and (aggressive or len(content) > tool_output_limit or len(content) > truncation_limit)
        ):
            rendered_content = tombstone
        elif len(content) > truncation_limit:
            rendered_content = truncate_compacted_text(content, max_chars=truncation_limit)

        compacted.append(entry if rendered_content == content else replace_content(entry, rendered_content))
    return compacted


def _is_tool_like_message(message: MessageRecord) -> bool:
    return message.role == "assistant" and bool(message.author_label) and "/" not in str(message.author_label)


def microcompact_message_records(messages: list[MessageRecord], *, aggressive: bool = False) -> list[Message]:
    preserve_recent = max(
        0,
        settings.conversation_cache_cold_keep_recent_messages if aggressive else settings.conversation_microcompact_keep_recent_messages,
    )
    compacted_records = microcompact_entries(
        messages,
        aggressive=aggressive,
        preserve_recent=preserve_recent,
        microcompact_limit=settings.conversation_microcompact_max_chars,
        guardrail_limit=settings.conversation_compaction_guardrail_max_chars,
        is_tool_entry=_is_tool_like_message,
        get_content=lambda message: message.content,
        replace_content=lambda message, content: replace(message, content=content),
        tombstone=CONTEXT_COMPACTION_TOOL_TOMBSTONE,
    )
    return [Message(role=message.role, content=message.content) for message in compacted_records]


def _format_compaction_source(messages: list[MessageRecord]) -> str:
    max_chars = max(200, settings.conversation_compaction_guardrail_max_chars)
    lines: list[str] = []
    for message in messages:
        content = message.content.strip()
        if _is_tool_like_message(message) and len(content) > max_chars:
            content = CONTEXT_COMPACTION_TOOL_TOMBSTONE
        elif len(content) > max_chars:
            content = truncate_compacted_text(content, max_chars=max_chars)
        author = f" ({message.author_label})" if message.author_label else ""
        lines.append(f"{message.role}{author}: {content}")
    return "\n\n".join(lines)


async def _load_cached_compaction_summary(
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
    *,
    conversation_id: str,
    run_id: str,
    branch_key: str,
    model: str,
    source_messages: list[MessageRecord],
    through_message_id: str,
    tail_message_count: int,
    record_llm_metrics: MetricsRecorder,
    chat_completion_fn: Callable[..., Awaitable[Any]] | None = None,
) -> str | None:
    completion_fn = chat_completion if chat_completion_fn is None else chat_completion_fn
    system_prompt = (
        "You are the AI-Cockpit conversation compactor. Summarize earlier conversation turns faithfully so later model calls can keep working context without replaying the full transcript. "
        "Keep concrete requirements, decisions, constraints, file paths, tool findings, and unresolved questions. Omit repetition and low-value chatter."
    )
    user_prompt = (
        "Summarize the following earlier conversation history for continued work. "
        "Write a concise but information-dense summary that can replace the original turns.\n\n"
        f"Transcript:\n{_format_compaction_source(source_messages)}"
    )
    session_id = f"conversation:{conversation_id}:{branch_key}:compact"
    summary_messages = [
        Message(role="system", content=system_prompt),
        Message(role="user", content=user_prompt),
    ]
    response = await completion_fn(
        summary_messages,
        model,
        temperature=0.2,
        max_tokens=settings.conversation_compaction_summary_max_tokens,
        session_id=session_id,
        prompt_segments=[
            PromptSegment(role="system", text=system_prompt, cache_candidate=True, stable=True),
            PromptSegment(role="user", text=user_prompt),
        ],
    )
    prompt_metrics = ensure_prompt_metrics(
        messages=summary_messages,
        model=model,
        session_id=session_id,
        usage=getattr(response, "usage", None),
        prompt_metrics=getattr(response, "prompt_metrics", None),
    )
    await record_llm_metrics(
        conversation_id=conversation_id,
        run_id=run_id,
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
    *,
    conversation_id: str,
    run_id: str,
    branch_key: str,
    model: str,
    prefix_messages: list[MessageRecord],
    tail_message_count: int,
    record_llm_metrics: MetricsRecorder,
    chat_completion_fn: Callable[..., Awaitable[Any]] | None = None,
) -> str | None:
    if not prefix_messages:
        return None
    through_message_id = prefix_messages[-1].id
    cached = await _load_cached_compaction_summary(
        conversation_id=conversation_id,
        branch_key=branch_key,
        through_message_id=through_message_id,
    )
    if cached is not None:
        return cached
    return await _generate_compaction_summary(
        conversation_id=conversation_id,
        run_id=run_id,
        branch_key=branch_key,
        model=model,
        source_messages=prefix_messages,
        through_message_id=through_message_id,
        tail_message_count=tail_message_count,
        record_llm_metrics=record_llm_metrics,
        chat_completion_fn=chat_completion_fn,
    )


async def build_conversation_prompt_context(
    *,
    conversation_id: str,
    run_id: str,
    branch_key: str,
    model: str,
    persisted_records: list[MessageRecord],
    record_llm_metrics: MetricsRecorder,
    chat_completion_fn: Callable[..., Awaitable[Any]] | None = None,
) -> ConversationPromptContext:
    cache_status = get_session_prompt_cache_status(f"conversation:{conversation_id}:{branch_key}", model)

    if cache_status.cache_cold and persisted_records:
        persisted_messages = microcompact_message_records(persisted_records, aggressive=True)
        cold_limit = max(1, settings.conversation_cache_cold_message_limit)
        if cold_limit > 0 and len(persisted_messages) > cold_limit:
            persisted_messages = persisted_messages[-cold_limit:]
        return ConversationPromptContext(
            messages=persisted_messages,
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
        summary = await _ensure_compaction_summary(
            conversation_id=conversation_id,
            run_id=run_id,
            branch_key=branch_key,
            model=model,
            prefix_messages=prefix_records,
            tail_message_count=len(tail_records),
            record_llm_metrics=record_llm_metrics,
            chat_completion_fn=chat_completion_fn,
        )
        if summary:
            tail_messages = microcompact_message_records(tail_records)
            return ConversationPromptContext(
                messages=[Message(role="system", content=CONVERSATION_SUMMARY_PREFIX + summary), *tail_messages],
            )

    persisted_messages = microcompact_message_records(persisted_records)
    if settings.conversation_context_message_limit > 0 and len(persisted_messages) > settings.conversation_context_message_limit:
        persisted_messages = persisted_messages[-settings.conversation_context_message_limit :]
    return ConversationPromptContext(messages=persisted_messages)


def microcompact_history_entries(history: list[dict[str, Any]], *, aggressive: bool = False) -> list[dict[str, Any]]:
    preserve_recent = (
        settings.conversation_cache_cold_keep_recent_messages if aggressive else settings.conversation_microcompact_keep_recent_messages
    )
    return microcompact_entries(
        history,
        aggressive=aggressive,
        preserve_recent=preserve_recent,
        microcompact_limit=settings.conversation_microcompact_max_chars,
        guardrail_limit=settings.conversation_compaction_guardrail_max_chars,
        is_tool_entry=lambda item: item.get("kind") == "tool",
        get_content=lambda item: item.get("output") if isinstance(item.get("output"), str) else None,
        replace_content=lambda item, content: {**item, "output": content},
        tombstone=TASK_CONTEXT_COMPACTION_TOMBSTONE,
    )


def history_summary_source(history: list[dict[str, Any]]) -> str:
    compacted = microcompact_history_entries(history)
    lines: list[str] = []
    for index, item in enumerate(compacted, start=1):
        kind = item.get("kind", "step")
        if kind == "tool":
            output = str(item.get("output", "")).strip()
            guardrail_limit = max(settings.conversation_microcompact_max_chars, settings.conversation_compaction_guardrail_max_chars)
            output = truncate_compacted_text(output, max_chars=guardrail_limit)
            lines.append(
                f"{index}. tool {item.get('tool')} args={item.get('arguments', {})!r} ok={item.get('ok', True)} output={output}"
            )
        elif kind == "question_answer":
            lines.append(
                f"{index}. question '{item.get('question', '')}' answer='{item.get('answer', '')}'"
            )
        else:
            lines.append(f"{index}. {item!r}")
    return "\n".join(lines) if lines else "No prior steps yet."


async def ensure_history_compaction_summary(
    *,
    conversation_id: str,
    run_id: str,
    metadata: dict[str, Any],
    model: str,
    update_run_metadata: MetadataUpdater,
    record_llm_metrics: MetricsRecorder,
    record_llm_visible_output: VisibleOutputRecorder | None = None,
    chat_completion_fn: Callable[..., Awaitable[Any]] | None = None,
) -> str | None:
    history = list(metadata.get("history", []))
    if (
        not settings.conversation_compaction_enabled
        or settings.conversation_compaction_trigger_messages <= 0
        or len(history) <= settings.conversation_compaction_trigger_messages
    ):
        return None

    keep_tail = min(max(1, settings.conversation_compaction_keep_tail_messages), len(history))
    prefix_history = history[:-keep_tail]
    if not prefix_history:
        return None

    through_step = len(prefix_history)
    cached = metadata.get("history_compaction") or {}
    if cached.get("through_step") == through_step and isinstance(cached.get("summary"), str):
        return str(cached["summary"])

    completion_fn = chat_completion if chat_completion_fn is None else chat_completion_fn
    system_prompt = (
        "You are the AI-Cockpit task-progress compactor. Summarize earlier agent steps faithfully so later agent decisions can keep working context without replaying the full task history. "
        "Keep concrete findings, decisions, files touched, failures, and unresolved questions. Omit repetition and verbose tool output."
    )
    user_prompt = (
        "Summarize the following earlier task history for continued execution. "
        "Write a concise but information-dense summary that can replace the original steps.\n\n"
        f"Task goal:\n{metadata.get('goal', '')}\n\n"
        f"Earlier steps:\n{history_summary_source(prefix_history)}"
    )
    session_id = f"task:{conversation_id}:compact"
    summary_messages = [
        Message(role="system", content=system_prompt),
        Message(role="user", content=user_prompt),
    ]
    response = await completion_fn(
        summary_messages,
        model,
        temperature=0.2,
        max_tokens=settings.conversation_compaction_summary_max_tokens,
        session_id=session_id,
        prompt_segments=[
            PromptSegment(role="system", text=system_prompt, cache_candidate=True, stable=True),
            PromptSegment(role="user", text=user_prompt),
        ],
    )
    prompt_metrics = ensure_prompt_metrics(
        messages=summary_messages,
        model=model,
        session_id=session_id,
        usage=getattr(response, "usage", None),
        prompt_metrics=getattr(response, "prompt_metrics", None),
    )
    metrics_event_id = await record_llm_metrics(
        conversation_id=conversation_id,
        run_id=run_id,
        model=model,
        metrics=prompt_metrics,
        request_kind="task.agent.context.compaction.summary",
    )
    if record_llm_visible_output is not None:
        await record_llm_visible_output(
            conversation_id=conversation_id,
            run_id=run_id,
            source_event_id=metrics_event_id,
            request_kind="task.agent.context.compaction.summary",
            response=response,
        )
    if response.error:
        return None

    summary = response.content.strip()
    if not summary:
        return None

    metadata["history_compaction"] = {
        "through_step": through_step,
        "summary": summary,
        "tail_count": len(history) - through_step,
    }
    await update_run_metadata(run_id, metadata)
    compacted_event = await conversation_store.append_event(
        conversation_id,
        run_id=run_id,
        actor_kind="system",
        event_type="context.compacted",
        payload_json={
            "scope": "task.agent.history",
            "through_step": through_step,
            "source_item_count": len(prefix_history),
            "tail_item_count": len(history) - through_step,
        },
    )
    await conversation_store.attach_artifact(
        conversation_id,
        run_id=run_id,
        source_event_id=compacted_event.id,
        artifact_type="context.compaction.summary",
        mime_type="application/json",
        content_json={
            "scope": "task.agent.history",
            "through_step": through_step,
            "source_item_count": len(prefix_history),
            "tail_item_count": len(history) - through_step,
            "summary": summary,
        },
    )
    return summary


async def build_agent_history_context(
    *,
    conversation_id: str,
    run_id: str,
    metadata: dict[str, Any],
    model: str,
    update_run_metadata: MetadataUpdater,
    record_llm_metrics: MetricsRecorder,
    record_llm_visible_output: VisibleOutputRecorder | None = None,
    chat_completion_fn: Callable[..., Awaitable[Any]] | None = None,
) -> AgentHistoryContext:
    history = list(metadata.get("history", []))
    if not history:
        return AgentHistoryContext(history_summary=None, rendered_history=[])

    cache_status = get_session_prompt_cache_status(f"task:{conversation_id}", model)
    if cache_status.cache_cold:
        keep_tail = min(max(1, settings.conversation_cache_cold_keep_recent_messages), len(history))
        cached_summary = metadata.get("history_compaction") or {}
        through_step = max(0, len(history) - keep_tail)
        history_summary = None
        if cached_summary.get("through_step") == through_step:
            history_summary = str(cached_summary.get("summary") or "").strip() or None
        history_tail = history[-keep_tail:]
        return AgentHistoryContext(
            history_summary=history_summary,
            rendered_history=microcompact_history_entries(history_tail, aggressive=True),
        )

    history_summary = await ensure_history_compaction_summary(
        conversation_id=conversation_id,
        run_id=run_id,
        metadata=metadata,
        model=model,
        update_run_metadata=update_run_metadata,
        record_llm_metrics=record_llm_metrics,
        record_llm_visible_output=record_llm_visible_output,
        chat_completion_fn=chat_completion_fn,
    )
    if history_summary:
        keep_tail = min(max(1, settings.conversation_compaction_keep_tail_messages), len(history))
        history_tail = history[-keep_tail:]
    else:
        history_tail = history
    return AgentHistoryContext(
        history_summary=history_summary,
        rendered_history=microcompact_history_entries(history_tail),
    )