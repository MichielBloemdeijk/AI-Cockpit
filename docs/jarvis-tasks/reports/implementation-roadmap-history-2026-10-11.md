<!-- Current gate policy: user revisions through personal-use setup 2026-10-09 -->

Archived on 2026-10-11. Status statements below are historical; the
[current completion ledger](../00-implementation-roadmap.md) supersedes them.

# Jarvis task implementation roadmap

Status: updated 2026-10-10. The [worker tool revision](../09-worker-tool-policy.md) replaces custom research with native Codex research and adds persisted tool selection, approved skills, subagent history and exact human connector approvals. Native Windows coding remains gated by Python tempfile failure; the selected trusted command broker remains explicit. M10 passed on source c8a0aec7: fifteen required assertions passed and four M8 exceptions remain accepted. Final ready admission: 3689.1404 ms. M11A-C are complete with final admission 3676.2937 ms and 15 required assertions passed on source 4f58a725. The user's physical M11D test remains. M11 follows the [personal setup plan](../10-personal-setup-and-phone-acceptance.md), with user phone tests last. Historical reports below retain their original observations.

The [main architecture](../../JARVIS_TASK_ARCHITECTURE.md) explains the target. Phases 1–8 own their detailed contracts; this roadmap owns delivery order and cross-phase gates. Later agreed decisions take precedence over historical design discussion. No harness comparison or additional product questionnaire is required before starting.

Physical M11D feedback on 2026-10-10 exposed microphone startup and phone Settings
issues. [Targeted phone repairs](phone-setup-fix-2026-10-10.md) replace manual
feature selection with automatic supported-feature enablement and retained pairing.
The source4f58a725 combined proof remains historical; current fixes require their
recorded targeted verification and a phone retest.

On 2026-10-11, phone recipe feedback exposed a missing browser-voice/native-capture
connection rather than an observed Chrome denial. The [screen repair report](phone-screen-fix-2026-10-11.md)
records authenticated device binding and capture-only Android invocations. Normal
Chrome capture and both-image recipe admission/upload pass without paid models;
physical M11D confirmation remains open. Earlier full-gate evidence stays historical.

The subsequent recipe reached its worker but failed to open the task database:
the worker interpreted a backend-relative SQLite URL from its attempt directory.
The [database repair](phone-database-fix-2026-10-11.md) canonicalizes DB
paths and extends unpaid Android acceptance through real worker processes and
typed recipe publication. Upload-only evidence is insufficient for this path.
The APK and existing pairing remain usable; physical M11D confirmation stays open.

The [persistent Android controls](../11-android-audio-controls.md) now unify native
wake/Live ownership across notification, assistant and WebView. Targeted offline
acceptance passes 18 background audio/recovery/control cases with real native
WebRTC and a local peer; no model is called. Screenshots remain independent and
background task publication survives Stop. See the [feature report](android-audio-controls-2026-10-11.md).
Install over the current APK to retain pairing. Final physical M11D checks remain.
This evidence does not replace historical paid admission or M8 cohort evidence.

The user-requested [clearer-audio diagnostic](m8-hq-audio-pilot-2026-10-07.md)
subsequently passed correct recipe/notes admissions in 3.052/3.500 seconds on
unchanged source. Startup greeting and imperfect prefix recognition persisted;
the user subsequently accepted M8 with these caveats. This does not change the
strict report's three-of-eight result or establish an audio reliability fix.

## Completion ledger and agreed scope

Phase files 01–08 describe responsibilities; milestones span several phases.
File 00 is this roadmap, and M0 is the investigation. The whole programme must
not be called "Phase 1". The operational runbook is a companion, not Phase 9.

The closure decisions require general project coding, subscription-first
execution with authorized API fallback, and one final ready-path admission **below 4.5 seconds**, with a two-second target. The recipe remains a thin reference; its 30-second result objective
is soft. Existing development data is disposable under [AGENTS.md](../../../AGENTS.md).

- **M0–M4 — historical passes:** only the environments and assertions recorded
  below and in their reports. They do not pass unimplemented phase contracts.
- **M5 — presentation/platform verified:** generic results, collections, storage
  and browser/emulator delivery. Its former all-architecture/rollout exit was
  not met; those outstanding obligations are explicitly reassigned to M6–M11.
- **M6 — passed at the recorded closure checkpoint:** supervisor assignment and general coding; Phases 1/4. Real failing-test repair, same-task restart, descendant termination and all 633 deterministic backend checks passed. M10 revalidates the final candidate.
- **M7 — passed at the recorded closure checkpoint:** managed-only intent, preferences, tier and fresh-context routing; Phases 1/2/8. All 633 deterministic backend checks and fresh live priority-route proof passed. Subsequent M8 repairs require final-candidate M10 evidence.
- **M8 — completed with accepted caveats:** production custom-wake/capture/audio integration and measured admission; Phases 2/3/7. Correct final pilots took 3.052/3.500 seconds. The user accepts imperfect transcription/startup greeting and remaining unproven speech reliability/full-cohort coverage. Preserve failed observations; do not claim all eight automated assertions passed. See [the acceptance decision](m8-accepted-with-caveats-2026-10-07.md).
- **M9 — completed:** actual dedicated subscription execution, active-owner
  competition, supervisor restart and real same-task API handoff after simulated
  exhaustion passed on source cbb51a2e. All 657 deterministic contracts passed.
  See [M9/M10 closure](m9-m10-closure-2026-10-07.md).
- **M10 — completed under the accepted M8 caveats:** source c8a0aec7 passed all fifteen required assertions: 673 backend, 192 frontend and 28 Android tests, builds, browser, migration, capture privacy, coding/restart, subscription/API handoff, descendant containment and the real mobile result journey. Final admission: 3689.1404 ms. Four M8 exceptions remain explicit. Native coding stays gated; tested trusted mode is labeled. See the [completion report](worker-tools-closure-2026-10-08.md).
- **M11A-C complete; ready for final manual phone test (M11D):** optional password/
  personal access and simple startup (A), one-time QR/link pairing and unified
  phone access (B), agent verification (C), then the user's physical checks and
  normal-use confirmation (D). No mandatory full backup for disposable data.
  See the [remaining completion plan](../10-personal-setup-and-phone-acceptance.md).

