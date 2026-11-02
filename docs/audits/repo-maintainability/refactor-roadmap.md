# Target architecture and ordered refactors

Baseline `0ab419d`, finalized 2026-10-29 after the systematic batches 1–7. This roadmap supersedes the first-pass version. The audit changed no application code or data. Feature removals still need an explicit value/cost decision (listed under *Decisions* below); nothing is obsolete just because it is called legacy.

## Target direction

The target is unchanged from the first pass, and the systematic batches confirmed it. Keep one application with feature-oriented ownership, not a new framework or service deployment. Routes and provider adapters translate inputs. Application operations own transitions and transactions. Pure policies express deterministic decisions. Persistence stores durable facts. Process entrypoints compose dependencies and enforce configuration preconditions.

The batches made three ownership gaps concrete. Each has one named owner to create:

1. **Task lifecycle** (T01, T02, T03, B01, B04, B07): one module owns every task/run/attempt transition and its vocabulary, always under the task lock. `task_service`, `worker_coordinator`, the reminder scheduler and the API become callers that supply evidence.
2. **Conversation run lifecycle** (B02, I03): one owner for start, guarded completion/failure/cancellation and the matching events, used by agent, tool, chat and council runs.
3. **Startup preconditions** (S01, E01, P02): one check that both launch paths run: secret strength, access mode versus bind address, executor/pool pairing.

Elsewhere the direction stays as before: narrow execution/native-runtime contracts (B05, B07), reusable web feature screens (C02, C07), typed result and control contracts shared by backend and clients (C05, C06, C14), and acceptance code behind debug seams (C04).

Do not extract every query into a repository, require an interface per class, generate all cross-language models, or split files to meet size targets. Each extraction must remove a concrete reason for unrelated code to change together.

## Findings register

50 entries across the subsystem reports (O01 was added after the owner review). Some overlap by theme; they are not 49 independent bugs. Evidence: **R** = reproduced by a saved isolated probe; **S** = confirmed in source (and library bytecode for A01) but not run; **D** = design/maintainability assessment.

