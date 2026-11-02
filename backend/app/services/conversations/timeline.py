"""Timeline items: the committed messages a conversation shows, chat or Jarvis."""
from __future__ import annotations

from typing import Any

TIMELINE_ITEM_EVENT_TYPE = "chat.timeline.item.committed"


def build_timeline_item(
    *,
    item_id: str,
    role: str,
    content: str,
    label: str | None = None,
    metadata: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    council: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "item_id": item_id,
        "role": role,
        "kind": "message",
        "content": content,
    }
    if label:
        payload["label"] = label
    if metadata:
        payload["metadata"] = metadata
    if usage:
        payload["usage"] = usage
    if council:
        payload["council"] = council
    return payload


def build_message_timeline_item(
    *,
    message_id: str,
    role: str,
    content: str,
    label: str | None = None,
    source_task_id: str | None = None,
) -> dict[str, Any]:
    """A durable message written outside a chat run (Jarvis text and voice turns)."""
    return build_timeline_item(
        item_id=f"{role}:{message_id}",
        role=role,
        content=content,
        label=label,
        metadata={"source_task_id": source_task_id} if source_task_id else None,
    )


def build_user_message_timeline_item(*, message_id: str, content: str) -> dict[str, Any]:
    return build_timeline_item(item_id=f"user:{message_id}", role="user", content=content)


def build_assistant_message_timeline_item(
    *,
    message_id: str,
    content: str,
    item_id: str | None = None,
    usage: dict[str, Any] | None = None,
    council: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_timeline_item(
        item_id=item_id or f"assistant:{message_id}",
        role="assistant",
        content=content,
        usage=usage,
        council=council,
    )


def extract_timeline_item_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    item_id = str(payload.get("item_id") or "").strip()
    role = str(payload.get("role") or "").strip()
    kind = str(payload.get("kind") or "").strip()
    if not item_id or not role or not kind:
        return None
    return dict(payload)
