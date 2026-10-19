import pytest

from app.db.session import session_scope
from app.db.tables import LiveSession
from app.db.repositories.jarvis import JarvisDomainRepository
from app.services.jarvis_coordinator import jarvis_coordinator
from app.services.task_service import task_service
from app.services.voice_session_adapter import VoiceProviderEvent
from app.services import jarvis_coordinator as module
from app.config import settings
from app.services.task_presentations import task_presentation_service
from app.db.tables import TaskEvent
from sqlalchemy import select

@pytest.mark.asyncio
@pytest.mark.parametrize("floor",["user_speaking","assistant_speaking","assistant_generating"])
@pytest.mark.parametrize("intent",["requested","discretionary","suppressed"])
async def test_production_context_eligibility_and_receipt(floor,intent,monkeypatch):
    await jarvis_coordinator.accept_text_turn("Begin",idempotency_key="floor-origin")
    live = await jarvis_coordinator.get_active_session()
    recorded = []
    class Recorder:
        def is_connected(self,**kw): return True
        async def create_response(self,**kw): recorded.append(kw)
        async def cancel_response(self,**kw): pass
        async def close(self,**kw): pass
    monkeypatch.setattr(module,"get_voice_session_adapter",lambda name:Recorder())
    async with session_scope() as db:
        row = await db.get(LiveSession,live.id)
        row.provider_adapter="openai_live"
        await JarvisDomainRepository(db).append_session_event_once(session_id=live.id,event_type="provider."+floor,
            payload_json={},idempotency_key="actual-floor")
    task=(await task_service.dispatch(goal="Hello World",origin_surface="test",idempotency_key="floor-task",
        notification_intent=intent,notification_reason="Frozen unit preference")).task
    await task_service.complete(task.id,summary="The page is ready",idempotency_key="floor-done")
    await jarvis_coordinator._queue_controlled_response(live.id,live.generation,"task_update")
    assert recorded
    facts=recorded[-1]["facts"]
    if intent=="requested" and floor=="user_speaking":
        assert not facts  # a requested outcome waits until the user finishes their turn
    else:
        assert facts[0]["notification"]["intent"]==intent
        # Only requested outcomes are spoken; the rest are quiet notes context.
        assert facts[0]["commentary"] is (intent=="requested")
    before=await jarvis_coordinator.get_session(live.id)
    assert before.floor_state=={"assistant_generating":"thinking"}.get(floor,floor)
    submission=recorded[-1]
    await jarvis_coordinator.handle_provider_event(live.id,live.generation,VoiceProviderEvent(
        event_id="floor-receipt",kind="assistant_update_submitted",payload={"request_id":submission["request_id"],
            "presentation_ids":list(submission["presentation_ids"]),"submission_ack":"context_appended"}))
    assert (await jarvis_coordinator.get_session(live.id)).floor_state==before.floor_state
    pending=(await task_service.get_task(task.id)).presentations
    assert len(pending)==1 and pending[0].state=="pending" and pending[0].lease_token is None


@pytest.mark.asyncio
async def test_failed_live_context_does_not_wait_for_orchestration_or_grant_task_actions(monkeypatch):
    await jarvis_coordinator.accept_text_turn("Begin",idempotency_key="failure-origin")
    live=await jarvis_coordinator.get_active_session()
    monkeypatch.setattr(settings,"task_orchestration_enabled",True)
    async with session_scope() as db:
        (await db.get(LiveSession,live.id)).provider_adapter="openai_live"
    failed=(await task_service.dispatch(goal="Failure context",origin_surface="test",idempotency_key="failure-task",
        notification_intent="requested",notification_reason="Tell me if it fails")).task
    await task_service.complete(failed.id,status="failed",summary="Input unavailable",idempotency_key="failure-done")
    question=(await task_service.dispatch(goal="Worker question",origin_surface="test",idempotency_key="held-question-task")).task
    await task_service.ask_question(question.id,question="Which input?",idempotency_key="held-question")
    await task_presentation_service.sync_candidates()
    # Away routing still waits for orchestration; the live conversation does not:
    # the failure and the worker's question are both visible to Live right away.
    assert not await task_presentation_service.list_pending()
    assert len(await task_presentation_service.list_pending(include_terminal_context=True))==2
    context=await task_presentation_service.build_attention_context(session_id=live.id,generation=live.generation)
    assert {i["task_id"] for i in context.items}=={failed.id,question.id}
    async with session_scope() as db:
        before=list((await db.execute(select(TaskEvent))).scalars())
    # Exposing context to Live grants no task action.
    await task_presentation_service.build_attention_context(session_id=live.id,generation=live.generation)
    assert (await task_service.get_task(failed.id)).task.status=="failed"
    async with session_scope() as db:
        assert len(list((await db.execute(select(TaskEvent))).scalars()))==len(before)

