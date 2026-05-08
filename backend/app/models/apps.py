"""Pydantic models for generated frontend app registry APIs."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class GeneratedAppSummary(BaseModel):
    id: str
    slug: str
    title: str
    description: str | None = None
    status: str
    route_path: str
    verification_status: str | None = None
    source_task_run_id: str | None = None
    source_conversation_id: str | None = None
    lease_task_run_id: str | None = None
    lease_conversation_id: str | None = None
    lease_acquired_at: datetime | None = None
    updated_at: datetime
    created_at: datetime


class GeneratedAppDetail(GeneratedAppSummary):
    frontend_root: str
    frontend_entry_path: str | None = None
    icon_asset_path: str | None = None
    cover_asset_path: str | None = None
    manifest_json: dict[str, Any] | None = None
    last_error: str | None = None
    allowed_write_roots: list[str] = Field(default_factory=list)


class GeneratedAppCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=128)
    description: str | None = None
    status: str = "draft"
    verification_status: str | None = None
    source_task_run_id: str | None = None
    source_conversation_id: str | None = None
    manifest_json: dict[str, Any] | None = None


class GeneratedAppUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status: str | None = None
    verification_status: str | None = None
    frontend_entry_path: str | None = None
    icon_asset_path: str | None = None
    cover_asset_path: str | None = None
    manifest_json: dict[str, Any] | None = None
    last_error: str | None = None
    source_task_run_id: str | None = None
    source_conversation_id: str | None = None