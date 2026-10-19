import asyncio
from copy import deepcopy
from datetime import timedelta
import json

import pytest
from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.db.tables import LiveSessionEvent, Task
from app.db.repositories.jarvis import JarvisDomainRepository
from app.services import live_decisions as decisions
from app.services.conversation_observer import validate_finding, now
from app.services.live_request_context import advanced_context
from app.services.jarvis_coordinator import jarvis_coordinator
from test_conversation_observer import evidence, finding


def native_provenance(e, variant="existing"):
    for d in e["native_decisions"]:
        d.update(bookkeeping_variant=variant, questions_hash=decisions.question_identity(d["presentations"], variant))


@pytest.mark.parametrize("preferences,bookkeeper,count", [(False,"responses",0),(True,"responses",1),(False,"decisions",1),(True,"decisions",2)])
def test_four_configuration_triggers(monkeypatch,preferences,bookkeeper,count):
    monkeypatch.setattr(settings,"jarvis_conversation_observer_mode","enabled")
    monkeypatch.setattr(settings,"jarvis_decisions_preferences",preferences)
    monkeypatch.setattr(settings,"jarvis_bookkeeper",bookkeeper)
    p={"presentation_id":"p1","source_task_event_id":"result1","summary":"Page ready"}
    ts=decisions.triggers([p])
    assert len(ts)==count
    assert len({t["event_type"] for t in ts})==count
    assert all("model" not in t["decisions"] for t in ts)


def test_clear_meaning_needs_source_window_not_verbatim_quote():
    e=evidence();f=finding();f.pop("quote",None)
    assert validate_finding(e,f)["supported"]
    e["fragments"].insert(0,{"id":"old","speaker":"user","text":"Something earlier","start_ms":0,"end_ms":100})
    assert validate_finding(e,f)["supported"]
    e["fragments"].insert(1,{"id":"backchannel","speaker":"user","text":"Mhm","start_ms":550,"end_ms":700})
    assert validate_finding(e,f)["supported"]


def test_minor_concealment_is_quality_not_an_unplayed_track():
    e=evidence()
    for r in e["playback"]:r["concealed_fraction"]=.02
    assert validate_finding(e,finding())["supported"]
    for r in e["playback"]:r["concealed_fraction"]=.15
    assert not validate_finding(e,finding())["supported"]
    for r in e["playback"]:r["concealed_fraction"]=0
    e["playback"][3]["concealed_fraction"]=.5
    assert not validate_finding(e,finding())["supported"]


def test_outcome_quality_counts_clock_overlap_once_without_losing_word_coverage():
    from app.services.conversation_observer import playback_support
    e=evidence();e["fragments"]=[
        {"id":"f1","speaker":"assistant","text":"The page","start_ms":500,"end_ms":800},
        {"id":"f2","speaker":"assistant","text":"is ready.","start_ms":700,"end_ms":1000}]
    e["playback"][3]["concealed_fraction"]=.2
    f=finding();f["fragment_ids"]=["f1","f2"]
    assert not playback_support([e["fragments"][1]],e["playback"])["supported"]
    result=validate_finding(e,f)
    assert result["supported"] and result["concealed_fraction"]==pytest.approx(50/600)
    e["playback"][3]["muted"]=True
    assert not validate_finding(e,f)["supported"]


def test_separate_covered_fragments_cannot_bridge_track_replacement():
    e=evidence();e["fragments"]=[
        {"id":"f1","speaker":"assistant","text":"The page","start_ms":500,"end_ms":700},
        {"id":"f2","speaker":"assistant","text":"is ready.","start_ms":1500,"end_ms":1700}]
    for r in e["playback"][6:]:r["track_id"]="replacement"
    f=finding();f["fragment_ids"]=["f1","f2"]
    assert validate_finding(e,f)["reason"]=="outcome_track_or_clock_replaced"


@pytest.mark.parametrize("change,expected", [(None,True),("later_caller",False),
    ("long_caller",False),("unknown_order",False),("unknown_answer_order",False),("punctuation_only",False)])
