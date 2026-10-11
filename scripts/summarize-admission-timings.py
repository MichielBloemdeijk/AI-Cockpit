"""Summarize current managed-only offline timing artifacts; never contact a provider."""
import argparse
from collections import defaultdict
from datetime import date
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import mean, median


def stats(values):
    values = sorted(values)
    return {"count": len(values), "mean_ms": mean(values), "median_ms": median(values),
        "p95_ms": values[math.ceil(.95 * len(values)) - 1], "max_ms": values[-1]}


def fmt(value):
    return f"{value['median_ms']:.2f} / {value['p95_ms']:.2f} / {value['max_ms']:.2f} ms (n={value['count']})"


def summarize(prefix):
    paths = [prefix.with_name(f"{prefix.stem}-trial-{index}.json") for index in range(3)]
    names = ("routing-managed", "full-flow-managed", "sse", "pending-capture")
    paths += [prefix.with_name(f"{prefix.stem}-{name}.json") for name in names]
    documents = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    rows = [row for document in documents[:3] for row in document["samples"] if not row["warmup"]]
    groups = {}
    for case in sorted({row["case"] for row in rows}):
        selected = [row for row in rows if row["case"] == case]
        stages = defaultdict(list)
        for row in selected:
            per_call = defaultdict(float)
            for entry in row["timing"]["spans"]:
                per_call[entry["stage"]] += entry["duration_ms"]
            for stage, duration in per_call.items():
                stages[stage].append(duration)
        groups[case] = {"total": stats([row["elapsed_ms"] for row in selected]),
            "stages": {stage: stats(values) for stage, values in stages.items()}}
    failures = sum(document.get("failures", document.get("unexpected_failures", 0)) for document in documents)
    source = Path(__file__).resolve().parents[1]
    source_names = ("backend/tests/test_admission_timing.py", "backend/app/services/admission_timing.py",
        "backend/app/services/openai_live_adapter.py", "backend/app/services/jarvis_coordinator.py",
        "backend/app/services/jarvis_live.py", "backend/app/services/task_capture.py",
        "backend/app/services/jarvis_brain.py", "backend/app/services/result_telemetry.py")
    hashes = {name: sha256((source / name).read_bytes()).hexdigest() for name in source_names}
    return {"schema": "admission-timing-summary.v2", "route": "managed_responses",
        "clock_source": "python.perf_counter_ns", "source_hashes_at_reporting": hashes,
        "gateway": groups, "routing": documents[3]["summary"], "full_flow": documents[4]["summary"],
        "sse": documents[5]["summary"], "pending_capture": documents[6]["samples"],
        "expected_required_timeouts": documents[6]["expected_required_timeouts"],
        "unexpected_failures": failures, "raw_artifacts": [str(path) for path in paths]}


def report(summary):
    lines = [f"# Managed offline admission timings — {date.today().isoformat()}", "",
        "The local flow uses the real adapter, transcript correlation, coordinator, gateway, durable journal and SSE generator with zero-delay provider fixtures. It excludes real voice/model delay, HTTP framing, network transport and Android receipt. It is diagnostic evidence, not live latency acceptance.", "",
        "Gateway/routing/capture fixtures reject socket network access. No paid provider or worker calls are made. Each of three independent migrated SQLite databases contributes 20 measured samples per gateway case; routing and full flow each retain 20 measured samples plus three warmups. SQLite uses the runtime WAL, synchronous=FULL and NullPool configuration.", "",
        f"Failures recorded in completed core profile artifacts: **{summary['unexpected_failures']}**. Expected required-current capture timeouts: **{summary['expected_required_timeouts']}**.", "",
        "This artifact count is not the overall pytest result. Failed diagnostic comparison tests can leave no artifact; retain the suite result and missing observations separately.", "",
        "All values are median / nearest-rank p95 / maximum. Nested stage work and percentiles cannot be added to form an end-to-end total.", "",
        "## Full local managed flow", ""]
    labels = {"elapsed_ms": "Delegation to broker return", "delegation_to_receipt_ms": "Delegation to simulated client SSE receipt",
        "task_commit_to_receipt_ms": "Task commit to SSE receipt", "boundary_and_routing_ms": "Boundary/correlation and pre-tool handling",
        "validation_ms": "Completed tool validation", "validated_call_to_shell_ms": "Validated call to shell start",
        "authority_ms": "Session/device authority", "gateway_ms": "Gateway", "result_record_and_return_ms": "Gateway return to journal and adapter return"}
    for key, value in summary["full_flow"].items():
        lines.append(f"- {labels[key]}: **{fmt(value)}**.")
    lines += ["", "## Managed routing without delivery", ""]
    for key, value in summary["routing"].items():
        lines.append(f"- {labels[key]}: {fmt(value)}.")
    lines += ["", "## Gateway cases and stages", ""]
    for case, group in summary["gateway"].items():
        lines += [f"### {case}", "", f"Total: **{fmt(group['total'])}**.", ""]
        lines += [f"- {stage}: {fmt(value)}." for stage, value in
            sorted(group["stages"].items(), key=lambda pair: pair[1]["p95_ms"], reverse=True)]
        lines.append("")
    lines += ["## Delivery and capture", "", f"- Event commit to SSE generator receipt: {fmt(summary['sse'])}.",
        "- Capture readiness delays below are injected fixtures, each observed once; they are not p95 estimates."]
    for row in summary["pending_capture"]:
        lines.append(f"- {row['case']}: {row['elapsed_ms']:.2f} ms; outcome `{row['outcome']}`.")
    lines += ["", "## Timing limits and reproduction", "",
        "Managed response-created and streamed item arrival are observable. The provider-owned request start, internal scheduling/prefill and one-way network delay remain unavailable; do not invent them or subtract unsynchronized clocks. User audio endpointing, activation-buffer residence and Android capture/receipt require their own observations. Upload and worker execution remain separate from admission.", "",
        "From backend, set JARVIS_OFFLINE_TIMING_REPORT to a fresh project data prefix, run `uv run pytest tests/test_admission_timing.py -q`, and clear that environment variable. From repo root, run `python scripts/summarize-admission-timings.py --prefix <prefix>.json --output <report>.md`. Raw samples retain warmups. Reporting-time source hashes are diagnostic provenance, not frozen-candidate acceptance.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prefix = args.prefix.resolve()
    summary = summarize(prefix)
    prefix.with_name(f"{prefix.stem}-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    args.output.resolve().write_text(report(summary), encoding="utf-8")
    print(json.dumps({"route": summary["route"], "full_flow": summary["full_flow"]["delegation_to_receipt_ms"],
        "unexpected_failures": summary["unexpected_failures"], "report": str(args.output.resolve())}))


if __name__ == "__main__":
    main()
