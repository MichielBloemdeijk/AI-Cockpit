from __future__ import annotations

from pathlib import Path
import tempfile
import os
import socket
import shutil
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config

# A personal .env (password access, secure cookies) must not change test behavior.
# Environment variables take precedence over the .env file in Settings.
os.environ.update(SESSION_COOKIE_SECURE="false", AUTH_PASSWORD="", JARVIS_ACCESS_MODE="personal")

from app.config import settings
from app.db.session import close_engine, ensure_database_ready, initialize_engine
from app.services.voice_session_adapter import fake_voice_session_adapter


@pytest.fixture(autouse=True)
def evaluation_network_boundary(monkeypatch):
    if os.environ.get("JARVIS_EVAL_NETWORK_BLOCKED") != "1":
        return
    connect = socket.socket.connect
    def local_only(instance, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise AssertionError("Offline evaluation attempted external networking")
        return connect(instance, address)
    monkeypatch.setattr(socket.socket, "connect", local_only)


@pytest.fixture
def tmp_path():
    # Python's Windows mode=0o700 directory creation removes inherited ACLs.
    # Test commands must use the same ordinary mkdir provisioning as runtime.
    parent = Path(tempfile.gettempdir()).resolve() / "jarvis-test-execution"
    parent.mkdir(exist_ok=True)
    path = parent / str(uuid4())
    path.mkdir()
    try:
        yield path
    finally:
        if path.resolve().parent != parent or path.is_symlink():
            raise RuntimeError("Test cleanup target escaped its execution root")
        __import__("gc").collect()
        def remove_readonly(function, target, error):
            resolved = Path(target).resolve()
            if not resolved.is_relative_to(path.resolve()) or not resolved.is_file():
                raise error
            resolved.chmod(__import__("stat").S_IWRITE)
            function(target)
        shutil.rmtree(path, onexc=remove_readonly)


@pytest_asyncio.fixture(autouse=True)
async def configured_database(tmp_path: Path):
    original_database_url = settings.database_url
    original_alembic_path = settings.alembic_ini_path
    original_conversation_workspaces_dir = settings.conversation_workspaces_dir
    original_knowledge_notes_dir = settings.knowledge_notes_dir
    original_knowledge_preferences_file = settings.knowledge_preferences_file
    original_verifier_generated_apps_dir = settings.verifier_generated_apps_dir
    original_task_workspaces_dir = settings.task_workspaces_dir
    original_task_context_blobs_dir = settings.task_context_blobs_dir
    database_path = tmp_path / "test.db"

    await close_engine()
    settings.database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    settings.alembic_ini_path = str(Path(__file__).resolve().parents[1] / "alembic.ini")
    settings.conversation_workspaces_dir = str(tmp_path / ".cockpit" / "conversations")
    settings.knowledge_notes_dir = str(tmp_path / "knowledge" / "notes")
    settings.knowledge_preferences_file = str(tmp_path / "knowledge" / "preferences.yaml")
    settings.verifier_generated_apps_dir = str(tmp_path / "verifier-runs")
    settings.task_workspaces_dir = str(tmp_path / "task-workspaces")
    settings.task_context_blobs_dir = str(tmp_path / "task-context-blobs")

    await ensure_database_ready()
    initialize_engine(settings.database_url)
    fake_voice_session_adapter.reset()

    alembic_config = Config(settings.alembic_config_path)
    alembic_config.set_main_option("sqlalchemy.url", settings.database_url.replace("+aiosqlite", ""))
    command.upgrade(alembic_config, "head")

    try:
        yield
    finally:
        # Finish coordinator DB work before the test event loop/database closes.
        from app.services.jarvis_coordinator import jarvis_coordinator
        await jarvis_coordinator.stop()
        fake_voice_session_adapter.reset()
        await close_engine()
        settings.database_url = original_database_url
        settings.alembic_ini_path = original_alembic_path
        settings.conversation_workspaces_dir = original_conversation_workspaces_dir
        settings.knowledge_notes_dir = original_knowledge_notes_dir
        settings.knowledge_preferences_file = original_knowledge_preferences_file
        settings.verifier_generated_apps_dir = original_verifier_generated_apps_dir
        settings.task_workspaces_dir = original_task_workspaces_dir
        settings.task_context_blobs_dir = original_task_context_blobs_dir
