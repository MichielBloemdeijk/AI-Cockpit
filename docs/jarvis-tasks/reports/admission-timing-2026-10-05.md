# Admission timing investigation — 2026-10-05

This is the baseline before trigger-based delivery/readiness. The
[subsequent implementation and measurements](admission-triggered-2026-10-05.md)
supersede the SSE polling and optional-image waiting findings below.

Local admission is much shorter than the previous live speech-to-admission timings. The clearest local opportunities are the optional latest-image deadline, SSE polling, synchronous observation writes and repeated event/DB transactions. Most of the previous live delay lies in the combined speech/provider/transport interval, which remains unseparated.

## Scope and method

No paid API calls were made. Gateway/routing/capture profiling rejects socket network access. Mock streams supply completed tool calls; no worker model is launched. SQLite uses the runtime's WAL, synchronous=FULL and NullPool configuration. Three independent migrated databases contribute 20 measured samples per gateway case (60 per case); each excludes and retains three warmups. Routing has 20 measured samples per mode, SSE has 20 controlled polling offsets, and pending capture has five single diagnostic cases. There were zero unexpected benchmark failures; one required-current timeout was expected.

All numbers below are median / nearest-rank p95 / maximum in milliseconds. Spans nest and capture roles overlap; neither durations nor percentiles can be added to make an end-to-end result. Database/session totals are cumulative work across potentially overlapping sessions. These are development-host diagnostics, not live acceptance or a loaded-system SLA. Screen tails were traced to DB commits/observation sessions; the underlying disk/OS reason is not isolated. Preliminary GetTickCount64 artifacts are excluded from this report.

## Offline gateway totals

- **Plain task:** 75.53 / 77.95 / 92.79 ms (n=60).
- **Task with a new document result:** 115.76 / 126.77 / 140.68 ms (n=60).
- **Screen workflow with both captures already ready:** 192.31 / 408.42 / 470.70 ms (n=60).
- **Ordinary continuation of the same active task:** 108.89 / 145.47 / 447.51 ms (n=60).
- **Idempotent replay (original commit excluded):** 7.70 / 7.87 / 7.91 ms (n=60).

## Local routing and result delivery

These runs replay through the real adapter, transcript boundary/correlation, coordinator, gateway and result journal. Fixture model and ASR delay is zero; HTTP transport and provider sockets are mocked. The ordinary user transcript is still pending when delegation arrives, exercising the safe timeline boundary without the fixed settling delay. They do not test real tool choice or provider reliability.

### Managed routing fixture

- Delegation receipt to broker result return: 214.02 / 250.42 / 434.12 ms (n=20).
- Transcript boundary, origin correlation and pre-tool event handling: 77.15 / 78.34 / 88.77 ms (n=20).
- Completed tool JSON/schema validation: 0.09 / 0.10 / 0.11 ms (n=20).
- Validated call to shell start (includes durable event handling): 31.57 / 32.48 / 42.92 ms (n=20).
- Shell session/device authority reads: 6.95 / 7.19 / 7.31 ms (n=20).
- Gateway return to durable result journal and adapter return: 16.78 / 17.53 / 19.71 ms (n=20).
- Gateway work: 81.52 / 117.45 / 288.62 ms (n=20).

### Client routing fixture

- Delegation receipt to broker result return: 216.65 / 235.95 / 520.34 ms (n=20).
- Transcript boundary, origin correlation and pre-tool event handling: 78.80 / 79.71 / 79.72 ms (n=20).
- Completed tool JSON/schema validation: 0.08 / 0.09 / 0.11 ms (n=20).
- Validated call to shell start (includes durable event handling): 31.78 / 51.94 / 335.76 ms (n=20).
- Shell session/device authority reads: 6.98 / 7.27 / 7.75 ms (n=20).
- Gateway return to durable result journal and adapter return: 17.12 / 17.59 / 17.69 ms (n=20).
- Gateway work: 81.51 / 82.19 / 82.27 ms (n=20).

- **Result commit to SSE generator emission/receipt:** 256.62 / 436.11 / 522.32 ms (n=20).
- The production stream polls every 500 ms. The test varies insertion across that period; no HTTP framing, network or Android time is included. Maximum can exceed 500 ms because reads/scheduling add time.

## Gateway stage breakdown

### Plain task

Median DB session commit count (includes read-only sessions): 5.

- db_session: 75.33 / 77.75 / 92.58 ms (n=60).
- task_dispatch: 31.47 / 32.45 / 39.07 ms (n=60).
- db_commit: 28.16 / 31.08 / 45.26 ms (n=60).
- focus_update: 12.80 / 13.31 / 16.05 ms (n=60).
- intent_reservation: 12.03 / 12.44 / 23.52 ms (n=60).
- intent_completion: 10.71 / 11.25 / 13.99 ms (n=60).
- result_lookup: 6.57 / 6.80 / 7.30 ms (n=60).
- Durable task commit to gateway return: 31.95 / 33.37 / 35.11 ms (n=60).

