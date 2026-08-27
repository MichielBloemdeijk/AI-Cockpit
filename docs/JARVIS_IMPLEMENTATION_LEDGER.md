# Jarvis Implementation Ledger

## Purpose

This ledger is the restart point for every Jarvis implementation session. The normative plan is [JARVIS_IMPLEMENTATION_PLAN.md](JARVIS_IMPLEMENTATION_PLAN.md). Update this file whenever a phase begins, materially deviates, passes or fails a checkpoint, or hands off to another session.

## Current position

- **Current phase:** Phase 2 — not started
- **Last completed phase:** Phase 1
- **Product implementation started:** yes
- **Next session entry point:** implement the Phase 2 standard-conversation and Jarvis daily-log lifecycle on the Phase 1 repositories; recheck worktree status before editing
- **Baseline branch:** codex/jarvis-voice-poc
- **Baseline commit:** 281fc9f9cbe7fed44cc11b4b3ee7f2c7c56f2b97
- **Phase 0 completion commit:** 5b9aa8c707324d9be079266fc7ee8afc5b2134db

## Phase status

| Phase | Status | Exit gate | Commit |
| --- | --- | --- | --- |
| 0. Plan and reversible cutover preparation | Complete | Passed 2026-08-27 | 5b9aa8c |
| 1. Clean schema and ordered-domain contracts | Complete | Passed 2026-08-27 | Phase completion commit |
| 2. Standard conversations and Jarvis daily lifecycle | Not started | — | — |
| 3. First-class tasks and task UI | Not started | — | — |
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
- Commit hash: resolve from git history in the next phase session
- Next entry point: Phase 2 operational-day function, transactional current-log resolution, and lazy first durable turn
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
