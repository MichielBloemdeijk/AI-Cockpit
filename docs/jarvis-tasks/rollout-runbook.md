# Jarvis personal-use operations and verification

**M11 personal use is accepted as of 2026-10-11.** The user confirmed working
tasks, including screenshot-based recipe creation, and accepted all work as
completed. See the [completion ledger](00-implementation-roadmap.md) and
[final acceptance record](reports/personal-use-accepted-2026-10-11.md).

Use `Jarvis.cmd` for startup/status/stop and the [phone guide](phone-test-checklist.md)
for setup or future troubleshooting. Optional-password private personal access,
one-time linking and automated setup verification are complete. Preserve paired
device scope, signed sessions, origin/privacy protections and durable recovery.
The dedicated subscription profile, selected trusted coding mode and four
accepted M8 caveats remain explicit. A full development-data backup is optional.

This runbook retains reusable operations and historical verification procedures.
Dated open-gate statements below describe their original checkpoint; they do
not override the completion ledger or create a new acceptance assignment.

## Development maintenance and completion boundaries

After the 2026-10-10 phone feedback, use the updated APK and automatic setup in
the [phone checklist](phone-test-checklist.md). Install updates without clearing
phone data. Phone Settings shows its saved device link; new pairing remains a
desktop action. The native audio service now owns both wake and Live; closing the
WebView or assistant panel leaves the selected listening mode running. See [targeted validation](reports/phone-setup-fix-2026-10-10.md);
the previous combined report belongs to its original frozen candidate.

Apply [AGENTS.md](../../AGENTS.md): existing application data is disposable.
Updating the local development schema/configuration, removing obsolete
compatibility paths or rebuilding scoped application data is normal M10 work.
No backup or approval is needed solely to preserve that data. Resolve exact
project-owned targets, stop affected writers and report meaningful resets.
Preserve source/uncommitted work, credentials, account settings and unrelated files.

Recovery, migration and disablement tests remain required long-term contracts.
A complete backup is optional for disposable development data, including this
personal M11 setup. If the user elects to preserve useful data, take a consistent
private snapshot after confirmed writer stop. M10 baseline evidence remains valid
for its recorded candidate; new M11 executable changes need their own verification.

## Persistent Android controls

Install the updated APK over the existing linked app, preserving its data.
Use the quiet Jarvis notification outside the app: Listen now starts Live;
Stop Live pauses all audio; Hey Jarvis returns to local wake listening.
Task work and result delivery continue after Stop. Paused retains controls
without a dummy foreground service. Permission repair opens setup. Dismissed
controls stay dismissed until explicit app opening/control use; respect Android
system Stop. Recovery restores only the paused/wake choice, never paid Live.
Host or phone unlinking removes controls. Screen-off capture is unavailable.
See [audio ownership and unpaid verification](11-android-audio-controls.md).

## Agent-run preparation

For unpaid phone-capture-to-worker verification, run from `backend`:

```powershell
$env:PYTHONPATH = "$PWD"
uv run python ../android/scripts/verify-phone-browser-capture.py --report ../data/phone-worker-check-unique
```

Use a fresh project-owned report directory. The API37 harness uses a disposable
paired backend with a relative SQLite URL, real normal-Chrome captures, the
production worker MCP command from its task directory, both image/history reads,
typed recipe publication and HTTP result retrieval. Recipe choices and native
research events are fixtures; no model is called. The standard privacy contract
stage also runs `tests/test_worker_database_process.py` with its declared HTTP
test cookie settings. A successful upload alone does not establish worker access.
See the [database repair](reports/phone-database-fix-2026-10-11.md). SQLite file paths
resolve relative to `backend` regardless of API/worker launch directories.

For admission profiling without provider credits, run from `backend`:

```powershell
$env:JARVIS_OFFLINE_TIMING_REPORT = "$PWD/../data/admission-timing-offline/local.json"
uv run pytest tests/test_admission_timing.py -q --disable-warnings --tb=short
Remove-Item Env:JARVIS_OFFLINE_TIMING_REPORT
```

