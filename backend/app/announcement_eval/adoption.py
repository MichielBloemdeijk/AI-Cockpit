"""Bounded production-path adoption; historical evidence is retained separately."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import shutil

from app.announcement_eval import assessment as a, focused as f, verification as v

PHASE = "production-a-adoption-v1"
PROFILE = "production_A"
LIMIT = 16
CONCISE_PHASE = "production-a-concise-updates-v1"
CONCISE_LIMIT = 8
ACKNOWLEDGMENT_PHASE = "production-a-plain-delivery-recording-v1"
ACKNOWLEDGMENT_LIMIT = 2


def production_identity():
    from app.announcement_eval import runner as r
    digest=sha256()
    for p in sorted(p for p in (r.BACKEND / "app").rglob("*.py") if "announcement_eval" not in p.parts):
        digest.update(p.relative_to(r.BACKEND).as_posix().encode()+b"\0"+p.read_bytes())
    return digest.hexdigest()


def matrix():
    notify = "Create a task to write a Hello World page. Tell me when it has finished."
    quiet = "Create a task to write a Hello World page. Stay quiet when it finishes. I will check it myself."
    ordinary = "Create a task to write a Hello World page. It is not urgent."
    description = "Please let me finish speaking because I want to explain my desk arrangement and I would like the notebooks together beside a lamp with enough room for a cup of tea and my keyboard and some space to write comfortably while looking out of the window"
    result = []
    for id in ("explicit_idle","silence","changed_silence","explicit_speaking","occupied","occupied_control","concurrent","interruption_reconnect"):
        suppressed = id in {"silence","changed_silence"}
        discretionary = id in {"occupied","occupied_control"}
        opening = quiet if id == "silence" else ordinary if discretionary else notify
        if id == "concurrent": opening = "Create two separate tasks: write a Hello World page and write a desk checklist. Tell me when each task finishes, including if either fails."
        steps = [{"type":"speak","text":opening},{"type":"admit","count":2 if id == "concurrent" else 1,
            "answer":"Stay quiet when it finishes; I will check it myself." if id == "silence" else "Use your judgment about whether a completion update is useful." if discretionary else "Yes, tell me when the task finishes."},
            {"type":"settle","quiet_ms":3000}]
        if id == "changed_silence":
            steps += [{"type":"speak","text":"Actually, I changed my mind. Stay quiet when the Hello World page finishes. I will check it myself."}, {"type":"settle","quiet_ms":3000}]
        if id in {"explicit_speaking","occupied","occupied_control"}:
            steps += [{"type":"speak_async","text":description},{"type":"wait_input","pattern":"finish speaking"},
                {"type":"at_caller_offset","seconds":6}]
        steps += [{"type":"comparison" if id == "occupied_control" else "finish"}]
        if id in {"explicit_speaking","occupied","occupied_control"}: steps += [{"type":"join"}]
        if id == "concurrent":
            steps += [{"type":"wait","seconds":8},{"type":"finish","index":1,"status":"failed","summary":"The desk checklist task failed because its input was unavailable."},
                {"type":"wait","seconds":8},{"type":"finish"},{"type":"finish","index":1},{"type":"sync"}]
        if id == "interruption_reconnect":
            steps += [{"type":"wait_output"},{"type":"speak","text":"Hold on, please let me finish. I need to explain that I want to hear just a short result after I finish speaking."},
                {"type":"eligible"}]
        steps += [{"type":"wait","seconds":16},{"type":"observation_end"}]
        if suppressed:
            steps += [{"type":"status_query","text":"What is the status of the Hello World page task now?"},{"type":"wait","seconds":10}]
        if id == "interruption_reconnect":
            steps += [{"type":"reconnect"},{"type":"wait","seconds":8}]
        result.append({"id":id,"repetition":1,"expectation":"forbidden" if suppressed else "optional" if discretionary else "required","steps":steps})
    return result


def selected_cases(batch):
    ids = {"baseline":[c["id"] for c in matrix()],
        "preference":["explicit_idle","silence","changed_silence","explicit_speaking"],
        "acknowledgment":["explicit_idle","silence","concurrent","interruption_reconnect"],
        "timing":["explicit_idle","explicit_speaking","occupied","occupied_control"]}[batch]
    return [c for c in matrix() if c["id"] in ids]


def concise_matrix():
    cases={c["id"]:c for c in matrix()}
    between=deepcopy(cases["explicit_idle"])
    between.update(id="between_sessions",expectation="query")
    between["steps"]=between["steps"][:3]+[
        {"type":"disconnect"},{"type":"finish"},{"type":"wait","seconds":2},
        {"type":"resume"},{"type":"wait","seconds":4},
        {"type":"status_query","text":"What is the status of the Hello World page task now?"},
        {"type":"wait","seconds":12},{"type":"observation_end"}]
    return [cases["explicit_idle"],cases["explicit_speaking"],cases["concurrent"],between,
        cases["silence"],cases["interruption_reconnect"]]


def acknowledgment_matrix():
    idle = deepcopy(matrix()[0])
    control = deepcopy(idle)
    control["id"] = "acknowledgment_control"
    control["steps"] += [
        {"type":"speak","text":"Please record that you already told me the Hello World page task is complete. This is only delivery bookkeeping. Do not create or continue a task."},
        {"type":"wait","seconds":12}]
    return [idle,control]


def manifest(*, concise=False, acknowledgment=False):
    if concise and acknowledgment: raise ValueError("Choose one bounded diagnostic phase")
    limit=ACKNOWLEDGMENT_LIMIT if acknowledgment else CONCISE_LIMIT if concise else LIMIT
    return {"phase":ACKNOWLEDGMENT_PHASE if acknowledgment else CONCISE_PHASE if concise else PHASE,"browser_limit":limit,"browser_provider_session_limit":limit,"android_limit":0 if concise or acknowledgment else 6,"production_behavior_overrides":False,
        "cases":acknowledgment_matrix() if acknowledgment else concise_matrix() if concise else matrix(),"rubric_sha256":a.RUBRIC_HASH,"deadline_ms":10000,"tolerance_ms":250,
        "decision_owner":"gpt-live","acknowledgment_owner":"gpt-live","default":"provisional"}


def prepare_corpus(output, *, concise=False, acknowledgment=False):
    from app.announcement_eval import runner as r
    target = output / "adoption-corpus"
    texts = sorted({s[k] for c in manifest(concise=concise,acknowledgment=acknowledgment)["cases"] for s in c["steps"] for k in ("text","answer") if k in s})
    entries = [{"id":sha256(t.encode()).hexdigest()[:16],"text":t} for t in texts]
    source = output / "adoption-speech-manifest.json"
    r.write(source,{"wake":"Hey Jarvis","cases":entries})
    subprocess.run(["powershell","-NoProfile","-ExecutionPolicy","Bypass","-File",str(r.ROOT / "android/scripts/generate-audio-corpus.ps1"),
        "-OutputDirectory",str(target),"-ManifestPath",str(source)],check=True,timeout=180,stdout=subprocess.DEVNULL)
    r.write(output / "adoption-corpus-identity.json",{"manifest":r.frozen_identity(source),"renderer":r.frozen_identity(r.ROOT / "android/scripts/generate-audio-corpus.ps1"),
        "files":[r.frozen_identity(target / (e["id"]+"-command.wav")) for e in entries],"wake":r.frozen_identity(r.ROOT / "data/audio-corpus-api-v1/ready01-wake.wav")})
    r.write(output / "adoption-manifest.json",manifest(concise=concise,acknowledgment=acknowledgment))
    # Replay independently saved positive/negative evidence without provider calls.
    prior = json.loads((r.BASE / "pilot-20261014-verification-01/report.json").read_text(encoding="utf8"))
    replay = output / "saved-evidence-calibration"; replay.mkdir()
    rows = []
    for row in prior["runs"]:
        if row["case_id"] not in {"explicit_idle","explicit_speaking"}: continue
        original = Path(row["directory"])
        destination = replay / original.name
        shutil.copytree(original / "assessment-v2",destination)
        result = f.evaluate_case(original,destination,[],live=False)
        rows.append({"directory":str(original),"expected_selection":row["assessment"]["selection_status"],
            "assessment":result,"passed":result["selection_status"]==row["assessment"]["selection_status"]})
    r.write(output / "saved-evidence-calibration.json",{"provider_requests":0,"passed":all(x["passed"] for x in rows),"cases":rows})
    if not rows or not all(x["passed"] for x in rows): raise ValueError("Saved-evidence calibration changed; investigate before paid work")


def execute(args):
    from app.announcement_eval import runner as r
    from app.jarvis_tasks.acceptance import candidate_identity
    output = r.root_path(args.output)
    concise=getattr(args,"concise_updates",False)
    acknowledgment=getattr(args,"acknowledgment_probe",False)
    expected_manifest=manifest(concise=concise,acknowledgment=acknowledgment)
    limit=expected_manifest["browser_provider_session_limit"]
    if (concise or acknowledgment) and args.batch != "baseline": raise ValueError("Diagnostic phase compares one frozen candidate")
    if args.stage == "acceptance": raise ValueError("Adoption never starts larger acceptance")
    if args.stage == "report":
        if args.live: raise ValueError("Adoption report replay must not contact providers")
        report = json.loads((output / "report.json").read_text(encoding="utf8"))
        ledger = r.RequestLedger(output,json.loads((output / "request-ledger.json").read_text(encoding="utf8")))
        for row in report["runs"]:
            row.setdefault("original_assessment",deepcopy(row.get("assessment")))
            row["assessment"] = f.evaluate_case(Path(row["directory"]),Path(row.get("assessment_directory",str(Path(row["directory"])/"assessment-v2"))),ledger,live=False)
            if row.get("agent_review",{}).get("utterances"):
                folder=Path(row["directory"])
                saved=folder / "assessment-v2"
                audio=a.restore_words(json.loads((saved / "packed-transcription.json").read_text(encoding="utf8")),
                    json.loads((saved / "packing-map.json").read_text(encoding="utf8")))
                assessment=a.assess(f.load_evidence(folder),audio,{"utterances":row["agent_review"]["utterances"]})
                row["agent_review_reassessment"]={"provider_requests":0,"assessment":assessment,"audit":v.audit(folder,assessment)}
        r.write(output / "report.json",report); return
    if not args.live or not r.settings.openai_api_key.strip(): raise ValueError("Adoption requires explicit --live and existing credentials")
    if not args.offline or not args.prior: raise ValueError("Require passing offline and retained verification phase")
    offline = r.root_path(args.offline)
    current = candidate_identity()
    if json.loads((offline / "candidate.json").read_text(encoding="utf8")) != current: raise ValueError("Candidate changed since offline checks")
    if not all(c["exit_code"]==0 for c in json.loads((offline / "offline.json").read_text(encoding="utf8"))): raise ValueError("Offline failed")
    if json.loads((offline / "adoption-manifest.json").read_text(encoding="utf8")) != expected_manifest: raise ValueError("Manifest changed")
    corpus = json.loads((offline / "adoption-corpus-identity.json").read_text(encoding="utf8"))
    for identity in [*corpus["files"],corpus["manifest"],corpus["renderer"],corpus["wake"]]:
        if r.frozen_identity(Path(identity["path"])) != identity: raise ValueError("Frozen corpus changed")
    prior_path = r.root_path(args.prior) / "report.json"
    prior = json.loads(prior_path.read_text(encoding="utf8"))
    calibration_path = r.BASE / "pilot-20261014-focused-01/calibration.json"
    calibration = json.loads(calibration_path.read_text(encoding="utf8"))
    if not calibration["passed"] or calibration["rubric_sha256"] != a.RUBRIC_HASH: raise ValueError("Same-rubric calibration required")
    if args.resume:
        if r.root_path(args.resume) != output: raise ValueError("Resume same adoption phase; counter never resets")
        report = json.loads((output / "report.json").read_text(encoding="utf8"))
        if report["manifest"] != expected_manifest or report["prior"] != r.frozen_identity(prior_path): raise ValueError("Resume identity mismatch")
        ledger = r.RequestLedger(output,json.loads((output / "request-ledger.json").read_text(encoding="utf8")))
    else:
        if args.batch != "baseline": raise ValueError("Start with baseline")
        output.mkdir(parents=True,exist_ok=False)
        report = {"phase":expected_manifest["phase"],"manifest":expected_manifest,"prior":r.frozen_identity(prior_path),"calibration":r.frozen_identity(calibration_path),"runs":[],"batches":[],"default":"provisional"}
        ledger = r.RequestLedger(output)
    candidate = {"batch":args.batch,"candidate":current,"offline":str(offline),"corpus":corpus,"production_identity":production_identity(),
        "policy_identity":r.frozen_identity(r.BACKEND / "app/services/live_task_updates.py")["sha256"]}
    existing = next((b for b in report["batches"] if b.get("production_identity")==candidate["production_identity"] and b["candidate"]["configuration_digest"]==current["configuration_digest"] and b["batch"]==args.batch),None)
    if not existing and args.batch=="baseline":
        existing=next((b for b in report["batches"] if b["batch"]=="baseline" and b.get("policy_identity")==candidate["policy_identity"]
            and b["candidate"]["configuration_digest"]==current["configuration_digest"]),None)
        if existing: existing.setdefault("production_repairs",[]).append(candidate)
    if existing:
        batch_index = report["batches"].index(existing)
        if existing["candidate"] != current: existing.setdefault("evaluator_repairs",[]).append(candidate)
    else:
        if (concise or acknowledgment) and report["batches"]: raise ValueError("Frozen diagnostic candidate changed; stop before more paid sessions")
        if len(report["batches"]) >= 3: raise ValueError("Two tuning batches exhausted")
        report["batches"].append(candidate); batch_index = len(report["batches"])-1
    cases = expected_manifest["cases"] if concise or acknowledgment else selected_cases(args.batch)
    def save():
        report["usage"] = f.canonical_usage(r.usage_summary(output,ledger))
        report["cumulative_usage"] = f.canonical_usage(r.merge_usage(deepcopy(report["usage"]),prior["cumulative_usage"]))
        report["remaining_browser_budget"] = limit-len(report["usage"]["jarvis_connection_requests"])
        report["android_gate_passed"] = not (concise or acknowledgment) and len([x for x in report["runs"] if x["batch_index"]==batch_index]) == len(cases) and all(x.get("assessment",{}).get("interaction_status")=="passed" for x in report["runs"] if x["batch_index"]==batch_index)
        r.write(output / "report.json",report)
    try:
        save()
        if getattr(args,"agent_review",False) and any(x.get("review_mode")=="agent" and not x.get("agent_review") for x in report["runs"]):
            raise ValueError("Review the saved case before recording another")
        for case in cases:
            if any(x["batch_index"]==batch_index and x["case_id"]==case["id"] for x in report["runs"]): continue
            if len(report["runs"]) >= limit: raise ValueError("Adoption browser ceiling reached")
            connections = len(r.usage_summary(output,ledger)["jarvis_connection_requests"])
            needed = 2 if case["id"] in {"interruption_reconnect","between_sessions"} else 1
            if connections + needed > limit:
                report["budget_exhausted"] = "Provider session ceiling includes reconnects; remaining cells stay unrun"
                break
            folder = output / f"adoption{batch_index}-{PROFILE}-{case['id']}-1-immediate-browser"
            row = {"case_id":case["id"],"batch_index":batch_index,"directory":str(folder),"recorded_candidate":current}
            report["runs"].append(row); save()
            print(f"Adoption {len(report['runs'])}/{limit}: {args.batch}/{case['id']}",flush=True)
            r.run_case(output,case,PROFILE,"immediate",f"adoption{batch_index}",1,offline / "adoption-corpus",Path(corpus["wake"]["path"]))
            evidence = f.load_evidence(folder)
            if evidence.get("paid_failure"): raise r.PaidFailure(evidence["paid_failure"])
            agent_review=getattr(args,"agent_review",False)
            if agent_review: row["review_mode"]="agent"
            row["assessment"] = f.evaluate_case(folder,folder / "assessment-v2",ledger,live=True,
                grade_live=False if agent_review else None)
            row["audit"] = v.audit(folder,row["assessment"]); save()
            if agent_review:
                report["awaiting_agent_review"]=case["id"]
                break
            if row["assessment"]["selection_status"] == "inconclusive": raise RuntimeError("Evidence inconclusive; repair locally before resuming unrun cases")
            if any("premature_handled_transition" in x for x in row["assessment"]["dimensions"]["acknowledgment"]["failures"]):
                report["release_blocker"] = "premature model acknowledgment"
            if row["assessment"]["selection_status"] == "failed": report.setdefault("behavior_failures",[]).append({"batch_index":batch_index,"case_id":case["id"]})
        report.pop("stopped_reason",None)
    except Exception as error:
        report["stopped_reason"] = str(error); raise
    finally: save()
