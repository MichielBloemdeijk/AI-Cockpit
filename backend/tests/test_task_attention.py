from __future__ import annotations

import asyncio
import json
import pytest
from sqlalchemy import func, select

from app.db.session import session_scope
from app.db.tables import (
    JarvisLiveEvent,
    NotificationOutbox,
    TaskPresentation,
)
from app.services.jarvis_coordinator import jarvis_coordinator
from app.services.notifications import acknowledge_delivery
from app.services.task_presentations import task_presentation_service
from app.services.task_service import task_service


@pytest.mark.asyncio
async def test_completion_between_sessions_retains_pending_facts_and_renews_exposure():
    accepted = await jarvis_coordinator.accept_text_turn("Begin", idempotency_key="between-first")
    first = await jarvis_coordinator.get_active_session()
    task = await task_service.dispatch(goal="Offline completion", origin_surface="test",
        notification_intent="requested", notification_reason="Tell me when finished", idempotency_key="between-task")
    await jarvis_coordinator.close_session(first.id,generation=first.generation)
    await task_service.complete(task.task.id,summary="Finished while Live was closed",idempotency_key="between-done")
    await task_presentation_service.sync_candidates()
    second = await jarvis_coordinator.open_controller(conversation_id=accepted.conversation.id,controller_id="new-controller")
    checklist = await task_presentation_service.get_attention_checklist(session_id=second.id,generation=second.generation)
    item = checklist.items[0]
    assert item["task_id"] == task.task.id and item["state"] == "pending"
    assert item["facts"]["summary"] == "Finished while Live was closed"
    assert item["facts"]["notification"]["intent"] == "requested"
    async with session_scope() as db:
        assert (await db.get(TaskPresentation,item["presentation_id"])).state == "pending"


@pytest.mark.asyncio
async def test_delivery_state_and_task_truth_survive_session_replacement():
    from app.services.jarvis_brain import restricted_brain_gateway
    accepted = await jarvis_coordinator.accept_text_turn("Begin",idempotency_key="delivery-first")
    first = await jarvis_coordinator.get_active_session()
    task = await task_service.dispatch(goal="Persist my result",origin_surface="test",idempotency_key="delivery-task")
    await task_service.complete(task.task.id,summary="The saved result is ready",idempotency_key="delivery-done")
    await task_presentation_service.sync_candidates()
    exposed = await task_presentation_service.get_attention_checklist(session_id=first.id,generation=first.generation)
    item = exposed.items[0]
    await jarvis_coordinator.close_session(first.id,generation=first.generation)
    second = await jarvis_coordinator.open_controller(conversation_id=accepted.conversation.id,controller_id="replacement")
    checklist = await task_presentation_service.get_attention_checklist(session_id=second.id,generation=second.generation)
    assert len(checklist.items) == 1
    assert checklist.items[0]["exposure_token"] != item["exposure_token"]
    result = await restricted_brain_gateway.execute(tool_name="get_task",arguments={"task_id":task.task.id},
        call_id="status-in-new-session",conversation_id=accepted.conversation.id,origin_turn_id=None,
        session_id=second.id,generation=second.generation)
    assert result.output["status"] == "completed" and result.output["summary"] == "The saved result is ready"
    async with session_scope() as db:
        row = await db.get(TaskPresentation,item["presentation_id"])
        assert row.state == "pending" and row.handled_by is None


