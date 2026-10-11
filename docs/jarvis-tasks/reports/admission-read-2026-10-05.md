# Admission reads, committed result delivery and SQLite diagnostics — 2026-10-05

This revision removes full-history loads from known task-ID resolution, closes lookup-only gateway/resolver scopes without committing, reuses selected admission turns, and wakes maintained result-page streams after commit. Task mutation services retain their locks, status checks, durable commits, receipts and recovery behavior. No provider calls, account changes or application-data resets were performed.

## Method

Current gateway measurements have 60 samples per case across three independent migrated SQLite databases, plus retained warmups. Full-flow samples have 20 per mode and traverse real routing/coordinator/gateway/journal/SSE with fixture providers and a simulated client. Separate result delivery compares polling and triggers interleaved on the same current read code, 20 samples each. The native probe has 60 measured SQLite commits with runtime WAL/FULL settings and new connections. A focused second gateway cohort adds a 50 ms event-loop pulse monitor to correlate long commit/close waits. All clocks used for intervals are perf_counter_ns in the same server process. These are offline diagnostics; speech, provider generation, HTTP/Android and loaded-system acceptance are excluded.

Numbers below are median / nearest-rank p95 / maximum. Nested stages overlap and must not be added. The previous queued-telemetry cohort was measured separately, so differences in its tails are not a controlled causal estimate.

## Gateway comparison

- **plain:** previous 74.10 / 83.71 / 339.63 ms; current **73.91 / 76.45 / 88.45 ms**, n=60 each. Session commit count 5 → 4, including the earlier read-only commits.
- **document:** previous 93.59 / 109.21 / 350.39 ms; current **94.12 / 193.33 / 335.63 ms**, n=60 each. Session commit count 5 → 4, including the earlier read-only commits.
- **screen_ready:** previous 146.05 / 196.67 / 417.58 ms; current **149.40 / 176.87 / 372.77 ms**, n=60 each. Session commit count 9 → 7, including the earlier read-only commits.
- **continuation:** previous 118.82 / 136.24 / 170.60 ms; current **103.49 / 122.60 / 366.05 ms**, n=60 each. Session commit count 9 → 5, including the earlier read-only commits.
- **replay:** previous 7.56 / 7.82 / 19.63 ms; current **7.58 / 7.90 / 8.33 ms**, n=60 each. Session commit count 1 → 1, including the earlier read-only commits.

Continuation's smaller history-free reads are the clearest admission improvement. Plain admission and replay remain similar. Document tail latency worsened in this cohort; its new spans place the outliers in transaction commit rather than flush. Do not describe this as a universal speedup.

## Delivery

- **Result-page commit to generator receipt (polling): 220.59 / 432.28 / 477.41 ms**, n=20.
- **Result-page commit to generator receipt (triggered): 14.35 / 20.10 / 20.55 ms**, n=20.
- **Full local delegation to receipt (responses): 267.24 / 281.92 / 430.53 ms**, n=20. Task commit to receipt: 69.26 / 78.69 / 86.04 ms.
- **Full local delegation to receipt (client): 271.31 / 315.76 / 389.52 ms**, n=20. Task commit to receipt: 70.76 / 101.65 / 137.40 ms.

Global result commit hints broadcast to all viewers, whose DB reads remain page-scoped. This can wake unrelated page viewers and costs an extra read at higher publication volume. Missed/cross-process notifications retain a 500 ms recovery scan. Existing workers publish from another process, so worker-originated updates can still use that scan; this revision does not claim a 20 ms cross-process bound. A shared process notification transport or filesystem/IPC signal would add infrastructure and needs separate evidence before adoption.

## Remaining local stages

### plain

- task_dispatch: 31.07 / 34.09 / 42.87 ms.
- db_commit: 27.10 / 30.08 / 41.81 ms.
- db_transaction_commit: 26.51 / 29.44 / 41.19 ms.
- db_connection_open: 20.80 / 21.29 / 21.78 ms.
- db_connection_setup: 15.86 / 16.10 / 16.24 ms.
- focus_update: 12.64 / 13.06 / 13.21 ms.
- intent_reservation: 11.89 / 14.51 / 20.55 ms.
- intent_completion: 10.51 / 11.01 / 13.59 ms.
- result_lookup: 1.36 / 1.42 / 1.54 ms.
- db_read_close: 0.93 / 0.99 / 1.04 ms.
- db_flush: 0.56 / 0.62 / 0.64 ms.

### document

- task_dispatch: 43.54 / 44.55 / 51.63 ms.
- db_connection_open: 27.24 / 35.62 / 38.51 ms.
- db_commit: 26.39 / 119.57 / 262.22 ms.
- db_transaction_commit: 25.74 / 118.98 / 261.55 ms.
- db_connection_setup: 21.70 / 30.70 / 33.21 ms.
- focus_update: 13.01 / 20.68 / 247.17 ms.
- intent_reservation: 12.36 / 12.72 / 98.75 ms.
- intent_completion: 10.91 / 21.44 / 22.70 ms.
- result_lookup: 1.49 / 3.20 / 3.47 ms.
- db_read_close: 0.95 / 3.33 / 3.46 ms.
- db_flush: 0.59 / 0.83 / 0.91 ms.
- result_observation_enqueue: 0.04 / 0.04 / 0.04 ms.

