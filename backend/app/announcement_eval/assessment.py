"""Versioned, span-based evaluation; never changes model or presentation state."""
from difflib import SequenceMatcher
from hashlib import sha256
import array
import json
import re
import wave

from app.announcement_eval.grading import valid_words

KINDS = ("admission", "progress", "promise", "backchannel", "outcome", "conversation", "ambiguous")
RUBRIC = """Classify every supplied assistant audio word in ordered, nonoverlapping spans.
Return utterances covering all word indices exactly once. Indices are zero based,
inclusive. Split changes of meaning; do not combine admission with an outcome.
kind: admission (task created/added/queued/started), progress (work still ongoing),
promise (future notification/action), backchannel (brief listening acknowledgments),
outcome (requested work finished/ready or execution failed), conversation (other
substantive speech), ambiguous (meaning not established). A solitary 'Done' is
ambiguous unless surrounding words explicitly establish completion of the requested
worker objective. 'Your task has been added' is ADMISSION, even after the worker
has finished. Never infer outcome from task state or time alone. Partial outcome
must actually assert terminal result; merely naming the page is not an outcome.
For outcome only set outcome=completed/failed and task_id to its verified identity;
all other kinds use outcome=none, task_id='', complete=false. A full, accurate
outcome has complete=true. A cutoff before it communicates the result is incomplete.
relevant evaluates the caller's latest request and topic. Silence requested WHEN
THE TASK FINISHES forbids unsolicited outcome announcements, not admission or a
promise to stay quiet. An answer to a later direct status question is solicited.
Do not decide interruption from receipt timestamps. We check actual recorded audio
and playback separately. Brief listening backchannels are allowed. Cite supplied
words only. Any unrecognizable or unsupported speech is ambiguous. Do not invent
intention, words, missing outcomes or failure claims. Give a brief explanation for
each span. Ignore profile/configuration; they are intentionally withheld.
"""
RUBRIC_HASH = sha256(RUBRIC.encode()).hexdigest()
SPAN = {"type": "object", "additionalProperties": False,
    "required": ["start_word", "end_word", "kind", "outcome", "task_id", "complete", "relevant", "explanation"],
    "properties": {"start_word": {"type": "integer"}, "end_word": {"type": "integer"},
        "kind": {"type": "string", "enum": list(KINDS)},
        "outcome": {"type": "string", "enum": ["completed", "failed", "none"]},
        "task_id": {"type": "string"}, "complete": {"type": "boolean"},
        "relevant": {"type": "boolean"}, "explanation": {"type": "string"}}}
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["utterances"],
    "properties": {"utterances": {"type": "array", "items": SPAN}}}


def tokens(text):
    text = str(text).lower().replace("’", "'")
    text = re.sub(r"\bweb[- ]page\b","webpage",text)
    text = re.sub(r"\bcued\b","queued",text)
    text = re.sub(r"\bmm-h\b","listeningsound",text)
    # Homophonic typography and conventional affirmative spellings are not new
    # speech claims. Do not normalize cut-off lexical prefixes or paraphrases.
    text = re.sub(r"(?<=\w)'s\b", "s", text)
    text = re.sub(r"\b(?:m-kay|mmkay|mkay)\b", "okay", text)
    # Ordered occurrences still matter; an additional sound remains unsupported.
    text = re.sub(r"\b(?:mm[- ]?hmm|mm[- ]?hm|mhm|hmm|mmhmm)\b", "listeningsound", text)
    return re.findall(r"[a-z0-9]+", text)


def align_words(words, provider):
    """Order-aware matching. Repeated words cannot borrow evidence from earlier speech."""
    audio_tokens, owners = [], []
    i = 0
    while i < len(words):
        word = words[i]
        part = tokens(word.get("word", ""))
        # Whisper sometimes splits one nonlexical 'Mm-hmm' into two word entries.
        # Preserve both owners and ordered occurrences rather than invent a word.
        if part == ["mm"] and i+1 < len(words) and tokens(words[i+1].get("word", "")) == ["listeningsound"]:
            audio_tokens.append("listeningsound"); owners.append((i, i+1)); i += 2; continue
        audio_tokens.extend(part); owners.extend([(i,)] * len(part)); i += 1
    matches = SequenceMatcher(None, audio_tokens, tokens(provider), autojunk=False).get_matching_blocks()
    aligned = {i for match in matches for i in range(match.a, match.a + match.size)}
    return [sum(i in aligned for i, owner in enumerate(owners) if n in owner) /
        max(1, sum(n in owner for owner in owners)) if tokens(words[n].get("word", "")) else 1
        for n in range(len(words))]


