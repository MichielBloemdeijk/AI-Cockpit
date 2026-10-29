# Backend maintainability review

Audited candidate: `codex/jarvis-voice-poc` at `0ab419d74d1595cfc9f3f803e9c069ae8d077f9a`, 29 October 2026. This is an evidence-based review of selected backend flows, with structural inspection of the remaining Python source. It is not a claim that every backend line was semantically reviewed. No application code, settings, data, credentials or provider accounts were changed. Useful existing features, including the non-Codex executor and standard conversation UI, are presumed supported unless a separate product decision retires them.

## Findings

### B01 — [P2, behavior defect] Capability revocation calls an unsupported control action

- Evidence: `backend/app/services/codex_task_runtime.py:480-490` checks for a disabled selected capability, revoked command network or removed approved skill, then calls `task_service.control(..., action="pause", requester="worker_policy")`.
- `backend/app/services/task_service.py:64-73` defines the supported actions, including `suspend` but not `pause`; `control` rejects the value at `728-730` before opening a transaction. The intended controlled suspension therefore never commits.
- Consequence: the runtime raises `ValueError`; the worker's generic failure handler (`backend/app/services/task_worker.py:265-273`) can mark an active task failed. This does not establish a permission bypass: the error path still closes the runtime and the supervised process boundary remains. It does establish broken revocation/control semantics.
- Reproduction: the saved probe extracts the real runtime action and executes the real control method through its validation branch. It produces `Unsupported task control action: pause` without touching a DB or provider.
- Recommendation: use the supported suspension action with the appropriate immediate writer-stop semantics, and share the control-action type across service, runtime and public boundaries. Add one fake-runtime test for revocation while a turn is active; verify durable control, writer exit and final paused state rather than only policy-selection helpers.

### B02 — [P2, behavior defect] A failure during conversation-run preparation loses ownership while leaving the run running

- Evidence: `backend/app/services/agent_runner.py:554-558` claims a queued run. Metadata restoration, conversation lookup, context construction and initial persistence then execute at `560-618`; the exception/failure transition starts only with `try` at `619`, ending at `816-819`.
- `backend/app/services/run_supervisor.py:48-57` removes the finished task from its ownership maps and calls `completed.exception()` without using its returned exception. Retrieval succeeds for an ordinary failed task, so the logger is not called and the usual unhandled-task warning is consumed.
- Consequence: an exception after the claim but before the main loop can leave durable status `running` with no supervisor task and no fallback error log. Existing API-startup recovery can later interrupt it (`conversation_store.mark_running_runs_interrupted`); it is not self-recovered while that process stays up. Main-loop failures do have their own logging and failure projection, so this finding is specifically about the uncovered preparation interval.
- Reproduction: AST-extracted `continue_run` plus the actual stdlib-only supervisor, a fake store and failing metadata restoration leave status `running`, zero `_fail_run` calls, zero supervisor error logs, and no remaining supervisor owner.
- Recommendation: put post-claim preparation under the same failure ownership as execution; ensure unexpected completion exceptions are logged by the supervisor. Add a restoration/context failure test asserting one durable failure projection and no stranded run. Preserve queued-claim/cancellation races and terminal-state guards.

### B03 — [P2, behavior defect] Partial API startup bypasses cleanup of already started components

- Evidence: `backend/app/main.py:54-78` initializes persistence and starts telemetry, reminders, coordinator, observer, health and optional orchestration/pool components. Its cleanup `try/finally` begins only at `79-81`.
- Consequence: a later `start()` exception skips this function's `stop()` calls and `close_engine()`, even for previously started components. The normal successful-start shutdown path is explicit, but the acquisition failure path is incomplete.
- Reproduction: inject a fake `conversation_observer.start` failure into the AST-extracted lifespan. Telemetry, reminder scheduler and coordinator have already started; the trace contains no stop call or engine close.
- Recommendation: register cleanup as resources are acquired, for example with `AsyncExitStack`, and keep reverse shutdown order visible. Test failure at multiple startup positions and a failing cleanup so later releases still run. This is a targeted lifecycle fix, not a reason to introduce a dependency-injection framework.

### B04 — [P2, design problem] Persistence and task ownership depend on implicit reverse calls and private transaction helpers

