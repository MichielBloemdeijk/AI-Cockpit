# Managed offline admission timings — 2026-10-06

The local flow uses the real adapter, transcript correlation, coordinator, gateway, durable journal and SSE generator with zero-delay provider fixtures. It excludes real voice/model delay, HTTP framing, network transport and Android receipt. It is diagnostic evidence, not live latency acceptance.

Gateway/routing/capture fixtures reject socket network access. No paid provider or worker calls are made. Each of three independent migrated SQLite databases contributes 20 measured samples per gateway case; routing and full flow each retain 20 measured samples plus three warmups. SQLite uses the runtime WAL, synchronous=FULL and NullPool configuration.

Failures recorded in completed core profile artifacts: **0**. Expected required-current capture timeouts: **1**.

This artifact count is not the overall pytest result. Failed diagnostic comparison tests can leave no artifact; retain the suite result and missing observations separately.

All values are median / nearest-rank p95 / maximum. Nested stage work and percentiles cannot be added to form an end-to-end total.

## Full local managed flow

- Delegation to broker return: **653.59 / 1081.78 / 1466.70 ms (n=20)**.
- Boundary/correlation and pre-tool handling: **193.11 / 469.56 / 482.11 ms (n=20)**.
- Completed tool validation: **0.13 / 0.33 / 1.75 ms (n=20)**.
- Validated call to shell start: **71.68 / 267.26 / 725.88 ms (n=20)**.
- Session/device authority: **18.96 / 82.18 / 83.02 ms (n=20)**.
- Gateway return to journal and adapter return: **29.44 / 43.28 / 129.68 ms (n=20)**.
- Gateway: **179.56 / 621.03 / 716.78 ms (n=20)**.
- Delegation to simulated client SSE receipt: **662.19 / 1114.66 / 1476.28 ms (n=20)**.
- Task commit to SSE receipt: **116.26 / 330.59 / 524.77 ms (n=20)**.

## Managed routing without delivery

- Delegation to broker return: 1197.94 / 2365.05 / 2463.60 ms (n=20).
- Boundary/correlation and pre-tool handling: 284.02 / 851.38 / 966.26 ms (n=20).
- Completed tool validation: 0.14 / 0.21 / 0.34 ms (n=20).
- Validated call to shell start: 78.63 / 799.43 / 829.44 ms (n=20).
- Session/device authority: 20.90 / 67.52 / 182.98 ms (n=20).
- Gateway return to journal and adapter return: 44.66 / 144.71 / 822.14 ms (n=20).
- Gateway: 408.99 / 1701.88 / 1819.50 ms (n=20).

## Gateway cases and stages

### continuation

Total: **105.38 / 131.40 / 521.21 ms (n=60)**.

- db_session: 105.08 / 131.13 / 520.89 ms (n=60).
- db_connection_open: 50.41 / 65.88 / 91.01 ms (n=60).
- db_connection_setup: 41.67 / 58.39 / 61.75 ms (n=60).
- db_commit: 29.22 / 41.87 / 440.70 ms (n=60).
- db_transaction_commit: 28.58 / 41.22 / 440.04 ms (n=60).
- intent_reservation: 13.71 / 34.80 / 60.66 ms (n=60).
- task_continuation: 20.09 / 32.27 / 427.80 ms (n=60).
- task_resolution: 17.85 / 26.11 / 37.44 ms (n=60).
- target_pinning: 11.64 / 21.79 / 23.56 ms (n=60).
- focus_update: 13.05 / 14.41 / 19.13 ms (n=60).
- intent_completion: 10.89 / 13.92 / 20.29 ms (n=60).
- db_read_close: 3.99 / 8.34 / 21.38 ms (n=60).
- result_lookup: 6.08 / 6.50 / 13.45 ms (n=60).
- db_flush: 0.60 / 0.67 / 0.85 ms (n=60).

### document

Total: **100.03 / 123.19 / 485.67 ms (n=60)**.

- db_session: 99.78 / 122.91 / 485.31 ms (n=60).
- task_dispatch: 45.19 / 47.92 / 425.36 ms (n=60).
- db_connection_open: 27.99 / 37.95 / 57.71 ms (n=60).
- db_commit: 26.38 / 33.67 / 404.97 ms (n=60).
- db_transaction_commit: 25.69 / 33.06 / 404.31 ms (n=60).
- db_connection_setup: 22.06 / 32.57 / 41.20 ms (n=60).
- intent_completion: 16.81 / 25.33 / 30.56 ms (n=60).
- focus_update: 13.37 / 17.30 / 22.15 ms (n=60).
- intent_reservation: 12.71 / 15.79 / 94.51 ms (n=60).
- db_read_close: 0.89 / 3.44 / 4.27 ms (n=60).
- result_lookup: 1.58 / 3.42 / 3.72 ms (n=60).
- db_flush: 0.61 / 0.84 / 1.14 ms (n=60).
- result_observation_enqueue: 0.04 / 0.06 / 0.07 ms (n=60).