@pytest.mark.asyncio
async def test_bounded_question_preserves_its_text_and_exposure_ids(monkeypatch):
    from app.config import settings
    # This tests serialization of an exposed question, not the orchestration
    # decision that must authorize exposing a worker question in a live setup.
    monkeypatch.setattr(settings, "task_orchestration_enabled", False)
    accepted = await jarvis_coordinator.accept_text_turn("Begin", idempotency_key="bounded-question-log")
    live = await jarvis_coordinator.get_active_session()
    task = await task_service.dispatch(goal="Recipe ingredients 🥕 " * 60, origin_surface="test",
        idempotency_key="bounded-question-task", result_request={"mode": "answer"})
    question = await task_service.ask_question(task.task.id,
        question="Would you prefer the vegetarian recipe option?", idempotency_key="bounded-question")
    await task_presentation_service.sync_candidates()
    update = await task_presentation_service.build_attention_context(session_id=live.id, generation=live.generation)
    fact = update.items[0]["facts"]
    assert fact["question"] == question.question
    assert fact["task_id"] == task.task.id
    assert fact["presentation_id"] == update.items[0]["presentation_id"]
    assert fact["exposure_token"] == update.items[0]["exposure_token"]
    assert fact["source"]["task_event_id"] == question.source_task_event_id
    assert len(json.dumps(fact, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 400


@pytest.mark.asyncio
async def test_opened_notification_handles_exact_event_and_suppresses_transport() -> None:
    accepted = await jarvis_coordinator.accept_text_turn(
        "Open push updates here",
        idempotency_key="attention-notification-origin",
    )
    snapshot = await task_service.dispatch(
        goal="Handle an opened push update",
        origin_surface="test",
        origin_conversation_id=accepted.conversation.id,
        idempotency_key="attention-notification-task",
    )
    await task_service.complete(
        snapshot.task.id,
        summary="The push update is ready.",
        idempotency_key="attention-notification-complete",
    )
    await task_presentation_service.sync_candidates()
    async with session_scope() as db:
        outbox = (await db.execute(
            select(NotificationOutbox).where(NotificationOutbox.task_id == snapshot.task.id)
        )).scalar_one()
        presentation = (await db.execute(
            select(TaskPresentation).where(TaskPresentation.source_task_event_id == outbox.task_event_id)
        )).scalar_one()
        outbox_id = outbox.id
        presentation_id = presentation.id

    assert await acknowledge_delivery(outbox_id, "opened") is True
    async with session_scope() as db:
        outbox = await db.get(NotificationOutbox, outbox_id)
        presentation = await db.get(TaskPresentation, presentation_id)
    assert outbox is not None and outbox.status == "superseded"
    assert presentation is not None
    assert presentation.state == "handled"
    assert presentation.handled_by == "notification_opened"
    assert presentation.handled_event_id is not None
    async with session_scope() as db:
        audit = (await db.execute(
            select(JarvisLiveEvent).where(JarvisLiveEvent.id == presentation.handled_event_id)
        )).scalar_one_or_none()
    assert audit is not None and audit.event_type == "attention.handled"


@pytest.mark.asyncio
async def test_notification_open_without_voice_audits_and_preserves_sending_history() -> None:
    snapshot = await task_service.dispatch(
        goal="Handle without a voice session",
        origin_surface="test",
        idempotency_key="attention-no-voice-task",
    )
    await task_service.complete(
        snapshot.task.id,
        summary="The push is already in flight.",
        idempotency_key="attention-no-voice-complete",
    )
    await task_presentation_service.sync_candidates()
    async with session_scope() as db:
        outbox = (await db.execute(
            select(NotificationOutbox).where(NotificationOutbox.task_id == snapshot.task.id)
        )).scalar_one()
        outbox.status = "sending"
        delivery_id = outbox.id
        presentation_id = (await db.execute(
            select(TaskPresentation.id).where(
                TaskPresentation.source_task_event_id == outbox.task_event_id
            )
        )).scalar_one()

    assert await acknowledge_delivery(delivery_id, "opened") is True
    async with session_scope() as db:
        outbox = await db.get(NotificationOutbox, delivery_id)
        presentation = await db.get(TaskPresentation, presentation_id)
        assert outbox is not None and outbox.status == "sending"
        assert presentation is not None and presentation.state == "handled"
        audit = (await db.execute(
            select(JarvisLiveEvent).where(JarvisLiveEvent.id == presentation.handled_event_id)
        )).scalar_one()
        assert audit.source_domain == "attention"
        assert audit.payload_json["notification_delivery_id"] == delivery_id


@pytest.mark.asyncio
async def test_context_snapshot_is_full_then_explicitly_empty_and_bounded() -> None:
    accepted = await jarvis_coordinator.accept_text_turn(
        "Build attention context",
        idempotency_key="attention-context-origin",
    )
    realtime = await jarvis_coordinator.get_active_session()
    assert realtime is not None
    presentation_ids: list[str] = []
    for index in range(2):
        snapshot = await task_service.dispatch(
            goal=("Very long goal " * 200) + str(index),
            origin_surface="test",
            origin_conversation_id=accepted.conversation.id,
            idempotency_key=f"attention-context-task-{index}",
        )
        await task_service.complete(
            snapshot.task.id,
            summary="Very long summary " * 300,
            idempotency_key=f"attention-context-complete-{index}",
        )
    await task_presentation_service.sync_candidates()

    update = await task_presentation_service.build_attention_context(
        session_id=realtime.id,
        generation=realtime.generation,
    )
    assert update.total_pending == 2
    assert len(update.items) == 2
    for item in update.items:
        fact = item["facts"]
        presentation_ids.append(item["presentation_id"])
        assert fact["presentation_id"] == item["presentation_id"]
        assert fact["exposure_token"] == item["exposure_token"]
        assert len(
            json.dumps(fact, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ) <= 1600
        assert fact["notification"]["revision"] == 0
        assert fact["commentary"] is False
    async with session_scope() as db:
        for presentation_id in presentation_ids:
            (await db.get(TaskPresentation, presentation_id)).state = "handled"

    empty = await task_presentation_service.build_attention_context(
        session_id=realtime.id,
        generation=realtime.generation,
    )
    assert empty.total_pending == 0
    assert empty.items == ()
    assert empty.next_cursor is None


@pytest.mark.asyncio
async def test_semantic_fingerprint_ignores_transport_lease_changes() -> None:
    accepted = await jarvis_coordinator.accept_text_turn(
        "Fingerprint this update",
        idempotency_key="attention-fingerprint-origin",
    )
    realtime = await jarvis_coordinator.get_active_session()
    assert realtime is not None
    snapshot = await task_service.dispatch(
        goal="Fingerprint lease",
        origin_surface="test",
        origin_conversation_id=accepted.conversation.id,
        idempotency_key="attention-fingerprint-task",
    )
    await task_service.complete(
        snapshot.task.id,
        summary="Fingerprint result.",
        idempotency_key="attention-fingerprint-complete",
    )
    await task_presentation_service.sync_candidates()
    before = await task_presentation_service.list_pending()
    fingerprint = task_presentation_service.semantic_fingerprint(before)
    reservation = await task_presentation_service.reserve(
        session_id=realtime.id,
        generation=realtime.generation,
        user_owns_floor=False,
    )
    assert reservation is not None
    after = await task_presentation_service.list_pending()
    assert task_presentation_service.semantic_fingerprint(after) == fingerprint


@pytest.mark.asyncio
async def test_concurrent_same_session_claims_leave_only_one_append_lease() -> None:
    accepted = await jarvis_coordinator.accept_text_turn(
        "Claim one update",
        idempotency_key="attention-concurrent-origin",
    )
    realtime = await jarvis_coordinator.get_active_session()
    assert realtime is not None
    for index in range(2):
        snapshot = await task_service.dispatch(
            goal=f"Concurrent claim {index}",
            origin_surface="test",
            origin_conversation_id=accepted.conversation.id,
            idempotency_key=f"attention-concurrent-task-{index}",
        )
        await task_service.complete(
            snapshot.task.id,
            summary=f"Concurrent result {index}.",
            idempotency_key=f"attention-concurrent-complete-{index}",
        )
    await task_presentation_service.sync_candidates()

    results = await asyncio.gather(
        task_presentation_service.reserve(
            session_id=realtime.id,
            generation=realtime.generation,
            user_owns_floor=False,
        ),
        task_presentation_service.reserve(
            session_id=realtime.id,
            generation=realtime.generation,
            user_owns_floor=False,
        ),
    )
    assert sum(result is not None for result in results) == 1
    async with session_scope() as db:
        leased = int((await db.execute(
            select(func.count()).select_from(TaskPresentation).where(
                TaskPresentation.reserved_session_id == realtime.id,
                TaskPresentation.lease_token.is_not(None),
            )
        )).scalar_one())
    assert leased == 1
