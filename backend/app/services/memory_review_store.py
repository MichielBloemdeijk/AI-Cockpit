from __future__ import annotations

from app.services.conversation_store import conversation_store


class MemoryReviewStore:
    async def create_memory_item(self, **kwargs):
        return await conversation_store.create_memory_item(**kwargs)

    async def list_memory_items(self, conversation_id: str):
        return await conversation_store.list_memory_items(conversation_id)

    async def list_all_memory_items(self, *, status: str | None = None):
        return await conversation_store.list_all_memory_items(status=status)

    async def get_memory_item(self, memory_item_id: str):
        return await conversation_store.get_memory_item(memory_item_id)

    async def approve_memory_item(self, memory_item_id: str, *, knowledge_path: str):
        return await conversation_store.approve_memory_item(memory_item_id, knowledge_path=knowledge_path)

    async def reject_memory_item(self, memory_item_id: str):
        return await conversation_store.reject_memory_item(memory_item_id)

    async def delete_memory_item(self, memory_item_id: str):
        return await conversation_store.delete_memory_item(memory_item_id)


memory_review_store = MemoryReviewStore()