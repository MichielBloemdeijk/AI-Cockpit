# M5 result-platform progress — 2026-10-02

Historical checkpoint note: the later [closure roadmap](../00-implementation-roadmap.md)
narrows M5 to its verified presentation/platform scope and explicitly assigns
unfinished contracts to M6–M10, with physical rollout in M11. Subscription is now
required by M9. The original results and deferred-gate discussion below are
retained as historical evidence; their old optional-subscription and broader
M5/"Phase 1" wording is superseded. This note does not rerun or pass any test.

Status: **platform implementation and autonomous/emulator acceptance verified;
broader M5/Phase 1 rollout sign-off still has deferred gates below**. No production
workflow/research flags, authentication settings or device opt-ins were changed.

## Implemented and exercised

- Additive/resumable migration `20261001_0031`: unified `CockpitApp` identities,
  generated-app backfill without route changes (including slug collisions),
  Documents/Recipes registration, expanded immutable revision metadata,
  transactional result events and scoped app records. Existing `/api/apps`
  remains operational. New generated apps synchronize their catalog identity.
- Authenticated catalog and current-only collection queries, escaped search,
  stable cursor pagination, updated/title ordering and safe snapshots bound to
  the result's associated run, not an unrelated later task run.
- Shared saved action for both types; missing means false. Reload persistence,
  optimistic revision conflicts and app/result isolation, without a content
  revision, worker or model call. Registered `saved` and generated `state`
  namespaces enforce 64-KiB canonical UTF-8 JSON records and a 10-MiB app quota.
  Concurrent writes serialize quota accounting in the database. App-wide scope
  uses a non-null sentinel to avoid SQLite's NULL uniqueness hole.
- Additive/resumable migration `20261001_0032` retains app capability revocation
  generations across migration replay. Owner-only issuance returns a separately
  signed 15-minute storage credential restricted to one generated app's `state`
  namespace and GET/PUT (or GET only). Generation revocation is checked again
  inside data transactions. Capability headers never authorize task, result,
  catalog-management or session APIs, even alongside a browser cookie. Tampering,
  expiry, malformed schemes, cross-app/result/namespace access and stale writes
  have autonomous coverage. Issuance by generated-app ID resolves catalog slug
  collisions. The shared `connectAppStorage`/`appStorageClient` helper keeps
  credentials in memory and omits cookies from data calls; builder guidance
  describes bootstrap, limits and reconnection after expiry.
- The additive `document.v1` contract now permits omitted summary, labelled
  HTTP(S) references and optional retained text/JSON attachments. Provenance IDs,
  when supplied, still require matching fetched evidence; ordinary reference
  links do not falsely claim fetched provenance. Publication rejects foreign,
  private-context, HTML and filesystem-only artifacts before task completion.
  Authenticated downloads expose only references in the current revision, use
  attachment/nosniff/private headers and never read worker-supplied filesystem
  URIs. Omitted additive fields preserve legacy publication fingerprints.
- Per-result durable SSE, ordered replay, cursor reset, normalized lifecycle
  labels, heartbeats, reconnect cursor retention, periodic reconciliation and
  fallback polling. Worker prose, raw diagnostics and source evidence are not
  copied into presentation events. Publication/result events remain inside the
  existing artifact/task/outbox transaction; injected failure rolls it back.
  Offline/online events close and resume the native transport with its cursor;
  obsolete callbacks cannot affect a replacement connection. Short SSE database
  reads/cleanup are cancellation-shielded, not the live stream or publications.
- Common Documents/Recipes collection UI and saved control; Apps workspace
  distinguishes collections while retaining generated-app management.
  Focused results retain last-good content, reject stale snapshots, show slow
  work/reconnection, support copy/print and safely reject unsupported renderers.
- A separately exportable Next result shell imports the **same** result screen,
  not a second renderer. FastAPI serves arbitrary future `/results/{id}` before
  legacy static mounts. The shell contains no private data; APIs authenticate.
  Assets use an isolated prefix. HTML has private/no-store, noindex, nosniff,
  referrer protection and hash-based CSP for Next's inline bootstrapping.
