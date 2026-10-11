# Autonomous Testing and Observability

Status: completed for the accepted personal-use scope on 2026-10-11. The
[completion ledger](00-implementation-roadmap.md) owns status and accepted
limits; this document retains the phase contract and historical evidence.

## Completion

This phase is complete under the [final personal-use acceptance](reports/personal-use-accepted-2026-10-11.md).
The [reports index](reports/README.md) retains candidate-scoped verification,
failed observations and the accepted M8 caveats. Historical implementation
notes below do not assign new work or override the completion ledger.


## Outcome

Let a Codex agent implement, run, diagnose, and demonstrate workflows unattended after one-time host setup, with desktop computer/browser control reserved for exceptional diagnostics and final visual review.

Start telemetry with M0's narrow native Codex contract probe, then deterministic control/capture tests in the integrated slice. Prove capture, admission, shared-pool execution, directory reservations, cancellation, and checkpoint recovery using a fixture executor and minimal result view. Repeat the native checks through the actual task services before broad workflow/presentation expansion. Phase 4 specifies the facade, configuration schema and loader. A deterministic fixture alone cannot demonstrate native cancellation or persistence behavior.

Verify the selected guided-agent workflow implementation. Comparative A/B tests and a multi-harness bakeoff are not prerequisites. Normal correctness, latency, and offline quality checks remain part of acceptance.

[Phase 8](08-background-orchestration.md) supplies the autonomous background-management and shared-attention acceptance cases. Its tests start once task APIs/events exist, alongside the early integration slices rather than after recipe completion.

## M4/M5 scope and autonomous acceptance

Use six compact reference scenarios: clear sourced completion, material ambiguity and resume, partial/all source failure, invalid output and correction, context-reusing follow-up, and failed/cancelled update preserving the previous result. Include an explicit user constraint and verify compliance or a truthful unsupported outcome. Recipe features such as serving conversion, dietary substitutions, equipment adaptations and shopping-list controls do not require dedicated acceptance suites.

Retain adversarial-content, URL safety, permissions, recovery, cancellation, stale-publication and billing/error coverage in shared capability/platform suites. Reference their evidence from workflow reports rather than duplicating those cases for each domain. Existing capture and pool suites continue to own their respective guarantees.

Prove extensibility by adding `research_screen@1` after the shared capabilities and recipe example: only its definition, instructions, fixtures and enablement configuration may be added. Verify generated catalog discovery/routing, shared retrieval and `document.v1` publication without changing core services or routing prompts. Test compatibility with existing manifests and pinned runs.

Use a small fixed set of representative real inputs with repeated ready and cold samples, through agent-runnable provider and emulator tooling. Reports include sample counts, completed/clarified/failed outcomes, total/stage latency, source support, useful completeness and every nested model/tool call. Assess semantics against retained source evidence with a recorded rubric and identify the assessment method; neither fixtures nor schema validity alone prove live usefulness. Subjective human recipe review is optional feedback, not an M4 or reference-workflow enablement dependency.

Keep the soft thirty-second ready-path objective. Investigate demonstrated shared-path bottlenecks with bounded tuning, report misses and avoid claiming production p95 from a small sample. No dedicated production grading loop or exhaustive culinary evaluation is required.

M5 verified the generic catalog, collection search/pagination, registered actions and namespaced storage. M10 repeats Documents/Recipes through the common UI and saved flag. M8 owns real voice/capture acceptance, M9 requires subscription/account evidence, and M11 records the completed personal phone acceptance.

## Test layers

