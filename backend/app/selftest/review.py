"""Automated agent review of a recorded voice run.

Builds a readable evidence pack (timed conversation from both recordings,
scenario expectation, task and bookkeeping state) and asks a reviewing model
for a judgment in the user's terms: natural, remembers requests, updates when
relevant, no misleading claims, overlap judged by its effect. Material or
borderline verdicts get a second, independent review. Disagreements are
reported, never auto-resolved. The pack is also written as Markdown so a
coding agent can review it directly.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import sqlite3
import struct
import wave

import httpx

from app.selftest import harness as h

REVIEW_MODEL = "gpt-5.6-luna"
RUBRIC = """You review a recorded conversation between a user and Jarvis, a voice personal assistant.
The owner wants Jarvis to feel like a capable human assistant: it remembers what the user asked,
does the work, tells the user what they asked to hear about at a natural moment, stays quiet when
asked, keeps its promises, and never says misleading things. Overlap or a short delay is not a
failure by itself; judge its effect (did the user miss something, was it disruptive, did Jarvis
recover naturally). Timings come from audio energy and transcripts; treat them as approximate.
Separately judge the bookkeeping: did the records of what was communicated match what actually
reached the user (e.g. an update the user talked over should not be recorded as clearly heard)?
Grade:
- fine: a good human assistant would be happy with this.
- awkward: noticeable but minor issues (wording, small overlap, slight delay, verbosity).
- material: a requested update did not effectively reach the user, misleading information,
  disruptive behaviour, unnecessary repetition that would annoy, a broken promise, or wrong records.