- Android action label is Open result for a result path. Pure JVM-tested
  navigation policy keeps same-origin/login return paths internal, sends
  external HTTP(S) links to a browser, blocks unsafe schemes/credentials, and
  restricts microphone permissions to the configured origin. Direct activity
  PendingIntents open results only on tap; completion never auto-launches an activity.
  Notifications with no published revision target the task rather than offering
  Open result for an empty failed/cancelled/answer-only page.

## Evidence and reproduction

### Autonomous rollout preparation and recovery rehearsal

The [operational runbook](../rollout-runbook.md) adds implemented, agent-run
`rollout-check`, `migration-rehearsal` and `rollout-rehearsal` commands. This is
preparation, not another phase or production authorization.

[Read-only local inspection](m5-rollout-check-current.json) correctly remains
blocked: the configured database is behind the current schema and owner auth
still uses the development setup. Built companion assets, pinned package and
separate profile/broker boundaries pass. Inspection never migrates/starts services,
reads profile credentials or calls a provider. External gates remain explicitly
unknown/blocked even if a budget boolean is asserted. All existing flags remain off.

[Configured-database clone migration](m5-migration-rehearsal-current.json) passes:
a bounded, read-only online SQLite snapshot migrates only its disposable clone
to `20261001_0033`, preserving three task identities/content, three attempts and
nine events. Reports include counts/column names/equality checks, not private rows
or paths. The temporary clone is removed; the original remains unmigrated. Zero
legacy artifacts/questions are reported as such, not treated as representative
recovery coverage. This is not a retained or complete production backup.

[Isolated recovery/disablement rehearsal](m5-rollout-rehearsal-current.json)
passes **34 tests** and verifies a fresh database restore plus an ordinary actual API restart: password
cookie, canonical revision, saved state, task/history and disabled-workflow gates
survive. WAL snapshotting, no-overwrite/sidecar checks, FK/integrity failure,
bounded busy-writer retry, migration replay and attention/flag transitions are
covered. The child installs its Windows job/parent boundary before pytest/API
startup; timeout/crash cannot leave its fixture API detached. Provider keys are
blank and production/model flags are off. Missing/empty/skipped/failed evidence
cannot pass, and reports never grant rollout approval. Full workspace/blob/native
profile backup and actual production deployment remain separate steps.

```powershell
cd backend
.venv/Scripts/python.exe -m app.jarvis_tasks rollout-check --report ../docs/jarvis-tasks/reports/m5-rollout-check-current.json
.venv/Scripts/python.exe -m app.jarvis_tasks migration-rehearsal --report ../docs/jarvis-tasks/reports/m5-migration-rehearsal-current.json
.venv/Scripts/python.exe -m app.jarvis_tasks rollout-rehearsal --report ../docs/jarvis-tasks/reports/m5-rollout-rehearsal-current.json
```

The read-only check intentionally exits nonzero for unresolved rollout gates;
successful migration/fixture rehearsals exit zero without changing enablement.
No new installs, paid calls, production migrations/auth changes or device opt-ins
were required for this preparation package. The custom-wake capture/Live integration
is still agent-implementable work, not an account or physical-device blocker.

### API37 production delivery and combined regression (latest package)

[Machine-readable API37 evidence](android-m5-api37-current.json) records a passing
cold-start run on the installed `JarvisM3Api37` Google APIs image, following a
passing warm run. The model-free harness builds the actual static companion and
Android APKs, then starts a disposable password-backed API and separate local
external-source origin. The result is allocated after the export build. Native
UI verifies wrong/correct login, building content, deliberate cookie loss, the
real **Open result** action, login return to the same result, saved state and
login after force-stop, task/result backlinks, external system-browser intent
and return to the unchanged result. One attempt and one revision remain; the
paired-device open receipt deduplicates to one. Completion is observed while
Home remains foreground and never opens an activity before the action is tapped.
External acceptance proves the browser launch/URL intent, not successful remote
page loading or every browser's onboarding flow.

The new checks exposed and fixed actual delivery problems:

- Notification actions now use direct activity PendingIntents, not a broadcast
  trampoline that leaves the shade covering the opened result. Legacy receivers
  remain for already-issued intents. Opening acknowledges/cancels the selected
  notification from the activity without blocking page loading.
