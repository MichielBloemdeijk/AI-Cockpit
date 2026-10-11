"""Real device-authenticated collection admission, delivery and worker restoration."""
import asyncio
import base64
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from PIL import Image
import pytest

from app.config import settings
from app.main import app
from app.services.task_capture import task_capture_service
from app.services.task_service import task_service
from app.services.task_workflows import load_workflow
from app.services.worker_coordinator import worker_coordinator
from app.jarvis_tasks.task_mcp import _image_result


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["distinct", "identical", "partial_failure", "missing_latest", "explicit_current_missing", "missing_invocation", "overlapping", "mismatched_current", "concurrent"])
async def test_frozen_screen_collection(monkeypatch, case):
    definition = load_workflow("screen_document@1")
    monkeypatch.setattr(settings, "task_executor", "codex")
    monkeypatch.setattr(settings, "task_workflow_allowlist", f"screen_document@1:{definition.digest}")
    activation = str(uuid4())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": ""})
        code = (await client.post("/api/jarvis/devices/pairing-code")).json()["code"]
        paired = (await client.post("/api/jarvis/devices/pair", json={"code": code, "label": "Collection test"})).json()
        client.headers["Authorization"] = f"Bearer {paired['credential']}"
        await client.patch(f"/api/jarvis/devices/{paired['device_id']}", json={"lock_state": "unlocked"})
        live = (await client.post("/api/jarvis/live/calls", json={"controller_id": paired["device_id"],
            "provider_adapter": "fake", "activation_context": {"source": "system", "activation_id": activation}})).json()["session"]
        options = dict(device_id=paired["device_id"], session_id=live["id"], generation=live["generation"],
            activation_id=activation, wait_seconds=0)
        now = datetime.now(timezone.utc)
        expiry = now + timedelta(seconds=80)
        async def report(identity, mode, *, available=True, expires=expiry, revision=2):
            result = await client.put(f"/api/jarvis/captures/{identity}/status", json={
                "activation_id": activation, "capture_mode": mode, "revision": revision,
                "status": "captured" if available else "unavailable", "expires_at": expires.isoformat(),
                "captured_at": now.isoformat() if available else None,
                "reason": None if available else "callback_null", "session_id": live["id"], "generation": live["generation"]})
            assert result.status_code == 200, result.text
        await report(str(uuid4()), "activation", available=case != "missing_invocation")
        fresh = await task_capture_service.request_fresh(session_id=live["id"], generation=live["generation"], request_identity="turn-one")
        duplicate = await task_capture_service.request_fresh(session_id=live["id"], generation=live["generation"], request_identity="turn-one")
        assert duplicate.capture_id == fresh.capture_id
        if case in {"overlapping", "mismatched_current"}:
            newer = await task_capture_service.request_fresh(session_id=live["id"], generation=live["generation"], request_identity="turn-two")
            assert newer.capture_id != fresh.capture_id
            await report(newer.capture_id, "fresh", expires=newer.expires_at.replace(tzinfo=timezone.utc))
        await report(fresh.capture_id, "fresh", available=case not in {"missing_latest", "explicit_current_missing"},
            expires=fresh.expires_at.replace(tzinfo=timezone.utc))
        if case == "mismatched_current":
            with pytest.raises(ValueError, match="delegated turn"):
                await task_capture_service.preflight_collection(**options, request_identity="turn-one",
                    capture_mode="fresh", capture_id=newer.capture_id)
            assert await task_service.list_tasks() == []
            return
        if case == "explicit_current_missing":
            with pytest.raises(ValueError, match="unavailable"):
                await task_capture_service.preflight_collection(**options, request_identity="turn-one", capture_mode="fresh")
            assert await task_service.list_tasks() == []
            return
        collection = await task_capture_service.preflight_collection(**options, request_identity="turn-one", capture_mode="fresh" if case == "missing_invocation" else "activation")
        if case == "missing_invocation":
            assert collection.missing_roles == ("invocation",)
        elif case == "missing_latest":
            assert collection.missing_roles == ("latest",)
        else:
            assert {role for role, _ in collection.captures} == {"invocation", "latest"}
            assert dict(collection.captures)["latest"].capture_id == fresh.capture_id
        admitted = await task_service.dispatch(goal="Describe these screens", origin_surface="test", idempotency_key="collection",
            execution_kind="workflow", execution_config={"definition": "screen_document@1"}, capture_binding=collection)
        repeated = await task_service.dispatch(goal="Describe these screens", origin_surface="test", idempotency_key="collection",
            execution_kind="workflow", execution_config={"definition": "screen_document@1"}, capture_binding=collection)
        assert repeated.task.id == admitted.task.id
        requests = (await client.get("/api/jarvis/context-requests/pending")).json()["requests"]
        async def upload(request, index):
            data = BytesIO()
            Image.new("RGB", (32, 16), (70 if case == "identical" else 70 + index * 40, 30, 40)).save(data, format="JPEG")
            jpeg = data.getvalue()
            response = await client.put(f"/api/jarvis/context-requests/{request['request_id']}/content", content=jpeg, headers={
                "Content-Type": "image/jpeg", "X-Upload-Token": request["upload_token"], "X-Content-SHA256": sha256(jpeg).hexdigest(),
                "X-Capture-Id": request["capture_id"], "X-Activation-Id": activation, "X-Generation": str(live["generation"]),
                "X-Captured-At": now.isoformat(), "X-Capture-Monotonic-Ms": "123"})
            assert response.status_code == 200, response.text
        if case == "concurrent":
            await asyncio.gather(*(upload(request, index) for index, request in enumerate(requests)))
        else:
            await upload(requests[0], 0)
        if len(requests) > 1 and case != "concurrent":
            assert (await task_service.get_task(admitted.task.id)).task.status == "waiting_for_input"
            if case == "partial_failure":
                failed = await client.post(f"/api/jarvis/context-requests/{requests[1]['request_id']}/unavailable", json={"reason": "upload_failed"})
                assert failed.status_code == 200
                assert (await task_service.get_task(admitted.task.id)).task.status == "failed"
                return
            await upload(requests[1], 1)
        worker = await worker_coordinator.activate(process_identity="collection-worker")
        claim = await worker_coordinator.claim_next(worker.worker_id)
        args = SimpleNamespace(task_id=admitted.task.id, run_id=claim.task.execution_run_id, attempt_id=claim.task.attempt_id,
            claim_token=claim.task.claim_token, workflow_schema="document.v1")
        result = await _image_result({}, args)
        image_count = sum(item["type"] == "image" for item in result["content"])
        assert image_count == (2 if case in {"distinct", "overlapping", "concurrent"} else 1)
        manifest = result["structuredContent"]
        assert manifest["missing_roles"] == list(collection.missing_roles)
        assert {role["role"] for image in manifest["images"] for role in image["roles"]} == {role for role, _ in collection.captures}
