"""Optional semantic classifiers; task tools and playback retain authority."""
import json
from hashlib import sha256

import httpx

from app.config import settings
from app.services.conversation_observer import SEMANTIC_POLICY

MODEL = "gpt-6-luna"
PREFERENCE_POLICY = (
    "Evaluate the user's latest request in conversation context, not quoted examples. "
    "Choose requested for a request/promise to report a task outcome, suppressed for a request "
    "for silence, discretionary for explicitly restoring your judgment, incomplete for an "
    "unfinished preference clause, otherwise none. Later instructions supersede earlier ones. "
    "Do not interpret task completion as a new notification request.")
COMMUNICATION_POLICY = (
    "Assess the assistant sentence being spoken at this output transcript event. "
    "Earlier dialogue can resolve references, but an earlier completion must not "
    "make a later unrelated sentence count as a new completion. " + SEMANTIC_POLICY)
POLICY_HASH = sha256((PREFERENCE_POLICY + COMMUNICATION_POLICY).encode()).hexdigest()


def choice(name, instructions, values):
    return {"type": "choice", "name": name, "instructions": instructions,
            "choices": [{"value": v} for v in values]}


def preference_question(variant=None):
    variant = variant or settings.jarvis_decisions_preference_variant
    policy = PREFERENCE_POLICY if variant in {"existing", "separate"} else (
        "Read the latest user words in dialogue context. A task update request or promise is requested; "
        "a correction asking for silence is suppressed; restoring assistant judgment is discretionary. "
        "An unfinished clause is incomplete; unrelated, quoted and hypothetical words are none. "
        "Follow the latest actual preference, not earlier instructions. Reporting that a task finished "
        "does not request notification; asking what a quoted phrase means is not an instruction.")
    return choice("notification_preference", policy,
                  ["requested", "suppressed", "discretionary", "incomplete", "none"])


def preference_questions(variant=None):
    variant = variant or settings.jarvis_decisions_preference_variant
    result = [preference_question(variant)]
    if variant == "separate":
        result.append(choice("preference_scope", "Is the latest user actually giving an instruction about task updates? "
            "Choose task for a concrete notification instruction or correction tied to a task in dialogue, "
            "unresolved for an instruction whose task needs clarification, and none for discussing wording, "
            "asking status, reporting completion or unrelated conversation. Task IDs need not be spoken. "
            "This advisory answer cannot itself change a preference.",
            ["task", "unresolved", "none"]))
    return result


def outcome_question(p, variant=None):
    variant = variant or settings.jarvis_decisions_bookkeeping_variant
    policy = COMMUNICATION_POLICY if variant == "existing" else (
        "Judge what the assistant actually communicated in its latest output, using dialogue to resolve references. "
        "Complete means a finished, accurate statement of this task's terminal result; partial means an unfinished "
        "result statement; none means admission, progress, a promise, question or unrelated speech; uncertain "
        "means the meaning cannot be established. Task facts below are comparison data, NEVER spoken evidence. "
        "Paraphrases are valid. A different task's completion does not count. An announcement of failure "
        "cannot communicate a verified success, or vice versa; classify a contradiction as uncertain. "
        "Do not infer communication merely because the task is complete.")
    return choice("outcome_" + p["presentation_id"], policy + " Verified result (not spoken evidence): " +
        json.dumps(p, ensure_ascii=False), ["complete", "partial", "none", "uncertain"])


def outcome_questions(p, variant=None):
    variant = variant or settings.jarvis_decisions_bookkeeping_variant
    result = [outcome_question(p, variant)]
    if variant == "separate":
        result.append(choice("association_" + p["presentation_id"],
            "Does the assistant's latest outcome statement describe this task goal? Resolve 'it' or 'the page' "
            "from dialogue. Same goal is yes; another goal is no; ambiguous is uncertain. Task IDs need not "
            "be spoken. Goal: " + str(p.get("goal") or p.get("facts",{}).get("goal")),
            ["yes", "no", "uncertain"]))
        result.append(choice("consistency_" + p["presentation_id"],
            "Does the outcome actually asserted by the assistant agree with the verified result below? "
            "A spoken failure contradicts verified success, and spoken success contradicts verified failure: no. "
            "A clear paraphrase of the same result is yes; missing or unclear outcome is uncertain. "
            "The verified result itself is not assistant speech. " + json.dumps(p,ensure_ascii=False),
            ["yes", "no", "uncertain"]))
    return result


