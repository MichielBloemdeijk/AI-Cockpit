"""Opt-in billed proof of the general Codex-to-Document publication adapter."""
import asyncio
import os

import pytest

pytestmark = pytest.mark.provider
from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.db.tables import ResultPage
from app.services.codex_task_runtime import codex_task_runtime
from app.services.task_service import task_service
from app.services.task_worker import TaskWorker
from app.services.task_results import result_snapshot
from test_codex_task_runtime_live import _live_directory


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("JARVIS_LIVE_CODEX") != "1", reason="billed live Codex test is opt-in")
async def test_general_codex_publication_update_and_answer_preserve_identity():
    if not settings.openai_api_key:
        pytest.skip("No authorized API credential is configured")
    originals = settings.codex_profiles_dir, settings.task_workspaces_dir
    root = settings.backend_root / "data"
    root.mkdir(parents=True, exist_ok=True)
    try:
        with _live_directory(root) as directory:
            settings.codex_profiles_dir = str(directory / "profiles")
            settings.task_workspaces_dir = str(directory / "workspaces")
            created = await task_service.dispatch(goal="Return only the Markdown word READY. No tools or file changes are needed.",
                origin_surface="test", idempotency_key="m5-live-initial", result_request={"mode": "new"})
            task_id = created.task.id
            worker = TaskWorker(runtime=codex_task_runtime)
            assert await worker.activate(process_identity="m5-live-result")

            async def execute():
                outcome = await asyncio.wait_for(worker.run_once(), timeout=120)
                assert outcome.status == "completed", (await task_service.get_task(task_id)).task.terminal_summary

            async def snapshot():
                async with session_scope() as session:
                    page = (await session.execute(select(ResultPage).where(ResultPage.task_id == task_id))).scalar_one()
                    return await result_snapshot(session, page)

            await execute()
            first = await snapshot()
            assert first["revision_number"] == 1 and first["content"]["body"].strip() == "READY"
            await task_service.continue_task(task_id, content="Replace the Document body with only UPDATED. No tools needed.",
                origin="test", idempotency_key="m5-live-edit", result_request={"mode": "update",
                    "public_id": first["public_id"], "expected_revision": 1})
            await execute()
            updated = await snapshot()
            assert updated["public_id"] == first["public_id"] and updated["revision_number"] == 2
            assert updated["content"]["body"].strip() == "UPDATED"
            assert not list(directory.rglob("document.md")), "A maintained result does not require a surrogate file"
            await task_service.continue_task(task_id, content="Answer with only ANSWER. Do not edit any result or file.",
                origin="test", idempotency_key="m5-live-answer", result_request={"mode": "answer", "public_id": first["public_id"]})
            await execute()
            assert await snapshot() == updated
            detail = await task_service.get_task(task_id)
            assert len(detail.runs) == 3 and all(run.context_reference for run in detail.runs)
    finally:
        settings.codex_profiles_dir, settings.task_workspaces_dir = originals


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("JARVIS_LIVE_CODEX") != "1", reason="billed live Codex test is opt-in")
async def test_workflow_answer_uses_filtered_codex_inventory_without_republishing(monkeypatch):
    from app.jarvis_tasks.task_mcp import _call
    from app.services.worker_coordinator import worker_coordinator
    from test_m4_workflows import admit, researched, content
    if not settings.openai_api_key:
        pytest.skip("No authorized API credential is configured")
    root = settings.backend_root / "data"
    root.mkdir(parents=True, exist_ok=True)
    with _live_directory(root) as directory:
        monkeypatch.setattr(settings, "codex_profiles_dir", str(directory / "profiles"))
        monkeypatch.setattr(settings, "task_workspaces_dir", str(directory / "workspaces"))
        args, claimed_worker = await admit(monkeypatch, "research_screen@1")
        _, source = await researched(args)
        await _call("result_publish", content(args, source) | {"operation_key": "initial-fixture"}, args)
        await worker_coordinator.release_claim(claimed_worker.worker_id, args.attempt_id,
            claim_token=args.claim_token, writers_stopped=True)
        async with session_scope() as session:
            page = (await session.execute(select(ResultPage).where(ResultPage.task_id == args.task_id))).scalar_one()
            before = await result_snapshot(session, page)
        await task_service.continue_task(args.task_id,
            content="Answer-only follow-up: simply acknowledge with task_finish(status completed, summary ANSWER, operation_key answer). No research or publication is needed.",
            origin="test", idempotency_key="workflow-live-answer", result_request={"mode": "answer", "public_id": before["public_id"]})
        worker = TaskWorker(runtime=codex_task_runtime)
        worker.worker_id = claimed_worker.worker_id
        outcome = await asyncio.wait_for(worker.run_once(), timeout=120)
        assert outcome.status == "completed", (await task_service.get_task(args.task_id)).task.terminal_summary
        async with session_scope() as session:
            after = await session.get(ResultPage, page.id)
            assert await result_snapshot(session, after) == before
