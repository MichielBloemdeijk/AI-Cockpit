"""Pure reporting for Jarvis Realtime v3 terminal turn records.

The browser owns reduction from raw traces to v3 records.  This module only
groups already-reduced records, so report generation never needs transcript or
PCM data and can be exercised from deterministic fixtures.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import json
import math
from typing import Any, Iterable, Mapping


OUTCOMES = ("completed", "cancelled", "timed_out", "failed", "rejected")
SUCCESSFUL_PLAYBACK_OUTCOME = "completed"
PLAYBACK_OBSERVED = "observed"


def extract_persisted_turn_records(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize direct v3 records or exported persisted telemetry rows."""

    records: list[dict[str, Any]] = []
    for raw in rows:
        if raw.get("schema") == "jarvis.realtime_turn_result.v3":
            records.append(dict(raw))
            continue
        if raw.get("event_type") not in {"turn.result", "turn.result.amendment"}:
            continue
        detail: object = raw.get("detail_json") or raw.get("detail")
        if isinstance(detail, Mapping) and detail.get("schema") == "jarvis.realtime_turn_result.v3":
            record = dict(detail)
        elif isinstance(detail, Mapping) and isinstance(detail.get("detail"), str):
            try:
                parsed = json.loads(detail["detail"])
            except (TypeError, ValueError):
                continue
            if not isinstance(parsed, dict) or parsed.get("schema") != "jarvis.realtime_turn_result.v3":
                continue
            record = parsed
        else:
            continue
        record.setdefault("observed_at", raw.get("observed_at"))
        record.setdefault("activation_id", raw.get("activation_id"))
        record.setdefault("session_id", raw.get("session_id"))
        record.setdefault("generation", raw.get("generation"))
        record.setdefault("input_item_id", raw.get("provider_input_item_id"))
        record.setdefault("request_id", raw.get("request_id"))
        record.setdefault("response_id", raw.get("provider_response_id"))
        records.append(record)
    return records


def percentile(values: Iterable[float], percentile_value: float) -> float | None:
    """Return a linearly interpolated percentile, or ``None`` for no data."""

    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return None
    if not 0.0 <= percentile_value <= 100.0:
        raise ValueError("percentile must be between 0 and 100")
    if len(ordered) == 1:
        return round(ordered[0], 3)
    position = (len(ordered) - 1) * percentile_value / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 3)


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _record_key(record: Mapping[str, Any], index: int) -> str:
    key = _string(record.get("turn_key"))
    return key or f"ambiguous:{index}"


def _record_time(record: Mapping[str, Any]) -> datetime | None:
    for field in ("terminal_at", "observed_at", "completed_at", "created_at"):
        raw = record.get(field)
        if not isinstance(raw, str):
            continue
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


def _merge_amendment(previous: dict[str, Any], amendment: Mapping[str, Any]) -> dict[str, Any]:
    """Apply a late metadata amendment without changing generation outcome."""

    merged = dict(previous)
    for key, value in amendment.items():
        if value is not None:
            merged[key] = value
    if previous.get("generation_outcome") in OUTCOMES:
        merged["generation_outcome"] = previous["generation_outcome"]
    return merged


