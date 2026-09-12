"""Matched Realtime/GPT-Live evaluation contracts and report generation."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from statistics import median
from typing import Any, Iterable, Literal


VoiceEngine = Literal["realtime", "live"]


@dataclass(frozen=True, slots=True)
class VoiceEvaluationScenario:
    id: str
    description: str


MATCHED_VOICE_SCENARIOS: tuple[VoiceEvaluationScenario, ...] = (
    VoiceEvaluationScenario("cold_wake_command", "Cold wake followed by an actionable command"),
    VoiceEvaluationScenario("ready_session_command", "Actionable command on an already-ready session"),
    VoiceEvaluationScenario("short_greeting", "Short conversational greeting"),
    VoiceEvaluationScenario("natural_pause", "Request containing a natural mid-sentence pause"),
    VoiceEvaluationScenario("activation_audio_once", "Activation audio reaches the provider exactly once"),
    VoiceEvaluationScenario("task_dispatch", "Durable background task dispatch"),
    VoiceEvaluationScenario("task_query", "Query an existing task"),
    VoiceEvaluationScenario("reminder", "Create a reminder through the authorized gateway"),
    VoiceEvaluationScenario("tool_failure", "Tool failure is surfaced without a false success claim"),
    VoiceEvaluationScenario("delayed_execution", "Speech continues while a delayed operation executes"),
    VoiceEvaluationScenario("speech_interruption", "User speech interrupts assistant playback"),
    VoiceEvaluationScenario("task_correction", "A correction supersedes stale pending work"),
    VoiceEvaluationScenario("explicit_cancellation", "Explicit cancellation reaches the task gateway"),
    VoiceEvaluationScenario("session_dismissal", "Session closes while accepted tasks continue"),
    VoiceEvaluationScenario("duplicate_event", "Duplicate provider events do not duplicate actions"),
    VoiceEvaluationScenario("multiple_function_calls", "All function results are returned before continuation"),
    VoiceEvaluationScenario("malformed_arguments", "Malformed tool arguments are rejected safely"),
    VoiceEvaluationScenario("reconnect_stale_generation", "Reconnect rejects events from a stale generation"),
    VoiceEvaluationScenario("unauthorized_controller", "Unauthorized controller cannot execute an action"),
    VoiceEvaluationScenario("locked_device", "Mutating action is blocked while the device is locked"),
)


@dataclass(frozen=True, slots=True)
class VoiceEvaluationObservation:
    scenario_id: str
    engine: VoiceEngine
    repetition: int
    cold: bool
    passed: bool
    command_end_ms: float | None = None
    first_audible_ack_ms: float | None = None
    durable_acceptance_ms: float | None = None
    tool_result_ready_ms: float | None = None
    first_audible_result_ms: float | None = None
    connection_start_ms: float | None = None
    connection_ready_ms: float | None = None
    task_admission_passed: bool | None = None
    authorization_violation: bool = False
    duplicate_action: bool = False
    command_loss: bool = False
    error: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "VoiceEvaluationObservation":
        return cls(**value)


def _duration(end: float | None, start: float | None) -> float | None:
    if end is None or start is None or end < start:
        return None
    return end - start


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def _metric(values: Iterable[float | None], *, failures: int) -> dict[str, Any]:
    observed = [float(value) for value in values if value is not None]
    return {
        "count": len(observed),
        "median_ms": median(observed) if observed else None,
        "p95_ms": _percentile(observed, 0.95),
        "failures": failures,
    }


def _engine_report(observations: list[VoiceEvaluationObservation]) -> dict[str, Any]:
    failures = sum(not item.passed for item in observations)
    admission = [item.task_admission_passed for item in observations if item.task_admission_passed is not None]
    return {
        "runs": len(observations),
        "passed": len(observations) - failures,
        "failed": failures,
        "scenario_count": len({item.scenario_id for item in observations}),
        "task_admission_rate": (
            sum(bool(value) for value in admission) / len(admission) if admission else None
        ),
        "ready_acknowledgement": _metric(
            (
                _duration(item.first_audible_ack_ms, item.command_end_ms)
                for item in observations
                if not item.cold
            ),
            failures=sum(not item.passed for item in observations if not item.cold),
        ),
        "cold_acknowledgement": _metric(
            (
                _duration(item.first_audible_ack_ms, item.command_end_ms)
                for item in observations
                if item.cold
            ),
            failures=sum(not item.passed for item in observations if item.cold),
        ),
        "durable_acceptance": _metric(
            (_duration(item.durable_acceptance_ms, item.command_end_ms) for item in observations),
            failures=failures,
        ),
        "audible_tool_result": _metric(
            (
                _duration(item.first_audible_result_ms, item.tool_result_ready_ms)
                for item in observations
            ),
            failures=failures,
        ),
        "connection_startup": _metric(
            (_duration(item.connection_ready_ms, item.connection_start_ms) for item in observations),
            failures=failures,
        ),
        "critical_failures": {
            "authorization": sum(item.authorization_violation for item in observations),
            "duplicate_action": sum(item.duplicate_action for item in observations),
            "command_loss": sum(item.command_loss for item in observations),
        },
    }


def evaluate_voice_engines(
    observations: Iterable[VoiceEvaluationObservation],
    *,
    admission_regression_tolerance: float = 0.02,
) -> dict[str, Any]:
    records = list(observations)
    known = {scenario.id for scenario in MATCHED_VOICE_SCENARIOS}
    unknown = sorted({item.scenario_id for item in records} - known)
    reports = {
        engine: _engine_report([item for item in records if item.engine == engine])
        for engine in ("realtime", "live")
    }
    realtime_scenarios = {item.scenario_id for item in records if item.engine == "realtime"}
    live_scenarios = {item.scenario_id for item in records if item.engine == "live"}
    matched = sorted(realtime_scenarios & live_scenarios & known)
    realtime_admission = reports["realtime"]["task_admission_rate"]
    live_admission = reports["live"]["task_admission_rate"]
    no_admission_regression = (
        realtime_admission is not None
        and live_admission is not None
        and live_admission + admission_regression_tolerance >= realtime_admission
    )
    live_critical = reports["live"]["critical_failures"]
    ready_ack_p95 = reports["live"]["ready_acknowledgement"]["p95_ms"]
    gates = {
        "twenty_matched_scenarios": len(matched) >= 20,
        "ready_acknowledgement_within_2000_ms": (
            ready_ack_p95 is not None and ready_ack_p95 <= 2_000
        ),
        "no_material_task_admission_regression": no_admission_regression,
        "no_authorization_regression": live_critical["authorization"] == 0,
        "no_duplicate_action_regression": live_critical["duplicate_action"] == 0,
        "no_command_loss_regression": live_critical["command_loss"] == 0,
    }
    return {
        "overall_passed": all(gates.values()),
        "gates": gates,
        "matched_scenarios": matched,
        "unknown_scenarios": unknown,
        "scenario_catalog": [asdict(scenario) for scenario in MATCHED_VOICE_SCENARIOS],
        "engines": reports,
    }
