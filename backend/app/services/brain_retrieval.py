"""Read-only Wiki and conversation retrieval for the AI-Cockpit brain."""
from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from sqlalchemy import and_, or_, select

from app.db.session import session_scope
from app.db.tables import Conversation, ConversationMessage
from app.services.knowledge_memory import list_knowledge_documents


MAX_RESULTS = 20
RECENT_CONVERSATION_CANDIDATES = 200
MAX_SNIPPET_CHARS = 420


def _limit(value: int) -> int:
    return max(1, min(int(value), MAX_RESULTS))


def _normalized_query(value: str | None) -> str:
    return " ".join(str(value or "").split()).strip().casefold()


def _snippet(value: str, query: str, *, limit: int = MAX_SNIPPET_CHARS) -> str:
    compact = " ".join(str(value or "").split()).strip()
    if len(compact) <= limit:
        return compact
    if not query:
        return compact[: limit - 1].rstrip() + "…"
    start = compact.casefold().find(query)
    if start < 0:
        return compact[: limit - 1].rstrip() + "…"
    padding = max(0, (limit - len(query)) // 2)
    left = max(0, start - padding)
    right = min(len(compact), left + limit)
    left = max(0, right - limit)
    rendered = compact[left:right].strip()
    return ("…" if left else "") + rendered + ("…" if right < len(compact) else "")


def _provenance(content: str) -> dict[str, str | None]:
    def field(name: str) -> str | None:
        match = re.search(rf"(?m)^\s*{re.escape(name)}:\s*['\"]?([^'\"\r\n]+)", content)
        return None if match is None else match.group(1).strip() or None

    return {
        "memory_item_id": field("memory_item_id"),
        "source_conversation_id": field("source_conversation_id"),
        "source_event_id": field("source_event_id"),
        "approved_at": field("approved_at"),
    }


def search_wiki(query: str | None = None, *, limit: int = 8) -> list[dict[str, Any]]:
    """Browse recent approved documents or match an exact/substring query."""
    normalized = _normalized_query(query)
    ranked: list[tuple[int, float, dict[str, Any]]] = []
    for document in list_knowledge_documents():
        title = str(document.get("title") or "")
        path = str(document.get("path") or "")
        content = str(document.get("content") or "")
        haystack = f"{title}\n{path}\n{content}".casefold()
        if normalized:
            if title.casefold() == normalized:
                rank = 0
                match_kind = "exact_title"
            elif normalized in title.casefold():
                rank = 1
                match_kind = "title_substring"
            elif normalized in haystack:
                rank = 2
                match_kind = "content_substring"
            else:
                continue
        else:
            rank = 3
            match_kind = "recent"
        updated_at = str(document.get("updated_at") or "")
        try:
            recency = datetime.fromisoformat(updated_at).timestamp()
        except ValueError:
            recency = 0.0
        ranked.append(
            (
                rank,
                -recency,
                {
                    "title": title,
                    "kind": str(document.get("kind") or "note"),
                    "path": path,
                    "updated_at": updated_at or None,
                    "match_kind": match_kind,
                    "excerpt": _snippet(content, normalized),
                    "provenance": _provenance(content),
                },
            )
        )
    ranked.sort(key=lambda item: (item[0], item[1], item[2]["path"]))
    return [item[2] for item in ranked[: _limit(limit)]]


async def search_conversations(query: str | None = None, *, limit: int = 8) -> list[dict[str, Any]]:
    """Search standard chats and closed Jarvis logs without exposing live logs."""
    normalized = _normalized_query(query)
    result_limit = _limit(limit)
    async with session_scope() as session:
        candidates = list(
            (
                await session.execute(
                    select(Conversation)
                    .where(
                        or_(
                            Conversation.conversation_kind == "standard",
                            and_(
                                Conversation.conversation_kind == "jarvis_daily",
                                Conversation.lifecycle_status == "closed",
                            ),
                        )
                    )
                    .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
                    .limit(RECENT_CONVERSATION_CANDIDATES)
                )
            ).scalars().all()
        )
        ranked: list[tuple[int, float, dict[str, Any]]] = []
        for conversation in candidates:
            messages = list(
                (
                    await session.execute(
                        select(ConversationMessage)
                        .where(
                            ConversationMessage.conversation_id == conversation.id,
                            ConversationMessage.is_final.is_(True),
                            ConversationMessage.role.in_(["user", "assistant"]),
                        )
                        .order_by(ConversationMessage.created_at.asc(), ConversationMessage.id.asc())
                    )
                ).scalars().all()
            )
            title = str(conversation.title or "Untitled conversation")
            exact_message = next(
                (message for message in messages if normalized and message.content.strip().casefold() == normalized),
                None,
            )
            matching_messages = [
                message for message in messages if normalized and normalized in message.content.casefold()
            ]
            if normalized:
                if title.casefold() == normalized:
                    rank = 0
                    match_kind = "exact_title"
                elif exact_message is not None:
                    rank = 1
                    match_kind = "exact_message"
                elif normalized in title.casefold():
                    rank = 2
                    match_kind = "title_substring"
                elif matching_messages:
                    rank = 3
                    match_kind = "message_substring"
                else:
                    continue
                selected_messages = matching_messages or messages[-3:]
            else:
                rank = 4
                match_kind = "recent"
                selected_messages = messages[-3:]
            updated_at = conversation.updated_at
            recency = updated_at.timestamp() if updated_at is not None else 0.0
            ranked.append(
                (
                    rank,
                    -recency,
                    {
                        "conversation_id": conversation.id,
                        "title": title,
                        "conversation_kind": conversation.conversation_kind,
                        "lifecycle_status": conversation.lifecycle_status,
                        "jarvis_day_key": (
                            conversation.jarvis_day_key.isoformat()
                            if conversation.jarvis_day_key is not None
                            else None
                        ),
                        "jarvis_day_sequence": conversation.jarvis_day_sequence,
                        "archived": conversation.archived_at is not None,
                        "updated_at": updated_at.isoformat(),
                        "match_kind": match_kind,
                        "deep_link": f"/chat?conversation={conversation.id}",
                        "excerpts": [
                            {
                                "message_id": message.id,
                                "role": message.role,
                                "content": _snippet(message.content, normalized),
                                "created_at": message.created_at.isoformat(),
                            }
                            for message in selected_messages[-4:]
                        ],
                    },
                )
            )
    ranked.sort(key=lambda item: (item[0], item[1], item[2]["conversation_id"]))
    return [item[2] for item in ranked[:result_limit]]


__all__ = ["search_conversations", "search_wiki"]
