"""Four production configurations, with a separate non-resetting adoption budget."""
import asyncio
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess

from app.services import conversation_observer as observer, live_decisions as decisions

PHASE = "natural-updates-four-configurations-v2"
CONFIGURATIONS = {"1": {"preferences":False,"bookkeeper":"responses"},
    "2": {"preferences":True,"bookkeeper":"responses"},
    "3": {"preferences":False,"bookkeeper":"decisions"},
    "4": {"preferences":True,"bookkeeper":"decisions"}}


def fixtures():
    from app.announcement_eval.bookkeeping import calibration_fixtures
    rows=deepcopy(calibration_fixtures())
    # Old labels remain untouched on disk. New rubric accepts contextual wording.
    for row in rows:
        if row["id"]=="ambiguous":
            row["expected_classifications"]=["uncertain"]
            row["label_rationale"]="Done could refer to subscribing or changing page layout; neither unambiguously reports the completed page."
            row["evidence"]["fragments"][0]["text"]="Please tell me when the page finishes. Also turn on compact layout."
    template=deepcopy(next(r for r in rows if r["id"]=="complete"))
    additions=[
        ("contextual_done","It's done; your page is ready.","complete"),
        ("paraphrase","Your Hello World page has finished and you can open it now.","complete"),
        ("partial_name_regression","The Hello World page","partial"),
        ("admission_homophone","Your Hello World task is cute. I'll tell you when it finishes.","none"),
        ("promise","I'll let you know when your page is ready.","none"),
        ("question","Is your Hello World page ready?","none"),
        ("hypothetical","If the page were ready, I'd tell you.","none"),
        ("unfinished","Your Hello World page has","partial"),
        ("contradiction","Your Hello World page failed.","uncertain"),
        ("wrong_task","Your desk checklist is ready.","none"),
        ("natural_status_answer","Yes, that page is finished. It is ready to use.","complete"),
        ("unrelated_overlap","The Hello World page is ready.","complete")]
    for name,text,label in additions:
        row=deepcopy(template);row.update(id=name,expected_classifications=[label],expected_deliverable=label=="complete")
        if name=="partial_name_regression":row["expected_classifications"]=["partial","none","uncertain"]
        row["evidence"]["fragments"][-1]["text"]=text
        if name=="unrelated_overlap":
            row["evidence"]["fragments"].insert(1,{"id":"earlier-user","speaker":"user","text":"Some earlier conversation","start_ms":300,"end_ms":600})
        rows.append(row)
    assert len(rows)==24
    assert len({r["id"] for r in rows})==24
    return rows


def preference_fixtures():
    examples=[("Create a page. Tell me when it finishes.","requested"),
        ("Tell me when it","incomplete"),("Create a page. Stay quiet when it finishes.","suppressed"),
        ("I asked for an update earlier. Actually, keep quiet when it finishes.","suppressed"),
        ("Never mind my request for silence; let me know when it's ready.","requested"),
        ("Use your judgment about whether its completion is worth mentioning.","discretionary"),
        ("Create a Hello World page.","none"),("What's the weather like?","none"),
        ("What does the phrase 'tell me when it finishes' mean?","none"),
        ("The page is ready.","none"),("Please notify me if either task fails or finishes.","requested"),
        ("Actually, don't tell me when the page finishes. I'll check it myself.","suppressed")]
    return [{"id":str(i),"input":{"dialogue":[{"speaker":"user","text":text}]},"expected":label}
        for i,(text,label) in enumerate(examples)]


def schedule():
    from app.announcement_eval import bookkeeping as b
    rows=deepcopy(b.schedule())
    base={r["id"]:r for r in rows}
    # Existing twelve cases use fourteen connections. Add two distinct cases.
    correction=deepcopy(base["changed_silence"]);correction["id"]="long_request_correction"
    correction["steps"][0]["text"]="Create a task to write a Hello World page. Please make it a simple page with a clear heading and a short greeting. Tell me when it has finished."
    regression=deepcopy(base["explicit_idle"]);regression["id"]="reminder_clarification"
    regression["steps"][3:3]=[{"type":"question"},{"type":"wait","seconds":3},
        {"type":"speak","text":"Use a short greeting and continue the task. Also remind me in ten seconds to stretch."},
        {"type":"wait","seconds":12}]
    rows.extend([regression,correction])
    return rows


