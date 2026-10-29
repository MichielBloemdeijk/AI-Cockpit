# Onboarding, guidance and verification audit

Audit date: 2026-10-29. Source: `codex/jarvis-voice-poc` at `0ab419d74d1595cfc9f3f803e9c069ae8d077f9a`.

## Method and limits

This review followed the repository entry documents, then attempted five concrete
maintenance-navigation exercises. It assessed whether a reader can identify the
behavior owner, cross-boundary contract and relevant verification without knowing
the implementation beforehand. No feature was implemented during these exercises.

The reviewer had participated in the earlier audit-planning exploration. This is
therefore a fresh-reader simulation with recorded false leads, not an independent
blind onboarding experiment. No onboarding time measurements were taken. A truly
fresh agent should repeat these exercises after refactoring.

The starting checkout was clean and its HEAD matched the source above. Other
auditors subsequently created audit/build artifacts; these are outside this
review's source evidence. This reviewer did not start application services, change
personal configuration, read credentials, reset data, run provider tests, or run
the regression suites. The coordinating auditor owns baseline-suite results.

Coverage includes detailed reading of root/frontend/Android onboarding guidance,
agent rules, environment/dependency/test configuration, launchers and setup
helpers; current architecture/task/self-test/phone/audio references; targeted
source and test reads for the five exercises; and structural/navigation review
of historical report and script entrypoints. Historical acceptance prose and every
individual test body were not exhaustively semantically reviewed here. Third-party
model/runtime binaries were not reviewed as application source.

A read-only relative-link check over the 85 tracked Markdown files at the source
revision checked 349 relative file links and found no missing file targets.
Anchors, external URLs and Markdown rendering were not checked. The important
documentation weakness is contradictory meaning, rather than missing files.

## Overall assessment

The repository already contains useful explanations, focused behavioral tests and
clear domain vocabulary. A determined reader can reach the right code. However,
several entry documents present different generations of the product as current,
and routine verification knowledge is spread across milestone contracts, CLI
source and personal-host maintenance notes. This increases the amount a reader
must verify by searching before trusting the written map.

The needed improvement is a small reliable entry map and accurate local ownership
guidance. More parallel architecture documents, mandatory abstraction layers or
one README per small file would increase this burden.

## Findings

### ONB-01 — P2: Current architecture guidance mixes incompatible generations

`docs/ARCHITECTURE.md:5` explicitly promises the current implementation, and
`:7-16` describes completed native research, subagents and personal acceptance.
Later, `:93-94` describes a separate Responses-discovery/source-fetch pipeline,
`:101` calls M5 in progress, and `:948` says the current durable runtime has no
subagents or MCP integration. Some of that final statement may intend only the
chat-resident runtime, but its wording does not establish that distinction.
`backend/app/services/worker_tool_policy.py:18-19` declares native research and
subagents as default worker groups, while `.env.example:72-73,89-90` explicitly
states that native research replaced the separate discovery/fetch pipeline.

The frontend's own entry map is more directly misleading:
`frontend/README.md:3,26,45` describes a disabled placeholder background surface.
The maintained route at `frontend/app/(app)/background/page.tsx:4` redirects to
`/tasks`; the actual tasks UI, Jarvis workspace and maintained result surface are
absent from that README's main-surface list (`:23-31`).

Consequence: readers can misunderstand which runtime they are changing or infer
that existing functionality is missing. A new agent must reconcile prose before
it can safely use it as context.

Recommendation: make the current architecture reference describe actual named
runtimes and ownership; move superseded implementation status into the existing
historical reports. Refresh the frontend map from maintained routes. Link current
invariants to focused contracts instead of carrying milestone narratives in the
onboarding path. Preserve historical failures and evidence as history.

Validation: compare each current-state claim with source, and repeat the five
navigation exercises below without consulting historical acceptance reports.

### ONB-02 — P2: An executable editor task generates rejected authentication configuration