- Evidence: `backend/app/db/repositories/tasks.py:105-108` marks feedback and calls the result projector inside `append_event`; `backend/app/db/session.py:228-232` knows the orchestration singleton and feature switch, while `277-281` stops the result observation queue before disposing the engine. `backend/app/services/result_static.py:63` imports an HTTP-header constant from the results route module.
- Separate domain services reach into the task service's private transaction contract: `task_context.py:34-38`, `task_semantic_compaction.py:61-66,112-116`, `worker_tool_policy.py:103-105`, and `task_context_upload.py:216-219`. Callers must know which helpers assume an existing transaction and acquired task lock.
- Consequence: adding a projection or changing fencing/lifecycle behavior requires following lower-layer callbacks into domain services and knowing private methods outside their apparent owner. The directory names suggest clearer separation than the actual execution graph provides. This is not proof that current transactions are wrong: keeping authoritative results and task events atomic is a good invariant.
- Recommendation: make the transaction-aware task authority/fence operation an explicit supported seam; keep orchestration wake and diagnostic shutdown wiring at composition boundaries. Expose the event/projection transaction boundary by name rather than making a generic repository append secretly own every projection. Move shared HTTP response policy beside the HTTP layer. Preserve atomic result publication, after-commit broadcasts and durable polling fallback during these changes.

### B05 — [P2, design problem] The pinned native runtime facade is split across production code and the probe CLI

- Evidence: production `codex_task_runtime.py:228` imports private `_sandbox_checks` from `app.jarvis_tasks.codex_probe`; `native_worker.py:55` imports `_command_exec`; `task_project_tools.py:89` imports both. `codex_probe.py:179-185` contains the raw command transport and the same module also owns report assertions, credential discovery and live experiment orchestration.
- Low-level SDK access is scattered: `native_worker.py:29-33,42-44,94-105`, `codex_task_runtime.py:356-357`, and `codex_probe.py:182` touch SDK private members. `native_worker.subagent_observations` imports `_json_value` back from the high-level runtime at `94`, reversing the proposed facade's direction.
- Consequence: changing SDK/probe shape requires understanding both runtime and diagnostic machinery; a developer looking for the production protocol boundary cannot learn it from one small module. Runtime version constants also appear in multiple modules. The package pin and explicit fail-closed checks are valuable and should remain.
- Recommendation: place the minimum pinned transport/normalization/native-policy operations in one production facade used by both runtimes and probes. Leave the CLI responsible for composing checks and reporting evidence. Keep runtime sandbox verification; extracting it is not permission to remove the gate or to retry unrestricted. A focused facade fake can validate protocol changes without a provider turn.

### B06 — [P2, design problem] Live event transitions and deferred effects are represented as an implicit local protocol

- Evidence: `backend/app/services/jarvis_coordinator.py:1533-2074` handles provider events in one method. At `1548-1566` it creates a seven-element shell-call tuple and many independent action flags/optional values; transactional changes occur from `1567`, and after-commit transport/presentation effects dispatch across `1942-2074`. Wake activation, speech response ownership, delivery, task updates, correlation and tool replay share that scope.
- The same module owns session bootstrap, wake verification and controlled response scheduling. `jarvis_brain.py:113-592` is a broad tool switch and mixes admission receipts, result/context lookups and per-tool actions. The canonical catalog does validate its tool names against the gateway allowlist, which is worth preserving.
- Consequence: a change to one event's meaning requires mentally simulating many flags and their ordering relative to commit. The actual safety distinction between durable transition and provider side effect is good but is encoded indirectly. File length alone is not the finding.
- Recommendation: retain one coordinator while introducing named transition outcomes/actions, then extract coherent wake/response transition routines with explicit inputs and outputs. Keep provider calls outside DB transactions. Separate gateway tool handlers only where they share a clear domain boundary; avoid one-file-per-branch or a generic event bus. Existing duplicate/reordered callback, stale-generation and interruption tests should anchor each extraction.

### B07 — [P3, contract clarity] The task runtime interface is nominally typed as one concrete implementation

- Evidence: `backend/app/services/task_worker.py:39` accepts `TaskAgentRuntime`, assigns a different Codex implementation under `type: ignore[assignment]` at `48`, and detects the context-management capability with `getattr` at `173`. The context, sink implementations, shared sink protocol and legacy loop live together in `agent_execution.py:35-381`.
- `task_execution_preferences.py:6-22` separately maintains a Pydantic input model and a manual provider schema with model/effort/tool enums. `worker_tool_policy.py:18-19` owns the actual capability identifiers, duplicated in the manual schema. This requires coordinated edits rather than a single discoverable contract. B01 is a concrete example of unchecked control vocabulary drift, although it is counted as its own bug above.
- Recommendation: define the small execution protocol and explicit context-management behavior next to its context/result/sink types; make both runtimes satisfy it without an ignore. Derive provider schema restrictions from shared contract definitions where useful. Add focused static checking for the extracted seam before expanding checking across unrelated existing code. No wholesale typing rewrite is needed.

