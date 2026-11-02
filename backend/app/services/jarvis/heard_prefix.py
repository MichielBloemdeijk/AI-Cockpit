"""Bounded transcript-prefix estimation from audible Live playback evidence."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


DEFAULT_TRANSCRIPT_MARGIN_MS = 250
_LEADING_AUDIO_MS = 80.0
_WORD_BASE_MS = 120.0
_SYLLABLE_MS = 145.0
_PUNCTUATION_MS = {
    ",": 130.0,
    ":": 150.0,
    ";": 180.0,
    ".": 280.0,
    "!": 280.0,
    "?": 300.0,
}
_WORD_RE = re.compile(r"\S+")
_VOWEL_GROUP_RE = re.compile(r"[aeiouy]+", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class TranscriptBoundary:
    """A transcript prefix observed at a monotonic offset from response creation."""

    char_end: int
    received_offset_ms: float


@dataclass(frozen=True, slots=True)
class HeardPrefixEstimate:
    transcript: str
    source: str
    cutoff_char: int
    audio_end_ms: int | None
    modeled_total_ms: int
    duration_scale: float


def estimate_heard_prefix(
    transcript: str,
    *,
    audio_end_ms: int | None,
    duration_scale: float = 1.0,
    generated_audio_ms: float | None = None,
    transcript_boundaries: Iterable[TranscriptBoundary] = (),
    mute_offset_ms: float | None = None,
    transcript_margin_ms: int = DEFAULT_TRANSCRIPT_MARGIN_MS,
) -> HeardPrefixEstimate:
    """Estimate the last complete word heard before an interruption.

    Provider ``audio_end_ms`` is the primary playout clock. Text is aligned to
    that clock with a speech-duration model calibrated by either actual PCM
    duration or completed utterances. Transcript-delta age is deliberately a
    conservative fallback only when the provider cutoff is unavailable.
    """

    normalized = transcript.strip()
    word_boundaries, modeled_total_ms = _modeled_word_boundaries(normalized)
    scale = max(0.65, min(float(duration_scale or 1.0), 1.6))
    if generated_audio_ms and generated_audio_ms > 0 and modeled_total_ms > 0:
        scale = max(0.65, min(generated_audio_ms / modeled_total_ms, 1.6))

    if not normalized or not word_boundaries:
        return HeardPrefixEstimate("", "no_transcript", 0, audio_end_ms, 0, scale)

    if audio_end_ms is not None:
        audible_ms = max(0.0, float(audio_end_ms))
        cutoff = _cutoff_for_modeled_time(word_boundaries, audible_ms, scale)
        source = (
            "provider_audio_end_pcm_aligned"
            if generated_audio_ms and generated_audio_ms > 0
            else "provider_audio_end_calibrated_text"
        )
        return HeardPrefixEstimate(
            normalized[:cutoff].rstrip(),
            source,
            cutoff,
            audio_end_ms,
            round(modeled_total_ms),
            scale,
        )

    fallback_cutoff = 0
    if mute_offset_ms is not None:
        evidence_limit = max(0.0, mute_offset_ms - max(0, transcript_margin_ms))
        for boundary in transcript_boundaries:
            if boundary.received_offset_ms <= evidence_limit:
                fallback_cutoff = max(fallback_cutoff, min(boundary.char_end, len(normalized)))
        if not fallback_cutoff:
            fallback_cutoff = _cutoff_for_modeled_time(word_boundaries, evidence_limit, scale)
        fallback_cutoff = _complete_word_cutoff(normalized, fallback_cutoff)
        return HeardPrefixEstimate(
            normalized[:fallback_cutoff].rstrip(),
            "transcript_delta_age_fallback",
            fallback_cutoff,
            None,
            round(modeled_total_ms),
            scale,
        )

    return HeardPrefixEstimate(
        "",
        "no_playout_evidence",
        0,
        None,
        round(modeled_total_ms),
        scale,
    )


def modeled_transcript_duration_ms(transcript: str) -> float:
    """Return the uncalibrated speech-duration estimate for a transcript."""

    _, total = _modeled_word_boundaries(transcript.strip())
    return total


def _modeled_word_boundaries(transcript: str) -> tuple[list[tuple[int, float]], float]:
    if not transcript:
        return [], 0.0
    elapsed = _LEADING_AUDIO_MS
    boundaries: list[tuple[int, float]] = []
    for match in _WORD_RE.finditer(transcript):
        token = match.group(0)
        lexical = re.sub(r"[^\w'-]", "", token, flags=re.UNICODE)
        syllables = _estimated_syllables(lexical)
        elapsed += _WORD_BASE_MS + (_SYLLABLE_MS * syllables)
        trailing = token[-1] if token else ""
        elapsed += _PUNCTUATION_MS.get(trailing, 0.0)
        boundaries.append((match.end(), elapsed))
    return boundaries, elapsed


def _estimated_syllables(word: str) -> int:
    ascii_word = word.lower()
    groups = _VOWEL_GROUP_RE.findall(ascii_word)
    if not groups:
        return 1
    count = len(groups)
    if ascii_word.endswith("e") and not ascii_word.endswith(("le", "ye")) and count > 1:
        count -= 1
    return max(1, count)


def _cutoff_for_modeled_time(
    boundaries: list[tuple[int, float]],
    audible_ms: float,
    scale: float,
) -> int:
    cutoff = 0
    for char_end, modeled_ms in boundaries:
        if modeled_ms * scale > audible_ms:
            break
        cutoff = char_end
    return cutoff


def _complete_word_cutoff(transcript: str, candidate: int) -> int:
    if candidate >= len(transcript):
        return len(transcript)
    cutoff = 0
    for match in _WORD_RE.finditer(transcript):
        if match.end() > candidate:
            break
        cutoff = match.end()
    return cutoff
