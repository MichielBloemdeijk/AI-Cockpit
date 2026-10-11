"""Actual isolated API process restart; no models, browser or interactive input."""
import asyncio
import os
from pathlib import Path
import socket
import sys
import sqlite3
from uuid import uuid4

from pydantic import SecretStr
from httpx import AsyncClient, HTTPError
import pytest

from app.config import settings
from app.db.session import close_engine
from app.services.worker_coordinator import worker_coordinator
from app.jarvis_tasks.sqlite_backup import snapshot_database
from test_result_observability import admitted
from test_task_result_contract import claim, finish


async def stop(process):
    if process.returncode is None:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 8)
        except TimeoutError:
            process.kill()
            await process.wait()


@pytest.mark.asyncio
@pytest.mark.parametrize("restore", [False, True], ids=["restart", "backup-restore"])
async def test_real_api_process_restart_keeps_result_saved_metrics_and_disabled_workflow_gate(tmp_path, restore):
    worker = await worker_coordinator.activate(process_identity="restart-result-fixture")
    task_id, page = await admitted()
    await finish(worker, task_id, await claim(worker, task_id), "Durable restart body")
    await close_engine()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {**os.environ, "DATABASE_URL": settings.database_url,
        "AUTH_PASSWORD": "restart-fixture",
        "SESSION_SECRET_KEY": uuid4().hex, "SESSION_COOKIE_SECURE": "false",
        "TASK_POOL_ENABLED": "false", "TASK_ORCHESTRATION_ENABLED": "false",
        "TASK_ORCHESTRATION_LIVE_ENABLED": "false", "TASK_EXECUTOR": "codex",
        "TASK_RESEARCH_ENABLED": "false", "TASK_WORKFLOW_ALLOWLIST": "",
        "OPENAI_API_KEY": "", "OPENROUTER_API_KEY": "", "WEB_PUSH_ENABLED": "false",
        "CONVERSATION_WORKSPACES_DIR": str(tmp_path / "conversations"),
        "KNOWLEDGE_NOTES_DIR": str(tmp_path / "knowledge"),
        "KNOWLEDGE_PREFERENCES_FILE": str(tmp_path / "preferences.yaml"),
        "VERIFIER_GENERATED_APPS_DIR": str(tmp_path / "verifier"),
        "TASK_WORKSPACES_DIR": settings.task_workspaces_dir,
        "TASK_CONTEXT_BLOBS_DIR": settings.task_context_blobs_dir,
        "CODEX_PROFILES_DIR": str(tmp_path / "profiles"),
        "TASK_ORCHESTRATION_CONTEXT_DIR": str(tmp_path / "orchestration")}
    root = Path(__file__).resolve().parents[1]
    endpoint = f"/api/results/{page.public_id}"
    async with AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=5) as client:
        before = None
        task_before = None
        history_before = None
        for generation in range(2):
            if generation == 1 and restore:
                source = settings.database_path
                restored = tmp_path / "restored.db"
                proof = snapshot_database(source, restored)
                assert proof["integrity"] == "ok" and proof["foreign_keys"] == "ok"
                # The second API migrates/reads only the fresh clone; the source
                # is retained untouched. Compare all history/result/storage rows.
                with sqlite3.connect(source) as original, sqlite3.connect(restored) as copy:
                    for table in ("tasks", "task_turns", "task_execution_runs", "task_attempts", "task_events",
                                  "task_artifacts", "result_pages", "result_page_revisions", "result_page_events",
                                  "app_data_records", "result_metrics", "notification_outbox", "cockpit_apps"):
                        assert copy.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() == original.execute(
                            f"SELECT * FROM {table} ORDER BY rowid").fetchall(), table
                env["DATABASE_URL"] = f"sqlite+aiosqlite:///{restored.as_posix()}"
            process = await asyncio.create_subprocess_exec(sys.executable, "-m", "uvicorn", "app.main:app",
                "--host", "127.0.0.1", "--port", str(port), "--log-level", "error", cwd=root, env=env,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            try:
                ready = False
                for _ in range(200):
                    if process.returncode is not None:
                        assert False, (await process.stderr.read()).decode(errors="replace")
                    try:
                        ready = (await client.get("/api/health")).status_code == 200
                    except HTTPError:
                        pass
                    if ready:
                        break
                    await asyncio.sleep(.05)
                assert ready, "Isolated API process did not start"
                if generation == 0:
                    assert (await client.get(endpoint)).status_code == 401
                    assert (await client.post("/api/auth/login", json={"password": "restart-fixture"})).status_code == 200
                    assert (await client.post(endpoint + "/actions/set_saved", json={"saved": True, "expected_revision": 0})).status_code == 200
                    before = (await client.get(endpoint)).json()
                else:
                    response = await client.get(endpoint)
                    assert response.status_code == 200  # Same signed cookie after restart.
                    assert response.json() == before
                stats = (await client.get(endpoint + "/observability")).json()
                assert stats["counts"]["allocation"] == 1
                assert stats["publication_outcomes"]["committed"] == 1
                assert before["actions"][0]["value"] == {"saved": True}
                assert before["content"]["body"] == "Durable restart body"
                task = (await client.get(f"/api/tasks/{task_id}")).json()
                assert len(task["attempts"]) == 1 and task["status"] == "completed"
                history_response = await client.get(f"/api/tasks/{task_id}/events")
                assert history_response.status_code == 200
                history = history_response.json()
                if generation == 0:
                    task_before, history_before = task, history
                else:
                    assert task == task_before and history == history_before
                rejected = await client.post("/api/tasks", json={"goal": "Disabled workflow", "idempotency_key": str(uuid4()),
                    "execution_kind": "workflow", "execution_config": {"definition": "research_screen@1"}})
                assert rejected.status_code == 409 and rejected.json()["detail"] == "Workflow version and digest are not enabled", rejected.text
                assert len((await client.get("/api/tasks")).json()) == 1
            finally:
                await stop(process)