- Pure contract tests: manifest, schemas, validators, routing corpus, and capability fakes.
- Backend integration: real repositories, migrations, admission, workers, progress inspection, per-action checkpoints, events, retained context, results, retry, cancellation, and recovery in isolated data.
- General task continuation: stable task identity across completed and follow-up turns; separate execution runs and recovery attempts; clarification within the requesting run; serialized concurrent follow-ups; retained/compacted context after worker/provider-session loss; resources reacquired before editing; late old-run events/controls fenced. Cover both general-agent and workflow tasks.
- Task controls and resources: shared-pool saturation without reserved capacity or automatic priority preemption; overlapping directory exclusion; default/immediate pause and yield; release only after confirmed writer stop; retained ownership during immediate crash handoff; reacquisition and change checks on resume.
- Recovery: direct process-exit detection, five-second heartbeat/twenty-second stale fallback, completed-result reuse, bounded safe transient retries, partial/uncertain interrupted actions, one automatic recovery without progress, and durable recovery-needed attention when no model is active. Assert cancel/immediate controls do not wait for heartbeat expiry or a fixed grace period.
- Codex adapter: pin SDK/runtime versions; verify native thread/turn/item mapping, persisted session storage across worker/host restart, compatible native resume, and portable restoration when native history is unavailable. Exercise real native shell/file writers and descendant termination, capability restrictions, and durable completion before dependent work. Preserve uncertain outcomes rather than treating event receipt as an exactly-once guarantee.
- Mobile task visibility: initial snapshot plus live activity, cursor replay/reconciliation without duplicate items, retained messages/tool results/errors and available reasoning summaries, and follow-up/control actions under the same task identity. Disconnecting the viewer or Live session must not cancel work; Jarvis inspection sees the same durable evidence.
- Model and cost controls: default settings, explicit and Jarvis-selected overrides, unsupported-setting rejection, recorded configuration changes, quota-unavailable decisions, permitted API fallback, and budget-denied fallback. Fixture these cases without real quota exhaustion or paid calls. Real authentication/model checks remain opt-in and use supported account flows.
- Background orchestration: absent voice with autonomous question resolution/recovery; deliberately busy background inference with independent voice admission/answers/controls, event intake, and factual forwarding; one automatic feedback owner; long repairs delegated to workers; durable replay after restart; no model calls for healthy heartbeats or repeated unchanged issues.
- Shared attention and concurrent decisions: exact update identities across reconnect, announced-but-unanswered questions, superseded presentation acknowledgments, user cancellation versus autonomous retry, and user answers/corrections versus stale autonomous actions. Assert no task lock is held across inference and no duplicate action is committed on replay.
- Capture admission: metadata available to routing before task allocation; captured-before-admission gating; at-most-two-second pending wait; no task/result on rejection; no automatic capture/upload retries; backend-owned failure/expiry after admission without a worker; duplicate/late report races and explicit Jarvis retries.
- Fake-Live replay: recorded completed tool events through the real adapter/coordinator/gateway.
- Live capability evals: opt-in billable model/search calls with frozen cases and recorded quality/latency/usage.
- Result UI: component tests and Playwright against the maintained route.
- Follow-up output: same-URL revisions, additions under the same task, questions without unsolicited revisions, relevant evidence reuse without another screenshot, and targeted retrieval when new criteria require it.
- Android emulator: one instrumentation-owned vertical slice through assistant invocation, production screenshot callback, upload, Live/event contracts, result WebView, notification, and edit.
- Physical device: periodic manual acceptance for wake, microphone, OEM behavior, protected content, network, battery, and notification delivery.

## Android harness

- Add an API 37 Google APIs x86_64 project AVD definition and scripts that use explicit AVD names and never modify unrelated devices.
- Add a small fixture app that displays deterministic visual markers, dish fixtures, low-quality content, and protected-content cases.
- Add an instrumentation module using UI Automator/Espresso APIs and shell role commands instead of fixed screen coordinates.
- Add a debug-only pairing bootstrap that installs a real scoped device credential into the normal credential store against an isolated backend. It is unavailable in release builds and normal production configuration.
- Add a debug-only prerecorded audio source at the installed WebRTC library's input-buffer seam and playback sample timestamps at its output seam. The request still traverses the real RTP/session path.
- Prove screenshot provenance by decoding the fixture marker from the workflow upload. ADB screenshots are evidence only and never workflow input.
- Capture logs, task/result IDs, correlated trace IDs, screenshots, page snapshots, and a short emulator recording on success or failure.

## Codex interface

Use the repository CLI for validation, fixtures, live evals, the dedicated AVD/demo and reports. M10 adds one acceptance entry point composing these suites. Scaffolding and convenience wrappers remain optional. Commands are non-interactive after prerequisites are present and return nonzero for incomplete required gates.