`.vscode/tasks.json:164-166` offers **Generate Password Hash** using `passlib` and
prints `AUTH_PASSWORD_HASH`. `backend/app/config.py:26-30` rejects that nonempty
setting as obsolete. The current implementation uses the optional private
plaintext setting (`backend/app/services/auth.py:23-28`), and the template
explicitly says there is no hash-generation step (`.env.example:33-38`).

This is not merely an old comment: an onboarding command steers the user toward a
configuration that prevents startup. The task should be removed, without adding
a compatibility alias. `backend/tests/test_auth.py:11-20` is the relevant existing
behavioral contract. Review related wording at `README.md:195-198,377`: saying a
configured password is required in either mode conflicts with the intended blank
personal-mode case unless it explicitly means "if a password is configured."

Validation: the task list contains no removed configuration workflow; personal
mode with a blank password and password mode with a configured password remain
covered by their existing tests. A source search for the retired name should
leave rejection behavior or deliberately historical evidence only.

### ONB-03 — P2: Generic setup and prepared personal-host operation are not clearly separated

The root quickstart copies `.env.example` and installs dependencies
(`README.md:43-91`). That template selects the legacy worker
(`.env.example:59-61`). The next personal section says to double-click `Jarvis.cmd`
(`README.md:174`), but the personal host requires the Codex pool and a prebuilt
frontend (`backend/app/jarvis_tasks/personal_host.py:126-134`). The phone guide
starts at the same prepared-host operation (`docs/jarvis-tasks/phone-test-checklist.md:13-17`),
so it is not a replacement for machine setup.

The retained `scripts/setup-jarvis.ps1:134-135` reports missing llama-server and a
Gemma file as prerequisites and `:188` describes a launcher starting llama-server.
The current launcher is a wrapper around `personal_host` (`scripts/jarvis.ps1:11-17`),
whose child list starts backend and frontend (`personal_host.py:141-143`). The root
personal section correctly says the local LLM is optional (`README.md:181-183`),
while the Jarvis section later calls it necessary for delegated tasks (`:246`).

Consequence: a new checkout following the generic steps does not yet satisfy the
advertised personal-launch command, and a discoverable setup helper assesses the
wrong prerequisites for that mode. This review did not invoke that helper or
attempt to alter the selected host configuration.

Recommendation: retain a clear generic-development path and a short explicit
personal-host setup path with build/configuration prerequisites. Keep the
prepared-owner phone guide clearly labeled as operation of the existing host.
Remove the unused old setup helper, or give it an accurate explicit purpose if
the local-model configuration is still intentionally maintained. Do not silently
enable paid providers, accounts or devices as an onboarding repair.

Validation: an isolated example configuration can be matched to the documented
mode and preflight prerequisites by inspection/local fixtures; the existing
personal-host/setup tests verify startup contracts. No live account enablement
is needed for this documentation/tooling fix.

### ONB-04 — P2: The routine verification recipe does not identify the full checks or their boundaries

The root development commands list backend pytest and frontend test/lint/build
(`README.md:301-308,347-353`). The package has a separate `typecheck` and result
export build (`frontend/package.json:8,16`), and the acceptance contract requires
both builds and frontend typechecking (`docs/jarvis-tasks/07-testing-observability.md:209-215`).
`frontend/tsconfig.json:35` excludes `result-export`, so a normal frontend check
alone cannot be presented as checking that separate application.

The self-test guide calls L0 network-blocked backend/frontend/Android unit checks
(`docs/jarvis-tasks/self-testing.md:18`). Its implementation intentionally selects
14 backend modules and two frontend modules (`backend/app/selftest/__main__.py:29-44`);
it is a useful Jarvis smoke/regression slice, not the full repository baseline.
Skipped layers also preserve a zero exit status (`:47-54`). The guide does not
make those limits explicit or link the broader verification recipe. L0 invokes
`gradlew.bat` unconditionally (`:44`), while the root README advertises manual
macOS/Linux development (`README.md:5,114`).

