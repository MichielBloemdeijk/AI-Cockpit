from app.services.jarvis_tone import (
    CALM_BUTLER_ACKNOWLEDGEMENT_CORPUS,
    calm_acknowledgement_violations,
)


def test_calm_butler_acknowledgement_corpus_is_concise_and_free_of_filler() -> None:
    assert len(CALM_BUTLER_ACKNOWLEDGEMENT_CORPUS) >= 6
    for case in CALM_BUTLER_ACKNOWLEDGEMENT_CORPUS:
        assert calm_acknowledgement_violations(case.response) == (), case


def test_calm_butler_evaluator_rejects_excited_verbose_filler() -> None:
    violations = calm_acknowledgement_violations(
        "Sure! I would be delighted to help you with anything else you may need."
    )
    assert set(violations) == {
        "word_count",
        "excitement",
        "banned_opening",
        "generic_filler",
    }