The benchmark uses fresh migrated test databases, real durable SQLite admission
and mocked provider streams. Gateway/routing/capture profiles reject socket
network access; no worker model is launched. It retains warmups, three independent
gateway trials, 20 managed routing samples, 20 managed full-flow samples,
20 controlled SSE delivery phases and
pending/timeout capture diagnostics. Reports are diagnostic; they cannot replace
live ASR/tool-choice, Android capture/transport or ready/cold latency acceptance.
Use a fresh report prefix and keep this timing run separate from concurrent builds
or heavy test workloads. See [the baseline timing report](reports/admission-timing-2026-10-05.md)
and [the triggered follow-up](reports/admission-triggered-2026-10-05.md).

Local SSE and required-capture readiness now wake after committed changes;
bounded recovery scans cover cross-process or missed signals. Required images
retain a two-second failure deadline. Optional roles are checked without waiting
at collection freeze. Run `tests/test_commit_signals.py` for broadcast, commit,
rollback, cancellation and read/wait race coverage. Capture/result diagnostics use
bounded queued batches; `close_engine()` drains them before database disposal in
API, worker, MCP and test entry points. Metrics are eventually visible; explicit
test drains belong outside measured admission intervals. Process-local queue health
and loss counts are exposed in result observability, with no content in loss logs.
Run `tests/test_observation_queue.py` and `tests/test_result_observability.py` for
overflow, failure, cancellation, draining and delayed run/revision coverage. The
[telemetry report](reports/admission-telemetry-2026-10-05.md) retains the controlled
interleaved comparison and complete local delivery samples.

SQLite WAL mode is persistent and is provisioned during database setup/migrations,
not reset when a reader opens a connection. Connection-local foreign keys,
full synchronous commits and writer/diagnostic lock budgets remain enforced.
Cancellation waits for connection initialization or a short diagnostic driver
operation to finish before releasing its handle. Run `tests/test_db_session.py`
for active-writer read responsiveness and repeated connection-setup cancellation.

For browser spoken-response acceptance, explicitly select audio containing both
a positive wake phrase and a substantive request. A calibration recording with
only the wake phrase establishes wake detection, but need not produce a reply.
Use `npm run test:live-smoke -- --audio <wake-and-request.wav>` and retain the
recording identity/hash with the evidence. The 2026-10-09 final browser input
reuses recorded wake and HQ request audio; no new speech-generation API request
was needed. Successful same-candidate browser evidence can be imported into the
combined run. Do not treat a connected session alone as a spoken-response pass.

The shared manifest gives full backend acceptance a 2400-second launcher budget.
This is a test-suite allowance, not an admission wait, task deadline or spending
limit. A suite timeout stays a failure; successful progress dots do not by
themselves establish complete teardown or an aggregate acceptance pass.

Run from `backend` using the installed environment:

```powershell
.venv/Scripts/python.exe -m app.jarvis_tasks rollout-check --report ../docs/jarvis-tasks/reports/m5-rollout-check-current.json
.venv/Scripts/python.exe -m app.jarvis_tasks migration-rehearsal --report ../docs/jarvis-tasks/reports/m5-migration-rehearsal-current.json
.venv/Scripts/python.exe -m app.jarvis_tasks rollout-rehearsal --report ../docs/jarvis-tasks/reports/m5-rollout-rehearsal-current.json
```

`rollout-check` returns nonzero while required current evidence is missing.
`preparation_status` describes only the
local checks. The command inspects configuration, the pinned package, built
companion pages and the SQLite schema/foreign keys. It does not start the API,
migrate data, inspect credentials, call a provider or change flags. Provider account settings
own spending limits; no Jarvis cutoff proof is required. Built assets
are not browser acceptance or proof that the currently running API mounted them.

Candidate-bound evidence evaluation is implemented through Phase 7's
`acceptance` command and `rollout-check --evidence`.
Reports distinguish implementation readiness from physical deployment readiness;
missing, skipped, stale or mismatched evidence remains blocked.

```powershell
.venv/Scripts/python.exe -m app.jarvis_tasks acceptance --gate M10 --report ../data/acceptance-current
# Authorized paid suites and an already prepared emulator/backend:
.venv/Scripts/python.exe -m app.jarvis_tasks acceptance --gate M10 --live --emulator emulator-5554 --audio-dir ../data/rendered-corpus --database ../data/isolated-voice.db --report ../data/acceptance-live
.venv/Scripts/python.exe -m app.jarvis_tasks rollout-check --evidence ../data/acceptance-current/acceptance.json --report ../data/readiness-current.json
```

