"""Recall-first wake verification shared by the Realtime response arbiter."""
from __future__ import annotations

from dataclasses import dataclass
import re


_DIRECT_PATTERN = re.compile(r"^(?:hey\s+)?jarvis(?:[,.!?\s]|$)", re.IGNORECASE)
_INCIDENTAL_PATTERN = re.compile(
    r"\b(?:about|called|mention(?:ed|ing)?|said)\s+jarvis\b|"
    r"\bjarvis\s+(?:movie|character|actor)\b",
    re.IGNORECASE,
)
_RECALL_FIRST_THRESHOLD = 0.18


@dataclass(frozen=True, slots=True)
class WakeVerification:
    accepted: bool
    reason: str


def verify_wake_transcript(transcript: str, wake_score: float) -> WakeVerification:
    """Reject only clear incidental mentions; favor recall for all gate candidates."""
    text = transcript.strip()
    if _INCIDENTAL_PATTERN.search(text):
        return WakeVerification(False, "incidental_mention")
    if _DIRECT_PATTERN.search(text):
        return WakeVerification(True, "direct_address")
    if wake_score >= _RECALL_FIRST_THRESHOLD:
        return WakeVerification(True, "wake_model_candidate")
    return WakeVerification(False, "wake_word_not_addressed")
