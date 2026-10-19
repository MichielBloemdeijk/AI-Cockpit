"""Evidence-based grading. Missing evidence is never interpreted as success."""
from hashlib import sha256
import json
import math
import re
from pathlib import Path
from app.announcement_eval.profiles import PREFERENCE_PROFILES, MODEL_ONLY_PROFILES, MODEL_ONLY_FOLLOWUP_PROFILES, MODEL_ONLY_SEQUENCES

RUBRIC = """Grade only the recorded spoken words against verified application state.
Return a JSON object with claims, relevant, interruption_ok, penalties, and explanation.
claims is a list of objects: task_id, outcome (completed/failed/question/reminder),
start_word, end_word, complete (boolean). Word indices are zero based, inclusive.
Every word has an explicit index: copy those indices instead of counting words.
end_word is the index of the final cited word, never an exclusive boundary.
Cite exact word spans: never cite a provider/backend text that is absent from the audio.
A claim must communicate the outcome and identify the task sufficiently in context.
Promises, acknowledgments, fillers such as 'checking', and backchannels are not outcomes.
Distinguish admission from completion of the worker's objective: 'I created that
task for you', 'the task is queued', and 'I started the task' are NOT completion
claims. Do not put them in claims. Only a statement that the requested result is
ready/finished, or that its execution failed, communicates a terminal outcome.
Do not count a user-requested repetition as an unsolicited duplicate.
An interrupted partial outcome has complete=false. Relevant speech concerns a useful
current result and respects requested silence, changed preferences and the ongoing topic.
interruption_ok excludes assistant takeovers while the caller is speaking, but permits
brief listening backchannels. Silence in discretionary cases is acceptable; it does not
prove an intentional choice. Do not guess intentions or missing audio. Never invent spans.
A request to stay quiet WHEN THE TASK FINISHES forbids unsolicited outcome
announcements, not the admission acknowledgment or a promise to respect silence.
An admission acknowledgment may finish while the independent worker completes;
do not penalize that overlap alone. 'I'll stay quiet when it finishes' is allowed.
For reminder/question regression, cite the actual question and the due reminder separately.
Reading internal identifiers or URLs aloud is unwanted speech; set relevant=false.
For every negative relevance/interruption judgment, cite a penalties entry with
kind (relevance/interruption/repetition), start_word, end_word, and explanation.
Use each word's supplied overlap_with_caller flag for interruptions. Receipt
times of provider transcripts are not microphone speech times. Brief isolated
backchannels such as 'Sure' or 'Okay' are permitted during task admission;
do not mark those as interruptions when the substantive reply follows the caller.
"""
RUBRIC_HASH = sha256(RUBRIC.encode()).hexdigest()
GRADER_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["claims", "relevant", "interruption_ok", "penalties", "explanation"], "properties": {
        "relevant": {"type": "boolean"}, "interruption_ok": {"type": "boolean"}, "explanation": {"type": "string"},
        "claims": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "required": ["task_id", "outcome", "start_word", "end_word", "complete"], "properties": {
                "task_id": {"type": "string"}, "outcome": {"type": "string", "enum": ["completed", "failed", "question", "reminder"]},
                "start_word": {"type": "integer"}, "end_word": {"type": "integer"}, "complete": {"type": "boolean"}}}},
        "penalties": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "required": ["kind", "start_word", "end_word", "explanation"], "properties": {
                "kind": {"type": "string", "enum": ["relevance", "interruption", "repetition"]},
                "start_word": {"type": "integer"}, "end_word": {"type": "integer"}, "explanation": {"type": "string"}}}}}}


def valid_words(words):
    prior = 0.0
    for word in words:
        start, end = word.get("start"), word.get("end")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            return False
        if not math.isfinite(start) or not math.isfinite(end) or start < prior - .25 or end < start:
            return False
        prior = start
    return True


