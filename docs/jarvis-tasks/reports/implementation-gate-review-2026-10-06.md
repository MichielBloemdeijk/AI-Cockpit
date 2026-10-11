# Implementation and gate review — 2026-10-06

The core M6–M9 contracts are implemented. The remaining work is acceptance
closure, focused reliability investigation and one acceptance-runner correction.
There is no demonstrated architectural blocker to the agreed goals, but the
programme is not implementation-complete until M10 passes. M11 physical devices
and normal-use deployment remain explicitly deferred.

This review inspected plans, runtime configuration, executable acceptance rules,
regression coverage and raw reports. One small offline runner probe confirmed a
tooling blocker. No paid tests or full acceptance suite ran. This assessment is
not an acceptance attestation.

## Milestone assessment

- **M0–M4:** historical guarded-route passes, with their original scope retained.
- **M5:** presentation/results platform verified historically; revalidate through
  M10 rather than designing another platform.
- **M6:** supervisor assignments, fenced project leases, journaled commands/edits,
  uncertain effects and descendant termination are implemented. Real coding
  fail/repair/pass/restart succeeded earlier. Current backend, coding-provider
  and process-tree assertions remain required. SQLite contention remains a risk.
- **M7:** managed-only routing, turn/intent ownership, validated tools, replay,
  shared preferences and internal screen preparation are implemented. The latest
  focused suite passed 129 tests and the current pilot verifies priority-tier
  routing. Full backend acceptance remains required.
- **M8:** real wake, capture, WebRTC, gateway and Android receipts are implemented.
  One current-source ordinary admission passed at **3.7746 s**. Full managed
  functional, capture and latency acceptance is still missing.
- **M9:** the dedicated subscription profile is saved; earlier actual subscription
  work and safe API continuation succeeded. Shared credential ownership exists.
  Current-candidate subscription/restart/API handoff proof remains required.
- **M10:** type-check, acceptance runner, evidence validation, migrations and
  readiness evaluation exist. Correct the runner issue below, then pass all
  required assertions on one frozen candidate.
- **M11:** deferred; it does not block development implementation closure.

## Confirmed acceptance-runner blocker

`run_acceptance()` includes all backend test files. `_command()` rejects skipped
required tests. However, **11 opt-in profiling cases** in
`test_admission_timing.py` and `test_sqlite_timing.py` skip when
`JARVIS_OFFLINE_TIMING_REPORT` is unset. The runner does not set that flag or
separate optional diagnostics from required contracts.

A network-free probe of `test_sqlite_timing.py` through the runner returned
pytest exit code **0**, one skip, and runner status **blocked**. Evidence is in
`data/implementation-gate-review-2026-10-06/optional-diagnostic-check.json`,
its JUnit XML and log. Collection found 12 tests across the two files: 11 opt-in
profiles and one ordinary timing-isolation contract test.

This is a real default-invocation M10 integration blocker, independent of credits
or provider speed. Explicitly classify optional diagnostics and separate them
from the required backend suite. Retain the ordinary timing-isolation test and
strict no-skips validation for every required assertion. Keep profiling separately
runnable and retain its failures. Do not ignore the whole timing module or relax
required gates. No executable repair was made during this review.

## Reliability and performance gaps

The managed-only broad run exposed an uncaught SQLite lock in overlapping-screen
collection and two best-effort observation-write timeouts. Isolated checks and
the final 129-test suite passed, but contention has not been proven repaired.
M6 prohibits uncaught SQLite locks. Investigate authentication/device-activity
writes and their interaction with observation writers before freezing the
candidate. Metric loss and failed durable admission are separate outcomes.

The latest pilot has only **225 ms** headroom to four seconds. M8 still needs:

- At least **20 managed ready admissions**, no failed required cases, and p95
  strictly below **4000 ms**. **2000 ms** remains a target.
- **Five independent cold starts**, with timings and failures reported separately.
  The four-second threshold applies to ready admissions.
- All **27 fixed corpus cases**: ten general, ten screen, and ambiguity, nonmatch,
  multiple outcomes, control, question, follow-up and fresh screen.
- Real screen A → clarification → screen B admission/continuation and the plural
  image/upload/worker context, privacy, expiry and fencing contracts.
- Controlled capture performance: at least 20 observations in each required group,
  startup p95 difference no more than 150 ms, normalized equal 20-second frame
  windows, and valid callback/upload/audio/ANR observations.

The earlier managed corpus failed **ready02, ready14, ready20 and multiple**.
Those failures are on different source; they are priority regression cases, not
proof the current code still fails. The latest ordinary task uploaded no images
and executed no worker, so it cannot prove screen/coding/recovery acceptance.

## Evidence and setup reconciliation

Current executable source digest is
`c53d20dce3d4713227308f0fcf7d037073eb92b34193e430e9cb5e0646435f36`,
matching the final managed pilot. Its declared emulator-profile candidate is
`e320e081f7151981f3d6ea003ef82cab26bb7a5fdb0b6318f2275620b2c221dc`.
Ordinary CLI configuration has a different candidate ID. The shared profile
manifest governs permitted differences; equal source alone does not waive
configuration, runtime, backend instance, database or 24-hour freshness checks.

M10 requires **17 assertions**:

- Local contracts/build/recovery: `backend`, `frontend_types`, `frontend_tests`,
  `frontend_build`, `result_shell`, `android`, `migration`, `process_tree`.
- Browser integration: `browser`.
- Provider execution: `coding_provider`, `subscription_provider`, `voice_provider`.
- Voice/capture: `audio_corpus`, `ready_latency`, `cold_latency`, `fresh_screen`,
  `capture_performance`.

The current pilot has one passed provider observation, insufficient ready samples,
a blocked partial corpus, and none of the other 14 assertions. Evaluation against
its own candidate remains blocked. Historical combined v3/v4 runs have different
source. Their platform/type/Android/process/migration passes support implemented
functionality, but do not close current gates. V4's depleted-credit failures are
historical; restored credits and current voice success supersede a current voice
billing-blocker claim. This does not guarantee future funding for every route.

The development database is at **20261003_0036**, the latest source migration.
Current command mode is explicitly **trusted**, with an **unelevated** model host.
Commands inherit Windows-user access; brokered edits, ownership, journaling and
process-stop contracts remain required. Native sandbox repair and spending-limit
proof are not outstanding requirements. The separate Jarvis authentication file
is present and was used successfully earlier. No new login is currently requested.

## Recommended closure sequence

1. Correct optional-diagnostic/required-suite separation and verify the collected
   command. Investigate SQLite contention offline with capture/auth/observation
   fixtures, preserving failure evidence.
2. Run required local contracts and builds, including cancellation, lease transfer,
   recovery, descendant termination, frontend types and migration. Keep paid
   broad suites disabled during preparation.
3. If local results are stable, target the previously failing screen/multiple
   requests with a small bounded managed pilot. Stop on regression or actual
   auth/quota/billing failure; do not rerun unchanged failures for a pass.
4. Freeze source/runtime/declared configuration, then run the required managed
   cohort, capture performance, actual coding/subscription/API handoff and combined
   M10. Reuse valid same-candidate evidence within its permitted scope/freshness.
5. Close M6–M10 only when their required assertions pass. Leave M11 deferred.

No account action is needed to begin. Actual runtime auth or billing failure may
require user help, but is not assumed. No new router, budget engine, VM/container,
physical testing or manual spoken corpus is required. The thirty-second recipe
objective remains soft; below-four-second ready p95 remains the admission gate.
