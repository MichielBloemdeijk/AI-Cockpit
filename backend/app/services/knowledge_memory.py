"""Writes approved conversation memories into the durable knowledge layer."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re

import yaml

from app.config import settings
from app.services.conversation_store import MemoryItemRecord


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized or "memory-item"


def _knowledge_root() -> Path:
    return settings.knowledge_notes_path


def _preferences_path() -> Path:
    return settings.knowledge_preferences_path


def _approved_note_path(memory_item: MemoryItemRecord) -> Path:
    date_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _knowledge_root() / f"{date_prefix}-{memory_item.kind}-{_slugify(memory_item.title)}.md"


def _append_preference(memory_item: MemoryItemRecord) -> Path:
    destination = _preferences_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if destination.exists():
        loaded = yaml.safe_load(destination.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            existing = loaded

    reviewed_preferences = existing.setdefault("reviewed_preferences", [])
    approved_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "memory_item_id": memory_item.id,
        "title": memory_item.title,
        "content": memory_item.content.strip(),
        "kind": memory_item.kind,
        "scope": memory_item.scope,
        "approved_at": approved_at,
        "source_conversation_id": memory_item.source_conversation_id,
        "source_event_id": memory_item.source_event_id,
    }
    for index, item in enumerate(reviewed_preferences):
        if item.get("memory_item_id") == memory_item.id:
            reviewed_preferences[index] = payload
            break
    else:
        reviewed_preferences.append(payload)
    destination.write_text(yaml.safe_dump(existing, sort_keys=False), encoding="utf-8")
    return destination


def write_approved_memory(memory_item: MemoryItemRecord) -> Path:
    if memory_item.scope == "preference" or memory_item.kind == "preference":
        return _append_preference(memory_item)

    destination = Path(memory_item.knowledge_path) if memory_item.knowledge_path else _approved_note_path(memory_item)
    destination.parent.mkdir(parents=True, exist_ok=True)
    approved_at = datetime.now(timezone.utc).isoformat()
    content = (
        "---\n"
        f"memory_item_id: {memory_item.id}\n"
        f"title: {memory_item.title}\n"
        f"kind: {memory_item.kind}\n"
        f"scope: {memory_item.scope}\n"
        f"approved_at: {approved_at}\n"
        f"source_conversation_id: {memory_item.source_conversation_id}\n"
        f"source_event_id: {memory_item.source_event_id or ''}\n"
        "tags: [reviewed-memory]\n"
        "---\n\n"
        f"{memory_item.content.strip()}\n"
    )
    destination.write_text(content, encoding="utf-8")
    return destination


def delete_approved_memory(memory_item: MemoryItemRecord) -> Path | None:
    if memory_item.scope == "preference" or memory_item.kind == "preference":
        destination = _preferences_path()
        if not destination.exists():
            return None
        loaded = yaml.safe_load(destination.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            return None
        reviewed_preferences = loaded.get("reviewed_preferences")
        if not isinstance(reviewed_preferences, list):
            return None
        loaded["reviewed_preferences"] = [
            item for item in reviewed_preferences if item.get("memory_item_id") != memory_item.id
        ]
        destination.write_text(yaml.safe_dump(loaded, sort_keys=False), encoding="utf-8")
        return destination

    destination = Path(memory_item.knowledge_path) if memory_item.knowledge_path else _approved_note_path(memory_item)
    if destination.exists():
        destination.unlink()
        return destination
    return None


def list_knowledge_documents() -> list[dict[str, str | None]]:
    documents: list[dict[str, str | None]] = []
    preferences_path = _preferences_path()
    if preferences_path.exists():
        documents.append(
            {
                "path": preferences_path.as_posix(),
                "title": "Preferences",
                "kind": "preferences",
                "content": preferences_path.read_text(encoding="utf-8"),
                "updated_at": datetime.fromtimestamp(preferences_path.stat().st_mtime, tz=timezone.utc).isoformat(),
            }
        )

    notes_root = _knowledge_root()
    if notes_root.exists():
        for note_path in sorted(notes_root.rglob("*.md")):
            documents.append(
                {
                    "path": note_path.as_posix(),
                    "title": note_path.stem,
                    "kind": "note",
                    "content": note_path.read_text(encoding="utf-8"),
                    "updated_at": datetime.fromtimestamp(note_path.stat().st_mtime, tz=timezone.utc).isoformat(),
                }
            )

    return documents