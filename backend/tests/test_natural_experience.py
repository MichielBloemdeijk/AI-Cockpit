from copy import deepcopy
import pytest

from app.announcement_eval import agent_review as review, natural_experience as experiment
from app.config import settings
from app.services import live_decisions as decisions


def test_overlap_and_latency_are_observations_not_verdicts():
    old={"schema":"old","dimensions":{"conversation_interruption":{"failures":["substantive_overlap:14"],"unknowns":[],"status":"failed"},
        "deadline":{"failures":["notification_deadline_missed:t"],"unknowns":[]},
        "task_truth":{"failures":["unverified_or_premature_outcome:0"],"unknowns":[]}},"interaction_status":"failed"}
    saved=deepcopy(old);new=review.observations(old)
    assert old==saved
    assert len(new["conversational_observations"])==2
    assert new["dimensions"]["task_truth"]["failures"]==["unverified_or_premature_outcome:0"]
    assert "interaction_status" not in new and new["review_status"]=="requires_agent_judgment"


def test_blocking_and_borderline_reviews_need_an_independent_judgment():
    r={"rubric_sha256":review.IDENTITY,"reviewer":"a","verdict":"material_problem","evidence":["clip"],"reason":"Wrong task"}
    with pytest.raises(ValueError,match="Independent"):review.validate_review(r)
    r["independent_review"]={"reviewer":"b","verdict":"material_problem","reason":"Confirms wrong task"}
    assert not review.validate_review(r)
    r["verdict"]="acceptable"
    with pytest.raises(ValueError,match="disagreement"):review.validate_review(r,adoption=True)


def test_phase_has_exact_new_nonresetting_allowances_and_fixture_counts():
    import json
    m=experiment.manifest()
    assert json.loads(json.dumps(m))==m
    assert m["limits"]=={"classification":240,"exploration":24,"confirmation":32,"android":6,"finalists":2}
    assert len(experiment.fixtures("development"))==24
    assert len(experiment.fixtures("development",True))==12
    assert len(experiment.fixtures("heldout"))==18
    assert len(experiment.fixtures("heldout",True))==8
    assert 24*4+12*3+18*4+8*3==228  # The approved maximum remains 240.
    assert len(experiment.exploration_cases("reference"))==2


@pytest.mark.parametrize("p",experiment.VARIANTS)
@pytest.mark.parametrize("b",experiment.VARIANTS)
def test_variants_use_same_runtime_native_and_offline_questions(p,b):
    item={"presentation_id":"p","task_id":"t","source_task_event_id":"r","goal":"Page"}
    old=(settings.jarvis_decisions_preference_variant,settings.jarvis_decisions_bookkeeping_variant)
    with experiment.configuration("both",p,b):
        ts=decisions.triggers([item])
        assert ts[0]["decisions"]["questions"]==decisions.preference_questions(p)
        assert ts[1]["decisions"]["questions"]==decisions.outcome_questions(item,b)
        assert len(ts[0]["decisions"]["questions"])==(2 if p=="separate" else 1)
        assert len(ts[1]["decisions"]["questions"])==(3 if b=="separate" else 1)
    assert (settings.jarvis_decisions_preference_variant,settings.jarvis_decisions_bookkeeping_variant)==old


def test_separate_task_association_does_not_clear_wrong_task():
    p={"presentation_id":"p"}
    assert decisions.outcome_label({"outcome_p":"complete","association_p":"no"},p,"separate")=="uncertain"
    assert decisions.outcome_label({"outcome_p":"complete","association_p":"yes","consistency_p":"no"},p,"separate")=="uncertain"
    assert decisions.outcome_label({"outcome_p":"complete","association_p":"yes","consistency_p":"yes"},p,"separate")=="complete"


def test_native_semantic_stop_survives_source_repair_without_stopping_other_roles():
    selection={"stopped_native_bookkeeping_variants":{"concise":"Unrelated speech classified complete"}}
    variants={"preference":"existing","bookkeeping":"concise"}
    for family in ("bookkeeping","both"):
        with pytest.raises(ValueError,match="Retained native classification"):
            experiment.validate_authority_stop(selection,family,variants)
        experiment.validate_authority_stop(selection,family,{**variants,"bookkeeping":"separate"})
    for family in ("reference","baseline","preferences"):
        experiment.validate_authority_stop(selection,family,variants)


def test_confirmation_and_android_cannot_mix_variants():
    rows=[{"family":"both","cohort":"confirmation","variants":{"preference":"concise","bookkeeping":"existing"}}]
    experiment.validate_frozen_variants(rows,"both","confirmation",rows[0]["variants"])
    with pytest.raises(ValueError,match="Variant change"):
        experiment.validate_frozen_variants(rows,"both","android",{"preference":"existing","bookkeeping":"existing"})
    rows[0]["source_digest"]="old"
    with pytest.raises(ValueError,match="Production candidate"):
        experiment.validate_frozen_variants(rows,"both","android",rows[0]["variants"],"new")
    experiment.validate_frozen_variants(rows,"both","confirmation",rows[0]["variants"],"old")