Candidate workflows are disabled by default. The CLI enables an exact candidate version only inside its isolated test environment; it cannot alter production enablement.

Use `python -m app.jarvis_tasks` from the backend environment. Current and planned subcommands:

- `probe-codex --report <path>` (implemented): version/tool/auth inventory and local containment tests; actual model calls require `--live`. Missing required native/model evidence produces an incomplete report and nonzero gate status, not a false pass. The implementation uses the pinned `codex-probe` dependency group and refuses to use the desktop Codex profile directly.
- `validate <workflow-id>@<version>` (implemented): strict manifest/schema/capability/fixture checks, no provider calls.
- `rollout-check --report <path>` (implemented): read-only, redacted configuration/assets/schema inspection. Reports unresolved account/evidence gates; accepts current M10 evidence and never changes enablement.
- `migration-rehearsal --report <path>` (implemented): snapshots the configured SQLite database read-only, migrates only a disposable clone and checks retained identities/content and foreign keys. Removes the temporary private clone; not production migration or a retained/full backup.
- `rollout-rehearsal --report <path>` (implemented on the pinned Windows host): isolated WAL backup/restore, actual API restart, history/result/saved-state retention, migration replay and orchestration disablement tests. Installs the process-tree/parent boundary before tests start; no paid providers or production mutations. Missing/skipped/failed evidence cannot pass. See the [operational runbook](rollout-runbook.md).
- `fixtures <workflow-id>@<version> --report <path>` (implemented for both M4 examples): selected deterministic cases through real task/result services in isolated storage; no network or UI.
- `eval <workflow-id>@<version> --live --samples 2 --report <directory>` (implemented for both M4 examples): fixed tomato-pasta/lentil-soup inputs, initial publication and retained-profile same-task revision, including worker/search usage. Each attempt starts an App Server. Use a fresh report directory; failed outcomes are not erased or automatically retried. Summaries identify small sample counts, median/p95 and unknown invoice cost. `total` native token usage is cumulative per thread, not additive across resumed attempts.
- `android demo --workflow <definition> --report <path>` (implemented): forwards to the existing isolated API37 harness, including automatic dedicated-AVD startup, screenshot, notification navigation, WebView and reconnect.
- `acceptance --gate M6|M7|M8|M9|M10 --report <directory> [--live] [--emulator SERIAL --audio-dir PATH --database PATH]`
  (implemented, closure evidence recorded): composes
  the required suites and writes candidate-bound assertions. M10 includes all
  M6–M9 checks and final cross-phase regression. Required disabled/unavailable
  layers report `blocked`, never a successful reduced-scope completion.
- `rollout-check --evidence <acceptance-report> --report <path>` (implemented): evaluates implementation evidence and local readiness, while
  separately reporting M11's physical/deployment state. Evidence cannot grant
  permission to enable production or alter account settings.
- `integration --suite ...`, custom `eval --cases`, `android prepare` and `report <run-id>` remain optional CLI conveniences, not implemented subcommands. The underlying integration gates run directly through pytest and the Android harness; missing wrappers do not represent missing runtime acceptance. A scaffold helper is also deferred.

Each run has a unique isolated database/artifact/workspace root and explicit profile. Production data and desktop Codex credentials are never test fixtures. Reports contain assertion IDs, pass/fail/blocked status, dependency versions, resolved settings, failure details and artifact references. Use existing pytest/service tests behind the CLI; do not implement a competing test runner. A scaffold helper can be added once the manifest is stable; it is not a dependency for the first workflow.

M0 must explicitly test file-based credential refresh ownership versus concurrent hosts, supported session continuation across configured auth modes, and restrictions against native-tool escape. Authentication failure does not cause copied credentials or silent mode changes. Fixtures cover budget exhaustion without buying credits or actually exhausting the account. A live probe can validate availability without claiming it has proved every future provider limit.

Migration tests start from a representative existing database with queued, running, waiting-question, waiting-time, completed and failed tasks. Verify stable IDs/attempt history/result links and continuation after backfill. Exercise additive feature disablement with active work: stop claims, confirm writer ownership, preserve sessions/results and avoid old-executor claims of incompatible checkpoints.

