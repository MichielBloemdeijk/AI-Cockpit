"""Recall-first wake verification shared by the Live response arbiter."""
from __future__ import annotations

from dataclasses import dataclass
import re


_DIRECT_PATTERN = re.compile(
    r"^\s*(?:(?P<cue>hey|hello|hi|okay|ok|yo)(?:\s*[,;:!?-]\s*|\s+))?jarvis\b",
    re.IGNORECASE,
)
_WAKE_TERM_PATTERN = re.compile(r"\bjarvis\b", re.IGNORECASE)
_INCIDENTAL_PATTERN = re.compile(
    r"\b(?:about|called|mention(?:ed|ing)?|said)\s+jarvis\b|"
    r"\bjarvis\s+(?:movie|character|actor)\b",
    re.IGNORECASE,
)
_STRONG_WAKE_THRESHOLD = 0.65


@dataclass(frozen=True, slots=True)
class WakeVerification:
    accepted: bool
    reason: str
    wake_term_present: bool = False
    prefix_classification: str = "none"
    transcript_word_count: int = 0


def verify_wake_transcript(transcript: str, wake_score: float) -> WakeVerification:
    """Fuse explicit transcript evidence with the wake detector score.

    Incidental mentions take precedence over every other signal. A direct
    address accepts immediately, including common conversational cue words.
    Otherwise a score of 0.65 or higher accepts as a strong wake candidate;
    this threshold preserves recall for observed high-confidence detections
    while rejecting low-score candidates without an address prefix.
    """
    text = " ".join(transcript.split())
    wake_term_present = bool(_WAKE_TERM_PATTERN.search(text))
    word_count = len(re.findall(r"\b[\w']+\b", text))
    if _INCIDENTAL_PATTERN.search(text):
        return WakeVerification(
            False,
            "incidental_mention",
            wake_term_present,
            "incidental",
            word_count,
        )
    direct = _DIRECT_PATTERN.match(text)
    if direct is not None:
        prefix = "cue_direct_address" if direct.group("cue") else "direct_address"
        return WakeVerification(True, "direct_address", True, prefix, word_count)
    if wake_score >= _STRONG_WAKE_THRESHOLD:
        return WakeVerification(
            True,
            "strong_wake_candidate",
            wake_term_present,
            "missing_prefix",
            word_count,
        )
    return WakeVerification(
        False,
        "wake_word_not_addressed",
        wake_term_present,
        "missing_prefix",
        word_count,
    )
