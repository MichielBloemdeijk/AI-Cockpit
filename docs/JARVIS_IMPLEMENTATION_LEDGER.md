# Jarvis Implementation Ledger

## Purpose

This ledger is the restart point for every Jarvis implementation session. The normative plan is [JARVIS_IMPLEMENTATION_PLAN.md](JARVIS_IMPLEMENTATION_PLAN.md). Update this file whenever a phase begins, materially deviates, passes or fails a checkpoint, or hands off to another session.

## Current position

- **Current phase:** Phase 7 — heard-prefix measurement and estimator research
- **Last completed phase:** Phase 6
- **Product implementation started:** yes
- **Next session entry point:** reproduce and instrument the reported sentence-scale early/late interruption cutoff drift, compare direct playout/truncation signals, then record the chosen Phase 7 estimator before changing durable delivery truth
- **Baseline branch:** codex/jarvis-voice-poc
- **Baseline commit:** 281fc9f9cbe7fed44cc11b4b3ee7f2c7c56f2b97
- **Phase 0 completion commit:** 5b9aa8c707324d9be079266fc7ee8afc5b2134db

## Phase status

| Phase | Status | Exit gate | Commit |
| --- | --- | --- | --- |
| 0. Plan and reversible cutover preparation | Complete | Passed 2026-08-27 | 5b9aa8c |
| 1. Clean schema and ordered-domain contracts | Complete | Passed 2026-08-27 | 70c15e7 |
| 2. Standard conversations and Jarvis daily lifecycle | Complete | Passed 2026-08-27 | 8d72f15 |
| 3. First-class tasks and task UI | Complete | Passed 2026-08-27 | 5e1b75d |
| 4. Worker, runtime seam, and resource leases | Complete | Passed 2026-08-27 | e98f15e |
| 5. Coordinator, fake provider, and live workspace | Complete | Passed 2026-08-27 | 71f78e9 |
| 6. OpenAI Realtime and WebRTC | Complete | Passed 2026-08-29 | 58b2a78 |
| 7. Barge-in and audible-history truth | Research in progress | Measured estimator and implementation pending | — |
| 8. Brain delegation and proactive updates | Not started | — | — |
| 9. Browser-local wake and follow-up | Not started | — | — |
| 10. Wiki/search, notifications, and archive UX | Not started | — | — |
| 11. POC retirement and final verification | Not started | — | — |

## Phase 0 record

### Scope completed

- Saved the decision-complete full implementation plan.
- Updated target/evolution terminology from eight-hour episodes to Jarvis daily logs with the 04:00 Europe/Amsterdam boundary.
- Recorded locked product, provider, ordering, task, worker, rollover, transcript, and scope decisions.
- Captured repository/toolchain baseline and complete automated verification.
- Created an online, integrity-checked backup of the active SQLite database inside the ignored backend/data tree.
- Preserved the active POC database so this documentation-only phase does not interrupt the running POC. The destructive clean cutover is intentionally deferred to Phase 1 after the new schema passes its blank-database gate.
- Made no schema, API, service, worker, or frontend product implementation changes.

### Repository state at start

- Branch: codex/jarvis-voice-poc
- HEAD: 281fc9f9cbe7fed44cc11b4b3ee7f2c7c56f2b97
- Commit subject: fix: persist Jarvis voice transcript
- Existing modified file: docs/ARCHITECTURE.md
- Existing untracked items included PLAN.md, claude-code reference/, data/, the earlier target/evolution documents, frontend instruction/reference files, and a knowledge note.
- All pre-existing changes were treated as user-owned and preserved.

### Toolchain

- uv 0.11.6
- Python 3.12.9
- Node.js v20.10.0
- npm 10.2.3
- Next.js 16.2.3

### Verification evidence

Run on 2026-08-27 from the paths shown:

1. backend: uv run pytest
   - Result: PASS
   - 178 passed in 210.03 seconds
   - 178 warnings, all reported through the repeated Alembic configuration deprecation about path_separator
2. frontend: npm run test
   - Result: PASS
   - 18 files and 109 tests passed in 15.95 seconds
3. frontend: npm run lint
   - Result: PASS
   - ESLint exited 0 with no findings
4. frontend: npm run build
   - Result: PASS
   - Next.js 16.2.3 production build and TypeScript checks completed; 28 static pages generated

### Database backup evidence