Each report directory must be fresh. Import a required observation with
`--evidence assertion_id=report.json`; its schema, gate, source/configuration/
runtime candidate and timestamp must match. The runner executes existing
pytest, Vitest, TypeScript, Next builds, Android JVM/build, browser and migration
checks. With the prepared emulator/audio configuration, missing privacy and
mobile-result evidence is produced automatically by the native emulator scripts.
Their reports still pass candidate/configuration/observation validation; a
successful launcher is insufficient. `--live` executes the supervised leased-project repair/restart test and
the opt-in provider suites; selected emulator audio uses the debug input and
real RTP/playback seams. Imported account/capture/voice evidence does not waive
the remaining assertions. Android/browser test results with skips cannot pass.

Current local blockers and unfinished corpus scenarios are recorded in
[M6–M10 progress](reports/m6-m10-progress.md). The host's offline renderer produces
empty audio; the pinned API renderer supplies validated synthetic speech instead.
Managed selection has reached durable admissions over real custom-wake RTP and
is now the only route. Historical misses and the incomplete full managed cohort
are retained M8 caveats under the user's acceptance decision. There is no routing-mode setting or client fallback. The audio
harness's `--prepare-scenarios` operates only in the explicitly selected isolated
project-owned database with its worker pool stopped. Full managed corpus and capture
performance evidence were original requirements; M10 must represent their
accepted exceptions explicitly rather than silently require them again.

`migration-rehearsal` uses SQLite's online backup API on the configured database
opened read-only, upgrades only a disposable clone and verifies its schema,
foreign keys and retained identities/content. Reports expose counts, column
names and equality outcomes, never row content, credentials or database paths.
The temporary private clone is removed after the run. This is not deployment
and not a retained production backup. A concurrent live source may continue
changing: retained comparisons refer to the consistent snapshot, not a later
source state. The configured source remains on its original schema.

`rollout-rehearsal` runs isolated fixtures with provider keys blank, model flags
off, a separate database/profile root and a Windows process-tree boundary before
pytest starts. Timeout/crash closes that tree, including the fixture API. It
verifies online WAL snapshotting, integrity/FK rejection, bounded busy retries,
no-overwrite behavior, restore into a fresh database, password cookie reuse,
result/saved-state/history retention, disabled-workflow rejection, migration
replay and orchestration disable/re-enable identity/attention behavior. Empty,
skipped, failed or missing test evidence cannot pass. No production data is
restored or overwritten. The JSON report is a fixture pass, not full rollout.

Report output only replaces a regular JSON report with the same schema; other
existing files and symlinks are refused. No new dependency install is needed
on the verified Windows host.

## Recorded development findings — 2026-10-02

The 2026-10-02 local inspection found the configured database behind
`20261001_0033` and owner authentication still using the development setup.
The companion assets and profile/tool boundaries pass. All task-pool,
orchestration/live and research flags remain off; the configured executor
remains legacy. The clone migration passes while retaining three tasks, three
attempts and nine events. These are local observations, not production enablement.
Zero legacy artifacts/questions in that snapshot do not prove their recovery;
the isolated migration/result fixtures provide the representative-data evidence.

## Completed M11 setup procedure (reference)

The [M11A–D checklist](10-personal-setup-and-phone-acceptance.md) owns the sequence
and exit criteria; these are its operational steps:

1. **Prepare personal access and startup.** Reuse the current Windows host and
   stable Tailscale endpoint. Verify the actual listener/proxy restriction once.
   Make the Jarvis password optional in explicit personal mode; accept an optional
   plaintext password through private ignored configuration. Keep session and
   device authorization internally. Provide one start/stop action and clear health.
   Update readiness rules so a missing password or full backup is not a false block.
2. **Prepare phone linking.** Provide Link phone with QR/link and manual fallback,
   using a short-lived single-use code and saved revocable device credentials.
   Reuse pairing for result/WebView access with device scope intact. Guide necessary
   Android choices once. Ordinary restarts/reconnects must not require another login.
3. **Complete agent checks.** Run local/mock, browser and emulator checks before
   targeted paid seams. Freeze the resulting candidate and finish required combined
   acceptance, retaining four M8 caveats and the final ready sample below 4.5 seconds.
   Test clean shutdown/restart, persistence, migration, cancellation and disablement.
   Confirm old writers have exited before ownership transfer or scoped data reset;
   never run legacy and supervised launchers against the same task concurrently.
