"""Actual private subscription execution then simulated denial and real API handoff."""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import settings
from app.db.repositories.tasks import TaskRepository
from app.db.session import session_scope
from app.db.tables import RuntimeCredentialLease
from app.jarvis_tasks.acceptance import SCHEMA, candidate_identity, resolve_test_configuration as configuration_for
from app.services.runtime_auth_routes import subscription_profile, profile_revision, select_route
from app.services.task_pool_supervisor import TaskPoolSupervisor
from app.services.task_service import task_service
from app.services.windows_job import process_creation_stamp, process_has_exited


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("JARVIS_SUBSCRIPTION_LIVE") != "1", reason="dedicated Jarvis sign-in is required")
async def test_real_subscription_then_safe_api_boundary(tmp_path, monkeypatch):
    report_path = Path(os.environ["JARVIS_SUBSCRIPTION_REPORT"])
    evidence = {"account_type": "unknown", "separate_profile": False, "shared_owner_competition": False,
        "same_task_safe_cutover": False, "quota_exhaustion": "simulated_without_spending"}
    configuration, candidate = None, candidate_identity()
    stop, running, passed = asyncio.Event(), None, False
    try:
        assert (subscription_profile() / "auth.json").is_file(), "Sign into the dedicated Jarvis profile with subscription-login"
        configuration = configuration_for("subscription_provider", paths={
            "codex_profiles_dir": str(tmp_path / "profiles"), "task_workspaces_dir": str(tmp_path / "workspaces"),
            "task_context_blobs_dir": settings.task_context_blobs_dir,
            "conversation_workspaces_dir": settings.conversation_workspaces_dir})
        for key, value in configuration["settings"].items():
            monkeypatch.setattr(settings, key, value)
            monkeypatch.setenv(key.upper(), str(value).lower() if isinstance(value, bool) else str(value))
        monkeypatch.setenv("DATABASE_URL", settings.database_url)
        monkeypatch.setenv("ALEMBIC_INI_PATH", str(settings.alembic_config_path))
        candidate = candidate_identity()
        running = asyncio.create_task(TaskPoolSupervisor().run(stop))
        initial = await task_service.dispatch(goal="Use project_write with target_id workspace, path subscription.txt, content READY and operation_key subscription-proof. Then report completion.",
            origin_surface="acceptance", idempotency_key="subscription-initial")
        async def complete(ordinal, *, check_competition=False):
            async with asyncio.timeout(180):
                while True:
                    if running.done():
                        await running
                    detail = await task_service.get_task(initial.task.id)
                    assert detail.task.status not in {"failed", "interrupted"}, detail.task.terminal_summary
                    if check_competition and not evidence["shared_owner_competition"]:
                        async with session_scope() as session:
                            lease = (await session.execute(select(RuntimeCredentialLease).where(
                                RuntimeCredentialLease.owner_kind == "worker"))).scalar_one_or_none()
                        if lease and process_has_exited(lease.pid, int(lease.creation_stamp)) is False:
                            competing = await select_route(owner_kind="background", owner_id="acceptance-competition",
                                pid=os.getpid(), stamp=process_creation_stamp(os.getpid()), contained=True,
                                api_profile=tmp_path / "competition-api")
                            assert competing.route == "api" and competing.reason == "subscription_busy"
                            evidence["shared_owner_competition"] = True
                    if len(detail.runs) == ordinal and detail.task.status == "completed":
                        assert not check_competition or evidence["shared_owner_competition"], "Active subscription ownership was not observed"
                        return detail
                    await asyncio.sleep(.2)
        first = await complete(1, check_competition=True)
        routes = [event.payload_json for event in first.events if event.event_type == "task.auth.selected"]
        assert routes and routes[-1]["route"] == "subscription", routes
        # Runtime validates actual account.type == chatgpt before starting this turn.
        evidence.update(account_type="chatgpt", separate_profile=True, task_id=initial.task.id)
        stop.set()
        await asyncio.wait_for(running, 30)
        async with session_scope() as session:
            await TaskRepository(session).append_event(task_id=initial.task.id, event_type="task.auth.unavailable",
                payload_json={"reason": "subscription_quota_exhausted", "profile_revision": profile_revision(),
                    "next_boundary_only": True, "automatic_replay": False}, idempotency_key="simulated-provider-exhaustion")
        await task_service.continue_task(initial.task.id, origin="acceptance", idempotency_key="subscription-api-continuation",
            content="Use project_read with target_id workspace to inspect subscription.txt. Use project_write with target_id workspace, path api.txt, content READY and operation_key api-proof. Report completion.")
        stop = asyncio.Event()
        running = asyncio.create_task(TaskPoolSupervisor().run(stop))
        final = await complete(2)
        routes = [event.payload_json for event in final.events if event.event_type == "task.auth.selected"]
        assert routes[-1]["route"] == "api" and routes[-1]["reason"] == "subscription_quota_exhausted"
        assert len(final.runs) == 2 and first.attempts[0].worker_id != final.attempts[-1].worker_id
        evidence.update(same_task_safe_cutover=True, routes=routes, run_ids=[run.id for run in final.runs])
        passed = True
    finally:
        stop.set()
        if running:
            await asyncio.wait_for(running, 30)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps({"schema": SCHEMA, "gate": os.getenv("JARVIS_ACCEPTANCE_GATE", "M10"),
            "candidate": candidate, "test_configuration": configuration, "finished_at": datetime.now(timezone.utc).isoformat(),
            "checks": [{"id": "subscription_provider", "status": "passed" if passed else "failed", "evidence": evidence}]}, indent=2))
