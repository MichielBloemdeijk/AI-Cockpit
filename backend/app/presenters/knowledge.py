from __future__ import annotations

from app.models.knowledge import KnowledgeReviewItemView

from app.presenters.conversations import present_memory_item


def present_knowledge_review_item(memory_item, *, conversation_title: str | None) -> KnowledgeReviewItemView:
    return KnowledgeReviewItemView(
        **present_memory_item(memory_item).model_dump(),
        conversation_title=conversation_title,
    )