def question_identity(presentations, variant):
    return sha256(json.dumps([q for p in presentations for q in outcome_questions(p, variant)],
                             sort_keys=True).encode()).hexdigest()


def outcome_label(answers, p, variant=None):
    variant = variant or settings.jarvis_decisions_bookkeeping_variant
    label = answers.get("outcome_" + p["presentation_id"]) or "uncertain"
    if variant == "separate" and label == "complete" and answers.get("association_"+p["presentation_id"]) != "yes":
        return "uncertain"
    if variant == "separate" and label == "complete" and answers.get("consistency_"+p["presentation_id"]) != "yes":
        return "uncertain"
    return label


def triggers(presentations=(), *, bookkeeping_variant=None):
    if settings.jarvis_conversation_observer_mode != "enabled":
        return []
    result = []
    if settings.jarvis_decisions_preferences:
        result.append({"type": "decisions", "event_type": "session.input_transcript.delta",
                       "decisions": {"questions": preference_questions()}})
    if settings.jarvis_bookkeeper == "decisions" and presentations:
        result.append({"type": "decisions", "event_type": "session.output_transcript.delta",
                       "decisions": {"questions": [q for p in presentations for q in outcome_questions(p, bookkeeping_variant)]}})
    return result


def terminal_context(attention, facts):
    terminal = {"task.completed", "task.failed", "task.interrupted", "task.cancelled"}
    items = [dict(item.get("facts") or item) for item in attention.get("items", []) if item.get("state", "pending") == "pending"]
    values = {p["presentation_id"]:p for p in [*items,*facts]
        if p.get("event_type") in terminal and p.get("presentation_id")}
    return list(values.values())


def windows(evidence, p):
    """Source windows for semantic selection, not invented completed turns."""
    out, current = [], []
    for f in evidence["fragments"]:
        if f["speaker"] != "assistant" or f.get("sequence", 1) <= p.get("exposed_after_sequence", 0):
            continue
        if current and f.get("start_ms") is not None and current[-1].get("end_ms") is not None and f["start_ms"] - current[-1]["end_ms"] > 750:
            out.append(current); current = []
        current.append(f)
        if f["text"].rstrip().endswith((".", "!", "?")):
            out.append(current); current = []
    if current:
        out.append(current)
    return out[-32:]


async def request(input_value, questions):
    async with httpx.AsyncClient(timeout=45, trust_env=False) as client:
        response = await client.post(settings.openai_live_base_url.rstrip("/") + "/decisions",
            headers={"Authorization": "Bearer " + settings.openai_api_key},
            json={"model": MODEL, "input": json.dumps(input_value, ensure_ascii=False), "questions": questions})
        response.raise_for_status()
        raw = response.json()
    return raw, {"request_id": raw.get("id") or response.headers.get("x-request-id"), "model": raw.get("model", MODEL),
                 "usage": raw.get("usage"), "policy_hash": POLICY_HASH,
                 "answers":raw.get("answers",[]),
                 "questions_hash": sha256(json.dumps(questions,sort_keys=True).encode()).hexdigest()}


