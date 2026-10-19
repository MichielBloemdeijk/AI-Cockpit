from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.db.tables import ConversationObservation, LiveSession, TaskPresentation
from app.db.repositories.jarvis import JarvisDomainRepository
from app.services.conversation_observer import (conversation_observer, playback_support, validate_finding, now)
from app.services.jarvis_coordinator import jarvis_coordinator
from app.services.task_service import task_service
from app.services.task_presentations import task_presentation_service


def test_context_is_not_eligible_completion_evidence():
    from app.services.conversation_observer import semantic_input
    e=evidence(); e["fragments"][0]["sequence"]=5
    e["presentations"]=[{"presentation_id":"p1","exposed_after_sequence":6,"facts":{}}]
    assert semantic_input(e)[0]["results"][0]["eligible_fragment_ids"]==[]
    assert validate_finding(e,finding())["reason"]=="speech_precedes_outcome_context"
    e["fragments"][0]["sequence"]=7
    compact,handles=semantic_input(e)
    assert compact["results"][0]["eligible_fragment_ids"]==["a1"] and handles["a1"]=="f1" and handles["r1"]=="p1"
    assert "f1" not in __import__("json").dumps(compact)  # the model sees short handles only
    assert validate_finding(e,finding())["supported"]


def test_explicit_subscription_required_in_enabled_voice_tools(monkeypatch):
    from app.services.jarvis_tool_catalog import get_jarvis_tool_definitions
    monkeypatch.setattr(settings,"jarvis_conversation_observer_mode","enabled")
    for t in get_jarvis_tool_definitions("openai_live"):
        if t["name"] in {"dispatch_task","start_workflow","set_task_update_policy"}:
            assert {"notification_intent","notification_reason"} <= set(t["parameters"]["required"])


@pytest.mark.asyncio
async def test_admission_receipt_exposes_later_speech_without_repairing_preference(monkeypatch):
    from app.services.voice_session_adapter import fake_voice_session_adapter
    from app.services.task_notification_preferences import current
    from app.db.tables import Task
    monkeypatch.setattr(settings,"jarvis_conversation_observer_mode","enabled")
    await jarvis_coordinator.accept_text_turn("Begin",idempotency_key="late-start")
    live=await jarvis_coordinator.get_active_session()
    async with session_scope() as db:
        repo=JarvisDomainRepository(db)
        for i,(gen,start,text) in enumerate([(live.generation,100,"Create the page"),
                (live.generation,5200,"Tell me when it finishes"),(live.generation-1,5500,"Ignore this old generation")]):
            await repo.append_session_event_once(session_id=live.id,event_type="provider.user_transcript_delta",
                payload_json={"delta":text,"start_ms":start,"end_ms":start+200,"generation":gen},idempotency_key=f"late-{i}")
    outputs=[]
    async def capture(**kwargs): outputs.append(kwargs["output"])
    monkeypatch.setattr(fake_voice_session_adapter,"send_tool_result",capture)
    await jarvis_coordinator._execute_shell_call(session_id=live.id,generation=live.generation,
        tool_name="dispatch_task",arguments={"goal":"Create a page","intent_id":"page",
            "notification_intent":"discretionary","notification_reason":"No request in initial context"},
        call_id="late-admission",origin_timeline={"offset_ms":4400})
    receipt=outputs[0]["result"]
    assert receipt["status"] == "request_context_advanced"
    assert not receipt["applied"]
    assert [f["text"] for f in receipt["later_user_context"]["fragments"]]==["Tell me when it finishes"]
    async with session_scope() as db:
        assert (await db.execute(select(Task).where(Task.goal=="Create a page"))).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_batch_age_starts_with_relevant_speech_not_old_admission(monkeypatch):
    from app.services import conversation_observer as module
    monkeypatch.setattr(settings,"jarvis_conversation_observer_mode","enabled")
    await jarvis_coordinator.accept_text_turn("Begin",idempotency_key="batch-start")
    live=await jarvis_coordinator.get_active_session()
    async with session_scope() as db:
        (await db.get(LiveSession,live.id)).provider_adapter="openai_live"
        old=await JarvisDomainRepository(db).append_session_event_once(session_id=live.id,
            event_type="provider.assistant_transcript_delta",payload_json={"delta":"Task queued.","start_ms":0,"end_ms":200},idempotency_key="old-admission")
        old.event.created_at=now()-timedelta(seconds=30)
    task=(await task_service.dispatch(goal="Page",origin_surface="test",idempotency_key="batch-task")).task
    await task_service.complete(task.id,summary="Page ready",idempotency_key="batch-done")
    await task_presentation_service.sync_candidates()
    await task_presentation_service.build_attention_context(session_id=live.id,generation=live.generation)
    async with session_scope() as db:
        repo=JarvisDomainRepository(db)
        await repo.append_session_event_once(session_id=live.id,event_type="provider.assistant_transcript_delta",
            payload_json={"delta":"The page", "start_ms":500,"end_ms":700},idempotency_key="fresh-outcome")
        await repo.append_session_event_once(session_id=live.id,event_type="client.playback",
            payload_json={"reports":evidence()["playback"]},idempotency_key="batch-playback")
    await conversation_observer.collect()
    async with session_scope() as db:
        assert not (await db.execute(select(ConversationObservation))).scalars().all()
    # Without a user reaction, the update is judged after the reaction window.
    monkeypatch.setattr(module,"now",lambda:now()+timedelta(seconds=3))
    await conversation_observer.collect()
    async with session_scope() as db:
        assert not (await db.execute(select(ConversationObservation))).scalars().all()
    monkeypatch.setattr(module,"now",lambda:now()+timedelta(seconds=8))
    await conversation_observer.collect()
    async with session_scope() as db:
        assert len((await db.execute(select(ConversationObservation))).scalars().all())==1


