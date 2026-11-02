"""Shared SQLAlchemy declarative base."""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for persistence models."""


def is_orm_schema_name(name: str | None, type_: str, parent_names: dict) -> bool:
    """Alembic `include_name` filter: skip the recall full-text tables, which are
    SQLite DDL in the baseline migration rather than ORM models."""
    return not (type_ == "table" and name is not None and name.startswith("recall_fts"))
