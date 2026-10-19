"""Independent audio/transport audit and bounded follow-up on two disputed claims."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess

from app.announcement_eval import assessment as a, focused as f
from app.announcement_eval.profiles import profile, profile_identity

PHASE = "context-mechanism-verification-v1"
PROFILE = "live_thinking_listen_first"
LIMIT = 8


def matrix():
    result = []
    for rep in (1, 2):
        old = next(c for c in f.matrix() if c["id"] == "occupied" and c["repetition"] == rep)
        description = ("Please listen until I have finished speaking because I want to explain my desk arrangement and I would like the notebooks together beside a lamp with enough room for a cup of tea and my keyboard and some space to write comfortably while looking out of the window" if rep == 1 else
            "Please wait until I have finished speaking because I am describing my workspace and I want a lamp beside my notebooks with room for my keyboard and a cup of tea and somewhere to write comfortably while keeping all the things that I use within reach")
        for condition in ("occupied_control", "occupied", "explicit_idle", "explicit_speaking"):
            requested = condition.startswith("explicit")
            opening = next(c for c in f.matrix() if c["id"] == ("explicit_idle" if requested else "occupied") and c["repetition"] == rep)["steps"][0]["text"]
            steps = [{"type": "speak", "text": opening}, {"type": "admit", "count": 1}, {"type": "settle", "quiet_ms": 3000}]
            if condition.startswith("occupied"):
                # Control and treatment use identical original occupied audio.
                steps += [{"type": "speak_async", "text": old["steps"][3]["text"]}, {"type": "wait", "seconds": 1}]
            if condition == "explicit_speaking":
                steps += [{"type": "speak_async", "text": description},
                    {"type": "wait_input", "pattern": "finished speaking"}]
            steps += [{"type": "comparison" if condition == "occupied_control" else "finish"}]
            if condition != "explicit_idle": steps += [{"type": "join"}]
            steps += [{"type": "wait", "seconds": 16}, {"type": "observation_end"}]
            result.append({"id": condition, "repetition": rep, "expectation": "required" if requested else "optional", "steps": steps})
    return result


def manifest():
    return {"phase": PHASE, "session_limit": LIMIT, "grading_limit": LIMIT, "transcription_limit": LIMIT,
        "profile": profile_identity(profile(PROFILE), "immediate"), "cases": matrix(), "rubric_sha256": a.RUBRIC_HASH,
        "deadline_ms": 10000, "tolerance_ms": 250, "stop_rule": "Stop each failed diagnostic condition; always retain matched control. Evidence/auth/billing/quota stops all paid work.",
        "speech_owner": "gpt-live", "production_changed": False}


def prepare_corpus(output):
    from app.announcement_eval import runner as r
    target = output / "verification-corpus"
    texts = sorted({s["text"] for c in matrix() for s in c["steps"] if "text" in s})
    entries = [{"id": sha256(t.encode()).hexdigest()[:16], "text": t} for t in texts]
    source = output / "verification-speech-manifest.json"
    r.write(source, {"wake": "Hey Jarvis", "cases": entries})
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(r.ROOT / "android/scripts/generate-audio-corpus.ps1"), "-OutputDirectory", str(target), "-ManifestPath", str(source)],
        check=True, timeout=180, stdout=subprocess.DEVNULL)
    identity = {"manifest": r.frozen_identity(source), "renderer": r.frozen_identity(r.ROOT / "android/scripts/generate-audio-corpus.ps1"),
        "files": [r.frozen_identity(target / (e["id"] + "-command.wav")) for e in entries],
        "wake": r.frozen_identity(r.ROOT / "data/audio-corpus-api-v1/ready01-wake.wav")}
    r.write(output / "verification-corpus-identity.json", identity)
    r.write(output / "verification-manifest.json", manifest())


def intersect(left, right, start, end):
    return sum(max(0, min(b, d, end) - max(c, x, start)) for x, b in left for c, d in right)


def native_span(events, text):
    output, owners = [], []
    for row in events:
        e = row["event"]
        if e.get("type") == "session.output_transcript.delta":
            part = a.tokens(e.get("delta", "")); output += part; owners += [e] * len(part)
    wanted = a.tokens(text)
    for i in range(len(output) - len(wanted) + 1):
        if wanted and output[i:i+len(wanted)] == wanted:
            selected = owners[i:i+len(wanted)]
            if all("start_ms" in e and "end_ms" in e for e in selected):
                return min(e["start_ms"] for e in selected), max(e["end_ms"] for e in selected)
    return None


def audit(folder, result):
    e = f.load_evidence(folder); base = e["assistant_audio_epoch_ms"]
    events = e["state"]["provider_events"]
    caller = a.energy_windows(folder / "caller.wav", join=0)
    assistant = a.energy_windows(folder / "assistant.wav", join=0)
    islands = a.energy_windows(folder / "assistant.wav", pad=0, join=.35)
    output = []
    for span in result.get("utterances", []):
        if span["kind"] != "outcome": continue
        start, end = ((span[edge] - base) / 1000 for edge in ("start_ms", "end_ms"))
        native = native_span(events, span["text"])
        input_ticks = [[r["event"]["start_ms"], r["event"]["end_ms"]] for r in events
            if r["event"].get("type") == "session.input_transcript.delta" and "start_ms" in r["event"]]
        simultaneous = intersect(caller, assistant, start, end)
        # Sensitivity to either audio stream being misaligned by up to 250 ms.
        lower = min(intersect(caller, [[x + shift, y + shift] for x,y in assistant], start+.25, end-.25)
            for shift in (-.25, -.125, 0, .125, .25)) if end-start > .5 else 0
        island_end = max((b for x,b in islands if x <= end+.1 and b >= end-.1), default=end)
        eligible = result.get("notification_clock_origins",{}).get(span["task_id"]) or e.get("eligible_ms", {}).get(span["task_id"])
        playback_end = base + max(end, island_end) * 1000
        output.append({"text": span["text"], "task_id": span["task_id"], "word_span_ms": [span["start_ms"], span["end_ms"]],
            "simultaneous_pcm_ms": simultaneous*1000, "conservative_pcm_ms": lower*1000,
            "native_provider_span_ms": native, "native_input_word_overlap_ms": intersect(input_ticks, [native], *native) if native else None,
            "acoustic_island_end_ms": playback_end, "conservative_latency_ms": playback_end-eligible if eligible else None,
            "client_playback": a.playback_span(e, span["start_ms"], playback_end),
            "caller_continues_after_outcome_ms": max((b*1000+base for x,b in caller), default=playback_end)-playback_end})
    transport = e.get("rtc_stats", [])
    wire = [r for p in transport for r in p["reports"]]
    starts = [m["epoch_ms"] for m in e["markers"] if m["type"] == "caller.start"]
    second_start = starts[1] if len(starts) > 1 else None
    second_end = next((m["epoch_ms"] for m in e["markers"] if m["type"] == "caller.end"
        and second_start is not None and m["epoch_ms"] > second_start), None)
    desk_pcm = intersect(caller, assistant, (second_start-base)/1000, (second_end-base)/1000)*1000 if second_end else None
    return {"schema": "jarvis-independent-signal-audit.v1", "source_artifacts": str(folder / "artifact-identity.json"),
        "outcomes": output, "pcm_frame_ms": 10, "threshold": "more than 5% samples above PCM16 amplitude 200; no pause joining",
        "occupied_turn_simultaneous_pcm_ms": desk_pcm, "occupied_turn_ms": [second_start, second_end],
        "capture_callback_offset_ms": [min((p["epoch_ms"]-p["audio_start_ms"] for p in e["capture"]["playback"]), default=None),
            max((p["epoch_ms"]-p["audio_start_ms"] for p in e["capture"]["playback"]), default=None)],
        "transport": {"available": bool(transport), "outbound_packets": max((r.get("packetsSent",0) for r in wire if r["type"]=="outbound-rtp"),default=0),
            "inbound_packets": max((r.get("packetsReceived",0) for r in wire if r["type"]=="inbound-rtp"),default=0),
            "source_energy": max((r.get("totalAudioEnergy",0) for r in wire if r["type"]=="media-source"),default=0),
            "sender_disabled": any(not s["enabled"] or s["state"] != "live" for p in transport for s in p["senders"])},
        "floor_received_ms": next((m["epoch_ms"] for m in e["markers"] if m["type"]=="caller.floor_received"),None),
        "context_delivery": result.get("deliveries"), "selection_status": result.get("selection_status"),
        "physical_speaker_verified": False}


def execute(args):
    from app.announcement_eval import runner as r
    from app.jarvis_tasks.acceptance import candidate_identity
    output = r.root_path(args.output)
    if args.stage == "acceptance": raise ValueError("Mechanism verification is not acceptance")
    if args.stage == "report":
        report = json.loads((output / "report.json").read_text(encoding="utf8"))
        if report["phase"] != PHASE: raise ValueError("Wrong verification phase")
        for row in report["runs"]:
            row["audit"] = audit(Path(row["directory"]), row["assessment"])
        r.write(output / "report.json", report); return
    if not args.live or not r.settings.openai_api_key.strip(): raise ValueError("Verification requires --live and existing credentials")
    if not args.offline or not args.prior: raise ValueError("Require passing offline and retained focused phase")
    offline = r.root_path(args.offline)
    current = candidate_identity()
    if json.loads((offline / "candidate.json").read_text(encoding="utf8")) != current: raise ValueError("Candidate changed")
    if not all(c["exit_code"]==0 for c in json.loads((offline / "offline.json").read_text(encoding="utf8"))): raise ValueError("Offline failed")
    if json.loads((offline / "verification-manifest.json").read_text(encoding="utf8")) != manifest(): raise ValueError("Manifest changed")
    corpus = json.loads((offline / "verification-corpus-identity.json").read_text(encoding="utf8"))
    for identity in [*corpus["files"], corpus["wake"], corpus["manifest"], corpus["renderer"]]:
        if r.frozen_identity(Path(identity["path"])) != identity: raise ValueError("Corpus changed")
    prior_path = r.root_path(args.prior) / "report.json"
    prior = json.loads(prior_path.read_text(encoding="utf8"))
    calibration = json.loads((prior_path.parent / "calibration.json").read_text(encoding="utf8"))
    if not calibration["passed"] or calibration["rubric_sha256"] != a.RUBRIC_HASH: raise ValueError("No same-rubric calibration")
    if args.resume:
        if r.root_path(args.resume) != output: raise ValueError("Resume the same phase; no counter reset")
        report = json.loads((output / "report.json").read_text(encoding="utf8"))
        if report["manifest"] != manifest() or report["prior"] != r.frozen_identity(prior_path): raise ValueError("Resume identity mismatch")
        if report["candidate"]["configuration_digest"] != current["configuration_digest"]: raise ValueError("Configuration changed")
        old_files = {Path(x["path"]).name:x["sha256"] for x in report["corpus"]["files"]}
        if old_files != {Path(x["path"]).name:x["sha256"] for x in corpus["files"]}: raise ValueError("Frozen speech changed")
        report.setdefault("candidate_history", []).append(report["candidate"])
        for row in report["runs"]: row.setdefault("recorded_candidate", report["candidate"])
        report["candidate"] = current; report["corpus"] = corpus
        report.setdefault("stop_history",[]).append(report.pop("stopped_reason", "local repair"))
        ledger = r.RequestLedger(output,json.loads((output / "request-ledger.json").read_text(encoding="utf8")))
        for row in report["runs"]:
            row.setdefault("original_assessment", deepcopy(row["assessment"]))
            row["assessment"] = f.evaluate_case(Path(row["directory"]),Path(row["directory"])/"assessment-v2",ledger,live=False)
            # The remaining fragment is explicitly uncertain. Retain its failed
            # evidence qualification; use only raw signal evidence for the control.
            if row["case_id"] == "occupied_control" and row["assessment"]["selection_status"] == "inconclusive":
                d = row["assessment"]["dimensions"]
                if d["evidence_validity"]["unknowns"] and all(x.startswith(("unmatched_audio_words:", "word_timing_across_removed_silence:")) for x in d["evidence_validity"]["unknowns"]) and not row["audit"]["outcomes"]:
                    row["retired_control_lexical_diagnostic"] = True
                else: raise ValueError("Unresolved evidence integrity; cannot resume")
            if row["assessment"]["selection_status"] == "failed":
                report["stopped_conditions"][row["case_id"]] = row["repetition"]
    else:
        output.mkdir(parents=True, exist_ok=False)
        report = {"phase": PHASE, "manifest": manifest(), "candidate": current, "corpus": corpus,
            "prior": r.frozen_identity(prior_path), "calibration": r.frozen_identity(prior_path.parent / "calibration.json"),
            "historical_audits": [], "runs": [], "stopped_conditions": {}}
        ledger = r.RequestLedger(output)
    def save():
        report["usage"] = f.canonical_usage(r.usage_summary(output, ledger))
        report["cumulative_usage"] = f.canonical_usage(r.merge_usage(deepcopy(report["usage"]), prior["cumulative_usage"]))
        ran = {(x["case_id"],x["repetition"]) for x in report["runs"]}
        report["unrun_cells"] = [{"case_id":c["id"],"repetition":c["repetition"]} for c in matrix() if (c["id"],c["repetition"]) not in ran]
        r.write(output / "report.json", report)
    try:
        if not report["historical_audits"]:
            for row in prior["runs"]:
                report["historical_audits"].append({"directory": row["directory"], "audit": audit(Path(row["directory"]), row["assessment"])})
        save()
        for case in matrix():
            condition = case["id"]
            if any(row["case_id"] == condition and row["repetition"] == case["repetition"] for row in report["runs"]): continue
            if condition in report["stopped_conditions"]: continue
            if condition == "occupied_control" and "occupied" in report["stopped_conditions"]: continue
            if len(report["runs"]) >= LIMIT or len(ledger) >= 2*LIMIT: raise ValueError("Verification ceiling reached")
            folder = output / f"verification-{PROFILE}-{condition}-{case['repetition']}-immediate-browser"
            row = {"case_id":condition, "repetition":case["repetition"], "directory":str(folder), "recorded_candidate":current}
            report["runs"].append(row); save()
            print(f"Verification {len(report['runs'])}/{LIMIT}: {condition}/{case['repetition']}",flush=True)
            r.run_case(output,case,PROFILE,"immediate","verification",case["repetition"],offline / "verification-corpus",Path(corpus["wake"]["path"]))
            evidence = f.load_evidence(folder)
            if evidence.get("paid_failure"): raise r.PaidFailure(evidence["paid_failure"])
            row["assessment"] = f.evaluate_case(folder,folder / "assessment-v2",ledger,live=True)
            row["audit"] = audit(folder,row["assessment"]); save()
            if row["assessment"]["selection_status"] == "inconclusive": raise RuntimeError("Evidence inconclusive; pause paid execution")
            if not row["audit"]["transport"]["available"] or row["audit"]["transport"]["sender_disabled"]:
                raise RuntimeError("Missing/invalid transport evidence; stop paid execution")
            if row["assessment"]["selection_status"] == "failed": report["stopped_conditions"][condition] = row["repetition"]
        report["completed_schedule"] = True
    except Exception as error:
        report["stopped_reason"] = str(error); raise
    finally: save()