## Telemetry and reports

### M8 speech-to-admission measurement

Use a fixed, versioned corpus containing positive recipe/research/general-task
requests, continuations, negative matches, ambiguous requests, multiple intents,
controls and questions. Predeclare expected outcomes and ready/cold cohorts;
do not select only fast successful samples after the run. Require at least 20
positive ready admissions and five separate cold starts, plus the negative and
failure cases. A ready Live session is connected before the command begins;
cold custom-wake session setup is measured separately, not subtracted from it.

The command-end timestamp is the last spoken-command sample entering the Android
production audio path. End the admission timer when that client receives the
correlated event/result proving durable admission and its task/run/result IDs.
Use Android's same monotonic clock for both. This includes capture waiting and
return transport; it is not model/tool completion, speech acknowledgment or worker
start. Measure backend stages on their own monotonic clock, never subtract raw
timestamps across machines. Record audio handoff/playback observations separately.

Backend admission traces use `time.perf_counter_ns()` on Windows, declared as
`clock_source=python.perf_counter_ns`, rather than the 15.625 ms GetTickCount64
clock used by Python 3.12's `monotonic_ns()`. Do not mix old/new clock sources or
join different backend process incarnations. The durable commit marker precedes
observation writes and focus/result/receipt completion. Replays preserve the
original commit and set `commit_is_original`; their current delivery spans must
not become fresh-admission samples.

The `admission_timing` metadata contains gateway boundaries and nested spans for
intent reservation/completion, target resolution/pinning, capture preflight,
task dispatch/continuation, result allocation/lookup, focus, DB sessions/commits
and result observations. Spans may nest or overlap: do not sum
them or their percentiles. It adds no per-span database writes. Detailed timing
is removed from provider tool outputs so diagnostics cannot inflate model input.

Database timing now separates `db_flush` and `db_transaction_commit` within the
existing `db_commit` envelope. `db_connection_open` includes the new connection
and its nested `db_connection_setup` PRAGMAs. `db_read_close` records lookup-only
cleanup. These nested intervals must not be summed into extra elapsed time.
Read-only gateway/resolver/result-stream scopes detach loaded rows and close
without committing; tracked ORM changes, flushes, mutations and explicit commits
are rejected. This is an application invariant, not an untrusted-SQL sandbox.

Known task-ID resolution reads the task row rather than loading all history;
mutation services retain their authoritative locking/status checks. Result URL
lookup can reuse an already selected turn after checking its task/request identity.
The separate result-page stream now uses commit signals with bounded recovery
rescans; page scoping, sequence replay, rollback and disconnect cleanup remain
covered. Its cross-process recovery deadline is still 500 ms.

Client routing also records actual HTTP start, headers and response observation
in the completed-tool event. Managed routing's actual request start remains
unobservable; response-created receipt must not be relabeled as request start.
SSE frames carry server emission metadata. Android records receipt at stream
callback entry, listener dispatch, capture encoding/status reports and upload
completion. SSE record-to-emission includes the result event's transaction;
raw server/client timestamp subtraction still cannot establish one-way transport.

Local event-stream delivery and required capture readiness now wake on committed
state changes. Signals are hints: readers snapshot before reading, broadcast to
all listeners, and keep bounded recovery scans for other processes/missed hints.
Rollback/savepoint release must not wake readers as though the outer writer had
committed. Required captures retain the two-second failure deadline; optional
roles get a single readiness check after the required role is ready, at collection
freeze, and never extend admission. Missing optional roles stay explicit. Lock,
expiry, session/generation and per-turn provenance are still revalidated.

Capture/result diagnostics now enqueue after the owning transaction stops. A
loop-local consumer batches independent metadata transactions: at most 512 queued
jobs plus a 32-job active batch, with a 20 ms background batching window. Request
traces/cancellation scopes do not propagate to that consumer. Allocation and
notification metadata freeze run/revision identity; deferred publication lookup
resolves the owning run's revision, never a later active run's identity.