def _canonical_records(records: Iterable[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    by_key: dict[str, dict[str, Any]] = {}
    ambiguous = 0
    for index, raw in enumerate(records):
        record = dict(raw)
        if record.get("schema") not in (None, "jarvis.realtime_turn_result.v3"):
            continue
        key = _record_key(record, index)
        if key.startswith("ambiguous:"):
            ambiguous += 1
        amendment_key = _string(record.get("amends_turn_key")) or _string(record.get("amendment_of"))
        target = amendment_key or key
        if target in by_key:
            by_key[target] = _merge_amendment(by_key[target], record)
        else:
            by_key[target] = record
    return list(by_key.values()), ambiguous


def _stats(values: Iterable[float]) -> dict[str, float | int | None]:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    return {
        "count": len(ordered),
        "min_ms": round(ordered[0], 3) if ordered else None,
        "median_ms": percentile(ordered, 50),
        "p90_ms": percentile(ordered, 90),
        "p95_ms": percentile(ordered, 95),
        "max_ms": round(ordered[-1], 3) if ordered else None,
    }


def _successful_latency(record: Mapping[str, Any], name: str) -> float | None:
    if record.get("generation_outcome") != SUCCESSFUL_PLAYBACK_OUTCOME:
        return None
    if record.get("playback_state") != PLAYBACK_OBSERVED:
        return None
    latencies = record.get("latency_ms")
    if not isinstance(latencies, Mapping):
        return None
    value = _number(latencies.get(name))
    return value if value is not None and value >= 0 else None


def _speech_end_to_playback(record: Mapping[str, Any]) -> float | None:
    if record.get("generation_outcome") != SUCCESSFUL_PLAYBACK_OUTCOME:
        return None
    if record.get("playback_state") != PLAYBACK_OBSERVED:
        return None
    latencies = record.get("latency_ms")
    if not isinstance(latencies, Mapping):
        return None
    names = (
        "local_speech_end_to_provider_commit",
        "provider_commit_to_gate_ready",
        "gate_ready_to_response_create_sent",
        "response_create_sent_to_response_created",
        "response_created_to_first_provider_audio",
        "first_provider_audio_to_playback_proxy",
    )
    values = [_number(latencies.get(name)) for name in names]
    if any(value is None or value < 0 for value in values):
        return None
    return sum(value for value in values if value is not None)


def _group(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(record.get("generation_outcome") or "failed") for record in records)
    for outcome in OUTCOMES:
        counts.setdefault(outcome, 0)
    latency_names = {
        name
        for record in records
        for name in (record.get("latency_ms") or {})
        if isinstance(name, str)
    }
    latency = {
        name: _stats(
            value
            for record in records
            if (value := _successful_latency(record, name)) is not None
        )
        for name in sorted(latency_names)
    }
    warm_values = [
        value
        for record in records
        if record.get("connection_cohort") == "warm"
        if (value := _speech_end_to_playback(record)) is not None
    ]
    return {
        "total": len(records),
        "outcomes": {outcome: counts[outcome] for outcome in OUTCOMES},
        "successful_playback": sum(
            1
            for record in records
            if record.get("generation_outcome") == SUCCESSFUL_PLAYBACK_OUTCOME
            and record.get("playback_state") == PLAYBACK_OBSERVED
        ),
        "latency_ms": latency,
        "warm_speech_end_to_playback_ms": _stats(warm_values),
    }


def build_jarvis_latency_report(
    records: Iterable[Mapping[str, Any]],
    *,
    configuration_id: str | None = None,
    benchmark_run_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, Any]:
    """Build a JSON-serializable v3 reliability and latency report."""

    canonical, ambiguous = _canonical_records(extract_persisted_turn_records(records))
    filtered: list[dict[str, Any]] = []
    for record in canonical:
        if configuration_id and record.get("configuration_id") != configuration_id:
            continue
        if benchmark_run_id and record.get("benchmark_run_id") != benchmark_run_id:
            continue
        timestamp = _record_time(record)
        if start and (timestamp is None or timestamp < start):
            continue
        if end and (timestamp is None or timestamp > end):
            continue
        filtered.append(record)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cohorts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in filtered:
        groups[str(record.get("configuration_id") or "unknown")].append(record)
        cohorts[str(record.get("connection_cohort") or "unknown")].append(record)

    totals = _group(filtered)
    totals["ambiguous"] = ambiguous
    totals["denominator_check"] = sum(totals["outcomes"].values()) == totals["total"]
    return {
        "schema": "jarvis.realtime_latency_report.v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "filters": {
            "configuration_id": configuration_id,
            "benchmark_run_id": benchmark_run_id,
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
        },
        "totals": totals,
        "by_configuration": {key: _group(value) for key, value in sorted(groups.items())},
        "by_cohort": {key: _group(value) for key, value in sorted(cohorts.items())},
        "records": filtered,
    }


def render_jarvis_latency_report(report: Mapping[str, Any]) -> str:
    """Render the report in a compact form suitable for a benchmark log."""

    totals = report.get("totals") if isinstance(report.get("totals"), Mapping) else {}
    outcomes = totals.get("outcomes") if isinstance(totals.get("outcomes"), Mapping) else {}
    warm = totals.get("warm_speech_end_to_playback_ms")
    lines = [
        "Jarvis Realtime latency report",
        f"Activations: {totals.get('total', 0)}",
        "Outcomes: " + ", ".join(f"{name}={outcomes.get(name, 0)}" for name in OUTCOMES),
        f"Successful playback: {totals.get('successful_playback', 0)}",
        f"Ambiguous/missing identity records: {totals.get('ambiguous', 0)}",
    ]
    if isinstance(warm, Mapping):
        lines.append(
            "Warm speech-end to playback: "
            f"median={warm.get('median_ms')} ms, p90={warm.get('p90_ms')} ms"
        )
    return "\n".join(lines)


# Stable aliases for callers that prefer the shorter service names.
summarize_latency_records = build_jarvis_latency_report