def evidence():
    reports = [{"id": str(i), "track_id": "t1", "client_start_ms": 1000+i*250, "client_end_ms":1250+i*250,
        "playback_start_ms": i*250, "playback_end_ms": (i+1)*250,
        "origin_lower_ms": 1000, "origin_upper_ms": 1100, "mapping_supported": True,
        "paused": False, "muted": False, "volume": 1, "gap": False, "track_live": True, "received_audio": True} for i in range(16)]
    return {"fragments": [{"id":"f1", "speaker":"assistant", "text":"The page is ready.", "start_ms":500,"end_ms":1500}],
        "playback":reports, "presentations":[]}


def finding():
    return {"presentation_id":"p1","classification":"complete","fragment_ids":["f1"],"reason":"Verified outcome"}


def test_complete_citation_and_playback():
    e=evidence()
    assert validate_finding(e,finding())["supported"]


@pytest.mark.parametrize("damage",["muted","paused","volume","gap","track_live","mapping_supported","origin_upper_ms","playback_end_ms"])
def test_bad_playback_never_handles(damage):
    e=evidence()
    for r in e["playback"]:
        r[damage] = {"muted":True,"paused":True,"volume":0,"gap":True,"track_live":False,
            "mapping_supported":False,"origin_upper_ms":5000,"playback_end_ms":r["playback_start_ms"]}[damage]
    assert not validate_finding(e,finding())["supported"]


def test_gap_and_track_replacement_do_not_bridge_outcome():
    e=evidence()
    e["playback"]=[r for r in e["playback"] if r["id"]!="4"]
    assert not validate_finding(e,finding())["supported"]
    e=evidence()
    for r in e["playback"][4:]: r["track_id"]="replacement"
    assert not validate_finding(e,finding())["supported"]


def test_overlapping_caller_text_does_not_veto_played_speech():
    # Whether the user took it in is a semantic judgment of their reaction,
    # not a transcript-timestamp heuristic (provider input timestamps lag).
    e=evidence()
    e["fragments"].append({"id":"caller","speaker":"user","text":"Hold on", "start_ms":800,"end_ms":1600})
    assert validate_finding(e,finding())["supported"]


def test_possibly_unheard_is_not_delivery():
    e=evidence(); f=finding(); f["classification"]="possibly_unheard"
    assert validate_finding(e,f)=={"supported":False,"reason":"possibly_unheard"}


def test_caller_after_complete_outcome_does_not_block_delivery():
    e=evidence()
    e["fragments"].append({"id":"caller","speaker":"user","text":"Thanks", "start_ms":2000,"end_ms":2300})
    assert validate_finding(e,finding())["supported"]