4. **Hand over a ready build.** Supply one installable app, working pairing action
   and the short M11D checklist. The user tests real speech, screens, notifications,
   result updates, background/lock behavior and restart/reconnect at the end. Reuse
   subscription login unless an actual supported-runtime auth failure requires help.
5. **Complete personal-use acceptance.** Record physical results and any specifically
   accepted limitation, then the user's confirmation of normal use on this host/phone.
   Enable only that selected scope. A future public/multi-user setup needs a separate
   deployment decision. The selected private personal host configuration is now active; personal phone acceptance was completed on 2026-10-11.

If startup or recovery fails, stop new claims/decisions, confirm writer shutdown
and preserve durable state while diagnosing. Do not fabricate completion, repeat
unknown side effects, downgrade schemas or feed Codex checkpoints to an old executor.

A backup is optional for disposable development data. If requested for retained
data, include the database, referenced context/artifacts/workspaces, dedicated
profiles and required private configuration consistently after writer stop. Keep
credentials and images private; never copy the desktop Codex credential cache or
put a bundle in reports/Git. Restore referenced files and database together with
writers stopped, reconciling credentials/external effects that changed afterward.

## Remaining testability boundaries

- Agent-testable: personal access modes, QR/link pairing, shared phone access,
  startup/restart, capture privacy and durable recovery. M10 passed on c8a0aec7;
  future executable changes need matched evidence. The four accepted M8 exceptions
  remain; do not reinstate waived full cohorts as hidden completion gates.
- Agent-testable with an authorized provider: prerecorded RTP/real Live timing,
  fixed-input quality/latency samples and truthful usage accounting. Existing
  permission to spend credits on isolated tests does not approve unattended
  rollout.
- M9 account dependency: the separate subscription profile was signed in through
  supported device login and its saved authentication is present. Current-source
  subscription execution, competition, restart and same-task API handoff passed;
  M9 is complete. See [M9/M10 closure](reports/m9-m10-closure-2026-10-07.md).
  No additional login is requested unless the supported runtime
  reports an actual authentication problem. Never reuse desktop credentials.
- M10 development maintenance: update local application schemas/configuration and
  scoped disposable data autonomously. Data preservation alone is not an approval
  gate; source, credentials and account settings remain protected.
- M11 user involvement: final app installation/system permissions and physical
  checks, followed by confirmation of normal use. Password and development-data
  backup are optional; no new hosting or subscription login is required by default.
- Deferred physical: microphone acoustics, wake-word behavior, OEM persistence,
  battery, protected/locked policy and actual phone notification delivery.

Refer to [M5 progress](reports/m5-progress.md) for historical platform/browser/API37
evidence and the [roadmap](00-implementation-roadmap.md) for the current M6–M11
completion ledger. A historical report is not a current-candidate acceptance run.


## Persistent subscription login and command mode

From `backend`, run `.venv/Scripts/python.exe -m app.jarvis_tasks subscription-login --device-code`.
Open the printed verification URL in your usual browser and enter its one-time code. Device
code login may first need enabling in ChatGPT security settings. Omit `--device-code` for
browser/localhost OAuth. Jarvis explicitly stores the refreshable login in its dedicated
`CODEX_SUBSCRIPTION_PROFILE`; later tasks and restarts reuse it. The desktop profile is never
copied. Do not paste credentials or auth.json into chat. An API key authenticates separately
billed API usage, not the personal subscription. Enterprise access tokens are a distinct
account capability, not a required integration for this iteration. Finish setup with workers
and orchestration stopped so that credential refresh has one owner.

Current local setup uses `TASK_COMMAND_MODE=trusted` and `TASK_WINDOWS_SANDBOX=unelevated`
after native setup produced repeated elevation prompts. Native setup, when explicitly selected,
uses `TASK_COMMAND_MODE=native` and `TASK_WINDOWS_SANDBOX=elevated`. Native
commands have explicit project roots, project-local scratch and no shared TEMP allowance.
Test directories use ordinary mkdir/inherited permissions. Validate real tooling with
`pytest tests/test_command_workloads.py tests/test_windows_job.py`; this covers Python/Node
scratch and subprocesses, nonzero test exits, cleanup and descendant stop behavior.
The weaker alternative is an explicit `TASK_COMMAND_MODE=trusted` setup choice: commands
inherit the Windows user's access. Brokered edits remain path-constrained, operations stay
journaled and directory/process ownership is unchanged. Never silently broaden a native failure.

