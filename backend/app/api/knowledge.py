"""Knowledge review queue and file-backed knowledge browsing APIs."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query

from app.models.conversations import MemoryApprovalResponse, MemoryItemView
from app.models.knowledge import (
    KnowledgeDocumentView,
    KnowledgeDocumentsResponse,
    KnowledgeReviewItemView,
    KnowledgeReviewQueueResponse,
)
from app.presenters.conversations import present_memory_item
from app.presenters.knowledge import present_knowledge_review_item
from app.services.auth import require_auth
from app.services.conversations.store import conversation_store
from app.services.knowledge_memory import list_knowledge_documents
from app.services.memory_review_store import MemoryItemNotFound, MemoryTransitionRejected, memory_review_store

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"], dependencies=[Depends(require_auth)])


@router.get("/review-items", response_model=KnowledgeReviewQueueResponse)
async def list_review_items(status: str | None = Query(default="proposed")):
    memory_items = await memory_review_store.list_all_memory_items(status=status)
    items: list[KnowledgeReviewItemView] = []
    for memory_item in memory_items:
        conversation = await conversation_store.get_conversation(memory_item.source_conversation_id)
        items.append(present_knowledge_review_item(memory_item, conversation_title=None if conversation is None else conversation.title))
    return KnowledgeReviewQueueResponse(items=items)


@router.get("/documents", response_model=KnowledgeDocumentsResponse)
async def list_documents():
    return KnowledgeDocumentsResponse(
        documents=[KnowledgeDocumentView.model_validate(document) for document in list_knowledge_documents()]
    )


@router.post("/memory-items/{memory_item_id}/approve", response_model=MemoryApprovalResponse)
async def approve_review_item(memory_item_id: str):
    with _review_errors():
        memory_item, knowledge_path = await memory_review_store.approve(memory_item_id)
    return MemoryApprovalResponse(
        memory_item=present_memory_item(memory_item),
        knowledge_path=str(knowledge_path),
    )


@router.post("/memory-items/{memory_item_id}/reject", response_model=MemoryItemView)
async def reject_review_item(memory_item_id: str):
    with _review_errors():
        return present_memory_item(await memory_review_store.reject(memory_item_id))


@router.delete("/memory-items/{memory_item_id}", response_model=MemoryItemView)
async def delete_review_item(memory_item_id: str):
    with _review_errors():
        return present_memory_item(await memory_review_store.delete(memory_item_id))


@contextmanager
def _review_errors() -> Iterator[None]:
    try:
        yield
    except MemoryItemNotFound:
        raise HTTPException(status_code=404, detail="Memory item not found") from None
    except MemoryTransitionRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