def test_delayed_final_question_delta_is_not_an_answer_interruption(change,expected):
    e=evidence();e["fragments"]=[
        {"id":"question-tail","speaker":"user","text":"now","start_ms":500,"end_ms":700,"sequence":19},
        {"id":"answer-start","speaker":"assistant","text":"It's","start_ms":500,"end_ms":700,"sequence":20},
        {"id":"answer-end","speaker":"assistant","text":"complete.","start_ms":700,"end_ms":900,"sequence":22}]
    f=finding();f["fragment_ids"]=["answer-start","answer-end"]
    if change=="later_caller":e["fragments"][0]["sequence"]=21
    if change=="long_caller":e["fragments"][0]["start_ms"]=100
    if change=="unknown_order":e["fragments"][0].pop("sequence")
    if change=="unknown_answer_order":e["fragments"][2].pop("sequence")
    if change=="punctuation_only":
        e["fragments"][2]["text"]="."
        f["classification"]="partial"  # "It's." is not a semantically complete outcome.
    assert validate_finding(e,f)["supported"] is expected


@pytest.mark.parametrize("coalesced",[False,True])
@pytest.mark.parametrize("later_caller",[False,True])
def test_prior_question_tail_does_not_make_delivery_depend_on_fragmentation(coalesced,later_caller):
    e=evidence();e["fragments"]=[
        {"id":"question-tail","speaker":"user","text":"now","start_ms":500,"end_ms":700,"sequence":21 if later_caller else 19},
        {"id":"answer-start","speaker":"assistant","text":"It's completed" if coalesced else "It's", "start_ms":500,"end_ms":700,"sequence":20},
        {"id":"answer-end","speaker":"assistant","text":"." if coalesced else " completed.","start_ms":700,"end_ms":900,"sequence":22}]
    f=finding();f["fragment_ids"]=["answer-start","answer-end"]
    assert validate_finding(e,f)["supported"] is (not later_caller)


def test_decisions_keeps_concurrent_pending_checklist_facts():
    first={"presentation_id":"one","source_task_event_id":"r1","event_type":"task.completed","goal":"Page","summary":"Page ready"}
    second={"presentation_id":"two","source_task_event_id":"r2","event_type":"task.failed","goal":"Checklist","summary":"Failed"}
    result=decisions.terminal_context({"items":[{"facts":first,"state":"pending"}]},[second])
    assert result==[first,second]


def test_native_result_correlates_version_and_source_event():
    e=evidence();e["fragments"][0]["provider_event_id"]="source1"
    p={"presentation_id":"p1","source_task_event_id":"r1"};e["presentations"]=[p]
    d={"presentations":[deepcopy(p)],"source_event_id":"source1","event":{"decision":{"answers":[
        {"name":"outcome_p1","type":"choice","choice":"complete"}]}}}
    e["native_decisions"]=[d]
    native_provenance(e)
    result,usage=decisions.native_assessment(e)
    assert result["outcomes"][0]["fragment_ids"]==["f1"]
    assert validate_finding(e,result["outcomes"][0])["supported"]
    changed=deepcopy(e);changed["presentations"][0]["source_task_event_id"]="r2"
    assert decisions.native_assessment(changed)[0]["outcomes"][0]["classification"]=="uncertain"
    d["source_event_id"]="unknown"
    assert not validate_finding(e,decisions.native_assessment(e)[0]["outcomes"][0])["supported"]


def test_native_out_of_order_and_refusal_do_not_reuse_older_complete():
    e=evidence();p={"presentation_id":"p1","source_task_event_id":"r1"};e["presentations"]=[p]
    e["fragments"][0]["text"]="The page is"
    e["fragments"][0]["provider_event_id"]="old"
    e["fragments"].append({"id":"f2","provider_event_id":"new","speaker":"assistant","text":" But actually", "start_ms":1600,"end_ms":1800})
    def event(source,value):return {"presentations":[deepcopy(p)],"source_event_id":source,"event":{"decision":{"answers":[
        {"name":"outcome_p1","type":"choice","choice":value}]}}}
    e["native_decisions"]=[event("new","partial"),event("old","complete")]
    native_provenance(e)
    result,_=decisions.native_assessment(e)
    assert result["outcomes"][0]["classification"]=="partial"
    e["native_decisions"][0]["event"]["decision"]["answers"][0]={"name":"outcome_p1","type":"refusal"}
    assert decisions.native_assessment(e)[0]["outcomes"][0]["classification"]=="uncertain"