### B08 — [P3, cleanup candidate] Explicit rollout compatibility remains spread through active behavior

- Evidence: `models/tasks.py:136-137` and `models/jarvis.py:58-59` retain the deprecated `presented_at` alias, populated by `api/tasks.py:229` and `api/jarvis.py:158`. `models/jarvis.py:339-350` accepts deprecated top-level reminder `due_at`, and `jarvis_time.py:105-112` translates it separately from structured timing. `jarvis_coordinator.py:329,1639-1642` synthesizes missing activation IDs and retains an explicitly old-client speech-stop gate. `task_presentations.py:708-713` accepts a pre-opaque checklist cursor. `services/chat_tools.py` is explicitly a compatibility re-export module.
- Consequence: a newcomer has to learn multiple names or event semantics for the same current concept. The wake compatibility flag also enlarges sensitive event-transition logic. These are distinct from useful older product features and from durable run/session recovery contracts.
- Recommendation: inventory current first-party callers and test fixtures, then remove unused aliases/rollout branches together, or document a concrete remaining caller and retirement condition. Do not delete the non-Codex executor or useful conversation features merely because their name contains "legacy". Keep fingerprint replay, native-session compatibility checks, migration tests and persistence invariants that serve the intended durable behavior.

## Batch 1 findings: configuration, persistence and schema

Read records: [review-persistence.json](evidence/review-persistence.json).

### P01 — [P2, correctness/contract] The ORM model and the migrated database disagree, and nothing checks them against each other

- Evidence: `evidence/schema-consistency-probe.py` migrates a fresh temporary DB and compares it with `Base.metadata`. Alembic autogenerate reports 46 differences. Eight foreign keys declared in `tables.py` do not exist in the database: `task_attempts.execution_run_id`, `task_attempts.parent_attempt_id`, `resource_leases.task_id` and `.execution_run_id`, `task_events.execution_run_id`, `task_artifacts.execution_run_id`, `task_questions.asking_run_id`, and `notification_outbox.task_event_id` (which differs). Even with `PRAGMA foreign_keys=ON`, the probe inserts a `task_artifacts` row pointing at a nonexistent run and SQLite accepts it.
- Root cause: on SQLite, `op.add_column` cannot add a foreign key, and `20260922_0023_task_control_plane.py:210-249` adds these as plain columns. The other differences are index/constraint names still carrying old `realtime_`/`voice_`/`speech_` prefixes, two indexes only the database has (`ix_orchestration_due`, `ix_task_execution_runs_queue`), and unique indexes the ORM calls constraints.
- `attention_migration_state` is created by migration 0021 and is absent from the ORM, yet `task_presentations.py:281-287` queries it with raw SQL on every reminder presentation to retire pre-migration events. Runtime behavior depends on a migration artifact.
- Tests: `test_jarvis_schema.py` asserts a hand-picked set of tables, columns and constraints. No test compares metadata against migrations. Migrations total 41 files and 3465 lines; `test_migration_recovery.py` (453 lines) mainly tests replay of legacy evidence through specific historical migrations.
- Consequence: a reader of `tables.py` sees integrity guarantees (cascades, `SET NULL`) that the database does not enforce. Deletion or cleanup code written against the model can leave orphans. Schema edits need two hand-synchronized definitions with no feedback when they drift.
- Recommendation: AGENTS.md treats development data as disposable, so squash the chain into one baseline generated from the corrected ORM, with indexes named consistently. Then add one test that runs `compare_metadata` against a freshly migrated database and fails on any difference. Remove `attention_migration_state` and the `migration_legacy_retired` branch together. Keep the migration lock and the partial-DDL resume behavior as a tested contract for future migrations; drop the legacy-backfill replay tests that only exercise retired history. Report the reset when it is done.
- Acceptance: the drift test is empty on a fresh DB; the orphan insert from the probe is rejected; task deletion cascades as the ORM declares; startup migration of an empty DB and concurrent startup still pass.

### P02 — [P3, change locality] Path settings are resolved three different ways, and config validation lives in launchers

