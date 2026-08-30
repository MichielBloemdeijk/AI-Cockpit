from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from app.services.agent_tools import build_tool_context, execute_agent_tool, get_agent_tool_definitions
from app.services.brain_retrieval import search_conversations, search_wiki
from app.services.conversation_store import conversation_store
from app.services.jarvis_brain import ALLOWED_SHELL_TOOLS, restricted_brain_gateway
from app.services.jarvis_daily import jarvis_daily_service
from app.services.knowledge_memory import write_approved_memory
from app.services.memory_review_store import memory_review_store


@pytest.mark.asyncio
async def test_wiki_search_reads_only_approved_file_backed_knowledge() -> None:
    conversation = await conversation_store.create_conversation(title="Source chat")
    approved = await memory_review_store.create_memory_item(
        scope="decision",
        kind="decision",
        title="Use the blue deployment lane",
        content="Production deploys use the blue deployment lane.",
        source_conversation_id=conversation.id,
        confidence=0.9,
    )
    proposed = await memory_review_store.create_memory_item(
        scope="decision",
        kind="decision",
        title="Unreviewed red lane",
        content="The red lane has not been reviewed.",
        source_conversation_id=conversation.id,
        confidence=0.5,
    )
    path = write_approved_memory(approved)
    await memory_review_store.approve_memory_item(approved.id, knowledge_path=str(path))

    results = search_wiki("blue deployment", limit=5)

    assert len(results) == 1
    assert results[0]["provenance"]["memory_item_id"] == approved.id
    assert proposed.id not in json.dumps(results)


@pytest.mark.asyncio
async def test_conversation_search_finds_closed_daily_log_and_excludes_current_log() -> None:
    first = await jarvis_daily_service.accept_text_turn(
        "The earlier daily log contains the cobalt launch code.",
        accepted_at=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
        idempotency_key="retrieval-first",
    )
    current = await jarvis_daily_service.start_fresh_log(
        requested_at=datetime(2026, 8, 30, 12, 5, tzinfo=timezone.utc),
        idempotency_key="retrieval-fresh",
    )

    results = await search_conversations("cobalt launch", limit=5)

    assert [item["conversation_id"] for item in results] == [first.conversation.id]
    assert results[0]["conversation_kind"] == "jarvis_daily"
    assert results[0]["lifecycle_status"] == "closed"
    assert current.id not in {item["conversation_id"] for item in results}


@pytest.mark.asyncio
async def test_retrieval_tools_are_brain_only_and_read_only() -> None:
    conversation = await conversation_store.create_conversation(title="Archive tool fixture")
    run = await conversation_store.start_run(conversation.id, kind="assistant")
    await conversation_store.append_user_message(
        conversation.id,
        run_id=run.id,
        content="The cobalt launch belongs in durable history.",
    )
    definitions = {tool.name: tool for tool in get_agent_tool_definitions()}
    assert definitions["wiki_search"].read_only is True
    assert definitions["conversation_search"].read_only is True
    assert "wiki_search" not in ALLOWED_SHELL_TOOLS
    assert "conversation_search" not in ALLOWED_SHELL_TOOLS

    with pytest.raises(ValueError, match="not allowed"):
        await restricted_brain_gateway.execute(
            tool_name="conversation_search",
            arguments={"query": "archive"},
            call_id="shell-search",
            conversation_id="conversation",
            origin_turn_id=None,
        )

    context = build_tool_context("brain-test", ".cockpit/conversations/brain-test")
    result = await execute_agent_tool(
        context=context,
        tool="conversation_search",
        arguments={"query": "cobalt launch"},
    )
    assert result.metadata["read_only"] is True
    assert json.loads(result.output)[0]["conversation_id"] == conversation.id
