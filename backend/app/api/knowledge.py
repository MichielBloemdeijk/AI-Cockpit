"""Knowledge review queue and file-backed knowledge browsing APIs."""
from __future__ import annotations

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
from app.services.conversation_store import conversation_store
from app.services.knowledge_memory import delete_approved_memory, list_knowledge_documents, write_approved_memory
from app.services.memory_review_store import memory_review_store

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
    memory_item = await memory_review_store.get_memory_item(memory_item_id)
    if memory_item is None:
        raise HTTPException(status_code=404, detail="Memory item not found")

    if memory_item.status != "approved":
        knowledge_path = write_approved_memory(memory_item)
        memory_item = await memory_review_store.approve_memory_item(memory_item_id, knowledge_path=str(knowledge_path))
    else:
        knowledge_path = write_approved_memory(memory_item)

    return MemoryApprovalResponse(
        memory_item=present_memory_item(memory_item),
        knowledge_path=str(knowledge_path),
    )


@router.post("/memory-items/{memory_item_id}/reject", response_model=MemoryItemView)
async def reject_review_item(memory_item_id: str):
    memory_item = await memory_review_store.get_memory_item(memory_item_id)
    if memory_item is None:
        raise HTTPException(status_code=404, detail="Memory item not found")
    rejected = await memory_review_store.reject_memory_item(memory_item_id)
    return present_memory_item(rejected)


@router.delete("/memory-items/{memory_item_id}", response_model=MemoryItemView)
async def delete_review_item(memory_item_id: str):
    memory_item = await memory_review_store.get_memory_item(memory_item_id)
    if memory_item is None:
        raise HTTPException(status_code=404, detail="Memory item not found")

    if memory_item.status == "approved":
        delete_approved_memory(memory_item)

    deleted = await memory_review_store.delete_memory_item(memory_item_id)
    return present_memory_item(deleted)