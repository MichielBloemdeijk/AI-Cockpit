# Queued telemetry and offline admission — 2026-10-05

Capture/result observations now enqueue bounded diagnostic jobs and batch independent SQLite writes. Admission does not await their persistence. Task/admission/event authority retains its original durable commits. A separate thread was unnecessary; the existing driver already runs SQLite work on threads.

## Scope and comparison method

No paid provider calls or real worker model runs were made. Gateway, capture and routing profiles reject socket connections; provider streams/tool calls are fixtures. SQLite uses runtime WAL, synchronous=FULL and NullPool. Reports use one high-resolution server clock. Each number below is median / nearest-rank p95 / maximum in milliseconds; nested spans and separate percentiles are not additive.

Three independent databases per revision contribute 60 measured samples per gateway case, plus retained warmups. The initial sequential before/after baseline suffered large disk/OS tails, including plain/replay cases unaffected by telemetry. Its full reduction cannot be attributed to the queue. A second controlled experiment alternates queued and awaited observations in two independent databases: 40 measured samples per case per delivery mode. Both use the current SQL/validation; the awaited comparator writes each observation synchronously. It is test-only, not a production compatibility path. Queue drains between paired samples occur outside measurement; the ordinary queued cohort runs without per-sample drains and includes background contention.

## Controlled interleaved comparison

- **Plain task:** awaited 75.22 / 77.53 / 78.41 ms; queued **75.98 / 78.93 / 347.52 ms** (n=40 each).
- **New document:** awaited 114.87 / 137.48 / 377.28 ms; queued **101.44 / 133.79 / 335.37 ms** (n=40 each).
- **Both screen images ready:** awaited 192.19 / 221.20 / 458.53 ms; queued **154.62 / 168.25 / 374.77 ms** (n=40 each).
- **Ordinary continuation:** awaited 108.88 / 110.60 / 127.04 ms; queued **109.32 / 281.18 / 364.81 ms** (n=40 each).
- **Idempotent replay:** awaited 7.44 / 7.80 / 7.91 ms; queued **7.47 / 7.75 / 7.83 ms** (n=40 each).

Continuation's queued p95 worsened in this cohort; it is not a demonstrated improvement. The three largest queued continuation outliers contain **zero observation calls** and durable DB commits taking 261.54, 181.89, 131.05 ms. Their traces locate the stalls in task/intent commits, not queue submission. Background diagnostics were drained before each paired sample. The underlying disk/OS cause remains unisolated; retain these outliers rather than treating the queue as a universal speedup.

## Full local flow through event receipt

These 20 measured samples per mode traverse transcript boundary/correlation, fixture selector, coordinator, gateway, durable task and result journal, and the real SSE generator consumed by a simulated client. They exclude HTTP framing, network, actual speech/ASR and Android receipt. They are not the ready-path live acceptance gate.

- **Managed delegation to receipt:** 262.15 / 284.79 / 518.50 ms; task commit to receipt 67.97 / 76.60 / 84.70 ms (n=20).
- **Client delegation to receipt:** 271.12 / 319.32 / 537.67 ms; task commit to receipt 65.83 / 91.25 / 100.90 ms (n=20).

## Ordinary queued cohort and remaining local work

### Plain task

Total **74.10 / 83.71 / 339.63 ms** (n=60); median session commit count 5, including read-only sessions.

- task_dispatch: 30.92 / 34.17 / 296.34 ms.
- db_commit: 27.85 / 37.67 / 293.13 ms.
- focus_update: 12.65 / 13.14 / 18.46 ms.
- intent_reservation: 11.84 / 12.44 / 23.76 ms.
- intent_completion: 10.44 / 10.90 / 13.52 ms.
- result_lookup: 6.42 / 6.60 / 7.19 ms.
Nested stages describe overlapping work; db_session includes the stages inside it.

### New document

Total **93.59 / 109.21 / 350.39 ms** (n=60); median session commit count 5, including read-only sessions.

- task_dispatch: 43.35 / 44.68 / 288.18 ms.
- db_commit: 27.96 / 40.05 / 272.23 ms.
- focus_update: 12.78 / 14.40 / 21.83 ms.
- intent_reservation: 12.26 / 12.68 / 15.56 ms.
- intent_completion: 10.90 / 19.66 / 210.42 ms.
- result_lookup: 10.10 / 17.34 / 209.11 ms.
- result_observation_enqueue: 0.04 / 0.05 / 0.05 ms.
Nested stages describe overlapping work; db_session includes the stages inside it.

### Both screen images ready

Total **146.05 / 196.67 / 417.58 ms** (n=60); median session commit count 9, including read-only sessions.

