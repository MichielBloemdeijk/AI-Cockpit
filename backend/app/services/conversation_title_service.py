"""Asynchronous model-based conversation title generation."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.db.repositories.conversations import ConversationRepository
from app.db.session import session_scope
from app.models.chat import Message
from app.services.llm import PromptSegment, chat_completion_structured

logger = logging.getLogger(__name__)


def _normalize_title(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.replace("\n", " ").split()).strip().strip('"\'')
    if not normalized:
        return None
    return normalized[:80]


class ConversationTitleService:
    def __init__(self) -> None:
        self._pending_tasks: dict[str, asyncio.Task[None]] = {}

    async def generate_title(self, user_content: str) -> str | None:
        normalized_prompt = " ".join(user_content.split()).strip()
        if not normalized_prompt:
            return None

        from app.services.chat_settings import chat_settings_service

        model = await chat_settings_service.get_task_agent_model()
        system_prompt = (
            "You write short conversation titles for AI Cockpit. Return JSON only. "
            "Summarize the user's request in 2-6 natural title-case words. "
            "Do not copy the full prompt, do not start with filler like Create, Build, Help, or Question, "
            "and do not use surrounding quotes."
        )
        user_prompt = (
            f"First user message:\n{normalized_prompt}\n\n"
            "Respond with a concise conversation title that would look natural in a sidebar."
        )

        payload, _response = await chat_completion_structured(
            [
                Message(role="system", content=system_prompt),
                Message(role="user", content=user_prompt),
            ],
            model,
            schema_name="conversation_title",
            json_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 80,
                    },
                },
                "required": ["title"],
                "additionalProperties": False,
            },
            temperature=0.2,
            max_tokens=80,
            prompt_segments=[
                PromptSegment(role="system", text=system_prompt, cache_candidate=True, stable=True),
                PromptSegment(role="user", text=user_prompt),
            ],
        )
        return _normalize_title(payload.get("title"))

    async def generate_and_store_title(self, conversation_id: str, user_content: str) -> None:
        try:
            title = await self.generate_title(user_content)
            if not title:
                return
            async with session_scope() as session:
                repo = ConversationRepository(session)
                conversation = await repo.get_conversation(conversation_id)
                if conversation is None or conversation.title:
                    return
                await repo.touch_conversation(conversation_id, title=title)
        except Exception:
            logger.exception("Failed to generate conversation title for %s", conversation_id)

    def schedule_title_generation(self, conversation_id: str, user_content: str) -> None:
        existing_task = self._pending_tasks.get(conversation_id)
        if existing_task is not None and not existing_task.done():
            return

        task = asyncio.create_task(self.generate_and_store_title(conversation_id, user_content))
        self._pending_tasks[conversation_id] = task
        task.add_done_callback(lambda _task: self._pending_tasks.pop(conversation_id, None))


conversation_title_service = ConversationTitleService()