### plain

Total: **76.42 / 82.98 / 382.11 ms (n=60)**.

- db_session: 76.22 / 82.73 / 381.91 ms (n=60).
- task_dispatch: 32.09 / 34.00 / 72.52 ms (n=60).
- db_commit: 27.52 / 31.23 / 333.50 ms (n=60).
- db_transaction_commit: 26.86 / 30.56 / 332.89 ms (n=60).
- db_connection_open: 21.74 / 22.65 / 203.33 ms (n=60).
- db_connection_setup: 16.66 / 17.25 / 24.37 ms (n=60).
- intent_reservation: 12.22 / 15.41 / 20.69 ms (n=60).
- focus_update: 13.10 / 14.10 / 33.09 ms (n=60).
- intent_completion: 10.81 / 12.47 / 317.70 ms (n=60).
- result_lookup: 1.45 / 1.56 / 2.04 ms (n=60).
- db_read_close: 0.95 / 1.09 / 1.32 ms (n=60).
- db_flush: 0.59 / 0.67 / 0.81 ms (n=60).

### replay

Total: **7.88 / 8.16 / 16.46 ms (n=60)**.

- intent_reservation: 7.86 / 8.14 / 16.42 ms (n=60).
- db_session: 7.82 / 8.09 / 16.35 ms (n=60).
- db_connection_open: 4.50 / 4.76 / 11.67 ms (n=60).
- db_connection_setup: 3.31 / 3.47 / 4.73 ms (n=60).
- db_commit: 1.00 / 1.15 / 1.42 ms (n=60).
- db_transaction_commit: 0.97 / 1.12 / 1.38 ms (n=60).
- db_flush: 0.02 / 0.02 / 0.02 ms (n=60).

### screen_ready

Total: **150.70 / 195.93 / 359.22 ms (n=60)**.

- db_session: 142.60 / 183.66 / 351.35 ms (n=60).
- task_dispatch: 53.23 / 90.14 / 132.50 ms (n=60).
- db_connection_open: 54.61 / 69.23 / 138.20 ms (n=60).
- db_connection_setup: 46.11 / 61.48 / 88.19 ms (n=60).
- db_commit: 22.20 / 33.28 / 243.95 ms (n=60).
- db_transaction_commit: 21.50 / 32.32 / 243.27 ms (n=60).
- focus_update: 17.11 / 29.08 / 48.89 ms (n=60).
- capture_preflight: 23.11 / 28.63 / 55.66 ms (n=60).
- intent_completion: 11.69 / 26.75 / 154.47 ms (n=60).
- intent_reservation: 12.67 / 17.17 / 24.28 ms (n=60).
- capture_binding_and_result_allocation: 9.26 / 10.62 / 13.37 ms (n=60).
- db_read_close: 1.87 / 9.09 / 19.85 ms (n=60).
- db_flush: 0.64 / 0.92 / 0.99 ms (n=60).
- result_observation_enqueue: 0.09 / 0.11 / 0.15 ms (n=60).

## Delivery and capture

- Event commit to SSE generator receipt: 8.88 / 10.40 / 12.12 ms (n=20).
- Capture readiness delays below are injected fixtures, each observed once; they are not p95 estimates.
- ready_after_100ms: 137.73 ms; outcome `captured`.
- ready_after_300ms: 327.61 ms; outcome `captured`.
- ready_after_1000ms: 1012.76 ms; outcome `captured`.
- optional_latest_never_ready: 34.74 ms; outcome `optional_unavailable`.
- required_latest_never_ready: 2035.28 ms; outcome `required_timeout`.

## Timing limits and reproduction

Managed response-created and streamed item arrival are observable. The provider-owned request start, internal scheduling/prefill and one-way network delay remain unavailable; do not invent them or subtract unsynchronized clocks. User audio endpointing, activation-buffer residence and Android capture/receipt require their own observations. Upload and worker execution remain separate from admission.

From backend, set JARVIS_OFFLINE_TIMING_REPORT to a fresh project data prefix, run `uv run pytest tests/test_admission_timing.py -q`, and clear that environment variable. From repo root, run `python scripts/summarize-admission-timings.py --prefix <prefix>.json --output <report>.md`. Raw samples retain warmups. Reporting-time source hashes are diagnostic provenance, not frozen-candidate acceptance.
