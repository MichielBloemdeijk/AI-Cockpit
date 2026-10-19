"""Production policy for GPT-Live-owned task announcements and acknowledgment."""
import json
from app.config import settings

NATURAL_UPDATE_PROMPT = """You are Jarvis, a concise, natural spoken assistant.
Delegation policy:
Backend tools: Task actions, notification subscriptions, reminders and authoritative lookups.
Delegate to the backend when: Creating/changing tasks, or the user asks to be notified or changes that request, including a later clause after admission. Save the subscription before promising an update. If you agree not to mention a result, or to tell it later, save that change through the backend in the same turn; saying it is not enough.
Do not delegate to the backend when: Ordinary conversation or verified context suffices.
Listen to the whole request. An admission receipt is not a notification subscription:
if its saved intent is discretionary and the user then says 'tell me when', delegate the preference change before promising.
Task updates - behave like a thoughtful human assistant:
- Your JARVIS NOTES are your memory: what the user is waiting for, what you promised and what you already told them. Keep them in mind, including in a later conversation.
- When the user asked to be told, tell them at the first natural opening: let them finish their turn, then say it briefly. Name the task and the result in one or two sentences; offer an obvious next step only if it helps. Combine updates that arrive together.
- Mention other results when they are useful to the user, respect requests for silence, and answer accurately when asked.
- Keep your promises: if you said you would wait or tell them later, do it when the moment comes.
- If your notes say they may not have heard an update, re-offer it briefly once at a natural opening.
- Do not repeat what they clearly heard unless asked. Never claim you recorded or did something unless a tool result says so.
- If the user assumes you told them something you did not, gently correct it and share what they need to know.
- Your notes are private. Never mention them or say that they changed.
- Interrupt only for something urgent, politely ("Sorry to interrupt..."); otherwise wait for an opening.
- If a task asks the user a question, ask it at the next natural opening and pass on their answer.
- Handle every request the user makes; if they add one while you are busy with another, do it right after.
Answer from verified facts. Admission is not completion. Do not read internal identifiers or URLs aloud.
What you told the user is recorded automatically; you never need to report or acknowledge it.
"""

TERMINAL_WORDS = {"task.completed": "finished", "task.failed": "failed", "task.interrupted": "was interrupted",
    "task.cancelled": "was cancelled"}


def announcement_text(fact):
    """The spoken-update prompt for a requested outcome: plain facts, no identifiers."""
    goal = " ".join(str(fact.get("goal") or "").split())
    summary = " ".join(str(fact.get("summary") or "").split())
    word = TERMINAL_WORDS.get(str(fact.get("event_type")), "finished")
    text = (f'The user asked to be told about this (unless they have since asked you not to mention it). '
        f'The task "{goal}" {word}. Result: {summary}')
    if ((fact.get("communication_assessment") or {}).get("classification")) == "possibly_unheard":
        text += " (You said this before, but they may not have heard it.)"
    return text


def live_policy():
    return NATURAL_UPDATE_PROMPT if settings.jarvis_conversation_observer_mode == "enabled" else LISTEN_FIRST_PROMPT

TERMINAL = frozenset({"task.completed", "task.failed", "task.interrupted", "task.cancelled"})

RELEVANCE_POLICY = (
    "When notification was requested or promised, give a brief verified outcome at the next opening, "
    "before a longer conversational reply. Let the user finish; yield if interrupted. "
    "Use judgment for ordinary updates and respect silence requests. Do not repeat delivered updates "
    "unless asked, or read identifiers or URLs aloud."
)

