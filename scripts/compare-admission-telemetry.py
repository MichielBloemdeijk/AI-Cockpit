"""Compare retained offline cohorts and interleaved diagnostic delivery; no network."""
import argparse
from collections import defaultdict
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import median


def stats(values):
    values = sorted(values)
    return dict(count=len(values), median_ms=median(values),
        p95_ms=values[math.ceil(.95*len(values))-1], max_ms=values[-1])


def fmt(value):
    return f"{value['median_ms']:.2f} / {value['p95_ms']:.2f} / {value['max_ms']:.2f} ms"


def read(path):
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result.get("failures", result.get("unexpected_failures", 0)) == 0, path
    return result


def grouped(rows, delivery=None):
    groups = {}
    for case in ("plain", "document", "screen_ready", "continuation", "replay"):
        selected = [row for row in rows if row["case"] == case and not row["warmup"]
            and (delivery is None or row["observation_delivery"] == delivery)]
        stages = defaultdict(list)
        for row in selected:
            per_call = defaultdict(float)
            for entry in row["timing"]["spans"]:
                per_call[entry["stage"]] += entry["duration_ms"]
            for stage, duration in per_call.items():
                stages[stage].append(duration)
        groups[case] = dict(total=stats([row["elapsed_ms"] for row in selected]),
            stages={stage: stats(values) for stage, values in stages.items()},
            median_session_commit_count=median(sum(entry["stage"] == "db_commit" for entry in row["timing"]["spans"]) for row in selected))
    return groups


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    cohorts = {label: [read(directory / f"{label}-trial-{i}.json") for i in range(3)] for label in ("before", "after")}
    groups = {label: grouped([row for trial in trials for row in trial["samples"]]) for label, trials in cohorts.items()}
    paired_trials = [read(directory / f"after-paired-trial-{i}.json") for i in range(2)]
    paired_rows = [row for trial in paired_trials for row in trial["samples"]]
    paired = {delivery: grouped(paired_rows, delivery) for delivery in ("awaited", "queued")}
    routes = {label: {mode: read(directory / f"{label}-routing-{mode}.json") for mode in ("responses", "client")} for label in ("before", "after")}
    full = {mode: read(directory / f"after-full-flow-{mode}.json") for mode in ("responses", "client")}
    sse = {label: read(directory / f"{label}-sse.json") for label in ("before", "after")}
    captures = {label: read(directory / f"{label}-pending-capture.json") for label in ("before", "after")}
    queue_runs = [trial["observation_queue"] for trial in cohorts["after"]+paired_trials]+[captures["after"]["observation_queue"]]
    assert all(run["failed"] == run["dropped"] == run["pending"] == 0 for run in queue_runs)
    root = Path(__file__).resolve().parents[1]
    files = ("backend/app/services/observation_queue.py", "backend/app/services/result_telemetry.py",
        "backend/app/db/session.py", "backend/tests/test_admission_timing.py", "backend/tests/test_observation_queue.py")
    result = dict(schema="offline-admission-telemetry-comparison.v1", gateway=groups, interleaved=paired,
        routing=routes, full_flow=full, sse=sse, capture=captures, observation_queues=queue_runs,
        reporting_time_source_sha256={name: sha256((root/name).read_bytes()).hexdigest() for name in files})
    directory.joinpath("comparison.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    labels = dict(plain="Plain task", document="New document", screen_ready="Both screen images ready",
        continuation="Ordinary continuation", replay="Idempotent replay")
    lines = ["# Queued telemetry and offline admission — 2026-10-05", "",
        "Capture/result observations now enqueue bounded diagnostic jobs and batch independent SQLite writes. Admission does not await their persistence. Task/admission/event authority retains its original durable commits. A separate thread was unnecessary; the existing driver already runs SQLite work on threads.", "",
        "## Scope and comparison method", "",
        "No paid provider calls or real worker model runs were made. Gateway, capture and routing profiles reject socket connections; provider streams/tool calls are fixtures. SQLite uses runtime WAL, synchronous=FULL and NullPool. Reports use one high-resolution server clock. Each number below is median / nearest-rank p95 / maximum in milliseconds; nested spans and separate percentiles are not additive.", "",
        "Three independent databases per revision contribute 60 measured samples per gateway case, plus retained warmups. The initial sequential before/after baseline suffered large disk/OS tails, including plain/replay cases unaffected by telemetry. Its full reduction cannot be attributed to the queue. A second controlled experiment alternates queued and awaited observations in two independent databases: 40 measured samples per case per delivery mode. Both use the current SQL/validation; the awaited comparator writes each observation synchronously. It is test-only, not a production compatibility path. Queue drains between paired samples occur outside measurement; the ordinary queued cohort runs without per-sample drains and includes background contention.", "",
        "## Controlled interleaved comparison", ""]
    for case, label in labels.items():
        old, new = paired["awaited"][case]["total"], paired["queued"][case]["total"]
        lines.append(f"- **{label}:** awaited {fmt(old)}; queued **{fmt(new)}** (n=40 each).")
    continuation_tails = sorted([row for row in paired_rows if row["case"] == "continuation"
        and row["observation_delivery"] == "queued" and not row["warmup"]], key=lambda row: row["elapsed_ms"], reverse=True)[:3]
    assert all(not any("result_observation" in span["stage"] for span in row["timing"]["spans"]) for row in continuation_tails)
    slow_commits = [max(span["duration_ms"] for span in row["timing"]["spans"] if span["stage"] == "db_commit") for row in continuation_tails]
    lines += ["", f"Continuation's queued p95 worsened in this cohort; it is not a demonstrated improvement. The three largest queued continuation outliers contain **zero observation calls** and durable DB commits taking {', '.join(f'{value:.2f}' for value in slow_commits)} ms. Their traces locate the stalls in task/intent commits, not queue submission. Background diagnostics were drained before each paired sample. The underlying disk/OS cause remains unisolated; retain these outliers rather than treating the queue as a universal speedup."]
    lines += ["", "## Full local flow through event receipt", "",
        "These 20 measured samples per mode traverse transcript boundary/correlation, fixture selector, coordinator, gateway, durable task and result journal, and the real SSE generator consumed by a simulated client. They exclude HTTP framing, network, actual speech/ASR and Android receipt. They are not the ready-path live acceptance gate.", ""]
    for mode, label in (("responses", "Managed"), ("client", "Client")):
        stage = full[mode]["summary"]
        lines.append(f"- **{label} delegation to receipt:** {fmt(stage['delegation_to_receipt_ms'])}; task commit to receipt {fmt(stage['task_commit_to_receipt_ms'])} (n=20).")
    lines += ["", "## Ordinary queued cohort and remaining local work", ""]
    for case, label in labels.items():
        group = groups["after"][case]
        lines += [f"### {label}", "", f"Total **{fmt(group['total'])}** (n=60); median session commit count {group['median_session_commit_count']:g}, including read-only sessions.", ""]
        lines += [f"- {name}: {fmt(values)}." for name, values in sorted(group["stages"].items(), key=lambda item: item[1]["median_ms"], reverse=True) if name != "db_session"]
        lines.append("Nested stages describe overlapping work; db_session includes the stages inside it.")
        lines.append("")
    lines += ["## Routing, capture and diagnostics", ""]
    for mode, label in (("responses", "Managed"), ("client", "Client")):
        before, after = routes["before"][mode]["summary"], routes["after"][mode]["summary"]
        lines += [f"- **{label} ordinary routing:** before {fmt(before['elapsed_ms'])}; after {fmt(after['elapsed_ms'])} (n=20 each). Plain routing produces no new result/capture metrics, so large telemetry savings are not expected."]
        for name in ("boundary_and_routing_ms", "validated_call_to_shell_ms", "authority_ms", "gateway_ms", "result_record_and_return_ms", "validation_ms"):
            lines.append(f"- {label} {name}: {fmt(after[name])}.")
    lines += [f"- **Standalone SSE commit to receipt:** before {fmt(sse['before']['summary'])}; after {fmt(sse['after']['summary'])}, n=20 each. Commit-triggered delivery remains enabled in both.", ""]
    for sample in captures["after"]["samples"]:
        lines.append(f"- {sample['case']}: **{sample['elapsed_ms']:.2f} ms**, `{sample['outcome']}` (one diagnostic observation).")
    accepted = sum(run["accepted"] for run in queue_runs)
    batches = sum(run["batches"] for run in queue_runs)
    lines += ["", f"Across the gateway/paired/capture cohorts, **{accepted} queued jobs completed in {batches} batches**, with zero dropped/failed/pending jobs after draining. Queue health is scoped to the current process/event loop, not the whole task platform. Queue wait and batch-write timing windows are retained per cohort in comparison.json; they are diagnostic persistence latency, not admission latency.", "",
        "## Tradeoffs, further improvements and blockers", "",
        "- **Diagnostic visibility:** metrics appear shortly after admission, rather than being synchronously readable. The queue holds at most 512 pending jobs plus a 32-job active batch, and batches for up to 20 ms off the admission path. Overflow, failed batches and timed-out drains are logged as counts without content/exception strings; the observability endpoint reports process-local health and bounded timing windows. Missing evidence remains unknown, never a successful measurement.",
        "- **Shutdown/crash:** normal database close rejects new observations and allows a two-second drain. On expiry, jobs are cancelled/discarded with loss counts; connection rollback/cleanup still finishes before engine disposal. A forced crash may lose diagnostics. Durable task state and result journals are unaffected. Making telemetry crash-durable would restore write/commit overhead and is not justified for these best-effort observations.",
        "- **SQLite contention:** one consumer batches writes with short query/busy deadlines and one fresh-transaction retry only for busy/locked errors. Batching reduces transactions but does not eliminate competition for SQLite's writer. Larger batches save commits but risk longer writer occupancy and more observations lost together; keep the present bounds unless measurements justify changing them.",
        "- **Next local target:** repeated durable routing/focus/receipt transactions and connection setup. Consider eliminating redundant read-only commits and safely sharing related authority reads before introducing connection pooling. Batching authoritative events could reduce commits, but changes replay, cancellation and failure boundaries; require targeted recovery tests. Keep synchronous=FULL and task ownership/fencing.",
        "- **Other delivery path:** the maintained result-page stream still uses a 500 ms polling fallback for publication updates. Triggering that stream could improve post-admission result freshness; it is separate from the unified admission stream measured here.",
        "- **Capture:** required images wake on readiness and retain a two-second failure deadline. Optional images never extend admission. Capture acquisition/encoding/status transfer on Android remains unmeasured by these backend fixtures; do not count fixture delays as device evidence.",
        "- **Provider/transport:** live endpointing, ASR, selector generation and network remain outside offline measurement. The historical multi-second unassigned interval cannot be attributed solely to the provider. Current local results do not pass M8/M10 or select a live routing winner. The last paid acceptance run was blocked by depleted API credits; that balance was not rechecked or charged here. Dedicated subscription sign-in is already available for worker execution, but does not replace API voice admission. Physical-device acceptance remains deferred.", "",
        "## Initial sequential comparison retained for transparency", ""]
    diagnostic_lines = ["", "Background timing windows for the three ordinary queued gateway trials:", ""]
    for index, trial in enumerate(cohorts["after"]):
        run = trial["observation_queue"]
        diagnostic_lines.append(f"- Trial {index}: queue wait {fmt(run['queue_wait'])}; batch write/commit {fmt(run['batch_write'])}. These windows include warmups and have {run['accepted']} jobs / {run['batches']} batches.")
    position = lines.index("## Tradeoffs, further improvements and blockers")
    lines[position:position] = diagnostic_lines+[""]
    for case, label in labels.items():
        lines.append(f"- {label}: initial before {fmt(groups['before'][case]['total'])}; after {fmt(groups['after'][case]['total'])}. System tails make this an unsuitable causal estimate.")
    lines += ["", "## Validation and reproduction", "",
        "Functional/regression test results are recorded in the progress ledger. Queue tests cover blocking writer isolation, batching/metadata copy, context isolation, saturation, failure recovery, one idempotent contention retry, cancellation, shutdown timeout/drain and delayed run/revision identity. Actual API-process restart tests verify retained task/result/metric evidence.", "",
        "From backend, set JARVIS_OFFLINE_TIMING_REPORT to a fresh absolute output prefix and run `uv run pytest tests/test_admission_timing.py -q`. The interleaved comparison tests are opt-in with the same setting. Raw before/after/paired artifacts and comparison.json are retained in data/admission-telemetry-2026-10-05. Regenerate this report from repo root with `python scripts/compare-admission-telemetry.py --directory data/admission-telemetry-2026-10-05 --output docs/jarvis-tasks/reports/admission-telemetry-2026-10-05.md`. Source hashes describe reporting-time files, not exact code identity at every earlier measurement or frozen-candidate acceptance. A subsequent publication-metadata correction leaves failed operations' uncommitted revisions unknown; it was validated separately after the admission cohorts and does not change admission execution.", ""]
    args.output.resolve().write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(dict(interleaved={case: {delivery: paired[delivery][case]["total"] for delivery in paired} for case in labels},
        full_flow={mode: report["summary"]["delegation_to_receipt_ms"] for mode, report in full.items()},
        queued_jobs=accepted, batches=batches)))


if __name__ == "__main__":
    main()
