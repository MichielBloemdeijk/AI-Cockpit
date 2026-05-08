from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config

from app.config import settings
from app.db.session import close_engine, ensure_database_ready, initialize_engine


@pytest_asyncio.fixture(autouse=True)
async def configured_database(tmp_path: Path):
    original_database_url = settings.database_url
    original_alembic_path = settings.alembic_ini_path
    original_conversation_workspaces_dir = settings.conversation_workspaces_dir
    original_knowledge_notes_dir = settings.knowledge_notes_dir
    original_knowledge_preferences_file = settings.knowledge_preferences_file
    database_path = tmp_path / "test.db"

    await close_engine()
    settings.database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    settings.alembic_ini_path = str(Path(__file__).resolve().parents[1] / "alembic.ini")
    settings.conversation_workspaces_dir = str(tmp_path / ".cockpit" / "conversations")
    settings.knowledge_notes_dir = str(tmp_path / "knowledge" / "notes")
    settings.knowledge_preferences_file = str(tmp_path / "knowledge" / "preferences.yaml")

    await ensure_database_ready()
    initialize_engine(settings.database_url)

    alembic_config = Config(settings.alembic_config_path)
    alembic_config.set_main_option("sqlalchemy.url", settings.database_url.replace("+aiosqlite", ""))
    command.upgrade(alembic_config, "head")

    try:
        yield
    finally:
        await close_engine()
        settings.database_url = original_database_url
        settings.alembic_ini_path = original_alembic_path
        settings.conversation_workspaces_dir = original_conversation_workspaces_dir
        settings.knowledge_notes_dir = original_knowledge_notes_dir
        settings.knowledge_preferences_file = original_knowledge_preferences_file