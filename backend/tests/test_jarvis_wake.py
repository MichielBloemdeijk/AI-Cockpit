from app.services.jarvis_wake import verify_wake_transcript


def test_wake_verifier_is_recall_first() -> None:
    assert verify_wake_transcript("Jarvis, remind me in ten minutes", 0.19).accepted
    assert verify_wake_transcript("remind me in ten minutes", 0.20).accepted


def test_wake_verifier_rejects_only_clear_incidental_mentions() -> None:
    decision = verify_wake_transcript("I was talking about Jarvis yesterday", 0.95)
    assert not decision.accepted
    assert decision.reason == "incidental_mention"
