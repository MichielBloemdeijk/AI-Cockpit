from datetime import datetime, timedelta, timezone
import json
from typing import Any

import pytest
from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.db.tables import AssistantPromise, ConversationObservation, LiveSession, LiveSessionEvent, TaskPresentation
from app.db.repositories.jarvis import JarvisDomainRepository
from app.services import assistant_notes
from app.services.conversation_observer import conversation_observer
from app.services.jarvis_coordinator import jarvis_coordinator
from app.services.live_task_updates import announcement_text
from app.services.openai_live_adapter import OpenAILiveAdapter
from app.services.task_presentations import task_presentation_service
from app.services.task_service import task_service
from tests.test_openai_live_adapter import FakeLiveSocket, wait_for_sideband


async def requested_outcome(monkeypatch, *, assessment=None):
    monkeypatch.setattr(settings, "jarvis_conversation_observer_mode", "enabled")
    await jarvis_coordinator.accept_text_turn("Begin", idempotency_key="notes-start")
    live = await jarvis_coordinator.get_active_session()
    async with session_scope() as db:
        (await db.get(LiveSession, live.id)).provider_adapter = "openai_live"
    task = (await task_service.dispatch(goal="Write a Hello World page", origin_surface="test", idempotency_key="notes-task",
        notification_intent="requested", notification_reason="Tell me when it is ready")).task
    await task_service.complete(task.id, summary="The Hello World page is ready.", idempotency_key="notes-done")
    await task_presentation_service.sync_candidates()
    async with session_scope() as db:
        row = (await db.execute(select(TaskPresentation).where(TaskPresentation.task_id == task.id))).scalar_one()
        if assessment:
            row.required_facts_json = {**row.required_facts_json, "communication_assessment": {"classification": assessment}}
    return live, task, row.id


@pytest.mark.asyncio
async def test_notes_are_readable_and_carry_the_users_wish(monkeypatch):
    live, task, _ = await requested_outcome(monkeypatch)
    async with session_scope() as db:
        notes = await assistant_notes.render(db, session_id=live.id)
    assert '"Write a Hello World page" finished' in notes["text"]
    assert "The Hello World page is ready." in notes["text"] and "They asked to be told." in notes["text"]
    assert task.id not in notes["text"]  # no internal identifiers for Live to read aloud
    assert "re-offer" not in notes["text"]


@pytest.mark.asyncio
async def test_possibly_unheard_adds_a_single_reoffer_hint_and_changes_the_signature(monkeypatch):
    live, task, pid = await requested_outcome(monkeypatch)
    async with session_scope() as db:
        before = await assistant_notes.render(db, session_id=live.id)
        row = await db.get(TaskPresentation, pid)
        row.required_facts_json = {**row.required_facts_json, "communication_assessment": {"classification": "possibly_unheard"}}
    async with session_scope() as db:
        after = await assistant_notes.render(db, session_id=live.id)
    assert "may not have heard it" in after["text"] and "once" in after["text"]
    assert before["signature"] != after["signature"]


@pytest.mark.asyncio
async def test_handled_outcomes_move_to_already_told(monkeypatch):
    live, task, pid = await requested_outcome(monkeypatch)
    async with session_scope() as db:
        row = await db.get(TaskPresentation, pid)
        row.state, row.handled_at, row.handled_by = "handled", datetime.now(timezone.utc), "conversation_observer"
    async with session_scope() as db:
        notes = await assistant_notes.render(db, session_id=live.id)
    assert "Already told them" in notes["text"] and "Waiting for the user:\n- Nothing." in notes["text"]


@pytest.mark.asyncio
async def test_promises_are_listed_and_expire_after_the_next_conversation(monkeypatch):
    live, task, _ = await requested_outcome(monkeypatch)
    async with session_scope() as db:
        db.add(AssistantPromise(session_id=live.id, generation=live.generation, text="I'll remind you to stretch after this.",
            source_key="k1", source_fragment_ids=["f"], status="open"))
    async with session_scope() as db:
        notes = await assistant_notes.render(db, session_id=live.id)
    assert "You promised:" in notes["text"] and "remind you to stretch" in notes["text"]
    # Two newer conversations later, an unkept promise is dropped.
    async with session_scope() as db:
        for index in range(2):
            current = await db.get(LiveSession, live.id)
            db.add(LiveSession(id=f"later-{index}", conversation_id=current.conversation_id, generation=1,
                lifecycle_state="closed", provider_adapter="openai_live",
                created_at=datetime.now(timezone.utc) + timedelta(minutes=index + 1)))
    async with session_scope() as db:
        notes = await assistant_notes.render(db, session_id="later-1")
        promise = (await db.execute(select(AssistantPromise))).scalar_one()
    assert promise.status == "dropped" and "You promised" not in notes["text"]