- Source: backend/data/ai_cockpit.db
- Backup: backend/data/backups/ai_cockpit.pre-jarvis-phase0.20260827T001126+0200.db
- Size: 10,907,648 bytes
- SHA-256: EA94761D5E9189295E2596CDD5125AF401E6D6F94A0C567735AEF227D5E8991E
- SQLite integrity_check: ok
- Alembic version: 20260504_0009
- Snapshot counts:
  - conversations: 11
  - conversation_runs: 63
  - conversation_events: 3330
  - conversation_messages: 115
  - generated_apps: 11
- Ignore boundary: backend/data/ is excluded by the root .gitignore.

### Phase 0 deviations and rationale

The original phase wording said to move the active database to a backup. Phase 0 instead created a consistent online backup and left the source database active. Moving an open database before the replacement schema exists would unnecessarily break the POC and could cause the current application to recreate an old-schema database. Phase 1 owns the clean cutover after its schema and upgrade tests pass. The rollback requirement is fully satisfied by the verified backup.

### Open blockers

None.

## Phase 1 record

### Scope completed

- Added the Jarvis-era schema head with conversation lifecycle/day fields, the singleton Jarvis pointer, audible-delivery records, Realtime sessions and ordered events, global tasks and attempts, questions, artifacts, targets, leases, presentations, worker instances, and the notification outbox.
- Preserved standard conversations, Council runs, branches, artifacts, generated apps, reviewed memory/Wiki provenance, Web Push compatibility, and the POC-only columns that later phases explicitly retire.
- Added per-conversation and per-session atomic cursor allocation plus a transactionally ordered global task stream.
- Added idempotent conversation event append, task dispatch, task event append, and atomic task state/event repository primitives.
- Added a consistent multi-domain snapshot projection whose advertised cursors come from committed event rows.
- Changed the default runtime path from `data/ai_cockpit.db` to the fresh `data/ai_cockpit_jarvis.db` only after the blank-database and regression gates passed.
- Initialized the ignored fresh runtime database at revision `20260827_0010`; the original POC database and Phase 0 backup were not overwritten or migrated.

### Repository state at start

- Branch: codex/jarvis-voice-poc
- HEAD: 5b9aa8c707324d9be079266fc7ee8afc5b2134db
- Commit subject: docs: establish Jarvis implementation contract
- Existing unrelated changes preserved: `.env.example`, `PLAN.md`, `claude-code reference/`, `data/`, `frontend/AGENTS.md`, `frontend/CLAUDE.md`, and the existing knowledge note.

### Verification evidence

Run on 2026-08-27:

1. backend narrow schema/persistence gate: `uv run pytest tests/test_jarvis_schema.py tests/test_conversation_store.py tests/test_db_session.py -q`
   - Result: PASS
   - 13 passed in 4.46 seconds
2. blank migration and metadata parity: `uv run alembic upgrade head` followed by `uv run alembic check` against a fresh temporary database
   - Result: PASS
   - Revision `20260827_0010`; no new upgrade operations detected
3. generated-app preservation gate: `uv run pytest tests/test_jarvis_schema.py tests/test_apps_api.py -q`
   - Result: PASS
   - 28 passed in 17.38 seconds
4. complete backend gate: `uv run pytest`
   - Result: PASS
   - 183 passed in 222.08 seconds
5. fresh runtime cutover:
   - `data/ai_cockpit_jarvis.db` created at revision `20260827_0010`
   - SQLite `integrity_check`: ok
   - Size: 487,424 bytes
   - SHA-256: AF404002A3FF50064BAF0ECB0B1023BA9C4509FD650BA2A405A7BF1E4E898039
6. Phase 0 backup revalidation in read-only mode:
   - Revision: `20260504_0009`
   - SQLite `integrity_check`: ok
   - Size: 10,907,648 bytes
   - SHA-256: EA94761D5E9189295E2596CDD5125AF401E6D6F94A0C567735AEF227D5E8991E

### Phase 1 deviations and rationale

The clean Jarvis-era schema is delivered as one new Alembic head on the proven historical revision chain rather than deleting the old migration files. Runtime data still receives the required semantic clean cutover because the application now targets a new database file. Retaining the historical chain keeps the POC schema reproducible and makes the Phase 0 fallback materially safer without changing any locked data-model invariant.

