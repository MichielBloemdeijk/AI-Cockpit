"""Pure experiment definitions; no production configuration switches."""
from dataclasses import dataclass
from hashlib import sha256
import json

RELEVANCE_POLICY = (
    "Treat task updates as opportunities to inform the user. Decide whether they are relevant now, "
    "worth mentioning later, or need no announcement. Honour requests to be notified and promises "
    "you made. Let the user finish speaking. During another conversation, defer ordinary updates "
    "until an appropriate opening. Respect requests for silence. Avoid repeated announcements and "
    "communicate only verified outcomes. Do not read internal identifiers or URLs aloud."
)
RECONSIDER = (
    "A verified task outcome has just been added to your context. Reconsider whether the user "
    "needs to hear it, using the task-update relevance policy and the current conversation. "
    "You may announce it naturally, defer it, or remain silent."
)
TERMINAL = frozenset({"task.completed", "task.failed", "task.interrupted", "task.cancelled"})
MODEL_ONLY_SEQUENCES = {
    "live_thinking_instruction": ("thinking", "instructions"),
    "live_commentary_instruction": ("commentary", "instructions"),
    "live_instruction_commentary": ("instructions", "commentary"),
    "live_outcome_instruction": ("instructions",),
    "live_thinking_commentary": ("thinking", "commentary"),
    "live_thinking_commentary_instruction": ("thinking", "commentary", "instructions"),
    "live_thinking_receipt_instruction": ("thinking", "instructions"),
    "live_thinking_context": ("thinking",),
    "live_commentary_context": ("commentary",),
    "live_instruction_thinking": ("instructions", "thinking"),
}
MODEL_ONLY_PROFILES = tuple(MODEL_ONLY_SEQUENCES)
MODEL_ONLY_FOLLOWUP_PROFILES = (
    "live_thinking_simple_reconsider", "live_outcome_conditional", "live_thinking_listen_first",
    "live_thinking_strict_floor",
)
MODEL_ONLY_SEQUENCES.update({
    "live_thinking_simple_reconsider": ("thinking", "instructions"),
    "live_outcome_conditional": ("instructions",),
    "live_thinking_listen_first": ("thinking",),
    "live_thinking_strict_floor": ("thinking",),
    "live_ack_probe": ("commentary",),
    "live_thinking_concise": ("thinking",),
})
MODEL_ONLY_LIMIT = 44  # Ten four-case screens, plus two paired timing comparisons.
MODEL_ONLY_RECONSIDER = (
    "Decide whether this newly verified outcome warrants speech now, later, or silence. "
    "Use the latest user preferences and your promises, even if the saved backend preference is stale. "
    "Honour notification requests at the next opening within ten seconds; respect silence and let the user finish. "
    "Never repeat an already communicated outcome. After fully speaking it, delegate acknowledgment using its "
    "delivery metadata. A receipt or composed reply does not establish communication."
)
CONDITIONAL_OUTCOME = (
    "The verified outcome below supersedes earlier admission or queueing reports for this task. "
    "If the user asked to be notified or you promised notification, briefly tell them the verified outcome "
    "at the next opening within ten seconds. If they subsequently requested silence, remain silent. "
    "Otherwise decide yourself whether to announce, defer, or stay quiet. Use the latest conversation "
    "when the saved preference is stale. Let the user finish their description before speaking; do not "
    "take a short pause as permission to interrupt. Never repeat an already communicated outcome."
)
PREFERENCE_PROFILES = frozenset({"thinking_preference", "preference_routed", *MODEL_ONLY_SEQUENCES})
PREFERENCE_RULES = (
    "Save notification intent separately from progress frequency: requested, discretionary, or suppressed. "
    "An explicit request to tell the user when a task finishes, or your own promise to do so, means requested. "
    "An explicit request for silence means suppressed. Otherwise use discretionary. "
    "Include notification_intent and notification_reason in dispatch_task or start_workflow when known. "
    "If a preference arrives after admission, including the rest of the same spoken sentence, "
    "call set_task_update_policy with the latest notification_intent and notification_reason. "
    "Use presentation_policy silent for suppressed; critical_only for requested or discretionary. "
    "Do not merely promise that a changed preference was saved: use the tool first. "
    "Later user preferences supersede earlier ones. Never infer requested from task importance. "
)
LIVE_PROMPT = """You are Jarvis, a concise, natural spoken assistant.
Backchannel policy: Brief listening sounds are permitted. Do not compete with the user's speech.
Interruption policy: Yield when interrupted. Let the user finish their request before a substantive reply.
Delegation policy:
Backend tools: Create and manage durable tasks, screen workflows, task notification preferences, reminders, history, and verified task status.
Delegate to the backend when: The user asks for those capabilities, changes a task or its notification preference, or an answer requires authoritative state.
Do not delegate to the backend when: You can answer from current verified context, or need a short clarification of an undefined outcome.
Delegating early does not end the user's request. Keep listening; delegate later preferences or corrections too.
Do not claim a task is complete until verified completion arrives. Task admission is not completion.
Do not cancel durable work merely because speech was interrupted.
Notification preferences and promises must be saved through the backend. Do not promise a preference change without recording it.
Task update relevance policy: """ + RELEVANCE_POLICY + """
A saved preference describes what the backend recorded. A later user instruction or promise in the conversation takes precedence; delegate its correction before briefing. Requested requires a brief outcome at the next opening; suppressed forbids unsolicited outcomes; discretionary leaves relevance to you.
If interrupted, finish only what the user still needs to hear at a suitable opening. Answer later status questions from verified outcomes, even if previously suppressed.
Only after an outcome has been fully spoken, delegate acknowledgment of that item. Backend text and append receipts do not establish spoken delivery.
"""
LISTEN_FIRST_PROMPT = LIVE_PROMPT.replace(
    "Delegating early does not end the user's request. Keep listening; delegate later preferences or corrections too.",
    "Wait until the user has finished the whole task request, including notification preferences, before delegating admission. "
    "Keep listening through the request; a short pause between sentences does not establish that it is finished. "
    "Save the complete preference with admission. Delegate later corrections too.",
)
STRICT_FLOOR_PROMPT = LISTEN_FIRST_PROMPT.replace(
    "Interruption policy: Yield when interrupted. Let the user finish their request before a substantive reply.",
    "Interruption policy: The user's ongoing description has priority over every backend reply and task outcome. "
    "Do not begin substantive speech while they are describing something, even at a short pause between sentences. "
    "If they resume speaking, yield immediately. Do not resume until they have finished their description. "
    "A request to let them finish applies to all pending task announcements too.",
) + (
    " Background append policy: Internal facts are not a new user turn and need no spoken acknowledgment or thanks. "
    "Do not volunteer a notification promise unless it is appropriate and you intend to honour it. "
    "During an unrelated conversation, normally retain routine completed outcomes for later; "
    "mention them only when a relevant opening exists. Explicit notification requests still require "
    "a brief verified outcome within ten seconds of an available opening after the user finishes. "
    "After interruption, treat an unfinished announcement as uncommunicated and resume only what is still needed."
)
ACK_PROBE_PROMPT = STRICT_FLOOR_PROMPT.replace(
    "Backend tools: Create and manage durable tasks, screen workflows, task notification preferences, reminders, history, and verified task status.",
    "Backend tools: Create and manage durable tasks, screen workflows, task notification preferences, reminders, history, "
    "verified task status, and record spoken task delivery with acknowledge_task_update.",
).replace(
    "Delegate to the backend when: The user asks for those capabilities, changes a task or its notification preference, or an answer requires authoritative state.",
    "Delegate to the backend when: The user asks for those capabilities, changes a task or its notification preference, "
    "or an answer requires authoritative state. Also delegate when you have fully spoken a verified task outcome "
    "and must record that communication, or when your own notification promise has not been saved.",
).replace(
    "Do not delegate to the backend when: You can answer from current verified context, or need a short clarification of an undefined outcome.",
    "Do not delegate to the backend merely to answer from current verified context or clarify an undefined outcome. "
    "This does not forbid delegation to save a preference or to acknowledge an outcome you have actually finished speaking.",
) + (
    " After finishing a task announcement, delegate confirmation of exactly that item using its presentation_id and exposure_token. "
    "This records completed speech; it is not a new task. Do not ask the user to trigger the bookkeeping and do not speak "
    "a second confirmation. Never delegate delivery acknowledgment before you finish speaking or after an interrupted partial announcement."
)


