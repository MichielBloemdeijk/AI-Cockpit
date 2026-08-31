"""Contracts for bounded local Jarvis history recall."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class HistorySearchRequest(BaseModel):
    argv: str = Field(min_length=1, max_length=2000)


class HistoryContextLine(BaseModel):
    line_number: int
    text: str
    matching: bool = False


class HistoryMatchView(BaseModel):
    source_type: str
    internal_source_id: str
    virtual_path: str
    timestamp: datetime
    title: str
    matching_text: str
    line_number: int
    context: list[HistoryContextLine] = Field(default_factory=list)


class HistorySearchResponse(BaseModel):
    matches: list[HistoryMatchView] = Field(default_factory=list)
    truncated: bool = False
    elapsed_ms: float


class RecallRebuildResponse(BaseModel):
    documents: int
