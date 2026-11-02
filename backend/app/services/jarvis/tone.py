"""Deterministic calm-butler acceptance corpus and response checks."""
from __future__ import annotations

from dataclasses import dataclass
import re


BANNED_OPENINGS = (
    "sure",
    "okay",
    "ok",
    "of course",
    "absolutely",
    "great",
    "got it",
    "alright",
    "i hear you",
)
BANNED_FILLER = (
    "let me know if",
    "let me pick up",
    "let me think",
    "happy to help",
    "anything else",
    "if you prefer",
    "i'll keep replies",
    "i'll keep it",
    "i'd be delighted",
    "i would be delighted",
    "speak when you're ready",
    "speak when you are ready",
    "i am speaking as text",
    "i'll weigh this carefully",
    "i will weigh this carefully",
)


@dataclass(frozen=True, slots=True)
class CalmButlerCase:
    prompt: str
    response: str


CALM_BUTLER_ACKNOWLEDGEMENT_CORPUS: tuple[CalmButlerCase, ...] = (
    CalmButlerCase("Hello.", "Hello."),
    CalmButlerCase("Are you there?", "Yes."),
    CalmButlerCase("Thanks.", "You're welcome."),
    CalmButlerCase("Remind me in ten minutes.", "Reminder set."),
    CalmButlerCase("Queue that task.", "It's queued."),
    CalmButlerCase("Cancel it.", "It's cancelled."),
    CalmButlerCase("Start the task while the worker is offline.", "It hasn't started; the worker is offline."),
    CalmButlerCase("Change that task to progress updates.", "Update preference changed."),
)

CALM_BUTLER_REGRESSION_RESPONSES: tuple[str, ...] = (
    "I keep it brief, but I still try to be clear and polite in conversation. If you prefer, I can be even more minimal.",
    "Let me pick up the thread and continue the story from there.",
    "Let me think about where to pick the story up cleanly and keep it moving.",
    "Yes. I'm here. Speak when you're ready.",
    "Alright, picking up the story again and keeping it concise.",
    "Got it, I'll weigh this carefully and then give you a single choice.",
    "I am speaking as text for you to hear.",
)


def calm_acknowledgement_violations(response: str) -> tuple[str, ...]:
    normalized = " ".join(str(response or "").casefold().split())
    normalized_words = re.sub(r"[^\w']+", " ", normalized).strip()
    words = re.findall(r"\b[\w']+\b", normalized)
    violations: list[str] = []
    if not 1 <= len(words) <= 8:
        violations.append("word_count")
    if "!" in response:
        violations.append("excitement")
    if any(
        normalized_words == opening or normalized_words.startswith(f"{opening} ")
        for opening in BANNED_OPENINGS
    ):
        violations.append("banned_opening")
    if any(phrase in normalized for phrase in BANNED_FILLER):
        violations.append("generic_filler")
    return tuple(violations)


__all__ = [
    "BANNED_FILLER",
    "BANNED_OPENINGS",
    "CALM_BUTLER_ACKNOWLEDGEMENT_CORPUS",
    "CALM_BUTLER_REGRESSION_RESPONSES",
    "CalmButlerCase",
    "calm_acknowledgement_violations",
]