Use `android/scripts/verify-managed-admission.py --report data/<fresh-name> --render-dir data/audio-corpus-api-v1`
for isolated managed API37 evaluation. `--capture-performance`
runs the separate controlled capture stage: 20 interleaved disabled/enabled starts, startup
p95 difference, equal 20-second frame windows and normalized capture-induced frame regression.
The combined acceptance runner executes audio and capture performance explicitly. All required
observations must pass for M10; a final ready admission at or above 4.5 seconds or unavailable subscription leaves M10 open.
Executable source/workflow/config/runtime must remain frozen during these runs; report-only
Markdown updates do not invalidate the candidate. Shared permitted test settings are in
`backend/app/jarvis_tasks/acceptance-profiles.json`; they do not waive backend-instance/database
checks or permit arbitrary configuration mismatches.

Resuming a native thread must still deliver the current durable run input and
post-context clarification answers. Native history does not receive user turns
committed while its host is stopped. Keep the frozen context immutable and supply
the fenced run delta; inspect omitted evidence through task history. Process-tree
evidence uses the shared `process_tree` test profile so disposable test directories
do not appear as undeclared configuration changes.

The 2026-10-05 frozen run encountered HTTP 429 with `credit_balance_exhausted`
from the voice API. This requires restoring the API organization's available
credits; another subscription login does not resolve it. Do not repeatedly run
the voice corpus while that condition persists. Keep the unavailable observations
blocked and repeat current-candidate live acceptance after the balance is restored.

That billing failure is historical. Credits were subsequently restored and the
2026-10-06 managed pilot succeeded on the current source. It does not establish
funding or authentication for every provider route indefinitely, but there is no
observed current voice billing blocker or new account action requested. The
[current gate review](reports/implementation-gate-review-2026-10-06.md) identifies
the remaining development and acceptance work.

## Provider test economy — agreed 2026-10-05

Restoring credits is not a reason to rerun broad paid suites during development.
First require promising network-blocked routing/admission/capture fixtures and
relevant functional, cancellation, replay and recovery regressions. Instrument
the stage being investigated before spending on a live request. Capture/Android
checks that require no model should run independently of paid voice sessions.

Then use a small targeted live pilot: two or three admissions that test the
changed seam, with workers/background inference disabled unless required by the
case. Keep model, tier, corpus and environment fixed. Stop on authentication,
quota/billing failure, missing timing evidence or a clear latency/functional
regression; correct and test locally before another pilot. Do not sweep models,
repeat unchanged corpora or hide failures by rerunning until a pass.

The routing harness has an explicit capped diagnostic path:

```powershell
# From backend: one to three ready cases, no cold/scenario sessions.
uv run python ../android/scripts/verify-managed-admission.py --report ../data/<fresh-pilot-name> --render-dir ../data/audio-corpus-api-v1 --pilot --case ready07 --case ready10 --case ready13
```

Each pilot sample is bounded to 30 seconds. It stops on a failed or missing
receipt, missing latency, malformed timing JSON or a latency >=4500 ms. Partial
pilots intentionally cannot pass full acceptance. Debug receipts retain scalar
timing summaries; full spans remain server-side. Future raw sample logs are
stored in `audio/android-logs`. Read [the first post-credit pilot](reports/admission-live-pilot-2026-10-05.md)
before spending on the next provider seam.

For an explicitly targeted provider-output investigation, `--model-diagnostics`
requires `--pilot`. It enables scoped local traces without changing reasoning
settings, with up to five seconds of post-admission
observation inside the existing 30-second sample cap. Add `--reasoning-summary`
only to request `reasoning.summary:auto`; that changes the request. These small
pilots are not latency acceptance evidence. The runner closes only attested, observed active
session generations before checking for output files. A missing file is recorded
as a diagnostic failure; do not repeatedly spend to obtain one.

Normal configuration keeps `OPENAI_LIVE_DIAGNOSTICS_DIR` empty and
`OPENAI_LIVE_REASONING_SUMMARY=false`. Trace files contain spoken text and tool
arguments; store them only in a scoped project `data/` folder and handle them as
private test data. Bounded buffers checkpoint provider terminal events before
coordinator cleanup, then flush on adapter close. Abrupt process death can still
lose uncheckpointed output; missing or truncated evidence remains unknown.
Analyze retained traces without network access using:

```powershell
python scripts/summarize-model-outputs.py --directory data/<pilot>/model-outputs --output data/<pilot>/model-output-summary.json
```

The [2026-10-05 output investigation](reports/admission-model-outputs-2026-10-05.md)
records both failed exports, recovered managed outputs, confirmed provider strict
schema normalization and the offline-only terminal export repair. Full hidden
reasoning is unavailable; streamed deltas are chunks rather than token IDs.

The shared voice function catalog now explicitly sets `strict:false`, so optional
arguments remain omittable in managed routing. Jarvis validates completed calls
locally before the restricted gateway; required fields and invalid/unknown values
remain guarded. Keep this setting explicit when adding tools. Published Fast mode
accepts the existing `priority` parameter with identical behavior; merely renaming
it is not a performance change. Ultrafast requires a supported different model
and higher published rates; it has not been enabled. See the output report for
current documented prices and the unmeasured admission tradeoff.

The [single optional-schema verification](reports/admission-optional-schema-verification-2026-10-05.md)
retains successful raw output with summaries disabled, minimal arguments and a
6.748-second ready admission. The receiver now avoids a second transport-error
report after committed provider closure; that later cleanup fix was tested only
offline. A provider-terminal checkpoint with `final_adapter_flush:false` can
retain completed response evidence but does not prove final adapter cleanup.
The bounded post-admission window may cut off spoken confirmation; preserve
that limitation rather than declaring full spoken-response acceptance.

The [matched two-per-route comparison](reports/admission-routing-paired-2026-10-05.md)
records four explicitly requested single-case pilots in managed/client/client/
managed order. Each used `--pilot --case ready07 --model-diagnostics`, a fresh
project-owned report directory and isolated backend. The pilot latency stops
and incomplete acceptance statuses were retained; the user-requested comparison
continued via separately reviewed single-case trials, with no broad corpus.
Managed was faster on both pairs and averaged 3.734 s versus 5.123 s. No p95 or
full gate follows from two samples per mode. Matching requires source/runtime/
PCM and model/tool settings, while allowing declared route and isolated-path
differences; do not require identical configuration candidate IDs across modes.
Managed HTTP-start timing is unexposed. Use stream observations and complete
Android admission receipts rather than inventing that timestamp. Next measure
activation-buffer residence offline; this report does not authorize more paid
trials by itself.

Only after the pilot passes and the candidate is frozen run the required full
acceptance cohort. Its minimum 20 managed ready admissions and five
independent cold starts remain required; a small pilot does not waive those gates.
Reuse valid evidence for the same candidate/runtime/backend/database instead of
repeating paid tests for report-only edits. Record request counts, purpose,
outcomes and available usage; unknown cost stays unknown. This is a testing
workflow, not a Jarvis spending-limit or cutoff-proof system.


## Revised M8 latency gate (2026-10-06)

The final fixed ready07 admission runs after functional/cold scenarios. Its Android monotonic speech-end-to-durable-receipt interval must be strictly below 4,500 ms, with `policy: single_final_under_4500_ms` and exactly one sample. A single-case managed pilot may supply only this timing assertion and M7's real voice-provider proof. Keep the full functional corpus, five independent cold starts, capture performance and privacy checks; the single sample makes no p95 claim.

Backend acceptance reads suite selection and evidence reuse from `backend/app/jarvis_tasks/acceptance-profiles.json`. M6/M7 use the same complete deterministic contract suite and their dedicated live provider assertions. Optional diagnostic skips are excluded; required skips still fail. M10 keeps the broad live regression suite.

## M8 frozen-candidate sequence

**Current policy:** M8 is completed with
[explicit user-accepted caveats](reports/m8-accepted-with-caveats-2026-10-07.md).
The full-cohort sequence below describes the original strict test protocol, not
a remaining milestone blocker. Executable acceptance now records the four
exceptions as `accepted_with_caveats` and avoids scheduling the waived bulk
voice/capture cohorts. Twenty policy tests passed. Privacy, ownership, recovery and representative
complete result/notification/follow-up tests remain in final verification.

The user-requested [clearer-audio diagnostic](reports/m8-hq-audio-pilot-2026-10-07.md)
passed two correct admissions at 3.052/3.500 seconds on unchanged source. It
retains startup greeting and imperfect prefix recognition and is not full gate
evidence. Its audio directory mixes three regenerated clips with unchanged
fixtures; do not present it as a fully regenerated acceptance corpus. Inspect
processed/delivered speech locally before broader paid retries.