@pytest.mark.parametrize("later_label", ["none", "partial", "complete"])
def test_native_later_conversation_cannot_replace_outcome_citation(later_label):
    e=evidence();p={"presentation_id":"p1","source_task_event_id":"r1"};e["presentations"]=[p]
    e["fragments"][0]["provider_event_id"]="outcome"
    e["fragments"].append({"id":"desk", "provider_event_id":"later", "speaker":"assistant",
        "text":"If you would like, I can help arrange your desk.", "start_ms":1800, "end_ms":2000})
    def event(source,label):return {"presentations":[deepcopy(p)],"source_event_id":source,
        "event":{"decision":{"answers":[{"name":"outcome_p1","type":"choice","choice":label}]}}}
    e["native_decisions"]=[event("later",later_label),event("outcome","complete")]
    native_provenance(e)
    result,usage=decisions.native_assessment(e)
    assert result["outcomes"][0]["fragment_ids"]==["f1"]
    assert len(usage["native_events"])==2
    # Later played conversation cannot supply missing playback of the outcome.
    e["playback"]=[]
    assert not validate_finding(e,decisions.native_assessment(e)[0]["outcomes"][0])["supported"]


def test_native_associated_contradiction_keeps_communication_uncertain(monkeypatch):
    monkeypatch.setattr(settings,"jarvis_decisions_bookkeeping_variant","separate")
    e=evidence();p={"presentation_id":"p1","source_task_event_id":"r1"};e["presentations"]=[p]
    e["fragments"][0]["provider_event_id"]="outcome"
    e["fragments"].append({"id":"correction", "provider_event_id":"later", "speaker":"assistant",
        "text":"Actually, the page failed.", "start_ms":1800, "end_ms":2000})
    def event(source,consistent):return {"presentations":[deepcopy(p)],"source_event_id":source,
        "event":{"decision":{"answers":[{"name":name+"p1","type":"choice","choice":label}
            for name,label in [("outcome_","complete"),("association_","yes"),("consistency_",consistent)]]}}}
    e["native_decisions"]=[event("later","no"),event("outcome","yes")]
    native_provenance(e,"separate")
    finding=decisions.native_assessment(e)[0]["outcomes"][0]
    assert finding["classification"]=="uncertain" and finding["fragment_ids"]==["correction"]
    assert not validate_finding(e,finding)["supported"]


@pytest.mark.parametrize("change", ["restart", "missing_variant", "wrong_questions", "punctuation"])
def test_native_recovery_requires_original_variant_and_source_provenance(monkeypatch,change):
    e=evidence();p={"presentation_id":"p1","source_task_event_id":"r1"};e["presentations"]=[p]
    e["fragments"][0]["provider_event_id"]="source"
    e["native_decisions"]=[{"presentations":[deepcopy(p)],"source_event_id":"source",
        "event":{"decision":{"answers":[{"type":"choice","name":name+"p1","choice":label}
            for name,label in [("outcome_","complete"),("association_","no"),("consistency_","yes")]]}}}]
    native_provenance(e,"separate")
    monkeypatch.setattr(settings,"jarvis_decisions_bookkeeping_variant","concise")
    d=e["native_decisions"][0]
    if change=="missing_variant":d.pop("bookkeeping_variant")
    if change=="wrong_questions":d["questions_hash"]="different"
    if change=="punctuation":
        e["fragments"][0]["text"]="."
        d["event"]["decision"]["answers"][1]["choice"]="yes"
    result,_=decisions.native_assessment(e)
    assert not validate_finding(e,result["outcomes"][0])["supported"]
    if change!="punctuation":assert result["outcomes"][0]["classification"]=="uncertain"


