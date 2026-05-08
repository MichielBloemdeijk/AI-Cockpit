"""bootstrap persistence infrastructure

Revision ID: 20260421_0001
Revises:
Create Date: 2026-04-21 00:00:00

"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260421_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("PRAGMA journal_mode=WAL")


def downgrade() -> None:
    pass