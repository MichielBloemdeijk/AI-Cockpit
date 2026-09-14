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
    LiveSessionEvent,
)
from app.services.jarvis_coordinator import jarvis_coordinator
from app.services.notifications import acknowledge_delivery
from app.services.task_presentations import task_presentation_service
from app.services.task_service import task_service
from app.services.voice_session_adapter import VoiceProviderEvent


@pytest.mark.asyncio
async def test_model_report_handles_one_item_and_duplicate_preserves_evidence() -> None:
    accepted = await jarvis_coordinator.accept_text_turn(
        "Keep the task update pending until I hear it",
        idempotency_key="attention-self-report-origin",
    )
    session = await jarvis_coordinator.get_active_session()
    assert session is not None
    snapshot = await task_service.dispatch(
        goal="Produce a model-reported result",
        origin_surface="test",
        origin_conversation_id=accepted.conversation.id,
        idempotency_key="attention-self-report-task",
    )
    await task_service.complete(
        snapshot.task.id,
        summary="The result is ready.",
        idempotency_key="attention-self-report-complete",
    )
    await task_presentation_service.sync_candidates()

    checklist = await task_presentation_service.get_attention_checklist(
        session_id=session.id,
        generation=session.generation,
    )
    assert len(checklist.items) == 1
    item = checklist.items[0]
    assert item["exposure_token"]
    assert item["presentation_id"]

    handled = await task_presentation_service.acknowledge_task_update(
        session_id=session.id,
        generation=session.generation,
        presentation_id=item["presentation_id"],
        exposure_token=item["exposure_token"],
        provider_event_id="provider-tool-event-1",
        provider_call_id="call-1",
    )
    assert handled["status"] == "handled"

    duplicate = await task_presentation_service.acknowledge_task_update(
        session_id=session.id,
        generation=session.generation,
        presentation_id=item["presentation_id"],
        exposure_token=item["exposure_token"],
        provider_event_id="provider-tool-event-2",
        provider_call_id="call-2",
    )
    assert duplicate["status"] == "already_handled"

    async with session_scope() as db:
        presentation = await db.get(TaskPresentation, item["presentation_id"])
        assert presentation is not None
        assert presentation.state == "handled"
        assert presentation.handled_by == "model_report"
        assert presentation.handled_event_id is not None
        audit = (await db.execute(
            select(JarvisLiveEvent).where(JarvisLiveEvent.id == presentation.handled_event_id)
        )).scalar_one_or_none()
        assert audit is not None
        assert audit.event_type == "attention.handled"
        assert audit.payload_json["provider_event_id"] == "provider-tool-event-1"