@pytest.mark.asyncio
async def test_partial_request_is_refreshed_without_task_or_infinite_refresh():
    await jarvis_coordinator.accept_text_turn("Begin",idempotency_key="fresh-start")
    live=await jarvis_coordinator.get_active_session()
    async def speech(text,start,key):
        async with session_scope() as db:
            await JarvisDomainRepository(db).append_session_event_once(session_id=live.id,event_type="provider.user_transcript_delta",
                payload_json={"delta":text,"start_ms":start,"end_ms":start+100,"generation":live.generation},idempotency_key=key)
    await speech("Tell me",1100,"fresh-1")
    async def later():
        await asyncio.sleep(.02);await speech(" when it finishes.",1300,"fresh-2")
    task=asyncio.create_task(later())
    context=await advanced_context(live.id,live.generation,1000,"correlation",quiet=.08,bound=.3)
    await task
    assert len(context["later_user_context"]["fragments"])==2
    async with session_scope() as db:
        assert not (await db.execute(select(Task))).scalars().all()
        await JarvisDomainRepository(db).append_session_event_once(session_id=live.id,event_type="brain.tool.result",
            payload_json={"generation":live.generation,"origin_correlation_id":"correlation","output":context},idempotency_key="fresh-receipt")
    assert await advanced_context(live.id,live.generation,1000,"correlation",quiet=0,bound=0) is None
    with pytest.raises(ValueError,match="authority"):
        await advanced_context(live.id,live.generation+1,1000,"correlation",quiet=0,bound=0)


@pytest.mark.asyncio
async def test_admission_does_not_block_sideband(monkeypatch):
    from test_openai_live_adapter import FakeLiveSocket,wait_for_sideband
    from app.services.openai_live_adapter import OpenAILiveAdapter
    monkeypatch.setattr(settings,"openai_api_key","test-key")
    monkeypatch.setattr(settings,"jarvis_conversation_observer_mode","enabled")
    socket=FakeLiveSocket();started=asyncio.Event();release=asyncio.Event();events=[]
    async def create(**kwargs): return {"session":{"id":"p"},"transport":{"sdp":"answer"}}
    async def connect(*args,**kwargs):return socket
    async def event(s,g,e):
        if e.kind=="shell_call":started.set();await release.wait()
        events.append(e)
    adapter=OpenAILiveAdapter(session_creator=create,websocket_connector=connect)
    try:
        await adapter.connect(session_id="s",generation=1,context={},on_event=event,sdp_offer="offer")
        await wait_for_sideband(adapter,("s",1))
        await adapter.handle_event("s",1,{"type":"response.event","delegation_id":"d","event":{"type":"response.output_item.done","response_id":"r",
            "item":{"type":"function_call","call_id":"c","name":"dispatch_task","arguments":json.dumps({"goal":"Page","intent_id":"page","notification_intent":"discretionary","notification_reason":"No request yet"})}}})
        await asyncio.wait_for(started.wait(),1)
        await asyncio.wait_for(adapter.handle_event("s",1,{"type":"session.input_transcript.delta","event_id":"input-1","delta":"Tell me when it finishes"}),.2)
        assert any(e.kind=="user_transcript_delta" and e.payload["provider_event_id"]=="input-1" for e in events)
        provider=adapter._sessions[("s",1)]
        provider.decision_configs["cfg"]=[{"presentation_id":"p1","source_task_event_id":"result1"}]
        await adapter.handle_event("s",1,{"type":"session.updated","client_event_id":"unrelated"})
        assert not provider.decision_context
        await adapter.handle_event("s",1,{"type":"session.updated","client_event_id":"cfg"})
        native={"type":"decision.event","event_id":"decision-1","source_event_id":"input-1",
            "source_event_type":"session.input_transcript.delta","event":{"type":"decision.completed","decision":{"answers":[]}}}
        await adapter.handle_event("s",1,native);await adapter.handle_event("s",1,native)
        assert len([e for e in events if e.kind=="decision"])==1
        # Source preceded the configuration receipt: a delayed decision cannot
        # acquire the new result context merely because it arrived later.
        assert next(e for e in events if e.kind=="decision").payload["presentations"]==[]
        await adapter.handle_event("s",1,{"type":"session.output_transcript.delta","event_id":"output-old",
            "delta":"The page is ready.","start_ms":500,"end_ms":1000})
        provider.decision_configs["cfg-new"]=[{"presentation_id":"p1","source_task_event_id":"result2"}]
        await adapter.handle_event("s",1,{"type":"session.updated","client_event_id":"cfg-new"})
        await adapter.handle_event("s",1,{**native,"event_id":"decision-old","source_event_id":"output-old",
            "source_event_type":"session.output_transcript.delta"})
        delayed=next(e for e in events if e.kind=="decision" and e.payload["event_id"]=="decision-old")
        assert delayed.payload["presentations"][0]["source_task_event_id"]=="result1"
        assert delayed.payload["questions_hash"]==decisions.question_identity(delayed.payload["presentations"],delayed.payload["bookkeeping_variant"])
        await adapter.handle_event("s",1,{**native,"event_id":"before-source","source_event_id":"future"})
        await adapter.handle_event("s",1,{"type":"session.output_transcript.delta","event_id":"future","delta":"Ready."})
        unresolved=next(e for e in events if e.kind=="decision" and e.payload["event_id"]=="before-source")
        assert unresolved.payload["source_provenance_missing"] and unresolved.payload["presentations"]==[]
    finally:
        release.set();await adapter.close(session_id="s",generation=1,reason="test",emit_event=False)


