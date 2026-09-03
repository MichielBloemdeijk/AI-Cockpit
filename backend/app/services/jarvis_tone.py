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
    "i hear you",
)
BANNED_FILLER = (
    "let me know if",
    "happy to help",
    "anything else",
    "i'd be delighted",
    "i would be delighted",
)


@dataclass(frozen=True, slots=True)
class CalmButlerCase:
    prompt: str
    response: str


CALM_BUTLER_ACKNOWLEDGEMENT_CORPUS: tuple[CalmButlerCase, ...] = (
    CalmButlerCase("Thanks.", "You're welcome."),
    CalmButlerCase("Remind me in ten minutes.", "Reminder set."),
    CalmButlerCase("Queue that task.", "It's queued."),
    CalmButlerCase("Cancel it.", "It's cancelled."),
    CalmButlerCase("Start the task while the worker is offline.", "It hasn't started; the worker is offline."),
    CalmButlerCase("Change that task to progress updates.", "Update preference changed."),
)


def calm_acknowledgement_violations(response: str) -> tuple[str, ...]:
    normalized = " ".join(str(response or "").casefold().split())
    normalized_words = re.sub(r"[^\w']+", " ", normalized).strip()
    words = re.findall(r"\b[\w']+\b", normalized)
    violations: list[str] = []
    if not 2 <= len(words) <= 8:
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
    "CalmButlerCase",
    "calm_acknowledgement_violations",
]