@pytest.mark.asyncio
async def test_observer_records_and_resolves_promises(monkeypatch):
    live, task, pid = await requested_outcome(monkeypatch)
    async with session_scope() as db:
        repo = JarvisDomainRepository(db)
        promise_event = await repo.append_session_event_once(session_id=live.id, event_type="provider.assistant_transcript_delta",
            payload_json={"delta": "I'll wait until you're done and then give you the result.", "start_ms": 0, "end_ms": 900},
            idempotency_key="promise")
        fragment = {"id": promise_event.event.id, "speaker": "assistant", "text": promise_event.event.payload_json["delta"],
            "start_ms": 0, "end_ms": 900, "sequence": promise_event.event.sequence}
        job = ConversationObservation(session_id=live.id, generation=live.generation, evidence_hash="promise-job",
            input_json={"fragments": [fragment], "presentations": [], "playback": [], "promises": []})
        db.add(job); await db.flush(); job_id = job.id
    flag = {"kind": "unrecorded_promise", "fragment_ids": [fragment["id"]], "reason": "Commitment to give the result later"}
    await conversation_observer.apply(job_id, {"outcomes": [], "flags": [flag, flag], "promises": []}, {})
    async with session_scope() as db:
        promise = (await db.execute(select(AssistantPromise))).scalar_one()
        assert promise.status == "open" and "wait until you're done" in promise.text
        job = ConversationObservation(session_id=live.id, generation=live.generation, evidence_hash="kept-job",
            input_json={"fragments": [], "presentations": [], "playback": [],
                "promises": [{"promise_id": promise.id, "text": promise.text}]})
        db.add(job); await db.flush(); kept_id = job.id
    await conversation_observer.apply(kept_id, {"outcomes": [], "flags": [],
        "promises": [{"id": promise.id, "status": "kept", "reason": "Gave the result after the user finished"}]}, {})
    async with session_scope() as db:
        assert (await db.execute(select(AssistantPromise))).scalar_one().status == "kept"


@pytest.mark.asyncio
@pytest.mark.parametrize("previous,now,speaks", [(None, "possibly_unheard", True),
    ("possibly_unheard", "possibly_unheard", False), (None, None, False)])
async def test_requested_outcome_gets_one_reoffer_when_possibly_unheard(monkeypatch, previous, now, speaks):
    live, task, pid = await requested_outcome(monkeypatch, assessment=now)
    submitted = {"notification": {"intent": "requested", "revision": 1},
        "communication_assessment": {"classification": previous} if previous else None}
    async with session_scope() as db:
        await JarvisDomainRepository(db).append_session_event_once(session_id=live.id,
            event_type="provider.assistant_update_submitted", payload_json={"presentation_ids": [pid],
                "generation": live.generation, "submitted_transcript": json.dumps(submitted)}, idempotency_key="receipt")
        result = await task_presentation_service.reserve_in_session(db, session_id=live.id, generation=live.generation,
            user_owns_floor=False, commentary=True)
        assert result.facts[0]["commentary"] is speaks


def test_announcement_text_names_task_and_result_without_identifiers():
    fact = {"goal": "Write a Hello World page", "summary": "The page is ready.", "event_type": "task.completed",
        "presentation_id": "p-123", "task_id": "t-456", "communication_assessment": {"classification": "possibly_unheard"}}
    text = announcement_text(fact)
    assert '"Write a Hello World page" finished' in text and "The page is ready." in text
    assert "p-123" not in text and "t-456" not in text and "may not have heard" in text