def manifest():
    return {"phase":PHASE,"configurations":CONFIGURATIONS,"screen_limit":8,"confirmation_browser_limit":32,
        "confirmation_per_candidate":16,"max_finalists":2,
        "browser_limit":40,"android_limit":6,"calibration_limit":60,"historical_sessions":84,
        "responses_policy_hash":observer.POLICY_HASH,"decisions_policy_hash":decisions.POLICY_HASH,
        "fixtures_hash":observer.digest(fixtures()),"preference_fixtures_hash":observer.digest(preference_fixtures()),
        "confirmation_cases":schedule(),"screen_cases":[c for c in schedule() if c["id"] in {"explicit_idle","explicit_speaking"}]}


def unavailable_classifiers(saved):
    blocked={}
    for row in calibration_runs(saved):
        message=str(row.get("provider_error", "")).lower()
        if (row.get("http_status") in {502,503,504} and "server_error" in message
                and not any(word in message for word in ("quota","billing","api_key","authentication"))):
            keys=("decisions","preferences") if row["classifier"] in {"decisions","preferences"} else ("responses",)
            blocked.update({k:{"reason":"provider_service_unavailable","failed_request":row["id"],
                "http_status":row["http_status"],"retry":False} for k in keys})
    return blocked


def calibration_runs(saved):
    return [r for batch in saved.get("retained_batches",[]) for r in batch["runs"]]+saved.get("runs",[])


def calibration_status(saved):
    expected={"responses":24,"decisions":24,"preferences":12}
    return {k:len([x for x in saved["runs"] if x["classifier"]==k])==count and
        all(x.get("passed",False) for x in saved["runs"] if x["classifier"]==k) for k,count in expected.items()}


async def calibrate(output):
    from app.announcement_eval import runner as r
    path=output/"calibration.json"
    saved=json.loads(path.read_text(encoding="utf8")) if path.exists() else {"identity":manifest(),"runs":[]}
    if saved["identity"]!=manifest():
        # A repaired classifier gets a new fixed batch. Its older results and
        # failed requests still consume the same sixty-request ceiling.
        retained=[*saved.get("retained_batches",[]),{k:v for k,v in saved.items() if k!="retained_batches"}]
        saved={"identity":manifest(),"runs":[],"retained_batches":retained}
        r.write(path,saved)
    for classifier in ("responses","decisions","preferences"):
        if classifier in unavailable_classifiers(saved):continue
        pending=[fixture for fixture in (preference_fixtures() if classifier=="preferences" else fixtures())
            if not any(x["classifier"]==classifier and x["id"]==fixture["id"] for x in saved["runs"])]
        remaining=60-len(calibration_runs(saved))
        if len(pending)>remaining:
            raise ValueError(f"Fixed {classifier} calibration batch requires {len(pending)} requests; only {remaining} remain")
        for fixture in pending:
            if len(calibration_runs(saved))>=60:raise ValueError("Calibration ceiling reached")
            row={"classifier":classifier,"id":fixture["id"],"started":True};saved["runs"].append(row);r.write(path,saved)
            try:
                if classifier=="preferences":
                    raw,usage=await decisions.request(fixture["input"],[decisions.preference_question()])
                    label=next((a.get("choice") for a in raw.get("answers",[]) if a.get("name")=="notification_preference"),None)
                    row.update(result=raw,usage=usage,passed=label==fixture["expected"])
                else:
                    result,usage=await (observer.infer(fixture["evidence"]) if classifier=="responses" else decisions.infer(fixture["evidence"]))
                    finding=result["outcomes"][0];support=observer.validate_finding(fixture["evidence"],finding)
                    row.update(result=result,usage=usage,validation=support,passed=finding["classification"] in fixture["expected_classifications"]
                        and support["supported"]==fixture["expected_deliverable"])
            except Exception as exc:
                row.update(error=type(exc).__name__,passed=False)
                response=getattr(exc,"response",None)
                if response is not None:
                    from app.config import settings
                    row["http_status"]=response.status_code
                    row["provider_error"]=response.text.replace(settings.openai_api_key,"[redacted]")[:2000]
                r.write(path,saved)
                if classifier in unavailable_classifiers(saved):break
                raise  # Stop all paid work on transport/auth/quota/integrity failure.
            r.write(path,saved)
    saved["unavailable"]=unavailable_classifiers(saved)
    saved["qualified"]=calibration_status(saved)
    r.write(path,saved)
    return saved


