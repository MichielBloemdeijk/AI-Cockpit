# Jarvis Implementation Ledger

## Purpose

This ledger is the restart point for every Jarvis implementation session. The normative plan is [JARVIS_IMPLEMENTATION_PLAN.md](JARVIS_IMPLEMENTATION_PLAN.md). Update this file whenever a phase begins, materially deviates, passes or fails a checkpoint, or hands off to another session.

## Current position

- **Current phase:** Phase 4 — not started
- **Last completed phase:** Phase 3
- **Product implementation started:** yes
- **Next session entry point:** implement Phase 4 worker ownership, FIFO claims, runtime sink seam, attempt workspaces, heartbeats, cancellation observation, and generalized leases; recheck worktree status before editing
- **Baseline branch:** codex/jarvis-voice-poc
- **Baseline commit:** 281fc9f9cbe7fed44cc11b4b3ee7f2c7c56f2b97
- **Phase 0 completion commit:** 5b9aa8c707324d9be079266fc7ee8afc5b2134db

## Phase status

| Phase | Status | Exit gate | Commit |
| --- | --- | --- | --- |
| 0. Plan and reversible cutover preparation | Complete | Passed 2026-08-27 | 5b9aa8c |
| 1. Clean schema and ordered-domain contracts | Complete | Passed 2026-08-27 | 70c15e7 |
| 2. Standard conversations and Jarvis daily lifecycle | Complete | Passed 2026-08-27 | 8d72f15 |
| 3. First-class tasks and task UI | Complete | Passed 2026-08-27 | Phase completion commit |
| 4. Worker, runtime seam, and resource leases | Not started | — | — |
| 5. Coordinator, fake provider, and live workspace | Not started | — | — |
| 6. OpenAI Realtime and WebRTC | Not started | — | — |
| 7. Barge-in and audible-history truth | Not started | — | — |
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
- Commit hash: resolve from git history in the next phase session
- Next entry point: Phase 4 worker process, runtime context/sink extraction, FIFO claiming, heartbeats, isolated workspaces, cancellation observation, and typed resource leases
- Blockers: none

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
