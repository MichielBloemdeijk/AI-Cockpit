from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import app.services.conversation_compaction as conversation_compaction
from app.models.chat import ModelResponse
from app.services.conversation_store import MessageRecord

from dataclasses import dataclass

from app.services.conversation_compaction import build_agent_history_context, build_conversation_prompt_context, microcompact_entries, truncate_compacted_text


@dataclass(slots=True)
class FakeEntry:
    kind: str
    content: str | None


def test_microcompact_entries_tombstones_older_tool_content_and_truncates_recent_tail():
    entries = [
        FakeEntry(kind="tool", content="tool output that should be replaced"),
        FakeEntry(kind="message", content="x" * 14),
        FakeEntry(kind="tool", content="y" * 14),
    ]

    compacted = microcompact_entries(
        entries,
        aggressive=False,
        preserve_recent=1,
        microcompact_limit=10,
        guardrail_limit=12,
        is_tool_entry=lambda entry: entry.kind == "tool",
        get_content=lambda entry: entry.content,
        replace_content=lambda entry, content: FakeEntry(kind=entry.kind, content=content),
        tombstone="[cleared]",
    )

    assert compacted[0].content == "[cleared]"
    assert compacted[1].content == "x" * 12 + "\n...[truncated]"
    assert compacted[2].content == "y" * 12 + "\n...[truncated]"


def test_truncate_compacted_text_leaves_short_values_unchanged():
    assert truncate_compacted_text("short", max_chars=20) == "short"
    assert truncate_compacted_text("123456", max_chars=4) == "1234\n...[truncated]"


@pytest.mark.asyncio
async def test_build_conversation_prompt_context_summarizes_prefix(monkeypatch):
    timestamp = datetime.now(timezone.utc)
    records = [
        MessageRecord(
            id="m1",
            conversation_id="c1",
            run_id="r1",
            source_event_id=None,
            role="user",
            author_label=None,
            content="Need help with compaction.",
            content_format="text/plain",
            is_final=True,
            created_at=timestamp,
        ),
        MessageRecord(
            id="m2",
            conversation_id="c1",
            run_id="r1",
            source_event_id=None,
            role="assistant",
            author_label="tool",
            content="tool output " * 20,
            content_format="text/plain",
            is_final=True,
            created_at=timestamp,
        ),
        MessageRecord(
            id="m3",
            conversation_id="c1",
            run_id="r1",
            source_event_id=None,
            role="assistant",
            author_label=None,
            content="Latest answer",
            content_format="text/plain",
            is_final=True,
            created_at=timestamp,
        ),
    ]

    monkeypatch.setattr(conversation_compaction.settings, "conversation_compaction_enabled", True)
    monkeypatch.setattr(conversation_compaction.settings, "conversation_compaction_trigger_messages", 2)
    monkeypatch.setattr(conversation_compaction.settings, "conversation_compaction_keep_tail_messages", 1)
    monkeypatch.setattr(conversation_compaction, "get_session_prompt_cache_status", lambda session_id, model: SimpleNamespace(cache_cold=False))

    async def _no_cached_artifacts(*args, **kwargs):
        return []

    async def _fake_chat_completion(messages, model, **kwargs):
        return ModelResponse(model=model, content="Summarized earlier context.")

    async def _record_metrics(**kwargs):
        return None

    async def _append_event(*args, **kwargs):
        return SimpleNamespace(id="event-1")

    async def _attach_artifact(*args, **kwargs):
        return SimpleNamespace(id="artifact-1")

    monkeypatch.setattr(conversation_compaction.conversation_read_model, "list_artifacts_for_branch", _no_cached_artifacts)
    monkeypatch.setattr(conversation_compaction, "chat_completion", _fake_chat_completion)
    monkeypatch.setattr(conversation_compaction.conversation_store, "append_event", _append_event)
    monkeypatch.setattr(conversation_compaction.conversation_store, "attach_artifact", _attach_artifact)

    context = await build_conversation_prompt_context(
        conversation_id="c1",
        run_id="r1",
        branch_key="main",
        model="anthropic/claude-sonnet-4-5",
        persisted_records=records,
        record_llm_metrics=_record_metrics,
    )

    assert context.time_based_microcompact_applied is False
    assert context.messages[0].role == "system"
    assert context.messages[0].content == "Conversation summary for earlier turns:\nSummarized earlier context."
    assert context.messages[1].content == "Latest answer"


@pytest.mark.asyncio
async def test_build_agent_history_context_generates_summary_and_tail(monkeypatch):
    metadata = {
        "goal": "Inspect the history handling.",
        "history": [
            {"kind": "tool", "tool": "workspace_search", "arguments": {"query": "foo"}, "ok": True, "output": "x" * 40},
            {"kind": "tool", "tool": "file_read", "arguments": {"path": "a.txt"}, "ok": True, "output": "y" * 40},
            {"kind": "tool", "tool": "file_write", "arguments": {"path": "b.txt"}, "ok": True, "output": "Latest"},
        ],
    }
    updates: list[dict[str, object]] = []

    monkeypatch.setattr(conversation_compaction.settings, "conversation_compaction_enabled", True)
    monkeypatch.setattr(conversation_compaction.settings, "conversation_compaction_trigger_messages", 2)
    monkeypatch.setattr(conversation_compaction.settings, "conversation_compaction_keep_tail_messages", 1)
    monkeypatch.setattr(conversation_compaction, "get_session_prompt_cache_status", lambda session_id, model: SimpleNamespace(cache_cold=False))

    async def _fake_chat_completion(messages, model, **kwargs):
        return ModelResponse(model=model, content="Earlier work summary")

    async def _update_run_metadata(run_id, next_metadata):
        updates.append(dict(next_metadata))

    async def _record_metrics(**kwargs):
        return "metrics-event"

    async def _record_visible(**kwargs):
        return None

    async def _append_event(*args, **kwargs):
        return SimpleNamespace(id="event-1")

    async def _attach_artifact(*args, **kwargs):
        return SimpleNamespace(id="artifact-1")

    monkeypatch.setattr(conversation_compaction, "chat_completion", _fake_chat_completion)
    monkeypatch.setattr(conversation_compaction.conversation_store, "append_event", _append_event)
    monkeypatch.setattr(conversation_compaction.conversation_store, "attach_artifact", _attach_artifact)

    context = await build_agent_history_context(
        conversation_id="c1",
        run_id="r1",
        metadata=metadata,
        model="anthropic/claude-sonnet-4-5",
        update_run_metadata=_update_run_metadata,
        record_llm_metrics=_record_metrics,
        record_llm_visible_output=_record_visible,
    )

    assert context.history_summary == "Earlier work summary"
    assert context.rendered_history == [metadata["history"][-1]]
    assert updates[-1]["history_compaction"]["summary"] == "Earlier work summary"