@pytest.mark.parametrize("change",["user","partial","duplicate","unknown"])
def test_invalid_citations_remain_pending(change):
    e=evidence(); f=finding()
    if change=="user": e["fragments"][0]["speaker"]="user"
    if change=="phantom": f["quote"]="The recipe is ready."
    if change=="partial": f["classification"]="partial"
    if change=="duplicate": f["fragment_ids"]=["f1","f1"]
    if change=="unknown": f["fragment_ids"]=["absent"]
    assert not validate_finding(e,f)["supported"]


async def prepared(monkeypatch):
    monkeypatch.setattr(settings,"jarvis_conversation_observer_mode","enabled")
    await jarvis_coordinator.accept_text_turn("Begin",idempotency_key="observer-start")
    live=await jarvis_coordinator.get_active_session()
    async with session_scope() as db:
        (await db.get(LiveSession,live.id)).provider_adapter="openai_live"
    task=(await task_service.dispatch(goal="Hello World page",origin_surface="test",idempotency_key="observer-task",
        notification_intent="requested",notification_reason="Tell me when ready")).task
    await task_service.complete(task.id,summary="The page is ready.",idempotency_key="observer-done")
    await task_presentation_service.sync_candidates()
    attention=await task_presentation_service.build_attention_context(session_id=live.id,generation=live.generation)
    return live,task,attention.items[0]


@pytest.mark.asyncio
async def test_terminal_self_report_cannot_bypass_observer(monkeypatch):
    live,task,item=await prepared(monkeypatch)
    result=await task_presentation_service.acknowledge_task_update(session_id=live.id,generation=live.generation,
        presentation_id=item["presentation_id"],exposure_token=item["exposure_token"])
    assert result["status"]=="verification_pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("closed,shadow,partial",[(False,False,False),(True,False,False),(False,True,False),(False,False,True)])
async def test_observer_commit_exact_result_and_idempotency(monkeypatch,closed,shadow,partial):
    live,task,item=await prepared(monkeypatch)
    e=evidence(); f=finding(); f["presentation_id"]=item["presentation_id"]
    if partial: f["classification"]="partial"
    async with session_scope() as db:
        p=await db.get(TaskPresentation,item["presentation_id"])
        e["presentations"]=[{"presentation_id":p.id,"source_task_event_id":p.source_task_event_id}]
        job=ConversationObservation(session_id=live.id,generation=live.generation,evidence_hash="h",input_json=e)
        db.add(job); await db.flush(); job_id=job.id
        if closed: (await db.get(LiveSession,live.id)).lifecycle_state="disconnected"
    if shadow: monkeypatch.setattr(settings,"jarvis_conversation_observer_mode","shadow")
    await conversation_observer.apply(job_id,{"outcomes":[f],"flags":[],"promises":[]},{"policy_hash":"test"})
    await conversation_observer.apply(job_id,{"outcomes":[f],"flags":[],"promises":[]},{})
    async with session_scope() as db:
        p=await db.get(TaskPresentation,item["presentation_id"])
        assert p.state==("pending" if shadow or partial else "handled")
        if p.state=="handled": assert p.handled_by=="conversation_observer"
        assert (await db.get(ConversationObservation,job_id)).status=="completed"


@pytest.mark.asyncio
async def test_stale_result_and_wrong_owner(monkeypatch):
    live,task,item=await prepared(monkeypatch)
    with pytest.raises(ValueError,match="owning"):
        await conversation_observer.ingest(session_id=live.id,generation=live.generation,
            controller_id="wrong",sequence=1,reports=evidence()["playback"][:1])
    e=evidence(); e["presentations"]=[{"presentation_id":item["presentation_id"],"source_task_event_id":"old"}]
    async with session_scope() as db:
        job=ConversationObservation(session_id=live.id,generation=live.generation,evidence_hash="stale",input_json=e)
        db.add(job); await db.flush(); jid=job.id
    f=finding();f["presentation_id"]=item["presentation_id"]
    await conversation_observer.apply(jid,{"outcomes":[f],"flags":[],"promises":[]},{})
    async with session_scope() as db:
        assert (await db.get(TaskPresentation,item["presentation_id"])).state=="pending"


