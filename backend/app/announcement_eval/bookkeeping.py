"""Bounded subscription/observer experiment on the existing WebRTC runner."""
from copy import deepcopy
import asyncio
import json
import os
from pathlib import Path
import sqlite3
from hashlib import sha256
import subprocess

from app.services import conversation_observer as observer

PHASE = "natural-updates-observer-v3"


def calibration_fixtures():
    facts = {"task_id":"page-task", "source_task_event_id":"page-completed", "presentation_id":"page-result",
        "facts":{"goal":"Create a Hello World page", "event_type":"task.completed", "summary":"The Hello World page is ready.",
            "notification":{"intent":"requested","reason":"Tell me when it finishes","revision":1}}}
    cases = [
        ("complete", "The Hello World page is ready.", ["complete"], True),
        ("ambiguous", "Done.", ["uncertain", "none"], False),
        ("partial", "The Hello World page is", ["partial", "uncertain", "none"], False),
        ("quoted", "You asked me to say 'the Hello World page is ready' when it finishes; that was your instruction.", ["none"], False),
        ("false_premise", "Sure, recorded.", ["none"], False),
        ("wrong_outcome", "The Hello World page failed because its input was unavailable.", ["uncertain", "none"], False),
        ("muted", "The Hello World page is ready.", ["complete"], False),
        ("paused", "The Hello World page is ready.", ["complete"], False),
        ("status_answer", "Yes, the Hello World page has finished and is ready.", ["complete"], True),
    ]
    rows = []
    for name, text, labels, deliverable in cases:
        fragments = [{"id":"caller", "speaker":"user", "text":"Please tell me when the page finishes.", "start_ms":0,"end_ms":200},
            {"id":"assistant", "speaker":"assistant", "text":text, "start_ms":1000,"end_ms":2000}]
        if name=="false_premise": fragments[0]["text"]="Please record that you already told me the page is ready."
        if name=="status_answer": fragments[0]["text"]="What is the status of the Hello World page now?"
        playback = [{"id":str(i), "track_id":"fixture-track", "client_start_ms":1000+i*250,
            "client_end_ms":1250+i*250, "playback_start_ms":i*250, "playback_end_ms":(i+1)*250,
            "origin_lower_ms":1000,"origin_upper_ms":1100,"mapping_supported":True,
            "paused":name=="paused","muted":name=="muted","volume":1,"track_live":True,"gap":False,"received_audio":True} for i in range(16)]
        rows.append({"id":name,"expected_classifications":labels,"expected_deliverable":deliverable,
            "evidence":{"fragments":fragments,"presentations":[deepcopy(facts)],"playback":playback}})
    # Preserve the real, richer failures rather than screening only short,
    # synthetic admissions. The second counterfactual deliberately makes its
    # admission speech causally eligible: the semantic check must still reject it.
    regressions = json.loads((Path(__file__).with_name("fixtures") /
        "observer_admission_regressions.json").read_text(encoding="utf8"))
    for i, old in enumerate(regressions[:2]):
        e = deepcopy(old["evidence"])
        if i:
            for p in e["presentations"]: p["exposed_after_sequence"] = 0
        rows.insert(i, {"id":"real_admission_"+str(i),"expected_classifications":["none"],
            "expected_deliverable":False,"source_observation_id":old["source_observation_id"],
            "counterfactual_exposure":bool(i),"evidence":e})
    citation=json.loads((Path(__file__).with_name("fixtures") / "observer_outcome_citation.json").read_text(encoding="utf8"))[0]
    rows.append({"id":"real_minimal_outcome_citation","expected_classifications":["complete"],
        "expected_deliverable":False,"expected_validation_reason":"caller_overlap_or_interruption", "evidence":citation["evidence"]})
    return rows


