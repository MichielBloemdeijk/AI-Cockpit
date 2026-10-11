"""The real worker bridge must share the backend DB from a task working directory."""
import asyncio
import json
import os
from pathlib import Path
import sys

import pytest

from app.config import settings
from app.services.task_service import task_service
from app.services.worker_coordinator import worker_coordinator
from app.jarvis_tasks.worker_process_probe import call_worker_bridge, probe_recipe_task


async def exchange(arguments, workspace, calls):
    process = await asyncio.create_subprocess_exec(sys.executable, *arguments,
        cwd=workspace, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "OPENAI_API_KEY": "", "CODEX_API_KEY": "", "OPENROUTER_API_KEY": ""})
    requests = [{"jsonrpc": "2.0", "id": i, "method": "tools/call",
        "params": {"name": name, "arguments": values}} for i, (name, values) in enumerate(calls)]
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(
            ("\n".join(json.dumps(r) for r in requests) + "\n").encode()), timeout=30)
        assert process.returncode == 0, stderr.decode(errors="replace")
        replies = [json.loads(line)["result"] for line in stdout.splitlines()]
        assert len(replies) == len(calls)
        return replies
    finally:
        if process.returncode is None:
            process.kill()
            await process.communicate()


@pytest.mark.asyncio
async def test_relative_database_from_real_worker_process(monkeypatch):
    created = await task_service.dispatch(goal="Worker process database regression", origin_surface="test",
        idempotency_key="relative-worker-db")
    worker = await worker_coordinator.activate(process_identity="relative-db-worker")
    claim = (await worker_coordinator.claim_next(worker.worker_id)).task
    workspace = Path(claim.workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)
    relative = Path(os.path.relpath(settings.database_path, settings.backend_root)).as_posix()
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{relative}")
    bridge = settings.backend_root / "app/jarvis_tasks/task_mcp.py"
    replies = await exchange([str(bridge), "--database-url", settings.database_url,
        "--task-id", created.task.id, "--run-id", claim.execution_run_id,
        "--attempt-id", claim.attempt_id, "--claim-token", claim.claim_token,
        "--workspace", str(workspace)], workspace, [("task_history", {})])
    assert not replies[0].get("isError"), replies[0]
    assert created.task.id in json.dumps(replies[0])


@pytest.mark.asyncio
async def test_recipe_worker_reads_and_publishes_across_process_restarts(monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from app.db.session import close_engine
    from app.main import app
    from test_m4_workflows import admit
    args, _ = await admit(monkeypatch, "recipe_from_screen@1", claim_task=False)
    relative = Path(os.path.relpath(settings.database_path, settings.backend_root)).as_posix()
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{relative}")
    report = await probe_recipe_task(args.task_id, expected_images=1)
    assert report["models_called"] == 0 and report["roles_read"] == ["invocation"]
    await close_engine()  # Reopen backend connections after the child's durable publication.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        await http.post("/api/auth/login", json={"password": ""})
        response = await http.get(f"/api/results/{report['public_id']}")
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["revision_number"] == 1 and page["status"] == "ready"
        assert page["content"]["title"] == "Tomato sandwich"


@pytest.mark.asyncio
async def test_worker_process_clarification_and_stale_claim(monkeypatch):
    from dataclasses import replace
    from app.services.codex_task_runtime import task_mcp_arguments
    task = await task_service.dispatch(goal="Which recipe?", origin_surface="test", idempotency_key="process-question")
    worker = await worker_coordinator.activate(process_identity="question-process-worker")
    context = (await worker_coordinator.claim_next(worker.worker_id)).task
    relative = Path(os.path.relpath(settings.database_path, settings.backend_root)).as_posix()
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{relative}")
    arguments = task_mcp_arguments(context)
    assert Path(arguments[arguments.index("--database-url") + 1].split("///", 1)[1]).is_absolute()
    rejected = await call_worker_bridge(replace(context, claim_token="stale"), [("task_history", {})])
    assert rejected[0]["isError"] and rejected[0]["structuredContent"]["error"] != "OperationalError"
    question = (await call_worker_bridge(context, [("task_question", {
        "question": "Which dish?", "operation_key": "process-question"})]))[0]
    assert not question["isError"], question
    assert (await task_service.get_task(task.task.id)).task.status == "waiting_for_input"
    await worker_coordinator.release_claim(worker.worker_id, context.attempt_id,
        claim_token=context.claim_token, writers_stopped=True)
    await task_service.answer_question(task.task.id, question["structuredContent"]["question_id"],
        answer="Tomato sandwich", idempotency_key="process-answer")
    resumed = (await worker_coordinator.claim_next(worker.worker_id)).task
    assert resumed.execution_run_id == context.execution_run_id
    history = (await call_worker_bridge(resumed, [("task_history", {})]))[0]
    assert not history["isError"] and "Tomato sandwich" in json.dumps(history)


@pytest.mark.asyncio
async def test_migrations_and_backend_reopen_share_relative_database(monkeypatch, tmp_path):
    from app.db.session import close_engine, run_migrations
    task = await task_service.dispatch(goal="Preserve existing task through startup", origin_surface="test",
        idempotency_key="relative-startup")
    database = settings.database_path
    relative = Path(os.path.relpath(database, settings.backend_root)).as_posix()
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{relative}?timeout=30")
    await close_engine()
    foreign = tmp_path / "foreign-working-directory"
    foreign.mkdir()
    monkeypatch.chdir(foreign)
    run_migrations()
    assert settings.database_path == database.resolve()
    assert (await task_service.get_task(task.task.id)).task.goal == task.task.goal
    assert list(foreign.iterdir()) == []


@pytest.mark.parametrize("url", ["sqlite+aiosqlite:///:memory:", "sqlite+aiosqlite://",
                                 "postgresql+asyncpg://user:secret@localhost/jarvis"])
def test_non_file_database_urls_are_unchanged(url):
    assert settings.resolve_database_url(url) == url
