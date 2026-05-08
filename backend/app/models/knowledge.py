"""Pydantic models for knowledge review and file browsing."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.conversations import MemoryItemView


class KnowledgeReviewItemView(MemoryItemView):
    conversation_title: str | None = None


class KnowledgeDocumentView(BaseModel):
    path: str
    title: str
    kind: str
    content: str
    updated_at: datetime | None = None


class KnowledgeReviewQueueResponse(BaseModel):
    items: list[KnowledgeReviewItemView] = Field(default_factory=list)


class KnowledgeDocumentsResponse(BaseModel):
    documents: list[KnowledgeDocumentView] = Field(default_factory=list)