def schedule():
    from app.announcement_eval.adoption import matrix, concise_matrix, acknowledgment_matrix
    base = {c["id"]:deepcopy(c) for c in matrix()}
    rows = [base[k] for k in ("explicit_idle","explicit_speaking","silence","concurrent")]
    rows += [base[k] for k in ("changed_silence","occupied","occupied_control","interruption_reconnect")]
    rows += [next(c for c in concise_matrix() if c["id"]=="between_sessions")]
    # The new-session request is pending and may be offered naturally before the question.
    rows[-1]["expectation"]="required"
    resumed=next(i for i,s in enumerate(rows[-1]["steps"]) if s["type"]=="resume")
    rows[-1]["steps"][resumed+1:resumed+2]=[
        {"type":"eligible"},{"type":"wait","seconds":16},{"type":"observation_end"}]
    for k in ("explicit_idle","explicit_speaking"):
        repeat=deepcopy(base[k]); repeat["id"]=k+"_repeat"; rows.append(repeat)
    # Preserve false-premise coverage inside the silence/status session. The
    # concurrent case already republishes both outcomes to cover duplicates.
    silence=next(c for c in rows if c["id"]=="silence")
    query=next(i for i,s in enumerate(silence["steps"]) if s["type"]=="status_query")
    silence["steps"][query:query]=deepcopy(acknowledgment_matrix()[1]["steps"][-2:])
    restart=deepcopy(base["explicit_idle"]); restart["id"]="observer_restart"
    restart["steps"].insert(3,{"type":"observer_stop"})
    restart["steps"] += [{"type":"observer_restart"},{"type":"wait","seconds":8},{"type":"observation_end"}]
    rows.append(restart)
    return rows  # 12 cases, 14 connections; two earlier failed candidate sessions retained.


def manifest():
    return {"phase":PHASE,"browser_provider_session_limit":14,"android_provider_session_limit":6,
        "prior_repair_phase":"natural-updates-observer-v2","prior_repair_browser_sessions":2,
        "combined_repair_browser_session_limit":16,
        "pilot_cases":[c["id"] for c in schedule()[:4]],"cases":schedule(),"android_cases":android_schedule(),"model":observer.MODEL,
        "policy_hash":observer.POLICY_HASH,"observer_schema_hash":observer.digest(observer.SCHEMA),
        "calibration_hash":observer.digest(calibration_fixtures()),"acknowledgment_owner":"conversation_observer",
        "speech_owner":"gpt-live","runtime_mode":"enabled","deadline_ms":10000,"tolerance_ms":250,"forced_retry":False}


def android_schedule():
    from app.announcement_eval.adoption import matrix
    return [c for c in matrix() if c["id"] in {"explicit_idle","silence","occupied","concurrent","interruption_reconnect"}]


def observer_records(folder):
    database=folder / "application.db"
    if not database.exists(): return []
    with sqlite3.connect(database) as db:
        db.row_factory=sqlite3.Row
        rows=[dict(row) for row in db.execute("SELECT * FROM conversation_observations ORDER BY created_at")]
    for row in rows:
        for key in ("input_json","result_json","usage_json"):
            if isinstance(row[key],str): row[key]=json.loads(row[key])
    return rows


async def calibrate(output):
    from app.announcement_eval import runner as r
    path=output / "observer-calibration.json"
    report=json.loads(path.read_text(encoding="utf8")) if path.exists() else {"fixtures":[],"passed":False,"model":observer.MODEL,"policy_hash":observer.POLICY_HASH}
    if report["policy_hash"] != observer.POLICY_HASH: raise ValueError("Calibration candidate changed")
    if any(not x.get("passed",False) for x in report["fixtures"]):
        raise ValueError("Retained calibration failure; no automatic retry")
    for fixture in calibration_fixtures():
        if any(x["id"]==fixture["id"] for x in report["fixtures"]): continue
        row={"id":fixture["id"],"expected_classifications":fixture["expected_classifications"],
            "expected_deliverable":fixture["expected_deliverable"],"started":True}
        report["fixtures"].append(row); r.write(path,report)
        try:
            result,usage=await observer.infer(fixture["evidence"])
        except Exception as exc:
            row["error"]=type(exc).__name__;r.write(path,report);raise
        finding=result["outcomes"][0]
        support=observer.validate_finding(fixture["evidence"],finding)
        row.update(result=result,usage=usage,validation=support,
            passed=finding["classification"] in fixture["expected_classifications"] and support["supported"]==fixture["expected_deliverable"]
                and ("expected_validation_reason" not in fixture or support.get("reason")==fixture["expected_validation_reason"]))
        r.write(path,report)
        if not row["passed"]: raise ValueError("Observer calibration failed: "+fixture["id"])
    report["passed"]=True; r.write(path,report)