Cross-domain causal IDs whose foreign keys would introduce circular DDL dependencies remain typed ID columns, while ownership and lifecycle foreign keys remain enforced. Repository transactions and contract tests enforce their write boundaries. This avoids an unresolvable Alembic dependency cycle and keeps future blank-database migrations deterministic.

### Checkpoint

- Status: passed 2026-08-27
- Commit subject: `feat(jarvis): establish durable domain schema`
- Commit hash: 70c15e7f8e5459f037167d7b59aa8307ab5edac0
- Next entry point: Phase 2 operational-day function, transactional current-log resolution, and lazy first durable turn
- Blockers: none

## Phase 2 record

### Scope completed

- Added the authoritative timezone-aware Europe/Amsterdam operational-day function with the 04:00 boundary.
- Added transactional singleton-state locking, lazy first-turn log creation, competing-first-turn serialization, active-exchange rollover deferral, idle rollover, daily titles, and same-day explicit fresh starts.
- Persisted typed Jarvis turns with modality, `jarvis_shell` execution mode, communication state, and idempotent accepted-turn events.
- Added explicit fresh-start acknowledgement on the old log, closure and pointer replacement in one transaction, and retry-safe fresh-start handling.
- Enforced closed Jarvis log immutability in repositories and rejected standard Agent, Council, inline-tool, branch, and resend entry points for every Jarvis log.
- Added `GET /api/jarvis/state`, `POST /api/jarvis/turns/text`, and `POST /api/jarvis/logs/new` typed contracts.
- Extended shared conversation summaries/messages with kind, lifecycle, day, current/live, read-only, modality, execution, communication, and causal metadata.
- Added All/Standard/Jarvis history filters, Jarvis lifecycle badges, current-log routing to `/jarvis`, and read-only rendering for closed logs without composer, branch, or resend controls.
- Added the initial typed `/jarvis` workspace needed for the Phase 2 routing contract. Page load reads state only; the first accepted turn remains the lazy creation boundary.
- Preserved archive/closure independence, standard conversation behavior, and global task identity across rollover.

### Repository state at start

- Branch: codex/jarvis-voice-poc
- HEAD: 70c15e7f8e5459f037167d7b59aa8307ab5edac0
- Commit subject: feat(jarvis): establish durable domain schema
- Existing unrelated user-owned changes remained untouched.

### Verification evidence

Run on 2026-08-27:

1. Phase 2 backend lifecycle/API gate: `uv run pytest tests/test_jarvis_daily.py tests/test_jarvis_schema.py tests/test_conversation_api.py -q`
   - Result: PASS
   - 25 passed in 12.11 seconds
2. metadata parity: `uv run alembic check`
   - Result: PASS
   - No new upgrade operations detected
3. complete backend gate: `uv run pytest`
   - First run found one backward-compatibility fixture failure after 193 passes; new `MessageRecord` truth fields received legacy-safe defaults.
   - Final result: PASS
   - 194 passed in 122.01 seconds
4. frontend state/component/API gate: `npm run test -- --run`
   - Result: PASS
   - 19 files and 112 tests passed
5. frontend lint: `npm run lint`
   - Result: PASS
6. frontend production build: `npm run build`
   - Result: PASS
   - Next.js 16.2.3 build and TypeScript checks completed; 29 routes generated, including `/jarvis`

### Phase 2 deviations and rationale

Windows Python does not include an IANA timezone database, so the cross-platform `tzdata` dependency was added to make the locked Europe/Amsterdam and DST behavior deterministic. The installed lock resolved `tzdata` 2026.3. This is an implementation dependency, not an architecture change.

The initial typed `/jarvis` page landed in Phase 2 rather than waiting for the full Phase 5 live workspace because the Phase 2 routing contract requires current history entries to open `/jarvis`. It intentionally contains no fake coordinator or voice state: it reads the current durable log, accepts typed shell turns, and exposes explicit fresh start. Phase 5 still owns the full conversation/tasks/activity/header projections.

### Checkpoint

- Status: passed 2026-08-27
- Commit subject: `feat(jarvis): add daily log lifecycle`
- Commit hash: 8d72f15ae6b3f3b531d7d51f8337fae3aa4f9087
- Next entry point: Phase 3 global task service, normalized task trace, APIs, and dedicated task UI
- Blockers: none

## Phase 3 record

### Scope completed