def grade(evidence: dict, audio: dict | None, judgment: dict | None) -> dict:
    failures, unknowns, measurements = [], [], {}
    if evidence.get("error"):
        unknowns.append("harness_error:" + str(evidence["error"]))
    if not evidence.get("input_understood"):
        unknowns.append("intended_spoken_request_not_verified")
    if not evidence.get("clock_valid"):
        unknowns.append("clock_alignment_unverified")
    if not evidence.get("recording_complete") or not evidence.get("playback_verified"):
        unknowns.append("complete_client_playback_evidence_missing")
    if audio is None or judgment is None:
        unknowns.append("audio_or_semantic_grade_missing")
        return {"status": "inconclusive", "failures": failures, "unknowns": unknowns}
    words = audio.get("words", [])
    if not valid_words(words):
        unknowns.append("invalid_audio_word_timestamps")
    claims = judgment.get("claims")
    if not isinstance(claims, list) or judgment.get("relevant") not in {True, False} or judgment.get("interruption_ok") not in {True, False}:
        unknowns.append("invalid_semantic_grade")
        claims = []
    base = evidence.get("assistant_audio_epoch_ms")
    if not isinstance(base, (float, int)):
        unknowns.append("audio_clock_missing")
        base = 0
    tasks = {t["id"]: t for t in evidence.get("state", {}).get("tasks", [])}
    delivered = set()
    provider_events = evidence.get("state", {}).get("provider_events", [])
    receipts = {e["event"].get("client_event_id") for e in provider_events
        if e.get("stream") == "incoming" and e["event"].get("type") in {"session.commentary.appended", "session.thinking.appended", "session.instructions.appended"}}
    if evidence.get("profile") in MODEL_ONLY_SEQUENCES:
        for event in provider_events:
            command = event.get("event", {})
            event_id = command.get("event_id", "")
            if event.get("stream") == "outgoing" and str(event_id).startswith("jarvis-live-update:") and event_id not in receipts:
                unknowns.append("append_receipt_missing:" + event_id)
            if command.get("type") == "append.receipt_barrier.timeout":
                unknowns.append("append_receipt_barrier_timeout")
    if evidence["expectation"] in {"required", "query"} and not evidence.get("target_task_ids"):
        unknowns.append("required_task_identity_missing")
    for event in evidence.get("state", {}).get("provider_events", []):
        command = event.get("event", {})
        if event.get("stream") != "outgoing" or not str(command.get("event_id", "")).startswith("jarvis-live-update:"):
            continue
        content = command.get("content", "")
        try:
            fact = json.loads(content[content.index("{"):])
        except (ValueError, TypeError):
            continue
        task = tasks.get(fact.get("task_id"))
        if task and fact.get("event_type") in {"task.completed", "task.failed"}:
            if command.get("event_id") in receipts: delivered.add(task["id"])
            if task["finished_at"] is None or event["epoch_ms"] < task["finished_at"] - 250:
                failures.append("terminal_context_precedes_durable_completion")
            if task["finished_at"] is not None:
                measured = measurements.setdefault(task["id"], {})
                delay = event["epoch_ms"] - task["finished_at"]
                measured["application_append_delay_ms"] = min(delay, measured.get("application_append_delay_ms", delay))
                measurements[task["id"]]["append_receipt_observed"] = command.get("event_id") in receipts
    if evidence.get("profile") in {"commentary_relevance", "thinking_relevance", "thinking_reconsider"} | PREFERENCE_PROFILES:
        for task_id in evidence.get("target_task_ids", []):
            if tasks.get(task_id, {}).get("finished_at") is not None and task_id not in delivered:
                unknowns.append("terminal_context_not_delivered:" + task_id)
    if evidence.get("profile") in PREFERENCE_PROFILES:
        expected_intent = "suppressed" if evidence["expectation"] == "forbidden" else "requested" if evidence["expectation"] == "required" else None
        # Intent is authored through actual tools, never inferred from silence or
        # the grader's expectations. Check the exact preference on delivery.
        if expected_intent and evidence.get("transport") != "adaptive":
            for task_id in evidence.get("target_task_ids", []):
                appends = []
                for row in provider_events:
                    command = row.get("event", {})
                    if row.get("stream") != "outgoing" or not str(command.get("event_id", "")).startswith("jarvis-live-update:"):
                        continue
                    try:
                        content = command.get("content", "")
                        fact = json.loads(content[content.index("{"):])
                    except (ValueError, TypeError):
                        continue
                    if fact.get("task_id") == task_id and fact.get("event_type") in {"task.completed", "task.failed"}:
                        appends.append(fact)
                if appends and any(f.get("notification", {}).get("intent") != expected_intent for f in appends):
                    failures.append("notification_preference_not_persisted:" + task_id)
    sessions = {(s["id"], s["generation"]) for s in evidence.get("state", {}).get("sessions", [])}
    if "sessions" in evidence.get("state", {}):
        for event in evidence["state"].get("provider_events", []):
            if event.get("session_id") and (event["session_id"], event.get("generation")) not in sessions:
                failures.append("provider_event_session_ownership_mismatch")
    verified = []
    provider_text = " ".join(e.get("event", {}).get("delta", "") for e in evidence.get("state", {}).get("provider_events", [])
        if e.get("event", {}).get("type") == "session.output_transcript.delta")
    provider_tokens = set(re.findall(r"\w+", provider_text.lower()))
    if bool(words) != bool(provider_tokens):
        unknowns.append("provider_speech_audio_evidence_disagreement")
    for claim in claims:
        a, b = claim.get("start_word"), claim.get("end_word")
        if type(a) is not int or type(b) is not int or not 0 <= a <= b < len(words) or claim.get("task_id") not in tasks:
            unknowns.append("uncitable_semantic_claim")
            continue
        if type(claim.get("complete")) is not bool or claim.get("outcome") not in {"completed", "failed", "question", "reminder"}:
            unknowns.append("invalid_claim_outcome")
            continue
        item = {**claim, "start_ms": base + words[a]["start"] * 1000, "end_ms": base + words[b]["end"] * 1000}
        spoken_tokens = re.findall(r"\w+", " ".join(w.get("word", "") for w in words[a:b + 1]).lower())
        if not spoken_tokens or sum(t in provider_tokens for t in spoken_tokens) / len(spoken_tokens) < .7:
            unknowns.append("audio_provider_transcript_disagreement")
        verified.append(item)
        task = tasks[claim["task_id"]]
        if claim["outcome"] in {"completed", "failed"}:
            if task["status"] != claim["outcome"] or task.get("finished_at") is None or item["end_ms"] < task["finished_at"] - 250:
                failures.append("untruthful_or_premature_outcome")
    expectation = evidence["expectation"]
    eligible = evidence.get("eligible_ms", {})
    outcomes = [c for c in verified if c["complete"] and c["outcome"] in {"completed", "failed"}]
    if expectation in {"required", "query"}:
        for task_id in evidence.get("target_task_ids", []):
            candidates = [c for c in outcomes if c["task_id"] == task_id and c["end_ms"] >= eligible.get(task_id, 0)]
            if not candidates:
                failures.append("requested_outcome_not_spoken:" + task_id)
                continue
            latency = min(c["end_ms"] for c in candidates) - eligible[task_id]
            measurements.setdefault(task_id, {})["notification_latency_ms"] = latency
            if latency > 10_250:  # 250 ms clock/ASR tolerance is reported explicitly.
                failures.append("notification_deadline_missed:" + task_id)
    if expectation == "forbidden" and outcomes:
        failures.append("requested_silence_violated")
    if expectation == "query" and any(c["start_ms"] < min(eligible.values(), default=0) - 250 for c in outcomes):
        failures.append("announced_before_status_request")
    maximum = evidence.get("max_announcements")
    if maximum is not None:
        for task_id in evidence.get("target_task_ids", []):
            if sum(c["task_id"] == task_id for c in outcomes) > maximum:
                failures.append("duplicate_announcement:" + task_id)
    if expectation == "regression":
        if not any(c["outcome"] == "question" for c in verified) or not any(c["outcome"] == "reminder" for c in verified):
            failures.append("question_or_reminder_not_spoken")
    penalties = []
    for penalty in judgment.get("penalties", []):
        a, b = penalty.get("start_word"), penalty.get("end_word")
        if type(a) is not int or type(b) is not int or not 0 <= a <= b < len(words):
            unknowns.append("uncitable_semantic_penalty"); continue
        cited_tokens = re.findall(r"\w+", " ".join(w.get("word", "") for w in words[a:b + 1]).lower())
        if not cited_tokens or sum(token in provider_tokens for token in cited_tokens) / len(cited_tokens) < .7:
            unknowns.append("audio_provider_penalty_disagreement")
            continue
        penalties.append(penalty)
    if judgment.get("relevant") is False:
        if any(p["kind"] == "relevance" for p in penalties): failures.append("irrelevant_or_unwanted_speech")
        else: unknowns.append("unsubstantiated_relevance_judgment")
    if judgment.get("interruption_ok") is False:
        windows = caller_windows(evidence)
        supported = False
        for p in penalties:
            if p["kind"] != "interruption": continue
            a, b = p["start_word"], p["end_word"]
            overlapping = [w for w in words[a:b + 1] if any(base + w["start"] * 1000 < end - 100
                and base + w["end"] * 1000 > start + 100 for start, end in windows)]
            if any(w["word"].lower().strip(".,!?") not in {"sure", "okay", "ok", "yes", "right", "mhm", "hmm"}
                    for w in overlapping): supported = True
        if supported: failures.append("conversation_interrupted")
        else: unknowns.append("unsubstantiated_interruption_judgment")
    for presentation in evidence.get("state", {}).get("presentations", []):
        if presentation.get("handled_by") != "model_report":
            continue
        communicated = [c for c in verified if c["task_id"] == presentation["task_id"] and c["complete"]]
        event_type = (presentation.get("required_facts_json") or {}).get("event_type", "")
        if event_type in {"task.completed", "task.failed"}:
            communicated = [c for c in communicated if c["outcome"] == event_type.split(".")[1]]
        at = presentation.get("handled_at")
        # Tool output/append ACK do not substitute for client playback.
        if at is None or not any(c["end_ms"] <= at + 250 for c in communicated):
            failures.append("premature_handled_transition:" + presentation["id"])
    return {"status": "failed" if failures else "inconclusive" if unknowns else "passed",
        "failures": sorted(set(failures)), "unknowns": sorted(set(unknowns)), "measurements": measurements,
        "observed_claims": verified, "observed_no_announcement": not bool(outcomes),
        "silence_intention": "unknown", "rubric_sha256": RUBRIC_HASH,
        "interruption_observation": [{"start_ms": base + words[p["start_word"]]["start"] * 1000,
            "end_ms": base + words[p["end_word"]]["end"] * 1000,
            "before_completion": base + words[p["end_word"]]["end"] * 1000 < min(
                (t["finished_at"] for t in tasks.values() if t.get("finished_at") is not None), default=0)}
            for p in penalties if p["kind"] == "interruption"],
        "acknowledgment_observation": delivery_assessment(evidence, verified)}