def test_frozen_comparison_has_explicit_separate_budgets_and_labels():
    from app.announcement_eval.natural_updates import manifest,fixtures,preference_fixtures
    m=manifest()
    assert len(fixtures())==24 and len(preference_fixtures())==12
    assert m["browser_limit"]==40 and m["android_limit"]==6 and m["calibration_limit"]==60
    assert m["max_finalists"]==2 and m["confirmation_browser_limit"]==32
    assert len(m["screen_cases"])==2
    assert sum(2 if c["id"] in {"between_sessions","interruption_reconnect"} else 1 for c in m["confirmation_cases"])==16


def test_both_bookkeepers_share_semantic_policy_and_ambiguity_has_a_real_alternative():
    from app.services.conversation_observer import POLICY, SEMANTIC_POLICY
    from app.announcement_eval.natural_updates import fixtures
    assert SEMANTIC_POLICY in POLICY and SEMANTIC_POLICY in decisions.COMMUNICATION_POLICY
    ambiguous=next(f for f in fixtures() if f["id"]=="ambiguous")
    assert "compact layout" in ambiguous["evidence"]["fragments"][0]["text"]
    assert ambiguous["expected_classifications"]==["uncertain"]


@pytest.mark.asyncio
@pytest.mark.parametrize("configuration", ["1","2","3","4"])
@pytest.mark.parametrize("condition", ["complete","partial","muted","stale"])
async def test_all_configurations_preserve_delivery_authority(monkeypatch, configuration, condition):
    from app.announcement_eval.natural_updates import CONFIGURATIONS
    from app.services.conversation_observer import conversation_observer
    from app.db.tables import ConversationObservation, TaskPresentation
    from test_conversation_observer import prepared
    cfg=CONFIGURATIONS[configuration]
    monkeypatch.setattr(settings,"jarvis_bookkeeper",cfg["bookkeeper"])
    monkeypatch.setattr(settings,"jarvis_decisions_preferences",cfg["preferences"])
    live,task,item=await prepared(monkeypatch)
    e=evidence();f=finding();f["presentation_id"]=item["presentation_id"]
    async with session_scope() as db:
        p=await db.get(TaskPresentation,item["presentation_id"])
        e["presentations"]=[{"presentation_id":p.id,"source_task_event_id":p.source_task_event_id}]
        if condition=="stale":e["presentations"][0]["source_task_event_id"]="superseded"
        if condition=="partial":f["classification"]="partial"
        if condition=="muted":
            for report in e["playback"]:report["muted"]=True
        if cfg["bookkeeper"]=="decisions":
            e["fragments"][0]["provider_event_id"]="source1"
            e["native_decisions"]=[{"presentations":deepcopy(e["presentations"]),"source_event_id":"source1",
                "event":{"decision":{"answers":[{"type":"choice","name":"outcome_"+p.id,"choice":f["classification"]}]}}}]
            native_provenance(e)
            result,_=decisions.native_assessment(e)
        else:result={"outcomes":[f],"flags":[]}
        job=ConversationObservation(session_id=live.id,generation=live.generation,evidence_hash="comparison",input_json=e)
        db.add(job);await db.flush();jid=job.id
    await conversation_observer.apply(jid,result,{"model":"local_mock"})
    await conversation_observer.apply(jid,result,{"model":"local_mock"})
    async with session_scope() as db:
        assert (await db.get(TaskPresentation,item["presentation_id"])).state==("handled" if condition=="complete" else "pending")