- Evidence: `config.py:231-278` has eight copy-paste properties resolving paths against `backend_root`. Five other path settings have no property, so consumers resolve them themselves. `codex_orchestration_runtime.py:171` resolves `task_orchestration_profile` against `backend_root`, but `:51` resolves `task_orchestration_context_dir` against the process working directory. `runtime_auth_routes.py:25` and `task_mcp.py:158` join `backend_root` by hand.
- The default `session_secret_key = "dev-secret-change-me"` signs session cookies, device hashes, upload tokens and app-storage grants. Only `personal_host.py:122` and `rollout_check.py:201` reject it; `Settings` itself does not, so other start paths run silently with the known key.
- The comment at `config.py:149-150` about Phase 1 and a "pre-Jarvis fallback database" describes history, not current behavior.
- `ntfy_topic` (`config.py:133`) has no reader anywhere in `backend/app`.
- Recommendation: one `_backend_path()` helper with a property per path setting, delete `ntfy_topic`, and every consumer uses the property. Move the secret-strength check into a `Settings` validator for non-test environments. Delete the stale comment.
- Acceptance: a worker started from the repository root and from `backend/` resolves every data path identically; starting the API with the default secret outside tests fails fast with a clear message.

### P03 — [P3, cleanup] Small persistence leftovers

- `session.py:367-377` rewrites `alembic.ini` into a temporary file when `path_separator` is missing. It never runs (the file already has it) and would leak a temporary file per start if it did. `env.py:14` already sets the URL that `run_migrations` sets twice. `_prepare_sqlite_journal` runs both in `ensure_database_ready` and at the end of `run_migrations`.
- `tables.py` (1679 lines, 59 models) is ordered by when tables were added. For example, `TaskAttempt` and `TaskEvent` sit after `JarvisDevice` and `WorkerInstance`. Grouping by domain (conversation, Jarvis/live, tasks, results/apps) with section headers would make it navigable without splitting it into a package.
- `repositories/tasks.py` imports `utc_now` from the conversation repository; a shared time helper belongs next to `base.py`.

## Batch 2 findings: interactive backend

Read records: [review-backend-interactive.json](evidence/review-backend-interactive.json). Large modules were read at their entry points and decision sites, not line by line; each record states its ranges.

### I04 — [P1, security boundary] The legacy agent's write-root confinement is advisory and is reported as a sandbox

- Evidence: `agent_tools/execution.py:99-288` confines `python_execution` by monkeypatching `open`, `os.*`, `shutil.*` and `subprocess.*` inside the child process before `exec`ing the model's code. `run_shell_command` (`:358-403`) only rejects pipe/redirect/chaining tokens and then runs any executable on `PATH`. Both tools are model-visible to the default `task_executor="legacy"` agent, and `python_execution` is also exposed through `POST /api/conversations/{id}/tools`. The result metadata reports `sandbox_mode: allowed_write_roots_only`.
- Reproduction: `evidence/legacy-tool-confinement-probe.py` uses only a fresh temporary directory, with the workspace as the sole write root. Plain `open()` outside it is blocked. `import _io; _io.open(...)` writes outside it, and the shell tool running `python -c open(...)` writes outside it ([output](evidence/legacy-tool-confinement-probe.txt)). Other unpatched routes (`os.popen`, `os.spawn*`, `ctypes`) were not needed to show the gap.
- Consequence: the model, or content it reads (files, web results), can write anywhere the backend user can, while the UI and metadata say the run was confined. The Codex executor's OS-level native sandbox is the real boundary; the legacy path is not equivalent. No evidence of misuse; this is a misstated guarantee, not an incident.
- Recommendation: decide whether the legacy executor and the conversation tool route stay. If they stay, run both tools inside the same OS-level sandbox the Codex path uses (or the Windows sandbox helper it already depends on), and remove `shell_command` from model-visible tools until then. Either way, stop reporting `allowed_write_roots_only` for in-process patching. Do not try to make the monkeypatch list complete; that cannot be made sound.
- Acceptance: the probe reports no escape through any of its three routes, plus `os.popen` and `ctypes`; existing legacy-agent tests still pass, or are removed along with the executor.

### I01 — [P2, change locality] The legacy agent runner is one module split into five files that share about 50 private names

- Evidence: `agent_runner.py` imports from `agent_run_support`, `agent_prompt_builder`, `agent_runner_helpers` and `agent_metadata`. `evidence/private-import-scan.py` counts 67 underscore-prefixed names imported across `backend/app` modules; 48 of them come from `agent_tools.base` (17), `agent_run_support` (16) and `agent_prompt_builder` (15), consumed by the runner, `agent_model_verifier`, `jarvis_benchmark` and tests. `_record_llm_metrics` exists twice (`chat_orchestrator.py:359`, `agent_run_support.py:322`) with the same payload. `api/chat.py:64` calls `chat_orchestrator._resolve_session_metadata`.
- Consequence: the file boundaries do not tell a reader what each part owns. Any rename inside "support" can break the runner, the verifier and the benchmark. The underscores signal "private" while the names act as the real interface.
- Recommendation: name the seams that actually exist (prompt/tool-call parsing, run-state persistence, tool-action handling, run lifecycle transitions) as public functions in the module that owns them, and fold the rest back. Use one metrics recorder. Do this after the legacy-executor decision in I04, since retiring it removes most of this code.