### Task with a new document result

Median DB session commit count (includes read-only sessions): 7.

- db_session: 115.39 / 126.41 / 140.34 ms (n=60).
- task_dispatch: 71.10 / 78.84 / 92.46 ms (n=60).
- db_commit: 40.49 / 51.51 / 64.23 ms (n=60).
- result_observation_write: 26.84 / 29.47 / 47.18 ms (n=60).
- focus_update: 12.82 / 13.68 / 15.95 ms (n=60).
- intent_reservation: 12.41 / 13.51 / 16.05 ms (n=60).
- intent_completion: 10.64 / 13.66 / 22.57 ms (n=60).
- result_lookup: 6.65 / 6.88 / 7.73 ms (n=60).
- Durable task commit to gateway return: 58.78 / 64.80 / 82.96 ms (n=60).

### Screen workflow with both captures already ready

Median DB session commit count (includes read-only sessions): 14.

- db_session: 202.51 / 420.35 / 558.30 ms (n=60).
- task_dispatch: 80.22 / 95.91 / 300.12 ms (n=60).
- result_observation_write: 65.29 / 163.94 / 334.90 ms (n=60).
- db_commit: 61.63 / 277.48 / 370.97 ms (n=60).
- capture_preflight: 42.99 / 134.28 / 305.46 ms (n=60).
- focus_update: 12.80 / 13.25 / 18.60 ms (n=60).
- intent_reservation: 12.47 / 12.87 / 135.42 ms (n=60).
- intent_completion: 11.10 / 11.90 / 19.99 ms (n=60).
- capture_binding_and_result_allocation: 9.01 / 9.35 / 9.38 ms (n=60).
- Durable task commit to gateway return: 71.61 / 86.53 / 95.47 ms (n=60).

### Ordinary continuation of the same active task

Median DB session commit count (includes read-only sessions): 9.

- db_session: 108.56 / 145.10 / 447.15 ms (n=60).
- db_commit: 34.91 / 46.52 / 373.08 ms (n=60).
- task_resolution: 28.47 / 29.40 / 62.87 ms (n=60).
- task_continuation: 19.55 / 20.14 / 358.11 ms (n=60).
- focus_update: 12.80 / 13.50 / 18.40 ms (n=60).
- intent_reservation: 12.47 / 13.46 / 15.70 ms (n=60).
- target_pinning: 11.11 / 14.16 / 272.80 ms (n=60).
- intent_completion: 10.57 / 11.05 / 14.26 ms (n=60).
- result_lookup: 6.59 / 6.77 / 6.83 ms (n=60).
- Durable task commit to gateway return: 31.13 / 33.03 / 37.03 ms (n=60).

### Idempotent replay (original commit excluded)

Median DB session commit count (includes read-only sessions): 1.

- intent_reservation: 7.67 / 7.84 / 7.89 ms (n=60).
- db_session: 7.63 / 7.80 / 7.85 ms (n=60).
- db_commit: 0.97 / 1.06 / 1.12 ms (n=60).

## Pending capture diagnostics

Each case is one observation, so these are elapsed times, not p95 estimates. The readiness delay is deliberately injected locally; actual Android capture/encoding latency is not measured here.

- ready_after_100ms: **185.92 ms**, outcome `captured`.
- ready_after_300ms: **377.63 ms**, outcome `captured`.
- ready_after_1000ms: **1050.50 ms**, outcome `captured`.
- optional_latest_never_ready: **2037.10 ms**, outcome `optional_unavailable`.
- required_latest_never_ready: **2041.20 ms**, outcome `required_timeout`.

Both roles run concurrently. Capture readiness polls at up to 50 ms intervals. A ready invocation image does not prevent waiting the full two-second deadline for an optional latest image. After that timeout, optional latest proceeds as missing; explicit-current times out and fails. Capture polling/DB reporting also adds overhead to the injected 100/300/1000 ms delays.

## Timing gaps and instrumentation added