def test_decisions_outage_does_not_qualify_unrun_fixtures_or_retry_endpoint():
    from app.announcement_eval.natural_updates import unavailable_classifiers,calibration_status
    saved={"runs":[{"classifier":"responses","id":str(i),"passed":True} for i in range(24)]+[
        {"classifier":"decisions","id":"first","passed":False,"http_status":503,
         "provider_error":'{"error":{"type":"server_error","message":"Decision admission timed out."}}'}]}
    assert calibration_status(saved)=={"responses":True,"decisions":False,"preferences":False}
    assert set(unavailable_classifiers(saved))=={"decisions","preferences"}
    saved["runs"][-1]["http_status"]=401
    assert not unavailable_classifiers(saved)


def test_revised_calibration_retains_requests_and_availability_blocks():
    from app.announcement_eval.natural_updates import calibration_runs,unavailable_classifiers,calibration_status
    old={"runs":[{"classifier":"decisions","id":"first","http_status":503,"provider_error":"server_error"}]}
    saved={"runs":[],"retained_batches":[old]}
    assert len(calibration_runs(saved))==1
    assert set(unavailable_classifiers(saved))=={"decisions","preferences"}
    assert not any(calibration_status(saved).values())


@pytest.mark.parametrize("failed_id", ["real_admission_1", "ambiguous", "quoted"])
def test_failed_semantic_label_blocks_authority_even_when_other_cases_pass(failed_id):
    from app.announcement_eval.natural_updates import calibration_status, fixtures
    saved={"runs":[{"classifier":"responses", "id":f["id"],
                    "passed":f["id"]!=failed_id} for f in fixtures()]}
    assert len(saved["runs"])==24
    assert not calibration_status(saved)["responses"]


@pytest.mark.asyncio
async def test_calibration_does_not_spend_on_a_batch_that_cannot_fit(tmp_path, monkeypatch):
    from app.announcement_eval import natural_updates as natural
    import json
    saved={"identity":natural.manifest(), "runs":[], "retained_batches":[{"runs":[
        {"classifier":"responses", "id":str(i), "passed":True} for i in range(48)] + [
        {"classifier":"decisions", "id":"first", "http_status":503,
         "provider_error":"server_error"}]}]}
    path=tmp_path/"calibration.json"
    path.write_text(json.dumps(saved), encoding="utf8")
    async def forbidden(*args, **kwargs):
        pytest.fail("An incomplete fixed batch must not make a provider request")
    monkeypatch.setattr(natural.observer, "infer", forbidden)
    monkeypatch.setattr(natural.decisions, "request", forbidden)
    with pytest.raises(ValueError, match="requires 24 requests; only 11 remain"):
        await natural.calibrate(tmp_path)
    assert len(natural.calibration_runs(json.loads(path.read_text(encoding="utf8"))))==49