@pytest.mark.asyncio
async def test_late_caller_evidence_is_checked_before_commit(monkeypatch):
    live,task,item=await prepared(monkeypatch)
    e=evidence(); f=finding(); f["presentation_id"]=item["presentation_id"]
    async with session_scope() as db:
        p=await db.get(TaskPresentation,item["presentation_id"])
        e["presentations"]=[{"presentation_id":p.id,"source_task_event_id":p.source_task_event_id}]
        job=ConversationObservation(session_id=live.id,generation=live.generation,evidence_hash="late",input_json=e)
        db.add(job);await db.flush();jid=job.id
    async with session_scope() as db:
        await JarvisDomainRepository(db).append_session_event_once(session_id=live.id,event_type="provider.user_transcript_delta",
            payload_json={"delta":"Hold on", "start_ms":800,"end_ms":1600,"generation":live.generation},idempotency_key="late-caller")
    await conversation_observer.apply(jid,{"outcomes":[f],"flags":[],"promises":[]},{})
    async with session_scope() as db:
        assert (await db.get(TaskPresentation,item["presentation_id"])).state=="pending"
        assert (await db.get(ConversationObservation,jid)).result_json["validation"][p.id]["reason"]=="newer_user_speech"


@pytest.mark.asyncio
async def test_subscription_routing(monkeypatch):
    live,task,item=await prepared(monkeypatch)
    async with session_scope() as db:
        p=await db.get(TaskPresentation,item["presentation_id"])
        fact=task_presentation_service._context_fact(p,exposure_token="t",commentary=True)
        assert fact["commentary"] and fact["completed_at"]
        p.required_facts_json={**p.required_facts_json,"notification":{"intent":"suppressed"}}
        assert not task_presentation_service._context_fact(p,exposure_token="t",commentary=True)["commentary"]


@pytest.mark.asyncio
async def test_collection_deduplicates_and_recovery_uses_saved_evidence(monkeypatch):
    from app.services import conversation_observer as module
    live,task,item=await prepared(monkeypatch)
    async with session_scope() as db:
        row=await db.get(LiveSession,live.id);row.controller_id="owner"
        await JarvisDomainRepository(db).append_session_event_once(session_id=live.id,
            event_type="provider.assistant_transcript_delta",payload_json={"delta":"The page is ready.","start_ms":500,"end_ms":1500},
            idempotency_key="outcome")
    await conversation_observer.ingest(session_id=live.id,generation=live.generation,controller_id="owner",
        sequence=1,reports=evidence()["playback"])
    # No provider calls: make the batching clock eligible.
    monkeypatch.setattr(module,"now",lambda:now()+timedelta(seconds=8))
    await conversation_observer.collect();await conversation_observer.collect()
    async with session_scope() as db:
        jobs=(await db.execute(select(ConversationObservation))).scalars().all()
        assert len(jobs)==1
        jobs[0].status="running";jobs[0].lease_until=now()-timedelta(seconds=1)
    calls=[]
    async def fake(e):
        calls.append(e)
        return {"outcomes":[{"presentation_id":item["presentation_id"],"classification":"none","fragment_ids":[],"reason":"fixture"}],"flags":[],"promises":[]},{}
    monkeypatch.setattr(module,"infer",fake)
    assert await conversation_observer.process_one()
    assert not await conversation_observer.process_one()
    assert len(calls)==1


@pytest.mark.asyncio
@pytest.mark.parametrize("same_generation",[True,False])
async def test_reason_refresh_does_not_repeat_requested_commentary(monkeypatch,same_generation):
    live,task,item=await prepared(monkeypatch)
    async with session_scope() as db:
        await JarvisDomainRepository(db).append_session_event_once(session_id=live.id,
            event_type="provider.assistant_update_submitted", payload_json={"presentation_ids":[item["presentation_id"]],"generation":live.generation if same_generation else live.generation-1,
                "submitted_transcript":__import__("json").dumps({"notification":{"intent":"requested","revision":1}})}, idempotency_key="receipt")
        result=await task_presentation_service.reserve_in_session(db,session_id=live.id,generation=live.generation,
            user_owns_floor=False,commentary=True)
        assert result and result.facts[0]["commentary"] is (not same_generation)