async def infer(evidence, variant=None):
    questions, candidates = [], {}
    for p in evidence["presentations"]:
        pid = p["presentation_id"]
        candidates[pid] = windows(evidence, p)
        questions.extend(outcome_questions(p, variant))
        questions.append(choice("window_" + pid, "Select the shortest source window establishing your classification; none if no outcome.",
            ["none", *[str(i) for i in range(len(candidates[pid]))]] if candidates[pid] else ["none", "unavailable"]))
    raw, usage = await request({"fragments": evidence["fragments"], "windows": {
        pid: [{"id": str(i), "text": "".join(f["text"] for f in w)} for i,w in enumerate(ws)]
        for pid,ws in candidates.items()}}, questions)
    answers = {a.get("name"): a.get("choice") if a.get("type") == "choice" else None for a in raw.get("answers", [])}
    outcomes = []
    for p in evidence["presentations"]:
        pid = p["presentation_id"]; selected = answers.get("window_" + pid)
        w = candidates[pid][int(selected)] if isinstance(selected, str) and selected.isdecimal() and int(selected) < len(candidates[pid]) else []
        outcomes.append({"presentation_id": pid, "classification": outcome_label(answers, p, variant),
            "fragment_ids": [f["id"] for f in w], "reason": "Decisions semantic source-window assessment"})
    return {"outcomes": outcomes, "flags": []}, usage


def native_assessment(evidence):
    """Consume correlated native results without making a second inference."""
    outcomes, used = [], []
    source_order = {f.get("provider_event_id"): i for i,f in enumerate(evidence["fragments"]) if f.get("provider_event_id")}
    for p in evidence["presentations"]:
        pid = p["presentation_id"]
        matching = [d for d in evidence.get("native_decisions", [])
            if any(x.get("presentation_id") == pid and x.get("source_task_event_id") == p.get("source_task_event_id")
                   for x in d.get("presentations", []))]
        matching = [d for d in matching if d.get("source_event_id") in source_order
                    and d.get("bookkeeping_variant") in {"existing", "concise", "separate"}
                    and d.get("questions_hash") == question_identity(d["presentations"], d["bookkeeping_variant"])]
        # A source_event_id identifies the input to a decision, not evidence that
        # an arbitrary later sentence communicated an earlier result. Retain the
        # latest decision in each growing source window, ordered by source rather
        # than arrival. Later conversation must not replace an earlier citation.
        assessed = {}
        for d in sorted(matching, key=lambda d: source_order[d["source_event_id"]]):
            source = d["source_event_id"]
            observed = {**evidence, "fragments": evidence["fragments"][:source_order[source]+1]}
            w = next((w for w in reversed(windows(observed, p))
                      if any(f.get("provider_event_id") == source for f in w)), [])
            if not w:
                continue
            answers = {a.get("name"): a.get("choice") for a in d.get("event", {}).get("decision", {}).get("answers", [])
                       if a.get("type") == "choice"}
            assessed[w[0]["id"]] = (d, answers, {"presentation_id": pid,
                "classification": outcome_label(answers, p, d["bookkeeping_variant"]), "fragment_ids": [f["id"] for f in w],
                "reason": "Native Decisions output trigger; correlated source window"})
        candidates = list(assessed.values())
        complete = [c for c in candidates if c[2]["classification"] == "complete"]
        selected = complete[0] if complete else candidates[-1] if candidates else None
        # Explicit association/consistency answers can disqualify a contradiction;
        # unrelated partial speech is not a retraction. Never infer this from words.
        contradictions = [c for c in candidates if c[1].get("association_"+pid) == "yes"
                          and c[1].get("consistency_"+pid) == "no"]
        if selected and any(source_order[c[0]["source_event_id"]] > source_order[selected[0]["source_event_id"]]
                            for c in contradictions):
            d, answers, finding = contradictions[-1]
            finding = {**finding, "classification": "uncertain", "reason": "Later associated outcome contradicts verified result"}
        elif selected:
            d, answers, finding = selected
        else:
            d, finding = None, {"presentation_id": pid, "classification": "uncertain", "fragment_ids": [],
                                "reason": "No correlated native source window"}
        outcomes.append(finding)
        used.extend(c[0] for c in candidates)
    return {"outcomes": outcomes, "flags": []}, {"model": MODEL, "policy_hash": POLICY_HASH,
        "native_events": used, "request_origin": "live_trigger"}