Be concrete and cite times. If the evidence cannot settle something, say so instead of guessing."""
SCHEMA = {"type": "object", "properties": {
    "verdict": {"type": "string", "enum": ["fine", "awkward", "material"]},
    "borderline": {"type": "boolean"},
    "bookkeeping_correct": {"type": "boolean"},
    "issues": {"type": "array", "items": {"type": "object", "properties": {
        "severity": {"type": "string", "enum": ["awkward", "material"]},
        "what": {"type": "string"}, "evidence": {"type": "string"}},
        "required": ["severity", "what", "evidence"], "additionalProperties": False}},
    "summary": {"type": "string"}, "uncertainty": {"type": "string"}},
    "required": ["verdict", "borderline", "bookkeeping_correct", "issues", "summary", "uncertainty"],
    "additionalProperties": False}


def voiced(path: Path, threshold: int = 300, window: float = .05, join: float = .35) -> list[tuple[float, float]]:
    with wave.open(str(path)) as audio:
        rate, channels, frames = audio.getframerate(), audio.getnchannels(), audio.readframes(audio.getnframes())
    samples = struct.unpack("<%dh" % (len(frames) // 2), frames)[::channels]
    step, out, current = int(rate * window), [], None
    for i in range(0, len(samples), step):
        chunk = samples[i:i + step]
        if math.sqrt(sum(x * x for x in chunk) / max(1, len(chunk))) <= threshold:
            continue
        t = i / rate
        if current and t - current[1] < join:
            current[1] = t + window
        else:
            if current:
                out.append(tuple(current))
            current = [t, t + window]
    if current:
        out.append(tuple(current))
    return [(round(a, 2), round(b, 2)) for a, b in out]


def heard_phrases(transcription: dict, gap: float = .8) -> list[tuple[float, float, str]]:
    phrases, current = [], None
    for word in transcription.get("words", []):
        if current and word["start"] - current[1] <= gap:
            current = (current[0], word["end"], current[2] + " " + word["word"])
        else:
            if current:
                phrases.append(current)
            current = (word["start"], word["end"], word["word"])
    if current:
        phrases.append(current)
    return [(round(a, 2), round(b, 2), t.strip()) for a, b, t in phrases]


def database_state(database: Path) -> dict:
    if not database.exists():
        return {}
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        def rows(sql):
            try:
                return [dict(r) for r in db.execute(sql)]
            except sqlite3.Error:
                return []
        presentations = []
        for p in rows("SELECT candidate_type, state, handled_by, required_facts_json FROM task_presentations"):
            facts = json.loads(p.pop("required_facts_json") or "{}")
            presentations.append({**p, "goal": facts.get("goal"), "summary": facts.get("summary"),
                "notification": (facts.get("notification") or {}).get("intent"),
                "assessment": (facts.get("communication_assessment") or {}).get("classification")})
        return {
            "tasks": rows("SELECT goal, status, terminal_summary FROM tasks"),
            "preference_changes": [json.loads(r["payload_json"] or "{}") for r in
                rows("SELECT payload_json FROM task_events WHERE event_type='task.notification_intent.changed' ORDER BY sequence")],
            "presentations": presentations,
            "promises": rows("SELECT text, status, resolution FROM assistant_promises"),
            "away_notifications": rows("SELECT status FROM notification_outbox"),
        }


def evidence_pack(folder: Path, scenario: dict) -> dict:
    evidence = json.loads((folder / "evidence.json").read_text(encoding="utf-8"))
    t0 = evidence.get("assistant_audio_epoch_ms")
    timeline = []
    if t0:
        for marker in evidence.get("markers", []):
            at = round((marker["epoch_ms"] - t0) / 1000, 2)
            if marker["type"] == "caller.start":
                timeline.append((at, f'USER (scripted) starts: "{marker.get("text", "")}"'))
            elif marker["type"] in {"completion", "session.disconnected", "session.resumed", "observation.end"}:
                timeline.append((at, f"EVENT {marker['type']}"))
    if (folder / "caller.wav").exists():
        timeline += [(a, f"user voice {a}-{b}s") for a, b in voiced(folder / "caller.wav")]
    transcription = folder / "assistant-transcription.json"
    if transcription.exists():
        audio = voiced(folder / "assistant.wav") if (folder / "assistant.wav").exists() else []
        for a, b, text in heard_phrases(json.loads(transcription.read_text(encoding="utf-8"))):
            covered = sum(max(0, min(b, y) - max(a, x)) for x, y in audio)
            if covered < .3 * max(b - a, .1):
                continue  # no audio behind it: a transcription artifact, not speech
            timeline.append((a, f'JARVIS (played, transcribed) {a}-{b}s: "{text}"'))
    elif (folder / "assistant.wav").exists():
        timeline += [(a, f"Jarvis voice {a}-{b}s (not transcribed)") for a, b in voiced(folder / "assistant.wav")]
    provider = [e for e in evidence.get("state", {}).get("provider_events", [])
        if e.get("event", {}).get("type") in {"session.input_transcript.delta", "session.output_transcript.delta"}]
    said = {"user": "", "jarvis": ""}
    for event in provider:
        said["user" if "input" in event["event"]["type"] else "jarvis"] += event["event"].get("delta") or ""
    return {"scenario": scenario["id"], "expect": scenario["expect"],
        "timeline": [f"{at:7.2f}s  {text}" for at, text in sorted(timeline)],
        "provider_transcripts": said, "state": database_state(folder / "application.db"),
        "caller_questionnaire": json.loads((folder / "caller-questionnaire.json").read_text(encoding="utf-8"))
            if (folder / "caller-questionnaire.json").exists() else None,
        "harness_error": evidence.get("error")}


def pack_markdown(pack: dict) -> str:
    return "\n".join([f"# Review pack: {pack['scenario']}", "", "## Expected", pack["expect"], "",
        "## Timeline (seconds on the recording clock)", "```", *pack["timeline"], "```", "",
        "## Provider transcripts (text only, timing approximate)", f"User: {pack['provider_transcripts']['user']}",
        f"Jarvis: {pack['provider_transcripts']['jarvis']}", "", "## Final state", "```json",
        json.dumps(pack["state"], indent=2, ensure_ascii=False), "```",
        *(["", "## Simulated caller's own account", json.dumps(pack["caller_questionnaire"], ensure_ascii=False)]
          if pack["caller_questionnaire"] else []),
        *(["", f"Harness error: {pack['harness_error']}"] if pack["harness_error"] else [])])


def ask(client: httpx.Client, ledger: h.RequestLedger, instructions: str, pack_text: str) -> dict:
    result = h.api_request(client, "/responses", ledger, payload={"model": REVIEW_MODEL,
        "reasoning": {"effort": "medium"}, "store": False, "instructions": instructions, "input": pack_text,
        "text": {"format": {"type": "json_schema", "name": "conversation_review", "strict": True, "schema": SCHEMA}}})
    text = "".join(c.get("text", "") for o in result.get("output", []) if o.get("type") == "message"
        for c in o.get("content", []) if c.get("type") == "output_text")
    return json.loads(text)


def review_run(folder: Path, scenario: dict, ledger: h.RequestLedger) -> dict:
    path = folder / "review.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    pack = evidence_pack(folder, scenario)
    text = pack_markdown(pack)
    (folder / "review-pack.md").write_text(text, encoding="utf-8")
    with httpx.Client(timeout=180, trust_env=False) as client:
        first = ask(client, ledger, RUBRIC, text)
        review = {"reviewer": REVIEW_MODEL, **first}
        if first["verdict"] == "material" or first["borderline"]:
            second = ask(client, ledger, RUBRIC + "\nYou are the independent second reviewer. Be skeptical of easy"
                " conclusions in either direction and reach your own verdict from the evidence.", text)
            review["independent"] = second
            review["agreed"] = second["verdict"] == first["verdict"]
    h.write(path, review)
    return review