@pytest.mark.asyncio
async def test_delayed_negative_assessment_does_not_refresh_context_after_new_assistant_speech(monkeypatch):
    live,task,item=await prepared(monkeypatch)
    e=evidence();e["fragments"][0]["sequence"]=1
    async with session_scope() as db:
        p=await db.get(TaskPresentation,item["presentation_id"])
        e["presentations"]=[{"presentation_id":p.id,"source_task_event_id":p.source_task_event_id}]
        job=ConversationObservation(session_id=live.id,generation=live.generation,evidence_hash="delayed-negative",input_json=e)
        db.add(job);await db.flush();jid=job.id
        await JarvisDomainRepository(db).append_session_event_once(session_id=live.id,
            event_type="provider.assistant_transcript_delta",payload_json={"delta":"The page is ready.",
            "start_ms":2000,"end_ms":2300,"generation":live.generation},idempotency_key="new-speech")
    f={"presentation_id":item["presentation_id"],"classification":"none","fragment_ids":[],"reason":"Older window"}
    await conversation_observer.apply(jid,{"outcomes":[f],"flags":[],"promises":[]},{})
    async with session_scope() as db:
        p=await db.get(TaskPresentation,item["presentation_id"])
        assert p.state=="pending"
        assert "communication_assessment" not in p.required_facts_json
        assert (await db.get(ConversationObservation,jid)).status=="completed"


def test_input_activity_schema_requires_measured_pairs_and_identity():
    from app.models.jarvis import JarvisPlaybackInterval
    from pydantic import ValidationError
    r={k:v for k,v in evidence()["playback"][0].items() if k!="id"}
    for change in [{"input_audio_energy":0},{"input_audio_energy":0,"input_audio_duration_ms":250},
                   {"input_source_id":"mic","input_audio_energy":float("nan"),"input_audio_duration_ms":250}]:
        with pytest.raises(ValidationError):JarvisPlaybackInterval(**r,**change)
    assert JarvisPlaybackInterval(**r,input_source_id="mic",input_audio_energy=0,input_audio_duration_ms=250)


@pytest.mark.asyncio
async def test_user_reaction_is_awaited_and_possibly_unheard_stays_pending(monkeypatch):
    from app.services import conversation_observer as module
    live,task,item=await prepared(monkeypatch)
    async with session_scope() as db:
        row=await db.get(LiveSession,live.id);row.controller_id="owner"
        repo=JarvisDomainRepository(db)
        await repo.append_session_event_once(session_id=live.id,event_type="provider.assistant_transcript_delta",
            payload_json={"delta":"The Hello World page is ready.","start_ms":500,"end_ms":1500},idempotency_key="outcome")
        await repo.append_session_event_once(session_id=live.id,event_type="provider.user_transcript_delta",
            payload_json={"delta":"Hold on, tell me after I finish.","start_ms":1600,"end_ms":2600},idempotency_key="reaction")
    await conversation_observer.ingest(session_id=live.id,generation=live.generation,controller_id="owner",
        sequence=1,reports=evidence()["playback"])
    # The user reacted; once they are quiet the batch is ready (no 6 s wait).
    monkeypatch.setattr(module,"now",lambda:now()+timedelta(seconds=3))
    await conversation_observer.collect()
    async with session_scope() as db:
        jobs=(await db.execute(select(ConversationObservation))).scalars().all()
        assert len(jobs)==1
        assert any(f["speaker"]=="user" and "Hold on" in f["text"] for f in jobs[0].input_json["fragments"])
    async def fake(e):
        outcome=next(f for f in e["fragments"] if f["speaker"]=="assistant")
        return {"outcomes":[{"presentation_id":item["presentation_id"],"classification":"possibly_unheard",
            "fragment_ids":[outcome["id"]],"reason":"User asked to hear it after finishing"}],"flags":[],"promises":[]},{}
    monkeypatch.setattr(module,"infer",fake)
    assert await conversation_observer.process_one()
    async with session_scope() as db:
        p=await db.get(TaskPresentation,item["presentation_id"])
        assert p.state=="pending"
        assert p.required_facts_json["communication_assessment"]["classification"]=="possibly_unheard"


def test_one_fully_played_utterance_establishes_communication():
    e=evidence()
    e["fragments"]=[{"id":"early","speaker":"assistant","text":"The page is ready.","start_ms":-5000,"end_ms":-4000,"sequence":1},
        {"id":"q","speaker":"user","text":"Is it done?","start_ms":0,"end_ms":400,"sequence":2},
        {"id":"late","speaker":"assistant","text":"Yes, it is finished.","start_ms":500,"end_ms":1500,"sequence":3}]
    f=finding(); f["fragment_ids"]=["early","late"]
    result=validate_finding(e,f)
    assert result["supported"] and result["fragment_ids"]==["late"]
    f["fragment_ids"]=["early"]
    assert not validate_finding(e,f)["supported"]


