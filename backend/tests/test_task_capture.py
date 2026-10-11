from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
import base64
from types import SimpleNamespace
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
import pytest
from PIL import Image

from app.main import app
from app.config import settings
from app.db.session import session_scope
from app.db.tables import NotificationOutbox, ResultPage, ResultPageRevision, TaskContextRequest
from app.services.task_capture import CaptureCollection, task_capture_service
from app.services.task_service import task_service
from app.services.task_workflows import load_workflow
from app.services.jarvis_brain import restricted_brain_gateway
from app.services.jarvis_tool_catalog import get_jarvis_tool_definitions
from app.services.worker_coordinator import worker_coordinator
from app.services.task_presentations import task_presentation_service
from app.jarvis_tasks.task_mcp import _call, _image_result
from sqlalchemy import select


@pytest.mark.asyncio
async def test_device_capture_report_preflight_and_revision_fence() -> None:
    transport = ASGITransport(app=app)
    activation_id, capture_id = str(uuid4()), str(uuid4())
    async with AsyncClient(transport=transport, base_url="http://testserver") as admin:
        assert (await admin.post("/api/auth/login", json={"password": ""})).status_code == 200
        code = (await admin.post("/api/jarvis/devices/pairing-code")).json()["code"]
        forbidden = await admin.put(f"/api/jarvis/captures/{capture_id}/status", json={
            "activation_id": activation_id, "status": "pending", "revision": 1,
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=80)).isoformat(),
        })
        assert forbidden.status_code == 401
    async with AsyncClient(transport=transport, base_url="http://testserver") as device:
        paired = (await device.post("/api/jarvis/devices/pair", json={"code": code, "label": "Pixel"})).json()
        device.headers["Authorization"] = f"Bearer {paired['credential']}"
        await device.patch(f"/api/jarvis/devices/{paired['device_id']}", json={"lock_state": "unlocked"})
        expires = datetime.now(timezone.utc) + timedelta(seconds=80)
        pending = {
            "activation_id": activation_id, "status": "pending", "revision": 1,
            "expires_at": expires.isoformat(),
        }
        first = await device.put(f"/api/jarvis/captures/{capture_id}/status", json=pending)
        assert first.status_code == 200, first.text
        assert first.json()["session_id"] is None
        live = await device.post("/api/jarvis/live/calls", json={
            "controller_id": paired["device_id"], "provider_adapter": "fake",
            "activation_context": {"source": "system", "activation_id": activation_id},
        })
        assert live.status_code == 200, live.text
        session = live.json()["session"]
        assert session["activation_id"] == activation_id
        with pytest.raises(ValueError, match="deadline"):
            await task_capture_service.preflight(
                device_id=paired["device_id"], session_id=session["id"],
                generation=session["generation"], activation_id=activation_id,
                capture_id=capture_id, wait_seconds=0.05,
            )
        assert await task_service.list_tasks() == []
        captured = {**pending, "status": "captured", "revision": 2,
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "session_id": session["id"], "generation": session["generation"]}
        ready = await device.put(f"/api/jarvis/captures/{capture_id}/status", json=captured)
        assert ready.status_code == 200, ready.text
        binding = await task_capture_service.preflight(
            device_id=paired["device_id"], session_id=session["id"],
            generation=session["generation"], activation_id=activation_id,
            capture_id=capture_id,
        )
        assert binding.capture_id == capture_id and binding.capture_revision == 2
        assert (await device.put(f"/api/jarvis/captures/{capture_id}/status", json=captured)).status_code == 200
        assert (await device.put(f"/api/jarvis/captures/{capture_id}/status", json=pending)).status_code == 409
        unavailable = {**captured, "status": "unavailable", "reason": "callback_null", "revision": 3}
        assert (await device.put(f"/api/jarvis/captures/{capture_id}/status", json=unavailable)).status_code == 200
        with pytest.raises(ValueError, match="unavailable"):
            await task_capture_service.preflight(
                device_id=paired["device_id"], session_id=session["id"],
                generation=session["generation"], activation_id=activation_id,
                capture_id=capture_id, wait_seconds=0,
            )
        early_id = str(uuid4())
        early = {**pending, "status": "unavailable", "reason": "platform_or_app_policy", "revision": 2}
        assert (await device.put(f"/api/jarvis/captures/{early_id}/status", json=early)).status_code == 200
        bound = {**early, "revision": 3, "session_id": session["id"], "generation": session["generation"]}
        result = await device.put(f"/api/jarvis/captures/{early_id}/status", json=bound)
        assert result.status_code == 200 and result.json()["session_id"] == session["id"]
        assert (await device.put(f"/api/jarvis/captures/{early_id}/status", json={**captured, "revision": 4})).status_code == 409
        assert (await device.put(f"/api/jarvis/captures/{early_id}/status", json={**bound, "revision": 4, "reason": "locked"})).status_code == 409
    from app.db.tables import ResultMetric
    from app.services.result_telemetry import observation_queue
    assert await observation_queue.drain()
    async with session_scope() as observation_session:
        measurements = list((await observation_session.execute(select(ResultMetric).where(ResultMetric.kind == "capture_preflight"))).scalars())
        assert {row.values_json["outcome"] for row in measurements} >= {"captured", "timeout", "rejected"}
        assert all(row.result_page_id is None and row.duration_ms >= 0 for row in measurements)