### I02 — [P2, contract clarity] Agent run state is an untyped dictionary persisted as JSON

- Evidence: run metadata is a free-form `dict` read through 112 `metadata.get(...)`/`metadata[...]` sites across `agent_runner.py`, `agent_run_support.py` and `agent_metadata.py`, covering at least 24 distinct keys. Most reads re-check types defensively (for example `agent_metadata.py:47-49`). The first-pass record notes duplicate task/agent alias keys that are normalized repeatedly.
- Consequence: there is no single place to learn which keys a run carries, which are optional, or which are derived. Adding a field means finding every defensive reader.
- Recommendation: one typed run-state model (dataclass or Pydantic) with a single load/dump at the persistence boundary. Drop the alias keys under the AGENTS.md data policy rather than translating them.

### I03 — [P2, correctness risk] Conversation run lifecycles use unguarded writes outside the agent path

- Evidence: `conversation_store` has nine run-status mutators. Agent runs use guarded `transition_run_status`/`claim_run_for_execution`. The tool route (`api/conversations.py:305-376`) and council path (`chat_orchestrator.py:837,876`) use `mark_run_failed`/`mark_run_completed`, which write status without an expected-state check, across five or six separate commits per request. `mark_run_interrupted` has no production caller. `stream_single_response` and `run_single_response` accept `temperature`/`max_tokens` from the client (`frontend/lib/api.ts:469,528`) and never use them. The stream loop re-lists every event of the run every 150 ms (`chat_orchestrator.py:568-580`).
- Consequence: the same class of stranding as B02. A failure between `start_run` and the final write leaves a tool or council run `running` until the next API restart, and a late write can overwrite a cancellation. Not separately reproduced.
- Recommendation: one run-lifecycle owner that every run kind uses for start, guarded completion/failure/cancellation and the matching event. Delete the unguarded writers and the dead one. Either honor or drop the ignored request fields. Poll with `list_events_after(sequence)` instead of re-listing.

### I05 — [P3, cleanup] Pre-timeline conversations and old app paths are still reconstructed

- `conversation_store` now writes a timeline item for every user and assistant message (`:691,752,811,855`). The client still keeps a full reconstruction for conversations without timeline items (`frontend/lib/chat-history.ts`, 565 lines, reached through `conversation-feed.ts:34-36`), plus `isLegacyProgressRow` and `isLegacyActiveRunTransientMessage` filters. **Revised in batch 7:** the council path does emit timeline items, but the reconstruction is still the only source of council answers, usage, task links and branch locks, so the timeline path silently dropped them (C14). Complete the timeline contract first, then remove the reconstruction. Most of `frontend/lib/agent-event-presenter.ts` would go with it: its tool-call/result summarizers are consumed only by `chat-history.ts`, and they duplicate the server's `conversation_timeline` summaries. One heuristic already differs: the client's `isLowSignalToolIntentSummary` matches only "use tool X", while the backend's version also treats "working on it", "continuing" and "in progress" as low-signal.
- `app_registry.py:90` names the live served route root `LEGACY_GENERATED_APP_FRONTEND_ROOT_BASE`, and isolated apps are copied into it (`:339-342`). `_remap_legacy_frontend_path` rewrites old stored contracts on every read. Rename the constant to what it is, and drop the remap with a data reset.

### I06 — [P3, change locality] `llm.py` mixes provider policy, prompt caching, transport and stream parsing

- Evidence: 1515 lines. Three hand-copied retry/error loops (`:997`, `:1144`, `:1419`), each repeating an identical `build_prompt_metrics(...)` call five times. `_build_client` receives the model through a `ContextVar` rather than an argument (`:676-712`). Model-family behavior lives in three places: `settings.native_tool_model_prefixes`, and hard-coded prefix tuples at `:616` and `:630`. `council_completion` (`:1467`) has no caller. A new httpx client is created per request.
- Recommendation: one retry/error-mapping helper shared by the three transports; one small model-profile table for prefixes, overrides and token budgets; pass the model explicitly; delete `council_completion`. The cache-shaping code and its tests (`test_llm_prompt_cache.py`) are a coherent unit and can stay where they are.

### X01 addendum — knowledge note identity