def preference_live_prompt(selected):
    if selected.name == "live_ack_probe":
        return ACK_PROBE_PROMPT
    if selected.name == "live_thinking_strict_floor":
        return STRICT_FLOOR_PROMPT
    return LISTEN_FIRST_PROMPT if selected.name in {"live_thinking_listen_first", "live_thinking_concise"} else LIVE_PROMPT


def reconsider_instruction(selected):
    return RECONSIDER if selected.name == "live_thinking_simple_reconsider" else CONDITIONAL_OUTCOME if selected.name == "live_outcome_conditional" else MODEL_ONLY_RECONSIDER


@dataclass(frozen=True)
class Profile:
    name: str
    channel: str | None
    relevance: bool = False
    reconsider: bool = False
    diagnostic_only: bool = False


PROFILES = {
    p.name: p for p in (
        Profile("baseline", None, diagnostic_only=True),
        Profile("production_A", None),
        Profile("commentary_relevance", "commentary", relevance=True),
        Profile("thinking_relevance", "thinking", relevance=True),
        Profile("thinking_reconsider", "thinking", relevance=True, reconsider=True),
        Profile("commentary_control", "commentary", diagnostic_only=True),
        Profile("thinking_preference", "thinking", relevance=True),
        Profile("preference_routed", "preference", relevance=True),
        *(Profile(name, "thinking", relevance=True, diagnostic_only=name == "live_ack_probe") for name in MODEL_ONLY_SEQUENCES),
    )
}


