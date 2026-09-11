from app.services.jarvis_wake import verify_wake_transcript


def test_wake_verifier_requires_direct_address() -> None:
    assert verify_wake_transcript("Jarvis, remind me in ten minutes", 0.19).accepted
    decision = verify_wake_transcript("remind me in ten minutes", 0.20)
    assert not decision.accepted
    assert decision.reason == "wake_word_not_addressed"


def test_wake_verifier_accepts_common_direct_address_variants() -> None:
    for phrase in (
        "Hey, Jarvis, remind me",
        "Hello Jarvis; remind me",
        "Hi, Jarvis!",
        "Okay, Jarvis",
        "OK Jarvis",
        "Yo Jarvis",
        "Jarvis, remind me",
    ):
        decision = verify_wake_transcript(phrase, 0.2)
        assert decision.accepted, phrase
        assert decision.reason == "direct_address"
        assert decision.wake_term_present is True
        assert decision.transcript_word_count >= 2


def test_wake_verifier_uses_strong_score_fallback_but_rejects_low_score() -> None:
    for score in (0.725, 0.928, 0.997):
        decision = verify_wake_transcript("remind me in ten minutes", score)
        assert decision.accepted
        assert decision.reason == "strong_wake_candidate"
        assert decision.prefix_classification == "missing_prefix"
    boundary = verify_wake_transcript("remind me in ten minutes", 0.65)
    assert boundary.accepted
    assert boundary.reason == "strong_wake_candidate"
    decision = verify_wake_transcript("remind me in ten minutes", 0.649)
    assert not decision.accepted


def test_wake_verifier_rejects_only_clear_incidental_mentions() -> None:
    decision = verify_wake_transcript("I was talking about Jarvis yesterday", 0.95)
    assert not decision.accepted
    assert decision.reason == "incidental_mention"
    assert decision.prefix_classification == "incidental"