- **Windows clock resolution:** old Python 3.12 monotonic_ns uses GetTickCount64 (15.625 ms resolution on this host). Admission boundaries/spans now consistently use the high-resolution monotonic QueryPerformanceCounter via perf_counter_ns. Historical and new raw clocks must not be mixed.
- **Transcript readiness/correlation:** received fragment, delegation receipt and resolved user-turn timing now distinguish arrival from correlation/settling. The genuinely ambiguous/incomplete path still has bounded waits; the fixed fallback gap is 750 ms. Offline routing exercises the no-fixed-wait path.
- **Selector request/response:** client completed-call metadata includes actual HTTP start, response headers, response observation, item arrival and validation duration. This separates local pre-request persistence, HTTP/provider wait and generation. Managed response-created receipt is observable; its actual provider-owned HTTP start remains unavailable.
- **Gateway internals:** reservation, task resolution/pinning, capture preflight, admission/continuation, result allocation/lookup, focus, receipt completion, DB sessions/commits and observation writes are now spans with no new per-span DB writes.
- **Durable commit:** marked after the owning task transaction returns, before result observation writes/focus/receipt completion. Replayed deliveries keep the original commit and record their own gateway interval separately.
- **Post-gateway result handling:** shell authority and persisted result-record-start markers separate local journal work. SSE envelopes add emission stamps; the record-start marker precedes transaction commit, so record-to-emission must not be labeled pure queue time.
- **Android receipt:** stream callback entry and listener dispatch now have distinct timestamps. Admission logs retain session/generation/task/run identity plus server timing metadata, without subtracting raw server and Android timestamps.
- **Android captures:** existing request/callback observations now continue through encoding finish, capture-status HTTP start/end/success and upload completion. They can be measured with offline emulator capture tests; no such device measurements are claimed by this backend/JVM run.
- **Still unseparated:** last command input sample to actual outbound audio transmission, voice endpointing/ASR/delegation delay, one-way network transport/buffering and actual Android listener/capture execution. RTP counters alone do not identify the last-command packet. Cross-clock transport needs a supported media timeline or independently bounded clock/RTT measurements; unsynchronized subtraction is not a valid shortcut.
- **After admission:** upload/worker queue/execution remain separate barriers. New upload completion instrumentation must not be included in the admission timer; Android capture acquisition/status readiness before admission must be included.

## Earlier live evidence (historical, not this candidate)

The retained managed comparison had 17/20 ready successes, median 4241.66 ms, p95/max 5774.70 ms. Client had 19/20 successes, median 5001.26 ms, p95/max 7441.65 ms. Functionality failed in both; neither qualified. Managed observed selector start-to-arguments median/p95 was 1344/2062 ms and arguments-to-commit 188/375 ms. Its combined unassigned upstream/downstream interval was median/p95 2729/3727 ms. These values are separate distributions, not additive stage budgets. The final depleted-credit run produced no valid latency samples. Source: the progress ledger and retained routing acceptance artifacts.

## Where to reduce latency next

1. **Optional latest-image wait:** choose a short optional-image grace period or stop waiting when the required context is ready, freezing provenance and reporting the missing role. This can remove most of the measured two-second wait in this case. Keep explicit-current fail-closed behavior, per-turn binding, expiry/lock rules and promised-upload failure semantics.
2. **SSE delivery:** replace the fixed 500 ms sleep with a commit-triggered wakeup plus a bounded cross-process/recovery polling fallback. Verify targeted delivery, replay/reconnect, missed wakeups and ordering. This targets hundreds of milliseconds without changing selector decisions.
3. **Observation writes:** move best-effort result/capture observations off the user-facing critical path after their owning transaction commits. Screen admission performs about 65 ms of cumulative observation work at the median in this run; measure the final stage values below rather than treating this as guaranteed saved elapsed time. Retain durable task/capture authority.
4. **Repeated DB/event transactions:** examine event batching and bounded connection reuse. Keep WAL synchronous=FULL, intent durability, task ownership, leases/fencing and cancellation. Schema validation is around a tenth of a millisecond and is not a useful target.
5. **Provider path:** use the new breakdown for the next focused paid batch, after local changes pass offline tests. The old multi-second unassigned interval cannot be explained by the roughly 0.2 s local fixture path alone. No additional router or speculative/keyword dispatch is justified by these results.

## Validation and reproduction

The final profiling cohort passed seven tests (three independent gateway trials, two routing modes, SSE and trace isolation); pending-capture profiling passed separately. Earlier targeted backend regressions passed 78 tests; Android's 27 JVM tests and APK build passed. Final regression results are recorded in the progress ledger. API credits and M10 live acceptance remain blocked. Source/configuration changes mean previous frozen-candidate passes cannot be reused as current acceptance.

From backend, set JARVIS_OFFLINE_TIMING_REPORT to a fresh output prefix and run `uv run pytest tests/test_admission_timing.py -q`. Clear that environment variable after profiling. Generate this summary from repo root with `python scripts/summarize-admission-timings.py --prefix data/admission-timing-2026-10-05/final.json --output docs/jarvis-tasks/reports/admission-timing-2026-10-05.md`. Raw final artifacts and a machine-readable final-summary.json are in data/admission-timing-2026-10-05; they include all measured samples and stage spans. Source SHA256 values in the JSON summary describe reporting-time runtime sources; they are diagnostic provenance, not a frozen-candidate acceptance attestation.