def profile(name: str) -> Profile:
    if name not in PROFILES:
        raise ValueError(f"Unknown announcement experiment: {name}")
    return PROFILES[name]


def revise_prompt(text: str, selected: Profile) -> str:
    if not selected.relevance:
        return text
    # Replace only the conflicting attention clauses; all admission/action rules remain.
    text = text.replace(
        "Quiet context does not require an immediate announcement, bookkeeping needs no spoken confirmation, ",
        "Bookkeeping needs no spoken confirmation, ",
    ).replace("Quiet context does not require immediate speech; ", "").replace(
        "Quiet context does not require immediate speech;", "")
    # The delegated backend remains on the same acknowledgment contract for comparison.
    return text + " Task update relevance policy: " + RELEVANCE_POLICY


def revise_live_prompt(text: str, selected: Profile, context: dict) -> str:
    if selected.name not in PREFERENCE_PROFILES:
        return revise_prompt(text, selected)
    prompt = preference_live_prompt(selected)
    if context.get("activation_source") == "wake_word":
        prompt += " The interface acknowledged activation. Listen quietly until a substantive user request."
    return prompt


def revise_backend_prompt(text: str, selected: Profile) -> str:
    if selected.name in PREFERENCE_PROFILES:
        text = text.replace(
            "When reviewing pending attention, use get_attention_checklist, mention updates naturally, "
            "and call acknowledge_task_update only after communicating exactly one item. Quiet context does not "
            "require immediate speech; do not announce handled or superseded items or read identifiers aloud. ",
            "Use get_attention_checklist for verified pending outcomes. Never read opaque identifiers aloud. ",
        )
    prompt = revise_prompt(text, selected)
    if selected.name in PREFERENCE_PROFILES:
        prompt += " Notification intent recording: " + PREFERENCE_RULES
        prompt += (
            " The Live assistant owns actual speech. Your generated text is not proof of user communication. "
            "Do not acknowledge_task_update because you composed a reply or received a tool result. "
            "Acknowledge only when Live delegates confirmation that it has finished communicating that item."
        )
    return prompt


