"""Bounded natural-assistant comparison; reuses the production WebRTC evaluator."""
import asyncio
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from hashlib import sha256
from pathlib import Path

from app.config import settings
from app.services import conversation_observer as observer, live_decisions as decisions
from app.announcement_eval import agent_review as review, natural_updates as previous

PHASE = "natural-assistant-experience-v1"
VARIANTS = ["existing", "concise", "separate"]
FAMILIES = {
    "reference": {"mode":"off", "preferences":False,"bookkeeper":"responses"},
    "baseline": {"mode":"enabled", "preferences":False,"bookkeeper":"responses"},
    "preferences": {"mode":"enabled", "preferences":True,"bookkeeper":"responses"},
    "bookkeeping": {"mode":"enabled", "preferences":False,"bookkeeper":"decisions"},
    "both": {"mode":"enabled", "preferences":True,"bookkeeper":"decisions"}}


def production_identity():
    root=Path(__file__).resolve().parents[3]
    roots=["backend/app","backend/alembic","backend/workflows","frontend/app","frontend/components",
        "frontend/lib","frontend/scripts","frontend/result-export","frontend/public","android/app/src/main"]
    files=sorted(f for r in roots for f in (root/r).rglob("*") if f.is_file() and "announcement_eval" not in f.parts
        and not any(x in f.parts for x in ("__pycache__","node_modules",".next","build")) and f.suffix not in (".pyc",".log"))
    digest=sha256()
    for f in files:digest.update(f.relative_to(root).as_posix().encode()+b"\0"+sha256(f.read_bytes()).digest())
    return digest.hexdigest()


def observation_cases(cases):
    return [{**deepcopy(case),"observation_tail_ms":30000} for case in cases]


def validate_harness_repair(proof, before, after):
    if (proof.get("old_source")!=before["source_digest"] or proof.get("new_source")!=after["source_digest"]
            or proof.get("production_hash")!=production_identity()
            or proof.get("configuration_digest")!=before["configuration_digest"]
            or before["configuration_digest"]!=after["configuration_digest"]
            or proof.get("classifier_fixtures_hash")!=review.identity(manifest()["fixtures"])
            or proof.get("classifier_questions_hash")!=review.identity(manifest()["question_templates"])):
        raise ValueError("Repair changed production/classifiers/configuration or has wrong source identity")
    if not review.validate_review(proof["review"],adoption=True):raise ValueError("Harness repair lacks independent acceptance")


def validate_implementation_repair(proof, before, after):
    if (proof.get("old_source") != before["source_digest"] or proof.get("new_source") != after["source_digest"]
            or proof.get("new_production_hash") != production_identity()
            or before["configuration_digest"] != after["configuration_digest"]
            or proof.get("classifier_fixtures_hash") != review.identity(manifest()["fixtures"])
            or proof.get("classifier_questions_hash") != review.identity(manifest()["question_templates"])
            or not proof.get("retained_failure_evidence") or not proof.get("changes")):
        raise ValueError("Implementation repair identity or retained evidence mismatch")
    if not review.validate_review(proof["review"],adoption=True):
        raise ValueError("Implementation repair requires independent acceptance")


def validate_authority_stop(selection, family, variants):
    if (FAMILIES[family]["bookkeeper"] == "decisions"
            and variants["bookkeeping"] in selection.get("stopped_native_bookkeeping_variants", {})):
        raise ValueError("Retained native classification failure; select a separately frozen diagnostic variant")


def heldout_bookkeeping():
    template=deepcopy(next(x for x in previous.fixtures() if x["id"]=="complete"))
    cases=[
        ("Ready for you: the page has been built.","complete",True),
        ("That page is finished now.","complete",True),
        ("Your requested Hello World page is ready to open.","complete",True),
        ("The page task failed; it did not produce a page.","uncertain",False),
        ("I have put the page request on my list.","none",False),
        ("I will mention it once the page is ready.","none",False),
        ("Should I tell you when your page finishes?","none",False),
        ("If your page finishes, I can tell you.","none",False),
        ("You said, 'the page is ready', but I have not checked that.","none",False),
        ("Your page has been","partial",False),
        ("The page is ready.","complete",False),
        ("The page is ready.","complete",False),
        ("Your shopping list has finished.","none",False),
        ("I am still working on your page.","none",False),
        ("Yes, the requested page has completed successfully.","complete",True),
        ("I noted your preference to hear when the page finishes.","none",False),
        ("Your page is complete and available now.","complete",True),
        ("Your page is","partial",False)]
    result=[]
    for i,(text,label,deliverable) in enumerate(cases):
        row=deepcopy(template);row.update(id=f"heldout_{i}",expected_classifications=[label],expected_deliverable=deliverable)
        row["evidence"]["fragments"][-1]["text"]=text
        if i in {10,11}:
            for report in row["evidence"]["playback"]:report["muted" if i==10 else "paused"]=True
        result.append(row)
    return result