def delivery_assessment(evidence, claims):
    """Report delivery and durable attention independently, without auto-handling."""
    rows = []
    for presentation in evidence.get("state", {}).get("presentations", []):
        task_id = presentation.get("task_id")
        complete = [c for c in claims if c.get("task_id") == task_id and c.get("complete")]
        rows.append({"presentation_id": presentation["id"], "task_id": task_id,
            "attention_state": presentation.get("state", "unknown"),
            "spoken_complete": bool(complete), "client_playback_verified": bool(evidence.get("playback_verified")),
            "communication_recorded": presentation.get("state") == "handled",
            "spoken_without_handled": bool(complete) and presentation.get("state") == "pending"})
    return rows


def frozen_identity(path: Path) -> dict:
    content = path.read_bytes()
    return {"path": str(path), "sha256": sha256(content).hexdigest()}


def caller_windows(evidence):
    windows, start = [], None
    for marker in evidence.get("markers", []):
        if marker["type"] == "caller.start": start = marker["epoch_ms"]
        elif marker["type"] == "caller.end" and start is not None:
            windows.append((start, marker["epoch_ms"])); start = None
    return evidence.get("caller_audio_windows", windows)


def recommendation(rows: list[dict], *, final: bool = False) -> dict:
    candidates = (*MODEL_ONLY_PROFILES, *MODEL_ONLY_FOLLOWUP_PROFILES, "thinking_preference", "preference_routed", "commentary_relevance", "thinking_relevance", "thinking_reconsider")
    for name in candidates:
        selected = [r for r in rows if r["profile"] == name and r.get("cohort") == ("acceptance" if final else "pilot")]
        ids = {r["case_id"] for r in selected}
        expected = {"explicit_idle", "silence", "occupied"}
        if name in PREFERENCE_PROFILES:
            expected.add("changed_silence")
        if final:
            corpus = json.loads(Path(__file__).with_name("corpus.json").read_text(encoding="utf-8"))
            expected_cases = {(c["id"], "browser", n, None) for c in corpus["cases"] for n in (1, 2, 3)}
            expected_cases.update((a["family"], "adaptive", n + 4, a["id"]) for n, a in enumerate(corpus["adaptive"]))
            expected_cases.update((id, "android", 1, None) for id in corpus["android"])
            actual = {(r["case_id"], r.get("transport"), r.get("repetition"), r.get("adaptive_id")) for r in selected}
            complete = len(selected) == 48 and actual == expected_cases
        else:
            complete = len(selected) == len(expected) and ids == expected
        if complete and all(r["grade"]["status"] == "passed" for r in selected):
            return {"profile": name, "status": "qualified" if final else "screened", "production_changed": False}
    return {"profile": None, "status": "no_qualifying_candidate", "production_changed": False}
