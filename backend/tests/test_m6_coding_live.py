"""Opt-in provider acceptance of leased, journaled supervised coding."""
import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys

import pytest

from app.config import settings
from app.jarvis_tasks.acceptance import SCHEMA, candidate_identity, resolve_test_configuration
from app.services.task_pool_supervisor import TaskPoolSupervisor
from app.services.task_service import task_service


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("JARVIS_LIVE_M6") != "1", reason="authorized live M6 provider check is opt-in")
async def test_supervised_project_failure_repair_and_host_restart(tmp_path, monkeypatch):
    candidate = candidate_identity()
    configuration = None
    report_path = Path(os.environ["JARVIS_M6_REPORT"])
    evidence = {"supervised": True, "journaled": True, "command_boundary_passed": False}
    passed = False
    stop, running = None, None
    task_id = None
    try:
        if os.name != "nt" or not settings.openai_api_key:
            pytest.fail("M6 requires the prepared Windows host and authorized API profile")
        project = tmp_path / "project"
        project.mkdir()
        (project / "addition.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        (project / "test_addition.py").write_text(
            "import unittest\nfrom addition import add\nclass AdditionTest(unittest.TestCase):\n"
            "    def test_sum(self):\n        self.assertEqual(add(2, 3), 5)\n", encoding="utf-8")
        configuration = resolve_test_configuration("coding_provider", paths={"task_project_roots": str(project),
            "codex_profiles_dir": str(tmp_path / "profiles"), "task_workspaces_dir": str(tmp_path / "workspaces"),
            "task_context_blobs_dir": settings.task_context_blobs_dir, "conversation_workspaces_dir": settings.conversation_workspaces_dir})
        values = {**configuration["settings"], "worker_poll_interval_seconds": .1}
        for key, value in values.items():
            monkeypatch.setattr(settings, key, value)
            monkeypatch.setenv(key.upper(), str(value).lower() if isinstance(value, bool) else str(value))
        candidate = candidate_identity()
        evidence["execution_mode"] = settings.task_command_mode
        evidence["test_configuration"] = configuration
        monkeypatch.setenv("DATABASE_URL", settings.database_url)
        monkeypatch.setenv("ALEMBIC_INI_PATH", str(settings.alembic_config_path))
        # The sandboxed worker must import the tested backend from its own cwd.
        argv = json.dumps([sys.executable, "-m", "unittest", "-v"])
        task = await task_service.dispatch(origin_surface="acceptance", idempotency_key="m6-coding-initial",
            targets=(("directory", str(project), True),),
            goal=f"Discover the leased project with project_roots. Run project_command argv {argv} "
                "with operation_key initial-failing-test before changing source. Inspect the failure, fix addition.py "
                "with project_write operation_key repair-addition, then run the same tests with operation_key repaired-test. "
                "Report the observed result. Do not modify the test or use native tools.")
        task_id = task.task.id

        async def start():
            event = asyncio.Event()
            return event, asyncio.create_task(TaskPoolSupervisor().run(event))

        async def completed(ordinal):
            deadline = asyncio.get_running_loop().time() + 180
            while asyncio.get_running_loop().time() < deadline:
                if running.done():
                    await running
                detail = await task_service.get_task(task_id)
                if detail.task.status in {"failed", "interrupted"}:
                    pytest.fail(detail.task.terminal_summary or detail.task.status)
                if len(detail.runs) == ordinal and detail.task.status == "completed":
                    return detail
                await asyncio.sleep(.2)
            pytest.fail("Supervised coding did not reach a durable terminal outcome")

        stop, running = await start()
        first = await completed(1)
        commands = [op for op in first.operations if op.operation_type == "broker.command" and op.state == "succeeded"]
        assert len(commands) >= 2
        evidence.update(failing_test_exit=commands[0].result_json["exit_code"],
            passing_test_exit=commands[-1].result_json["exit_code"], command_boundary_passed=True)
        assert evidence["failing_test_exit"] != 0 and evidence["passing_test_exit"] == 0
        assert all(op.result_json.get("execution_mode") == settings.task_command_mode for op in commands)
        assert any(op.operation_type == "broker.project_write" and op.state == "succeeded" for op in first.operations)
        stop.set()
        await asyncio.wait_for(running, 30)
        await task_service.continue_task(task_id, origin="acceptance", idempotency_key="m6-coding-restart",
            content=f"After host restart, inspect addition.py and run project_command argv {argv} "
                "with operation_key restarted-test. Confirm its result without changing source or tests.")
        stop, running = await start()
        final = await completed(2)
        assert first.attempts[0].worker_id != final.attempts[-1].worker_id
        assert any(op.operation_type == "broker.command" and op.result_json.get("exit_code") == 0
            and op.execution_run_id == final.runs[-1].id for op in final.operations)
        artifacts = report_path.parent / "coding-artifacts"
        artifacts.mkdir(parents=True)
        for name in ("addition.py", "test_addition.py"):
            (artifacts / name).write_bytes((project / name).read_bytes())
        evidence.update(task_id=task_id, restart_same_task_continuation=True,
            runs=[run.id for run in final.runs], commands=[{"id": op.id, "state": op.state,
                "result": op.result_json} for op in final.operations if op.operation_type == "broker.command"],
            source_sha256=sha256((project / "addition.py").read_bytes()).hexdigest())
        passed = True
    finally:
        if stop is not None:
            stop.set()
        if running is not None:
            await asyncio.wait_for(running, 30)
        if task_id:
            detail = await task_service.get_task(task_id)
            evidence["terminal_status"] = detail.task.status
            evidence["terminal_summary"] = detail.task.terminal_summary
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps({"schema": SCHEMA, "gate": os.getenv("JARVIS_ACCEPTANCE_GATE", "M10"),
            "candidate": candidate, "test_configuration": configuration, "finished_at": datetime.now(timezone.utc).isoformat(),
            "checks": [{"id": "coding_provider", "status": "passed" if passed else "failed", "evidence": evidence}]}, indent=2), encoding="utf-8")
