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
    stale = await task_presentation_service.acknowledge_task_update(session_id=first.id,generation=first.generation,
        presentation_id=item["presentation_id"],exposure_token=item["exposure_token"])
    assert stale["status"] == "invalid_exposure"
    async with session_scope() as db:
        assert (await db.get(TaskPresentation,item["presentation_id"])).state == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("acknowledged",[False,True])
async def test_delivery_state_and_task_truth_survive_session_replacement(acknowledged):
    from app.services.jarvis_brain import restricted_brain_gateway
    accepted = await jarvis_coordinator.accept_text_turn("Begin",idempotency_key="delivery-first")
    first = await jarvis_coordinator.get_active_session()
    task = await task_service.dispatch(goal="Persist my result",origin_surface="test",idempotency_key="delivery-task")
    await task_service.complete(task.task.id,summary="The saved result is ready",idempotency_key="delivery-done")
    await task_presentation_service.sync_candidates()
    exposed = await task_presentation_service.get_attention_checklist(session_id=first.id,generation=first.generation)
    item = exposed.items[0]
    if acknowledged:
        await task_presentation_service.acknowledge_task_update(session_id=first.id,generation=first.generation,
            presentation_id=item["presentation_id"],exposure_token=item["exposure_token"])
    await jarvis_coordinator.close_session(first.id,generation=first.generation)
    second = await jarvis_coordinator.open_controller(conversation_id=accepted.conversation.id,controller_id="replacement")
    checklist = await task_presentation_service.get_attention_checklist(session_id=second.id,generation=second.generation)
    assert len(checklist.items) == (0 if acknowledged else 1)
    if not acknowledged:
        assert checklist.items[0]["exposure_token"] != item["exposure_token"]
        stale = await task_presentation_service.acknowledge_task_update(session_id=second.id,generation=second.generation,
            presentation_id=item["presentation_id"],exposure_token=item["exposure_token"])
        assert stale["status"] == "invalid_exposure"
    result = await restricted_brain_gateway.execute(tool_name="get_task",arguments={"task_id":task.task.id},
        call_id="status-in-new-session",conversation_id=accepted.conversation.id,origin_turn_id=None,
        session_id=second.id,generation=second.generation)
    assert result.output["status"] == "completed" and result.output["summary"] == "The saved result is ready"
    async with session_scope() as db:
        row = await db.get(TaskPresentation,item["presentation_id"])
        assert row.state == ("handled" if acknowledged else "pending")
        assert row.handled_by == ("model_report" if acknowledged else None)


@pytest.mark.asyncio
async def test_bounded_question_preserves_its_text_and_acknowledgement_ids(monkeypatch):
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
        ) <= 1600
        assert fact["notification"]["revision"] == 0
        assert fact["commentary"] is False
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
@pytest.mark.parametrize("valid_exposure", [True, False])
async def test_live_raw_acknowledgment_without_user_origin_reaches_durable_state_and_continuation(
    monkeypatch, valid_exposure,
) -> None:
    """Exercise the provider/coordinator seam, not just a constructed shell callback."""
    from app.config import settings
    from app.db.tables import LiveSession
    from app.services.openai_live_adapter import OpenAILiveAdapter
    from test_openai_live_adapter import FakeLiveSocket, wait_for_sideband

    await jarvis_coordinator.accept_text_turn("Begin", idempotency_key="raw-ack-origin")
    live = await jarvis_coordinator.get_active_session()
    assert live is not None
    task = await task_service.dispatch(goal="Provider seam result", origin_surface="test",
        idempotency_key="raw-ack-task")
    await task_service.complete(task.task.id, summary="The verified result is ready.",
        idempotency_key="raw-ack-complete")
    await task_presentation_service.sync_candidates()
    checklist = await task_presentation_service.get_attention_checklist(
        session_id=live.id, generation=live.generation)
    item = checklist.items[0]

    socket = FakeLiveSocket()
    observed = []
    async def create(**kwargs):
        return {"session": {"id": "raw-ack-provider"}, "transport": {"sdp": "answer"}}
    async def connect(*args, **kwargs):
        return socket
    async def on_event(session_id, generation, event):
        observed.append(event)
        await jarvis_coordinator.handle_provider_event(session_id, generation, event)
    adapter = OpenAILiveAdapter(session_creator=create, websocket_connector=connect)
    monkeypatch.setattr(settings, "openai_api_key", "fixture-key")
    monkeypatch.setattr("app.services.jarvis_coordinator.get_voice_session_adapter", lambda name: adapter)
    async with session_scope() as db:
        (await db.get(LiveSession, live.id)).provider_adapter = "openai_live"
    try:
        await adapter.connect(session_id=live.id, generation=live.generation, context={},
            on_event=on_event, sdp_offer="offer")
        await wait_for_sideband(adapter, (live.id, live.generation))
        # A post-speech bookkeeping delegation has no new user transcript to match.
        await adapter.handle_event(live.id, live.generation, {
            "type": "session.delegation.created", "event_id": "raw-ack-delegation",
            "offset_ms": 5000, "delegation": {"id": "ack-delegation",
                "type": "delegation", "target": "responses", "response_id": "ack-response"},
        })
        await adapter.handle_event(live.id, live.generation, {
            "type": "response.event", "delegation_id": "ack-delegation",
            "event": {"type": "response.created", "response": {"id": "ack-response"}},
        })
        callback = {"type": "response.event", "event_id": "raw-ack-function",
            "delegation_id": "ack-delegation", "event": {
                "type": "response.output_item.done", "response_id": "ack-response",
                "item": {"type": "function_call", "call_id": "raw-ack-call",
                    "name": "acknowledge_task_update", "arguments": json.dumps({
                        "presentation_id": item["presentation_id"],
                        "exposure_token": item["exposure_token"] if valid_exposure else "stale-token",
                    })},
            }}
        await adapter.handle_event(live.id, live.generation, callback)
        await adapter.handle_event(live.id, live.generation, callback)
        shell = [event for event in observed if event.kind == "shell_call"]
        assert len(shell) == 1 and shell[0].payload["origin_item_id"] is None
        await adapter.handle_event(live.id, live.generation, {
            "type": "response.event", "delegation_id": "ack-delegation",
            "event": {"type": "response.completed", "response": {"id": "ack-response"}},
        })
        results = [event for event in socket.sent if event["type"] == "response.item.create"]
        assert len(results) == 1 and results[0]["item"]["call_id"] == "raw-ack-call"
        expected = "handled" if valid_exposure else "invalid_exposure"
        assert json.loads(results[0]["item"]["output"])["result"]["status"] == expected
        assert sum(event["type"] == "response.create" for event in socket.sent) == 1
        async with session_scope() as db:
            row = await db.get(TaskPresentation, item["presentation_id"])
            assert row.state == ("handled" if valid_exposure else "pending")
            assert row.handled_by == ("model_report" if valid_exposure else None)
            durable_results = list((await db.execute(select(LiveSessionEvent).where(
                LiveSessionEvent.session_id == live.id,
                LiveSessionEvent.event_type == "brain.tool.result",
            ))).scalars().all())
            assert len(durable_results) == 1
            assert durable_results[0].payload_json["output"]["status"] == expected
            assert (await task_service.get_task(task.task.id)).task.status == "completed"
    finally:
        await adapter.close(session_id=live.id, generation=live.generation,
            reason="offline_ack_audit", emit_event=False)


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