- Open acknowledgements accept normal owner cookies or valid paired-device
  credentials. Revoked/anonymous/app-capability credentials fail closed; even a
  capability alongside a cookie is rejected. VAPID/subscription management stays
  cookie-only. Native acknowledgements work before WebView login and never
  answer worker questions or change publication authority.
- WebView cookies flush after page completion/backgrounding, retaining a real
  login across abrupt process death. System-bar/cutout insets apply to its parent
  container so top backlinks are not drawn beneath the status bar. Actual taps
  verify both changes; final native screenshots were inspected.
- Concurrent regression exposed a telemetry deadline firing during connection
  cleanup after commit. The 250-ms deadline now covers metric queries/writes,
  not commit/rollback/connection close; those short operations finish cleanly.
  Deterministic timeout/disconnect/contention tests and browser error-log checks
  preserve this boundary without shielding task execution or a live stream.

Cold-start SystemUI continuously emits accessibility events and can make the
stock `uiautomator dump` wait-for-idle fail even when the action is visible. A
bounded read-only instrumentation reader in the **separate test APK** snapshots
the active hierarchy without that idle prerequisite. It masks password fields,
does not restart Jarvis/Chrome, never clicks by guessed coordinates and does not
read a cached hierarchy. Debug pairing controls and fixture publication routes
are absent from the release app/production API. The existing SDK/dependencies
sufficed; there were no new external installs, paid calls or production opt-ins.

Final full backend checkpoint: **499 passed, 12 opt-in skips**. The subsequent
final fixture/helper/auth/notification/observability/capability suite passes
**30 tests**, including the added hierarchy-parser test. Frontend **191 tests**,
normal Next production build, static companion build, **24 Android JVM tests**
and both APK builds pass. The actual password-backed production browser journey
passes separately: **1153 ms fixture admission / 622 ms publication-to-visible**.
Warm native admission measured **140 ms**, cold native admission **109 ms**.
Native publication-to-visible was **13.09 / 14.55 seconds**, including automated
shade interaction and password entry, not provider execution or a paint-only
benchmark. The harness stops only the disposable process trees it starts.

```powershell
cd backend
uv run python ../android/scripts/verify-result-platform.py --report ../docs/jarvis-tasks/reports/android-m5-api37-current.json
.venv/Scripts/python.exe -m pytest tests/test_android_result_fixture.py tests/test_notifications.py tests/test_task_notifications.py tests/test_result_observability.py tests/test_app_capabilities.py -q
```

### Result observability and process restart (latest package)

Additive/resumable migration `20261001_0033` stores metadata-only result metrics,
separate from publication authority and presentation events. Authenticated
`GET /api/results/{public_id}/observability` exposes durable counts, publication
outcomes, emitted replay-event counts and the latest 100 samples with durations
and correlation IDs. Counts cover all retained samples; duration percentiles
refer only to that bounded recent window, not a production latency distribution.
`POST /api/results/{public_id}/observations` accepts only bounded UUID/sequence,
registered milestone, duration and observed revision fields. Extra fields,
invalid durations and future revisions are rejected. View milestones and delivery
receipts deduplicate without changing content, state, task events or worker work.

The platform records capture preflight success/rejection/timeout, screen admission
including preflight, allocation/admission, validation/transaction, publication
commit/replay/conflict/validation failure/rollback, completion-to-enqueue,
snapshot/ready view milestones, SSE connection/reconnect/fallback and emitted
replay events, plus notification transport/open acknowledgements. Pre-admission
capture metrics have no result identity; rejected captures allocate nothing.
Server-stage durations use a monotonic clock. Browser milestones measure elapsed
time from view mount; allocation/publication-to-client receipts are explicitly
labelled server-wall-clock proxies including network delay, not exact paint
timings. Server SSE emission does not claim browser receipt; notification
transport/open acknowledgement does not claim verified Android UI display.
Old notification receipts retain their producing run/revision after an update.

Metric writes happen independently after the owning task transaction ends, with
a 250-ms query/write deadline and a 100-ms SQLite busy timeout. Short commit,
rollback and connection cleanup finish outside the query deadline. Disconnect
shielding covers only short metric transactions/cleanup. Injected metric failure
and actual SQLite writer contention cannot undo publication or steal its write
transaction. Structured JSON logs exclude prompts, bodies, credentials, claim
tokens and raw exception diagnostics. Verification found and fixed migration
startup disabling application loggers; in-process migrations now preserve the
API/worker logging configuration.