def append_commands(command: dict, selected: Profile, preference: dict | None = None) -> list[dict]:
    if selected.channel is None or not str(command.get("event_id", "")).startswith("jarvis-live-update:"):
        return [command]
    content = str(command.get("content", ""))
    offset = content.find("{")
    if offset < 0:
        return [command]
    try:
        fact = json.loads(content[offset:])
    except ValueError:
        return [command]
    if fact.get("event_type") not in TERMINAL:
        return [command]  # Reminders, questions, progress and tombstones keep their contracts.
    if selected.name in PREFERENCE_PROFILES:
        if preference is None:
            raise ValueError("Preference-aware delivery requires authoritative task preference")
        intent = preference["intent"]
        channel = "commentary" if selected.name == "preference_routed" and intent == "requested" else "thinking"
        # Keep opaque delivery handles available without embedding the backend's
        # full task/event records. Human-facing information is short verified text.
        compact = {key: fact[key] for key in ("task_id", "event_type", "presentation_id", "exposure_token") if key in fact}
        compact["notification"] = preference
        summary = " ".join(str(fact.get("summary") or "").split())[:180]
        goal = " ".join(str(fact.get("goal") or "").split())[:90]
        compact["summary"] = summary
        if selected.name == "live_thinking_concise":
            compact["goal"] = goal
            # Identical authority and handles, factual fields once, no runtime policy.
            content = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
            if len(content.encode("utf-8")) > 1600:
                raise ValueError("Concise context exceeds bounded budget")
            return [{**command, "type": "session.thinking.append", "content": content}]
        prefix = {"requested": "The backend recorded a request for a completion announcement. Honour it at the next opening unless a later preference supersedes it.",
            "suppressed": "The backend recorded a request for silence. Keep the outcome for later questions; do not announce it unless a later preference supersedes silence.",
            "discretionary": "The backend currently records discretionary notification. This does not establish that no notification was requested. Check later user instructions and your promises; save any correction before deciding relevance."}[intent]
        content = prefix + f" Verified task: {goal}. Verified outcome: {summary}. Delivery metadata: " + json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
        if len(content.encode("utf-8")) > 1600:
            raise ValueError("Preference-aware append exceeds the bounded context budget")
        if selected.name in MODEL_ONLY_SEQUENCES:
            sequence = MODEL_ONLY_SEQUENCES[selected.name]
            commands = []
            for index, event_channel in enumerate(sequence):
                # The sequence is fixed by the experimental profile, never by
                # notification intent, the grader or application speech rules.
                text = content if event_channel != "instructions" else reconsider_instruction(selected)
                if sequence == ("instructions",):
                    text += " Verified outcome and delivery metadata: " + content
                commands.append({**command, "type": f"session.{event_channel}.append",
                    "event_id": command["event_id"] if index == 0 else command["event_id"] + f":{index}:{event_channel}",
                    "content": text})
            return commands
        return [{**command, "type": f"session.{channel}.append", "content": content}]
    # Retain every factual field; only delivery metadata differs between candidates.
    fact["commentary"] = selected.channel == "commentary"
    amended = {**command, "type": f"session.{selected.channel}.append",
        "content": json.dumps(fact, ensure_ascii=False, separators=(",", ":"))}
    if selected.name == "commentary_control":
        amended["content"] = "Speak this update naturally, then acknowledge it. " + amended["content"]
    commands = [amended]
    if selected.reconsider:
        commands.append({"type": "session.instructions.append", "delegation_id": None,
            "event_id": command["event_id"] + ":reconsider", "content": RECONSIDER})
    return commands


def profile_identity(selected: Profile, timing: str) -> dict:
    if timing not in {"listening", "immediate"}:
        raise ValueError("Unknown delivery timing")
    content = {**selected.__dict__, "timing": timing, "terminal_projection": "all" if selected.channel else "production",
        "policy": RELEVANCE_POLICY if selected.relevance else "",
        "reconsider_instruction": RECONSIDER if selected.reconsider else ""}
    if selected.name in PREFERENCE_PROFILES:
        content.update(preference_context="durable-model-authored-v1", live_prompt=preference_live_prompt(selected),
            preference_rules=PREFERENCE_RULES, acknowledgment_contract="unchanged-observed-separately")
    if selected.name in MODEL_ONLY_SEQUENCES:
        content.update(append_sequence=list(MODEL_ONLY_SEQUENCES[selected.name]),
            receipt_barrier=selected.name == "live_thinking_receipt_instruction",
            reconsider_instruction=reconsider_instruction(selected),
            decision_owner="gpt-live", diagnostic_phase="model-only-context-v1")
        if timing == "immediate":
            content["immediate_context_floors"] = ["thinking", "user_speaking", "assistant_speaking"]
    return {**content, "sha256": sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()}