def heldout_preferences():
    cases=[("Make the page and give me a heads-up once it is ready.","requested"),
        ("Actually, leave that update until I ask you about it.","suppressed"),
        ("Use your judgment about whether to mention the result.","discretionary"),
        ("Please let me know as soon as the","incomplete"),
        ("What does 'keep me posted' mean?","none"),
        ("Forget the silence request; please tell me once it finishes.","requested"),
        ("How is my page coming along?","none"),
        ("Do not announce the result. I will check it later.","suppressed")]
    return [{"id":f"heldout_{i}","input":{"dialogue":[{"speaker":"user","text":t}]},"expected":v} for i,(t,v) in enumerate(cases)]


def fixtures(split, preference=False):
    rows=(previous.preference_fixtures() if preference else previous.fixtures()) if split=="development" else (heldout_preferences() if preference else heldout_bookkeeping())
    if preference:
        for row in rows:
            row["input"]["tasks"]=[{"task_id":"page-task","goal":"Create a Hello World page"}]
            row["expected_scope"]="none" if row["expected"]=="none" else "task"
    return rows


def manifest():
    return {"phase":PHASE,"limits":{"classification":240,"exploration":24,"confirmation":32,"android":6,"finalists":2},
        "families":FAMILIES,"variants":VARIANTS,"agent_rubric":review.IDENTITY,
        "question_templates":{v:{"preference":decisions.preference_questions(v),"bookkeeping":decisions.outcome_questions({"presentation_id":"template","task_id":"template","goal":"Template task"},v)} for v in VARIANTS},
        "fixtures":{split:{"bookkeeping":fixtures(split),"preferences":fixtures(split,True)} for split in ("development","heldout")},
        "confirmation_cases":observation_cases(previous.schedule()),
        "observation_tail":{"max_ms":30000,"quiet_ms":2000,"purpose":"avoid cutting active playback; silence is not completed-turn proof"},
        "measurement_policy":"contextual; agent judgment owns naturalness"}


@contextmanager
def configuration(family, preference="existing", bookkeeping="existing"):
    values={"jarvis_conversation_observer_mode":FAMILIES[family]["mode"],
        "jarvis_decisions_preferences":FAMILIES[family]["preferences"],"jarvis_bookkeeper":FAMILIES[family]["bookkeeper"],
        "jarvis_decisions_preference_variant":preference,"jarvis_decisions_bookkeeping_variant":bookkeeping}
    old={k:getattr(settings,k) for k in values};env={k.upper():os.environ.get(k.upper()) for k in values}
    try:
        for k,v in values.items():setattr(settings,k,v);os.environ[k.upper()]=str(v).lower() if isinstance(v,bool) else v
        yield
    finally:
        for k,v in old.items():setattr(settings,k,v)
        for k,v in env.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v


def consequence(fixture, result):
    finding=result["outcomes"][0];support=observer.validate_finding(fixture["evidence"],finding)
    expected=fixture["expected_deliverable"]
    return {"validation":support,"label_match":finding["classification"] in fixture["expected_classifications"],
        "harmful_false_delivery":support["supported"] and not expected,
        "missed_delivery":expected and not support["supported"],
        "product_match":support["supported"]==expected}


