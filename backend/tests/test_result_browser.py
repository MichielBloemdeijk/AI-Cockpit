"""Opt-in actual Next export + FastAPI + Playwright journey, with no paid calls.

RUN_M5_BROWSER=1 .venv/Scripts/python.exe -m pytest tests/test_result_browser.py -q -s
Test-only controls are never mounted in the production app.
"""
from __future__ import annotations

import asyncio
from pydantic import SecretStr
import os
from pathlib import Path
import socket
from threading import Thread
from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
import pytest
from sqlalchemy import select
import uvicorn

from app.config import settings
from app.db.session import session_scope
from app.db.tables import ResultPage
from app.jarvis_tasks.task_mcp import _call
from app.main import app
from app.services.auth import require_auth
from app.services.task_service import task_service
from app.services.worker_coordinator import worker_coordinator

pytestmark = [pytest.mark.browser, pytest.mark.skipif(os.environ.get("RUN_M5_BROWSER") != "1", reason="opt-in production browser/build acceptance")]


async def checked_process(*args, cwd, timeout=60):
    process = await asyncio.create_subprocess_exec(*args, cwd=cwd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
        assert process.returncode == 0, output.decode(errors="replace")
        return output
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.asyncio
async def test_static_export_future_id_live_update_refresh_followups_and_fencing(monkeypatch, caplog):
    from test_m4_workflows import admit, content, researched
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    await checked_process("node", str(frontend / "node_modules/next/dist/bin/next"), "build", "result-export", cwd=frontend)
    monkeypatch.setattr(settings, "auth_password", SecretStr("m5-browser-fixture"))
    monkeypatch.setattr(settings, "session_cookie_secure", False)
    state = {}
    fixture = FastAPI(dependencies=[Depends(require_auth)])

    @fixture.post("/_fixture/admit")
    async def create():
        args, worker = await admit(monkeypatch, "research_screen@1", password="m5-browser-fixture")
        state.update(args=args, worker=worker, initial=SimpleNamespace(**vars(args)))
        async with session_scope() as session:
            page = (await session.execute(select(ResultPage).where(ResultPage.task_id == args.task_id))).scalar_one()
            return {"url": f"/results/{page.public_id}", "public_id": page.public_id, "task_id": args.task_id}

    @fixture.post("/_fixture/publish/{revision}")
    async def publish(revision: int):
        args = state["args"]
        if revision == 1:
            _, state["source"] = await researched(args)
        payload = content(args, state["source"]) | {"title": f"M5 revision {revision}", "body": f"Published body {revision}", "operation_key": f"publish-{revision}"}
        if revision > 1:
            payload["base_revision"] = revision - 1
        await _call("result_publish", payload, args)
        await worker_coordinator.release_claim(state["worker"].worker_id, args.attempt_id, claim_token=args.claim_token, writers_stopped=True)
        return {"published": revision}

    @fixture.post("/_fixture/followup")
    async def followup():
        args = state["args"]
        await task_service.continue_task(args.task_id, content="Revise the result", origin="test", idempotency_key=os.urandom(16).hex())
        claim = await worker_coordinator.claim_next(state["worker"].worker_id)
        args.run_id, args.attempt_id, args.claim_token = claim.task.execution_run_id, claim.task.attempt_id, claim.task.claim_token
        return {"run_id": args.run_id}

    @fixture.post("/_fixture/fail")
    async def fail():
        args = state["args"]
        await _call("task_finish", {"summary": "Private failure diagnostics", "status": "failed", "operation_key": "failed"}, args)
        await worker_coordinator.release_claim(state["worker"].worker_id, args.attempt_id, claim_token=args.claim_token, writers_stopped=True)
        return {"failed": True}

    @fixture.post("/_fixture/late")
    async def late():
        try:
            await _call("task_finish", {"summary": "Late initial-run failure", "status": "failed", "operation_key": "late"}, state["initial"])
        except ValueError:
            return {"blocked": True}
        raise AssertionError("Stale initial attempt was not fenced")

    # Login must remain accessible without a cookie; the mounted app has its own
    # normal endpoint auth. Only fixture controls depend on require_auth.
    fixture.mount("/_result-assets", StaticFiles(directory=str(frontend / "result-export/out")))
    fixture.mount("/", app)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(fixture, lifespan="off", log_level="warning"))
    thread = Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        assert server.started
        output = await checked_process("node", str(frontend / "scripts/verify-result-platform.mjs"), f"http://127.0.0.1:{port}", cwd=frontend)
        print(output.decode().strip())
    finally:
        server.should_exit = True
        await asyncio.to_thread(thread.join, 10)
        sock.close()
        assert not thread.is_alive()
    assert not [entry for entry in caplog.records if entry.levelno >= 40], "Browser journey must not leave backend cleanup/transport errors"
