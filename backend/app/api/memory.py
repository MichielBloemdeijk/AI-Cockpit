"""Reviewed-memory aliases used by Jarvis v2 clients."""
from __future__ import annotations

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException

from app.db.session import session_scope
from app.db.tables import MemoryItem
from app.models.conversations import MemoryItemView
from app.presenters.conversations import present_memory_item
from app.services.auth import require_auth
from app.services.memory_review_store import memory_review_store


router = APIRouter(prefix="/api/memory", tags=["memory"], dependencies=[Depends(require_auth)])


class MemoryProposalPatch(BaseModel):
    id: str
    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, min_length=1)
    status: str | None = None


@router.get("/proposals", response_model=list[MemoryItemView])
async def list_memory_proposals() -> list[MemoryItemView]:
    items = await memory_review_store.list_all_memory_items(status="proposed")
    return [present_memory_item(item) for item in items]


@router.patch("/proposals", response_model=MemoryItemView)
async def patch_memory_proposal(request: MemoryProposalPatch) -> MemoryItemView:
    if request.status not in {None, "proposed", "rejected", "deleted"}:
        raise HTTPException(status_code=400, detail="Use the approval endpoint to approve memory")
    async with session_scope() as session:
        row = await session.get(MemoryItem, request.id)
        if row is None:
            raise HTTPException(status_code=404, detail="Memory proposal not found")
        if request.title is not None:
            row.title = " ".join(request.title.split()).strip()
        if request.content is not None:
            row.content = request.content.strip()
        if request.status is not None:
            row.status = request.status
        await session.flush()
    saved = await memory_review_store.get_memory_item(request.id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Memory proposal not found")
    return present_memory_item(saved)