def test_harness_repair_cannot_change_production_or_classification(monkeypatch):
    monkeypatch.setattr(experiment,"production_identity",lambda:"same-production")
    before={"source_digest":"old","configuration_digest":"config"};after={"source_digest":"new","configuration_digest":"config"}
    accepted={"rubric_sha256":review.IDENTITY,"reviewer":"a","verdict":"acceptable","reason":"Harness only", "evidence":["diff"],
        "independent_review":{"reviewer":"b","verdict":"acceptable","reason":"Verified runtime unchanged"}}
    proof={"old_source":"old","new_source":"new","production_hash":"same-production","configuration_digest":"config", "classifier_questions_hash":review.identity(experiment.manifest()["question_templates"]),"classifier_fixtures_hash":review.identity(experiment.manifest()["fixtures"]),"review":accepted}
    experiment.validate_harness_repair(proof,before,after)
    proof["production_hash"]="changed-production"
    with pytest.raises(ValueError,match="Repair changed"):experiment.validate_harness_repair(proof,before,after)


def test_implementation_repair_retains_failure_and_classifier_identity(monkeypatch):
    monkeypatch.setattr(experiment,"production_identity",lambda:"repaired-production")
    before={"source_digest":"old","configuration_digest":"config"}
    after={"source_digest":"new","configuration_digest":"config"}
    accepted={"rubric_sha256":review.IDENTITY,"reviewer":"a","verdict":"acceptable",
        "reason":"Verified application evidence repair","evidence":["retained-case"],
        "independent_review":{"reviewer":"b","verdict":"acceptable","reason":"Confirmed narrow repair"}}
    proof={"old_source":"old","new_source":"new","new_production_hash":"repaired-production",
        "classifier_fixtures_hash":review.identity(experiment.manifest()["fixtures"]),
        "classifier_questions_hash":review.identity(experiment.manifest()["question_templates"]),
        "retained_failure_evidence":["retained-case"],"changes":["microphone evidence"],"review":accepted}
    experiment.validate_implementation_repair(proof,before,after)
    proof["classifier_questions_hash"]="changed"
    with pytest.raises(ValueError,match="identity"):experiment.validate_implementation_repair(proof,before,after)


@pytest.mark.asyncio
@pytest.mark.parametrize("consistent,expected",[("yes","complete"),("no","uncertain")])
async def test_separate_inference_preserves_task_and_truth_questions(monkeypatch,consistent,expected):
    fixture=next(x for x in experiment.fixtures("development") if x["id"]=="complete")
    p=fixture["evidence"]["presentations"][0];pid=p["presentation_id"]
    async def request(value,questions):
        assert any(q["name"]=="consistency_"+pid for q in questions)
        assert any(q["name"]=="association_"+pid and "Create a Hello World page" in q["instructions"] for q in questions)
        answers=[{"type":"choice","name":name+pid,"choice":v} for name,v in
            [("outcome_","complete"),("association_","yes"),("consistency_",consistent),("window_","0")]]
        return {"answers":answers},{"answers":answers}
    monkeypatch.setattr(decisions,"request",request)
    result,usage=await decisions.infer(fixture["evidence"],"separate")
    assert result["outcomes"][0]["classification"]==expected
    assert usage["answers"]


def test_taxonomy_mismatch_is_not_same_as_product_failure():
    row=next(r for r in experiment.fixtures("development") if r["id"]=="contradiction")
    result={"outcomes":[{"presentation_id":row["evidence"]["presentations"][0]["presentation_id"],"classification":"none","fragment_ids":[]}]}
    score=experiment.consequence(row,result)
    assert not score["label_match"] and score["product_match"] and not score["harmful_false_delivery"]


@pytest.mark.asyncio
async def test_unfinished_classification_reservation_cannot_be_retried_or_counted(tmp_path,monkeypatch):
    import json
    path=tmp_path/"classification.json"
    path.write_text(json.dumps({"manifest_hash":review.identity(experiment.manifest()),"requests":[{"key":["development","responses","complete"],"started":True}]}),encoding="utf8")
    async def forbidden(*args):raise AssertionError("No provider request allowed")
    monkeypatch.setattr(decisions,"request",forbidden)
    with pytest.raises(ValueError,match="Unfinished request reservation"):
        await experiment.calibrate(tmp_path,"development")


@pytest.mark.asyncio
async def test_uncertain_observation_is_context_not_handled(monkeypatch):
    from test_conversation_observer import prepared
    from app.db.session import session_scope
    from app.db.tables import TaskPresentation,ConversationObservation
    from app.services.conversation_observer import conversation_observer
    live,task,item=await prepared(monkeypatch)
    fixture=experiment.fixtures("development")[0]["evidence"]
    fixture=deepcopy(fixture)
    async with session_scope() as db:
        presentation=await db.get(TaskPresentation,item["presentation_id"])
        fixture["presentations"]=[{"presentation_id":item["presentation_id"],"source_task_event_id":presentation.source_task_event_id}]
        job=ConversationObservation(session_id=live.id,generation=live.generation,evidence_hash="uncertain",status="running",input_json=fixture)
        db.add(job);await db.flush();jid=job.id
    finding={"presentation_id":item["presentation_id"],"classification":"uncertain","fragment_ids":[],"reason":"Cannot establish complete communication"}
    await conversation_observer.apply(jid,{"outcomes":[finding],"flags":[]},{})
    async with session_scope() as db:
        p=await db.get(TaskPresentation,item["presentation_id"])
        assert p.state=="pending" and p.handled_at is None
        assert p.required_facts_json["communication_assessment"]["classification"]=="uncertain"