Paid test selection is also not represented by one consistent marker: compare
the provider marker in `backend/tests/test_m5_result_live.py:7,21` with opt-in-only
subscription and M6 tests (`test_subscription_provider_live.py:22-25` and
`test_m6_coding_live.py:18-23`). The default pytest environment excludes the private
`.env` (`backend/tests/conftest.py:17-19`) but does not itself turn on the external
network block (`:31-40`); the self-test command does (`__main__.py:26-28`). Thus
"pytest," "exclude provider," and "network-blocked suite" are different claims.
No provider request was made to investigate this distinction.

Finally, the contract describes routine CI (`07-testing-observability.md:213-215,277`),
but `git ls-files '.github/*' '*CODEOWNERS*' '*CONTRIBUTING*'` returned no tracked
files. This is a gap between the written verification model and repository-owned
automation, not proof that an external CI system is absent.

Recommendation: publish one short maintained command map that distinguishes
focused checks, full offline regression, both frontend builds, browser/emulator
checks and explicit provider acceptance. Name L0's subset and skipped status
accurately; select the platform's Gradle wrapper if L0 is meant to be portable.
Make paid-test classification and the offline entrypoint consistent. Add only
the smallest useful repeatable local/CI gate; do not build another acceptance
framework or invent a provider spending-limit system.

Validation: the documented commands match the coordinating auditor's baseline
results, all first-party applications are represented, offline runs block
provider networking, and a reader can identify checks without reading milestone
history. Confirm that a deliberately skipped layer is reported as incomplete
where full coverage is claimed.

### ONB-05 — P3: Diagnosis and replay prerequisites are hidden behind source inspection

The self-test guide documents offline and paid scenario runs, but omits the free
`doctor` command registered at `backend/app/selftest/__main__.py:128-140,158-161`.
That command exposes the existing symptom/component/evidence/proposed-fix model
(`backend/app/services/diagnostics.py:1-4,26-31`) before rerunning a paid scenario.
Run diagnosis writes a `diagnosis.json` evidence artifact
(`backend/app/selftest/doctor.py:60-64`); it does not repair application state.

L1 is listed as a replay layer (`docs/jarvis-tasks/self-testing.md:19`), but the
guide does not explain that its real recordings are private, untracked fixtures
(`backend/app/selftest/replay_labels.yaml:1-5`). Missing databases produce
`missing_recording` (`replay.py:88-94`). The bookkeeping reference also says
13 recordings (`docs/jarvis-tasks/conversation-bookkeeping.md:66-67`) despite
additional labeled cohorts in `replay_labels.yaml:46` onward. A fresh clone's
inability to replay those private cases should not be confused with a regression.

Recommendation: extend the existing short self-test guide with free diagnosis,
where logs/evidence live, required replay inputs and how unavailable recordings
are reported. Replace a hand-maintained corpus count with a description or a
derived inventory. Keep private recordings outside Git.

Validation: a reader can select diagnosis, offline fixtures or a specifically
authorized provider seam, and understands missing evidence before spending on a
replay. Use existing diagnostic tests (`backend/tests/test_selftest.py:110`) as
behavioral evidence; do not introduce text-matching tests for this prose.

### ONB-06 — P3: Temporary frontend build conventions leak into shared verification configuration

`frontend/tsconfig.json:32-33` includes a specific `.next-jarvis-agent-test-m2`
generated directory. Current isolation is configurable
(`frontend/next.config.ts:9-11`), self-testing uses `.next-selftest`
(`backend/app/selftest/harness.py:143,236-239`), and app verification uses unique
`.next-verify-*` directories (`backend/app/services/app_verifier.py:45-60`).
The ignore conventions differ: `.gitignore:41-43` names smoke/self-test outputs,
whereas ESLint ignores smoke but not self-test output
(`frontend/eslint.config.mjs:9-15`). This exposes a reader to unexplained
historical directory names and makes the intended source/build boundary harder
to understand. Actual lint/typecheck failures, if any, belong to the separate
baseline evidence; this source review does not claim a reproduced failure.