async def calibrate(output, split):
    from app.announcement_eval import runner as r
    path=output/"classification.json"
    saved=json.loads(path.read_text(encoding="utf8")) if path.exists() else {"manifest_hash":review.identity(manifest()),"manifest":manifest(),"requests":[]}
    if saved["manifest_hash"]!=review.identity(manifest()):
        if (split!="heldout" or any(x["split"]=="heldout" for x in saved["requests"])
                or saved["manifest"]["fixtures"]!=manifest()["fixtures"]):raise ValueError("Frozen classification manifest changed")
        saved.setdefault("retained_manifests",[]).append(saved["manifest"])
        saved.update(manifest=manifest(),manifest_hash=review.identity(manifest()))
    if any(x.get("error") for x in saved["requests"]):raise ValueError("Retained provider failure; no paid continuation")
    if any("result" not in x for x in saved["requests"]):raise ValueError("Unfinished request reservation; reconcile without automatic retry")
    if split=="heldout" and len([x for x in saved["requests"] if x["split"]=="development"])!=132:
        raise ValueError("Complete development comparison before held-out testing")
    for classifier in ("responses", *VARIANTS, *["preference_"+v for v in VARIANTS]):
        preference=classifier.startswith("preference_");variant=classifier.removeprefix("preference_")
        for fixture in fixtures(split,preference):
            key=[split,classifier,fixture["id"]]
            if any(x["key"]==key for x in saved["requests"]):continue
            if len(saved["requests"])>=240:raise ValueError("Classification ceiling reached")
            row={"key":key,"split":split,"classifier":classifier,"fixture":fixture["id"],"started":True,"manifest_hash":saved["manifest_hash"]}
            saved["requests"].append(row);r.write(path,saved)
            try:
                if preference:
                    raw,usage=await decisions.request(fixture["input"],decisions.preference_questions(variant))
                    answers={a.get("name"):a.get("choice") for a in raw.get("answers",[]) if a.get("type")=="choice"}
                    label=answers.get("notification_preference");expected=fixture["expected"]
                    no_action={"none","incomplete"}
                    match=label==expected or (label in no_action and expected in no_action)
                    row.update(result=raw,usage=usage,label_match=label==expected,product_match=match,
                        wrong_preference=not match,scope_match=answers.get("preference_scope")==fixture["expected_scope"] if variant=="separate" else None)
                else:
                    result,usage=await (observer.infer(fixture["evidence"]) if classifier=="responses" else decisions.infer(fixture["evidence"],variant))
                    row.update(result=result,usage=usage,**consequence(fixture,result))
            except Exception as exc:
                row["error"]=type(exc).__name__;r.write(path,saved);raise
            r.write(path,saved)
    return saved


def exploration_cases(family):
    cases={c["id"]:c for c in previous.schedule()}
    return observation_cases([cases[k] for k in (("explicit_idle","explicit_speaking") if family=="reference" else
        ("explicit_idle","explicit_speaking","between_sessions"))])


def validate_frozen_variants(rows, family, cohort, variants, source_digest=None):
    relevant=[x for x in rows if x["family"]==family and x["cohort"]==cohort and not x.get("diagnostic_id")]
    if cohort=="android":relevant += [x for x in rows if x["family"]==family and x["cohort"]=="confirmation"]
    if any(x["variants"]!=variants for x in relevant):raise ValueError("Variant change within frozen cohort")
    if cohort in {"confirmation", "android"} and source_digest is not None:
        if any(x.get("source_digest") != source_digest for x in relevant):
            raise ValueError("Production candidate changed within qualification cohort")