`knowledge_memory._approved_note_path` (`:27-29`) names a note by date, kind and slugified title, without the memory ID. Two different memories with the same kind and title, approved on the same day, write the same file: the second overwrites the first, and deleting either one removes the note both rows point to. This is source-confirmed rather than run, and belongs in the same reviewed-memory service fix as X01.

## Batch 5 findings: durable tasks and Live backend

Read records: [review-durable-live.json](evidence/review-durable-live.json). This batch covers about 23k lines, so it was read where scans pointed: [private-attribute-scan](evidence/private-attribute-scan.json), [long-functions-scan](evidence/long-functions-scan.json), status-literal and status-assignment counts, attempt-construction sites, and background-task tracking.

### T03 — [P2, behavior defect] The reminder scheduler can overwrite a concurrent cancel

- Evidence: every task-state writer in `task_service` first calls `_lock_task`, a no-op `UPDATE` that starts the SQLite write transaction before reading (`task_service.py:2175-2182`). Under pysqlite, a plain `SELECT` does not begin a transaction. `reminder_scheduler._resume_one` (`:82-145`) reads the task with a plain `SELECT`, checks `status == "waiting_until"`, then writes `completed` (reminders) or `queued` (scheduled waits) along with its run status and event.
- Reproduction: [reminder-race-probe.py](evidence/reminder-race-probe.py) uses a fresh temporary DB. It commits `status='cancelled'` from a second connection between the scheduler's read and its write, and the scheduler then writes `completed` over it ([output](evidence/reminder-race-probe.txt)). A real `control(cancel)` commits the same way, since it doesn't wait for a lock the scheduler never takes.
- Consequence: a cancel or suspend that lands in the same instant a wait expires is lost. A cancelled reminder still fires; a cancelled scheduled task is requeued and runs. The window is small (one poll per second), so this is rare but real. Not a data-loss risk beyond that task.
- Recommendation: route the resume through `task_service` under `_lock_task`, re-checking status after the lock, as part of T02. **Acceptance:** the probe ends with `cancelled`.

### T02 — [P2, change locality] Task lifecycle transitions are implemented separately in several places

- Control effects: `task_service.control` (`:774-894`) applies each action directly when the task isn't running. `confirm_control` (`:1008-1046`) applies the same actions again after a stop. `worker_coordinator.worker_exited` (`:690-780`) re-implements `confirm_control` for process-exit evidence. All three set the same task and run fields, events and lease cleanup by hand.
- Attempt creation: `TaskAttempt(...)` is constructed in six places (`task_service.py:2443`, `reminder_scheduler.py:121`, `worker_coordinator.py:722,821,1439,1497`). Only `_create_attempt` handles result-page binding and current-attempt pointers on both task and run. The reminder scheduler's copy creates the resumed attempt without `parent_attempt_id`, checkpoint or `checkpoint_version`, unlike every other resume path. Its effect depends on the executor; the native runtime binds per run.
- Direct status writes: 36 `task.status =` and 31 `run.status =` assignments across 8 service modules, always as hand-kept task/run pairs.
- Hidden projection masks the divergence: `confirm_control` calls `fail_document_result` on cancel and `worker_exited` doesn't. Both behave the same only because the `append_event` projector (B04) also fails the page when it sees `task.cancelled`. A reader can't learn that from either call site.
- Recommendation: one task-lifecycle module that owns the transitions (`apply_control`, `confirm_stop(evidence)`, `create_attempt`, `resume_after_wait`, `fail`/`complete`), each taking the lock and writing task, run, attempt and events together. `control`, `confirm_control`, `worker_exited` and the scheduler call it with their own evidence. This is the concrete shape behind the roadmap's "explicit transaction/control seam" (B04/B07). Keep the existing fence and idempotency checks; they are correct.

### T01 — [P3, contract clarity] Status vocabularies are bare strings shared across several state machines

- About 340 quoted status literals (`"queued"`, `"running"`, `"failed"`, …) appear in `services`/`api`/`models`, against 21 uses of the `TaskStatus`/`RunStatus` enums. The same words mean different things for tasks, execution runs, conversation runs, presentations, live sessions and result pages. There are 27 inline subsets (`in {"failed", "interrupted"}` appears 9 times) plus named sets scattered across five modules, and `ACTIVE_SESSION_STATES`/`ACTIVE_LIVE_STATES` are the same set defined twice (`jarvis_coordinator.py:52`, `task_capture.py:20`). The control-action vocabulary is restated in four places (`CONTROL_ACTIONS`, `models/tasks.py:305`, `jarvis_tool_catalog.py:184`, the orchestration subset at `task_orchestration.py:165`); B01 is what drift in it looks like.
- Recommendation: one `Literal`/`StrEnum` per state machine plus its named predicate sets, in the lifecycle module from T02, and derive API/tool schemas from it. Type checking on that seam (B07) then catches the next `pause`.

