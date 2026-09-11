from __future__ import annotations

import json

from app.services.jarvis_latency_report import build_jarvis_latency_report, render_jarvis_latency_report


def _record(key: str, outcome: str, *, playback: str = "observed", cohort: str = "warm") -> dict:
    return {
        "schema": "jarvis.realtime_turn_result.v3",
        "turn_key": key,
        "activation_id": key,
        "session_id": "session-1",
        "generation": 1,
        "generation_outcome": outcome,
        "playback_state": playback,
        "transcript_state": "missing",
        "gate_state": "accepted",
        "connection_cohort": cohort,
        "configuration_id": "server_vad:s350:t0.5:p300",
        "benchmark_run_id": "run-1",
        "latency_ms": {
            "local_speech_end_to_provider_commit": 100,
            "provider_commit_to_gate_ready": 0,
            "gate_ready_to_response_create_sent": 50,
            "response_create_sent_to_response_created": 100,
            "response_created_to_first_provider_audio": 700,
            "first_provider_audio_to_playback_proxy": 50,
        },
    }


def test_report_keeps_failures_in_denominator_and_only_uses_observed_playback() -> None:
    records = [
        _record("completed-1", "completed"),
        _record("completed-2", "completed", playback="not_observed"),
        _record("cancelled-1", "cancelled", playback="not_observed"),
        _record("timeout-1", "timed_out", playback="not_applicable"),
        _record("failed-1", "failed", playback="not_observed", cohort="cold"),
        _record("rejected-1", "rejected", playback="not_applicable"),
    ]
    report = build_jarvis_latency_report(records)

    assert report["totals"]["total"] == 6
    assert report["totals"]["denominator_check"] is True
    assert report["totals"]["outcomes"] == {
        "completed": 2,
        "cancelled": 1,
        "timed_out": 1,
        "failed": 1,
        "rejected": 1,
    }
    assert report["totals"]["successful_playback"] == 1
    assert report["totals"]["warm_speech_end_to_playback_ms"]["count"] == 1
    assert report["by_cohort"]["cold"]["total"] == 1


def test_report_deduplicates_records_and_applies_late_amendments_without_changing_outcome() -> None:
    completed = _record("activation-1", "completed")
    amendment = {
        "schema": "jarvis.realtime_turn_result.v3",
        "amends_turn_key": "activation-1",
        "turn_key": "activation-1-amendment",
        "transcript_state": "available_late",
        "generation_outcome": "failed",
    }
    report = build_jarvis_latency_report([completed, dict(completed), amendment])
    assert report["totals"]["total"] == 1
    assert report["records"][0]["generation_outcome"] == "completed"
    assert report["records"][0]["transcript_state"] == "available_late"


def test_report_marks_missing_identity_as_ambiguous_and_filters_run() -> None:
    report = build_jarvis_latency_report([
        _record("run-1", "completed"),
        {**_record("run-2", "completed"), "benchmark_run_id": "other"},
        {"schema": "jarvis.realtime_turn_result.v3", "generation_outcome": "failed"},
    ], benchmark_run_id="run-1")
    assert report["totals"]["total"] == 1
    assert report["totals"]["ambiguous"] == 1
    assert "Activations: 1" in render_jarvis_latency_report(report)
    assert "Ambiguous" in render_jarvis_latency_report(report)


def test_report_reads_persisted_terminal_trace_rows_without_truncation() -> None:
    record = _record("persisted-1", "completed")
    report = build_jarvis_latency_report([{
        "event_type": "turn.result",
        "observed_at": "2026-09-09T12:00:00Z",
        "activation_id": "persisted-1",
        "provider_input_item_id": "input-1",
        "detail_json": record,
    }])
    assert report["totals"]["total"] == 1
    assert report["records"][0]["input_item_id"] == "input-1"

    legacy_wrapped = build_jarvis_latency_report([{
        "event_type": "turn.result",
        "detail_json": {"detail": json.dumps(_record("persisted-2", "cancelled"))},
    }])
    assert legacy_wrapped["totals"]["outcomes"]["cancelled"] == 1
