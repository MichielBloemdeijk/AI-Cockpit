# Cross-system findings

Baseline: `0ab419d`, 2026-10-29. These findings supplement, rather than duplicate, the subsystem reviews.

## X01 — Reviewed-memory lifecycle has multiple mutation authorities

**Priority:** high; correctness and ownership. **Confidence:** reproduced in an isolated current-source probe, with fake persistence and real temporary publication files; not an HTTP integration test.

`backend/app/api/memory.py:32-50` accepts any existing memory row, including approved ones, and directly changes status/content. Unlike `backend/app/api/knowledge.py:76-87`, it does not remove published knowledge when deleting. A later normal delete only removes the file if status is still approved. `knowledge_memory.py:119-146` lists files independently of DB status; `brain_retrieval.py:65-72` searches that listing.

Reproduction: `backend/.venv/Scripts/python.exe docs/audits/repo-maintainability/evidence/memory-probe.py` prints `PATCH status=deleted; approved file remains=True; knowledge listing includes it=True`. No personal files, app DB or provider are touched.

**Consequence:** a user-visible withdrawal can leave content available to knowledge retrieval. Editing an approved row can likewise make the row disagree with its publication. Similar approve/reject implementations in `api/conversations.py:450-482` add another place to reason about this.

**Recommendation:** one reviewed-memory application service owns allowed transitions and file publication/removal; routes delegate. Proposal edits must reject approved rows until they explicitly return to review. Reject/delete of approved items must withdraw their publication through that same service. Retain provenance and tombstones. Do not solve this by hiding files only in the review UI. Define reconciliation/retry behavior for file-success/DB-failure and DB-success/file-failure, since SQLite cannot transact the filesystem.

**Acceptance:** exercise proposal edit, approval, repeated approval, approved edit rejection, rejection/withdrawal, deletion through every maintained route, file/DB failure recovery, and retrieval after withdrawal in isolated storage. Existing knowledge happy-path coverage is not proof of these cross-route transitions.

## X02 — Verification artifacts cross contributor-tool boundaries

**Priority:** medium; developer experience. **Confidence:** observed command behavior and configuration.

`frontend/eslint.config.mjs:9-15` excludes only selected build directories; `frontend/.gitignore` excludes additional acceptance outputs. `frontend/tsconfig.json:31-32` includes a specific acceptance build's generated types. Running `npm run lint` traversed pre-existing `.next-jarvis-agent-test` bundles and third-party compiled code; it was explicitly stopped. A tracked-file fallback completed and found 3 errors / 18 warnings in maintained code (see verification).

**Recommendation:** make normal lint/typecheck scope independent of accumulated test outputs. Keep acceptance/build tsconfigs separate and make generated-directory naming/ignore policy consistent. Do not globally suppress the reported rules to make the gate green. Validate from both a clean source tree and a tree with acceptance artifacts present.

## Batch 6 findings: entrypoints, CLIs, scripts and tooling

Read records: [review-entrypoints.json](evidence/review-entrypoints.json).

### S01 — P1, security: the documented default setup gives any network peer an owner session, and through it code execution

- Evidence: `.env.example:35-37` ships `JARVIS_ACCESS_MODE=personal` and an empty `AUTH_PASSWORD`. In that mode `verify_password("")` is true (`services/auth.py:23-28`), so `POST /api/auth/login {"password": ""}` returns a full owner session to any client (`api/auth.py:26-41`). The README quickstart (`README.md:96-120`) starts `start-backend.ps1`, which runs `uvicorn --host 0.0.0.0`, and `npm run dev`, which runs `next dev --hostname 0.0.0.0`. `config.cors_origins` and `lib/dev-origins.ts` deliberately admit the host's LAN IPv4 addresses, so LAN access is the designed dev experience.
- Reproduction: [default-access-probe.py](evidence/default-access-probe.py) runs in-process through `httpx.ASGITransport` (no socket, fresh temporary DB). An unauthenticated list returns 401; an empty-password login returns 200; the caller then creates a conversation and runs `python_execution` with `print(6*7)` → `42` ([output](evidence/default-access-probe.txt)). Combined with I04, that code is not confined to the workspace.
- Scope: this is the dev path. `personal_host.py` binds loopback only behind Tailscale Serve, refuses the default secret, and verifies listener ownership, so the prepared personal host is not exposed this way. `setup-jarvis.ps1` only warns about the empty password. Whether the personal `.env` sets a password was not checked; the audit doesn't read it.
- Recommendation: the dev launchers bind `127.0.0.1` by default, with LAN as an explicit opt-in. Empty-password personal mode is accepted only for loopback clients; any other origin requires a password or a device session. `Settings` refuses the placeholder secret (P02). Do this before the I04 fix, which it makes reachable.
- Acceptance: the probe, run with a non-loopback client address, gets 401 from login; loopback dev still works without a password; personal-host behavior is unchanged.