@pytest.mark.asyncio
async def test_malformed_model_answer_fails_one_job_without_stopping(monkeypatch):
    from app.services import conversation_observer as module
    live,task,item=await prepared(monkeypatch)
    async with session_scope() as db:
        db.add(ConversationObservation(session_id=live.id,generation=live.generation,evidence_hash="bad",
            input_json={**evidence(),"presentations":[{"presentation_id":item["presentation_id"],"source_task_event_id":"x"}]}))
    async def wrong(e):
        raise ValueError("Observer must classify each exact presentation once")
    monkeypatch.setattr(module,"infer",wrong)
    assert await conversation_observer.process_one()
    assert conversation_observer.failure is None
    async with session_scope() as db:
        job=(await db.execute(select(ConversationObservation))).scalars().one()
        assert job.status=="failed" and "exact presentation" in job.error


def test_model_handles_resolve_and_unknown_handles_stay_invalid():
    from app.services.conversation_observer import resolve_handles
    handles={"r1":"p1","a1":"f1"}
    result=resolve_handles({"outcomes":[{"presentation_id":"r1","classification":"complete","fragment_ids":["a1","a9"],"reason":"x"}],
        "flags":[{"kind":"unrecorded_promise","fragment_ids":["a1"],"reason":"y"}],"promises":[]},handles)
    assert result["outcomes"][0]["presentation_id"]=="p1" and result["outcomes"][0]["fragment_ids"]==["f1","a9"]
    assert result["flags"][0]["fragment_ids"]==["f1"]
    assert validate_finding(evidence(),result["outcomes"][0])["reason"]=="invalid_citation"


def test_pre_outcome_citations_are_ignored_not_fatal():
    e=evidence()
    e["fragments"]=[{"id":"early","speaker":"assistant","text":"You won't get updates.","start_ms":100,"end_ms":400,"sequence":1},
        {"id":"f1","speaker":"assistant","text":"It is complete.","start_ms":500,"end_ms":1500,"sequence":5}]
    e["presentations"]=[{"presentation_id":"p1","exposed_after_sequence":3}]
    f=finding(); f["fragment_ids"]=["early","f1"]
    assert validate_finding(e,f)["supported"]
    f["fragment_ids"]=["early"]
    assert validate_finding(e,f)["reason"]=="speech_precedes_outcome_context"


@pytest.mark.parametrize("hole,progress,supported",[(40,40,True),(110,110,True),(40,0,False),(400,400,False)])
def test_short_telemetry_hole_is_bridged_only_with_continuous_player_progress(hole,progress,supported):
    e=evidence()
    after=[r for r in e["playback"] if int(r["id"])>=6]
    for r in after:
        r["client_start_ms"]+=hole; r["client_end_ms"]+=hole
        r["playback_start_ms"]+=progress; r["playback_end_ms"]+=progress
    assert validate_finding(e,finding())["supported"] is supported


@pytest.mark.asyncio
@pytest.mark.parametrize("final_before_user,expected",[(False,True),(True,False)])
async def test_new_user_turn_during_assistant_speech_is_marked_overlapped(monkeypatch,final_before_user,expected):
    from app.services import conversation_observer as module
    live,task,item=await prepared(monkeypatch)
    async with session_scope() as db:
        row=await db.get(LiveSession,live.id);row.controller_id="owner"
        repo=JarvisDomainRepository(db)
        await repo.append_session_event_once(session_id=live.id,event_type="provider.assistant_transcript_delta",
            payload_json={"delta":"The page is ready.","start_ms":500,"end_ms":1500},idempotency_key="outcome")
        if final_before_user:
            await repo.append_session_event_once(session_id=live.id,event_type="provider.assistant_transcript",
                payload_json={"transcript":"The page is ready."},idempotency_key="final")
        await repo.append_session_event_once(session_id=live.id,event_type="provider.user_transcript_delta",
            payload_json={"delta":"Oh sorry, quick question","start_ms":9000,"end_ms":9800},idempotency_key="user")
    await conversation_observer.ingest(session_id=live.id,generation=live.generation,controller_id="owner",
        sequence=1,reports=evidence()["playback"])
    monkeypatch.setattr(module,"now",lambda:now()+timedelta(seconds=8))
    await conversation_observer.collect()
    async with session_scope() as db:
        job=(await db.execute(select(ConversationObservation))).scalars().one()
        user=[f for f in job.input_json["fragments"] if f["speaker"]=="user"][-1]
        assert bool(user.get("overlapped_assistant"))is expected