Routine tests restart the actual isolated Uvicorn process, not just the database
engine. The same signed login cookie, canonical result, saved state, metric
counts and one completed attempt survive restart. Disabled workflow admission
still rejects without creating another task or result. Injected publication
failure leaves no revision or delivery, and migration replay preserves metrics.
No provider calls, production settings changes or installation are needed.

The 20-admission local fixture sample measured **9.70 ms maximum allocation**,
**86.21 ms admission p95** and **130.47 ms maximum admission including metric
recording**, under concurrent regression load. It is a warm local fixture sample,
not a cold-deployment or provider benchmark. The password-backed production
export/browser journey also verifies durable telemetry and actual emitted SSE
replay; its refreshed sample measured **876 ms fixture admission** and **457 ms
publication-to-visible content**. A final run including the ready-receipt proxy
assertion passed at **1109 ms admission / 560 ms publication-to-visible**. It
asserts no backend cleanup/transport errors.
Frontend regression passes **191 tests** and the normal production build passes.
The full backend checkpoint before the final hardening additions passed **490
tests with 12 opt-in skips**; final regression results are recorded below.
The final observability/restart/platform/capture/delivery/migration targeted
suite passes **52 tests**, including logging configuration preservation and
successful page/receipt responses when metric storage is unavailable.
The final full backend rerun passes **495 tests, 12 opt-in skips**. The production
export/browser test passes separately with explicit opt-in; the skipped live
provider/emulator checks are not claimed as rerun by this package. Whitespace
validation also passes. M5 remains in progress pending the updated Android and
combined exit gates below.

```powershell
cd backend
.venv/Scripts/python.exe -m pytest tests/test_result_observability.py tests/test_result_restart.py tests/test_result_platform.py tests/test_task_capture.py tests/test_task_notifications.py tests/test_notifications.py tests/test_migration_recovery.py -q
.venv/Scripts/python.exe -m pytest -q
$env:RUN_M5_BROWSER='1'
try { .venv/Scripts/python.exe -m pytest tests/test_result_browser.py -q -s }
finally { Remove-Item Env:\RUN_M5_BROWSER }
```

### Production companion navigation (latest package)

The focused FastAPI export now includes data-free HTML for login, Documents,
Recipes, Apps (maintained collections), task list and arbitrary future task IDs.
It imports existing screens, not parallel renderers. An allowlisted file mapping
keeps URL parameters out of filesystem selection; companion HTML retains the
result shell's private/no-store/noindex headers and hash-only script CSP. Private
APIs still require a session. Existing fixed legacy HTML pages remain available
when the companion build is absent; new result IDs still report an actionable
missing-build error. Normal Next deployment keeps client navigation;
the focused export loads documents for alias routes instead of fetching unbuilt
RSC payloads. Generated-app management and other Cockpit routes remain in the
full frontend; the focused Apps companion does not advertise those controls.

The browser starts with no cookie, follows the real login form (including wrong
password rejection), and returns to a result allocated **after** the export
build with its query string and fragment intact. It follows result → source task
→ result, result → Documents → searched result, Documents → Apps → Recipes and
task-list/detail backlinks; reopening preserves saved state and the entire
result snapshot without executing work. It also rejects an off-origin backslash
return URL. The refreshed test checks session loss and task login return too.
Copy/print, real native SSE reconnect/cursor retention, same-URL revision updates,
failed-update preservation and stale-attempt fencing remain in this journey.

Password-backed acceptance exposed an existing deployment bug hidden by blank
development hashes: Passlib 1.7's bcrypt backend self-test fails with bcrypt 5,
causing correct passwords to be rejected. Verification now uses installed native
`bcrypt.checkpw`; existing 2a/2b/2y hashes remain compatible and malformed hashes
or inputs over 72 UTF-8 bytes fail closed without truncation. The dependency and
README hash-generation command are updated; no configured credentials changed,
no package installation and no paid model calls were required for this slice.