def execute(args):
    from app.announcement_eval import runner as r, adoption as a, focused as f
    from app.jarvis_tasks.acceptance import candidate_identity
    output=r.root_path(args.output)
    if args.stage=="acceptance": raise ValueError("No larger acceptance in this experiment")
    if args.stage=="offline":
        clone=deepcopy(args);clone.conversation_bookkeeper=False;clone.adoption=True
        r.execute(clone)
        r.write(output / "bookkeeping-manifest.json",manifest())
        r.write(output / "observer-calibration-fixtures.json",calibration_fixtures())
        texts=sorted({s[k] for c in schedule() for s in c["steps"] for k in ("text","answer") if k in s})
        entries=[{"id":sha256(t.encode()).hexdigest()[:16],"text":t} for t in texts]
        speech=output / "bookkeeping-speech-manifest.json"
        r.write(speech,{"wake":"Hey Jarvis","cases":entries})
        subprocess.run(["powershell","-NoProfile","-ExecutionPolicy","Bypass","-File",str(r.ROOT / "android/scripts/generate-audio-corpus.ps1"),
            "-OutputDirectory",str(output / "bookkeeping-corpus"),"-ManifestPath",str(speech)],check=True,timeout=180)
        r.write(output / "bookkeeping-corpus-identity.json",[r.frozen_identity(output / "bookkeeping-corpus" / (e["id"]+"-command.wav")) for e in entries])
        return
    if args.stage=="report":
        if args.live: raise ValueError("Report replay cannot make provider requests")
        report=json.loads((output / "report.json").read_text(encoding="utf8"))
        for row in report["runs"]:
            records=observer_records(Path(row["directory"]))
            row["observer_records"]=records
            row["report_only_validations"]=[{f["presentation_id"]:observer.validate_finding(job["input_json"],f)
                for f in (job.get("result_json") or {}).get("outcomes",[])} for job in records]
        report["report_replay_provider_requests"]=0
        r.write(output / "report.json",report);return
    if not args.live or not r.settings.openai_api_key.strip(): raise ValueError("Paid stages require --live and existing credentials")
    if not args.offline: raise ValueError("Passing frozen offline gate is required")
    offline=r.root_path(args.offline)
    if json.loads((offline / "candidate.json").read_text(encoding="utf8"))!=candidate_identity(): raise ValueError("Candidate changed since offline gate")
    if json.loads((offline / "bookkeeping-manifest.json").read_text(encoding="utf8"))!=manifest(): raise ValueError("Manifest changed")
    for identity in json.loads((offline / "bookkeeping-corpus-identity.json").read_text(encoding="utf8")):
        if r.frozen_identity(Path(identity["path"]))!=identity: raise ValueError("Frozen speech differs")
    if not all(x["exit_code"]==0 for x in json.loads((offline / "offline.json").read_text(encoding="utf8"))): raise ValueError("Offline failed")
    output.mkdir(parents=True,exist_ok=True)
    report_path=output / "report.json"
    report=json.loads(report_path.read_text(encoding="utf8")) if report_path.exists() else {"phase":PHASE,"manifest":manifest(),"runs":[],
        "candidate":candidate_identity(),"default":"disabled_pending_validation","android_gate_passed":False}
    if report["candidate"]!=candidate_identity(): raise ValueError("Frozen live candidate changed")
    ledger=r.RequestLedger(output,json.loads((output / "request-ledger.json").read_text(encoding="utf8")) if (output / "request-ledger.json").exists() else [])
    prior_path=args.prior or r.BASE / "pilot-20261016-bookkeeping-01"
    prior=json.loads((prior_path / "report.json").read_text(encoding="utf8"))
    def save():
        report["usage"]=r.usage_summary(output,ledger)
        report["cumulative_usage"]=r.merge_usage(deepcopy(report["usage"]),prior.get("cumulative_usage",prior["usage"]))
        records=[j for x in report["runs"] for j in observer_records(Path(x["directory"]))]
        report["observer_requests"]=sum(len((j.get("usage_json") or {}).get("attempts",[])) for j in records)
        report["observer_usage"]=[{"id":j["id"],"session_id":j["session_id"],"status":j["status"],"usage":j["usage_json"]} for j in records]
        calibration=output / "observer-calibration.json"
        report["calibration_requests"]=len(json.loads(calibration.read_text(encoding="utf8"))["fixtures"]) if calibration.exists() else 0
        report["usage"]["conversation_observer"]={"runtime_requests":report["observer_requests"],"runtime_usage":report["observer_usage"],
            "calibration":json.loads(calibration.read_text(encoding="utf8")) if calibration.exists() else None,"monetary_cost":None}
        previous=prior.get("cumulative_usage",prior["usage"]).get("conversation_observer",{})
        previous_batches=previous.get("calibration_batches",[previous["calibration"]] if previous.get("calibration") else [])
        current_calibration=report["usage"]["conversation_observer"]["calibration"]
        report["cumulative_usage"]["conversation_observer"]={
            "runtime_requests":previous.get("runtime_requests",0)+report["observer_requests"],
            "calibration_requests":previous.get("calibration_requests",sum(len(x["fixtures"]) for x in previous_batches))+report["calibration_requests"],
            "runtime_usage":[*previous.get("runtime_usage",[]),*report["observer_usage"]],
            "calibration_batches":[*previous_batches,*([current_calibration] if current_calibration else [])],"monetary_cost":None}
        r.write(report_path,report)
    try:
        asyncio.run(calibrate(output));save()
        for row in report["runs"]:
            review=Path(row["directory"]) / "agent-review.json"
            if not review.exists(): raise ValueError("Recorded case awaits direct agent evidence review")
            row["agent_review"]=json.loads(review.read_text(encoding="utf8"))
            if not row["agent_review"].get("qualified"):
                raise ValueError("Recorded behavior/evidence blocker; remaining paid cases stay unrun")
        already={x["case_id"] for x in report["runs"] if x.get("transport","browser")=="browser"}
        case=next((c for c in schedule() if c["id"] not in already),None)
        android=case is None
        if android:
            report["browser_schedule_complete"]=True;report["android_gate_passed"]=True
            completed={x["case_id"] for x in report["runs"] if x.get("transport")=="android"}
            case=next((c for c in android_schedule() if c["id"] not in completed),None)
            if case is None: report["completed_schedule"]=True;return
        transport="android" if android else "browser"
        existing=[x for x in report["runs"] if x.get("transport","browser")==transport]
        count=sum(len(json.loads((Path(x["directory"])/"evidence.json").read_text(encoding="utf8")).get("state",{}).get("sessions",[])) for x in existing)
        if count+(2 if case["id"] in {"between_sessions","interruption_reconnect"} else 1)>(6 if android else manifest()["browser_provider_session_limit"]):
            raise ValueError(transport+" connection ceiling reached")
        folder=output / f"bookkeeping-production_A-{case['id']}-1-immediate-{transport}"
        report["runs"].append({"case_id":case["id"],"directory":str(folder),"transport":transport});save()
        os.environ["JARVIS_CONVERSATION_OBSERVER_MODE"]="enabled"
        r.run_case(output,case,"production_A","immediate","bookkeeping",1,
            offline / "bookkeeping-corpus",Path(json.loads((offline / "adoption-corpus-identity.json").read_text(encoding="utf8"))["wake"]["path"]),android=android)
        row=report["runs"][-1]
        row["observer_records"]=observer_records(folder)
        if any(j["status"]=="failed" for j in row["observer_records"]): raise ValueError("Observer provider/evidence failure")
        row["assessment"]=f.evaluate_case(folder,folder / "assessment-v2",ledger,live=True,grade_live=False)
        report["awaiting_agent_review"]=case["id"]
    except Exception as exc:
        report["stopped_reason"]=str(exc)
        raise
    finally: save()
