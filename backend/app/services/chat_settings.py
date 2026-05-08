"""Persists and resolves default chat settings and conversation session metadata."""
from __future__ import annotations

from typing import Any

from app.config import settings
from app.models.settings import ChatSettingsResponse, ConversationSessionMetadata
from app.services.conversation_store import ConversationRecord, conversation_store


class ChatSettingsService:
    def _available_models(self) -> list[str]:
        values = [*settings.council_model_list, settings.synthesizer_model, settings.task_agent_model]
        ordered: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = str(value or "").strip()
            if normalized and normalized not in seen:
                ordered.append(normalized)
                seen.add(normalized)
        return ordered

    def _env_defaults(self) -> ConversationSessionMetadata:
        available_models = settings.council_model_list
        first_model = available_models[0] if available_models else settings.synthesizer_model
        return ConversationSessionMetadata(
            mode="single",
            single_model=first_model,
            council_models=available_models or [first_model],
            synthesizer_model=settings.synthesizer_model or first_model,
        )

    def _normalize(self, payload: ConversationSessionMetadata | dict | None) -> ConversationSessionMetadata:
        if payload is None:
            return self._env_defaults()
        if isinstance(payload, ConversationSessionMetadata):
            metadata = payload
        else:
            metadata = ConversationSessionMetadata.model_validate(payload)
        if not metadata.council_models:
            raise ValueError("At least one council model is required")
        return metadata

    def _stored_settings(self, payload: dict[str, Any] | None) -> tuple[ConversationSessionMetadata, str]:
        defaults = self._env_defaults()
        task_agent_model = settings.task_agent_model
        if not payload:
            return defaults, task_agent_model

        if isinstance(payload.get("defaults"), dict) or isinstance(payload.get("task_agent_model"), str):
            stored_defaults = self._normalize(payload.get("defaults")) if payload.get("defaults") is not None else defaults
            stored_task_model = str(payload.get("task_agent_model") or task_agent_model).strip() or task_agent_model
            return stored_defaults, stored_task_model

        return self._normalize(payload), task_agent_model

    async def get_defaults(self) -> ConversationSessionMetadata:
        stored_defaults = await conversation_store.get_chat_defaults()
        if stored_defaults is None or not stored_defaults.chat_defaults_json:
            return self._env_defaults()
        defaults, _task_agent_model = self._stored_settings(stored_defaults.chat_defaults_json)
        return defaults

    async def get_task_agent_model(self) -> str:
        stored_defaults = await conversation_store.get_chat_defaults()
        if stored_defaults is None or not stored_defaults.chat_defaults_json:
            return settings.task_agent_model
        _defaults, task_agent_model = self._stored_settings(stored_defaults.chat_defaults_json)
        return task_agent_model

    async def update_defaults(self, defaults: ConversationSessionMetadata) -> ConversationSessionMetadata:
        normalized = self._normalize(defaults)
        existing_task_agent_model = await self.get_task_agent_model()
        await conversation_store.upsert_chat_defaults(
            {
                "defaults": normalized.model_dump(),
                "task_agent_model": existing_task_agent_model,
            }
        )
        return normalized

    async def update_settings(self, *, defaults: ConversationSessionMetadata, task_agent_model: str) -> ChatSettingsResponse:
        normalized_defaults = self._normalize(defaults)
        normalized_task_agent_model = str(task_agent_model).strip()
        if not normalized_task_agent_model:
            raise ValueError("Task agent model is required")
        await conversation_store.upsert_chat_defaults(
            {
                "defaults": normalized_defaults.model_dump(),
                "task_agent_model": normalized_task_agent_model,
            }
        )
        return ChatSettingsResponse(
            available_models=self._available_models(),
            defaults=normalized_defaults,
            task_agent_model=normalized_task_agent_model,
        )

    async def get_settings_response(self) -> ChatSettingsResponse:
        return ChatSettingsResponse(
            available_models=self._available_models(),
            defaults=await self.get_defaults(),
            task_agent_model=await self.get_task_agent_model(),
        )

    def resolve_conversation_metadata(
        self,
        conversation: ConversationRecord,
        fallback_defaults: ConversationSessionMetadata,
    ) -> ConversationSessionMetadata:
        if conversation.session_metadata_json:
            return self._normalize(conversation.session_metadata_json)
        mode = conversation.mode_hint if conversation.mode_hint in {"single", "council"} else fallback_defaults.mode
        return fallback_defaults.model_copy(update={"mode": mode})

    def build_requested_metadata(
        self,
        *,
        defaults: ConversationSessionMetadata,
        session_metadata: ConversationSessionMetadata | None,
        council_mode: bool,
        model: str | None,
    ) -> ConversationSessionMetadata:
        if session_metadata is not None:
            return self._normalize(session_metadata)
        return defaults.model_copy(
            update={
                "mode": "council" if council_mode else "single",
                "single_model": model or defaults.single_model,
            }
        )


chat_settings_service = ChatSettingsService()