from __future__ import annotations

import json

from app.models.chat import ModelResponse, ToolCall, ToolCallFunction
from app.services.jarvis_benchmark import (
    JarvisBenchmarkReport,
    PerformanceResult,
    ToolGateCase,
    ToolGateResult,
    _percentile_nearest_rank,
    evaluate_tool_case,
)


def _response(name: str, arguments: dict) -> ModelResponse:
    return ModelResponse(
        model="local/test",
        content="",
        tool_calls=[ToolCall(id="call-1", function=ToolCallFunction(name=name, arguments=json.dumps(arguments)))],
    )


def test_tool_case_requires_expected_schema_valid_call() -> None:
    case = ToolGateCase("read", "Read README", "file_read", {"path": "README.md"})
    schemas = {
        "file_read": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        }
    }

    passed = evaluate_tool_case(case, _response("file_read", {"path": "README.md"}), schemas=schemas, duration_seconds=0.1)
    wrong = evaluate_tool_case(case, _response("file_read", {"path": 42}), schemas=schemas, duration_seconds=0.1)

    assert passed.passed is True
    assert wrong.passed is False
    assert "not of type" in str(wrong.error)


def test_nearest_rank_percentile_is_deterministic_for_small_gate_sample() -> None:
    assert _percentile_nearest_rank([0.4, 0.1, 0.3, 0.2, 0.5], 0.9) == 0.5
    assert _percentile_nearest_rank([], 0.9) is None


def test_report_enforces_documented_gate_counts_and_performance() -> None:
    report = JarvisBenchmarkReport(
        model="local/test",
        tool_results=[ToolGateResult(str(index), "tool", ["tool"], index < 19, 0.1) for index in range(20)],
        multistep_results=[],
        performance=PerformanceResult([], 1.0, 25.0, 11_000),
    )

    assert report.gates["single_tool_19_of_20"] is True
    assert report.gates["multistep_8_of_10"] is False
    assert report.overall_passed is False


def test_performance_error_fails_all_performance_gates() -> None:
    report = JarvisBenchmarkReport(
        model="local/test",
        tool_results=[],
        multistep_results=[],
        performance=PerformanceResult([], None, None, 9_000, error="empty response"),
    )

    assert report.gates["tokens_per_second_20"] is False
    assert report.gates["p90_first_token_1_5_seconds"] is False
    assert report.gates["peak_gpu_memory_below_11500_mib"] is False
