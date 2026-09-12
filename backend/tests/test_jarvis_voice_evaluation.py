from __future__ import annotations

from dataclasses import replace

from app.services.jarvis_voice_evaluation import (
    MATCHED_VOICE_SCENARIOS,
    VoiceEvaluationObservation,
    evaluate_voice_engines,
)


def _observation(scenario_id: str, engine: str, *, acknowledgement_ms: float = 900) -> VoiceEvaluationObservation:
    return VoiceEvaluationObservation(
        scenario_id=scenario_id,
        engine=engine,  # type: ignore[arg-type]
        repetition=1,
        cold=False,
        passed=True,
        command_end_ms=1_000,
        first_audible_ack_ms=1_000 + acknowledgement_ms,
        durable_acceptance_ms=1_400,
        task_admission_passed=True,
    )


def test_matched_twenty_scenarios_pass_promotion_gates() -> None:
    observations = [
        _observation(scenario.id, engine)
        for scenario in MATCHED_VOICE_SCENARIOS
        for engine in ("realtime", "live")
    ]

    report = evaluate_voice_engines(observations)

    assert report["overall_passed"] is True
    assert report["gates"]["twenty_matched_scenarios"] is True
    assert report["engines"]["live"]["ready_acknowledgement"]["p95_ms"] == 900


def test_live_critical_failure_and_admission_regression_block_promotion() -> None:
    observations = [
        _observation(scenario.id, "realtime")
        for scenario in MATCHED_VOICE_SCENARIOS
    ]
    observations.extend(
        replace(
            _observation(scenario.id, "live"),
            passed=scenario.id != "duplicate_event",
            task_admission_passed=False,
            duplicate_action=scenario.id == "duplicate_event",
        )
        for scenario in MATCHED_VOICE_SCENARIOS
    )

    report = evaluate_voice_engines(observations)

    assert report["overall_passed"] is False
    assert report["gates"]["no_material_task_admission_regression"] is False
    assert report["gates"]["no_duplicate_action_regression"] is False