| ID | Priority | Evidence | Summary | Phase |
|---|---|---|---|---|
| S01 | P3 (documented; decision 5) | R | Default dev config: any network peer gets an owner session and code execution | 0 (optional) |
| I04 | closed by decision 1 | R | Legacy agent's Python/shell confinement is advisory but reported as a sandbox | 2 (removal) |
| C11 | P1 | R | Browser Stop keeps mic and WebRTC open until an unbounded DELETE returns | 0 |
| O01 | P2 | D (mechanical) | Flat `services/` (105 modules) and `frontend/lib` (43) folders; ownership only in filename prefixes | 4–5 (incremental) |
| B01 | P2 | R | Policy revocation sends unsupported `pause` | 1 |
| B02 | P2 | R | Failure after run claim leaves it `running` with no owner or log | 1 |
| B03 | P2 | R | Partial API startup skips cleanup of started components | 1 |
| T03 | P2 | R | Reminder scheduler can overwrite a concurrent cancel | 1 |
| C01 | P2 | R (+S stream path) | Older conversation load/stream overwrites the selected conversation | 1 |
| C10 | P2 | R | Superseded wake failure overwrites the new `ready` state | 1 |
| C14 | P2 | R | Timeline feed lost council answers, usage, task links, branch locks | 1 |
| P01 | P2 | R | Eight ORM foreign keys absent from the DB; no drift test | 1 (test), 3 (squash) |
| X01 | P2 | R (+S collision) | Memory withdrawal leaves knowledge published; note filenames collide | 1 |
| A01 | P2 | S | Phone event stream times out before each heartbeat and reconnects with backoff | 1 |
| C12 | P3 | R | Partial ONNX init leaks loaded sessions | 1 |
| E01 | P2 | D | Two launch paths; safety checks only in one | 0, 3 |
| E02 | P2 | D | Verification spread over 2 CLIs and ~20 scripts with milestone names | 3 |
| A03 | P2 | S | Android harnesses inherit the personal `.env` | 3 |
| X02 | P2 | S | Lint/typecheck scope crosses generated acceptance artifacts | 3 |
| ONB-01–06 | P2–P3 | S/D | Contributor docs, setup commands and verification guidance | 3 |
| P02 | P3 | S | Path settings resolved three ways; secret check only in launchers | 0 (secret), 3 |
| E03 | P3 | D (mechanical) | No Python lint/type check; 21 routes without a client | 3 (tooling), 6 (routes) |
| B04 | P2 | D | Hidden projections and private cross-service transaction helpers | 4 |
| T02 | P2 | D | Control/attempt transitions duplicated across modules | 4 |
| I03 | P2 | S | Non-agent run lifecycles use unguarded writes across separate commits | 4 |
| B05 | P2 | D | Native runtime facade split across production and probe CLI | 4 |
| B06 | P2 | D | Live event transitions encoded in flags inside one 542-line handler | 4 |
| I01 | closed by decision 1 | D | Legacy runner split over five files sharing ~50 private names | 2 (removal) |
| I02 | closed by decision 1 | D | Agent run state is an untyped dict | 2 (removal) |
| T01 | P3 | D | Status vocabularies are bare strings across state machines | 4 |
| B07 | P3 | D | Runtime interface typed as one concrete implementation | 4 |
| I06 | P3 | D | `llm.py` mixes provider policy, caching, transport, parsing | 4 |
| C02 | P2 | D | Jarvis route owns protocol and lifecycle orchestration | 5 |
| C03 | P2 | D | Native audio ownership exposed through a writable global | 5 |
| C04 | P2 | D | Acceptance code compiled into production audio/capture paths | 5 |
| C05 | P2 | D | Result renderer registry is a partial extension point | 5 |
| C06 | P2 | S | Task controls inferred from English summary text | 5 |
| C07 | P2 | D | Static companion imports route pages | 5 |
| C09 | P2 | S | Request lifetime and error semantics differ per endpoint | 5 |
| A02 | P3 | D | Assistant session encodes four modes in mutable flags | 5 |
| C08 | P2 | S | Unused wake gates and legacy inference mode (test-only callers) | 6 |
| C13 | P2 | S | Old wake handoff protocol still runs beside the current one | 6 |
| I05 | P3 | S | Pre-timeline reconstruction and legacy app paths | 6 (after C14) |
| B08 | P3 | S | Rollout aliases and compatibility tombstones | 6 |
| P03 | P3 | S | Small persistence leftovers | 6 |

## Decisions (owner answers, 2026-10-29)

1. **Legacy executor and Chat-page agent: retire.** Usage evidence: the private config runs `TASK_EXECUTOR=codex`, which `personal_host` requires. The dev DB has no standard Chat conversations (only two Jarvis daily logs) and no chat-built generated apps (`generated_apps` = 0; the two `cockpit_apps` are result collections and stay). Retire together: the legacy task runtime in `agent_execution`; the chat agent loop (`agent_runner`, `agent_run_support`, `agent_prompt_builder`, `agent_runner_helpers`, `agent_metadata`, `agent_model_verifier`); the model-visible file/shell/python/app tools and `POST /api/conversations/{id}/tools`; and the chat-built generated-app path (`app_builder`, the `app_initialize`/`app_verify` tools and app-root syncing in `app_registry`). Keep conversations, branching, memory extraction, the knowledge wiki and the result platform. Single-model chat becomes a plain streaming completion. I04, I01 and I02 close with the removal, I03 shrinks to the chat/council paths, and `.env.example` switches to the Codex executor. The one reason to keep the legacy executor (offline tasks on the local llama.cpp model) does not apply to current use. Before removal, confirm the `/workspace/apps` page needs only `cockpit_apps`.
   **Keep (owner, 2026-10-29):** the Recipes app (`cockpit_apps` collection `recipes`, `/collections/recipes`, `recipe.v1` result type) and the `recipe_from_screen@1` workflow. Individual recipe/document entries may be reset. The recipes path depends only on the shared declarations in `agent_execution` (context/result/sink types), which stay; the legacy loop class in that module goes. The P01 squash must keep seeding the `documents` and `recipes` collection rows that migration 0031 creates today. Also removed with the agent loop: `jarvis_benchmark` and `scripts/benchmark-jarvis.py`, which benchmark local models against it.
