"""Bounded focused phase using the existing host/driver and public runner stages."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import httpx

from app.announcement_eval import assessment as scoring
from app.announcement_eval.profiles import profile, profile_identity

PROFILES = ("live_thinking_listen_first", "live_thinking_concise")
CONDITIONS = ("explicit_idle", "explicit_speaking", "silence", "changed_silence", "occupied", "occupied_control")
LIMIT = 24
PHASE = "focused-context-v2"


def matrix():
    cases = []
    for repetition in (1, 2):
        requested = ("Create a task to write a Hello World page. Tell me when it has finished." if repetition == 1 else
            "Please start a task to write a Hello World page. Let me know once the page is ready.")
        quiet = ("Create a task to write a Hello World page. Stay quiet when it finishes. I will look at it myself." if repetition == 1 else
            "Please start a task to write a Hello World page. Do not announce its completion. I will check it myself.")
        ordinary = ("Create a task to write a Hello World page. It is not urgent." if repetition == 1 else
            "Please start a task to write a Hello World page. It can wait; there is no urgency.")
        correction = ("Actually, I changed my mind. Stay quiet when the Hello World page finishes. I will check it myself." if repetition == 1 else
            "I have changed my preference. Do not announce when that page is ready. I will inspect it myself.")
        description = ("Let's talk about arranging my desk instead. I am thinking of placing a lamp beside my notebook. Please let me finish. There should also be some space for a cup of tea. The page can wait while we discuss this." if repetition == 1 else
            "While the task runs, please listen to my whole description before replying. I would like a tidy desk with my notebooks together. There should be room for a lamp and a cup of tea. I am still thinking about where each item should go.")
        question = "What is the current status of the Hello World page task?" if repetition == 1 else "Is the Hello World page task finished?"
        for condition in CONDITIONS:
            expectation = "required" if condition in {"explicit_idle", "explicit_speaking"} else "forbidden" if condition in {"silence", "changed_silence"} else "optional"
            opening = quiet if condition == "silence" else ordinary if condition.startswith("occupied") else requested
            steps = [{"type": "speak", "text": opening}, {"type": "admit", "count": 1}, {"type": "settle"}]
            if condition == "changed_silence":
                steps += [{"type": "speak", "text": correction}, {"type": "settle"}]
            occupied = condition in {"explicit_speaking", "occupied", "occupied_control"}
            if occupied:
                steps += [{"type": "speak_async", "text": description}, {"type": "wait", "seconds": 1}]
            steps += [{"type": "comparison"}] if condition == "occupied_control" else [{"type": "finish"}]
            if occupied: steps += [{"type": "join"}]
            steps += [{"type": "wait", "seconds": 16}, {"type": "observation_end"}]
            if expectation == "forbidden":
                steps += [{"type": "status_query", "text": question}, {"type": "wait", "seconds": 12}]
            cases.append({"id": condition, "repetition": repetition, "expectation": expectation, "steps": steps})
    return cases


def schedule():
    # Four-run pilot first, followed by matched conditions; second wording reverses A/B.
    cells = [(1, "explicit_idle"), (1, "silence")]
    cells += [(r, c) for r in (1, 2) for c in CONDITIONS if (r, c) not in cells]
    return [{"condition": c, "repetition": r, "profile": p} for r, c in cells
        for p in (PROFILES if r == 1 else tuple(reversed(PROFILES)))]


def manifest():
    return {"schema": "jarvis-focused-experiment.v2", "phase": PHASE, "session_limit": LIMIT,
        "grading_limit": 64, "transcription_limit": 24, "profiles": [profile_identity(profile(p), "immediate") for p in PROFILES],
        "cases": matrix(), "schedule": schedule(), "rubric_sha256": scoring.RUBRIC_HASH,
        "settle_ms": 1500, "setup_timeout_ms": 20000, "observation_ms": 16000,
        "deadline_ms": 10000, "measurement_tolerance_ms": 250, "decision_owner": "gpt-live"}


def prepare_corpus(output, *, reuse=None):
    from app.announcement_eval import runner as r
    target = output / "focused-corpus"
    texts = sorted({s["text"] for c in matrix() for s in c["steps"] if "text" in s})
    entries = [{"id": sha256(t.encode()).hexdigest()[:16], "text": t} for t in texts]
    source = output / "focused-speech-manifest.json"
    r.write(source, {"wake": "Hey Jarvis", "cases": entries})
    if reuse and (reuse / "focused-corpus-identity.json").exists() and not target.exists():
        saved = json.loads((reuse / "focused-corpus-identity.json").read_text(encoding="utf-8"))
        for identity in saved["files"]:
            if r.frozen_identity(Path(identity["path"])) != identity: raise ValueError("Cached focused corpus changed")
        target.mkdir()
        for identity in saved["files"]:
            r.shutil.copy2(identity["path"], target / Path(identity["path"]).name)
    if not target.exists():
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(r.ROOT / "android/scripts/generate-audio-corpus.ps1"), "-OutputDirectory", str(target),
            "-ManifestPath", str(source)], check=True, timeout=180, stdout=subprocess.DEVNULL)
    identities = [r.frozen_identity(target / (e["id"] + "-command.wav")) for e in entries]
    r.write(output / "focused-corpus-identity.json", {"manifest": r.frozen_identity(source),
        "renderer": r.frozen_identity(r.ROOT / "android/scripts/generate-audio-corpus.ps1"), "files": identities,
        "wake": r.frozen_identity(r.ROOT / "data/audio-corpus-api-v1/ready01-wake.wav")})
    r.write(output / "focused-manifest.json", manifest())


def semantic_input(evidence, audio):
    return {"caller": [m for m in evidence.get("markers", []) if m["type"].startswith("caller.")],
        "recognized_caller": "".join(e["event"].get("delta", "") for e in evidence.get("state", {}).get("provider_events", [])
            if e["event"].get("type") == "session.input_transcript.delta"),
        "tasks": evidence.get("state", {}).get("tasks", []),
        "words": [{"index": i, **{k:w[k] for k in ("word", "start", "end")}} for i, w in enumerate(audio.get("words", []))],
        "status_query_end_ms": evidence.get("status_query_end_ms")}


def semantic_call(input_value, path, ledger, *, live):
    from app.announcement_eval import runner as r
    identity = sha256(json.dumps({"input": input_value, "rubric": scoring.RUBRIC_HASH}, sort_keys=True).encode()).hexdigest()
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved["identity"] != identity: raise ValueError("Saved semantic input/rubric changed")
        return saved["judgment"]
    if not live: return None
    if sum(item["purpose"] == "/responses" for item in ledger) >= 64:
        raise ValueError("Focused grading ceiling reached")
    with httpx.Client(timeout=60, trust_env=False) as client:
        result = r.api_request(client, "/responses", ledger, payload={"model": "gpt-5.6-luna", "service_tier": "priority",
            "reasoning": {"effort": "low"}, "store": False, "instructions": scoring.RUBRIC,
            "input": json.dumps(input_value), "text": {"format": {"type": "json_schema", "name": "utterance_assessment",
                "strict": True, "schema": scoring.SCHEMA}}})
    text = "".join(c.get("text", "") for item in result.get("output", []) if item.get("type") == "message"
        for c in item.get("content", []) if c.get("type") == "output_text")
    judgment = json.loads(text)
    r.write(path, {"identity": identity, "rubric_sha256": scoring.RUBRIC_HASH, "judgment": judgment, "response": result})
    return judgment


def calibration_fixtures():
    definitions = [
        ("added", "Your task to write a Hello World page has been added.", "admission", "none", False),
        ("queued", "The task is queued.", "admission", "none", False),
        ("created", "I created that task for you.", "admission", "none", False),
        ("ambiguous_done", "Done.", "ambiguous", "none", False),
        ("promise", "I will tell you when the page is finished.", "promise", "none", False),
        ("progress", "I am still working on the page.", "progress", "none", False),
        ("complete", "Your Hello World page is ready.", "outcome", "completed", True),
        ("failed", "The Hello World page task failed because the input was unavailable.", "outcome", "failed", True),
        ("backchannel", "Mm-hmm.", "backchannel", "none", False),
        ("conversation", "Place the lamp opposite your writing hand.", "conversation", "none", False),
        ("quiet_promise", "I will stay quiet when it finishes.", "promise", "none", False),
        ("partial_name", "The Hello World page", "ambiguous", "none", False),
    ]
    result = []
    for id, text, kind, outcome, complete in definitions:
        words = [{"word": w, "start": i * .2, "end": (i + 1) * .2} for i, w in enumerate(text.split())]
        result.append({"id": id, "input": {"caller": [], "recognized_caller": "Create a Hello World page task.",
            "tasks": [{"id": "task-a", "goal": "Write a Hello World page", "status": "failed" if id == "failed" else "completed"}],
            "words": [{"index": i, **w} for i, w in enumerate(words)]},
            "expected": {"kind": kind, "outcome": outcome, "complete": complete}})
    return result


def calibrate(output, ledger, live):
    from app.announcement_eval import runner as r
    folder = output / "calibration"; folder.mkdir(exist_ok=True)
    results = []
    for fixture in calibration_fixtures():
        judgment = semantic_call(fixture["input"], folder / (fixture["id"] + ".json"), ledger, live=live)
        spans = (judgment or {}).get("utterances", [])
        passed = len(spans) == 1 and spans[0]["start_word"] == 0 and spans[0]["end_word"] == len(fixture["input"]["words"]) - 1
        passed = passed and all(spans[0].get(k) == v for k, v in fixture["expected"].items())
        results.append({"fixture": fixture, "judgment": judgment, "passed": passed})
    report = {"rubric_sha256": scoring.RUBRIC_HASH, "passed": all(row["passed"] for row in results), "results": results}
    r.write(output / "calibration.json", report)
    return report


def load_evidence(folder):
    from app.announcement_eval import runner as r
    identity_path = folder / "artifact-identity.json"
    if identity_path.exists():
        for identity in json.loads(identity_path.read_text(encoding="utf-8")):
            if r.frozen_identity(Path(identity["path"])) != identity:
                raise ValueError("Frozen recorded artifact changed: " + identity["path"])
    evidence = json.loads((folder / "evidence.json").read_text(encoding="utf-8"))
    final_path=folder/"observer-drain.json"
    if final_path.exists():
        final=json.loads(final_path.read_text(encoding="utf8"))
        evidence["observer_processing"]=final
        # Preserve the driver's earlier snapshot on disk. Only project later
        # durable communication state for the exact same result identity.
        by_id={p["id"]:p for p in final.get("presentations",[])}
        for p in evidence.get("state",{}).get("presentations",[]):
            latest=by_id.get(p["id"])
            if latest and latest["source_task_event_id"]==p.get("source_task_event_id"):
                p.update({k:latest[k] for k in ("state","handled_by","handled_at","handled_event_id")})
    # Provider punctuation is not a recognition failure. Preserve the original
    # flag and recording; this narrowly repairs the earlier hyphen-only check.
    if not evidence.get("input_understood") and "hello-world" in evidence.get("recognized_request","") and evidence.get("target_task_ids"):
        recognized = evidence["recognized_request"]
        preference_ok = evidence.get("expectation") not in {"required","forbidden","query"} or bool(__import__("re").search(
            r"tell.{0,60}(when|once)|let.{0,30}know|notify" if evidence["expectation"]=="required" else r"quiet|silence|silent|do not announce",recognized))
        if preference_ok:
            evidence["input_understood"] = True
            evidence["recognition_flag_repair"] = "hello-world punctuation; original recording and flag retained"
    if (folder / "caller.wav").exists():
        base = evidence.get("assistant_audio_epoch_ms", 0)
        evidence["caller_voiced_windows"] = [[base + s * 1000, base + e * 1000]
            for s,e in scoring.energy_windows(folder / "caller.wav")]
    return evidence


def evaluate_case(folder, destination, ledger, *, live, historical=False, grade_live=None, transcription_limit=24):
    from app.announcement_eval import runner as r
    destination.mkdir(exist_ok=True)
    evidence = load_evidence(folder)
    if historical:
        audio = json.loads((folder / "audio-transcription.json").read_text(encoding="utf-8"))
    else:
        audio_path = destination / "audio-transcription.json"
        if audio_path.exists(): audio = json.loads(audio_path.read_text(encoding="utf-8"))
        elif live and not evidence.get("error") and not evidence.get("capture_error"):
            if sum(item["purpose"] == "/audio/transcriptions" for item in ledger) >= transcription_limit:
                raise ValueError("Focused transcription ceiling reached")
            mapping = scoring.pack_audio(folder / "assistant.wav", destination / "assistant-packed.wav")
            r.write(destination / "packing-map.json", mapping)
            if mapping:
                with httpx.Client(timeout=60, trust_env=False) as client, (destination / "assistant-packed.wav").open("rb") as recording:
                    transcript = r.api_request(client, "/audio/transcriptions", ledger, data={"model": "whisper-1",
                        "response_format": "verbose_json", "language": "en", "timestamp_granularities[]": "word"},
                        files={"file": ("assistant-packed.wav", recording, "audio/wav")})
                r.write(destination / "packed-transcription.json", transcript)
                audio = scoring.restore_words(transcript, mapping)
            else: audio = {"words": [], "text": "", "verified_acoustic_silence": True}
            r.write(audio_path, audio)
        else: audio = None
        if audio is not None and (destination / "packed-transcription.json").exists():
            # Derive timing warnings from the original provider response without
            # changing cached words, original grades or issuing another request.
            audio = scoring.restore_words(json.loads((destination / "packed-transcription.json").read_text(encoding="utf8")),
                json.loads((destination / "packing-map.json").read_text(encoding="utf8")))
    judgment = semantic_call(semantic_input(evidence, audio), destination / "semantic.json", ledger,
        live=live if grade_live is None else grade_live) if audio is not None else None
    result = scoring.assess(evidence, audio, judgment)
    r.write(destination / "assessment.json", result)
    return result


def recommendation(rows):
    result = []
    expected = {(c, r) for c in CONDITIONS for r in (1, 2)}
    for name in PROFILES:
        selected = [row for row in rows if row["profile"] == name]
        complete = {(row["case_id"], row["repetition"]) for row in selected} == expected
        selection = complete and all(row["assessment"]["selection_status"] == "passed" for row in selected)
        interaction = selection and all(row["assessment"]["interaction_status"] == "passed" for row in selected)
        result.append({"profile": name, "matrix_complete": complete, "selection_demonstrated": selection,
            "complete_interaction_qualified": interaction})
    preferred = next((row["profile"] for row in result if row["selection_demonstrated"]), None)
    return {"profiles": result, "preferred_for_investigation": preferred,
        "acceptance_allowed": any(row["complete_interaction_qualified"] for row in result), "production_changed": False}


def canonical_usage(usage):
    """Request IDs identify actual calls; differing saved snapshots are not new calls."""
    value = deepcopy(usage)
    value["jarvis_voice"] = list({x["session_id"]: x for x in value["jarvis_voice"]}.values())
    value["jarvis_backend"] = list({x["response_id"]: x for x in value["jarvis_backend"]}.values())
    unique = {}
    for row in value["transcription_and_grading_requests"]:
        key = row.get("request_id") or (row.get("purpose"), row.get("started_at"))
        if key == (row.get("purpose"), None): key = json.dumps(row, sort_keys=True)
        unique[key] = row
    value["transcription_and_grading_requests"] = list(unique.values())
    return value


def retained_usage(reports):
    from app.announcement_eval import runner as r
    parents = {Path(row["directory"]).parent for report in reports for row in report["runs"]}
    result = None
    identities = []
    def embedded(value):
        if isinstance(value, dict):
            if "jarvis_voice" in value: yield value
            else:
                for key, child in value.items():
                    if key != "runs": yield from embedded(child)
    for parent in sorted(parents):
        r.root_path(parent)
        ledger_path = parent / "request-ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else []
        result = r.merge_usage(r.usage_summary(parent, ledger), result)
        path = parent / "report.json"
        if path.exists():
            identities.append(r.frozen_identity(path))
            for usage in embedded(json.loads(path.read_text(encoding="utf-8"))): result = r.merge_usage(result, usage)
    return canonical_usage(result), identities


def annotate_report(report):
    executed = {(row["case_id"], row["repetition"], row["profile"]) for row in report["runs"]}
    report["unrun_cells"] = [{**cell, "reason": "profile_stopped" if cell["profile"] in report["stopped_profiles"]
        else "phase_stopped"} for cell in schedule() if (cell["condition"], cell["repetition"], cell["profile"]) not in executed]


def execute(args):
    from app.announcement_eval import runner as r
    from app.jarvis_tasks.acceptance import candidate_identity
    output = r.root_path(args.output)
    if args.stage == "acceptance": raise ValueError("Focused phase cannot automatically launch acceptance")
    if args.stage == "report":
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        if report["phase"] != PHASE: raise ValueError("Not a focused report")
        ledger = r.RequestLedger(output, json.loads((output / "request-ledger.json").read_text(encoding="utf-8")))
        for row in report["runs"]:
            row["assessment"] = evaluate_case(Path(row["directory"]), Path(row["assessment_directory"]), ledger, live=args.live)
        report["recommendation"] = recommendation(report["runs"])
        prior = json.loads(Path(report["prior"]["path"]).read_text(encoding="utf-8"))
        older = json.loads(Path(prior["prior_phase"]["path"]).read_text(encoding="utf-8"))
        report["prior_usage"], report["prior_usage_sources"] = retained_usage([prior, older])
        report["usage"] = r.usage_summary(output, ledger)
        report["cumulative_usage"] = canonical_usage(r.merge_usage(deepcopy(report["usage"]), report["prior_usage"]))
        report["report_generation_candidate"] = candidate_identity()
        annotate_report(report)
        r.write(output / "report.json", report); return
    if not args.live or not r.settings.openai_api_key.strip(): raise ValueError("Focused pilot requires --live and existing authorized key")
    if not args.offline or not args.prior: raise ValueError("Focused pilot requires --offline and retained --prior report")
    offline = r.root_path(args.offline)
    if not all(check["exit_code"] == 0 for check in json.loads((offline / "offline.json").read_text(encoding="utf-8"))):
        raise ValueError("Offline checks did not pass")
    current = candidate_identity()
    if json.loads((offline / "candidate.json").read_text(encoding="utf-8")) != current:
        raise ValueError("Candidate changed since offline validation")
    if json.loads((offline / "focused-manifest.json").read_text(encoding="utf-8")) != manifest():
        raise ValueError("Focused manifest changed")
    corpus_identity = json.loads((offline / "focused-corpus-identity.json").read_text(encoding="utf-8"))
    for identity in [*corpus_identity["files"], corpus_identity["wake"], corpus_identity["manifest"], corpus_identity["renderer"]]:
        if r.frozen_identity(Path(identity["path"])) != identity: raise ValueError("Focused corpus changed")
    prior_path = r.root_path(args.prior) / "report.json"
    prior = json.loads(prior_path.read_text(encoding="utf-8"))
    if len(prior["runs"]) != 28: raise ValueError("Expected retained 28-run context screen")
    older_path = Path(prior["prior_phase"]["path"])
    if r.frozen_identity(older_path) != prior["prior_phase"]: raise ValueError("Original thirteen-run report changed")
    older = json.loads(older_path.read_text(encoding="utf-8"))
    prior_usage, usage_sources = retained_usage([prior, older])
    if args.resume:
        source = r.root_path(args.resume)
        report = json.loads((source / "report.json").read_text(encoding="utf-8"))
        if report["manifest"] != manifest() or report["prior"] != r.frozen_identity(prior_path):
            raise ValueError("Focused resume identity mismatch")
        old_files = {Path(i["path"]).name: i["sha256"] for i in report["corpus_identity"]["files"]}
        if old_files != {Path(i["path"]).name: i["sha256"] for i in corpus_identity["files"]}:
            raise ValueError("Focused resume changed frozen audio")
        if report["candidate"] != current:
            if report["candidate"]["configuration_digest"] != current["configuration_digest"]:
                raise ValueError("Focused continuation changed application configuration")
            report.setdefault("candidate_history", []).append(report["candidate"])
            report["candidate"] = current
            report["corpus_identity"] = corpus_identity
            report["continuation_reason"] = "Local setup/evidence repair; prompts, payloads, corpus and session ceiling unchanged"
        if source != output: raise ValueError("Resume the same dedicated focused output; no count resets")
        ledger = r.RequestLedger(output, json.loads((output / "request-ledger.json").read_text(encoding="utf-8")))
        if report.get("stopped_reason"):
            report.setdefault("stop_history", []).append({"reason": report.pop("stopped_reason"), "attempted_sessions": len(report["runs"])})
    else:
        output.mkdir(parents=True, exist_ok=False)
        report = {"schema": "jarvis-focused-report.v2", "phase": PHASE, "candidate": current,
            "manifest": manifest(), "corpus_identity": corpus_identity, "prior": r.frozen_identity(prior_path),
            "prior_run_count": 41, "prior_usage": prior_usage, "runs": [], "historical": [], "stopped_profiles": {}}
        ledger = r.RequestLedger(output)
    def save():
        report["usage"] = r.usage_summary(output, ledger)
        report["cumulative_usage"] = canonical_usage(r.merge_usage(deepcopy(report["usage"]), prior_usage))
        report["prior_usage_sources"] = usage_sources
        annotate_report(report)
        report["recommendation"] = recommendation(report["runs"])
        r.write(output / "report.json", report)
    try:
        calibration = calibrate(output, ledger, True)
        report["calibration"] = calibration
        save()
        if not calibration["passed"]: raise RuntimeError("Semantic calibration failed; no voice sessions allowed")
        historical = output / "historical"; historical.mkdir(exist_ok=True)
        for index, original in enumerate(prior["runs"]):
            if any(row["index"] == index for row in report["historical"]): continue
            print(f"Historical assessment {index+1}/28", flush=True)
            destination = historical / str(index + 1)
            result = evaluate_case(Path(original["directory"]), destination, ledger, live=True, historical=True)
            report["historical"].append({"index": index, "original": original, "assessment": result,
                "assessment_directory": str(destination)}); save()
        for recorded in report["runs"]:
            recorded.setdefault("original_assessment", recorded["assessment"])
            recorded.setdefault("recorded_candidate", report.get("candidate_history", [current])[0])
            result = evaluate_case(Path(recorded["directory"]), Path(recorded["assessment_directory"]), ledger, live=False)
            recorded["assessment"] = result
            if "admission_resumed_after_settle" in result["dimensions"]["evidence_validity"]["unknowns"]:
                recorded["retired_setup_diagnostic"] = True
                if report["stopped_profiles"].get(recorded["profile"], {}).get("condition") == recorded["case_id"]:
                    report["stopped_profiles"].pop(recorded["profile"], None)
            if "script_contains_competing_notification_preference" in result["dimensions"]["evidence_validity"]["unknowns"]:
                recorded["retired_script_diagnostic"] = True
        save()
        for cell in schedule():
            name = cell["profile"]
            if name in report["stopped_profiles"]: continue
            existing = next((row for row in report["runs"] if row["profile"] == name and row["case_id"] == cell["condition"]
                and row["repetition"] == cell["repetition"]), None)
            if existing:
                result = evaluate_case(Path(existing["directory"]), Path(existing["assessment_directory"]), ledger, live=True)
                existing["assessment"] = result
                if existing.get("retired_script_diagnostic"):
                    save(); continue
                if "admission_resumed_after_settle" in result["dimensions"]["evidence_validity"]["unknowns"]:
                    existing["retired_setup_diagnostic"] = True
                    report["stopped_profiles"].pop(name, None)
                    save(); continue  # Preserve/count this case; no replacement session.
            else:
                if len(report["runs"]) >= LIMIT: raise ValueError("Focused 24-session limit reached")
                case = next(c for c in matrix() if c["id"] == cell["condition"] and c["repetition"] == cell["repetition"])
                directory = output / f"focused-{name}-{case['id']}-{cell['repetition']}-immediate-browser"
                # Persist planned cell before any session opens, including crashes.
                row = {"profile": name, "case_id": case["id"], "repetition": cell["repetition"],
                    "directory": str(directory), "assessment_directory": str(directory / "assessment-v2"),
                    "recorded_candidate": current,
                    "assessment": {"selection_status": "inconclusive", "interaction_status": "inconclusive"}}
                report["runs"].append(row); save()
                print(f"Focused run {len(report['runs'])}/24: {name}/{case['id']}/{cell['repetition']}", flush=True)
                folder = r.run_case(output, case, name, "immediate", "focused", cell["repetition"],
                    offline / "focused-corpus", Path(corpus_identity["wake"]["path"]))
                evidence = json.loads((folder / "evidence.json").read_text(encoding="utf-8"))
                if evidence.get("paid_failure"): raise r.PaidFailure(evidence["paid_failure"])
                result = evaluate_case(folder, folder / "assessment-v2", ledger, live=True)
                row["assessment"] = result
            save()
            if result["selection_status"] == "inconclusive":
                raise RuntimeError("Focused evidence inconclusive; stop paid work for local investigation")
            if result["selection_status"] == "failed": report["stopped_profiles"][name] = cell
        report["completed_schedule"] = True
    except Exception as error:
        report["stopped_reason"] = str(error); raise
    finally: save()