- Added the authoritative global task service over the Phase 1 schema with idempotent dispatch, attempts, normalized lifecycle transitions, questions, answers, artifacts, targets, terminal results, presentations, cancellation, and explicit retry.
- Added the globally ordered, replayable, idempotent task event stream and queue-position plus worker-health projections.
- Implemented the cancellation state machine: queued or waiting work cancels immediately, while running work records a cancellation request until the executor confirms safe exit.
- Added typed authenticated `/api/tasks` list, dispatch, detail, global event replay, cancellation, retry, question creation, and one-time answer endpoints.
- Added `/tasks` dispatch/list and `/tasks/{id}` full Agent trace pages with attempts, worker health, queue position, targets, questions, artifacts, results, cancel, and retry controls.
- Redirected the retired `/background` routes to `/tasks` and changed shared navigation to the global Tasks surface.
- Reused the shared `ChatMessage` Agent-trace renderers through a deterministic task-event projection.
- Added a compatibility bridge that gives every newly delegated standard-chat or Jarvis background run a global task identity immediately, links the temporary run as the current attempt runtime reference, and mirrors start, progress, questions, answers, terminal state, cancellation, and restart interruption into the task domain.
- Changed conversation task-card identity from the temporary child run to the global task while retaining the child run only as compatibility execution metadata.
- Kept global task rows out of conversation history; conversational presentation remains an explicit causal projection.
- Updated the shipped architecture document to describe first-class tasks and the bounded Phase 4 worker gap.

### Repository state at start

- Branch: codex/jarvis-voice-poc
- HEAD: 8d72f15ae6b3f3b531d7d51f8337fae3aa4f9087
- Commit subject: feat(jarvis): add daily log lifecycle
- Existing unrelated user-owned changes remained untouched.

### Verification evidence

Run on 2026-08-27:

1. Phase 3 service, integration, and lifecycle gate: `uv run pytest tests/test_task_service.py tests/test_conversation_store.py tests/test_agent_runner.py tests/test_run_lifecycle.py -q`
   - Result: PASS
   - 71 passed in 29.63 seconds
2. metadata parity: `uv run alembic check`
   - Result: PASS
   - No new upgrade operations detected
3. complete backend gate: `uv run pytest`
   - First run exposed one obsolete assertion that expected `/api/tasks` to be absent; its Phase 3 expectation was updated to HTTP 200.
   - Final result: PASS
   - 199 passed in 135.77 seconds
4. frontend task trace/API and regression gate: `npm run test -- --run`
   - Result: PASS
   - 20 files and 116 tests passed
5. frontend lint: `npm run lint`
   - Result: PASS
6. frontend production build: `npm run build`
   - Result: PASS
   - Next.js 16.2.3 build and TypeScript checks completed; 30 routes generated, including `/tasks` and `/tasks/[taskId]`

### Phase 3 deviations and rationale

The existing in-process child run remains temporarily linked as `agent_runtime_reference` for execution compatibility. It is no longer the product task identity or task-state source. Removing that runtime path before Phase 4 supplies the separate worker and execution sink would leave delegated tasks unexecutable; the bridge is bounded to the explicitly planned worker cutover.

The Tasks UI can accept work before the worker exists and honestly reports worker health as offline. Dispatch remains durable and queued; Phase 4 owns claiming and execution rather than hiding or faking worker state.

### Checkpoint

- Status: passed 2026-08-27
- Commit subject: `feat(jarvis): add first-class tasks`
- Commit hash: 5e1b75d6408319c8bffa7ffbb48c2f1ec9d5a469
- Next entry point: Phase 4 worker process, runtime context/sink extraction, FIFO claiming, heartbeats, isolated workspaces, cancellation observation, and typed resource leases
- Blockers: none

## Phase 4 record

### Scope completed