@pytest.mark.asyncio
async def test_preference_review_revision_fence(monkeypatch):
    from test_conversation_observer import prepared
    from app.services.task_service import task_service
    from app.services.task_notification_preferences import current
    live,task,item=await prepared(monkeypatch)
    async with session_scope() as db:
        stored=(await db.execute(select(Task).where(Task.goal=="Hello World page"))).scalar_one()
        task_id=stored.id
        expected=(await current(db,stored))["revision"]
    await task_service.set_update_policy(task_id,"silent",notification_intent="suppressed",notification_reason="Newer user correction",idempotency_key="newer")
    with pytest.raises(ValueError,match="superseded"):
        await task_service.set_update_policy(task_id,"critical_only",notification_intent="requested",notification_reason="Old review",idempotency_key="old-review",
            notification_authority={"session_id":live.id,"generation":live.generation,"expected_revision":expected,"source_sequence":0})
    async with session_scope() as db:
        assert (await current(db,(await db.execute(select(Task).where(Task.id==task_id))).scalar_one()))["intent"]=="suppressed"


@pytest.mark.asyncio
async def test_input_trigger_uses_source_order_not_late_arrival(monkeypatch):
    from test_conversation_observer import prepared
    from app.services import notification_preference_review as review
    monkeypatch.setattr(settings,"jarvis_decisions_preferences",True)
    live,task,item=await prepared(monkeypatch)
    monkeypatch.setattr(review,"now",lambda:now()+timedelta(seconds=3))
    async with session_scope() as db:
        repo=JarvisDomainRepository(db)
        for source,text in [("old","Tell me when it finishes"),("new","Actually, don't tell me when")]:
            await repo.append_session_event_once(session_id=live.id,event_type="provider.user_transcript_delta",
                payload_json={"generation":live.generation,"provider_event_id":source,"delta":text},idempotency_key=source)
        for source,value in [("new","incomplete"),("old","requested")]:
            await repo.append_session_event_once(session_id=live.id,event_type="provider.decision",
                payload_json={"generation":live.generation,"source_event_type":"session.input_transcript.delta","source_event_id":source,
                    "event":{"decision":{"answers":[{"name":"notification_preference","type":"choice","choice":value}]}}},idempotency_key="decision-"+source)
    assert await review.process_one() is False


@pytest.mark.asyncio
async def test_second_rounded_timestamp_cannot_finish_an_outcome_batch_early(monkeypatch):
    from test_conversation_observer import prepared
    from app.services import conversation_observer as module
    from app.db.tables import ConversationObservation
    live,task,item=await prepared(monkeypatch)
    base=now().replace(microsecond=0)
    async with session_scope() as db:
        result=await JarvisDomainRepository(db).append_session_event_once(session_id=live.id,
            event_type="provider.assistant_transcript_delta",payload_json={"delta":"The Hello World page",
                "start_ms":500,"end_ms":1000,"generation":live.generation},idempotency_key="prefix-batch")
        result.event.created_at=base
    await module.conversation_observer.ingest(session_id=live.id,generation=live.generation,
        controller_id=live.controller_id,sequence=1,reports=evidence()["playback"])
    monkeypatch.setattr(module,"now",lambda:base+timedelta(seconds=1.1))
    await module.conversation_observer.collect()
    async with session_scope() as db:
        assert not (await db.execute(select(ConversationObservation))).scalars().all()
        result=await JarvisDomainRepository(db).append_session_event_once(session_id=live.id,
            event_type="provider.assistant_transcript_delta",payload_json={"delta":" is ready.",
                "start_ms":1000,"end_ms":1500,"generation":live.generation},idempotency_key="complete-batch")
        result.event.created_at=base+timedelta(seconds=1)
    monkeypatch.setattr(module,"now",lambda:base+timedelta(seconds=3.1))
    await module.conversation_observer.collect()
    async with session_scope() as db:
        jobs=(await db.execute(select(ConversationObservation))).scalars().all()
        assert len(jobs)==1
        assert jobs[0].input_json["fragments"][-1]["text"]==" is ready."


