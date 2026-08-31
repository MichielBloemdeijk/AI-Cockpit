"""Authenticated local history retrieval API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.models.retrieval import (
    HistoryContextLine,
    HistoryMatchView,
    HistorySearchRequest,
    HistorySearchResponse,
    RecallRebuildResponse,
)
from app.services.auth import require_auth
from app.services.history_retrieval import history_retrieval_service


router = APIRouter(prefix="/api/retrieval", tags=["retrieval"], dependencies=[Depends(require_auth)])


@router.post("/history", response_model=HistorySearchResponse)
async def search_history(request: HistorySearchRequest) -> HistorySearchResponse:
    try:
        matches, truncated, elapsed_ms = await history_retrieval_service.search(request.argv)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return HistorySearchResponse(
        matches=[
            HistoryMatchView(
                source_type=item.source_type,
                internal_source_id=item.internal_source_id,
                virtual_path=item.virtual_path,
                timestamp=item.timestamp,
                title=item.title,
                matching_text=item.matching_text,
                line_number=item.line_number,
                context=[HistoryContextLine(**line) for line in item.context],
            )
            for item in matches
        ],
        truncated=truncated,
        elapsed_ms=round(elapsed_ms, 3),
    )


@router.post("/history/rebuild", response_model=RecallRebuildResponse)
async def rebuild_history() -> RecallRebuildResponse:
    return RecallRebuildResponse(documents=await history_retrieval_service.rebuild())