The latest [final-request report](reports/m8-final-2026-10-07.md) records the
attached-sideband readiness correction, offline checks, bounded pilots and
current same-source gate review. The repaired run stopped on enabled sample
eleven with no user transcript despite successful startup. Native privacy and
Android passed; three of eight current assertions pass. Stop paid repetition
until actual post-processing/delivered speech is diagnosed locally. Readiness follows authenticated attachment;
do not restart an already-running sideband session or depend on a replayed
primary startup event. This retains Android's transport and durable-session
confirmation. Prior-source capture pairs remain diagnostics only.

See [M8 progress](reports/m8-progress-2026-10-06.md) for retained failures and
repairs. Run network-blocked contract/timing tests first. Freeze executable
source, workflow definitions and instructions before producing these proofs;
report-only documentation edits do not change candidate identity.

From the repository root, using fresh directories under project `data`:

```powershell
backend/.venv/Scripts/python.exe android/scripts/verify-capture-feasibility.py --report data/m8-native-current
backend/.venv/Scripts/python.exe android/scripts/verify-managed-admission.py --mobile-result --gate M8 --render-dir data/audio-corpus-api-v1 --report data/m8-mobile-current
backend/.venv/Scripts/python.exe android/scripts/verify-managed-admission.py --acceptance --gate M8 --render-dir data/audio-corpus-api-v1 --report data/m8-current --evidence capture_privacy=data/m8-native-current/acceptance.json --evidence mobile_result=data/m8-mobile-current/acceptance.json
```

The native privacy stage makes zero provider calls. The mobile stage uses one
real spoken admission and native worker initial/follow-up execution, including
the actual notification and mobile form. Select the form's edit intent for a
maintained-result revision; its answer-only default deliberately preserves the
document. Close Live after promised image uploads before waiting for workers.
Run the full cohort only after this pilot passes. M8 does not run M10's broad
provider suites. Its functional corpus and capture-performance cohorts remain
required; the runner validates imported privacy/mobile evidence before paid
execution and stops failed sequences. Missing health observations stay unknown.

M8 has eight assertions and M10 nineteen. `--performance-samples 1` is a bounded
capture diagnostic, never complete acceptance. `--pilot --case ready07` supplies
only the separate final timing/provider observation and deliberately returns
incomplete-corpus status; inspect its ready-latency assertion independently.
Do not reuse earlier-candidate proofs or retry unchanged failures to select a
faster result.

Harness-owned emulator/backend/frontend launches use the existing Windows Job
Object host, including termination after launcher exit or parent loss. Reused
emulators remain unowned and are not killed by harness cleanup. Failed audio
samples request a bounded media snapshot without replay, retaining PCM identity,
source consumption, RTP/playback and runtime health; missing observations remain
unknown. Source consumption alone does not prove complete received audio or
provider transcription. Diagnose incomplete transcripts before another full
paid cohort; retain the original failure.

SSE disconnects use AnyIO cancellation. Lookup-only database scopes finish the
local query and release its connection before redelivering cancellation; do not
place provider/network waits in those scopes. A contended SQLite lookup can
delay disconnect cleanup until the existing 30-second busy timeout. Write scopes
still roll back cancelled work, with rollback/close protected from repeated
cancellation. Terminal Live sidebands retire their idle watchers and pending
tools. Candidate checks prune generated/link directories and hash source files
in bounded chunks; Android's source package named data is included.

Native wake sessions carry activation_source=wake_word in provider context.
Their visible overlay acknowledges activation; Live is instructed to listen
quietly for the actual request instead of greeting over the first command.
Manual sessions retain normal greeting behavior. This adds no fixed delay or
keyword dispatch. Keep source/PCM consumption, post-processing/RTP and provider
transcription evidence distinct when diagnosing missing words; packet counts
alone are not proof of the complete received command.

The quiet-wake instruction is a behavioral request, not an enforced mute or
utterance boundary. A later capture-performance sample still greeted during
startup and transcribed only the request's tail. Preserve that failure and
investigate native processing/transport/provider recognition locally; do not
silently treat a prompt change or buffer-only proof as a reliable audio fix.