def execute(args):
    from app.announcement_eval import runner as r,bookkeeping as b,focused as f
    from app.jarvis_tasks.acceptance import candidate_identity
    output=r.root_path(args.output)
    if args.stage=="offline":
        clone=deepcopy(args);clone.natural_updates=False;clone.conversation_bookkeeper=True
        b.execute(clone)
        texts=sorted({s[k] for c in schedule() for s in c["steps"] for k in ("text","answer") if k in s})
        entries=[{"id":sha256(t.encode()).hexdigest()[:16],"text":t} for t in texts]
        corpus=output/"natural-corpus";speech=output/"natural-speech.json"
        r.write(speech,{"wake":"Hey Jarvis","cases":entries})
        subprocess.run(["powershell","-NoProfile","-ExecutionPolicy","Bypass","-File",str(r.ROOT/"android/scripts/generate-audio-corpus.ps1"),
            "-OutputDirectory",str(corpus),"-ManifestPath",str(speech)],check=True,timeout=180)
        r.write(output/"natural-corpus-identity.json",[r.frozen_identity(corpus/(e["id"]+"-command.wav")) for e in entries])
        r.write(output/"natural-manifest.json",manifest());r.write(output/"natural-fixtures.json",fixtures())
        r.write(output/"preference-fixtures.json",preference_fixtures())
        r.write(output/"local-comparison.json",{
            "accuracy_conclusion":"Local mocks validate integration, not model judgment; all four remain online candidates.",
            "configurations":{key:{**value,"speech_policy":"shared","semantic_policy":"shared",
                "requested_routing":"commentary","other_routing":"thinking",
                "checks":["trigger selection","complete/partial/muted/stale delivery ledger",
                    "preference revisions","source-event correlation and ordering","admission freshness"]}
                for key,value in CONFIGURATIONS.items()},
            "offline_gate":str(output/"offline.json"),"provider_requests":0})
        historical=[]
        for name in ("pilot-20261016-bookkeeping-01","pilot-20261016-bookkeeping-02"):
            root=r.BASE/name
            for case in root.glob("bookkeeping-*"):
                for job in b.observer_records(case):
                    historical.append({"source":str(case),"observation_id":job["id"],"original":job.get("result_json"),
                        "replay":[observer.validate_finding(job["input_json"],x) for x in (job.get("result_json") or {}).get("outcomes",[])]})
        r.write(output/"historical-replay.json",historical)
        return
    if args.stage=="report":
        if args.live:raise ValueError("Report replay must be network-free")
        report=json.loads((output/"report.json").read_text(encoding="utf8"))
        report["report_replay_provider_requests"]=0
        for row in report["runs"]:
            row["observer_records"]=b.observer_records(Path(row["directory"]))
        r.write(output/"report.json",report);return
    if not args.live or not args.offline:raise ValueError("Paid stages require --live and a frozen offline gate")
    if not r.settings.openai_api_key.strip():raise ValueError("Existing authorized OPENAI_API_KEY is unavailable")
    offline=r.root_path(args.offline)
    if json.loads((offline/"candidate.json").read_text(encoding="utf8"))!=candidate_identity():raise ValueError("Candidate changed since offline freeze")
    if json.loads((offline/"natural-manifest.json").read_text(encoding="utf8"))!=manifest():raise ValueError("Manifest changed")
    if not all(x["exit_code"]==0 for x in json.loads((offline/"offline.json").read_text(encoding="utf8"))):raise ValueError("Offline gate failed")
    for item in json.loads((offline/"natural-corpus-identity.json").read_text(encoding="utf8")):
        if r.frozen_identity(Path(item["path"]))!=item:raise ValueError("Corpus changed")
    output.mkdir(exist_ok=True,parents=True)
    path=output/"report.json"
    report=json.loads(path.read_text(encoding="utf8")) if path.exists() else {"phase":PHASE,"manifest":manifest(),
        "candidate":candidate_identity(),"runs":[],"default":"disabled_pending_qualification","prior":str(r.BASE/"pilot-20261017-natural-01"/"report.json")}
    if report["phase"]!=PHASE:raise ValueError("Use a new output directory for the newly authorized comparison phase")
    if report["candidate"]!=candidate_identity():
        if args.confirm or args.stage=="acceptance":raise ValueError("Confirmation candidate changed; prior confirmation cannot qualify a new source")
        report.setdefault("retained_candidates",[]).append(report["candidate"])
        report["candidate"]=candidate_identity()
    if report.get("paid_stop"):
        saved_calibration=json.loads((output/"calibration.json").read_text(encoding="utf8"))
        errors=[x for x in saved_calibration["runs"] if x.get("error")]
        if (args.configuration=="1" and not report["runs"] and calibration_status(saved_calibration)["responses"]
                and errors and all(bool(unavailable_classifiers({"runs":[x]})) for x in errors)):
            report.setdefault("retained_paid_stops",[]).append({"stop":report.pop("paid_stop"),
                "continuation":"Only independently calibrated configuration 1; no Decisions request retried"})
            report.pop("stopped_reason",None)
        else:raise ValueError("Retained paid execution stop: "+report["paid_stop"])
    ledger=r.RequestLedger(output,json.loads((output/"request-ledger.json").read_text(encoding="utf8")) if (output/"request-ledger.json").exists() else [])
    def save():
        report["usage"]=r.usage_summary(output,ledger)
        prior=json.loads(Path(report["prior"]).read_text(encoding="utf8"))
        report["cumulative_usage"]=r.merge_usage(deepcopy(report["usage"]),prior["cumulative_usage"])
        report["observer_records"]=[j for x in report["runs"] for j in b.observer_records(Path(x["directory"]))]
        report["classifier_requests"]={"calibration":json.loads((output/"calibration.json").read_text(encoding="utf8")) if (output/"calibration.json").exists() else None,
            "runtime_requests":sum(len((j.get("usage_json") or {}).get("attempts",[])) for j in report["observer_records"]),
            "runtime_usage":[j.get("usage_json") for j in report["observer_records"]]}
        native={}
        for x in report["runs"]:
            events=Path(x["directory"])/"events.jsonl"
            if not events.exists():continue
            for line in events.read_text(encoding="utf8").splitlines():
                e=json.loads(line);event=e.get("event",{})
                if e.get("stream")=="incoming" and event.get("type")=="decision.event":
                    native[(e.get("session_id"),event.get("event_id"))]=event
        report["native_decisions_requests"]={"count":len(native),"events":list(native.values())}
        report["monetary_cost"]=None;r.write(path,report)
    try:
        calibration=asyncio.run(calibrate(output));report["calibration"]=calibration;save()
        if args.calibrate:return
        config=args.configuration
        if config not in CONFIGURATIONS:raise ValueError("Select --configuration 1..4")
        cfg=CONFIGURATIONS[config];q=calibration["qualified"]
        if not q[cfg["bookkeeper"]] or (cfg["preferences"] and not q["preferences"]):raise ValueError("Configuration did not pass semantic calibration")
        for row in report["runs"]:
            review=Path(row["directory"])/"agent-review.json"
            if not review.exists():raise ValueError("Recorded case requires direct agent review")
            row["review"]=json.loads(review.read_text(encoding="utf8"))
        own=[x for x in report["runs"] if x["configuration"]==config and x.get("source_digest")==report["candidate"]["source_digest"]]
        if any(not x["review"].get("qualified") for x in own):raise ValueError("Configuration has a retained mandatory failure")
        if args.confirm:
            selection=output/"selection.json"
            if not selection.exists():raise ValueError("Freeze directly reviewed finalists in selection.json first")
            selected=json.loads(selection.read_text(encoding="utf8"))
            finalists=selected.get("finalists",[])
            if (not 1<=len(finalists)<=2 or len(set(finalists))!=len(finalists) or config not in finalists
                    or selected.get("source_digest")!=report["candidate"]["source_digest"]):raise ValueError("Finalist identity mismatch")
            if len([x for x in own if x["cohort"]=="screen"])!=2:raise ValueError("Winner lacks two screening cases")
            cohort="confirmation";cases=schedule()
        else:cohort="screen";cases=manifest()["screen_cases"]
        android=args.stage=="acceptance"
        if android:
            if selected.get("winner")!=config:raise ValueError("Android requires the selected browser winner")
            browser=[x for x in own if x["cohort"]=="confirmation" and x["transport"]=="browser"]
            if len(browser)!=len(schedule()) or not all(x["review"].get("complete_interaction_qualified") for x in browser):raise ValueError("Android requires full browser qualification")
            cases=b.android_schedule();cohort="android"
        done={x["case_id"] for x in own if x["cohort"]==cohort}
        case=next((c for c in cases if c["id"] not in done),None)
        if case is None:report[cohort+"_complete"]=True;return
        connections=lambda c:2 if c["id"] in {"between_sessions","interruption_reconnect"} else 1
        def counted_connections(x):
            events=Path(x["directory"])/"events.jsonl"
            actual=sum(json.loads(line).get("event",{}).get("type")=="provider.connect.request" for line in events.read_text(encoding="utf8").splitlines()) if events.exists() else 0
            return max(x["connection_reservation"],actual)
        count=sum(counted_connections(x) for x in report["runs"] if x["cohort"]==cohort)
        limit=6 if android else 32 if args.confirm else 8
        if count+connections(case)>limit:raise ValueError("Non-resetting phase session ceiling reached")
        run_cohort=cohort+"-c"+config+"-"+report["candidate"]["source_digest"][:8]
        folder=output/f"{run_cohort}-production_A-{case['id']}-1-immediate-{'android' if android else 'browser'}"
        row={"configuration":config,"cohort":cohort,"case_id":case["id"],"directory":str(folder),
            "transport":"android" if android else "browser","connection_reservation":connections(case)}
        row["source_digest"]=report["candidate"]["source_digest"]
        report["runs"].append(row);save()
        env={"JARVIS_CONVERSATION_OBSERVER_MODE":"enabled","JARVIS_DECISIONS_PREFERENCES":str(cfg["preferences"]).lower(),"JARVIS_BOOKKEEPER":cfg["bookkeeper"]}
        old={k:os.environ.get(k) for k in env};os.environ.update(env)
        try:
            wake=Path(json.loads((offline/"adoption-corpus-identity.json").read_text(encoding="utf8"))["wake"]["path"])
            r.run_case(output,case,"production_A","immediate",run_cohort,1,offline/"natural-corpus",wake,android=android)
        finally:
            for k,v in old.items():
                if v is None:os.environ.pop(k,None)
                else:os.environ[k]=v
        records=b.observer_records(folder)
        evidence=json.loads((folder/"evidence.json").read_text(encoding="utf8"))
        if evidence.get("paid_failure") or r.provider_failure(evidence):
            raise r.PaidFailure("Voice provider authentication/billing/quota failure; no further paid requests")
        if evidence.get("capture_error") or (not evidence.get("error") and evidence.get("recording_complete") is not True):
            raise r.PaidFailure("Recording evidence integrity failed; repair locally before any paid continuation")
        if any(j["status"]=="failed" for j in records):raise r.PaidFailure("Observer provider/evidence failure")
        row["assessment"]=f.evaluate_case(folder,folder/"assessment-v3",ledger,live=True,grade_live=False,transcription_limit=46)
        report["awaiting_agent_review"]=str(folder)
    except r.PaidFailure as exc:
        report["paid_stop"]=str(exc);raise
    except Exception as exc:
        # Calibration transport/auth failures stop the whole paid phase.
        if not (output/"calibration.json").exists() or any(x.get("error") for x in json.loads((output/"calibration.json").read_text(encoding="utf8")).get("runs",[])):
            report["paid_stop"]=type(exc).__name__
        report["stopped_reason"]=str(exc);raise
    finally:save()