- task_dispatch: 50.59 / 65.57 / 323.21 ms.
- db_commit: 25.50 / 34.36 / 317.70 ms.
- capture_preflight: 21.95 / 23.73 / 35.74 ms.
- focus_update: 16.62 / 28.00 / 30.14 ms.
- intent_reservation: 12.26 / 15.27 / 244.90 ms.
- intent_completion: 11.44 / 21.40 / 25.73 ms.
- capture_binding_and_result_allocation: 8.98 / 9.43 / 10.57 ms.
- result_observation_enqueue: 0.09 / 0.10 / 0.10 ms.
Nested stages describe overlapping work; db_session includes the stages inside it.

### Ordinary continuation

Total **118.82 / 136.24 / 170.60 ms** (n=60); median session commit count 9, including read-only sessions.

- task_resolution: 34.11 / 43.87 / 67.59 ms.
- db_commit: 33.37 / 43.99 / 53.05 ms.
- task_continuation: 19.34 / 24.03 / 28.09 ms.
- intent_reservation: 14.06 / 26.14 / 55.79 ms.
- focus_update: 12.66 / 15.51 / 23.94 ms.
- target_pinning: 11.49 / 21.46 / 23.72 ms.
- intent_completion: 10.44 / 13.44 / 19.13 ms.
- result_lookup: 6.44 / 6.65 / 11.73 ms.
Nested stages describe overlapping work; db_session includes the stages inside it.

### Idempotent replay

Total **7.56 / 7.82 / 19.63 ms** (n=60); median session commit count 1, including read-only sessions.

- intent_reservation: 7.54 / 7.80 / 19.61 ms.
- db_commit: 0.93 / 1.01 / 4.46 ms.
Nested stages describe overlapping work; db_session includes the stages inside it.

## Routing, capture and diagnostics

- **Managed ordinary routing:** before 211.26 / 221.49 / 392.87 ms; after 211.33 / 230.12 / 231.95 ms (n=20 each). Plain routing produces no new result/capture metrics, so large telemetry savings are not expected.
- Managed boundary_and_routing_ms: 76.60 / 81.51 / 95.34 ms.
- Managed validated_call_to_shell_ms: 30.78 / 33.08 / 48.14 ms.
- Managed authority_ms: 6.87 / 7.03 / 7.17 ms.
- Managed gateway_ms: 79.98 / 81.12 / 94.18 ms.
- Managed result_record_and_return_ms: 16.43 / 16.72 / 17.93 ms.
- Managed validation_ms: 0.09 / 0.10 / 0.10 ms.
- **Client ordinary routing:** before 211.54 / 232.64 / 572.33 ms; after 212.19 / 222.08 / 223.88 ms (n=20 each). Plain routing produces no new result/capture metrics, so large telemetry savings are not expected.
- Client boundary_and_routing_ms: 76.98 / 78.05 / 80.61 ms.
- Client validated_call_to_shell_ms: 31.01 / 32.30 / 34.31 ms.
- Client authority_ms: 6.81 / 7.05 / 7.11 ms.
- Client gateway_ms: 79.88 / 91.15 / 91.19 ms.
- Client result_record_and_return_ms: 16.90 / 17.20 / 18.31 ms.
- Client validation_ms: 0.08 / 0.09 / 0.09 ms.
- **Standalone SSE commit to receipt:** before 9.19 / 17.50 / 19.51 ms; after 10.07 / 15.10 / 18.43 ms, n=20 each. Commit-triggered delivery remains enabled in both.

- ready_after_100ms: **143.31 ms**, `captured` (one diagnostic observation).
- ready_after_300ms: **337.55 ms**, `captured` (one diagnostic observation).
- ready_after_1000ms: **1046.95 ms**, `captured` (one diagnostic observation).
- optional_latest_never_ready: **39.41 ms**, `optional_unavailable` (one diagnostic observation).
- required_latest_never_ready: **2034.49 ms**, `required_timeout` (one diagnostic observation).

Across the gateway/paired/capture cohorts, **814 queued jobs completed in 499 batches**, with zero dropped/failed/pending jobs after draining. Queue health is scoped to the current process/event loop, not the whole task platform. Queue wait and batch-write timing windows are retained per cohort in comparison.json; they are diagnostic persistence latency, not admission latency.


Background timing windows for the three ordinary queued gateway trials:

- Trial 0: queue wait 14.04 / 37.13 / 92.79 ms; batch write/commit 23.48 / 66.17 / 330.76 ms. These windows include warmups and have 161 jobs / 97 batches.
- Trial 1: queue wait 12.24 / 41.80 / 93.38 ms; batch write/commit 23.68 / 66.36 / 186.76 ms. These windows include warmups and have 161 jobs / 96 batches.
- Trial 2: queue wait 12.59 / 43.02 / 75.26 ms; batch write/commit 24.15 / 72.38 / 385.31 ms. These windows include warmups and have 161 jobs / 91 batches.

## Tradeoffs, further improvements and blockers

- **Diagnostic visibility:** metrics appear shortly after admission, rather than being synchronously readable. The queue holds at most 512 pending jobs plus a 32-job active batch, and batches for up to 20 ms off the admission path. Overflow, failed batches and timed-out drains are logged as counts without content/exception strings; the observability endpoint reports process-local health and bounded timing windows. Missing evidence remains unknown, never a successful measurement.
- **Shutdown/crash:** normal database close rejects new observations and allows a two-second drain. On expiry, jobs are cancelled/discarded with loss counts; connection rollback/cleanup still finishes before engine disposal. A forced crash may lose diagnostics. Durable task state and result journals are unaffected. Making telemetry crash-durable would restore write/commit overhead and is not justified for these best-effort observations.
- **SQLite contention:** one consumer batches writes with short query/busy deadlines and one fresh-transaction retry only for busy/locked errors. Batching reduces transactions but does not eliminate competition for SQLite's writer. Larger batches save commits but risk longer writer occupancy and more observations lost together; keep the present bounds unless measurements justify changing them.
- **Next local target:** repeated durable routing/focus/receipt transactions and connection setup. Consider eliminating redundant read-only commits and safely sharing related authority reads before introducing connection pooling. Batching authoritative events could reduce commits, but changes replay, cancellation and failure boundaries; require targeted recovery tests. Keep synchronous=FULL and task ownership/fencing.
- **Other delivery path:** the maintained result-page stream still uses a 500 ms polling fallback for publication updates. Triggering that stream could improve post-admission result freshness; it is separate from the unified admission stream measured here.
- **Capture:** required images wake on readiness and retain a two-second failure deadline. Optional images never extend admission. Capture acquisition/encoding/status transfer on Android remains unmeasured by these backend fixtures; do not count fixture delays as device evidence.
- **Provider/transport:** live endpointing, ASR, selector generation and network remain outside offline measurement. The historical multi-second unassigned interval cannot be attributed solely to the provider. Current local results do not pass M8/M10 or select a live routing winner. The last paid acceptance run was blocked by depleted API credits; that balance was not rechecked or charged here. Dedicated subscription sign-in is already available for worker execution, but does not replace API voice admission. Physical-device acceptance remains deferred.

## Initial sequential comparison retained for transparency

- Plain task: initial before 115.86 / 1670.65 / 1847.82 ms; after 74.10 / 83.71 / 339.63 ms. System tails make this an unsuitable causal estimate.
- New document: initial before 199.67 / 2082.40 / 2823.37 ms; after 93.59 / 109.21 / 350.39 ms. System tails make this an unsuitable causal estimate.
- Both screen images ready: initial before 371.50 / 3287.57 / 5647.03 ms; after 146.05 / 196.67 / 417.58 ms. System tails make this an unsuitable causal estimate.
- Ordinary continuation: initial before 185.94 / 2200.36 / 4440.55 ms; after 118.82 / 136.24 / 170.60 ms. System tails make this an unsuitable causal estimate.
- Idempotent replay: initial before 12.06 / 175.55 / 431.68 ms; after 7.56 / 7.82 / 19.63 ms. System tails make this an unsuitable causal estimate.

## Validation and reproduction

Functional/regression test results are recorded in the progress ledger. Queue tests cover blocking writer isolation, batching/metadata copy, context isolation, saturation, failure recovery, one idempotent contention retry, cancellation, shutdown timeout/drain and delayed run/revision identity. Actual API-process restart tests verify retained task/result/metric evidence.

From backend, set JARVIS_OFFLINE_TIMING_REPORT to a fresh absolute output prefix and run `uv run pytest tests/test_admission_timing.py -q`. The interleaved comparison tests are opt-in with the same setting. Raw before/after/paired artifacts and comparison.json are retained in data/admission-telemetry-2026-10-05. Regenerate this report from repo root with `python scripts/compare-admission-telemetry.py --directory data/admission-telemetry-2026-10-05 --output docs/jarvis-tasks/reports/admission-telemetry-2026-10-05.md`. Source hashes describe reporting-time files, not exact code identity at every earlier measurement or frozen-candidate acceptance. A subsequent publication-metadata correction leaves failed operations' uncommitted revisions unknown; it was validated separately after the admission cohorts and does not change admission execution.