Diagnostic persistence is eventual. Tests/assertions that require complete metric
evidence must explicitly drain the observation queue outside latency measurement.
Ordinary endpoints do not drain it. Snapshot `observation_queue` health describes
the current process/event loop; it reports pending/completed/dropped/failed counts
and bounded queue-wait/batch-write timing windows. A missing observation remains
unknown. Publication, task ownership and result/event journals are not queued.

Database close rejects new jobs and bounds draining to two seconds, reports losses,
then finishes cancellation/connection cleanup before disposal. Crash durability
is not guaranteed for diagnostics. One fresh-transaction retry is allowed for
SQLite busy/locked errors only, with idempotent metric keys. Cover queue overflow,
writer failure, shutdown drain/timeout, cancelled drain, request context isolation,
delayed run/revision binding and actual API-process restart.

Retain every functional sample/outcome and report descriptive median/p95/maximum when available. The user-approved ready latency gate for this iteration is **one final end-to-end ready admission below 4,500 ms**, including capture readiness, measured after the functional/cold scenarios. A diagnostic single-case run can supply that timing assertion; it does not establish p95 or pass the remaining M8 assertions. Five independent cold starts and the fixed functional corpus remain required.
Unexpected failures/timeouts also fail functionality and cannot be discarded to pass latency. Report cold timing and per-workflow distributions
separately. Run Phase 3's callback/Live-ready-overhead/fresh/upload checks too.
Small samples establish the defined acceptance result, not a sustained SLA.
Managed Responses delegation is the only routing path after the user's decision.
Retain all twenty ready functional cases and five independent cold starts; the separate final ready sample supplies the latency assertion.
If the final managed admission misses 4.5 seconds after one focused correction, retain the failed evidence
and report the stage breakdown and provider delay before expanding scope.

### M10 current-candidate acceptance evidence

M8 requires eight assertions: Android JVM/build, functional audio corpus, final
ready latency, cold latency, fresh-screen continuation, capture performance,
native capture privacy and the real sourced result/notification/reopen/UI
follow-up journey. M10 includes nineteen assertions. The shared configuration
manifest declares the isolated emulator, mobile-worker and network-free native
privacy profiles. Prevalidate supplied proofs before paid stages and stop on a
failed required sample; do not continue into capture performance after failed
audio execution.

`acceptance` orchestrates existing tests and harnesses. The required
`npm run typecheck` uses `tsc --noEmit`; all frontend tests/e2e fixtures must type
check without excluding failing files or disabling their checks. Run frontend
tests, both builds, Android JVM/build and maintained browser acceptance alongside
backend and gate-specific provider/emulator tests. Routine CI runs deterministic,
type-check and browser layers; configured provider/emulator jobs are explicit.
A hosted CI account is not needed to run the full gate on the prepared local host.

Reports carry schema version, candidate ID, source revision plus a content digest
covering relevant modified/untracked source and definitions, non-secret effective
configuration digest, runtime/SDK/image versions, suite commands, timestamps,
assertion IDs, outcomes and evidence references. Exclude credentials/private
content from digest inputs exposed in reports; do not hash secrets as an apparent
redaction. Gate-specific configuration differences are recorded as part of the
same candidate manifest. Build artifacts, output reports and private test data
do not make the candidate identity circular.

Use `pass`, `fail`, `blocked` or `deferred` with reasons and dependency owners.
Missing, empty, skipped, mismatched-candidate or invalidated evidence cannot pass
a required gate. A relevant source/runtime/configuration change invalidates prior
acceptance; historical reports stay available without being relabeled current.
No account cutoff-proof gate is required; provider account settings own spending limits.
M9 needs actual authorized profile/continuation evidence, not just fixture auth.

The runner evaluates M6–M9 and all phase contracts for the current candidate,
then M10's cross-phase regression and local preparation. It exposes separate
`implementation_ready` and `physical_deployment_ready` results. These runtime
checks keep their declared evidence requirements; the user's M11 acceptance
is recorded separately and does not manufacture candidate-matched proof. The existing
always-blocked `rollout-check` becomes evidence-aware in M10; no report supplies
device/production authorization. Rehearsal success alone cannot pass deployment.