@pytest.mark.asyncio
async def test_invalid_exposure_does_not_mutate_attention() -> None:
    accepted = await jarvis_coordinator.accept_text_turn(
        "Keep this update pending",
        idempotency_key="attention-invalid-origin",
    )
    session = await jarvis_coordinator.get_active_session()
    assert session is not None
    snapshot = await task_service.dispatch(
        goal="Pending invalid report",
        origin_surface="test",
        origin_conversation_id=accepted.conversation.id,
        idempotency_key="attention-invalid-task",
    )
    await task_service.complete(
        snapshot.task.id,
        summary="Still pending.",
        idempotency_key="attention-invalid-complete",
    )
    await task_presentation_service.sync_candidates()
    checklist = await task_presentation_service.get_attention_checklist(
        session_id=session.id,
        generation=session.generation,
    )
    item = checklist.items[0]
    result = await task_presentation_service.acknowledge_task_update(
        session_id=session.id,
        generation=session.generation,
        presentation_id=item["presentation_id"],
        exposure_token="stale-token",
    )
    assert result["status"] == "invalid_exposure"
    async with session_scope() as db:
        presentation = await db.get(TaskPresentation, item["presentation_id"])
        assert presentation is not None and presentation.state == "pending"


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
async def test_attention_transition_rolls_back_with_caller_transaction() -> None:
    accepted = await jarvis_coordinator.accept_text_turn(
        "Prepare rollback evidence",
        idempotency_key="attention-rollback-origin",
    )
    realtime = await jarvis_coordinator.get_active_session()
    assert realtime is not None
    snapshot = await task_service.dispatch(
        goal="Rollback model attention",
        origin_surface="test",
        origin_conversation_id=accepted.conversation.id,
        idempotency_key="attention-rollback-task",
    )
    await task_service.complete(
        snapshot.task.id,
        summary="Rollback this transition.",
        idempotency_key="attention-rollback-complete",
    )
    await task_presentation_service.sync_candidates()
    checklist = await task_presentation_service.get_attention_checklist(
        session_id=realtime.id,
        generation=realtime.generation,
    )
    item = checklist.items[0]

    with pytest.raises(RuntimeError, match="injected"):
        async with session_scope() as db:
            result = await task_presentation_service.acknowledge_task_update_in_session(
                db,
                session_id=realtime.id,
                generation=realtime.generation,
                presentation_id=item["presentation_id"],
                exposure_token=item["exposure_token"],
                provider_event_id="rollback-provider-event",
                provider_call_id="rollback-call",
            )
            assert result["status"] == "handled"
            raise RuntimeError("injected")

    async with session_scope() as db:
        presentation = await db.get(TaskPresentation, item["presentation_id"])
        assert presentation is not None and presentation.state == "pending"
        assert presentation.handled_event_id is None
        audit_count = int((await db.execute(
            select(func.count()).select_from(JarvisLiveEvent).where(
                JarvisLiveEvent.source_domain == "attention",
                JarvisLiveEvent.source_id == item["presentation_id"],
            )
        )).scalar_one())
        assert audit_count == 0


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
        ) <= 400
        handled = await task_presentation_service.acknowledge_task_update(
            session_id=realtime.id,
            generation=realtime.generation,
            presentation_id=item["presentation_id"],
            exposure_token=item["exposure_token"],
        )
        assert handled["status"] == "handled"

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
async def test_provider_callback_attention_and_tool_result_commit_and_replay_once() -> None:
    accepted = await jarvis_coordinator.accept_text_turn(
        "Persist the callback atomically",
        idempotency_key="attention-provider-atomic-origin",
    )
    realtime = await jarvis_coordinator.get_active_session()
    assert realtime is not None
    snapshot = await task_service.dispatch(
        goal="Atomic provider callback",
        origin_surface="test",
        origin_conversation_id=accepted.conversation.id,
        idempotency_key="attention-provider-atomic-task",
    )
    await task_service.complete(
        snapshot.task.id,
        summary="Atomic callback result.",
        idempotency_key="attention-provider-atomic-complete",
    )
    await task_presentation_service.sync_candidates()
    checklist = await task_presentation_service.get_attention_checklist(
        session_id=realtime.id,
        generation=realtime.generation,
    )
    item = checklist.items[0]
    callback = VoiceProviderEvent(
        event_id="provider-atomic-callback",
        kind="shell_call",
        payload={
            "call_id": "provider-atomic-call",
            "name": "acknowledge_task_update",
            "arguments": {
                "presentation_id": item["presentation_id"],
                "exposure_token": item["exposure_token"],
            },
        },
    )
    await jarvis_coordinator.handle_provider_event(realtime.id, realtime.generation, callback)
    await jarvis_coordinator.handle_provider_event(realtime.id, realtime.generation, callback)

    async with session_scope() as db:
        presentation = await db.get(TaskPresentation, item["presentation_id"])
        assert presentation is not None and presentation.state == "handled"
        results = list((await db.execute(
            select(LiveSessionEvent).where(
                LiveSessionEvent.session_id == realtime.id,
                LiveSessionEvent.event_type == "brain.tool.result",
            )
        )).scalars().all())
        assert len(results) == 1
        assert results[0].payload_json["output"]["status"] == "handled"


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
