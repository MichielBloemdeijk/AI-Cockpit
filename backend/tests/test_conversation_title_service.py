from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.db.session import initialize_engine
from app.services.conversation_store import conversation_store
from app.services.conversation_title_service import conversation_title_service
import app.services.conversation_title_service as conversation_title_service_module
from app.services.chat_settings import chat_settings_service


@pytest.mark.asyncio
async def test_conversation_title_service_generates_and_stores_title(monkeypatch: pytest.MonkeyPatch):
    initialize_engine()

    conversation = await conversation_store.create_conversation(mode_hint="single")

    async def _fake_task_model() -> str:
        return "moonshotai/kimi-k2.6"

    async def _fake_structured(*_args, **_kwargs):
        return {"title": "Launch Checklist"}, SimpleNamespace(content='{"title":"Launch Checklist"}', usage=None)

    monkeypatch.setattr(chat_settings_service, "get_task_agent_model", _fake_task_model)
    monkeypatch.setattr(conversation_title_service_module, "chat_completion_structured", _fake_structured)

    await conversation_title_service.generate_and_store_title(conversation.id, "Please help me plan a launch checklist for tomorrow")

    updated = await conversation_store.get_conversation(conversation.id)

    assert updated is not None
    assert updated.title == "Launch Checklist"


@pytest.mark.asyncio
async def test_conversation_title_service_does_not_overwrite_existing_title(monkeypatch: pytest.MonkeyPatch):
    initialize_engine()

    conversation = await conversation_store.create_conversation(title="Keep Existing", mode_hint="single")
    structured_called = False

    async def _fake_task_model() -> str:
        return "moonshotai/kimi-k2.6"

    async def _fake_structured(*_args, **_kwargs):
        nonlocal structured_called
        structured_called = True
        return {"title": "Replacement Title"}, SimpleNamespace(content='{"title":"Replacement Title"}', usage=None)

    monkeypatch.setattr(chat_settings_service, "get_task_agent_model", _fake_task_model)
    monkeypatch.setattr(conversation_title_service_module, "chat_completion_structured", _fake_structured)

    await conversation_title_service.generate_and_store_title(conversation.id, "Rename this conversation")

    updated = await conversation_store.get_conversation(conversation.id)

    assert updated is not None
    assert updated.title == "Keep Existing"
    assert structured_called is True