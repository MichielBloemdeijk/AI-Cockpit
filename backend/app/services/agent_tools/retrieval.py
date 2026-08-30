"""Read-only durable-memory tools exposed to brain agents, never the shell."""
from __future__ import annotations

import json
from typing import Any

from app.services.agent_tools.base import AgentTool, ToolExecutionContext, ToolExecutionResult, _tool_schema
from app.services.brain_retrieval import search_conversations, search_wiki


def _schema(description: str) -> dict[str, Any]:
    return _tool_schema(
        {
            "query": {
                "type": "string",
                "description": description,
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "description": "Maximum number of results. Defaults to 8.",
            },
        }
    )


async def _wiki_search(_context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolExecutionResult:
    results = search_wiki(
        str(arguments.get("query") or "") or None,
        limit=int(arguments.get("limit") or 8),
    )
    return ToolExecutionResult(
        tool="wiki_search",
        output=json.dumps(results, ensure_ascii=False, default=str),
        metadata={"count": len(results), "read_only": True},
    )


async def _conversation_search(_context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolExecutionResult:
    results = await search_conversations(
        str(arguments.get("query") or "") or None,
        limit=int(arguments.get("limit") or 8),
    )
    return ToolExecutionResult(
        tool="conversation_search",
        output=json.dumps(results, ensure_ascii=False, default=str),
        metadata={"count": len(results), "read_only": True},
    )


RETRIEVAL_TOOLS = [
    AgentTool(
        name="wiki_search",
        description=(
            "Browse recent approved Wiki documents or search them with exact/substring matching. "
            "Results include file and review provenance. This tool cannot write or approve Wiki content."
        ),
        input_schema=_schema("Optional exact or substring query. Omit it to browse recent Wiki documents."),
        executor=_wiki_search,
        read_only=True,
        allows_parallel=True,
    ),
    AgentTool(
        name="conversation_search",
        description=(
            "Browse recent durable conversation history or search it with exact/substring matching. "
            "Search covers standard chats and closed Jarvis daily logs; the current live Jarvis log is excluded."
        ),
        input_schema=_schema("Optional exact or substring query. Omit it to browse recent conversations."),
        executor=_conversation_search,
        read_only=True,
        allows_parallel=True,
    ),
]


__all__ = ["RETRIEVAL_TOOLS"]