Recommendation: use one documented temporary-output convention and isolated
tsconfig generation for harnesses. Remove historical generated-directory entries
from the shared source config and consistently exclude generated/vendor output
from source linting. Continue checking first-party tests and both maintained apps.

Validation: full checks work from a clean checkout and after a harness has left
its normal artifacts; temporary outputs do not enter the source inventory.

## Navigation exercises

### 1. Add a maintained result view

Entry: root README architecture links, then the result-platform contract
(`docs/jarvis-tasks/05-result-pages.md:203-215`). The contract clearly describes
trusted backend registrations and matching frontend renderer identities.

Located owners: `backend/app/services/task_result_types.py:58,104-115` owns schema
registration/validation; `frontend/app/results/result-renderers.tsx:15,32,51-54`
owns rendering; `frontend/lib/api.ts:705-743` contains the client content types;
`backend/app/services/task_results.py:12` consumes registration for publication.
Tests: renderer safety (`result-renderers.test.tsx:9`), schema/attachment contract
(`backend/tests/test_document_contract.py:16,28`) and result publication/platform
tests. A display-only change need not change task lifecycle or worker scheduling.

False leads: the frontend README has no result surface, and guessed generic
`result_types.py`/`result_views.py` names do not exist. Search for registration and
the result kind resolved ownership. Those guessed filenames were reviewer
assumptions, not broken documented links. An exact extension breadcrumb would
remove this search without creating a framework around two views.

### 2. Change task retry presentation

Located display owner: `frontend/app/(app)/tasks/[taskId]/page.tsx:14,82-106,168-170`.
It owns retry visibility, resource-change explanation and the control submission.
Shared status labels and trace projection live in `frontend/lib/task-status.ts:4-23`
and `task-trace.ts:9-57`. The control path is
`frontend/lib/api.ts:1188` -> `backend/app/api/tasks.py:426-427` ->
`backend/app/services/task_service.py:714,806-835`.
Tests: the task page's resource-change interaction (`page.test.tsx:86-105`),
trace projection (`frontend/lib/task-trace.test.ts:36-55`) and lifecycle/retry
behavior (`backend/tests/test_task_service.py:121-135`).

False lead: a name search finds the convenience `/retry` endpoint at
`backend/app/api/tasks.py:456-459`; the current page uses the shared `/control`
path. Changing presentation alone should not edit lifecycle policy. The page
test and explicit contract are useful anchors; the general README is not enough
to identify this distinction.

### 3. Change native Stop behavior

Best onboarding example: `docs/jarvis-tasks/11-android-audio-controls.md:3-33`
states the service owner, microphone release before cleanup, idle selection and
stale-stop fencing in a short document. It maps directly to
`AudioControlState.kt:35-64` (pure policy), `JarvisAudioService.kt:67-71,180-198`
(side effects), and `JarvisNotifications.kt:54-67` (immutable action identity),
under `android/app/src/main/java/nl/sander/aicockpit/jarvis/`.
The corresponding JVM test is
`android/app/src/test/java/nl/sander/aicockpit/jarvis/audio/AudioControlStateTest.kt:30-42`;
the feature document provides the model-free real-audio/notification harness
(`11-android-audio-controls.md:43-57`).

False lead avoided: stopping the assistant window or WebView is not stopping
the native audio owner; the document says this explicitly. No extra layering is
needed to explain this boundary. This example should guide other focused docs.

### 4. Diagnose a communication/replay failure

Entry: `docs/jarvis-tasks/conversation-bookkeeping.md:11-18,46-80` establishes the
observer as communication authority and separates playback evidence from model
judgment. The self-test guide then locates scenarios and isolated artifacts
(`self-testing.md:24-47`). Source inspection was required to discover `doctor`
and the distinction between diagnosis, paid review and paid replay
(`backend/app/selftest/__main__.py:128-167`).
Relevant files: `conversation_observer.py`, `assistant_notes.py`,
`live_task_updates.py`, `selftest/doctor.py`, `selftest/replay.py` and its labels.
The diagnostic output itself gives these ownership breadcrumbs
(`selftest/doctor.py:41-55`). Existing tests include observer, notes and self-test
modules. False leads: the guide's simple layer list does not surface the free
diagnostic first step or missing private replay inputs; see ONB-05.

