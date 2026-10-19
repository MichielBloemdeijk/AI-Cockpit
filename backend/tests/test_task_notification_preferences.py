import pytest
from sqlalchemy import select

from app.db.session import session_scope
from app.db.tables import Task, TaskEvent, NotificationOutbox
from app.services.task_service import task_service
from app.services.task_notification_preferences import current, normalize, EVENT
from app.services.task_presentations import task_presentation_service
from app.services.jarvis_coordinator import jarvis_coordinator, FLOOR_EVENT_TYPES
from app.services.live_task_updates import terminal_payload
from app.db.repositories.jarvis import JarvisDomainRepository


async def saved(task_id):
    async with session_scope() as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
        preference = await current(session, task)
        events = (await session.execute(select(TaskEvent).where(TaskEvent.task_id == task_id,
            TaskEvent.event_type == EVENT).order_by(TaskEvent.sequence))).scalars().all()
        return task, preference, events


@pytest.mark.asyncio
async def test_atomic_preferences_replay_revision_and_conflicts():
    args = dict(goal="Verified preference task", origin_surface="test", idempotency_key="preference-admit",
        notification_intent="requested", notification_reason="Tell me when it finishes", presentation_policy="follow_progress")
    task = (await task_service.dispatch(**args)).task
    replay = await task_service.dispatch(**args)
    assert replay.task.id == task.id
    _, preference, events = await saved(task.id)
    assert preference["revision"] == 1 and len(events) == 1
    with pytest.raises(ValueError, match="conflict"):
        await task_service.dispatch(**{**args, "notification_intent":"discretionary"})
    await task_service.set_update_policy(task.id, "silent", notification_intent="suppressed",
        notification_reason="Changed to silence", idempotency_key="preference-correction")
    await task_service.set_update_policy(task.id, "silent", notification_intent="suppressed",
        notification_reason="Changed to silence", idempotency_key="preference-correction")
    await task_service.dispatch(**args)
    task, preference, events = await saved(task.id)
    assert task.presentation_policy == "silent" and preference["revision"] == 2 and len(events) == 2
    assert preference["intent"] == "suppressed"
    with pytest.raises(ValueError, match="conflict"):
        await task_service.set_update_policy(task.id, "critical_only", notification_intent="requested",
            notification_reason="Notify", idempotency_key="preference-correction")


@pytest.mark.asyncio
async def test_invalid_authority_rolls_back_admission_and_policy():
    await jarvis_coordinator.accept_text_turn("Begin", idempotency_key="preference-session")
    live = await jarvis_coordinator.get_active_session()
    stale = {"session_id":live.id,"generation":live.generation + 1,"call_id":"stale"}
    with pytest.raises(ValueError, match="session authority"):
        await task_service.dispatch(goal="Stale admission", origin_surface="test", idempotency_key="stale-admit",
            notification_intent="requested", notification_reason="Notify", notification_authority=stale)
    async with session_scope() as session:
        assert (await session.execute(select(Task).where(Task.idempotency_key == "stale-admit"))).scalar_one_or_none() is None
    task = (await task_service.dispatch(goal="Current task", origin_surface="test", idempotency_key="current-task")).task
    with pytest.raises(ValueError, match="session authority"):
        await task_service.set_update_policy(task.id, "silent", idempotency_key="stale-policy",
            notification_intent="suppressed", notification_reason="Quiet", notification_authority=stale)
    task, preference, events = await saved(task.id)
    assert task.presentation_policy == "critical_only" and preference["revision"] == 0 and not events


@pytest.mark.asyncio
async def test_suppression_retains_terminal_context_and_retires_unsent_push():
    await jarvis_coordinator.accept_text_turn("Begin", idempotency_key="suppressed-session")
    live = await jarvis_coordinator.get_active_session()
    task = (await task_service.dispatch(goal="Hello World", origin_surface="test", idempotency_key="suppressed-task",
        notification_intent="requested", notification_reason="Notify me")).task
    await task_service.complete(task.id, summary="The page is ready", idempotency_key="suppressed-completion")
    await task_presentation_service.sync_candidates()
    before = await task_presentation_service.build_attention_context(session_id=live.id, generation=live.generation)
    await task_service.set_update_policy(task.id,"silent",notification_intent="suppressed",
        notification_reason="I will check later",idempotency_key="suppress-after-completion")
    after = await task_presentation_service.build_attention_context(session_id=live.id,generation=live.generation)
    assert after.items[0]["presentation_id"] == before.items[0]["presentation_id"]
    assert after.items[0]["facts"]["notification"]["revision"] == 2
    assert after.items[0]["facts"]["notification"]["intent"] == "suppressed"
    assert "Verified outcome" in terminal_payload(after.items[0]["facts"])
    assert after.items[0]["facts"]["commentary"] is False
    async with session_scope() as session:
        notifications = (await session.execute(select(NotificationOutbox).where(NotificationOutbox.task_id == task.id))).scalars().all()
        assert notifications and all(n.status == "superseded" for n in notifications)
    silent = (await task_service.dispatch(goal="Silent task",origin_surface="test",idempotency_key="silent-admit",
        notification_intent="suppressed",notification_reason="Stay quiet")).task
    await task_service.complete(silent.id,summary="Ready quietly",idempotency_key="silent-done")
    await task_presentation_service.sync_candidates()
    async with session_scope() as session:
        assert not (await session.execute(select(NotificationOutbox).where(NotificationOutbox.task_id == silent.id))).scalars().all()
    assert len((await task_presentation_service.build_attention_context(session_id=live.id,generation=live.generation)).items) == 2


def test_intent_validation_and_transport_receipt_has_no_floor_authority():
    assert normalize("follow_progress","requested","Notify me")[0] == "follow_progress"
    assert normalize(None,"suppressed","Quiet")[0] == "silent"
    for policy, intent, reason in [("silent","requested","Notify"),("follow_progress","suppressed","Quiet"),
            (None,"requested",None),(None,"requested","x"*161),(None,None,"Reason without intent")]:
        with pytest.raises(ValueError): normalize(policy,intent,reason)
    assert "assistant_update_submitted" not in FLOOR_EVENT_TYPES


@pytest.mark.asyncio
@pytest.mark.parametrize("floor",["user_speaking","assistant_speaking","assistant_generating"])
async def test_append_receipt_preserves_actual_floor(floor):
    await jarvis_coordinator.accept_text_turn("Begin",idempotency_key="actual-floor")
    live = await jarvis_coordinator.get_active_session()
    async with session_scope() as session:
        repo = JarvisDomainRepository(session)
        await repo.append_session_event_once(session_id=live.id,event_type="provider."+floor,
            payload_json={},idempotency_key="floor-before-receipt")
        expected = await jarvis_coordinator._floor_state(session,live.id)
        await repo.append_session_event_once(session_id=live.id,event_type="provider.assistant_update_submitted",
            payload_json={},idempotency_key="transport-receipt")
        assert await jarvis_coordinator._floor_state(session,live.id) == expected
