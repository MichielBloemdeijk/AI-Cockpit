"""Reviewed-memory lifecycle: the one owner of memory transitions and their knowledge publication.

Routes delegate here, so an item's review status and its published knowledge
file never disagree. SQLite cannot transact the filesystem, so each transition
orders its two writes to stay retryable: approval publishes before committing
and removes a fresh publication if the commit fails; withdrawal (reject or
delete of an approved item) unpublishes before committing, and repeating it
after a failed commit finishes the change.
"""
from __future__ import annotations

from pathlib import Path

from app.db.tables import MemoryStatus
from app.services.conversations.store import MemoryItemRecord, conversation_store
from app.services.knowledge_memory import delete_approved_memory, write_approved_memory


class MemoryItemNotFound(LookupError):
    pass


class MemoryTransitionRejected(ValueError):
    pass


class MemoryReviewStore:
    async def create_memory_item(self, **kwargs):
        return await conversation_store.create_memory_item(**kwargs)

    async def list_memory_items(self, conversation_id: str):
        return await conversation_store.list_memory_items(conversation_id)

    async def list_all_memory_items(self, *, status: str | None = None):
        return await conversation_store.list_all_memory_items(status=status)

    async def get_memory_item(self, memory_item_id: str):
        return await conversation_store.get_memory_item(memory_item_id)

    async def _require(self, memory_item_id: str) -> MemoryItemRecord:
        memory_item = await conversation_store.get_memory_item(memory_item_id)
        if memory_item is None:
            raise MemoryItemNotFound(memory_item_id)
        return memory_item

    async def approve(self, memory_item_id: str) -> tuple[MemoryItemRecord, Path]:
        """Publish the item; approving an approved item republishes it."""
        memory_item = await self._require(memory_item_id)
        if memory_item.status == MemoryStatus.deleted.value:
            raise MemoryTransitionRejected("A deleted memory item cannot be approved")
        knowledge_path = write_approved_memory(memory_item)
        if memory_item.status == MemoryStatus.approved.value:
            return memory_item, knowledge_path
        try:
            approved = await conversation_store.approve_memory_item(memory_item_id, knowledge_path=str(knowledge_path))
        except BaseException:
            delete_approved_memory(memory_item)
            raise
        return approved, knowledge_path

    async def reject(self, memory_item_id: str) -> MemoryItemRecord:
        memory_item = await self._require(memory_item_id)
        if memory_item.status == MemoryStatus.deleted.value:
            raise MemoryTransitionRejected("A deleted memory item cannot be rejected")
        self._withdraw(memory_item)
        return await conversation_store.reject_memory_item(memory_item_id)

    async def delete(self, memory_item_id: str) -> MemoryItemRecord:
        """Tombstone the item, withdrawing its publication first."""
        memory_item = await self._require(memory_item_id)
        self._withdraw(memory_item)
        return await conversation_store.delete_memory_item(memory_item_id)

    @staticmethod
    def _withdraw(memory_item: MemoryItemRecord) -> None:
        if memory_item.status == MemoryStatus.approved.value:
            delete_approved_memory(memory_item)


memory_review_store = MemoryReviewStore()