### B04 addendum — measured scope

[private-attribute-scan.py](evidence/private-attribute-scan.py) counts 25 uses of other modules' private singleton members. 13 of them are on `task_service` (`_lock_task`, `_assert_fence`, `_current_run`, `_worker_health`, `_assign_worker_eligibility`) from five modules. Others: `chat_orchestrator` (5, see I01), `task_presentation_service` (4, from the observer and orchestration), and `selftest/host.py` patching `openai_live_adapter._send/_handle_event` and `jarvis_coordinator._trace_response_gate` at runtime (batch 6).

### B08 addendum

`services/notifications.py:19` keeps `LegacyNotificationStateError`, a "compatibility tombstone for removed spoken/snoozed acknowledgements" that returns HTTP 410. Under AGENTS.md this can go with the other rollout aliases.

### Batch 5 positive patterns

- `OpenAILiveAdapter` (1522 lines) imports no persistence or coordinator code. The `VoiceSessionAdapter` protocol boundary holds, and the coordinator owns all durable effects.
- Orchestration `DecisionTools` takes no identities from the model, checks an explicit per-tool argument allowlist, fences every mutation with `DecisionAuthority`, and records an idempotent audit.
- All 27 `asyncio.create_task` sites in services and hosts keep a reference and are cleaned up, so there are no fire-and-forget tasks.
- The notification outbox is durable, with bounded retries and startup recovery.

## Positive patterns to preserve

- Task identity is separated into task, user turn, execution run and attempt. `task_service._assert_fence` (`2184-2212`) checks run/attempt/token and supervisor generation. Current worker claim setup and generation checks are explicit in `worker_coordinator.py:1055-1071,1229-1263`.
- Result admission and publication have real behavioral contracts: strict `ResultRequest`, registered typed result schemas, expected revision checks, exact replay fingerprints, retained last good content and atomic artifact/event/page/terminal publication. `task_service.py:1627-1881` is complex partly because it retains those invariants; do not simplify by splitting its commit across transactions.
- Shared-resource reacquisition distinguishes changed resources from unobservable resources (`worker_coordinator.py:290-391`). Explicit acceptance cannot turn an unknown state into proof. Expose this structured reason to clients rather than requiring them to parse the human summary.
- `VoiceSessionAdapter` is a useful narrow protocol, and its fake adapter crosses the same normalized callback boundary. The canonical tool catalog checks its names against the restricted gateway allowlist.
- `CommitSignal` broadcasts after commit with bounded rescan recovery, while the diagnostic `ObservationQueue` is explicitly not task/publication authority. `db/session.py` carefully shields cleanup from request cancellation and rejects mutation in read scopes.
- Tests avoid the personal `.env`; the shared fixture provides a migrated isolated DB, workspace roots, fake voice state and optional external-network blocking. Relevant test suites explicitly cover stale writes, concurrent follow-ups, exact process-exit proof, failed publication rollback and result replay. Their presence is evidence of valuable contracts, not a claim that this reviewer reran or fully inspected every test.

## Reproducible verification and limits

Run from the repository root:

```powershell
backend/.venv/Scripts/python.exe docs/audits/repo-maintainability/evidence/backend-lifecycle-probes.py
```

Observed on the audited candidate: exit `0`; all three probes reported `defect_reproduced: true`. Full JSON output is retained in `evidence/backend-lifecycle-probes.jsonl`.

- Startup: previously started telemetry/scheduler/coordinator, then injected observer failure; `cleanup_calls: []`.
- Conversation runner: `durable_status: running`, `failure_transition_calls: 0`, `supervisor_error_logs: 0`, `supervisor_still_owns_run: false`.
- Revocation: runtime action `pause`, control error `Unsupported task control action: pause`.

These probes execute extracted real control flow with fakes, not end-to-end API/provider execution. They establish the local error paths and vocabulary conflict. They do not prove actual provider shutdown timing, process-tree exit or database transaction behavior. The script intentionally asserts reproduction of the current defects; it is evidence, not a passing application acceptance test. The central audit owner owns full backend-suite results; no paid test or physical-device test was run for this review.

## Coverage appendix

### Focused semantic source review

These paths had behavior or ownership logic read and traced. Large files were sampled at the relevant functions; they were not all reviewed line by line. Key sections are stated above. This list can be reconciled with the central coverage inventory without treating a file entry as exhaustive verification.