2. **Council mode: keep, update models.** Fix C14 for council answers and usage. Update `AVAILABLE_MODELS`/`COUNCIL_MODELS`/`SYNTHESIZER_MODEL` in `config.py`, `.env.example` and the private `.env`; all three currently name `claude-sonnet-5`, `gpt-5.6-*`, `gemini-3.5-flash` and `grok-4.5`. Look up current IDs from the OpenRouter catalog at implementation time rather than from memory. Remove the unused `llm.council_completion` (I06).
3. **Unused routes: remove unless future use is planned.** Proposed removal: memory proposals (2), conversation-scoped memory approve/reject (2), `app-catalog` storage-capability duplicates (2), `tasks/{id}/retry` and `tasks/{id}/events`, `retrieval/history` and `/rebuild` (the voice brain calls `history_retrieval` directly), reminder REST CRUD and `jarvis/tasks/resolve` (6), `PUT /api/worker-tools/permissions` (the UI uses `PUT /api/worker-tools`), `orchestration/{id}/inspect` and `/resume`, and `DELETE notifications/subscriptions/{id}`. Keep-if-used: `GET /api/jarvis/diagnostics` (the doctor's findings over HTTP) and `GET /api/jarvis/live/observer` (observer health), which are browser-debugging endpoints.
4. **Dev DB reset: any time.** Do the P01 squash and reset at the start of phase 3, before phase-4 schema changes.
5. **Security is secondary (personal, Tailscale-only).** Rule: fix a security item only if it is severe or very easy; otherwise record it as documentation. Applied:
   - **S01:** downgraded to documentation plus an optional small fix. Binding dev to loopback would break dev access over the Tailscale IP. The low-cost option is to accept the empty-password login only from loopback and Tailscale (100.64.0.0/10) client addresses, about five lines in `api/auth.py`.
   - **I04:** closes with decision 1.
   - **C11:** stays a phase-0 fix. It is a correctness bug (the microphone stays live after Stop), not only a security item.
   - **Placeholder session secret (P02), LAN-wide CORS, harness `.env` inheritance (A03, kept for its isolation/reproducibility value):** documentation only.

## Implementation status

| Step | Status | Commit |
|---|---|---|
| Phase 0: C11 browser Stop ordering | Done, regression test added | `d4438b1` |
| Phase 0: S01 optional loopback/Tailscale login restriction | Not done (documented only, per decision 5) | — |
| Phase 2: retire legacy executor and Chat agent (closes I01, I02, I04; supervisor now logs run failures, part of B02) | Done | `bd68c27` |
| Phase 2: remove unused routes (17) | Done; kept `/api/jarvis/diagnostics` and `/api/jarvis/live/observer` | `354e8cf` |
| Phase 2: council/picker models; dead LLM helpers (part of I06) | Done | `9d7e692` |
| Generated apps → app platform, steps 1–6 (see below; closes C05, removes the old generated-app subsystem and part of I05) | Done | `e597ac8` … `fa7066f` |
| Phase 3 (X02, part): frontend lint scope excludes build outputs of test and audit runs; the source lint errors fixed | Done | `f089bdb` |
| App platform gaps: app workflows from functions/UI, `write_app_data`, Recipes-from-README acceptance, Android scripts on app records (model-free emulator runs pass) | Done | `8abb796` … |
| Phase 1: B01, B02 (rest), B03, T03, C01, C10, C12, C14, X01, A01, P01 drift test | Done, one regression test per finding (see below) | `d615719` |
| Phase 3: docs/setup (ONB, E01, E02), A03, E03 static checking, P02, P01 squash | Done (see below); the type checker waits for the phase-4 seams | `134a896` |
| Phase 4: task lifecycle, conversation runs, execution contracts, live transitions, provider transport | Not started | — |
| Phase 5: client feature ownership (C02, C03, C04, C06, C07, C09, A02) | Not started | — |
| Phase 6: remaining leftovers (C08, C13, rest of I05, B08, P03, E02 scripts) | Not started | — |
| Final assessment with a fresh-context agent | Not run | — |

Generated apps (owner decision, 2026-10-30): app creation stays part of Jarvis, rebuilt as an installable app platform; see [13-app-platform.md](../../jarvis-tasks/13-app-platform.md). **Done (2026-11-01):** all six platform steps are implemented, and the old generated-app subsystem (registry, `/api/apps`, `/api/app-catalog`, the storage SDK, the generated-app tables and the `generated_app` task target) is removed. Results are records in installed apps, so the result renderer registry (C05) is gone; the static companion still imports the task route page (C07 remains for that). Tool-event presenters in `agent-event-presenter.ts`/`conversation_timeline.py` and the `live_item` read path now only render historical events; they go with the C14/I05 work.

## Phase 0 — Small immediate fixes

- **C11:** in `jarvis-live.ts` `dispose`, stop tracks and close the peer/data channel before the backend close, and bound the close request. Acceptance: the C11 probe shows tracks stopped while the close is pending.
- **S01 (optional, per decision 5):** accept the empty-password login only from loopback/Tailscale client addresses. Otherwise S01 stays documented.

I04 needs no mitigation step: decision 1 removes the code.

## Phase 1 — Repair demonstrated defects, one focused change each

B01, B02, B03, T03, C01 (loads and stream callbacks), C10, C12, C14, X01 (withdrawal plus ID-based note names), A01, and a P01 drift test (`compare_metadata` on a fresh DB) that is expected to fail until the squash. Each change turns the saved probe's scenario into a real regression test of the desired behavior. Do not keep defect-asserting probes as gates. None of these depend on the structural phases. Where a fix would naturally land in a phase-4 module (T03 in the task lifecycle owner), make the minimal locked fix now and move it later.

**Done (2026-11-02).** Each test below fails on the previous code where the defect was reproducible in-process.

- **B01:** revocation sends an immediate `suspend` (`suspend_for_policy_revocation`); `test_policy_revocation_during_a_turn_stops_the_writer_and_pauses` drives a worker through it to `paused`.
- **B02:** chat, council and turn preparation share one run owner (`_owned_run` in `chat_orchestrator`), so any failure or cancellation after the run starts ends it. The supervisor already logged failures (phase 2).
- **B03:** the API lifespan registers each release on an `AsyncExitStack` as it acquires it; `test_api_lifespan.py` covers a failing start and a failing stop.
- **T03:** the reminder scheduler takes `task_service._lock_task` before reading (minimal fix; moves into the phase-4 lifecycle owner). `test_reminder_scheduler_race.py` commits a cancel from a second SQLite connection inside the read-to-write window, like the probe, and asserts it now waits for the lock.
- **X01:** `memory_review_store` owns approve/reject/delete and the knowledge file; withdrawing an approved item unpublishes it, and a failed approval commit removes the fresh file. Notes are named `{kind}-{slug}-{id}.md`, and listings take the title from the front matter. The memory PATCH route and conversation-scoped approve/reject were already removed in phase 2.
- **P01:** `test_schema_drift.py` (metadata comparison and orphan insert) was `xfail(strict=True)` until the phase-3 squash, which removed the markers.
- **C01:** `useChat` owns a selection ref; loads carry a generation and commit only while newest and still selected, and stream callbacks write only while the view shows the conversation and branch they were sent from (a branch send no longer jumps to `main`). Task-page polling, the second case under C01, is not changed.
- **C10, C12:** the wake runtime re-checks its operation after cleanup; ONNX creation releases loaded sessions when one fails.
- **C14:** committed assistant timeline items carry `usage` and, for council answers, `council` (the model responses), written by the server and mapped by the feed. Task links and branch locks need no timeline field today: no writer gives timeline conversations a source task (`task_delegation` has no caller), and the mutating chat tools behind branch locks were removed in phase 2. The legacy reconstruction stays until phase 6 (I05).
- **A01:** one shared `OkHttpClient` for all `CockpitApi` instances; the live stream uses a derived client with a 45 s read timeout (three heartbeats), and backoff resets on open. JVM-tested against a local SSE server; not run on a device or emulator.

## Phase 2 — Apply decisions 1–3

Do this before restructuring code that would otherwise be refactored and then deleted.

- Retire the legacy executor, the chat agent loop, its tools, the tool route and the chat-built generated-app path (decision 1), with dependent tests, fixtures and docs, in one change. Check the other importers of the removed modules first: `jarvis_benchmark` and `scripts/benchmark-jarvis.py` use `chat_completion_stream_response` and agent-prompt helpers. Single-model chat streams a plain completion; council keeps its orchestrator path.
- Update council model lists in code, `.env.example` and the private `.env` (decision 2).
- Remove the unused routes listed in decision 3 with their tests.
- Re-run `route-usage-scan.py` and `private-import-scan.py`; the agent-runner private-name edges should be gone.

Acceptance: backend and web suites pass. A council conversation streams and reloads. Single chat streams. No remaining import of the removed modules.

## Phase 3 — Trustworthy contributor entrypoints and tooling

- Docs and setup (ONB-01–06, E01): one current architecture and reading map, one verification index (E02) giving command, scope, cost (free/paid/emulator) and when to run it. Label historical plans as history. Remove the password-hash task and the alias launchers.
- Verification hygiene: lint/typecheck scope independent of acceptance artifacts (X02). Fix the three lint errors. Android harnesses start backends through one support module with `AI_COCKPIT_ENV_FILE` (A03). The selftest recorder uses an explicit observer hook instead of patching private methods (E02).
- Static checking (E03): ruff for `backend/app`, and mypy or pyright on the seams phase 4 creates.
- Config (P02): one path resolver; delete `ntfy_topic`.
- Schema (P01): squash to a baseline generated from the corrected ORM with consistent index names, reset the dev DB, drop `attention_migration_state` and its runtime query, and keep the migration-lock/partial-DDL resume tests for future migrations.

Acceptance: setup runs from a clean checkout with isolated config; lint completes on a tree with acceptance outputs present; the drift test is empty; a harness run with a poisoned personal `.env` gives the same result.

**Done (2026-11-02).** E01 decision (owner, 2026-11-02): the shared startup check enforces only what does not break the dev path; secret strength and loopback binding stay personal-host checks, per decision 5.

- **P01:** the 43 migrations are one baseline (`20261102_0001`) generated from the ORM, with the FTS5 recall tables, their triggers and the live-event triggers as SQL, WAL, and the preferences row. Two raw indexes moved into the ORM. `attention_migration_state` and its runtime query are gone. The drift tests pass without markers (FTS tables excluded through `is_orm_schema_name`, also used by Alembic autogenerate). Old-revision replay tests were removed; the migration-lock, parallel-startup and logging tests stay, plus a test that the baseline feeds recall search and the live-event log. The dev DB was reset to the baseline (Jarvis stopped first; phone pairing and history were in it).
- **E01:** `Settings.check_startup_preconditions` (password mode needs `AUTH_PASSWORD`) runs on every launch path; the personal host keeps its secret, pool, Tailscale and ownership checks. Alias launchers, `scripts/start-worker.ps1` and the VS Code worker task (the API supervises the pool) and the password-hash task are removed; stale `TASK_EXECUTOR` entries are gone.
- **P02:** one resolver, `Settings.backend_path`, behind a property for every path setting; consumers that joined `backend_root` by hand or resolved against the cwd (orchestration context and profile, context blobs, subscription profile, live diagnostics) use the properties. `ntfy_topic` and the stale persistence comment are deleted. The placeholder secret stays documentation only (decision 5).
- **X02, ONB-06:** one temporary-output convention (`.next-<purpose>`) ignored by Git, lint and typecheck; lint completes in about 14 s with all old acceptance outputs present. The three lint errors were fixed in `f089bdb`.
- **A03:** `android/scripts/harness_support.py` starts every harness backend with `AI_COCKPIT_ENV_FILE` set to the empty `harness.env`; paid harnesses name the one credential they use (`personal_credential`) and set the deployed command mode explicitly. `test_android_harness_support.py` poisons the env file and checks a started process. The harnesses still import each other for shared emulator helpers; no emulator run was made.
- **E02:** the self-test recorder uses an explicit `LiveTrafficObserver` on the adapter and coordinator instead of patching privates (only the fixture screen is still patched). Gates are named by what they verify (`coding`, `voice`, `android`, `subscription`, `release`), and milestone-numbered test files, fixture names and opt-in flags were renamed. `docs/VERIFICATION.md` is the index. Root experiment summarizers stay for phase 6.
- **E03:** ruff (correctness rules) for `backend/app`, clean. mypy/pyright is deferred to phase 4, whose seams it is meant to cover.
- **ONB-01–05:** a new current `docs/ARCHITECTURE.md` with a reading map (the old one moved to `reports/architecture-before-2026-11.md`); the README separates the development setup from the personal host and its preconditions; the frontend and Android READMEs and `.env.example` match current code; `setup-jarvis.ps1` (wrong prerequisites, unreferenced) is removed with its VAPID step kept as a README command. The self-test guide documents `doctor`, replay inputs and L0's real scope; L0 reports `"complete": false` when a layer is skipped and picks the platform's Gradle wrapper. All billed tests carry the `provider` marker, and every other test is network-blocked by default. The chat UI no longer calls single-model mode "Agent".

## Phase 4 — Backend ownership seams

Order matters: lifecycle first, because B04, T01 and C06 all depend on it. Each new owner becomes a feature package (O01): `services/tasks/` for the lifecycle module and the `task_*` services it absorbs, then `conversations/`, `jarvis/`, `orchestration/`, `results/`. Move modules into a package when its owner is created, not in a separate big-bang move, and keep `frontend/lib` in step (`lib/jarvis/`, `lib/conversations/`, …) during phase 5.

1. **Task lifecycle module** (T02, T01, B07, B04): typed status/action vocabularies per state machine; `apply_control`, `confirm_stop(evidence)`, `create_attempt`, `resume_after_wait`, `complete`/`fail`, each under `_lock_task` and writing task, run, attempt and events together. Callers stop using `task_service` privates. Projections become explicit named steps of these operations rather than a side effect of `append_event`. Keep fence checks, idempotency and atomic result publication unchanged.
2. **Conversation run lifecycle** (I03, B02 class): one owner for all run kinds; delete the unguarded writers and the dead `mark_run_interrupted`.
3. **Execution contracts** (B05, B07, I01, I02): a small production facade for the pinned native runtime used by both runtime and probes; the execution protocol next to its context and sink types; typed run state if the legacy executor stays.
4. **Live coordinator transitions** (B06): named transition outcomes and actions for provider events, extracted into coherent wake and response routines. Keep provider calls outside DB transactions; duplicate/reordered/stale-generation tests anchor each step.
5. **Provider transport** (I06): one retry/error helper, a model-profile table, explicit model parameter.

Acceptance: targeted regressions plus the full offline suite. `private-attribute-scan.py` reports no `task_service` privates used outside it. Status literals are confined to the vocabulary module. The type checker covers the new seams.

## Phase 5 — Client feature ownership

- Web: reusable task/app/login screens with thin Next and static adapters (C07). Jarvis feed/replay owner and voice composition owner separated from the route (C02). Discriminated result model with per-kind render/copy (C05). Task controls driven by the structured control projection from phase 4 (C06). Shared finite-request transport with abort and structured errors (C09).
- Android: read-only audio state plus commands, with the service kept private (C03). Acceptance audio/capture behind a debug-source-set seam with a trivial release implementation (C04). A sealed show-mode for the assistant session (A02).

Acceptance follows the per-finding acceptance in the client review. Offline tests come first, then model-free browser/emulator acceptance for affected seams. Physical-device checks remain deferred and are named as such.

## Phase 6 — Remove verified leftovers

After their replacements are in place: unused wake gates, legacy inference mode and `AgentStatusCard` (C08); the old wake handoff protocol (C13); the pre-timeline reconstruction and most of `agent-event-presenter.ts`, after C14 (I05); app-path remapping (I05); rollout aliases and tombstones (B08); persistence leftovers (P03); unused routes per decision 3 (E03); root experiment scripts and `verify-personal-link.mjs` (E02). Each removal checks callers again at that time and updates tests and docs in the same change.

## Preservation rules (apply in every phase)

Durable task/run/attempt identities and generation fencing; revisioned result publication with replay fingerprints; the after-commit broadcast plus durable-rescan recovery model; `personal_host`'s loopback/Tailscale/ownership checks; native audio's release-before-replace and stop-before-network-close ordering; capture identity/expiry/lock checks; the `VoiceSessionAdapter` boundary; orchestration's fenced `DecisionAuthority`; tracked background tasks; and the durable notification outbox.

## Final assessment (after the refactors)

Repeat the five reader exercises with an agent that has no prior audit context. This was planned for batch 7 but needs a fresh-context agent, so it was not run. Record false leads, contract discovery and test selection, and compare representative change sets before and after. Rerun the mechanical scans in `evidence/` (private access, long functions, route usage, schema drift) as trend measures, not targets.