Evidence import accepts bounded regular reports up to 8,000,000 bytes: the full
functional audio report, including correlated diagnostics, exceeded the former
2,000,000-byte limit. Blocked imports include their actual validation reason.
Candidate, configuration, runtime, freshness and semantic checks still apply.
Rechecking retained raw evidence against its original candidate is useful for
diagnosis, but executable loader/test changes create a new candidate. Do not
label original-candidate passes as acceptance of that newer source. M8's
earlier quiet-wake checkpoint had seven passing assertions; the latest repaired
source has three after a retained speech-path failure. Capture performance
remains incomplete. After a promising local correction and targeted pilot, complete
the interleaved capture cohort and run the fixed final ready07 once at the end.


## Worker tool revision checks

Run focused offline checks before provider calls:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_worker_tool_policy.py tests/test_native_worker.py tests/test_m4_workflows.py tests/test_task_mcp.py tests/test_migration_recovery.py -q
```

The development migration adds persisted global policy, connector grants and exact permission questions without resetting application data. Native boundary diagnostics make no model calls; a basic boundary pass does not override the Python/Node workload gate. Set command mode during setup, never as an automatic retry. Trusted mode inherits user access and must be labeled in reports.

The retired native-experimental switch and separate research-model setting are removed. Refresh enabled workflow digests after changing definitions; an empty allowlist stays disabled. Real connector integrations remain deferred. Workers cannot configure permissions, and background agents cannot approve human-only permission questions.

Acceptance identity now covers saved policy/grants and approved skill contents. Do not modify those during a frozen run. Costs remain unknown when providers omit them; report measured usage and request purposes instead of estimating an invoice.

The final 2026-10-09 proof is `data/worker-tools-m10-db-final/combined/acceptance.json`.
`data/worker-tools-readiness-final.json` confirms baseline implementation readiness
with zero provider calls and no mutations. Its password block records the old
validator accurately. M11A has replaced that mandatory rule for personal mode; the historical raw report
remains unchanged. New readiness evidence verifies actual private transport.
Physical tests happen at M11D, and backup is optional for development data. The
substantive-request browser pilot was imported into the frozen M10 run rather
than repeated. Planning-only changes do not justify another paid acceptance run.


## Current personal host commands

Use `Jarvis.cmd Start`, `Jarvis.cmd Status` and `Jarvis.cmd Stop` from the root.
Double-click with no arguments starts the host and retains the status window.
The launcher uses a process-scoped PowerShell execution-policy override; it does
not change the Windows policy. The hidden helper contains descendants in a
Windows Job Object and waits for confirmed shutdown before another owner starts.
Ready verifies loopback-only owned ports 3000/8000, an online task pool, a private
Tailscale HTTPS proxy and Funnel disabled. No extra LLM server is mandatory.

Current URL: https://sarah.tail68fb8f.ts.net. Private state/logs are under
`.cockpit/jarvis/`; configuration and signing secret stay in ignored `.env`.
See [phone-test-checklist.md](phone-test-checklist.md) for installation and pairing.


## M11A-C acceptance complete - 2026-10-10

Current phone update: the [2026-10-11 screen repair](reports/phone-screen-fix-2026-10-11.md)
adds paired browser voice capture without creating a second voice call. Install
the updated APK over existing app data and reopen Jarvis. Its unpaid emulator
verification includes a normal Chrome tab and both invocation/latest images
uploaded to a recipe task. M11D phone confirmation is now complete by the user's 2026-10-11 acceptance.

[Completion report](reports/personal-setup-2026-10-09.md): source4f58a725,
15 required assertions passed, four accepted M8 exceptions; final admission
3676.2937 ms. Current proof is `data/m11-final/final/acceptance.json`; readiness
is `data/m11-final/readiness-final.json`. The host is Ready. Use the
[phone guide](phone-test-checklist.md) for future troubleshooting.

Keep normal host HTTPS/Secure cookies enabled. The standalone plain-HTTP privacy
contract subprocess needs `SESSION_COOKIE_SECURE=false` in its test environment,
as declared by `capture_privacy`; modifying an in-process Settings object alone
does not change a subprocess environment. Process-tree proofs need the declared
`process_tree` pool/orchestration flags disabled at launch. These are isolated test
settings, not changes to the personal host. Supply those passing same-candidate
assertions when assembling final acceptance; the strict profile/source/database
validation remains intact. Never edit failed raw reports or rerun paid stages for
report-only/profile-invocation corrections.