Frontend regression: **189 tests passed**, normal Next production build passed,
and focused export/build browser acceptance passed. An updated run measured
891 ms fixture admission and 427 ms publication-to-visible while the full backend
suite ran concurrently; these are fixture samples, not provider latency claims.
The refreshed result/task-login journey passed with the real form and cookies.
Backend full-suite checkpoint: **483 passed, 12 opt-in skips**. After the final
legacy-fallback and 2y compatibility additions, the complete auth/result-platform
targeted suite passed **20 tests**. Skipped live/provider/emulator tests are not
claimed as rerun by this package.

### Generic publication and explicit follow-ups (latest package)

General tasks can opt into a maintained Document at admission without a workflow
definition or custom recipe integration. Final Markdown is adapted into the
existing typed publication transaction. Explicit `new`, `update` and `answer`
contracts are persisted per run, independent of execution preferences. New
queued results allocate immediately; updates retain their selected URL and fixed
expected base; answer-only runs do not change the page, revision or result events.
HTTP/Jarvis inspection supplies current result metadata, and the task page lets
the user select a mode/result. Legacy publishing-only workflows do not offer an
unsupported answer-only option.

Publication now persists notification intent in the same transaction, respecting
silent policy; projection deduplicates it by the existing event identity. Tests
inject an exception after outbox insertion and verify rollback of artifact,
revision, operation, events, completion and delivery. They also cover foreign
ownership, unclaimed publication, strict schema/base validation, idempotent replay,
queued stale edits and last-good preservation. The old workflow's terminal outcome
and URL survive promotion to a general-agent follow-up with another result.
Screen-workflow replay looks up the original admitted run, not the task's latest
run or an ambiguous task-wide result.

Two **billed live Codex** tests pass unattended: three general-agent turns
(create/update/answer) and one workflow answer-only turn using the filtered MCP
inventory. An initial live edit really did create `document.md` and publish a
file-update confirmation instead of the requested body. The adapter instructions
were corrected to require complete canonical Markdown; the original strict
content assertion was retained and the rerun passes without a surrogate file.
Required MCP startup and exact fail-closed inventory verification remain in place.
No SDK/runtime upgrade, broader tool grants, production settings change or new
installation was needed. These small live samples are functionality checks, not
provider latency/quality guarantees or a subscription-profile sign-off.

A repeated live run also exposed a post-commit heartbeat race: the answer was
durably completed with summary `ANSWER`, but worker cleanup reported `lost_claim`.
Committed-attempt recognition is now shared by general and workflow workers,
independent of the task's latest run/kind. Cleanup errors after a committed outcome
do not overwrite it or report a false failure. Event-synchronized tests force
renewal into the actual post-commit/pre-cleanup window for normal and exceptional
cleanup. Publishing agents also reject an absent final body instead of inventing
a generic completion sentence, and explicit workflow edits cannot use answer-only
finish to avoid their publication contract.

The latest full backend checkpoint is **476 passed, 12 opt-in skips**. Final
publication/worker guards added after that checkpoint are covered by the focused
contract/control-plane/worker/workflow rerun: **106 passed**. The two live tests
also pass on the final worker implementation.
The earlier checkpoints below are retained as historical evidence, not a claim
that all remaining M5 gates have passed.

Frontend **181 tests pass**, including explicit mode/identity/base submission and
legacy capability compatibility; the normal production build passes. The actual
Next-export/FastAPI/Playwright regression passes again (future ID, SSE, saved
refresh, same-URL revision, failure/fence, copy/print and offline cursor recovery).
This sample measured **809 ms** fixture admission and **1049 ms** publication to
visible content under concurrent regression load, not provider timing.

```powershell
cd backend
.venv/Scripts/python.exe -m pytest tests/test_task_result_contract.py tests/test_task_capture.py tests/test_task_control_plane.py tests/test_worker_coordinator.py tests/test_task_execution_preferences.py tests/test_task_mcp.py tests/test_m4_workflows.py tests/test_result_platform.py tests/test_task_workflows.py tests/test_task_notifications.py -q
$env:JARVIS_LIVE_CODEX='1'
.venv/Scripts/python.exe -m pytest tests/test_m5_result_live.py -q
```

Routine runs skip billed/live/browser tests unless explicitly opted in. All
fixtures use isolated databases/profiles and restore settings after execution.