@pytest.mark.asyncio
async def test_adapter_speaks_requested_outcomes_and_refreshes_notes_quietly(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    socket = FakeLiveSocket()

    async def create(**_: Any) -> dict[str, Any]:
        return {"session": {"id": "live_notes"}, "transport": {"sdp": "answer"}}

    async def connect(*_: Any, **__: Any) -> FakeLiveSocket:
        return socket

    async def on_event(*_: Any) -> None:
        return None

    notes = {"text": "JARVIS NOTES\nWaiting for the user:\n- Nothing.", "signature": "s1"}
    adapter = OpenAILiveAdapter(session_creator=create, websocket_connector=connect)
    config = OpenAILiveAdapter._session_config({"assistant_notes": notes})
    assert "JARVIS NOTES" in config["instructions"] and "CURRENT_ATTENTION_CHECKLIST" not in config["instructions"]
    await adapter.connect(session_id="s", generation=1, context={"assistant_notes": notes}, on_event=on_event, sdp_offer="offer")
    await wait_for_sideband(adapter, ("s", 1))
    # Unchanged notes and no facts: nothing is injected.
    await adapter.create_response(session_id="s", generation=1, request_id="u0", response_kind="task_update",
        presentation_ids=(), facts=(), context={"attention_context": {"items": [], "deltas": []}, "assistant_notes": notes})
    assert [m["type"] for m in socket.sent] == ["session.update"]
    fact = {"goal": "Write a Hello World page", "summary": "The page is ready.", "event_type": "task.completed",
        "presentation_id": "p1", "commentary": True, "notification": {"intent": "requested"}}
    changed = {"text": notes["text"].replace("- Nothing.", '- "Write a Hello World page" finished.'), "signature": "s2"}
    await adapter.create_response(session_id="s", generation=1, request_id="u1", response_kind="task_update",
        presentation_ids=("p1",), facts=(fact,), context={"attention_context": {"items": [], "deltas": []}, "assistant_notes": changed})
    commentary, thinking = socket.sent[-2], socket.sent[-1]
    assert commentary["type"] == "session.commentary.append" and "The page is ready." in commentary["content"]
    assert "p1" not in commentary["content"]
    assert thinking["type"] == "session.thinking.append" and "finished." in thinking["content"]
    session = adapter._sessions[("s", 1)]
    assert session.pending_commentary[commentary["event_id"]][1] == ("p1",)
    assert json.loads(session.pending_commentary[commentary["event_id"]][2])["notification"]["intent"] == "requested"
    await adapter.close(session_id="s", generation=1, reason="test", emit_event=False)


@pytest.mark.asyncio
async def test_spoken_update_waits_while_the_user_is_speaking(monkeypatch):
    live, task, pid = await requested_outcome(monkeypatch)
    async with session_scope() as db:
        held = await task_presentation_service.reserve_in_session(db, session_id=live.id, generation=live.generation,
            user_owns_floor=True, commentary=True, terminal_only=True, quiet_context=True)
        assert held is None
        ready = await task_presentation_service.reserve_in_session(db, session_id=live.id, generation=live.generation,
            user_owns_floor=False, commentary=True)
        assert ready.facts[0]["commentary"] is True


@pytest.mark.asyncio
async def test_bookkeeper_assesses_delivered_reminders(monkeypatch):
    from app.services import conversation_observer as module
    from datetime import timedelta
    live, task, _ = await requested_outcome(monkeypatch)
    reminder = (await task_service.dispatch(goal="Remind me: Stretch", origin_surface="jarvis_reminder",
        idempotency_key="stretch")).task
    async with session_scope() as db:
        db.add(TaskPresentation(task_id=reminder.id, candidate_type="reminder", state="pending", importance="normal",
            coalescing_key=f"task:{reminder.id}:reminder", source_task_event_id=None, idempotency_key="stretch-reminder",
            required_facts_json={"goal": "Remind me: Stretch", "summary": "Stretch", "event_type": "task.reminder.due"}))
    async with session_scope() as db:
        row = (await db.execute(select(TaskPresentation).where(TaskPresentation.candidate_type == "reminder"))).scalar_one()
        await JarvisDomainRepository(db).append_session_event_once(session_id=live.id, event_type="attention.context_exposed",
            payload_json={"presentation_id": row.id, "generation": live.generation}, idempotency_key="reminder-exposed")
        await JarvisDomainRepository(db).append_session_event_once(session_id=live.id, event_type="provider.assistant_transcript_delta",
            payload_json={"delta": "Time to stretch!", "start_ms": 500, "end_ms": 1500}, idempotency_key="reminder-said")
        (await db.get(LiveSession, live.id)).controller_id = "owner"
    from tests.test_conversation_observer import evidence
    await conversation_observer.ingest(session_id=live.id, generation=live.generation, controller_id="owner",
        sequence=1, reports=evidence()["playback"])
    real_now = module.now
    monkeypatch.setattr(module, "now", lambda: real_now() + timedelta(seconds=8))
    await conversation_observer.collect()
    async with session_scope() as db:
        job = (await db.execute(select(ConversationObservation))).scalars().one()
        assert row.id in {p["presentation_id"] for p in job.input_json["presentations"]}
