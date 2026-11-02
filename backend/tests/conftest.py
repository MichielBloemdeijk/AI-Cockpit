from __future__ import annotations

from pathlib import Path
import tempfile
import asyncio
import os
import socket
import sys
import time
import shutil
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config

# Tests never read the personal .env (passwords, cookies, deployment switches, models):
# Settings start from their code defaults; tests that need a value set it explicitly.
os.environ["AI_COCKPIT_ENV_FILE"] = str(Path(__file__).parent / "no-personal-env")
# Agent-run tests must never wait for an administrator (UAC) prompt, and they exercise the
# deployed command mode (trusted). The native sandbox currently fails on this host (scratch
# folder permission denied unelevated; elevated waits for UAC); it has its own probe tests.
os.environ.setdefault("TASK_WINDOWS_SANDBOX", "unelevated")
os.environ.setdefault("TASK_COMMAND_MODE", "trusted")
# Tests drive the app runtime's steps directly instead of its background loop.
os.environ.setdefault("APP_RUNTIME_ENABLED", "false")
# Tests load Android harness scripts by path; they import their shared harness_support module.
sys.path.append(str(Path(__file__).resolve().parents[2] / "android" / "scripts"))

from app.config import settings
from app.app_platform import close_apps_engine, ensure_apps_database, install_builtin_apps
from app.db.session import close_engine, initialize_engine
from app.services.jarvis.voice_session_adapter import fake_voice_session_adapter


@pytest.fixture(autouse=True)
def offline_network_boundary(request, monkeypatch):
    # Only billed provider tests (marked `provider`, each also behind its own opt-in
    # flag) may leave this machine; everything else fails on external networking.
    if request.node.get_closest_marker("provider"):
        return
    connect = socket.socket.connect
    def local_only(instance, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise AssertionError("Offline evaluation attempted external networking")
        return connect(instance, address)
    monkeypatch.setattr(socket.socket, "connect", local_only)


@pytest.fixture(autouse=True)
def offline_conversation_observer(monkeypatch):
    # The application lifespan starts the conversation observer. Tests never reach
    # the paid Responses assessment; observer tests substitute their own infer().
    from app.services.jarvis import observer as conversation_observer

    async def unavailable(evidence):
        raise RuntimeError("Tests must not call the observer's Responses model")

    monkeypatch.setattr(conversation_observer, "infer", unavailable)


@pytest.fixture(scope="session", autouse=True)
def sweep_stale_test_folders():
    parent = Path(tempfile.gettempdir()).resolve() / "jarvis-test-execution"
    cutoff = time.time() - 3600
    for leftover in parent.glob("*") if parent.is_dir() else ():
        try:
            stale = leftover.is_dir() and not leftover.is_symlink() and leftover.stat().st_mtime < cutoff
        except FileNotFoundError:
            continue  # Every xdist worker sweeps; another one removed it first.
        if stale:
            shutil.rmtree(leftover, ignore_errors=True)


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


@pytest.fixture(scope="session")
def migrated_template_database(tmp_path_factory) -> Path:
    # Replaying every migration per test dominated suite time; migrate once and copy.
    # env.py reads settings.resolved_database_url, so point settings at the template.
    template_path = tmp_path_factory.mktemp("template-db") / "template.db"
    original_database_url = settings.database_url
    original_alembic_path = settings.alembic_ini_path
    settings.database_url = f"sqlite+aiosqlite:///{template_path.as_posix()}"
    settings.alembic_ini_path = str(Path(__file__).resolve().parents[1] / "alembic.ini")
    try:
        alembic_config = Config(settings.alembic_config_path)
        alembic_config.set_main_option("sqlalchemy.url", settings.database_url.replace("+aiosqlite", ""))
        command.upgrade(alembic_config, "head")
    finally:
        settings.database_url = original_database_url
        settings.alembic_ini_path = original_alembic_path
    return template_path


@pytest_asyncio.fixture(autouse=True)
async def configured_database(tmp_path: Path, migrated_template_database: Path):
    original_database_url = settings.database_url
    original_alembic_path = settings.alembic_ini_path
    original_conversation_workspaces_dir = settings.conversation_workspaces_dir
    original_knowledge_notes_dir = settings.knowledge_notes_dir
    original_knowledge_preferences_file = settings.knowledge_preferences_file
    original_task_workspaces_dir = settings.task_workspaces_dir
    original_task_context_blobs_dir = settings.task_context_blobs_dir
    original_app_platform = (settings.apps_database_url, settings.apps_dir, settings.app_files_dir)
    database_path = tmp_path / "test.db"

    await close_engine()
    settings.database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    settings.alembic_ini_path = str(Path(__file__).resolve().parents[1] / "alembic.ini")
    settings.conversation_workspaces_dir = str(tmp_path / ".cockpit" / "conversations")
    settings.knowledge_notes_dir = str(tmp_path / "knowledge" / "notes")
    settings.knowledge_preferences_file = str(tmp_path / "knowledge" / "preferences.yaml")
    settings.task_workspaces_dir = str(tmp_path / "task-workspaces")
    settings.task_context_blobs_dir = str(tmp_path / "task-context-blobs")
    settings.apps_database_url = f"sqlite+aiosqlite:///{(tmp_path / 'apps.db').as_posix()}"
    settings.apps_dir = str(tmp_path / "apps")
    settings.app_files_dir = str(tmp_path / "app-files")

    shutil.copyfile(migrated_template_database, database_path)
    initialize_engine(settings.database_url)
    await ensure_apps_database()
    # Documents and Recipes are installed in every Cockpit, as on start.
    await install_builtin_apps()
    fake_voice_session_adapter.reset()

    try:
        yield
    finally:
        # Finish coordinator DB work before the test event loop/database closes.
        from app.services.jarvis.observer import conversation_observer
        from app.services.jarvis.coordinator import jarvis_coordinator
        await conversation_observer.stop()
        await jarvis_coordinator.stop()
        # Fire-and-forget push deliveries may still hold a database connection
        # (Windows then cannot delete the test database).
        from app.services.updates import notifications as task_notifications
        pending = list(task_notifications._outbox_tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        fake_voice_session_adapter.reset()
        await close_apps_engine()
        await close_engine()
        settings.database_url = original_database_url
        (settings.apps_database_url, settings.apps_dir, settings.app_files_dir) = original_app_platform
        settings.alembic_ini_path = original_alembic_path
        settings.conversation_workspaces_dir = original_conversation_workspaces_dir
        settings.knowledge_notes_dir = original_knowledge_notes_dir
        settings.knowledge_preferences_file = original_knowledge_preferences_file
        settings.task_workspaces_dir = original_task_workspaces_dir
        settings.task_context_blobs_dir = original_task_context_blobs_dir