- Added `AgentExecutionContext` and `AgentExecutionSink` as the execution boundary, retained a conversation sink adapter, and added a task sink that writes ordered global events, checkpoints, questions, artifacts, terminal state, and presentations.
- Added a worker-native agent loop that reuses the existing model boundary, native control tools, provider tool schemas, and tool execution registry inside an isolated task-attempt workspace.
- Added `python -m app.worker` as a process entry point; FastAPI no longer starts delegated background execution.
- Added transactionally serialized worker registration, one-active-worker enforcement, independent idle/busy heartbeat, and stopped/rejected/stale worker states.
- Added strict FIFO head claiming by task creation order and stable ID. A lease-blocked head is never bypassed.
- Added typed, exclusive, sorted, all-or-nothing resource acquisition over every mutable task target, lease renewal, expiry cleanup, and release on all worker exits.
- Added durable attempt ownership, claim/start/heartbeat timestamps, runtime references, isolated workspace paths, checkpoints, and globally ordered claim/runtime events.
- Added checkpoint-and-requeue with idempotent target expansion, complete lease release, and a new attempt under the same task.
- Added running-cancellation observation and safe executor exit; queued/waiting cancellation remains immediate.
- Added stale-worker takeover that interrupts owned tasks, ends attempts, expires leases, emits a durable event, and never retries automatically.
- Kept API restart ownership-neutral by excluding first-class worker-owned task projections from legacy run interruption recovery.
- Updated the voice/background compatibility surface to read and cancel global tasks while preserving old run-only records.
- Added live worker-health API and Tasks-page projection.
- Added standalone and all-services PowerShell worker launch paths plus VS Code `Start Task Worker`/`Start All` integration.
- Updated README and shipped architecture documentation for the three-process backend/worker/frontend runtime.

### Repository state at start

- Branch: codex/jarvis-voice-poc
- HEAD: 5e1b75d6408319c8bffa7ffbb48c2f1ec9d5a469
- Commit subject: feat(jarvis): add first-class tasks
- Existing unrelated user-owned changes remained untouched.

### Verification evidence

Run on 2026-08-27:

1. Phase 4 worker, lifecycle, and compatibility gate: `uv run pytest tests/test_worker_coordinator.py tests/test_task_worker.py tests/test_task_service.py tests/test_agent_runner.py tests/test_conversation_store.py tests/test_run_lifecycle.py tests/test_voice_api.py -q`
   - Result: PASS
   - 84 passed in 92.46 seconds
2. focused coordination/worker gate after final lifecycle changes: `uv run pytest tests/test_worker_coordinator.py tests/test_task_worker.py -q`
   - Result: PASS
   - 10 passed in 4.36 seconds
3. deterministic native-tool worker scenario: `uv run pytest tests/test_task_worker.py::test_deterministic_real_agent_task_completes_through_worker -q`
   - Result: PASS
   - The worker executed `file_write` inside `attempt-1`, persisted its trace/checkpoint, and finalized a result artifact
4. metadata parity: `uv run alembic check`
   - Result: PASS
   - No new upgrade operations detected
5. complete backend gate: `uv run pytest`
   - Result: PASS
   - 209 passed in 229.79 seconds
6. frontend task health/API and regression gate: `npm run test -- --run`
   - Result: PASS
   - 20 files and 117 tests passed
7. frontend lint and production build: `npm run lint` and `npm run build`
   - Result: PASS
   - Next.js 16.2.3 build and TypeScript checks completed; 30 routes generated
8. PowerShell parser gate for `scripts/start-worker.ps1` and `scripts/start-jarvis-local.ps1`
   - Result: PASS
   - Both scripts parsed without errors

### Phase 4 deviations and rationale

The Phase 1 schema already contained worker, target, lease, attempt, and checkpoint fields, so Phase 4 required no Alembic revision. `alembic check` confirms model/DDL parity.

The native agent decision path is shared through the existing model boundary, control-tool definitions, provider schemas, and tool registry rather than moving the interactive conversation loop wholesale. This keeps standard chat behavior stable while the new context/sink boundary gives task execution its own durable projection and cancellation ownership.

The legacy background child run remains a status-only compatibility projection for the POC voice API. The worker synchronizes its coarse state, but global tasks, attempts, events, questions, artifacts, and leases are authoritative. The child is never scheduled by FastAPI.

### Checkpoint

- Status: passed 2026-08-27
- Commit subject: `feat(jarvis): add durable task worker`
- Commit hash: e98f15e749d5570c223f983e68bd0b72f12a68a6
- Next entry point: Phase 5 coordinator contract, deterministic fake provider, live Jarvis workspace, and command lab
- Blockers: none

## Phase 5 record

### Scope completed