Full invocation after the emulator, corpus and account evidence are prepared:

```powershell
python -m app.jarvis_tasks acceptance --gate M10 --live --emulator emulator-5554 --audio-dir <rendered-corpus> --database <isolated-backend.db> --backend-url http://127.0.0.1:<isolated-backend-port> --report <new-report-path>
```

Run layers against isolated fixtures/profiles; report required prerequisites
without prompting during a run. Initial account authorization is the explicit
external setup boundary. Optional scaffold/custom-case/report convenience
commands do not block M10.

The running isolated backend must use the same source/configuration as the CLI;
the audio harness verifies its authenticated startup candidate, database and
instance before and after measurement. The composed runner prepares the
isolated question/control/follow-up scenarios automatically. It never treats a
different test configuration as the configured development candidate.

Use named milestones from the overarching architecture and store duration fields measured on the same monotonic clock. Reports identify workflow/model/capability versions, ready/cold conditions, assertions, failures, usage, and trace/result links. Raw audio and screenshots remain outside application telemetry and Git.

Include capture-status propagation and preflight waiting in admission latency. Report capture rejections/timeouts and upload failures as well as successful timings. Separately report queue/start latency under pool saturation and resource contention, process-exit-to-replacement timing, control-request-to-confirmed-stop timing, and directory release/reacquisition. Do not treat prompt admission as proof of prompt execution or count an accepted stop request as proof that writers have stopped.

Correlate task, turn, execution run, attempt, and logical capability operation. Separate initial-request latency from follow-up latency and user-wait time from active execution. Count provider calls inside batched capabilities as well as outer agent turns; report reused evidence and redundant calls. Evaluate usefulness and evidence support independently of schema validation.

Also correlate native Codex thread/turn/item IDs and SDK/runtime versions. Report effective model/effort and override reasons, subscription versus API execution, available quota/usage, known or estimated API costs, and explicit billing handoffs. Include separately billed routing/vision/retrieval capabilities. Unknown cost/quota is unavailable data, not zero; raw credentials and hidden reasoning are not telemetry inputs.

For background management, correlate source event, decision, relevant task/run/question revision, action and attention item. Separate deterministic forwarding latency from background decision queue/model/action latency. Report pending-decision age, duplicate suppression, stale-action rejection and inference usage; show voice dispatch latency while background judgment is busy.

## Verification gate

- A clean fixture run needs no network, browser, emulator, or desktop control.
- A backend vertical slice needs no browser or Android UI.
- The Android demo is repeatable from one command once SDK licenses, system image, acceleration, and test credentials are configured.
- Saturated shared-pool scheduling, duplicate events, restart, missing/protected screenshot, pre-admission capture timeout, fail-on-error upload handling, retained-context diagnostics, source timeout, long-running task inspection/cancellation, stale edit, and notification navigation all produce reviewable evidence.
- A paused/yielded task demonstrably releases its directory after confirmed stop, another task can edit it, and the original task reacquires access and checks changes before continuing. A cancelled/replaced worker and its subprocesses cannot write after access is transferred.
- The real Codex milestone demonstrates those writer/lease guarantees, durable action reuse and session continuation after restart, mobile history reconnect, and Jarvis inspection before broad workflow and result-page expansion. A native interrupt acknowledgment alone cannot pass the gate.
- Before unattended orchestration rollout, demonstrate meaningful autonomous progress without voice and preserved direct voice responsiveness while background inference is blocked. Pending feedback and unresolved questions survive service restart without duplicate effects; routine factual updates require no model call.
- CI runs deterministic and browser-rendering layers; live and emulator layers are explicit gated jobs.

## Known boundary

No emulator can prove physical microphone acoustics, wake-word performance, OEM assistant capture, lock-screen behavior, or battery characteristics. M11 is accepted for the selected personal phone; broader physical guarantees remain unproven and are not dependencies in everyday development.
## Completion evidence

See the [completion ledger](00-implementation-roadmap.md) and
[reports index](reports/README.md). Earlier progress reports are historical;
M0–M11 are closed under the user's accepted scope and caveats.
