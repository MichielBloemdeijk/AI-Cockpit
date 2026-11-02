"""Read-only `system.*` collections: Cockpit's own data through the app data API.

Apps read them with a grant (`"read": ["system.tasks"]`) like any other
app's collection. Records have the usual `{key, value, revision, updated_at}`
shape; they are never written through the platform.

- `system.tasks`: Jarvis's tasks (key: task id).
- `system.results`: results tasks saved as app records (key: result id).
- `system.conversations`: conversations (key: conversation id); `get` adds the
  latest messages.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

COLLECTIONS = ("tasks", "results", "conversations")
SCAN_LIMIT = 500
MESSAGES_LIMIT = 100

# The shape of each collection's values, for builders and package checks.
SCHEMAS: dict[str, dict[str, Any]] = {
    "tasks": {"type": "object", "properties": {
        "goal": {"type": "string"}, "status": {"type": "string"}, "status_reason": {"type": ["string", "null"]},
        "summary": {"type": ["string", "null"]}, "origin": {"type": "string"}, "kind": {"type": "string"},
        "importance": {"type": "string"}, "created_at": {"type": "string"}, "updated_at": {"type": "string"},
        "finished_at": {"type": ["string", "null"]}}},
    "results": {"type": "object", "properties": {
        "task_id": {"type": "string"}, "app": {"type": "string"}, "collection": {"type": "string"},
        "key": {"type": "string"}, "title": {"type": "string"}, "url": {"type": "string"},
        "created_at": {"type": "string"}, "updated_at": {"type": "string"}}},
    "conversations": {"type": "object", "properties": {
        "title": {"type": ["string", "null"]}, "kind": {"type": "string"}, "status": {"type": "string"},
        "created_at": {"type": "string"}, "updated_at": {"type": "string"},
        "messages": {"type": "array", "description": "Only from get: the latest messages, oldest first",
                     "items": {"type": "object", "properties": {
                         "role": {"type": "string"}, "text": {"type": "string"}, "at": {"type": "string"}}}}}},
}


def _iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).isoformat()


def _record(key: str, value: dict[str, Any]) -> dict[str, Any]:
    return {"key": key, "value": value, "revision": 1, "updated_at": value.get("updated_at")}


async def _rows(name: str, key: str | None = None) -> list[dict[str, Any]]:
    from app.db.session import session_scope
    from app.db.tables import Conversation, Task, TaskResult

    async with session_scope() as session:
        if name == "tasks":
            statement = select(Task).order_by(Task.updated_at.desc()).limit(SCAN_LIMIT)
            if key is not None:
                statement = select(Task).where(Task.id == key)
            return [_record(task.id, {
                "goal": task.goal, "status": task.status, "status_reason": task.status_reason,
                "summary": task.terminal_summary, "origin": task.origin_surface, "kind": task.execution_kind,
                "importance": task.importance, "created_at": _iso(task.created_at),
                "updated_at": _iso(task.updated_at), "finished_at": _iso(task.finished_at),
            }) for task in (await session.execute(statement)).scalars()]
        if name == "results":
            from app.services.results.records import result_url
            statement = select(TaskResult).order_by(TaskResult.updated_at.desc()).limit(SCAN_LIMIT)
            if key is not None:
                statement = select(TaskResult).where(TaskResult.id == key)
            return [_record(result.id, {
                "task_id": result.task_id, "app": result.app_slug, "collection": result.collection,
                "key": result.record_key, "title": result.title,
                "url": result_url(result.app_slug, result.collection, result.record_key),
                "created_at": _iso(result.created_at), "updated_at": _iso(result.updated_at),
            }) for result in (await session.execute(statement)).scalars()]
        statement = select(Conversation).order_by(Conversation.updated_at.desc()).limit(SCAN_LIMIT)
        if key is not None:
            statement = select(Conversation).where(Conversation.id == key)
        records = [_record(conversation.id, {
            "title": conversation.title, "kind": conversation.conversation_kind,
            "status": conversation.lifecycle_status, "created_at": _iso(conversation.created_at),
            "updated_at": _iso(conversation.updated_at),
        }) for conversation in (await session.execute(statement)).scalars()]
        if key is not None and records:
            from app.db.tables import ConversationMessage
            messages = (await session.execute(select(ConversationMessage).where(
                ConversationMessage.conversation_id == key, ConversationMessage.is_final.is_(True))
                .order_by(ConversationMessage.created_at.desc()).limit(MESSAGES_LIMIT))).scalars()
            records[0]["value"]["messages"] = [{"role": message.role, "text": message.content,
                                                "at": _iso(message.created_at)} for message in reversed(list(messages))]
        return records


async def get(name: str, key: str) -> dict[str, Any] | None:
    found = await _rows(name, key)
    return found[0] if found else None


async def query(name: str, *, where: dict[str, Any] | None = None, order: str | None = None,
                limit: int = 50) -> list[dict[str, Any]]:
    """Equality filters and ordering over the latest SCAN_LIMIT records, newest first by default."""
    records = [record for record in await _rows(name)
               if all(record["value"].get(field) == value for field, value in (where or {}).items())]
    if order:
        field = order.removeprefix("-")
        records.sort(key=lambda record: str(record["key"] if field == "key" else record["value"].get(field) or ""),
                     reverse=order.startswith("-"))
    return records[:max(1, min(int(limit), 200))]