### screen_ready

- db_connection_open: 55.97 / 66.45 / 88.64 ms.
- task_dispatch: 50.74 / 67.10 / 178.69 ms.
- db_connection_setup: 46.94 / 58.54 / 79.99 ms.
- capture_preflight: 22.25 / 27.29 / 28.11 ms.
- db_commit: 21.21 / 26.35 / 28.13 ms.
- focus_update: 21.08 / 28.23 / 36.77 ms.
- db_transaction_commit: 20.51 / 25.65 / 27.47 ms.
- intent_reservation: 12.31 / 13.18 / 19.26 ms.
- intent_completion: 11.49 / 19.75 / 21.60 ms.
- capture_binding_and_result_allocation: 9.03 / 9.49 / 10.78 ms.
- db_read_close: 5.00 / 8.14 / 222.71 ms.
- db_flush: 0.62 / 0.78 / 0.87 ms.
- result_observation_enqueue: 0.09 / 0.10 / 0.11 ms.

### continuation

- db_connection_open: 49.29 / 64.70 / 70.56 ms.
- db_connection_setup: 40.90 / 55.26 / 61.99 ms.
- db_commit: 27.94 / 36.57 / 294.75 ms.
- db_transaction_commit: 27.30 / 35.96 / 294.15 ms.
- task_continuation: 19.42 / 26.48 / 28.91 ms.
- task_resolution: 18.32 / 27.57 / 40.49 ms.
- intent_reservation: 12.76 / 22.70 / 31.28 ms.
- focus_update: 12.72 / 15.20 / 20.39 ms.
- target_pinning: 10.89 / 22.75 / 279.44 ms.
- intent_completion: 10.54 / 11.40 / 16.04 ms.
- result_lookup: 5.79 / 6.39 / 16.59 ms.
- db_read_close: 3.81 / 7.74 / 8.81 ms.
- db_flush: 0.57 / 0.63 / 0.64 ms.

### replay

- intent_reservation: 7.56 / 7.87 / 8.31 ms.
- db_connection_open: 4.35 / 4.56 / 5.03 ms.
- db_connection_setup: 3.17 / 3.31 / 3.40 ms.
- db_commit: 0.97 / 1.03 / 1.15 ms.
- db_transaction_commit: 0.94 / 1.00 / 1.13 ms.
- db_flush: 0.02 / 0.02 / 0.02 ms.

## SQLite and loop evidence

- Native open_ms: 0.77 / 0.82 / 1.61 ms, n=60.
- Native setup_ms: 1.87 / 1.99 / 2.06 ms, n=60.
- Native statement_ms: 0.03 / 0.03 / 0.03 ms, n=60.
- Native native_commit_ms: 3.34 / 5.04 / 7.31 ms, n=60.
- Native close_ms: 1.76 / 2.27 / 3.66 ms, n=60.
- Native total_ms: 7.79 / 9.70 / 12.17 ms, n=60.
- screen_ready db_transaction_commit: **115.33 ms**, 3 overlapping loop pulses; maximum excess over the 50 ms target **12.45 ms**.

Flush is sub-millisecond in the ordinary cohort. Native commit timings show that persistent slow commit throughput is not reproduced by the isolated primitive workload. Loop pulses during a long await can narrow whether the event loop was frozen, but do not separate aiosqlite worker scheduling, filesystem flush/checkpoint latency or external Windows activity. The exact storage/driver cause remains unresolved; do not attribute the entire gap to the provider or to telemetry. Read-close stalls can also include final-connection WAL cleanup.

## Next steps and tradeoffs

- Connection open/setup occupies meaningful cumulative time across repeated NullPool sessions. Keep the current policy until a bounded reuse experiment covers concurrent writers, cancellation, engine disposal, test event loops and process restart. Avoid reducing WAL/FULL durability to improve a benchmark.
- Focus, intent completion and routing events still use separate durable transactions. Combining them could reduce latency but changes recovery/failure boundaries; do this only with explicit same-task crash/replay coverage and a promising offline comparison.
- Cross-process result notifications remain the main limitation of the new result-stream hint. The in-process improvement is measured; a worker-to-API latency claim still needs its own check.
- Live admission remains unvalidated on this candidate. Restored credits should first fund a two/three-case targeted pilot after local tests, then the required full frozen-candidate cohort if that pilot passes. No unchanged paid corpus loops or model sweeps. See the runbook and AGENTS.md for the persistent testing policy.

## Verification

The broad offline suite passed 188 tests, including DB guards, commit signal broadcast/replay/rollback/cancellation, task controls, capture collection/partial failures, actual API restart/restore, and queued telemetry. The full offline profiling selection passed 12 tests with two interleaved telemetry tests deliberately deselected. The additional pulse-monitor gateway cohort is a focused diagnostic, not a live acceptance gate. Raw samples and the machine-readable current-summary.json are retained in data/admission-read-2026-10-05.

Regenerate from repo root: `python scripts/summarize-admission-read.py --prefix data/admission-read-2026-10-05/current.json --baseline data/admission-telemetry-2026-10-05/after.json --loop-prefix data/admission-read-2026-10-05/loop-50ms.json --output docs/jarvis-tasks/reports/admission-read-2026-10-05.md`.
