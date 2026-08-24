"""Server-authoritative primary voice conversation lifecycle."""
from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from app.services.chat_settings import chat_settings_service
from app.services.conversation_store import ConversationRecord, conversation_store


async def get_or_create_primary() -> ConversationRecord:
    existing = await conversation_store.get_primary_voice_conversation()
    if existing is not None:
        return existing
    defaults = await chat_settings_service.get_defaults()
    try:
        return await conversation_store.create_conversation(
            title="Jarvis",
            mode_hint="voice",
            session_metadata_json=defaults.model_dump(),
            is_primary_voice=True,
        )
    except IntegrityError:
        # Another browser/device won the race to create the singleton.
        existing = await conversation_store.get_primary_voice_conversation()
        if existing is None:
            raise
        return existing


async def reset_primary() -> ConversationRecord:
    current = await get_or_create_primary()
    active_tasks = await conversation_store.list_runs(current.id)
    active_statuses = {"queued", "running", "paused", "cancelling"}
    blockers = [run.id for run in active_tasks if run.status in active_statuses]
    if blockers:
        raise RuntimeError("Cancel active runs before resetting the voice conversation: " + ", ".join(blockers))
    await conversation_store.set_primary_voice(current.id, False)
    await conversation_store.archive_conversation(current.id)
    return await get_or_create_primary()