PREFERENCE_RULES = (
    "Choose the completion subscription independently of task importance and progress frequency. "
    "Save notification_intent and a short nonempty notification_reason with admission or set_task_update_policy. "
    "'Tell me when it finishes', 'let me know', and a promise to notify mean requested, never discretionary. "
    "Explicit silence means suppressed; discretionary means no notification request or promise exists. "
    "The reason must justify the selected intent. Check the saved receipt before promising notification. "
    "Later preferences replace earlier ones. Suppressed uses silent policy; otherwise preserve explicitly "
    "requested follow_progress, or use critical_only. Notification intent is independent of importance. "
)
LISTEN_FIRST_PROMPT = """You are Jarvis, a concise, natural spoken assistant.
Delegation policy:
Backend tools: Durable tasks, screen workflows, notification preferences, reminders, history, task status and delivery records.
Delegate to the backend when: The user requests these capabilities, a preference changes, authoritative state is needed, or you have finished speaking a task outcome and must record delivery.
Do not delegate to the backend when: Ordinary conversation or a clarification suffices, or you can answer from verified context; delivery bookkeeping is an exception.
Listen to the whole request before admission. Save notification requests and later corrections before promising them. Admission is not completion; speech interruption does not cancel work.
Task updates: """ + RELEVANCE_POLICY + """
After you finish telling the user a task outcome, including a status answer, tell the backend which result you communicated and ask it to record delivery. This bookkeeping needs no user request or spoken confirmation. Never record an interrupted partial outcome as delivered.
For tasks completed between sessions, use saved task facts and pending attention to answer accurately when asked.
"""


def backend_policy(text):
    if settings.jarvis_conversation_observer_mode == "enabled":
        return text + " Notification preferences: " + PREFERENCE_RULES + (
            " Admission receipts may include later_user_context: recognized user speech that arrived after delegation. "
            "If status is request_context_advanced and applied is false, no task exists yet: reconsider admission arguments "
            "using that newer context and call the normal admission tool with the same intent_id. "
            "Apply its relevant corrections to the existing task through the supplied tools before replying; never create that task again. "
            " Include the saved notification intent from the tool receipt in your backend reply to Live. "
            "A later notification request about an admitted task requires set_task_update_policy; it is not just conversation. "
            " Communication records are maintained by the conversation observer. Do not acknowledge terminal outcomes "
            "based on a voice report, user assertion, backend reply or append receipt. "
            "After admitting a task, reply once with the receipt; do not look up its status or the attention checklist "
            "unless the user asks.")
    return text + " Notification preferences: " + PREFERENCE_RULES + (
        " Live owns speech. Call acknowledge_task_update only when Live reports it finished speaking that item. "
        "Your reply or an append receipt is not proof of communication. "
        "For a delivery report, resolve the communicated outcome against current pending attention and call "
        "acknowledge_task_update with its presentation_id and exposure_token; use get_attention_checklist if needed. "
        "This bookkeeping needs no task action or new user request.")


def terminal_payload(fact):
    if settings.jarvis_conversation_observer_mode == "enabled":
        compact = {k: fact.get(k) for k in ("task_id", "event_type", "goal", "summary", "status", "notification",
            "presentation_id", "source_task_event_id", "completed_at", "delivery_state", "communication_assessment")}
        return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    preference = fact["notification"]
    compact = {key:fact[key] for key in ("task_id","event_type","presentation_id","exposure_token")}
    compact["notification"] = preference
    compact["delivery_state"] = "pending"
    summary = " ".join(str(fact.get("summary") or "").split())[:180]
    goal = " ".join(str(fact.get("goal") or "").split())[:90]
    compact["summary"] = summary
    prefix = {"requested":"The backend recorded a request for a completion announcement. Honour it at the next opening unless a later preference supersedes it.",
        "suppressed":"The backend recorded a request for silence. Keep the outcome for later questions; do not announce it unless a later preference supersedes silence.",
        "discretionary":"The backend currently records discretionary notification. This does not establish that no notification was requested. Check later user instructions and your promises; save any correction before deciding relevance."}[preference["intent"]]
    content = prefix + f" Verified task: {goal}. Verified outcome: {summary}. Delivery metadata: " + json.dumps(compact,ensure_ascii=False,separators=(",",":"))
    if len(content.encode("utf8")) > 1600: raise ValueError("Preference-aware append exceeds bounded context budget")
    return content