**Implementation complete** means M10 passes under the revised policy, including
real M9 subscription evidence and explicit M8 accepted exceptions.
**Personal use accepted** means M11's setup and final phone checks also pass. Every open
requirement belongs to one of the gates below, which specifies its phase owner,
work, environment, checks and exit. Update this ledger when evidence changes;
phase documents link here instead of maintaining competing gate statuses.

M6 and M7 can proceed independently; M8 follows M7's routing interfaces. M9
preparation can proceed alongside them. Account setup cannot block independent
implementation, but its absence cannot be waived to pass M9 or M10. M11's personal
setup is the next development work; physical tests stay at the end.

The [2026-10-06 gate review](implementation-gate-review-2026-10-06.md)
is historical; the runner subsequently grew to nineteen M10 assertions and the
optional-diagnostic suite selection was repaired. The user's accepted M8
exceptions are now encoded explicitly: four checks are `accepted_with_caveats`,
leaving fifteen required passing M10 assertions. Twenty policy checks passed;
the runner no longer schedules waived bulk voice/capture cohorts. Retain privacy, durable
task correctness, recovery and provider continuity checks. Saved subscription
login and funded voice access are observed; no current account action is requested.
Reuse same-candidate evidence only within its declared scope/freshness rules.

The 2026-10-05 admission investigation adds high-resolution local spans and
offline routing/capture/SSE benchmarks. Commit-triggered delivery/readiness and
nonwaiting optional-image checks now remove the demonstrated local waits.
Capture/result observations now use bounded queued batches, with loss reporting
and shutdown/revision-identity coverage. Continue local timing work without API
calls; prioritize repeated DB transactions. These diagnostics do not pass M8 or
M10. See [the baseline report](admission-timing-2026-10-05.md) for the
remaining provider/transport gaps and [the triggered follow-up](admission-triggered-2026-10-05.md)
for the current local measurements.
The [telemetry follow-up](admission-telemetry-2026-10-05.md) records
controlled comparisons, complete local routing-to-SSE receipt and remaining gaps.
The subsequent local revision replaces full-history task-ID resolution with
task-row reads, removes lookup-only gateway commits, reuses selected admission
turns and triggers result-page delivery after commit. Commit/flush/connection
spans and native SQLite/loop diagnostics investigate intermittent stalls.
[The read/delivery report](admission-read-2026-10-05.md) records the current
measurements, unresolved commit tails and cross-process notification limitation.
Provider testing follows the [runbook's small-pilot policy](../rollout-runbook.md#provider-test-economy--agreed-2026-10-05):
promising local/mock results first, then a targeted pilot before full frozen
acceptance. More credits do not trigger broad paid development reruns.

After the user restored credits and authorized targeted end-to-end testing,
[the live pilot](admission-live-pilot-2026-10-05.md) observed one valid
client receipt at **7.71 s** and one failed managed receipt report with a forensic
**4.76 s** estimate. Both admitted correct tasks; no p95 or route selection follows
from these single observations. Remaining delay concentrates before delegation
and in selector HTTP/provider response. Pilot limits stopped further paid cases;
continue instrumentation/correction locally before another small pilot. Credits
are no longer the observed blocker, but M8 latency and final M9/M10 acceptance
remain open.

The subsequent [model-output investigation](admission-model-outputs-2026-10-05.md)
confirms that implicit provider strict-mode normalization makes optional dispatch
fields mandatory. The user-requested correction now sets `strict:false` in the
shared tool catalog and clarifies optional fields while preserving completed-call
validation. Its latency benefit remains unmeasured; no paid rerun accompanied
the correction. Existing priority requests already use Fast mode. Ultrafast
requires a different selector model at higher token prices and has not been enabled.
Exposed reasoning counts are modest; exact first-delta/throughput timing
remains unavailable after two failed diagnostic exports. Terminal export repair
is tested offline only. Those failures and additional usage remain recorded;
no acceptance gate or latency target changes.

The [single authorized live verification](admission-optional-schema-verification-2026-10-05.md)
confirms optional-field omission and successful raw export. It admitted one correct
task at **6.748 s**, so the four-second gate remains unmet. Most selector delay
preceded the first argument; the argument stream lasted **0.221 s**. A shutdown-only
duplicate error report was corrected afterward and verified by 46 offline tests,
without another paid trial. The live candidate and later cleanup candidate remain
distinct; no broader acceptance or p95 claim follows.

The subsequent [matched two-per-route comparison](admission-routing-paired-2026-10-05.md)
used identical ready07 input, executable source and model settings after 68
offline checks passed. Managed admitted correct tasks in **3.551/3.918 s**;
client in **5.456/4.791 s**. Managed was **1.389 s faster on the two-sample mean**,
with selection overlapping transcript correlation. Both managed observations
were below four seconds, but p95 and M8/M10 acceptance remain unestablished.
Managed is now the sole routing path following the user's decision. See
[the managed-only implementation](managed-routing-only-2026-10-06.md).
Investigate audio-buffer/final-frame
timing locally before further paid tuning. Exactly four voice trials ran,
with eight completed selectors, no worker inference and no runtime changes.

The [final managed-only verification](managed-admission-final-pilot-2026-10-06.md)
subsequently recorded one correct admission at **3.7746 s**, within the prior
managed range and below four seconds. This is one diagnostic sample, not p95
acceptance. No additional paid trial followed; M8/M10 remain open.

## M0 — Resolve the native Codex contract before broad integration

Implementation status (2026-09-22): **complete for the pinned Windows host and
the API-authenticated execution route.** The [latest live report](codex-m0-live-current.json)
has 18 passing gates, no failures, and no gating blockers. It proves the native
session, restart/resume, follow-up, streaming, interruption, filesystem sandbox,
descendant-stop, crash classification, model, API-auth, restricted workflow
profile, and acknowledged broker-tool contracts. Subscription continuation is
the sole non-gating blocked check: subscription execution remains disabled until
a separately authenticated Jarvis ChatGPT profile passes the same continuation
probe. This was non-gating for M0's API route; subscription is now required by
M9 before implementation completion. See the [report notes](README.md).

The selected minimum safeguards are part of the M0 result, not optional advice:

- pin `openai-codex` and its bundled CLI to `0.155.1`;
- use a dedicated, single-owner Jarvis `CODEX_HOME`, never copied desktop
  credentials, and bind every host to its authorized runtime workspace;
- select the native Windows sandbox explicitly (`unelevated` on this host),
  validate read-only/write/escape behavior at startup, and fail closed if it
  cannot be proven;
- use one App Server process tree per writable execution boundary on Windows;
  immediate stop kills that tree because `command/exec/terminate` is not
  supported for Windows-sandboxed processes;
- expose workflow tasks only to allowlisted brokered Jarvis MCP tools; disable
  native shell/unified exec, web search, subagents, and undeclared MCP tools;
- commit/fsync the Jarvis action journal before returning tool success. Restore
  a started-but-not-succeeded side effect as `uncertain` and never replay it
  automatically;
- use the verified API route initially. Keep subscription execution serialized
  and disabled until its separate profile is verified.

Build a small opt-in probe against the selected Codex runtime, in disposable test directories and a separate Jarvis runtime profile. Use the official Python SDK where sufficient and local App Server methods behind the same facade otherwise. Pin the tested SDK/runtime versions together and save a redacted capability report. Do not change the user's desktop Codex configuration or authentication files.

1. Discover available models/efforts and verify Luna/low for the background profile. Prove thread creation, streamed items, persisted history, resume after host restart, and a follow-up in the same task. Record native IDs and supported protocol methods. Keep native legacy history initially; Jarvis API history pagination is independent of native storage format.
2. Run a harmless writing tool with a descendant process. Interrupt, confirm the entire writable execution boundary has stopped, transfer its test directory, and prove the old writer cannot modify it afterward. Exercise crash, pause, cancellation and host restart. Measure stop latency; an interrupt acknowledgment is insufficient.
3. Crash after a completed tool and during a side effect. Establish exactly where completed observations become durable and what a restored agent can reuse. If built-in tools can advance before Jarvis records the result, require an acknowledged tool boundary/wrapper or a narrowly documented runtime change. Never label a stream notification an enforced checkpoint.
4. Run a workflow profile with only Jarvis tools. Attempt native shell, file mutation, subagent and undeclared MCP access. The profile must enforce denial through runtime configuration plus OS/resource isolation, rather than instructions alone.
5. Verify the supported subscription authentication arrangement and compatible API continuation. Simulate quota/billing errors; do not deliberately exhaust real credits. Record credential ownership, refresh behavior and session-location compatibility. No concurrent private jobs may independently mutate a copied file-based credential cache.

Use one subscription execution at a time until a supported concurrent arrangement is established for the actual host and pinned runtime. This is an initial authentication safety bound, not a new worker category or permanent concurrency requirement. Prefer a supported single credential owner if it can also preserve isolated writers. Additional work, including independent background decisions, can use the already-authorized API route when subscription execution is unavailable. Never make voice admission wait for an authentication slot. If this fallback is unavailable, retain the work and show its actual provider blocker.

The auth constraint comes from [OpenAI's private automation guidance](https://learn.chatgpt.com/docs/auth/ci-cd-auth); it is not evidence that all supported desktop concurrency is forbidden. Verify the exact intended arrangement. Subscription access and native process isolation are the two principal early integration risks.

**Exit:** a passing report, exact version pins, and a chosen containment/tool/auth strategy. A failing subtest names the gap and the smallest required correction; dependent features remain disabled until it passes. This is a narrow Codex feasibility check, not a second task framework or a competing prototype.

## M1 — Durable task contracts and deterministic execution

Extend the existing services rather than introduce another scheduler. Primary ownership stays with `task_service.py`, `agent_execution.py`, `worker_coordinator.py`, `task_worker.py`, the existing database tables/migrations, and task routes. New adapter/helper modules can live beside them.

- Introduce task turns, execution runs, attempt/run binding, relevant state revisions, native bindings and durable operation records as specified in Phases 1 and 4. Backfill without changing task IDs, existing attempt numbers, workspaces or event identities.
- Implement the homogeneous supervisor/pool, queue ordering, resource normalization and overlapping-directory exclusion. Keep one standby and up to eight busy workers; admission and supervision run outside those workers.
- Implement requested versus confirmed controls, process-exit observation, fencing and bounded safe recovery. Lease release follows confirmed writer stop. Persisted native state and artifacts live outside disposable execution processes.
- Expose snapshot/history replay, run-aware controls and follow-ups through existing authenticated task APIs. Preserve SQLite; use short transactions, uniqueness constraints and bounded lock retries.
- Drive the real services with a deterministic executor first. Then wire the M0-proven adapter and repeat the session, action and writer tests through the actual task API.

**Exit:** migration/restart/race tests pass, eight busy fixture workers do not produce uncaught database-lock failures, and all task kinds use one continuation/control contract. Mobile history reconnect and Jarvis inspection see the same retained evidence.

M1 implementation status (2026-09-24): **complete for the pinned,
API-authenticated Windows route.** The additive schema, turn/run/control APIs,
eight-busy coordinator, contained supervisor, brokered Codex file action
boundary, Jarvis controls, task-page history, automatic supervisor
election/repair, exact-exit orphan reconciliation, per-task event cursor, and
immutable per-run `task-context.v1` handoff are implemented. Digest-checked
retry reuse, bounded task-scoped evidence retrieval, shared-directory change
checks, and post-exit file-write reconciliation are covered by deterministic
and opt-in live tests. An unresolved uncertain operation blocks further
brokered writes in that run. Changed or unscannable shared targets and
non-filesystem resources without state observers fail closed on reacquisition.

The first constrained, source-controlled workflow (`text_report@1`) is
admitted only under `TASK_EXECUTOR=codex` and an exact
`TASK_WORKFLOW_ALLOWLIST=id@version:digest` entry. Its pinned YAML/Markdown
definition allows only task-history retrieval and typed result publication;
native mutation tools stay disabled. Publication is atomic and replay-safe,
records a durable operation and source-backed JSON artifact, and renders on
the task detail page. The same-task follow-up and a long-history continuation
passed a real-Codex smoke test, including model-assisted rolling summaries
of old turns. Original turns/events/artifacts remain retrievable by identity.
The worker also handles the publication-before-native-stream-drain race.

The pool remains opt-in (`TASK_POOL_ENABLED=false`) and workflows remain
disabled by default. Same-run recovery proves brokered private-workspace file
writes after exact process-tree exit; arbitrary native or external effects
remain fail-closed. A pinned context is a run's starting snapshot; later
actions remain in durable event/operation history and must be checked before
another side effect. The eight-busy fixture asserts under-two-second p95
admission, but does not stand in for future capture/upload workflow timing.
An Android API-36 emulator and debug APK passed the scripted
process-stop/reopen history reconnect against a disposable backend using
`android/scripts/verify-task-history-reconnect.ps1`. The typed workflow API,
MCP guard, result view, publication replay, and semantic compaction have
automated regression tests. M2/M4 still own actual screen-capture upload and
the recipe workflow; this M1 text workflow does not claim those capabilities.
The final M1 regression passed 348 backend tests (three billed live tests
opt-in), 164 frontend tests, Android debug unit tests, the real constrained
workflow/long-history smoke test, and the supervised Codex replacement test.

Agent-runnable verification requires no interactive input for the local
deterministic tests (`cd backend && uv run --group dev pytest -q`) or frontend
tests (`cd frontend && npm test -- --run`). Android JVM unit tests also run
unattended (`cd android && .\\gradlew.bat testDebugUnitTest --no-daemon`). The opt-in real Codex tests run with
`JARVIS_LIVE_CODEX=1` and the already-provisioned API-authenticated Jarvis
profile; an agent can run them unattended on that host, but CI needs a managed
test credential and isolated runtime profile. M2/M4/M5 subsequently supplied
the screenshot/private-upload/result and browser/emulator slices that were
unavailable at the M1 checkpoint; their evidence is recorded below.

Current closure assignments for M1's original follow-on gaps:

- M6 completes supervisor-only assignment and required project coding. Additional
  non-filesystem mutation integrations remain future scope and fail closed.
- M8 completes custom-wake/fresh/real-audio acceptance beyond the existing
  system-gesture/fake-Live emulator path.
- M9 requires a separate authorized subscription profile and account evidence;
  M11 retains physical setup. Neither changes M1's historical API-route result.
- M10 fixes repository-wide `tsc --noEmit` failures, including newer result/API
  tests, and supplies current acceptance orchestration. Older builds and unit
  passes are separate evidence. A fresh provider CI host still needs managed
  credentials and an isolated profile; the prepared local host can run the gate.

## M2 — Voice, Android capture and minimal result slice

Connect managed voice delegation directly to the shared APIs. Implement invocation-screen status, the two-second pending readiness gate, admission/upload correlation and fail-on-error behavior. Add the minimal maintained `document.v1` result view and notification navigation; do not wait for collections or richer app storage.

Prove a complete fixture request through the emulator and a real Codex task through the backend/mobile history view. Then connect them. A synthetic screenshot or mocked native writer cannot substitute for the corresponding real acceptance case.

**Exit:** the invocation, upload, task, result and notification carry matching identities; rejection creates no task; admitted capture failure requires no worker; reconnect does not restart execution. Report ready/cold and queued latency separately.

**2026-09-30 implementation checkpoint:** The guarded `screen_document@1` vertical
slice now passes unattended full Android API-36 and API-37 emulator runs using the real
`onHandleScreenshot` bitmap, fake Live routing, run-bound private upload, a
supervised real Codex worker, maintained result, an actual Android completion
notification tap into the WebView, and a process reconnect without a second
worker attempt. Run `cd backend && uv run python
../android/scripts/verify-screen-workflow.py`; it starts disposable loopback
services, retains evidence in a uniquely named temp folder, and stops its
services on exit. Deterministic rejection, upload-failure, identity-fence, and
idempotency cases are in `test_task_capture.py`; the live image-to-Codex case
is opt-in in `test_codex_task_runtime_live.py`. The M2 milestone exit is met
for this guarded route: the connected run proves matching invocation/capture,
upload, run, result and notification identities; deterministic tests prove
pre-admission rejection without a task and post-admission failure without a
worker; the emulator harness proves reconnect does not start another attempt
and reports the latency stages separately.

The harness now defaults to `JarvisM3Api37`, using the installed
`system-images;android-37.0;google_apis;x86_64` revision 6 and 4 GB RAM.
API-37 capture is invoked by the actual system assistant key; debug arguments
only bind fixture identities and select fake Live. No capture-policy bypass or
injected bitmap is used. API-36 remains selectable through `JARVIS_TEST_AVD`
and `JARVIS_TEST_IMAGE`.

This is not a full Phase-3 or physical-device sign-off. API-37 verification
exposed missing `ACCESS_LOCAL_NETWORK` permission handling and missing
`usesAssistData`/`usesAssistScreenshots` declarations; both are now implemented.
Android 17 also strips capture flags from background self-invocation without a
system-recognized hotword/foreground signal. The custom local-wake path needs
supported integration and native verification before wake-based screen
workflows can be enabled. Passing system-gesture capture does not establish
custom-wake capture. The subsequent [pre-M3 feasibility pass](pre-m3-feasibility.md)
proves visible-assistant screen A/fresh B and native protected/denied/locked
rejection on API 37; production initial-wake timing and fresh Live routing
remain broader Phase-3 implementation work.
The fixture reports fake-Live cold-ready, monotonic backend
ready-call/admission, capture/upload, and queue/start values separately. The
fake provider and one-run samples cannot establish production p95 gates. Real
speech-end, provider ready-path, and physical Pixel/OEM timing remain
unmeasured.

## M3 — Autonomous background management

**Complete (2026-09-30), guarded Windows/API route:** durable intake/decision
loops, shared guarded capabilities, bounded fresh context, deferred recovery,
execution preferences, telemetry, rollout/backlog and voice-reconnect behavior
are implemented and agent-tested. Final backend regression: **420 passed,
7 skipped**; the expanded native suite passed **3 tests**, including two paid
decision cases and exact post-exit history inspection. The API-37 integrated
emulator and Android build/unit tests passed. See Phase 8's
[implementation progress](../08-background-orchestration.md#13-implementation-progress--2026-09-30)
for implemented behavior, evidence scope, test commands and external rollout gates.

Implement Phase 8's code-only feedback intake/forwarding and single background decision slot. Reuse the Codex adapter with a restricted orchestration profile, separate from the occupied worker pool. Default to Luna/low, eligible subscription first and authorized API fallback, relying on provider account settings and retaining actual auth/quota/billing errors.

Use the existing event log/checkpoint and attention services plus one pending-decision table. Add relevant-state preconditions to common task mutations. Test absent voice, explicit user controls racing autonomous actions, restart replay, unresolved questions, and deliberate background inference blockage.

**Exit:** useful autonomous progress without voice; direct voice admission, controls, event intake and factual forwarding continue while background inference is busy. No second automatic worker-feedback owner, synchronized master conversation or recurring AI health loop exists.

**Readiness:** M3's guarded-route exit is met; proceed to M4. Custom-wake capture
integration, provider/physical timing, and older frontend test-fixture type
errors remain separate work. Provider account settings own spending limits; no
Jarvis cutoff proof is required. Subscription remains unavailable until a
dedicated profile passes continuation verification. That sign-in is not required
to implement and test the durable decision loop.

## M4 — Reusable fast workflows and two reference tasks

Extend the existing Phase 4 YAML/Markdown loader and constrained execution path into shared capability, input-context and result-type registries. Remove the current coupling of tool selection and screen-workflow discovery to fixed output schemas. Generate workflow descriptions and routing examples from enabled definitions; use the existing generic gateway and task lifecycle. Preserve existing definitions and pinned runs while adding the new contracts.

Implement shared image/context access, batched research, clarification, publication and continuation. Phase 6 supplies the thin `recipe_from_screen@1` example and a small `recipe.v1` schema/renderer registered through the common result platform. One adequate fetched source can support completion; do not require corroboration rounds or recipe-specific optimisation. Defer shopping-list controls, serving calculators, dietary/equipment adaptation machinery and specialised recipe extraction. Explicit user constraints still require compliance or a truthful unsupported outcome.

Then add `research_screen@1`: a concise sourced explanation of the visible subject using the same image/research capabilities and `document.v1`. This second task must require only its definition, instructions, fixtures and enablement configuration, with no transport, scheduler, lifecycle, routing-prompt or result-service changes.

Run compact deterministic reference cases, real-provider examples and the emulator path through agent-runnable tooling. Reuse shared security/recovery suites. Measure repeated ready and cold paths with a small fixed input set, including nested model/tool usage. Investigate measured bottlenecks with bounded tuning; finish when the request is adequately supported. Subjective human recipe review is optional feedback, not an M4 dependency.

**Exit:** both reference tasks publish useful sourced results through the shared platform; clarification and same-task revisions work, failed updates preserve the previous result, and the second-task addition proves reuse. Reports include sample counts, median/p95 latency, usage and quality failures. The thirty-second ready-path target remains a soft objective; report misses without claiming it has been met. Full generic collections/actions/storage remain required M5 work, not prerequisites for M4.

**Implemented and verified (2026-10-01):** the M4 guarded-route exit is met.
Independent capability/input/result registries, compatible v1/v2 definitions,
catalog-generated routing metadata, journaled batched research, durable questions,
retained context/evidence and fenced expected-base revisions serve both examples.
Recipe-specific code is only its definition/fixtures and minimal type/renderer;
`research_screen@1` adds only its package and isolated enablement configuration.
Both examples passed real Codex/API publication and source-reusing same-URL edits,
and actual API37 screenshot, notification, WebView and reconnect acceptance.
Deterministic suites cover clarification, invalid output, partial/all read failure,
capability/URL safety and preservation of prior results after failed updates.

The final regression is 447 backend tests passed (nine opt-in skips), 166 frontend
tests passed, and a passing frontend production build. Four final native input
samples (tomato pasta and lentil soup for each definition) produced four initial
results plus four same-task revisions without new searches. Initial cold native
medians/p95 were 25.63/27.94 seconds for recipe and 36.40/37.41 for research (n=2
each). Follow-up medians were 12.59 and 14.39 seconds. Emulator admission-to-ready
was 34.17 and 26.27 seconds (n=1 each). These tiny samples do not establish a
production guarantee or prove the initial ready path always meets thirty seconds.
Usage includes the separately billed search capability; invoice cost is unavailable.
See [M4 evidence and reproduction](m4-acceptance.md). Production research
and workflow allowlists remain disabled. The M5 platform checkpoint follows;
remaining implementation closure now belongs to M6–M10.

## M5 — Full presentation and result platform

Status 2026-10-02: platform implementation and autonomous/emulator acceptance verified. Catalog/backfill, collection/saved UI, bounded optimistic storage with revocable app HTTP capabilities, document references/retained attachments, durable result SSE and the production static result shell have passing autonomous coverage. General agent Document publication and explicit new/update/answer associations have deterministic and live Codex proof, including atomic delivery rollback. Password-backed production navigation, metadata timing/delivery observations, actual API-process restart/disablement and browser copy/print/offline recovery pass. Actual API37 notification-to-WebView login/return, saved/cookie reopen, backlinks and external-browser intent pass; the combined backend/frontend/Android checkpoint also passes. See [M5 progress](m5-progress.md) for the historical evidence and commands.

The completed scope includes Phase 5 maintained views, unified catalog, collection search/pagination, registered actions and namespaced app storage. Documents and Recipes share collection infrastructure; Recipes adds its type, minimal renderer and catalog metadata, without specialised browsing or controls. The per-result saved flag demonstrates generic actions/storage separately from canonical content. Recipe edits remain ordinary task follow-ups.

Scope revision: the original M5 also required all architecture acceptance and
unattended rollout. That broad exit was never passed. Coding/scheduling, routing,
native voice/capture, subscription/API handoff, final integration and physical rollout
are now explicit M6–M11 obligations. Renaming M5 does not waive or pass them.

**Exit:** Phase 5's presentation/platform contracts pass backend, browser and
API37 delivery/restart acceptance for the recorded candidate. M10 revalidates
them with the completed system; M11 verifies physical delivery.

## M6 — Complete scheduling and general coding

**Owners:** Phases 1/4. **Environment:** isolated Windows projects, pinned Codex,
deterministic faults and an authorized live provider. **Status:** implementation
present; current-candidate acceptance passed. See [closure evidence](m6-m7-closure-2026-10-06.md).

1. Move task claiming into the elected supervisor. Persist assignments bound to
   supervisor generation, child identity, run, attempt and claim token before
   execution. Children register readiness and execute their assignment; they do
   not independently claim the queue. Reconcile a lost assignment/child using
   existing fencing and confirmed-stop rules. Retain FIFO/importance, blocked
   task bypass, one standby and the eight-busy ceiling.
2. Extend the general profile from private-workspace file/history tools to
   authorized project inspection, edits and build/test commands. Resolve target
   roots and command working directories from server-owned leases. Journal
   commands through the configured native or explicitly selected trusted command
   boundary and per-attempt process tree. Current setup selects trusted mode,
   whose commands inherit Windows-user access;
   the unjournaled experimental profile cannot satisfy this gate.
3. Persist command/file outcomes before returning success. Preserve uncertain
   interrupted effects for inspection, never automatic replay. Reacquire and
   check project state after another task had access. Workflow and background
   profiles retain their narrower tool inventories.
4. Initial mutable resources are filesystem projects. Non-filesystem mutations
   remain unsupported until a separate capability supplies authorization,
   observation and reconciliation; an unobservable target fails closed.

**Checks/exit:** a real general coding task changes a disposable project's
source, runs failing then passing tests, and continues after host restart under
the same task. Verify eight-worker contention, assignment loss, competing
supervisors, crash before/after assignment, graceful/immediate controls, external
project changes and stale callbacks. After cancellation/replacement and lease
transfer, the old command and descendants cannot write. No uncaught SQLite lock
failures or duplicate durable effects are allowed. Record commands, code/test
artifacts and writer-stop evidence under M6 assertion IDs.

## M7 — Complete Live routing contracts

**Owners:** Phases 1/2/8. **Environment:** gateway/service and fake-provider
fixtures; live tier support is exercised again in M8. **Status:** implementation
present; current-candidate backend and live priority-route acceptance passed. See [closure evidence](m6-m7-closure-2026-10-06.md).

1. Persist a backend-owned user-turn scope and per-outcome `intent_id` shared by
   `dispatch_task`, `start_workflow` and `continue_task`. Provider call IDs still
   identify exact tool deliveries; they are not the cross-tool intent key.
   Replay returns the existing admission. Changed arguments or another route
   under the same intent conflict; explicit distinct outcomes use distinct IDs.
2. Use one execution-preferences contract with `model`, `reasoning_effort` and
   `override_reason` across admission, continuation and background controls.
   Validate permissions/defaults centrally and audit changes at safe boundaries.
   Return durable task/turn/run/result/capture identities consistently.
3. Handle activation/fresh collection preparation internally in admission and
   continuation. Proactively request current capture on first user delegation;
   bind it to device/session/activation and originating turn. There is no standalone
   selector capture tool. Normal follow-ups reuse retained evidence without uploads.
4. Set delegated `service_tier: "priority"`, leaving voice/worker profiles
   independent. Persist requested/actual tier, available usage/cost and latency.
   An explicit unsupported-tier response allows one default-tier fallback;
   arbitrary errors do not. Preserve intent and committed action identities.

**Checks/exit:** completed/partial/reordered tool events, exact replay, concurrent
cross-tool conflicts, multi-intent requests, ambiguity, follow-ups versus
clarification, stale catalog/session, fresh capture, missing override reasons,
unsupported settings/tier and failure after admission commit. No duplicate task
or unsupported spoken progress claim; background inference cannot hold voice
admission or controls. Phase 2 owns the full protocol assertions.

## M8 — Complete Android capture and real voice acceptance

**Owners:** Phases 2/3/7. **Dependency:** M7 interfaces. **Environment:** pinned
API37 emulator, real assistant callbacks, recorded audio and authorized Live/API
providers. **Status:** completed with user-accepted caveats on 2026-10-07. The
[decision record](m8-accepted-with-caveats-2026-10-07.md) supersedes the
original full-cohort exit below. The original test failures remain unchanged.

The [2026-10-06 M8 investigation](m8-progress-2026-10-06.md) records
native capture, recipe-routing and mobile follow-up repairs. The executable gate
now requires native privacy and the real sourced-result journey explicitly,
alongside the full audio and controlled capture-performance cohorts.

The earlier quiet-wake checkpoint passed seven of eight assertions, then
capture sample four lost the request prefix. The final-request investigation
completed twenty capture pairs before a separate startup timeout. Backend
attached-sideband readiness was repaired and survived a new run, but enabled
sample eleven produced no user transcript or task despite successful startup.
The fixed ready07 pilot passed at 3.534 seconds; native privacy and all 28
Android tests passed. The current gate review has three of eight passing
assertions. Source consumption and RTP counts cannot establish intact processed
speech or complete provider receipt. Paid testing stopped pending local audio
processing/delivery diagnosis. Those are retained strict-test findings; the user
subsequently accepted milestone completion with those limitations.
See [the retained final-request evidence](m8-final-2026-10-07.md).

1. Stage the first custom-wake screenshot once the assistant window is visible,
   preserving one root activation and Live session. Separate screenshot/basic
   context from rich assist structure without changing the stored preference.
2. Connect fresh capture through the public gateway: screen A, clarification,
   screen B, then task admission or same-task continuation. Retain context
   fencing, privacy, expiry and no automatic capture/upload retries.
3. Add debug-only recorded audio through the production wake/handoff and WebRTC
   input-buffer seams, plus playback observations. Use real RTP/session routing.
   Synthetic bitmap injection and ADB screenshots cannot provide workflow input.
4. Run a fixed, versioned spoken corpus covering both reference workflows,
   general coding admission, ambiguity, nonmatches, multiple intents, questions,
   controls and follow-ups. Assert durable outcomes and retain diagnostic audio
   privately; no routine case requires a person to speak or tap.
5. Apply Phase 7's protocol: the full ready functional corpus and five
   separate cold starts, followed by one final ready admission. Speech-end to client receipt of durable
   admission targets two seconds; one final end-to-end sample **below 4.5 seconds**, including capture readiness, passes this iteration's latency gate. This is not a p95 guarantee.
   Also pass Phase 3's callback, Live-ready overhead, fresh-capture and upload
   performance checks. Report execution queue time independently.
6. Allow at most two measured catalog/transport tuning iterations of managed
   Responses delegation. The client selector has been removed following the
   matched comparison and the user's decision. If managed still misses the gate,
   report the remaining stage/provider delays; a final sample at or above 4.5 seconds leaves the latency assertion open.

**Original checks/exit (superseded by the accepted-caveats decision):** the production custom-wake/audio path reaches a sourced result
and notification, reopens and accepts a follow-up. Automatically exercise
protected/blank, locked, denied, expired, replaced and failed-upload cases,
interruption/reconnect, and blocked background inference. Both functional and
timing assertions pass; a fake-Live or system-gesture pass alone cannot close M8.

## M9 — Complete subscription-first execution and billing continuity

**Owners:** Phases 4/7/8. **Environment:** separately authorized private Jarvis
subscription profile and API profiles, plus billing/auth fixtures. **Status:**
completed on source cbb51a2e. Actual subscription execution, active-owner
competition, supervisor restart and same-task API handoff passed; the M9
evaluator passed. See [closure evidence](m9-m10-closure-2026-10-07.md).

1. Provision the Jarvis subscription profile through a supported login flow.
   Never borrow desktop credentials. Share one subscription execution/refresh
   lease across worker and background hosts; release it only after its owner
   stopped. A stale heartbeat alone cannot grant a second credential owner.
2. Prefer eligible subscription execution; busy/unavailable subscription
   capacity uses authorized API fallback. Admission and code-only services do
   not wait for the auth lease. Keep runtime tool profiles and filesystem
   isolation independent of the selected authentication route.
3. Record route selection, reason and handoff at a safe turn/continuation
   boundary. Resume a compatible native binding; otherwise restore portable
   context under the same task. Unknown side effects remain unresolved rather
   than being repeated to change authentication mode.
4. Verify actual subscription execution and safe API handoff. Simulate provider
   exhaustion/denial without spending deliberately; preserve real authentication,
   quota and billing error handling. Cost reporting is optional and unknown costs
   remain unknown. No app budget engine or cutoff-proof gate is required.

**Checks/exit:** real subscription work, host restart/continuation and real API
fallback pass. Fixtures force worker/background credential competition, refresh
ownership loss, expired auth, quota exhaustion, fallback denial and uncertain
actions at handoff. Record actual auth route and available usage/cost without
credentials. Initial account login/evidence can require user help; all subsequent
cases run unattended. M0's historical non-gating subscription result cannot
pass this required gate.

## M10 — Final autonomous acceptance and development readiness

**Owners:** all phases; Phase 7 owns the runner/evidence contract. **Dependencies:**
M6–M9. **Environment:** one current candidate on the prepared Windows/API37 host.
**Status:** completed under the accepted M8 caveats (2026-10-09). Native research replaces
the former source-fetch blocker. The first revised run exposed a SQLite telemetry
cleanup race and a browser microphone fixture issue; both were repaired after
offline verification. A second run exposed a cancelled per-connection WAL setup
and a wake-only browser input; both were corrected and retained as failures.
All 661 offline cases and eleven timing checks passed. Frozen source c8a0aec7
passed all fifteen required combined assertions and retained four accepted M8
exceptions. Final admission: 3689.1404 ms; real mobile admission: 3473.7376 ms.
Read-only readiness confirms implementation completion, while owner-login setup
and physical rollout remain M11 work. See [the current report](worker-tools-closure-2026-10-08.md).
The [preceding checklist](m9-m10-closure-2026-10-07.md) retains historical
failures and does not attest the revised source.

1. Retain the required frontend type-check command and repaired test/e2e fixtures.
   Historical type/unit/build passes exist; rerun them for the final candidate.
2. Use the implemented single `acceptance` command and candidate-bound evidence format in
   Phase 7. Run backend, frontend, Android, browser, provider and emulator
   assertions for the same source/configuration/runtime candidate. Compose
   existing suites rather than creating another test framework.
3. Replace the permanently blocked readiness report with evaluated required
   evidence. Missing, skipped, failed, stale or different-candidate evidence
   cannot pass. Account attestations cannot replace executable checks; a readiness
   report never supplies deployment authorization.
4. Reverify coding, recipe and research; clarification/revisions; shared
   attention; cancellation and uncertain effects; resource contention; crash,
   reconnect, migration, actual process restart and feature disablement.
5. Bring the configured development schema/assets/configuration current and
   remove obsolete compatibility paths where appropriate. Stop writers before
   scoped resets; development data preservation alone requires no backup or
   permission. Preserve tests of intended long-term persistence/recovery.
6. Update implemented-system documentation and every phase's evidence links.
   Keep bounded attachments, trusted generated code and deferred external
   mutation capabilities explicit. Unknown costs remain unknown; the 30-second
   recipe objective stays soft with measured misses reported.

**Checks/exit:** M6/M7/M9 and the remaining required current-candidate assertions
pass, with M8's accepted exceptions explicitly represented in the report;
development preparation is current; every required phase contract is verified
or explicitly assigned to M11. This is the only **implementation complete**
declaration. Optional scaffolding/report convenience commands do not block it.

## M11 — Simple personal setup and final phone acceptance

**Owners:** Phases 3/5/7/8 and [runbook](../rollout-runbook.md).
**Dependency:** M10 baseline passed. **Status:** A-C complete on
2026-10-10; D user phone testing and normal-use confirmation remain. The [detailed plan](../10-personal-setup-and-phone-acceptance.md)
owns the checklist and supersedes former mandatory password/full-backup gates
for this personal Tailscale setup.

1. **A — access/startup:** existing Windows host and stable Tailscale URL,
   explicit password-optional personal mode, optional plaintext password in
   private configuration, and one start/stop action with clear readiness.
2. **B — phone linking:** one QR/link action, persistent device identity,
   no second WebView login, guided Android permissions and simple unlink/recovery.
3. **C — agent verification:** local/mock first, browser/emulator setup and
   recovery, then required final-candidate acceptance and an installable build.
4. **D — user's phone test last:** real speech/screens, notifications/results,
   follow-ups/cancellation, background/lock behavior and restart/reconnect.

Keep device authorization/privacy and durable task guarantees. Full backup is
optional for disposable development data. Existing M8 caveats and trusted command
mode remain explicit. Personal readiness now verifies the private transport and accepts an optional
password and backup. Final-candidate verification passed: 15 required assertions, four accepted M8
exceptions, 679 backend / 192 frontend / 30 Android checks and final admission
3676.2937 ms. See the [completion report](personal-setup-2026-10-09.md).

**Exit:** A–C pass, required physical checks pass or have specific recorded user
acceptance, and the user confirms normal use for the selected host/phone. Mark
**M11 personal use accepted** separately from the historical M10 baseline.

## Migration and release rules

1. Keep schemas, consumers, fixtures and documentation coherent. Development data may be migrated or rebuilt under AGENTS.md; do not retain legacy readers or backfills solely for disposable data. Continue testing durable identities/recovery on representative fixtures; never invent unavailable history.
2. Stop and confirm old writers before changing ownership or resetting scoped development data. Never have legacy and supervised launchers claim the same task. Reconcile retained interrupted work under the current policy.
3. Enable independently scoped flags for the Codex executor, background decisions and workflow versions. Code-only history/attention stays available independently of model flags.
4. Operational disablement stops new claims/decisions and confirms writer stop while keeping durable history/results available. A development-data reset is a separate scoped operation, not a substitute for proving disablement. Never route Codex-only checkpoints to an incompatible executor. Revisit production data preservation before M11.

## Implementation defaults and limits

These are initial configurable operational values, not new product restrictions or promises of measured performance:

- Existing worker heartbeat every five seconds; stale fallback at twenty seconds. Observe actual exits immediately. Cancellation never waits for that fallback.
- Local feedback wake on committed events, plus a one-second missed-wakeup/restart scan; consume up to 100 events per short batch. Scanning never calls a model merely because time passed.
- Provider connection timeout ten seconds and no-progress transport timeout 120 seconds. Streaming activity resets the no-progress timer. Long-running tasks have no overall deadline. A lost stream creates an uncertain native turn until reconciled; it does not authorize starting a duplicate turn.
- At most two adapter retries for safe transient reads/connects, with short jittered backoff and server retry guidance. Classify quota/billing separately. Never automatically retry capture/upload or unknown side effects.
- Retrieval uses native hosted web tools. Source errors are observations; the worker can select alternatives. Publication retains native event provenance and normal URLs without claiming independent complete downloads.
- Background working context initially targets 12,000 input tokens plus retrieved evidence as needed. Preserve current instructions, question and action preconditions; retrieve additional history instead of silently dropping required facts. This is context shaping, not a hard task thinking budget.
- One background decision invocation at a time. There is no worker-task timeout, mandatory planner/grader call or fixed total tool-round count.

## Next action

M11A-C from the [personal setup plan](../10-personal-setup-and-phone-acceptance.md) are complete. The remaining action is the user's M11D phone test and normal-use confirmation. Reuse the Windows/Tailscale setup, make a password optional and remove mandatory full-backup setup for disposable development data. Keep trusted execution and M8 caveats explicit. A-C are complete; follow the ready [phone checklist](../phone-test-checklist.md) for D. M10 c8a0aec7 remains historical baseline evidence. Current executable changes need candidate-matched verification, starting offline and conserving paid tests.

## Admission/execution closure revision — 2026-10-04

The approved implementation revision removes app cutoff-proof settings and gates; actual
provider authentication, quota and billing failures remain durable. Costs may remain unknown.
M6 uses ordinary inherited-permission task directories, command-local scratch and an explicit
native/trusted setup choice. Elevated native Windows execution passed representative Python
and Node workloads and supervised fail/repair/pass/restart coding on this host; final
candidate-bound acceptance is still required.

M7/M8 freeze plural invocation/latest screen context, proactively request current capture at
first delegation, dispatch completed validated streaming tool calls and bypass transcript
settling only when completion/timeline evidence safely establishes the utterance boundary.
Managed Responses delegation is the sole routing path after the matched comparison
and the user's 2026-10-06 decision. Remove client selection and its configuration;
retain turn ownership, gateway validation and durable results. Require at least
20 managed ready admissions and five independent cold starts. If managed remains
above four seconds after a focused correction, report stage/provider delays and
keep the performance gate open rather than inventing another router.

M10 includes all existing backend/frontend/Android/browser/provider/recovery assertions,
a separate capture-performance stage, workflow-inclusive source identity and shared declared
test configurations. No historical pass or documentation update completes this gate. M11
physical-device acceptance and normal-use rollout remain deferred. The only account-dependent
step is a one-time supported login to the persistent dedicated subscription profile; it is
reused and refreshed across tasks and restarts.


Native acceptance subsequently exposed repeated elevation prompts for isolated directories.
The bounded native remedy therefore did not meet operational requirements. Current local
setup explicitly selects trusted brokered commands with unelevated model hosts. This is the
agreed weaker boundary: commands inherit user access; durable broker/lease/process contracts
are unchanged. Final acceptance records this mode and must not claim native confinement.

The final integration repair also delivers durable run input and clarification answers
to resumed native threads and declares isolated process-tree evidence configuration.
The current frozen run and all remaining measured failures are tracked in the progress
ledger; no provider retry or report-only documentation update changes the exit criteria.

On 2026-10-05, current-candidate voice acceptance hit an external API blocker:
`credit_balance_exhausted`. Restore the configured API organization's credits before
repeating live voice/capture/API tests. Saved subscription login already works; the
The previous four-second p95 requirement is superseded by the user-approved final sample below 4.5 seconds. Functional/capture acceptance remains open independently.

## M6/M7 current-candidate closure — 2026-10-06

[M6/M7 acceptance passed](m6-m7-closure-2026-10-06.md) after 633 required deterministic backend checks, real coding failure/repair/restart, fresh Windows descendant-stop proof and one actual managed voice trial. The final admission took 4167.892 ms, passing the user-approved single final latency assertion below 4500 ms. M8 functional/cold/capture, M9 current subscription/API handoff, M10 combined acceptance and deferred M11 remain separate.