@pytest.mark.asyncio
@pytest.mark.parametrize("mode",["active","terminal","newer_context","wrong_target","conflicting_policy","invalid_json","gateway_error","gateway_value_error"])
async def test_native_preference_review_executes_real_gateway_and_retains_response(monkeypatch,mode):
    import httpx
    from test_conversation_observer import prepared
    from app.services import notification_preference_review as review
    from app.services.task_service import task_service
    from app.services.task_notification_preferences import current
    from app.db.tables import ConversationObservation
    monkeypatch.setattr(settings,"jarvis_decisions_preferences",True)
    live,_,_=await prepared(monkeypatch)
    task=(await task_service.dispatch(goal="Another page",origin_surface="jarvis",origin_conversation_id=live.conversation_id,
        idempotency_key="review-real-task",notification_intent="requested",notification_reason="Please tell me when ready")).task
    if mode=="terminal":await task_service.complete(task.id,summary="Another page ready",idempotency_key="review-real-complete")
    async with session_scope() as db:
        repo=JarvisDomainRepository(db)
        await repo.append_session_event_once(session_id=live.id,event_type="provider.user_transcript_delta",
            payload_json={"generation":live.generation,"provider_event_id":"correction","delta":"Actually, do not notify me about another page."},idempotency_key="real-correction")
        await repo.append_session_event_once(session_id=live.id,event_type="provider.decision",
            payload_json={"generation":live.generation,"source_event_type":"session.input_transcript.delta","source_event_id":"correction",
            "event":{"decision":{"answers":[{"name":"notification_preference","type":"choice","choice":"suppressed"}]}}},idempotency_key="real-decision")
    monkeypatch.setattr(review,"now",lambda:now()+timedelta(seconds=3))
    args={"task_reference":"not-supplied" if mode=="wrong_target" else task.id,"presentation_policy":"silent",
        "notification_intent":"suppressed","notification_reason":"User explicitly changed to silence"}
    if mode=="conflicting_policy":args["presentation_policy"]="critical_only"
    raw={"id":"resp-review-real","model":"gpt-5.6-luna","usage":{"total_tokens":123},
        "output":[{"type":"function_call","name":"set_task_update_policy","arguments":json.dumps(args)}]}
    if mode=="invalid_json":raw['output'][0]['arguments']='not-json'
    if mode in {'gateway_error','gateway_value_error'}:
        from app.services.jarvis_brain import restricted_brain_gateway
        async def broken(**kwargs):raise (TypeError if mode=='gateway_error' else ValueError)('Unexpected application defect')
        monkeypatch.setattr(restricted_brain_gateway,'execute',broken)
    async def post(client,url,**kwargs):
        assert url.endswith('/responses')
        if mode=="newer_context":
            async with session_scope() as db:
                await JarvisDomainRepository(db).append_session_event_once(session_id=live.id,
                    event_type="provider.user_transcript_delta",payload_json={"generation":live.generation,
                    "provider_event_id":"newer","delta":"Actually keep the notification after all."},idempotency_key="newer-review-input")
        return httpx.Response(200,json=raw,request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx.AsyncClient,"post",post)
    if mode in {'gateway_error','gateway_value_error'}:
        with pytest.raises(TypeError if mode=='gateway_error' else ValueError,match="Unexpected application"):await review.process_one()
    else:assert await review.process_one()
    async with session_scope() as db:
        stored=await db.scalar(select(Task).where(Task.id==task.id));pref=await current(db,stored)
        job=(await db.execute(select(ConversationObservation))).scalars().one()
        assert job.usage_json['request_id']=='resp-review-real' and job.usage_json['usage']['total_tokens']==123
        assert job.result_json['proposed_calls'][0]['arguments']==raw['output'][0]['arguments']
        if mode in {'active','terminal'}:
            assert pref['intent']=='suppressed' and pref['revision']==2
            assert stored.status==('completed' if mode=='terminal' else task.status)
            assert job.status=='completed' and job.result_json['receipts'][0]['task_id']==task.id
        else:
            assert pref['intent']=='requested' and pref['revision']==1
            if mode=='newer_context':assert job.result_json['receipts']==[{'status':'superseded'}]
            elif mode in {'gateway_error','gateway_value_error'}:assert job.status=='failed' and 'Unexpected application' in job.error
            else:
                assert job.status=='completed' and job.result_json['receipts'][0]['status']=='rejected'
                assert job.result_json['receipts'][0]['error']=='invalid_preference_proposal'
    if mode not in {'gateway_error','gateway_value_error','newer_context'}:assert await review.process_one() is False