@pytest.mark.asyncio
async def test_captured_workflow_admission_waits_for_upload_and_targets_event(monkeypatch) -> None:
    screen_definition = load_workflow("screen_document@1")
    monkeypatch.setattr(settings, "task_executor", "codex")
    monkeypatch.setattr(settings, "task_workflow_allowlist", f"screen_document@1:{screen_definition.digest}")
    transport = ASGITransport(app=app)
    activation_id, capture_id = str(uuid4()), str(uuid4())
    async with AsyncClient(transport=transport, base_url="http://testserver") as admin:
        assert (await admin.post("/api/auth/login", json={"password": ""})).status_code == 200
        code = (await admin.post("/api/jarvis/devices/pairing-code")).json()["code"]
    async with AsyncClient(transport=transport, base_url="http://testserver") as device:
        paired = (await device.post("/api/jarvis/devices/pair", json={"code": code, "label": "Pixel"})).json()
        device.headers["Authorization"] = f"Bearer {paired['credential']}"
        assert (await device.patch(f"/api/jarvis/devices/{paired['device_id']}", json={"lock_state": "unlocked"})).status_code == 200
        live = await device.post("/api/jarvis/live/calls", json={
            "controller_id": paired["device_id"], "provider_adapter": "fake",
            "activation_context": {"source": "system", "activation_id": activation_id},
        })
        assert live.status_code == 200, live.text
        live_session = live.json()["session"]
        now = datetime.now(timezone.utc)
        report = await device.put(f"/api/jarvis/captures/{capture_id}/status", json={
            "activation_id": activation_id, "status": "captured", "revision": 1,
            "captured_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=80)).isoformat(),
            "session_id": live_session["id"], "generation": live_session["generation"],
        })
        assert report.status_code == 200, report.text
        binding = await task_capture_service.preflight(
            device_id=paired["device_id"], session_id=live_session["id"],
            generation=live_session["generation"], activation_id=activation_id,
            capture_id=capture_id,
        )
        # A late metadata-only report for the same immutable captured image
        # must not turn a successful preflight into a rejected admission.
        same_image = await device.put(f"/api/jarvis/captures/{capture_id}/status", json={
            "activation_id": activation_id, "status": "captured", "revision": 2,
            "captured_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=80)).isoformat(),
            "session_id": live_session["id"], "generation": live_session["generation"],
        })
        assert same_image.status_code == 200, same_image.text
        created = await task_service.dispatch(
            goal="Describe this screen", origin_surface="jarvis_voice", idempotency_key="capture-workflow-1",
            execution_kind="workflow", execution_config={"definition": "screen_document@1"},
            capture_binding=CaptureCollection((("invocation", binding),), missing_roles=("latest",)),
        )
        assert created.task.status == "waiting_for_input"
        assert created.task.status_reason == "context_upload_pending"
        repeated = await task_service.dispatch(
            goal="Describe this screen", origin_surface="jarvis_voice", idempotency_key="capture-workflow-1",
            execution_kind="workflow", execution_config={"definition": "screen_document@1"},
            capture_binding=CaptureCollection((("invocation", binding),), missing_roles=("latest",)),
        )
        assert repeated.task.id == created.task.id
        async with session_scope() as session:
            requests = list((await session.execute(select(TaskContextRequest))).scalars().all())
            assert len(requests) == 1
            assert requests[0].task_id == created.task.id
        replay = await device.get("/api/jarvis/live/replay")
        assert replay.status_code == 200
        targeted = [event for event in replay.json()["events"] if event["event_type"] == "context.requested"]
        assert len(targeted) == 1 and targeted[0]["payload"]["capture_id"] == capture_id
        async with AsyncClient(transport=transport, base_url="http://testserver") as browser:
            assert (await browser.post("/api/auth/login", json={"password": ""})).status_code == 200
            browser_replay = await browser.get("/api/jarvis/live/replay")
        redacted = [event for event in browser_replay.json()["events"] if event["event_type"] == "context.requested"]
        assert len(redacted) == 1 and "capture_id" not in redacted[0]["payload"]
        pending_requests = (await device.get("/api/jarvis/context-requests/pending")).json()["requests"]
        assert len(pending_requests) == 1
        pending_request = pending_requests[0]
        image = BytesIO()
        Image.new("RGB", (32, 16), (17, 31, 47)).save(image, format="JPEG")
        jpeg = image.getvalue()
        headers = {
            "Content-Type": "image/jpeg", "X-Upload-Token": pending_request["upload_token"],
            "X-Content-SHA256": sha256(jpeg).hexdigest(),
            "X-Capture-Id": capture_id, "X-Activation-Id": activation_id,
            "X-Generation": str(live_session["generation"]),
            "X-Captured-At": now.isoformat(), "X-Capture-Monotonic-Ms": "123.0",
        }
        path = f"/api/jarvis/context-requests/{pending_request['request_id']}/content"
        accepted = await device.put(path, content=jpeg, headers=headers)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["state"] == "ready"
        assert accepted.json()["context_reference"].startswith("context://")
        duplicate = await device.put(path, content=jpeg, headers=headers)
        assert duplicate.status_code == 200 and duplicate.json() == accepted.json()
        assert (await device.get("/api/jarvis/context-requests/pending")).json()["requests"] == []
        assert (await device.post(
            f"/api/jarvis/context-requests/{pending_request['request_id']}/unavailable",
            json={"reason": "upload_failed"},
        )).json()["state"] == "ready"
        detail = await task_service.get_task(created.task.id)
        assert detail is not None and detail.task.status in {"queued", "waiting_for_worker"}
        assert detail.runs[0].context_reference is None  # Reserved for the portable Codex handoff.
        assert any(artifact.artifact_type == "task_context_images" for artifact in detail.artifacts)
        worker = await worker_coordinator.activate(process_identity="capture-image-worker")
        assert worker is not None
        claim = await worker_coordinator.claim_next(worker.worker_id)
        assert claim.task is not None and claim.task.task_id == created.task.id
        tool_args = SimpleNamespace(
            task_id=created.task.id, run_id=claim.task.execution_run_id,
            attempt_id=claim.task.attempt_id, claim_token=claim.task.claim_token,
            workflow_schema="document.v1",
        )
        image_result = await _image_result({}, tool_args)
        assert base64.b64decode(next(item["data"] for item in image_result["content"] if item["type"] == "image")) == jpeg
        published = await _call("result_publish", {
            "title": "Visible marker", "summary": "One marker on the screen",
            "body": "The screen shows a visible marker.",
            "source_event_sequences": [], "operation_key": "screen-document-1",
        }, tool_args)
        assert published["status"] == "completed"
        assert (await _call("result_publish", {
            "title": "Visible marker", "summary": "One marker on the screen",
            "body": "The screen shows a visible marker.",
            "source_event_sequences": [], "operation_key": "screen-document-1",
        }, tool_args)) == published
        async with session_scope() as session:
            page = (await session.execute(select(ResultPage).where(ResultPage.task_id == created.task.id))).scalar_one()
            assert page.status == "ready" and page.revision_number == 1
            assert len(list((await session.execute(select(ResultPageRevision).where(
                ResultPageRevision.result_page_id == page.id,
            ))).scalars().all())) == 1
            result_url = f"/results/{page.public_id}"
        result_snapshot = await device.get(f"/api{result_url}")
        assert result_snapshot.status_code == 200
        assert result_snapshot.json()["content"]["body"] == "The screen shows a visible marker."
        await task_presentation_service.sync_candidates()
        async with session_scope() as session:
            notifications = list((await session.execute(select(NotificationOutbox).where(
                NotificationOutbox.task_id == created.task.id,
                NotificationOutbox.event_kind == "task.completed",
            ))).scalars().all())
            assert len(notifications) == 1 and notifications[0].deep_link == result_url

        failed = await task_service.dispatch(
            goal="Describe a second screen", origin_surface="jarvis_voice", idempotency_key="capture-workflow-2",
            execution_kind="workflow", execution_config={"definition": "screen_document@1"},
            capture_binding=CaptureCollection((("invocation", binding),), missing_roles=("latest",)),
        )
        second_pending = (await device.get("/api/jarvis/context-requests/pending")).json()["requests"]
        assert len(second_pending) == 1
        reported = await device.post(
            f"/api/jarvis/context-requests/{second_pending[0]['request_id']}/unavailable",
            json={"reason": "upload_failed"},
        )
        assert reported.status_code == 200 and reported.json()["state"] == "unavailable"
        failed_detail = await task_service.get_task(failed.task.id)
        assert failed_detail is not None and failed_detail.task.status == "failed"
        async with session_scope() as session:
            failed_page = (await session.execute(select(ResultPage).where(
                ResultPage.task_id == failed.task.id,
            ))).scalar_one()
            assert failed_page.status == "failed" and failed_page.current_revision_id is None
        late = await device.put(
            f"/api/jarvis/context-requests/{second_pending[0]['request_id']}/content",
            content=jpeg, headers={**headers, "X-Upload-Token": second_pending[0]["upload_token"]},
        )
        assert late.status_code == 409
        assert "start_workflow" in {tool["name"] for tool in get_jarvis_tool_definitions()}
        args = {"goal": "Describe the visible marker", "definition": "screen_document@1", "intent_id": "screen"}
        admitted = await restricted_brain_gateway.execute(
            tool_name="start_workflow", arguments=args, call_id="screen-call-1",
            conversation_id=live_session["conversation_id"], origin_turn_id=None,
            session_id=live_session["id"], generation=live_session["generation"], turn_scope="fixture-screen-turn",
        )
        assert admitted.output["accepted"] and admitted.output["status"] == "waiting_for_input"
        assert admitted.output["timing_ms"]["ready_call_to_admission"] >= 0
        from app.db.tables import ResultMetric
        from app.services.result_telemetry import observation_queue
        assert await observation_queue.drain()
        async with session_scope() as observation_session:
            measurement = (await observation_session.execute(select(ResultMetric).where(ResultMetric.kind == "screen_admission"))).scalar_one()
            assert measurement.values_json["includes_capture_preflight"] is True
            assert measurement.duration_ms >= measurement.values_json["preflight_ms"]
        replayed = await restricted_brain_gateway.execute(
            tool_name="start_workflow", arguments=args, call_id="screen-call-1",
            conversation_id=live_session["conversation_id"], origin_turn_id=None,
            session_id=live_session["id"], generation=live_session["generation"], turn_scope="fixture-screen-turn",
        )
        assert replayed.output["internal_task_id"] == admitted.output["internal_task_id"]
        assert replayed.output["replayed"] is True
        # A later additional result must not change the original call's URL
        # or make its task-scoped capture/result lookups ambiguous.
        await task_service.continue_task(admitted.output["internal_task_id"], content="An additional Document",
            origin="test", idempotency_key="screen-additional", execution_kind="agent", result_request={"mode": "new"})
        replayed_again = await restricted_brain_gateway.execute(
            tool_name="start_workflow", arguments=args, call_id="screen-call-1",
            conversation_id=live_session["conversation_id"], origin_turn_id=None,
            session_id=live_session["id"], generation=live_session["generation"], turn_scope="fixture-screen-turn",
        )
        assert replayed_again.output["result_url"] == admitted.output["result_url"]
        pending_followup = await task_service.continue_task(admitted.output["internal_task_id"],
            content="Use this capture in a later screen workflow", origin="test", idempotency_key="pending-screen-failure",
            execution_kind="workflow", execution_config={"definition": "screen_document@1"}, capture_binding=CaptureCollection((("invocation", binding),), missing_roles=("latest",)),
            result_request={"mode": "new"})
        async with session_scope() as session:
            pending_request = (await session.execute(select(TaskContextRequest).where(
                TaskContextRequest.execution_run_id == pending_followup.run.id))).scalar_one()
            pending_id = pending_request.id
        failed_pending = await device.post(f"/api/jarvis/context-requests/{pending_id}/unavailable",
            json={"reason": "upload_failed"})
        assert failed_pending.status_code == 200, failed_pending.text
        pending_detail = await task_service.get_task(admitted.output["internal_task_id"])
        pending_run = next(run for run in pending_detail.runs if run.id == pending_followup.run.id)
        assert pending_run.status == "failed"
        assert pending_detail.task.current_run_id != pending_run.id
        assert pending_detail.task.status == "waiting_for_input"
