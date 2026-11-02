from __future__ import annotations

from contextlib import aclosing
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import select

from app.main import app
from app.db.session import session_scope
from app.db.tables import JarvisLiveEvent, TaskResult
from app.services.jarvis.brain import restricted_brain_gateway
from app.services.jarvis.tool_catalog import get_jarvis_tool_definitions
from app.services.tasks.service import task_service
from app.services.tasks.worker_coordinator import worker_coordinator
from test_task_result_contract import claim, finish


async def _paired_live_phone(transport, lock_state="unlocked"):
    async with AsyncClient(transport=transport, base_url="http://testserver") as admin:
        assert (await admin.post("/api/auth/login", json={"password": ""})).status_code == 200
        code = (await admin.post("/api/jarvis/devices/pairing-code")).json()["code"]
    device = AsyncClient(transport=transport, base_url="http://testserver")
    paired = (await device.post("/api/jarvis/devices/pair", json={"code": code, "label": "Pixel"})).json()
    device.headers["Authorization"] = f"Bearer {paired['credential']}"
    live = await device.post("/api/jarvis/live/calls", json={
        "controller_id": paired["device_id"], "provider_adapter": "fake",
        "activation_context": {"source": "system", "activation_id": str(uuid4())},
    })
    assert live.status_code == 200, live.text
    assert (await device.patch(f"/api/jarvis/devices/{paired['device_id']}",
                               json={"lock_state": lock_state})).status_code == 200
    return device, paired["device_id"], live.json()["session"]


async def _show(live_session, task_id, call_id="call-show-1"):
    return await restricted_brain_gateway.execute(
        tool_name="show_on_phone", arguments={"task_id": task_id}, call_id=call_id,
        conversation_id="conversation-show", origin_turn_id=None,
        session_id=live_session["id"], generation=live_session["generation"])


async def _open_requests(device):
    replay = (await device.get("/api/jarvis/live/replay")).json()["events"]
    return [e for e in replay if e["event_type"] == "display.open_requested"]


@pytest.mark.asyncio
async def test_show_on_phone_opens_the_result_in_its_app() -> None:
    device, _, live_session = await _paired_live_phone(ASGITransport(app=app))
    async with aclosing(device):
        created = await task_service.dispatch(goal="Write a short packing list", origin_surface="test",
                                              idempotency_key="show-result-1", result_request={"mode": "new"})
        worker = await worker_coordinator.activate(process_identity="show-result")
        await finish(worker, created.task.id, await claim(worker, created.task.id), "Socks, charger.")
        async with session_scope() as session:
            published = (await session.execute(select(TaskResult).where(
                TaskResult.task_id == created.task.id))).scalar_one()
        result = await _show(live_session, created.task.id)
        assert result.output["shown"] == "result" and result.output["delivered_as"] == "screen"
        [event] = await _open_requests(device)
        assert event["payload"]["path"] == f"/apps/documents?collection=document&key={published.record_key}"
        assert event["payload"]["session_id"] == live_session["id"] and event["payload"]["expires_at"]
        # A replayed call reuses the same request instead of switching twice.
        await _show(live_session, created.task.id)
        assert len(await _open_requests(device)) == 1


@pytest.mark.asyncio
async def test_show_on_phone_opens_the_task_without_a_result_and_reports_a_locked_phone() -> None:
    device, device_id, live_session = await _paired_live_phone(ASGITransport(app=app), lock_state="locked")
    async with aclosing(device):
        created = await task_service.dispatch(goal="Check the backups", origin_surface="test",
                                              idempotency_key="show-task-1")
        result = await _show(live_session, created.task.id)
        assert result.output["shown"] == "task" and result.output["delivered_as"] == "notification"
        [event] = await _open_requests(device)
        assert event["payload"]["path"] == f"/tasks/{created.task.id}"
    async with session_scope() as session:
        row = (await session.execute(select(JarvisLiveEvent).where(
            JarvisLiveEvent.event_type == "display.open_requested"))).scalar_one()
    assert row.target_device_id == device_id


@pytest.mark.asyncio
async def test_show_on_phone_requires_the_live_phone_session() -> None:
    created = await task_service.dispatch(goal="Check the backups", origin_surface="test",
                                          idempotency_key="show-task-2")
    with pytest.raises(ValueError, match="active Live session"):
        await restricted_brain_gateway.execute(
            tool_name="show_on_phone", arguments={"task_id": created.task.id}, call_id="call-show-2",
            conversation_id="conversation-show", origin_turn_id=None)


def test_voice_catalog_offers_showing_results_on_the_phone() -> None:
    tools = {tool["name"]: tool for tool in get_jarvis_tool_definitions("openai_live")}
    assert tools["show_on_phone"]["parameters"]["required"] == ["task_reference"]
    from app.services.jarvis.openai_live_adapter import OpenAILiveAdapter
    assert "show_on_phone" in OpenAILiveAdapter._router_instructions({})