- Added provider-neutral `VoiceSessionAdapter` and normalized provider events plus a deterministic fake adapter that crosses the same callback boundary planned for OpenAI.
- Added a durable Jarvis coordinator that owns controller generations, provider session lifecycle, floor projection, hydration context, speech-delivery truth, restricted shell mediation, presentation reservations, and fresh-log/session coordination.
- Enforced one mutable controller generation, observer-only reads, deterministic supersession, reconnect through a new generation, and stale-session rejection before and after brain calls.
- Added completed, interrupted, and unheard delivery persistence. Only heard content becomes transcript/context, duplicate deliveries are idempotent, and late generating/speaking events after terminal delivery are retained only as ignored diagnostics.
- Added a restricted brain gateway with exactly `dispatch_task`, `get_task`, `answer_task_question`, `cancel_task`, `start_new_jarvis_log`, and `end_voice_session`; it has no agent registry, filesystem, browser, code, app, or Wiki access.
- Added task-presentation projection with deterministic progress coalescing, terminal supersession, question preservation/folding after answer, safe-floor deferral, generation-bound reservation, interrupted-delivery retry, and once-only presentation completion.
- Expanded `GET /api/jarvis/state` with transcript, active tasks/questions, pending presentations, latest session/floor state, worker health, and independent conversation/task/session cursors.
- Added independent conversation and session replay endpoints alongside the global task replay endpoint, fake-provider event injection, controller/observer establishment, presentation reservation, and session-close APIs.
- Rebuilt `/jarvis` as a responsive conversation/tasks/activity workspace with controller state, worker health, typed shell input, task actions, questions, pending-update state, explicit fresh/sleep/arm controls, and display-only merged activity.
- Added a developer conversation lab for deterministic user-floor, direct-response, interruption, delegation, presentation, controller-supersession, and provider-error injection.
- Added frontend independent-domain state that sorts and deduplicates replay, advances only the owning cursor, resets session state on controller replacement, resets log/session state on rollover, and preserves global task activity.
- Fixed the daily-turn idempotent-read refresh path surfaced by browser retry testing.

### Repository state at start

- Branch: codex/jarvis-voice-poc
- HEAD: e98f15e749d5570c223f983e68bd0b72f12a68a6
- Commit subject: feat(jarvis): add durable task worker
- Existing unrelated user-owned changes remained untouched.

### Verification evidence

Run on 2026-08-27:

1. focused coordinator/daily/schema/task/worker/compatibility gate: `uv run pytest tests/test_jarvis_coordinator.py tests/test_jarvis_daily.py tests/test_jarvis_schema.py tests/test_task_service.py tests/test_task_worker.py tests/test_worker_coordinator.py tests/test_voice_api.py -q`
   - Result: PASS
   - 42 passed in 18.60 seconds
2. complete backend gate: `uv run pytest -q`
   - Result: PASS
   - 216 passed in 232.80 seconds
3. metadata parity: `uv run alembic check`
   - Result: PASS
   - No new upgrade operations detected
4. frontend state/API/regression gate: `npm run test -- --run`
   - Result: PASS
   - 21 files and 122 tests passed
5. frontend lint and production build: `npm run lint` and `npm run build`
   - Result: PASS
   - Next.js 16.2.3 build and TypeScript checks completed; 30 routes generated

### Required simulated scenario evidence

- Direct shell response and delegation/immediate acknowledgement pass through the fake adapter and restricted brain gateway.
- Fast completion, slow progress/completion, and completion while the user owns the floor produce deterministic presentation state.
- Task questions are preserved and answered once through the restricted shell.
- Explicit fresh log and the existing active-task daily-rollover scenario preserve global task identity.
- Interrupted heard-prefix delivery supports a later natural continuation without exposing unheard text to hydration.
- Duplicate provider events and reordered late delivery-state events remain idempotent and diagnostic.
- Controller replacement/reconnect rejects stale generations while observer reads never take ownership.
- Progress coalescing, user-floor deferral, interrupted-presentation retry, and once-only completion are covered end to end.

### Phase 5 deviations and rationale

The Phase 1 schema already contained realtime sessions, voice events, speech deliveries, and task presentations, so Phase 5 required no Alembic revision. Metadata parity confirms no missing DDL.

The fake workspace consumes each domain through ordered cursor replay polling rather than holding three long-lived transport connections. Ordering, dedupe, independent resume cursors, and session replacement are complete; Phase 6 can carry the same contracts over its WebRTC/sideband transport without changing domain ownership.

