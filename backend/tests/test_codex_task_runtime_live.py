"""Opt-in billed smoke test for the guarded production Codex task adapter."""
from __future__ import annotations

import os
import shutil
import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
import pytest

pytestmark = pytest.mark.provider
from PIL import Image

from app.config import settings
from app.db.session import session_scope
from app.db.tables import TaskTurn
from app.main import app
from app.services.codex_task_runtime import codex_task_runtime
from app.services.task_service import task_service
from app.services.task_worker import TaskWorker
from app.services.task_pool_supervisor import TaskPoolSupervisor
from app.services.worker_coordinator import worker_coordinator
from app.services.task_workflows import load_workflow
from app.services.task_capture import CaptureCollection, task_capture_service
from app.db.tables import ResultPage
from sqlalchemy import select


@contextmanager
def _live_directory(parent: Path):
    directory = parent / f"jarvis-live-{uuid4()}"
    directory.mkdir()
    try:
        yield directory
    finally:
        if directory.parent == parent:
            shutil.rmtree(directory, ignore_errors=True)


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("JARVIS_LIVE_CODEX") != "1", reason="billed live Codex test is opt-in")
async def test_selected_task_mode_api_write_and_same_task_followup() -> None:
    if not settings.openai_api_key:
        pytest.skip("No authorized API credential is configured")
    original_profiles = settings.codex_profiles_dir
    original_workspaces = settings.task_workspaces_dir
    live_root = settings.backend_root / "data"
    live_root.mkdir(parents=True, exist_ok=True)
    try:
        with _live_directory(live_root) as live_dir:
            settings.codex_profiles_dir = str(Path(live_dir) / "codex-profiles")
            settings.task_workspaces_dir = str(Path(live_dir) / "task-workspaces")
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                await client.post("/api/auth/login", json={"password": ""})
                admitted = await client.post(
                    "/api/tasks",
                    json={
                        "goal": "Create proof.txt in this task workspace containing only the word READY. Then report completion.",
                        "idempotency_key": "live-native-initial",
                    },
                )
                assert admitted.status_code == 201, admitted.text
                task_id = admitted.json()["id"]
                worker = TaskWorker(runtime=codex_task_runtime)
                assert await worker.activate(process_identity="live-native-fixture")
                first = await worker.run_once()
                assert first.status == "completed", (await task_service.get_task(task_id)).task.terminal_summary

                detail = await task_service.get_task(task_id)
                assert detail is not None
                workspace = Path(detail.attempts[0].workspace_path)
                assert (workspace / "proof.txt").read_text(encoding="utf-8").strip() == "READY"
                assert len(detail.events) > 2

                followup = await client.post(
                    f"/api/tasks/{task_id}/turns",
                    json={
                        "content": "Read proof.txt in the same workspace and report its exact content without changing it.",
                        "idempotency_key": "live-native-followup",
                    },
                )
                assert followup.status_code == 201, followup.text
                second = await worker.run_once()
                assert second.status == "completed", (await task_service.get_task(task_id)).task.terminal_summary
                final = await client.get(f"/api/tasks/{task_id}")
                assert final.status_code == 200
                assert len(final.json()["runs"]) == 2
                assert final.json()["id"] == task_id
                assert all(run["context_reference"] for run in final.json()["runs"])
    finally:
        settings.codex_profiles_dir = original_profiles
        settings.task_workspaces_dir = original_workspaces


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("JARVIS_LIVE_CODEX") != "1", reason="billed live Codex test is opt-in")
async def test_constrained_workflow_publishes_typed_result_and_continues() -> None:
    if not settings.openai_api_key:
        pytest.skip("No authorized API credential is configured")
    originals = (
        settings.codex_profiles_dir, settings.task_workspaces_dir,
        settings.task_executor, settings.task_workflow_allowlist,
    )
    root = settings.backend_root / "data"
    root.mkdir(parents=True, exist_ok=True)
    definition = load_workflow("text_report@1")
    try:
        with _live_directory(root) as live_dir:
            settings.codex_profiles_dir = str(live_dir / "codex-profiles")
            settings.task_workspaces_dir = str(live_dir / "task-workspaces")
            settings.task_executor = "codex"
            settings.task_workflow_allowlist = f"text_report@1:{definition.digest}"
            task = await task_service.dispatch(
                goal=("Call jarvis_task.result_publish with title Proof, body READY, "
                      "source_event_sequences [], and operation_key proof-initial."),
                origin_surface="test", idempotency_key="live-workflow-initial",
                execution_kind="workflow", execution_config={"definition": "text_report@1"},
            )
            worker = TaskWorker(runtime=codex_task_runtime)
            assert await worker.activate(process_identity="live-workflow-fixture")
            first = await worker.run_once()
            assert first.status == "completed", (await task_service.get_task(task.task.id)).task.terminal_summary
            detail = await task_service.get_task(task.task.id)
            assert detail is not None and detail.artifacts[-1].content_json["schema"] == "text-report.v1"
            assert detail.artifacts[-1].content_json["content"]["body"] == "READY"
            assert not list(Path(detail.attempts[0].workspace_path).glob("*.txt"))
            # Synthetic retained turns make the real SDK compactor run before
            # this follow-up pins its portable context.
            async with session_scope() as session:
                for sequence in range(3, 30):
                    session.add(TaskTurn(
                        id=str(uuid4()), task_id=task.task.id, sequence=sequence,
                        role="user", content=f"Background note {sequence}: the first report said READY.",
                        origin="test", execution_run_id=detail.runs[0].id,
                    ))
            await task_service.continue_task(
                task.task.id,
                content=("Call jarvis_task.result_publish with title Follow-up, body SECOND, "
                         "source_event_sequences [], and operation_key proof-followup."),
                idempotency_key="live-workflow-followup", origin="test", execution_kind="workflow",
            )
            second = await worker.run_once()
            assert second.status == "completed"
            final = await task_service.get_task(task.task.id)
            assert final is not None and len(final.runs) == 2
            assert final.artifacts[-1].content_json["content"]["body"] == "SECOND"
            assert all(run.result_reference for run in final.runs)
            summaries = [artifact for artifact in final.artifacts
                         if artifact.artifact_type == "task.context.summary"]
            assert len(summaries) == 2
            assert max(item.content_json["through_turn_sequence"] for item in summaries) == 17
            assert final.runs[-1].context_reference
    finally:
        (
            settings.codex_profiles_dir, settings.task_workspaces_dir,
            settings.task_executor, settings.task_workflow_allowlist,
        ) = originals


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("JARVIS_LIVE_CODEX") != "1", reason="billed live Codex test is opt-in")
async def test_screen_document_uploaded_image_reaches_real_codex_and_result() -> None:
    """Backend/model integration; the separate emulator gate proves native capture."""
    if not settings.openai_api_key:
        pytest.skip("No authorized API credential is configured")
    original = (settings.codex_profiles_dir, settings.task_workspaces_dir,
                settings.task_executor, settings.task_workflow_allowlist)
    definition = load_workflow("screen_document@1")
    live_root = settings.backend_root / "data" / f"jarvis-live-{uuid4()}"
    live_root.mkdir(parents=True)
    try:
        settings.codex_profiles_dir = str(live_root / "codex-profiles")
        settings.task_workspaces_dir = str(live_root / "task-workspaces")
        settings.task_executor = "codex"
        settings.task_workflow_allowlist = f"screen_document@1:{definition.digest}"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as admin:
            assert (await admin.post("/api/auth/login", json={"password": ""})).status_code == 200
            code = (await admin.post("/api/jarvis/devices/pairing-code")).json()["code"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as device:
            pair = (await device.post("/api/jarvis/devices/pair", json={"code": code,
                "label": "Live Codex image fixture"})).json()
            device.headers["Authorization"] = f"Bearer {pair['credential']}"
            assert (await device.patch(f"/api/jarvis/devices/{pair['device_id']}",
                json={"lock_state": "unlocked"})).status_code == 200
            activation_id, capture_id = str(uuid4()), str(uuid4())
            live = (await device.post("/api/jarvis/live/calls", json={
                "controller_id": pair["device_id"], "provider_adapter": "fake",
                "activation_context": {"source": "system", "activation_id": activation_id},
            })).json()["session"]
            now = datetime.now(timezone.utc)
            assert (await device.put(f"/api/jarvis/captures/{capture_id}/status", json={
                "activation_id": activation_id, "status": "captured", "revision": 1,
                "captured_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=80)).isoformat(),
                "session_id": live["id"], "generation": live["generation"],
            })).status_code == 200
            binding = await task_capture_service.preflight(
                device_id=pair["device_id"], session_id=live["id"],
                generation=live["generation"], activation_id=activation_id,
                capture_id=capture_id,
            )
            task = await task_service.dispatch(
                goal="Describe the dominant color of this screen in one sentence.",
                origin_surface="jarvis_voice", idempotency_key=f"live-screen-{uuid4()}",
                execution_kind="workflow", execution_config={"definition": "screen_document@1"},
                capture_binding=CaptureCollection((("invocation", binding),), missing_roles=("latest",)),
            )
            assert task.task.status == "waiting_for_input"
            request = (await device.get("/api/jarvis/context-requests/pending")).json()["requests"][0]
            image = BytesIO()
            Image.new("RGB", (160, 120), (0, 40, 220)).save(image, format="JPEG")
            jpeg = image.getvalue()
            upload = await device.put(
                f"/api/jarvis/context-requests/{request['request_id']}/content",
                content=jpeg,
                headers={"Content-Type": "image/jpeg", "X-Upload-Token": request["upload_token"],
                         "X-Content-SHA256": sha256(jpeg).hexdigest(),
                         "X-Capture-Id": capture_id, "X-Activation-Id": activation_id,
                         "X-Generation": str(live["generation"]),
                         "X-Captured-At": now.isoformat(), "X-Capture-Monotonic-Ms": "123.0"},
            )
            assert upload.status_code == 200, upload.text
            worker = TaskWorker(runtime=codex_task_runtime)
            assert await worker.activate(process_identity="live-screen-document")
            finished = await worker.run_once()
            assert finished.status == "completed", (await task_service.get_task(task.task.id)).task.terminal_summary
            async with session_scope() as session:
                page = (await session.execute(select(ResultPage).where(
                    ResultPage.task_id == task.task.id))).scalar_one()
                assert page.status == "ready"
                result_url = f"/results/{page.public_id}"
            result = await device.get(f"/api{result_url}")
            assert result.status_code == 200, result.text
            assert "blue" in result.json()["content"]["body"].lower()
    finally:
        (settings.codex_profiles_dir, settings.task_workspaces_dir,
         settings.task_executor, settings.task_workflow_allowlist) = original
        if live_root.parent == settings.backend_root / "data":
            shutil.rmtree(live_root, ignore_errors=True)


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("JARVIS_LIVE_CODEX") != "1", reason="billed live Codex test is opt-in")
@pytest.mark.skipif(os.name != "nt", reason="Windows process-tree pool proof")
async def test_native_task_runs_across_supervised_worker_replacement(monkeypatch) -> None:
    if not settings.openai_api_key:
        pytest.skip("No authorized API credential is configured")
    originals = (
        settings.codex_profiles_dir,
        settings.task_workspaces_dir,
        settings.task_executor,
        settings.worker_poll_interval_seconds,
    )
    live_root = settings.backend_root / "data"
    live_root.mkdir(parents=True, exist_ok=True)
    with _live_directory(live_root) as live_dir:
        settings.codex_profiles_dir = str(live_dir / "codex-profiles")
        settings.task_workspaces_dir = str(live_dir / "task-workspaces")
        settings.task_executor = "codex"
        settings.worker_poll_interval_seconds = 0.1
        monkeypatch.setenv("DATABASE_URL", settings.database_url)
        monkeypatch.setenv("ALEMBIC_INI_PATH", str(settings.alembic_config_path))
        monkeypatch.setenv("TASK_WORKSPACES_DIR", settings.task_workspaces_dir)
        monkeypatch.setenv("CODEX_PROFILES_DIR", settings.codex_profiles_dir)
        monkeypatch.setenv("TASK_EXECUTOR", "codex")
        supervisor = TaskPoolSupervisor(coordinator=worker_coordinator)
        stop = asyncio.Event()
        running = asyncio.create_task(supervisor.run(stop))
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                await client.post("/api/auth/login", json={"password": ""})
                admitted = await client.post(
                    "/api/tasks",
                    json={
                        "goal": "Create pool-proof.txt in this task's private workspace with content POOL_READY. Use the configured local coding tools and then report completion.",
                        "idempotency_key": "live-pool-first",
                    },
                )
                assert admitted.status_code == 201, admitted.text
                task_id = admitted.json()["id"]

                async def wait_completed(run_ordinal: int) -> None:
                    deadline = asyncio.get_running_loop().time() + 90
                    while asyncio.get_running_loop().time() < deadline:
                        if running.done():
                            await running
                        detail = await task_service.get_task(task_id)
                        if detail is not None:
                            if detail.task.status in {"failed", "interrupted"}:
                                pytest.fail(detail.task.terminal_summary or detail.task.status)
                            if detail.task.status == "completed" and len(detail.runs) == run_ordinal:
                                return
                        await asyncio.sleep(0.2)
                    pytest.fail("Supervised Codex task did not complete")

                await wait_completed(1)
                first = await task_service.get_task(task_id)
                assert first is not None
                workspace = Path(first.attempts[0].workspace_path)
                assert (workspace / "pool-proof.txt").read_text(encoding="utf-8").strip() == "POOL_READY"
                followup = await client.post(
                    f"/api/tasks/{task_id}/turns",
                    json={
                        "content": "Read pool-proof.txt in this task's private workspace and report its exact content without modifying it.",
                        "idempotency_key": "live-pool-second",
                    },
                )
                assert followup.status_code == 201, followup.text
                await wait_completed(2)
                final = await task_service.get_task(task_id)
                assert final is not None
                assert len(final.runs) == 2
                assert all(run.context_reference for run in final.runs)
                assert final.attempts[0].worker_id != final.attempts[-1].worker_id
                assert any(operation.operation_type == "broker.project_write" and operation.state == "succeeded" for operation in final.operations)
        finally:
            stop.set()
            await asyncio.wait_for(running, timeout=20)
            (
                settings.codex_profiles_dir,
                settings.task_workspaces_dir,
                settings.task_executor,
                settings.worker_poll_interval_seconds,
            ) = originals