Backend full regression checkpoint: **465 passed, 10 opt-in skips**, including
disconnect cleanup and capability migration replay. The subsequent final
notification/capture/platform suite passes **28 tests**, including the added
unpublished-result targeting cases.
The expanded generated-app/workflow/platform/document/migration suite passes
**68 tests**; final storage/document/platform coverage passes **16 tests**.
Frontend: **176 passed**, 39 files;
normal production build and standalone static result export both pass.
Android `:app:testDebugUnitTest :app:assembleDebug` passes, including three new
navigation-policy tests. No new paid model calls were needed.

Playwright browser acceptance builds the actual static shell and starts an
isolated FastAPI fixture server. It verifies a result ID created after the
build, building-to-ready over SSE without reload, saved state after reload,
same-URL revision 2, failed-update preservation, fenced old attempts, no browser
exceptions and a 393-pixel viewport without horizontal overflow. One fixture
sample measured **630 ms** admission (including fixture capture/upload setup)
and **539 ms** publication-request-to-visible content. It also checks actual
clipboard copy, print invocation and print CSS, and real browser offline/online
reconnection with the retained cursor and no page reload. The fixture observes
native SSE snapshots before disconnecting rather than relying on timing sleeps.
These are fixture measurements, not a live-model latency guarantee. Print
acceptance verifies invocation and rendered print styles, not OS print-dialog
operation or a physical printer. Three successful updated browser runs measured
630–723 ms fixture admission and 539–625 ms publication-to-visible content;
these few samples are not a latency distribution or provider benchmark.

```powershell
cd backend
.venv/Scripts/python.exe -m pytest tests/test_auth.py tests/test_app_capabilities.py tests/test_document_contract.py tests/test_result_platform.py tests/test_migration_recovery.py tests/test_m4_workflows.py -q
$env:RUN_M5_BROWSER='1'
.venv/Scripts/python.exe -m pytest tests/test_result_browser.py -q -s
```

```powershell
cd frontend
npm test
npm run build
npm run build:results
```

```powershell
cd android
.\gradlew.bat :app:testDebugUnitTest :app:assembleDebug --console=plain
```

The matching Playwright Chromium/headless-shell runtime was missing and was
installed unattended with `npx playwright install chromium`. No user input was
needed. The browser fixture's mutation controls exist only in its temporary
test app; they are not production endpoints. Build the result export **before**
starting FastAPI so its assets mount, and restart FastAPI after first installing
that build. The focused export complements the normal Cockpit frontend, not an
export of every existing dynamic Cockpit route.

### Storage trust boundary

These credentials constrain HTTP storage operations, not arbitrary JavaScript.
Generated routes remain trusted project code on Cockpit's origin. Malicious
same-origin code could deliberately omit the capability header and use the
owner's session, so this work must not be described as an untrusted-code browser
sandbox. Supporting untrusted generated code would require isolated origins or
an equivalent host-mediated sandbox and separate acceptance tests. No credential
is embedded in generated source, app manifests, result links or browser storage.
This boundary is automatically testable; it is not a physical-device gate.

## Deferred gates before full rollout sign-off

The listed M5 platform implementation and agent-testable acceptance work is now
verified, including updated API37 delivery and combined regression. No known
implementation defect remains in that guarded fixture route. These broader
gates remain explicit; they are not silently passed by an emulator or fake worker:

- Physical/OEM behavior: actual audio/microphone timing, device-specific locked
  and protected-context policy, background persistence and real-phone delivery.
- End-to-end custom local-wake screenshot wiring and real Live/provider timing;
  M4's system-gesture/fake-Live evidence is not that sign-off. Native feasibility
  proof does not establish the full production wake/audio path.
- A separately authorized Jarvis subscription profile and its continuation and
  budget boundaries, if subscription execution is wanted. Desktop credentials
  must not be copied or reused.
- Provider/account enforced-budget and production latency/quality acceptance,
  followed by user-approved device/flag opt-in and progressive rollout. Existing
  API samples prove functionality, not an invoice cutoff or sustained SLA.

Physical testing remains deferred, as requested. Rollout preparation can be
done autonomously; enabling production or authorizing a new account/device
requires explicit user direction. All current production flags remain unchanged.