Typed turns lazily establish a fake `typed-bridge` controller after creating the first durable log. This makes the Phase 5 workspace immediately usable without microphone/provider setup while retaining explicit controller supersession semantics.

### Checkpoint

- Status: passed 2026-08-27
- Commit subject: `feat(jarvis): add provider-independent live workspace`
- Commit hash: 71f78e97d1ca73b8b5595b12ee0ccee0ebdaf1b6
- Next entry point: Phase 6 official OpenAI Realtime documentation review, API-drift record, WebRTC call bootstrap, sideband adapter, secret-safe projections, reconnect/rotation, and fake/openai contract parity
- Blockers: none

## Phase 6 record

### Official API review and adapter deviations

Reviewed the current official OpenAI `gpt-realtime-2.1`, WebRTC, server-side controls, Realtime conversations, and VAD documentation on 2026-08-27 before product code changes.

- The unified WebRTC bootstrap is a backend-authenticated multipart request to `/v1/realtime/calls` containing `sdp` and a JSON `session` field. This matches the planned backend proxy but replaces older raw-SDP/ephemeral-key examples at the provider boundary.
- The provider call ID is returned only in the HTTP `Location` response header. The OpenAI adapter will retain it in process memory solely to bind `wss://api.openai.com/v1/realtime?call_id=...`; neither the durable public session projection nor the browser response will contain it.
- Current session configuration nests turn detection under `session.audio.input.turn_detection`, uses `output_modalities`, and names transcript lifecycle events `response.output_audio_transcript.delta` and `.done`. These are adapter-only schema details and do not change the provider-neutral coordinator contract.
- `semantic_vad` supports `eagerness`, `create_response`, and `interrupt_response`; the implementation will use low eagerness as planned. Marin remains a current recommended voice.

These are provider adapter details, not contradictions of a locked invariant, so Phase 6 continues without an architecture decision point.

### Checkpoint

- Status: passed 2026-08-29
- Starting HEAD: 71f78e97d1ca73b8b5595b12ee0ccee0ebdaf1b6
- Existing unrelated user-owned changes remain untouched
- Blockers: none

### Scope completed

- Added the backend-authenticated unified-call SDP proxy and retained the provider call ID only in adapter memory for the sideband connection and best-effort hangup.
- Added an explicit OpenAI sideband adapter with authenticated readiness, current nested Realtime session configuration, context hydration, normalized coordinator events, restricted tool-result submission, provider failure handling, and partial-call cleanup.
- Kept the OpenAI key, provider call ID, and provider error bodies out of browser-facing models and responses.
- Persisted committed provider user transcripts through the same atomic conversation/event path used by the provider-neutral coordinator.
- Added browser WebRTC offer/answer, microphone capture, remote audio, data-channel setup, teardown, bounded 0/0.5/1/2-second retry, typed fallback, failed/disconnected-peer reconnect, and 55-minute normal supersession.
- Wired `/jarvis` Arm/Sleep controls and controller-state feedback to the Realtime lifecycle without changing the fake-provider development surface.
- Added backend adapter/coordinator contracts and frontend WebRTC lifecycle tests.

### Implementation deviations and rationale

- Unified-call creation uses `aiohttp.FormData(default_to_multipart=True)` rather than the existing `httpx` client. A live provider probe on 2026-08-28 showed that the `httpx` multipart encoding used here was rejected as invalid SDP while browser-standard FormData and `aiohttp` both returned HTTP 201. This is isolated inside the provider adapter.
- The browser SDP offer is preserved byte-for-byte, including its terminal CRLF. Trimming it caused the live provider to reject the otherwise valid offer with an SDP EOF parse error.
- A successful authenticated sideband WebSocket handshake is the explicit readiness boundary. The provider did not reliably replay `session.created` to a sideband socket attached after unified-call creation, so waiting for that event made a healthy call time out. Session lifecycle events are still normalized when received.

These are provider-transport details and do not change a locked architecture invariant.

### Verification evidence

Run on 2026-08-28:

1. backend adapter/coordinator/API contracts
   - Result: PASS — full backend suite, 220 tests
2. frontend unit and state tests
   - Result: PASS — 22 files, 127 tests
3. frontend production build
   - Result: PASS — TypeScript and 30 routes
4. migration and bytecode checks
   - Result: PASS — no new Alembic operations and application modules compile