def execute(args):
    from app.announcement_eval import runner as r,bookkeeping as b,focused as f
    from app.jarvis_tasks.acceptance import candidate_identity
    output=r.root_path(args.output)
    if args.stage=="report":
        if args.live:raise ValueError("Report replay cannot invoke providers")
        report=json.loads((output/"report.json").read_text(encoding="utf8"));report["report_replay_provider_requests"]=0
        r.write(output/"report.json",report);return
    if args.stage=="offline":
        clone=deepcopy(args);clone.natural_experience=False;clone.natural_updates=True
        previous.execute(clone)
        r.write(output/"experience-manifest.json",manifest());return
    output.mkdir(exist_ok=True,parents=True)
    if not args.live or not args.offline:raise ValueError("Paid work requires --live and frozen --offline")
    offline=r.root_path(args.offline)
    if json.loads((offline/"experience-manifest.json").read_text(encoding="utf8"))!=manifest():raise ValueError("Manifest changed")
    if json.loads((offline/"candidate.json").read_text(encoding="utf8"))!=candidate_identity():raise ValueError("Source changed since offline gate")
    if not all(x["exit_code"]==0 for x in json.loads((offline/"offline.json").read_text(encoding="utf8"))):raise ValueError("Offline gate failed")
    for item in json.loads((offline/"natural-corpus-identity.json").read_text(encoding="utf8")):
        if r.frozen_identity(Path(item["path"]))!=item:raise ValueError("Frozen audio changed")
    path=output/"report.json"
    report=json.loads(path.read_text(encoding="utf8")) if path.exists() else {"phase":PHASE,"candidate":candidate_identity(),"runs":[],"default_changed":False}
    if report["candidate"]!=candidate_identity():
        repair=offline/"harness-repair.json"
        implementation_repair=offline/"implementation-repair.json"
        if implementation_repair.exists():
            proof=json.loads(implementation_repair.read_text(encoding="utf8"))
            validate_implementation_repair(proof,report["candidate"],candidate_identity())
            report.setdefault("implementation_repairs",[]).append(proof)
            report.setdefault("retained_candidates",[]).append(report["candidate"])
        elif repair.exists():
            proof=json.loads(repair.read_text(encoding="utf8"));validate_harness_repair(proof,report["candidate"],candidate_identity())
            for row in report["runs"]:row.setdefault("source_digest",report["candidate"]["source_digest"])
            report.setdefault("harness_repairs",[]).append(proof)
            report.setdefault("retained_candidates",[]).append(report["candidate"])
        else:
            if not (args.calibrate and args.fixture_set=="heldout" and not report["runs"]):raise ValueError("Frozen phase source changed")
            cp=output/"classification.json"
            if not cp.exists() or any(x["split"]=="heldout" for x in json.loads(cp.read_text(encoding="utf8"))["requests"]):
                raise ValueError("Held-out candidate already frozen")
            report.setdefault("retained_development_candidates",[]).append(report["candidate"])
        report["candidate"]=candidate_identity()
    ledger=r.RequestLedger(output,json.loads((output/"request-ledger.json").read_text(encoding="utf8")) if (output/"request-ledger.json").exists() else [])
    try:
        if report.get("paid_stop"):raise ValueError("Retained provider/evidence stop")
        if args.calibrate:
            asyncio.run(calibrate(output,args.fixture_set));return
        classification=json.loads((output/"classification.json").read_text(encoding="utf8"))
        if len(classification["requests"])!=228 or any(x.get("error") or "result" not in x for x in classification["requests"]):
            raise ValueError("Complete bounded classification comparison before voice")
        selection=json.loads((output/"selection.json").read_text(encoding="utf8"))
        if selection.get("source_digest")!=report["candidate"]["source_digest"]:raise ValueError("Selection source mismatch")
        family=args.family
        if family not in FAMILIES:raise ValueError("Select --family")
        if family in selection.get("stopped_families",{}):raise ValueError("Retained material regression; unchanged family stopped")
        for row in report["runs"]:
            review_path=Path(row["directory"])/"natural-agent-review.json"
            if not review_path.exists():raise ValueError("Recorded case requires agent review")
            row["review"]=json.loads(review_path.read_text(encoding="utf8"));review.validate_review(row["review"])
            if (row["review"].get("blocks_unchanged_candidate") and row["family"]==family
                    and row.get("source_digest")==report["candidate"]["source_digest"]):
                raise ValueError("Independently reviewed material regression stops unchanged candidate")
        variants=selection["variants"].get(family,{"preference":"existing","bookkeeping":"existing"})
        cohort="confirmation" if args.confirm else "exploration"
        cases=observation_cases(previous.schedule()) if args.confirm else exploration_cases(family)
        diagnostic=None
        if getattr(args,"diagnostic",None):
            if args.confirm or args.stage=="acceptance":raise ValueError("Diagnostics belong to exploration")
            diagnostic=next((x for x in selection.get("diagnostics",[]) if x["id"]==args.diagnostic and x["family"]==family),None)
            if not diagnostic or not diagnostic.get("hypothesis") or not diagnostic.get("interpretation"):raise ValueError("Freeze diagnostic hypothesis and interpretation")
            variants=diagnostic.get("variants",variants)
            cases=observation_cases([next(c for c in previous.schedule() if c["id"]==diagnostic["case_id"])])
        validate_authority_stop(selection,family,variants)
        if args.confirm and family not in selection.get("finalists",[]):raise ValueError("Freeze at most two reviewed finalists")
        if len(selection.get("finalists",[]))>2:raise ValueError("At most two finalists")
        if args.stage=="acceptance":
            if family!=selection.get("winner"):raise ValueError("Android requires browser winner")
            if not review.validate_review(selection["adoption_review"],adoption=True):raise ValueError("Browser adoption review does not recommend qualification")
            own=[x for x in report["runs"] if x["family"]==family and x["cohort"]=="confirmation"]
            if len(own)!=len(previous.schedule()):raise ValueError("Full browser confirmation required")
            cases=observation_cases(b.android_schedule());cohort="android"
        done={x["case_id"] for x in report["runs"] if x["family"]==family and x["cohort"]==cohort and x.get("diagnostic_id")==args.diagnostic}
        if not diagnostic:validate_frozen_variants(report["runs"],family,cohort,variants,report["candidate"]["source_digest"])
        case=next((c for c in cases if c["id"] not in done),None)
        if case is None:return
        connections=2 if case["id"] in {"between_sessions","interruption_reconnect"} else 1
        def connection_count(row):
            events=Path(row["directory"])/"events.jsonl"
            actual=sum(json.loads(line).get("event",{}).get("type")=="provider.connect.request" for line in events.read_text(encoding="utf8").splitlines()) if events.exists() else 0
            return max(row["connections"],actual)
        count=sum(connection_count(x) for x in report["runs"] if x["cohort"]==cohort)
        if count+connections>manifest()["limits"][cohort]:raise ValueError("Phase connection ceiling reached")
        run_cohort=cohort+"-"+family+("-"+review.identity(diagnostic)[:12] if diagnostic else "")
        folder=output/f"{run_cohort}-production_A-{case['id']}-1-immediate-{'android' if cohort=='android' else 'browser'}"
        row={"family":family,"cohort":cohort,"case_id":case["id"],"directory":str(folder),"connections":connections,"variants":variants,"diagnostic_id":args.diagnostic,"source_digest":report["candidate"]["source_digest"]}
        report["runs"].append(row);r.write(path,report)
        with configuration(family,**variants):
            wake=Path(json.loads((offline/"adoption-corpus-identity.json").read_text(encoding="utf8"))["wake"]["path"])
            r.run_case(output,case,"production_A","immediate",run_cohort,1,offline/"natural-corpus",wake,android=cohort=="android")
        evidence=json.loads((folder/"evidence.json").read_text(encoding="utf8"))
        drain=folder/"observer-drain.json"
        if drain.exists():
            processing=json.loads(drain.read_text(encoding="utf8"))
            if processing.get("timed_out") or processing.get("error"):
                raise r.PaidFailure("Observer processing did not settle before owned backend cleanup")
        if evidence.get("paid_failure") or r.provider_failure(evidence):raise r.PaidFailure("Provider failure; stop paid work")
        if evidence.get("capture_error") or (not evidence.get("error") and evidence.get("recording_complete") is not True):raise r.PaidFailure("Recording integrity failed")
        if any(j["status"]=="failed" for j in b.observer_records(folder)):raise r.PaidFailure("Observer provider/evidence failure")
        row["measurement_assessment"]=f.evaluate_case(folder,folder/"assessment-v3",ledger,live=True,grade_live=False,transcription_limit=62)
        report["awaiting_agent_review"]=str(folder)
    except Exception as exc:
        if isinstance(exc,r.PaidFailure) or (args.calibrate and (output/"classification.json").exists() and
            any(x.get("error") for x in json.loads((output/"classification.json").read_text(encoding="utf8"))["requests"])):
            report["paid_stop"]=type(exc).__name__
        report["stopped_reason"]=str(exc);raise
    finally:
        report["usage"]=r.usage_summary(output,ledger)
        prior=r.BASE/"pilot-20261017-natural-02"/"report.json"
        report["cumulative_usage"]=r.merge_usage(deepcopy(report["usage"]),json.loads(prior.read_text(encoding="utf8"))["cumulative_usage"])
        cp=output/"classification.json"
        report["classification_usage"]={"request_count":0,"usage":[]}
        if cp.exists():
            requests=json.loads(cp.read_text(encoding="utf8"))["requests"]
            report["classification_usage"]={"request_count":len(requests),"usage":[x.get("usage") for x in requests],"complete":sum("result" in x for x in requests),"errors":sum("error" in x for x in requests)}
        report["runtime_observer_usage"]=[j.get("usage_json") for row in report["runs"] for j in b.observer_records(Path(row["directory"]))]
        report["native_decisions_events"]=[]
        for row in report["runs"]:
            ep=Path(row["directory"])/"events.jsonl"
            if ep.exists():report["native_decisions_events"].extend(json.loads(line) for line in ep.read_text(encoding="utf8").splitlines() if json.loads(line).get("event",{}).get("type")=="decision.event")
        report["prior_classifier_accounting"]=str(prior.parent/"final-usage-summary.json")
        report["monetary_cost"]=None;r.write(path,report)