@pytest.mark.asyncio
async def test_admission_refreshes_context_once_per_request_even_if_the_user_keeps_talking(monkeypatch):
    from app.services.voice_session_adapter import fake_voice_session_adapter
    from app.db.tables import Task
    monkeypatch.setattr(settings,"jarvis_conversation_observer_mode","enabled")
    await jarvis_coordinator.accept_text_turn("Begin",idempotency_key="keep-talking")
    live=await jarvis_coordinator.get_active_session()
    async def speak(i,start,text):
        async with session_scope() as db:
            await JarvisDomainRepository(db).append_session_event_once(session_id=live.id,event_type="provider.user_transcript_delta",
                payload_json={"delta":text,"start_ms":start,"end_ms":start+200,"generation":live.generation},idempotency_key=f"talk-{i}")
    await speak(0,100,"Create the page")
    await speak(1,5200,"and tell me if it fails")
    outputs=[]
    async def capture(**kwargs): outputs.append(kwargs["output"])
    monkeypatch.setattr(fake_voice_session_adapter,"send_tool_result",capture)
    arguments={"goal":"Create a page","intent_id":"page","notification_intent":"requested","notification_reason":"Tell me if it fails"}
    await jarvis_coordinator._execute_shell_call(session_id=live.id,generation=live.generation,tool_name="dispatch_task",
        arguments=arguments,call_id="first",origin_timeline={"offset_ms":4400})
    assert outputs[-1]["result"]["status"]=="request_context_advanced"
    await speak(2,9000,"anyway, about my demo plans")  # the user keeps talking
    await jarvis_coordinator._execute_shell_call(session_id=live.id,generation=live.generation,tool_name="dispatch_task",
        arguments=arguments,call_id="second",origin_timeline={"offset_ms":4400})
    assert outputs[-1]["result"].get("status")!="request_context_advanced"
    async with session_scope() as db:
        assert (await db.execute(select(Task).where(Task.goal=="Create a page"))).scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_late_wake_phrase_transcript_is_not_an_overlap(monkeypatch):
    from app.services import conversation_observer as module
    live,task,item=await prepared(monkeypatch)
    async with session_scope() as db:
        row=await db.get(LiveSession,live.id);row.controller_id="owner"
        repo=JarvisDomainRepository(db)
        await repo.append_session_event_once(session_id=live.id,event_type="provider.assistant_transcript_delta",
            payload_json={"delta":"Quick update: the page is ready.","start_ms":500,"end_ms":1500},idempotency_key="outcome")
        await repo.append_session_event_once(session_id=live.id,event_type="provider.user_transcript_delta",
            payload_json={"delta":" Jarvis","start_ms":9000,"end_ms":9300},idempotency_key="late-wake")
    await conversation_observer.ingest(session_id=live.id,generation=live.generation,controller_id="owner",
        sequence=1,reports=evidence()["playback"])
    monkeypatch.setattr(module,"now",lambda:now()+timedelta(seconds=8))
    await conversation_observer.collect()
    async with session_scope() as db:
        job=(await db.execute(select(ConversationObservation))).scalars().one()
        assert not any(f.get("overlapped_assistant") for f in job.input_json["fragments"])


def test_long_android_report_intervals_still_cover_playback():
    e=evidence()
    merged=[]
    for a,b in zip(e["playback"][0::2],e["playback"][1::2]):  # 500 ms reports instead of 250 ms
        merged.append({**a,"client_end_ms":b["client_end_ms"],"playback_end_ms":b["playback_end_ms"]})
    # One 1 s report (UI jank) spanning the cited outcome.
    e["playback"]=merged[:3]+[{**merged[3],"client_end_ms":merged[4]["client_end_ms"],"playback_end_ms":merged[4]["playback_end_ms"]}]+merged[5:]
    assert validate_finding(e,finding())["supported"]