5. secret boundary
   - Result: PASS — zero `OPENAI_API_KEY` references in frontend source and zero configured-key matches in emitted frontend server/static files
6. live OpenAI call using the configured root `.env` key and an isolated silent browser track
   - Result: PASS — WebRTC/data channel and sideband connected, OpenAI produced `Phase six live smoke passed.`, the normalized assistant response was persisted, and the provider call was closed

### Manual exit evidence

- On 2026-08-29 the user confirmed that the real-device Phase 6 experience works successfully after exercising the delivered build.
- Physical microphone input, audible Realtime response, typed workspace use, and reconnect no longer block the phase gate.
- The user separately reported sentence-scale early/late transcript cutoff drift during barge-in. That does not reopen Phase 6: precise audible-history truth was deliberately assigned to Phase 7 and the current Phase 6 path contains no final playout estimator.

No new product decision or hard implementation blocker was found. Phase 6 is complete at `58b2a78`.

## Phase 7 record

### 2026-08-29 — measured heard-prefix research entry

- Starting HEAD: 58b2a78be2ea2fd8c5ff54951d0a7faf6ff37579
- User evidence: interruption can leave the durable assistant transcript more than a sentence early or late relative to what was actually heard.
- Interpretation: provider-side interruption and generated transcript completion are insufficient evidence of browser playout. The current result is expected pre-Phase 7 behavior, not the planned final accuracy.
- Research checkpoint: capture synchronized receive/play/mute/provider/truncate traces; compare Web Audio playout timing, usable WebRTC receive statistics, and provider truncation acknowledgement; retain the 250 ms transcript-age rule only as fallback.
- Acceptance clarification: a full-sentence discrepancy fails Phase 7. Deterministic trace fixtures must cover both over-commit and under-commit before the durable transcript path changes.
- Safe workspace polish started alongside the checkpoint: conversation auto-scroll, `/voice` to `/jarvis` retirement redirect, and Jarvis PWA/notification deep-link targeting.
- Architecture decisions required: none yet. Stop only if measured browser/provider capabilities contradict the locked rule that durable assistant history contains what was probably heard.

Verification on 2026-08-29:

- backend: PASS — 221 tests, Alembic metadata parity, and application bytecode compilation
- frontend: PASS — 23 test files/129 tests, ESLint, TypeScript, and 30-route production build
- retired route: PASS — production `/voice` returned HTTP 308 with `Location: /jarvis`
- cleanup: the temporary production verification server was stopped; no application service was left running

## Phase 1 entry checklist (completed)

- Read the implementation plan, this ledger, and applicable repository instructions.
- Confirm the Phase 0 docs commit and record its hash in this ledger as part of the Phase 1 commit.
- Inspect git status and preserve unrelated user changes.
- Inventory current SQLAlchemy tables, Alembic migrations, repositories, and sequence allocation tests.
- Decide the mechanical clean-baseline migration layout without reopening the locked data model.
- Add blank-database and concurrency tests before switching the active database.
- Do not delete or overwrite the Phase 0 backup.

## Phase 2 entry checklist

- Resolve and record the Phase 1 completion commit hash.
- Recheck worktree state and preserve the unrelated user-owned files.
- Implement the Europe/Amsterdam 04:00 operational-day function with DST boundary tests.
- Add a Jarvis application service that owns lazy current-log creation, rollover, explicit fresh start, and closed-log mutation guards.
- Extend shared history/API projections without changing standard-chat behavior.
- Complete the Phase 2 backend/frontend deterministic gate before starting first-class task UI work.

## Phase 3 entry checklist

- Resolve and record the Phase 2 completion commit hash.
- Define typed task/question/artifact API projections over the Phase 1 schema without reusing conversation list identity.
- Implement dispatch, cancel, answer, explicit retry, target declaration, and atomic task-state/event transitions.
- Add global task event replay and `/tasks` plus `/tasks/{id}` UI surfaces.
- Keep old background-run endpoints available only as compatibility surfaces until their later retirement phase.

## Session update template

Copy this section for each material session:

### YYYY-MM-DD — Phase N — short description

- Starting HEAD:
- Existing unrelated changes:
- Scope attempted:
- Files/subsystems changed:
- Verification commands and outcomes:
- Decisions/deviations:
- Checkpoint status:
- Commit subject/hash:
- Next entry point:
- Blockers:
