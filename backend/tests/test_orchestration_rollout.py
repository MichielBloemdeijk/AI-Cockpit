from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.db.tables import LiveSession, NotificationOutbox, TaskPresentation
from app.services.jarvis_coordinator import jarvis_coordinator
from app.services.task_notifications import _send_outbox_by_id
from app.services.task_orchestration import DecisionTools
from app.services.task_presentations import task_presentation_service
from app.services.task_service import task_service
from test_task_orchestration import FixtureRuntime, question, ready


@pytest.mark.asyncio
async def test_held_legacy_transport_lease_does_not_block_factual_append(monkeypatch):
    monkeypatch.setattr(settings, "task_orchestration_enabled", False)
    await jarvis_coordinator.accept_text_turn("Keep me updated", idempotency_key="lease-voice")
    live = await jarvis_coordinator.get_active_session()
    await question("Ask the user for the environment")
    await task_presentation_service.sync_candidates()
    original = (await task_presentation_service.list_pending())[0]
    from app.services.task_orchestration import now
    async with session_scope() as session:
        candidate = await session.get(TaskPresentation, original.id)
        candidate.lease_token = "pre-rollout-lease"
        candidate.reserved_session_id = live.id
        candidate.reserved_generation = live.generation
        candidate.reserved_at = now()
        (await session.get(LiveSession, live.id)).lifecycle_state = "ready"
    monkeypatch.setattr(settings, "task_orchestration_enabled", True)
    factual = (await task_service.dispatch(goal="Factual completion", origin_surface="test", idempotency_key="fact")).task
    await task_service.complete(factual.id, summary="Done", idempotency_key="complete")
    await task_presentation_service.sync_candidates()
    async with session_scope() as session:
        reserved = await task_presentation_service.reserve_in_session(
            session, session_id=live.id, generation=live.generation, user_owns_floor=False,
        )
        assert reserved is not None
        assert original.id not in reserved.presentation_ids
        assert reserved.facts[0]["task_id"] == factual.id


@pytest.mark.asyncio
async def test_new_unjudged_issue_survives_disablement_without_rewinding_cursor(monkeypatch):
    monkeypatch.setattr(settings, "task_orchestration_enabled", True)
    task, q = await question("Ask me for the environment")
    service = await ready()
    assert not await task_presentation_service.list_pending()
    async with session_scope() as session:
        original = (await session.execute(select(TaskPresentation).where(
            TaskPresentation.source_task_event_id == q.source_task_event_id,
        ))).scalar_one()
        original_id = original.id
    monkeypatch.setattr(settings, "task_orchestration_enabled", False)
    await task_presentation_service.sync_candidates()
    assert [row.id for row in await task_presentation_service.list_pending()] == [original_id]
    monkeypatch.setattr(settings, "task_orchestration_enabled", True)
    assert not await task_presentation_service.list_pending()
    tools = DecisionTools(await service.claim())
    await tools.call("retain", {"reason": "No environment was specified", "operation_key": "retain"})
    assert [row.id for row in await task_presentation_service.list_pending()] == [original_id]
    assert (await task_service.get_task(task.id)).questions[0].state == "asked"


@pytest.mark.asyncio
async def test_legacy_backlog_flag_transitions_retain_identity_and_require_backend_receipt(monkeypatch):
    monkeypatch.setattr(settings, "task_orchestration_enabled", False)
    await jarvis_coordinator.accept_text_turn("Listen for task updates", idempotency_key="rollout-voice")
    live = await jarvis_coordinator.get_active_session()
    task, q = await question("Ask the user which environment to use")
    await task_presentation_service.sync_candidates()
    original = (await task_presentation_service.list_pending())[0]
    checklist = await task_presentation_service.get_attention_checklist(session_id=live.id, generation=live.generation)
    exposure = checklist.items[0]["exposure_token"]
    async with session_scope() as session:
        candidate = await session.get(TaskPresentation, original.id)
        # Worker/model facts cannot masquerade as a backend judgment.
        candidate.required_facts_json = {**candidate.required_facts_json, "orchestration_decision_id": "forged"}
        outbox = (await session.execute(select(NotificationOutbox).where(
            NotificationOutbox.task_id == task.id,
        ))).scalar_one()
        outbox_id = outbox.id
    monkeypatch.setattr(settings, "task_orchestration_enabled", True)
    assert not await task_presentation_service.list_pending()
    assert not (await task_presentation_service.get_attention_checklist(session_id=live.id, generation=live.generation)).items
    rejected = await task_presentation_service.acknowledge_task_update(
        session_id=live.id, generation=live.generation, presentation_id=original.id, exposure_token=exposure,
    )
    assert rejected["status"] == "invalid_exposure"
    await _send_outbox_by_id(outbox_id)
    async with session_scope() as session:
        assert (await session.get(NotificationOutbox, outbox_id)).status == "queued"
        assert (await session.get(TaskPresentation, original.id)).state == "pending"

    # Rollback exposes the original candidate; no new state/inbox or lost item.
    monkeypatch.setattr(settings, "task_orchestration_enabled", False)
    assert [row.id for row in await task_presentation_service.list_pending()] == [original.id]
    monkeypatch.setattr(settings, "task_orchestration_enabled", True)
    service = await ready()
    tools = DecisionTools(await service.claim())
    await tools.call("retain", {"reason": "Only the user can choose", "operation_key": "retain"})
    assert [row.id for row in await task_presentation_service.list_pending()] == [original.id]
    await _send_outbox_by_id(outbox_id)
    async with session_scope() as session:
        assert (await session.get(NotificationOutbox, outbox_id)).status == "delivered"
    # Existing receipts continue to authorize visibility across flag restarts.
    for enabled in (False, True):
        monkeypatch.setattr(settings, "task_orchestration_enabled", enabled)
        assert [row.id for row in await task_presentation_service.list_pending()] == [original.id]
    assert (await task_service.get_task(task.id)).questions[0].state == "asked"
    assert (await task_service.get_task(task.id)).questions[0].id == q.id


@pytest.mark.asyncio
async def test_active_voice_reconnect_with_blocked_decision_and_full_pool(monkeypatch):
    monkeypatch.setattr(settings, "task_orchestration_enabled", True)
    accepted = await jarvis_coordinator.accept_text_turn("Keep me updated", idempotency_key="combined-voice")
    old_live = await jarvis_coordinator.get_active_session()
    task, q = await question("Ask the user for the environment")

    async def block(claim, tools):
        await runtime.release.wait()
        return await tools.call("retain", {"reason": "User choice required", "operation_key": "retain"})

    runtime = FixtureRuntime(block)
    service = await ready(runtime)
    background = asyncio.create_task(service.run_one())
    try:
        await asyncio.wait_for(runtime.started.wait(), 2)
        from test_m3_preflight import exercise_code_only_services, saturate_pool
        occupied = await saturate_pool()
        await asyncio.wait_for(exercise_code_only_services(occupied), 5)
        await service.intake()
        old_checklist = await task_presentation_service.get_attention_checklist(
            session_id=old_live.id, generation=old_live.generation,
        )
        assert old_checklist.items  # factual completions forward while judgment is blocked
        assert not background.done()
        await jarvis_coordinator.supersede_session(old_live.id, generation=old_live.generation, reason="test_reconnect")
        new_live = await jarvis_coordinator.open_controller(
            conversation_id=accepted.conversation.id, controller_id="reconnected-controller", adapter_name="fake",
        )
        old_item = old_checklist.items[0]
        stale = await task_presentation_service.acknowledge_task_update(
            session_id=old_live.id, generation=old_live.generation,
            presentation_id=old_item["presentation_id"], exposure_token=old_item["exposure_token"],
        )
        assert stale["status"] == "invalid_exposure"
        runtime.release.set()
        await asyncio.wait_for(background, 3)
        checklist = await task_presentation_service.get_attention_checklist(session_id=new_live.id, generation=new_live.generation)
        item = next(item for item in checklist.items if item["task_id"] == task.id)
        arguments = dict(session_id=new_live.id, generation=new_live.generation,
                         presentation_id=item["presentation_id"], exposure_token=item["exposure_token"])
        assert (await task_presentation_service.acknowledge_task_update(**arguments))["status"] == "handled"
        assert (await task_presentation_service.acknowledge_task_update(**arguments))["status"] == "already_handled"
        assert (await task_service.get_task(task.id)).questions[0].state == "asked"
        assert (await task_service.get_task(task.id)).questions[0].id == q.id
        assert len(runtime.calls) == 1
    finally:
        runtime.release.set()
        await asyncio.gather(background, return_exceptions=True)