### E01 — P2: two launch families with different guarantees

`Jarvis.cmd` → `scripts/jarvis.ps1` → `personal_host serve` is the guarded production-like path: one host, loopback, Tailscale, Codex pool, prebuilt frontend. `start-backend.ps1` + `scripts/start-worker.ps1` + `start-frontend.ps1`, and the equivalent VS Code tasks, are the dev path with none of those checks and the legacy executor from `.env.example`. ONB-03 noted that the docs blur the two. In code, the difference is that every safety precondition lives in `personal_host.serve` instead of in `Settings` or app startup, so it applies to only one of the two. `scripts/start-jarvis-local.ps1`/`stop-jarvis-local.ps1` are three-line aliases of `jarvis.ps1`. **Direction:** move the configuration preconditions (secret, access mode versus bind address, executor/pool pairing) into one startup check run by both paths; the personal host keeps only its host-specific checks (Tailscale, listener ownership, single instance). Delete the alias scripts.

### E02 — P2: verification is spread over many entry points with historical names

- Two CLIs: `python -m app.selftest` (offline, replay, doctor, review, run) and `python -m app.jarvis_tasks` (subscription-login, rollout-check, `acceptance --gate M6…M10`, rehearsals, validate, fixtures, eval, android demo, probe). Then 11 `android/scripts/verify-*.py` harnesses, `frontend/scripts` and `frontend/e2e` drivers, and backend pytest. ONB-04/05 found the docs don't say which to run when; this adds that the code surface itself has no index or shared runner.
- Milestone names are part of the interface: `M5`–`M10` appear in 12 tracked files, including CLI choices (`jarvis_tasks/__main__.py:26`), `acceptance-profiles.json` and `android/scripts/m5_result_fixture.py`. A newcomer needs the project history to know what "M8" verifies.
- Five root `scripts/*.py` analysis tools (`summarize-admission-*`, `compare-admission-telemetry`) are referenced only by dated October reports; `frontend/scripts/verify-personal-link.mjs` has no reference.
- The self-test hooks Live traffic by replacing private methods of production singletons during its lifespan (`selftest/host.py:174-178`: `openai_live_adapter._send`, `._handle_event`, `.connect`, `OpenAILiveAdapter._session_config`, `jarvis_coordinator._trace_response_gate`). Any rename in the adapter silently stops recording. The adapter already accepts a `LiveModelDiagnostics` observer; the recorder belongs on that kind of explicit hook.
- **Direction:** one verification index (a doc table, or `python -m app.selftest list`) mapping each command to scope, cost (free / paid / emulator) and when to run it. Name gates by what they verify, not milestone numbers. Move experiment summarizers next to their reports or delete them. Give the adapter and coordinator an explicit observer interface for the recorder.

### E03 — P3: tooling gaps and unused API surface

- No Python linter or type checker is configured (`backend/pyproject.toml` has only pytest settings). B07, T01 and I02 all recommend typed contracts; without a checker, nothing enforces them. Start narrow: ruff, plus mypy or pyright on the lifecycle and execution seams only.
- [route-usage-scan.py](evidence/route-usage-scan.py) imports the app (no lifespan) and searches first-party clients for each of the 110 `/api` routes. 21 have no client match, and 16 of those have no test match either ([output](evidence/route-usage-scan.json)). It matches by path, not method, and dynamic URL building can hide callers, so these are candidates rather than proof. Notable groups:
  - Duplicate route families: `/api/app-catalog/{slug}/storage-capability` (+ `/revoke`), while generated apps call `/api/apps/{id}/storage-capability`. Conversation-scoped memory approve/reject, while the UI uses the knowledge routes. `GET/PATCH /api/memory/proposals` (X01).
  - Reminder REST CRUD (5 routes): the voice brain calls `task_service` directly.
  - `POST /api/tasks/{id}/retry`: the UI uses `/control` (C06). `/api/retrieval/history` and `/rebuild`, `/api/jarvis/tasks/resolve`, `/api/jarvis/diagnostics`, `/api/jarvis/live/observer`, `PUT /api/worker-tools/permissions`, `/api/orchestration/{id}/inspect`.