def energy_windows(path, *, pad=0, join=.08):
    """Fixed threshold, 10 ms frames; signal evidence, not inferred conversation intent."""
    with wave.open(str(path)) as audio:
        if audio.getnchannels() != 1 or audio.getsampwidth() != 2:
            raise ValueError("Expected mono PCM16 audio")
        rate = audio.getframerate()
        samples = array.array("h", audio.readframes(audio.getnframes()))
    step, windows = max(1, rate // 100), []
    for i in range(0, len(samples), step):
        chunk = samples[i:i + step]
        if sum(abs(v) > 200 for v in chunk) <= len(chunk) * .05:
            continue
        start, end = max(0, i / rate - pad), min(len(samples) / rate, (i + len(chunk)) / rate + pad)
        if windows and start <= windows[-1][1] + join:
            windows[-1][1] = end
        else:
            windows.append([start, end])
    return windows


def pack_audio(source, target):
    """One transcription/request per case; remove long silence with reversible timing."""
    intervals = energy_windows(source, pad=.25, join=.35)
    mapping, offset = [], 0
    with wave.open(str(source)) as original:
        params = original.getparams(); rate = original.getframerate()
        with wave.open(str(target), "wb") as packed:
            packed.setparams(params)
            for start, end in intervals:
                a, b = round(start * rate), round(end * rate)
                original.setpos(a); packed.writeframes(original.readframes(b - a))
                mapping.append({"packed_start": offset / rate, "packed_end": (offset + b - a) / rate,
                    "original_start": a / rate, "original_end": b / rate})
                offset += b - a
    return mapping


def restore_words(transcript, mapping):
    restored = json.loads(json.dumps(transcript))
    for word in restored.get("words", []):
        original = {}
        region_indices = []
        for edge in ("start", "end"):
            value = word[edge]
            regions = [m for m in mapping if m["packed_start"] - .001 <= value <= m["packed_end"] + .001]
            if not regions:
                raise ValueError("Transcription word outside packed audio")
            m = regions[-1] if edge == "start" else regions[0]
            region_indices.append(mapping.index(m))
            original[edge] = m["original_start"] + value - m["packed_start"]
        word.update(original)
        if region_indices[0] != region_indices[1] and any(mapping[i+1]["original_start"] - mapping[i]["original_end"] > .001
                for i in range(region_indices[0],region_indices[1])):
            word["packed_gap_crossing"] = True
    restored["timing_restored"] = True
    return restored


def playback_span(evidence, start, end):
    capture = evidence.get("capture") or {}
    samples = capture.get("playback", [])
    if not samples or not capture.get("rate"):
        return None
    frames = [p for p in samples if p.get("audio_end_ms", p.get("epoch_ms", 0)) >= start
        and p.get("audio_start_ms", p.get("epoch_ms", 0)) <= end]
    required = ("audio_start_ms", "audio_end_ms", "muted", "volume", "track_id", "position")
    if not frames or any(any(k not in p for k in required) for p in frames):
        return None  # Earlier recorder cannot retrospectively prove utterance playback.
    frames.sort(key=lambda p: p["audio_start_ms"])
    cursor, prior = start, None
    for p in frames:
        if p["audio_start_ms"] > cursor + 100:
            return None
        if p.get("paused") or p["muted"] or p["volume"] <= 0 or not p.get("track_live"):
            return False
        if prior and (p["track_id"] != prior["track_id"] or p["position"] < prior["position"]):
            return None
        cursor = max(cursor, p["audio_end_ms"]); prior = p
    if cursor < end - 100 or frames[-1]["position"] <= frames[0]["position"]:
        return None
    return True


def status(failures, unknowns):
    return "inconclusive" if unknowns else "failed" if failures else "passed"


def assess(evidence, audio, judgment):
    names = ("evidence_validity", "context_delivery", "notification_selection", "deadline", "task_truth",
        "conversation_interruption", "preference_persistence", "acknowledgment")
    dimensions = {n: {"failures": [], "unknowns": []} for n in names}
    def fail(n, value): dimensions[n]["failures"].append(value)
    def unknown(n, value): dimensions[n]["unknowns"].append(value)
    for flag in ("input_understood", "clock_valid", "recording_complete"):
        if not evidence.get(flag): unknown("evidence_validity", flag)
    if evidence.get("error") or evidence.get("capture_error"):
        unknown("evidence_validity", evidence.get("error") or evidence["capture_error"])
    words = (audio or {}).get("words", [])
    if audio is None or judgment is None or not valid_words(words):
        unknown("evidence_validity", "audio_or_valid_semantic_grade_missing")
    events = evidence.get("state", {}).get("provider_events", [])
    provider = "".join(e.get("event", {}).get("delta", "") for e in events
        if e.get("event", {}).get("type") == "session.output_transcript.delta")
    alignment = align_words(words, provider)
    if bool(words) != bool(tokens(provider)):
        unknown("evidence_validity", "provider_speech_audio_disagreement")
    unsupported = [i for i, score in enumerate(alignment) if score < .8]
    if unsupported: unknown("evidence_validity", "unmatched_audio_words:" + str(unsupported))
    audio_tokens = tokens(" ".join(w.get("word","") for w in words))
    provider_tokens = tokens(provider)
    supported_provider = {i for m in SequenceMatcher(None,audio_tokens,provider_tokens,autojunk=False).get_matching_blocks()
        for i in range(m.b,m.b+m.size)}
    nonlexical = {"listeningsound","mm","okay","ok","hi","hey","yeah","alright","understood"}
    omitted = [i for i,t in enumerate(provider_tokens) if i not in supported_provider and t not in nonlexical]
    if omitted: unknown("evidence_validity","provider_words_missing_from_audio_transcription:"+str(omitted))
    uncertain_listening = [i for i,t in enumerate(provider_tokens) if i not in supported_provider and t in {"hi","hey","understood"}]
    if uncertain_listening: unknown("conversation_interruption","untranscribed_greeting_or_listening_acknowledgment:"+str(uncertain_listening))
    base = evidence.get("assistant_audio_epoch_ms", 0)
    tasks = {t["id"]: t for t in evidence.get("state", {}).get("tasks", [])}
    target = evidence.get("target_task_ids", [])
    expectation = evidence.get("expectation", "optional")
    receipt_events = {e["event"].get("client_event_id"): e for e in events
        if e.get("stream") == "incoming" and e["event"].get("type") in
        {"session.thinking.appended", "session.commentary.appended", "session.instructions.appended"}}
    deliveries, spans, overlaps, covered = {}, [], [], []
    sessions = {(s["id"], s["generation"]) for s in evidence.get("state", {}).get("sessions", [])}
    for event in events:
        if event.get("session_id") and "sessions" in evidence.get("state", {}) and (event["session_id"], event.get("generation")) not in sessions:
            fail("evidence_validity", "session_ownership_mismatch")
        c = event.get("event", {})
        if event.get("stream") != "outgoing" or not str(c.get("event_id", "")).startswith("jarvis-live-update:"):
            continue
        try:
            text = c.get("content", ""); fact = json.loads(text[text.index("{"):])
        except (ValueError, TypeError): continue
        tid = fact.get("task_id")
        if tid not in tasks or fact.get("event_type") not in {"task.completed", "task.failed"}: continue
        receipt = receipt_events.get(c["event_id"])
        deliveries.setdefault(tid, []).append({"sent_ms": event["epoch_ms"],
            "receipt_ms": receipt["epoch_ms"] if receipt else None, "fact": fact})
        if receipt is None: unknown("context_delivery", "receipt_missing:" + c["event_id"])
        if tasks[tid].get("finished_at") is None or event["epoch_ms"] < tasks[tid]["finished_at"] - 250:
            fail("task_truth", "terminal_context_precedes_completion")
    for tid in target:
        if tasks.get(tid, {}).get("finished_at") and not deliveries.get(tid):
            unknown("context_delivery", "terminal_context_not_delivered:" + tid)
        intended = "suppressed" if expectation == "forbidden" else "requested" if expectation == "required" else None
        if intended and any(d["fact"].get("notification", {}).get("intent") != intended for d in deliveries.get(tid, [])):
            fail("preference_persistence", "incorrect_saved_preference:" + tid)
    caller_turns, begin = [], None
    for m in evidence.get("markers", []):
        if m["type"] == "caller.start": begin = m["epoch_ms"]
        elif m["type"] == "caller.end" and begin is not None:
            caller_turns.append((begin, m["epoch_ms"])); begin = None
    windows = evidence.get("caller_voiced_windows")
    if windows is None: unknown("conversation_interruption", "caller_voiced_intervals_missing")
    for u in (judgment or {}).get("utterances", []):
        a, b = u.get("start_word"), u.get("end_word")
        if type(a) is not int or type(b) is not int or not 0 <= a <= b < len(words):
            unknown("evidence_validity", "invalid_span"); continue
        covered.extend(range(a, b + 1))
        item = {**u, "text": " ".join(w["word"] for w in words[a:b + 1]),
            "start_ms": base + words[a]["start"] * 1000, "end_ms": base + words[b]["end"] * 1000}
        item["word_timing_gap_indices"] = [i for i in range(a,b+1) if words[i].get("packed_gap_crossing")]
        # Internal word boundaries do not change the utterance's start/end or its
        # full playback deadline. A crossed END boundary can inflate its span.
        uncertain_timing = bool(words[b].get("packed_gap_crossing") and tokens(words[b].get("word", "")))
        if uncertain_timing:
            unknown("evidence_validity" if u.get("kind") == "outcome" else "conversation_interruption",
                "word_timing_across_removed_silence:" + str(a))
        item["client_playback"] = playback_span(evidence, item["start_ms"], item["end_ms"])
        if item["client_playback"] is None: unknown("evidence_validity", "utterance_playback_unverified:" + str(a))
        elif item["client_playback"] is False: fail("evidence_validity", "utterance_not_played:" + str(a))
        if u.get("kind") not in KINDS: unknown("evidence_validity", "unknown_kind")
        if u.get("kind") != "outcome" and (u.get("outcome") != "none" or u.get("complete")):
            unknown("evidence_validity", "non_outcome_with_terminal_claim")
        if u.get("kind") == "outcome":
            # A second deterministic check prevents the observed admission error.
            text = item["text"].lower()
            explicit = bool(re.search(r"\b(ready|finished|complete|completed|failed|could not|couldn't|unable)\b", text)) or (
                "done" in tokens(text) and len(tokens(text)) > 1)
            if not explicit:
                unknown("task_truth", "terminal_claim_requires_explicit_result:" + str(a))
            tid = u.get("task_id"); task = tasks.get(tid)
            if not task: unknown("task_truth", "unknown_claimed_task")
            elif task["status"] != u["outcome"] or task.get("finished_at") is None or item["start_ms"] < task["finished_at"] - 250:
                fail("task_truth", "unverified_or_premature_outcome:" + str(a))
            if not u.get("relevant", True): fail("notification_selection", "irrelevant_task_outcome:" + str(a))
        actual = [[max(item["start_ms"], s), min(item["end_ms"], e)] for s, e in windows or []
            if max(item["start_ms"], s) < min(item["end_ms"], e)]
        brief = u.get("kind") == "backchannel" and b - a < 5 and item["end_ms"] - item["start_ms"] <= 1500
        if actual and not brief and not uncertain_timing:
            overlap_ms = sum(e - s for s, e in actual)
            if overlap_ms > 100:
                fail("conversation_interruption", "substantive_overlap:" + str(a))
                query_start = evidence.get("status_query_start_ms")
                if query_start is None and evidence.get("status_query_end_ms") is not None:
                    query_start = max((m["epoch_ms"] for m in evidence.get("markers", []) if m["type"] == "caller.start"
                        and m["epoch_ms"] <= evidence["status_query_end_ms"]), default=evidence["status_query_end_ms"])
                if u.get("kind") == "outcome" and (query_start is None or item["start_ms"] < query_start):
                    fail("notification_selection", "outcome_interrupts_caller:" + str(a))
        overlaps.append({"start_word": a, "kind": u.get("kind"), "voiced_overlap_ms": sum(e-s for s,e in actual),
            "brief_backchannel": brief, "timing_uncertain": uncertain_timing, "turn_relationship": "starts_over_caller" if any(s <= item["start_ms"] < e for s,e in windows or [])
                else "caller_barges_in" if actual else "within_turn_pause" if any(s <= item["start_ms"] < e for s,e in caller_turns) else "opening"})
        spans.append(item)
    if expectation == "required" and any("the page can wait" in m.get("text", "").lower()
            for m in evidence.get("markers", []) if m["type"] == "caller.start"):
        unknown("evidence_validity", "script_contains_competing_notification_preference")
    settled = max((m["epoch_ms"] for m in evidence.get("markers", []) if m["type"] == "admission.settled"), default=None)
    completed = min((t["finished_at"] for t in tasks.values() if t.get("finished_at") is not None), default=None)
    # Post-hoc audit of the fixture's promised idle precondition. Original speech
    # remains evidence, but a late admission cannot qualify an idle comparison.
    if settled is not None and completed is not None and any(s["kind"] == "admission" and
            settled - 250 <= s["start_ms"] <= completed + 2500 and s["end_ms"] >= completed for s in spans):
        unknown("evidence_validity", "admission_resumed_after_settle")
    if covered != list(range(len(words))): unknown("evidence_validity", "semantic_spans_not_exact_partition")
    outcomes = [s for s in spans if s["kind"] == "outcome" and s["complete"]]
    query_end = evidence.get("status_query_end_ms")
    query_start = evidence.get("status_query_start_ms")
    if query_end is not None and query_start is None:
        query_start = max((m["epoch_ms"] for m in evidence.get("markers", [])
            if m["type"] == "caller.start" and m["epoch_ms"] <= query_end), default=query_end)
    observation_end = evidence.get("observation_end_ms", float("inf"))
    before_query = [s for s in outcomes if s["start_ms"] < (query_start or float("inf"))]
    for tid in target:
        if sum(s["task_id"] == tid for s in before_query) > 1:
            fail("notification_selection", "duplicate_unsolicited_outcome:" + tid)
    if expectation == "forbidden" and before_query: fail("notification_selection", "requested_silence_violated")
    latencies, clock_origins, nominal_origins = {}, {}, {}
    if expectation in {"required", "query"} or query_end:
        for tid in target:
            eligible = query_end or evidence.get("eligible_ms", {}).get(tid)
            nominal_origins[tid] = eligible
            # A rendered clip includes trailing silence. Use the last actual
            # caller sound in the intended final turn, keeping completion as the
            # earliest possible task-outcome origin. Never move an idle origin
            # to context transmission or provider receipt.
            if eligible is not None and windows:
                turn = next(((s,e) for s,e in reversed(caller_turns) if e-1 <= eligible <= e+500
                    and (query_end is not None or (tasks.get(tid,{}).get("finished_at") or float("inf")) <= e)),None)
                if turn:
                    voiced_end = max((min(e,turn[1]) for s,e in windows if s < turn[1] and e > turn[0]),default=None)
                    if voiced_end is not None:
                        eligible = voiced_end if query_end is not None else max(tasks[tid]["finished_at"],voiced_end)
            clock_origins[tid] = eligible
            possible = [s for s in outcomes if s["task_id"] == tid and eligible is not None and s["end_ms"] >= eligible
                and (query_end is not None or s["end_ms"] <= observation_end)]
            if not possible:
                fail("notification_selection", "requested_outcome_not_spoken:" + tid)
                fail("deadline", "requested_outcome_missing:" + tid); continue
            latency = min(s["end_ms"] for s in possible) - eligible
            latencies[tid] = latency
            if latency > 10250: fail("deadline", "notification_deadline_missed:" + tid)
    attention = []
    for p in evidence.get("state", {}).get("presentations", []):
        spoken = [s for s in outcomes if s["task_id"] == p["task_id"] and s["client_playback"] is True]
        attention.append({"presentation_id": p["id"], "spoken_complete": bool(spoken), "state": p.get("state")})
        if p.get("handled_by") == "model_report" and (p.get("handled_at") is None or not any(s["end_ms"] <= p["handled_at"] + 250 for s in spoken)):
            fail("acknowledgment", "premature_handled_transition:" + p["id"])
        elif spoken and p.get("state") != "handled": fail("acknowledgment", "spoken_outcome_not_acknowledged:" + p["id"])
    for d in dimensions.values(): d["status"] = status(d["failures"], d["unknowns"])
    core = ("evidence_validity", "context_delivery", "notification_selection", "deadline", "task_truth")
    core_unknown = any(dimensions[n]["unknowns"] for n in core)
    core_fail = any(dimensions[n]["failures"] for n in core)
    selection = "inconclusive" if core_unknown else "failed" if core_fail else "passed"
    interaction = status([v for d in dimensions.values() for v in d["failures"]], [v for d in dimensions.values() for v in d["unknowns"]])
    return {"schema": "jarvis-announcement-assessment.v2", "rubric_sha256": RUBRIC_HASH,
        "dimensions": dimensions, "selection_status": selection, "interaction_status": interaction,
        "conclusion": "inconclusive" if selection == "inconclusive" else "complete_interaction_qualified" if interaction == "passed"
            else "selection_demonstrated_in_screening" if selection == "passed" else "selection_not_demonstrated",
        "utterances": spans, "interruption_observations": overlaps, "deliveries": deliveries,
        "notification_latency_ms": latencies, "notification_clock_origins":clock_origins,
        "nominal_clock_origins":nominal_origins,"timing_basis":"durable completion or actual caller speech ending; 250ms tolerance",
        "acknowledgment_observations": attention, "silence_intention": "unknown"}
