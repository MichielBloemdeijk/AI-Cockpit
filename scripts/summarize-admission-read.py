"""Report local read/delivery improvements and retained SQLite stall diagnostics."""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
from statistics import median


def stats(values):
    values = sorted(values)
    return dict(count=len(values), median_ms=median(values), p95_ms=values[math.ceil(.95*len(values))-1], max_ms=values[-1])


def fmt(value):
    return f"{value['median_ms']:.2f} / {value['p95_ms']:.2f} / {value['max_ms']:.2f} ms"


def read(path):
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result.get("failures", 0) == 0, path
    if "observation_queue" in result:
        assert all(result["observation_queue"][key] == 0 for key in ("failed", "dropped", "pending")), path
    return result


def load_trials(prefix):
    return [read(prefix.with_name(f"{prefix.stem}-trial-{i}.json")) for i in range(3)]


def group(trials):
    rows = [row for trial in trials for row in trial["samples"] if not row["warmup"]]
    groups = {}
    for case in ("plain", "document", "screen_ready", "continuation", "replay"):
        selected = [row for row in rows if row["case"] == case]
        stages = defaultdict(list)
        for row in selected:
            per_call = defaultdict(float)
            for span in row["timing"]["spans"]:
                per_call[span["stage"]] += span["duration_ms"]
            for name, duration in per_call.items():
                stages[name].append(duration)
        groups[case] = dict(total=stats([row["elapsed_ms"] for row in selected]),
            stages={name: stats(values) for name, values in stages.items()},
            median_session_commits=median(sum(s["stage"] == "db_commit" for s in row["timing"]["spans"]) for row in selected))
    return groups


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--loop-prefix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prefix = args.prefix.resolve()
    current_trials = load_trials(prefix)
    current, baseline = group(current_trials), group(load_trials(args.baseline.resolve()))
    delivery = read(prefix.with_name(f"{prefix.stem}-result-delivery.json"))
    native = read(prefix.with_name(f"{prefix.stem}-sqlite-native.json"))
    full = {mode: read(prefix.with_name(f"{prefix.stem}-full-flow-{mode}.json")) for mode in ("responses", "client")}
    loops = load_trials(args.loop_prefix.resolve())
    stalls = []
    for i, trial in enumerate(loops):
        assert trial["event_loop_target_ms"] == 50
        pulses = trial["event_loop_observations"]
        for row in trial["samples"]:
            if row["warmup"]:
                continue
            for span in row["timing"]["spans"]:
                if span["stage"] in {"db_transaction_commit", "db_read_close"} and span["duration_ms"] >= 100:
                    overlap = [p for p in pulses if p["end_ns"] >= span["start_ns"] and p["start_ns"] <= span["end_ns"]]
                    stalls.append(dict(trial=i, case=row["case"], stage=span["stage"], duration_ms=span["duration_ms"],
                        loop_pulses=len(overlap), max_loop_lateness_ms=max((p["lateness_ms"] for p in overlap), default=None)))
    summary = dict(schema="offline-admission-read-comparison.v1", current=current, baseline=baseline,
        result_delivery=delivery, native_sqlite=native, full_flow=full, loop_stalls=stalls,
        loop_target_ms=50, loop_gateway=group(loops))
    prefix.with_name(f"{prefix.stem}-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    lines = ["# Admission reads, committed result delivery and SQLite diagnostics — 2026-10-05", "",
        "This revision removes full-history loads from known task-ID resolution, closes lookup-only gateway/resolver scopes without committing, reuses selected admission turns, and wakes maintained result-page streams after commit. Task mutation services retain their locks, status checks, durable commits, receipts and recovery behavior. No provider calls, account changes or application-data resets were performed.", "",
        "## Method", "",
        "Current gateway measurements have 60 samples per case across three independent migrated SQLite databases, plus retained warmups. Full-flow samples have 20 per mode and traverse real routing/coordinator/gateway/journal/SSE with fixture providers and a simulated client. Separate result delivery compares polling and triggers interleaved on the same current read code, 20 samples each. The native probe has 60 measured SQLite commits with runtime WAL/FULL settings and new connections. A focused second gateway cohort adds a 50 ms event-loop pulse monitor to correlate long commit/close waits. All clocks used for intervals are perf_counter_ns in the same server process. These are offline diagnostics; speech, provider generation, HTTP/Android and loaded-system acceptance are excluded.", "",
        "Numbers below are median / nearest-rank p95 / maximum. Nested stages overlap and must not be added. The previous queued-telemetry cohort was measured separately, so differences in its tails are not a controlled causal estimate.", "",
        "## Gateway comparison", ""]
    for case in current:
        lines.append(f"- **{case}:** previous {fmt(baseline[case]['total'])}; current **{fmt(current[case]['total'])}**, n=60 each. Session commit count {baseline[case]['median_session_commits']:g} → {current[case]['median_session_commits']:g}, including the earlier read-only commits.")
    lines += ["", "Continuation's smaller history-free reads are the clearest admission improvement. Plain admission and replay remain similar. Document tail latency worsened in this cohort; its new spans place the outliers in transaction commit rather than flush. Do not describe this as a universal speedup.", "", "## Delivery", ""]
    for mode in ("polling", "triggered"):
        lines.append(f"- **Result-page commit to generator receipt ({mode}): {fmt(delivery['summary'][mode])}**, n=20.")
    for mode in ("responses", "client"):
        lines.append(f"- **Full local delegation to receipt ({mode}): {fmt(full[mode]['summary']['delegation_to_receipt_ms'])}**, n=20. Task commit to receipt: {fmt(full[mode]['summary']['task_commit_to_receipt_ms'])}.")
    lines += ["", "Global result commit hints broadcast to all viewers, whose DB reads remain page-scoped. This can wake unrelated page viewers and costs an extra read at higher publication volume. Missed/cross-process notifications retain a 500 ms recovery scan. Existing workers publish from another process, so worker-originated updates can still use that scan; this revision does not claim a 20 ms cross-process bound. A shared process notification transport or filesystem/IPC signal would add infrastructure and needs separate evidence before adoption.", "", "## Remaining local stages", ""]
    for case, values in current.items():
        lines += [f"### {case}", ""]
        for name, value in sorted(values["stages"].items(), key=lambda item: item[1]["median_ms"], reverse=True):
            if name != "db_session":
                lines.append(f"- {name}: {fmt(value)}.")
        lines.append("")
    lines += ["## SQLite and loop evidence", ""]
    for name, value in native["summary"].items():
        lines.append(f"- Native {name}: {fmt(value)}, n=60.")
    for stall in stalls:
        lines.append(f"- {stall['case']} {stall['stage']}: **{stall['duration_ms']:.2f} ms**, {stall['loop_pulses']} overlapping loop pulses; maximum excess over the 50 ms target **{stall['max_loop_lateness_ms']:.2f} ms**.")
    lines += ["", "Flush is sub-millisecond in the ordinary cohort. Native commit timings show that persistent slow commit throughput is not reproduced by the isolated primitive workload. Loop pulses during a long await can narrow whether the event loop was frozen, but do not separate aiosqlite worker scheduling, filesystem flush/checkpoint latency or external Windows activity. The exact storage/driver cause remains unresolved; do not attribute the entire gap to the provider or to telemetry. Read-close stalls can also include final-connection WAL cleanup.", "", "## Next steps and tradeoffs", "",
        "- Connection open/setup occupies meaningful cumulative time across repeated NullPool sessions. Keep the current policy until a bounded reuse experiment covers concurrent writers, cancellation, engine disposal, test event loops and process restart. Avoid reducing WAL/FULL durability to improve a benchmark.",
        "- Focus, intent completion and routing events still use separate durable transactions. Combining them could reduce latency but changes recovery/failure boundaries; do this only with explicit same-task crash/replay coverage and a promising offline comparison.",
        "- Cross-process result notifications remain the main limitation of the new result-stream hint. The in-process improvement is measured; a worker-to-API latency claim still needs its own check.",
        "- Live admission remains unvalidated on this candidate. Restored credits should first fund a two/three-case targeted pilot after local tests, then the required full frozen-candidate cohort if that pilot passes. No unchanged paid corpus loops or model sweeps. See the runbook and AGENTS.md for the persistent testing policy.", "", "## Verification", "",
        "The broad offline suite passed 188 tests, including DB guards, commit signal broadcast/replay/rollback/cancellation, task controls, capture collection/partial failures, actual API restart/restore, and queued telemetry. The full offline profiling selection passed 12 tests with two interleaved telemetry tests deliberately deselected. The additional pulse-monitor gateway cohort is a focused diagnostic, not a live acceptance gate. Raw samples and the machine-readable current-summary.json are retained in data/admission-read-2026-10-05.", "",
        "Regenerate from repo root: `python scripts/summarize-admission-read.py --prefix data/admission-read-2026-10-05/current.json --baseline data/admission-telemetry-2026-10-05/after.json --loop-prefix data/admission-read-2026-10-05/loop-50ms.json --output docs/jarvis-tasks/reports/admission-read-2026-10-05.md`.", ""]
    args.output.resolve().write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(dict(gateway={case: value["total"] for case, value in current.items()}, result_delivery=delivery["summary"], loop_stalls=stalls)))


if __name__ == "__main__":
    main()
