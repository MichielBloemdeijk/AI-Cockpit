from app.services.heard_prefix import TranscriptBoundary, estimate_heard_prefix


TRANSCRIPT = (
    "First point is complete. The second sentence must stay unheard. "
    "The third is later."
)


def test_provider_audio_cutoff_uses_slow_utterance_alignment_to_avoid_early_tail() -> None:
    estimate = estimate_heard_prefix(
        TRANSCRIPT,
        audio_end_ms=2_100,
        generated_audio_ms=7_140,
    )

    assert estimate.transcript == "First point is complete."
    assert estimate.source == "provider_audio_end_pcm_aligned"
    assert estimate.duration_scale > 1.3


def test_provider_audio_cutoff_uses_fast_utterance_alignment_to_avoid_late_prefix() -> None:
    estimate = estimate_heard_prefix(
        TRANSCRIPT,
        audio_end_ms=1_500,
        generated_audio_ms=4_016,
    )

    assert estimate.transcript == "First point is complete. The"
    assert estimate.source == "provider_audio_end_pcm_aligned"
    assert estimate.duration_scale < 0.8


def test_provider_zero_cutoff_is_unheard_and_never_commits_generated_tail() -> None:
    estimate = estimate_heard_prefix(TRANSCRIPT, audio_end_ms=0)

    assert estimate.transcript == ""
    assert estimate.cutoff_char == 0


def test_transcript_delta_age_is_only_the_bounded_missing_provider_fallback() -> None:
    estimate = estimate_heard_prefix(
        TRANSCRIPT,
        audio_end_ms=None,
        transcript_boundaries=(
            TranscriptBoundary(char_end=len("First point is complete."), received_offset_ms=700),
            TranscriptBoundary(
                char_end=len("First point is complete. The second sentence"),
                received_offset_ms=1_050,
            ),
        ),
        mute_offset_ms=1_200,
        transcript_margin_ms=250,
    )

    assert estimate.transcript == "First point is complete."
    assert estimate.source == "transcript_delta_age_fallback"


def test_missing_playout_evidence_commits_no_assistant_text() -> None:
    estimate = estimate_heard_prefix(TRANSCRIPT, audio_end_ms=None)

    assert estimate.transcript == ""
    assert estimate.source == "no_playout_evidence"