- `backend/app/main.py`
- `backend/app/worker.py`
- `backend/app/api/tasks.py` — contracts, projections and detail assembly.
- `backend/app/db/session.py` — lifecycle, commit hooks, read scopes and cancellation cleanup.
- `backend/app/db/repositories/tasks.py` — dispatch, append and transition behavior.
- `backend/app/models/tasks.py` — summary, recovery data, presentation and control contracts.
- `backend/app/models/jarvis.py` — presentation and reminder timing contracts.
- `backend/app/presenters/tasks.py`
- `backend/app/services/agent_execution.py`
- `backend/app/services/agent_runner.py` — request handling context and `continue_run` claim/preparation/main-loop failure boundaries.
- `backend/app/services/agent_tools/base.py` — tool/context contracts and path-resolution entry logic.
- `backend/app/services/agent_tools/registry.py`
- `backend/app/services/agent_tools/tasks.py`
- `backend/app/services/app_registry.py` — stored contract reconstruction and path compatibility.
- `backend/app/services/auth.py`
- `backend/app/services/chat_tools.py`
- `backend/app/services/codex_task_runtime.py` — policy/auth/setup, native binding, stream observation, publication and cleanup; helper bodies sampled.
- `backend/app/services/commit_signals.py`
- `backend/app/services/conversation_store.py` — run claim/transition and atomic completion paths.
- `backend/app/services/jarvis_brain.py` — gateway receipt, admission and workflow entry paths; remaining dispatch branches structurally inspected.
- `backend/app/services/jarvis_coordinator.py` — lifecycle, activation normalization and selected provider-event transaction/effect paths.
- `backend/app/services/jarvis_time.py` — reminder timing normalization.
- `backend/app/services/jarvis_tool_catalog.py` — admission schema, shared preferences and allowlist validation.
- `backend/app/services/live_task_updates.py`
- `backend/app/services/native_worker.py`
- `backend/app/services/observation_queue.py`
- `backend/app/services/orchestration_authority.py`
- `backend/app/services/phone_display.py`
- `backend/app/services/result_static.py`
- `backend/app/services/run_lifecycle.py`
- `backend/app/services/run_supervisor.py`
- `backend/app/services/runtime_auth_routes.py`
- `backend/app/services/task_capabilities.py`
- `backend/app/services/task_context.py` — pinned context authority and evidence lookup entry.
- `backend/app/services/task_context_upload.py` — upload completion, readiness and worker-eligibility update.
- `backend/app/services/task_delegation.py`
- `backend/app/services/task_execution_preferences.py`
- `backend/app/services/task_notification_preferences.py`
- `backend/app/services/task_orchestration.py` — runtime/tool boundary, issue intake, context and lease/recovery entry paths.
- `backend/app/services/task_presentations.py` — projection, compatibility cutoff and checklist cursor paths.
- `backend/app/services/task_project_tools.py` — authorized-root tail and sandbox execution facade.
- `backend/app/services/task_result_contract.py`
- `backend/app/services/task_result_types.py`
- `backend/app/services/task_results.py`
- `backend/app/services/task_semantic_compaction.py` — fenced read/model/write separation.
- `backend/app/services/task_service.py` — admission, continuation, control, completion, fencing and attempt/result allocation.
- `backend/app/services/task_statuses.py`
- `backend/app/services/task_update_policy.py`
- `backend/app/services/task_worker.py`
- `backend/app/services/voice_session_adapter.py` — protocol and fake adapter boundary.
- `backend/app/services/worker_coordinator.py` — reacquisition, claim/generation and resource/worker/run ownership paths.
- `backend/app/services/worker_tool_policy.py`
- `backend/app/jarvis_tasks/codex_probe.py` — raw command/sandbox helper ownership and probe configuration, not the entire paid probe corpus.

### Semantically reviewed test/support paths

- `backend/pyproject.toml`
- `backend/tests/conftest.py`
- `backend/tests/test_run_supervisor.py`
- `backend/tests/test_worker_tool_policy.py`

### Structural-only and unverified areas

All first-party `backend/app/**/*.py` paths were included in file/import/function-shape inspection. Files absent from the semantic list above received only inventory, import, signature or targeted search coverage in this review. In particular, full LLM streaming/cache behavior, all generated-app build/verification paths, all retrieval/knowledge operations, detailed Android upload authorization, SQLAlchemy table constraints, historical migration bodies, full CLI/selftest acceptance implementations and all other test bodies are not claimed semantically complete here. Other audit workstreams may supply deeper coverage. No production security or paid-provider acceptance conclusion follows from this maintainability review.