- Under AGENTS.md, remove unused routes with their tests after a quick owner check, or keep them deliberately with a named consumer. Each one is surface that S01-style access exposes.

### O01 — P2: Folder structure doesn't show ownership

Added after the owner review, when checking that the audit covered code organization as well as behavior. `backend/app/services/` is one flat folder of 105 modules. Ownership is visible only in filename prefixes: 23 `task_*`, 13 `jarvis_*`, 7 `conversation_*`, 7 `agent_*`, 6 `orchestration_*`, 5 `app_*`, plus singletons such as `commit_signals`, `phone_display` and `screen_look`. `frontend/lib/` is likewise flat, with 43 modules, 17 of them `jarvis-*`. Nothing tells a reader which modules form a feature, which are its public entry points, or which are internal helpers. That gap is part of why private cross-module imports (I01, B04) went unnoticed. `db/repositories` has only 3 repositories against 54 modules that open sessions directly, so the `db/` versus `services/` split doesn't express ownership either.

**Direction:** feature packages that match the owners in the roadmap (`services/tasks/`, `conversations/`, `jarvis/`, `orchestration/`, `results/`, `knowledge/`), each with a small `__init__` naming its public operations. Create them as each owner is extracted in phases 4–5, not as a separate mass move, which would churn every import with no ownership change. Don't impose layers beyond that.

## Positive patterns to carry forward

- `CommitSignal` documents why notifications are hints, with durable rescans as recovery authority; preserve that model when moving commit wiring.
- Typed result registrations and run/revision fencing already provide a useful extension seam. Repair consumer typing/registry dispatch rather than replacing the result platform.
- Conversation hooks and native pure policies demonstrate smaller responsibility owners already working in this repo.
- Explicit tool read/write roots, device-origin constraints, generation/claim checks and process-tree containment are functional boundaries; generic simplification must retain them.

## Root semantic review coverage

Full small modules or relevant lifecycle sections were read in: `backend/app/db/session.py`, `backend/app/db/repositories/tasks.py`, `backend/app/services/commit_signals.py`, `backend/app/services/agent_execution.py`, `backend/app/services/result_static.py`, `backend/app/api/results.py`, `backend/app/services/task_result_contract.py`, `backend/app/services/task_result_types.py`, `backend/app/services/agent_tools/base.py`, `backend/app/api/chat.py`, `backend/app/api/knowledge.py`, `backend/app/api/memory.py`, `backend/app/api/conversations.py`, `backend/app/services/knowledge_memory.py`, `backend/app/services/memory_review_store.py`, `backend/app/services/brain_retrieval.py`, `backend/app/services/conversation_store.py`, `backend/app/services/chat_settings.py`, `backend/app/services/jarvis_brain.py`, `backend/app/services/run_supervisor.py`, `backend/app/services/run_lifecycle.py`, `backend/app/services/app_registry.py`, `backend/app/main.py`, `backend/app/worker.py`, `backend/tests/conftest.py`, `backend/app/selftest/__main__.py`, `frontend/lib/hooks.ts`, `frontend/lib/use-conversation-detail.ts`, `frontend/lib/use-conversation-actions.ts`, `frontend/scripts/copy-vad-assets.mjs`, `frontend/next.config.ts`, `frontend/result-export/next.config.ts`, `frontend/eslint.config.mjs`, `frontend/tsconfig.json`, `frontend/package.json`, `backend/pyproject.toml`, `android/build.gradle.kts`, `android/gradle.properties`, `.gitignore`, `frontend/.gitignore`, `AGENTS.md`, `frontend/AGENTS.md`.

Larger modules in this list were reviewed by responsibility/critical section, not certified line by line. Symbol inventories additionally cover the broader maintained tree.