### 5. Change settings or reviewed knowledge

The frontend entry map correctly points to Settings and Knowledge, but Settings
contains several domains. The page imports both chat and Jarvis preference APIs
(`frontend/app/(app)/settings/page.tsx:6`). Chat defaults use
`frontend/lib/api.ts:1378-1394` -> `backend/app/api/chat.py:121-130` ->
`backend/app/services/chat_settings.py:62-105`; Jarvis preferences use
`frontend/lib/api.ts:886-901` -> `backend/app/api/jarvis.py:179-205` ->
`backend/app/services/jarvis_preferences.py:23-65`. Future-chat behavior has a
clear targeted test (`backend/tests/test_phase2_chat_settings.py:27,98`).

Knowledge's route/client/API names are easy to follow:
`frontend/app/(app)/knowledge/page.tsx:8-17` -> `frontend/lib/api.ts:1644-1676` ->
`backend/app/api/knowledge.py:20-75` -> `knowledge_memory.py:63-111` and the
review persistence facade. `backend/tests/test_knowledge_review_api.py:11-55`
checks the queue/approve/reject/files flow. The actual public route `/wiki`
re-exports the Knowledge page (`frontend/app/(app)/wiki/page.tsx:1`), and
`frontend/next.config.ts:21-25` redirects `/knowledge` there.

False leads: there is no generic backend `api/settings.py` or `settings_store.py`;
following the client is more reliable than guessing. The named
`memory_review_store.py:6-26` forwards directly to `conversation_store`, adding
one navigation hop without owning persistence. That facade should be evaluated
with the architectural audit rather than expanded to create artificial layers.

## Positive patterns to preserve

- Root AGENTS policy provides clear development-data, scope and provider-test
  rules (`AGENTS.md:5-24,27-50`); the Cursor policy explicitly points back to it
  (`.cursor/rules/development-stage-priorities.mdc:7-18`).
- Test isolation is concrete: personal `.env` exclusion, observer inference
  substitution and per-test database/workspace paths
  (`backend/tests/conftest.py:17-19,43-52,120-139`). Backend collection is scoped
  to tests, and parallel/debug controls are documented inline
  (`backend/pyproject.toml:65-74`). These are useful contracts, not test clutter.
- Frontend strict typechecking is enabled (`frontend/tsconfig.json:7`), and
  component/utility tests sit beside their source. Result rendering tests check
  hostile content rather than merely echoing implementation.
- The Android Stop boundary combines a pure state machine, a real side-effect
  owner, focused JVM checks and an agent-runnable model-free emulator harness.
- Completion history is deliberately retained with candidate scope and accepted
  caveats (`docs/jarvis-tasks/reports/README.md:5-15,66-71` and
  `00-implementation-roadmap.md:63-75`). Keep this evidence while removing
  obsolete instructions from the current learning path.

## Suggested acceptance after refactoring

- A genuinely fresh reviewer can complete the five owner/contract/test
  navigation exercises from maintained entry docs, recording any unresolved
  question or misleading path; do not impose invented timing targets.
- One short entry map distinguishes the chat runtime, durable workers, live
  conversation, maintained results and Android owner without requiring milestone
  history. Focused contracts explain the durable boundaries when needed.
- A newcomer can choose focused checks versus the complete offline baseline and
  identify which browser/emulator/provider evidence remains unavailable.
- Run the meaningful existing behavioral checks for any code/tooling changes.
  For documentation-only changes, verify references/commands against source and
  reuse valid same-candidate evidence. Do not add prose snapshot tests.
- Keep historical reports and private recordings appropriately separate from
  current instructions. No new compatibility layer, live provider run, account
  enablement or physical-device test is implied by these onboarding repairs.
