# Verification

Every check in the repository, what it covers, what it costs and when to run
it. Run free checks first; paid checks investigate a specific remaining seam
after offline results look right (see the provider test rules in
[AGENTS.md](../AGENTS.md)). No CI runs these; they are run locally.

Cost: **free** (no network beyond loopback), **emulator** (free, needs the
Android SDK and the `JarvisM3Api37` API 37 image), **paid** (billed provider
calls; each is opt-in).

## Routine checks

Run these before committing a change in the matching area.

| Command (from) | Covers | Cost |
|---|---|---|
| `uv run pytest` (`backend/`) | The whole backend suite, in parallel. Tests marked `provider` are billed and skip unless their own opt-in flag is set; every other test fails if it reaches the network. | free |
| `uv run ruff check app` (`backend/`) | Python correctness lint (undefined names, unused imports and variables). | free |
| `uv run pyright` (`backend/`) | Type check of the refactored seams listed under `[tool.pyright]` in `pyproject.toml` (basic mode; currently the `tasks`, `conversations`, `results` and `updates` packages, the execution seam, `jarvis/coordinator` and `llm`). | free |
| `npm test` (`frontend/`) | All frontend unit and hook tests (Vitest). | free |
| `npm run lint` (`frontend/`) | ESLint over source; build outputs (`.next*`) are ignored. | free |
| `npm run typecheck` (`frontend/`) | TypeScript over the main app. Excludes `result-export`. | free |
| `npm run build` (`frontend/`) | Production build of the main app, needed by the personal host. | free |
| `npm run build:results` (`frontend/`) | Production build of the separate `result-export` app, the only check of that app. | free |
| `.\gradlew.bat testDebugUnitTest assembleDebug` (`android/`) | Android JVM tests and the debug APK. | free |

For a quick backend loop, pass test paths (`uv run pytest tests/test_x.py`) or
`-n 0` to run serially while debugging.

## Diagnosis

| Command (from `backend/`) | Covers | Cost |
|---|---|---|
| `uv run python -m app.selftest doctor` | Read-only findings with proposed fixes for the configured database, another `--database`, or a recorded `--run` folder. | free |
| `uv run python -m app.jarvis_tasks rollout-check --report <dir>` | Read-only preparation report for the personal host (configuration, schema, profiles). | free |

## Jarvis voice and tasks

Layers are described in [self-testing](jarvis-tasks/self-testing.md).

| Command (from `backend/`) | Covers | Cost |
|---|---|---|
| `uv run python -m app.selftest offline` | L0: a network-blocked Jarvis smoke slice, not the full suite. | free (needs the Android SDK unless `--skip android`) |
| `uv run python -m app.selftest replay --name <run> --live` | L1: bookkeeper replay of private recorded conversations. | paid (Responses) |
| `uv run python -m app.selftest run --suite core --name <run> --live` | L2: scripted voice scenarios through the real browser client. | paid (GPT-Live) |
| `... run ... --live --android` | L4: the same through the Android emulator client. | paid, emulator |
| `uv run python -m app.selftest review --folder <run> --scenario <suite:id> --live` | Agent review of a recorded run. | paid (Responses) |
| `npm run test:live-smoke` (`frontend/`) | Wake sample → real microphone stream → GPT-Live → persisted voice segments. | paid (GPT-Live) |

## Workflows and acceptance gates

| Command (from `backend/`) | Covers | Cost |
|---|---|---|
| `uv run python -m app.jarvis_tasks validate <id@version>` | A versioned workflow definition. | free |
| `uv run python -m app.jarvis_tasks fixtures <workflow> --report <dir>` | Isolated service fixtures for a reference workflow. | free |
| `uv run python -m app.jarvis_tasks eval <workflow> --live --report <dir>` | Paid native, source and revision samples of a workflow. | paid |
| `uv run python -m app.jarvis_tasks android demo --report <dir>` | Real screenshot → guarded Codex → notification-to-result navigation on the emulator. | paid, emulator |
| `uv run python -m app.jarvis_tasks acceptance --gate <gate> --report <dir>` | Candidate-bound acceptance: `coding` (Codex coding provider, process tree), `voice` (GPT-Live), `android` (emulator audio, capture, mobile result), `subscription` (subscription provider route), `release` (all of the above plus both frontend builds). Missing or skipped evidence blocks readiness. Add `--live` for the billed suites. | free without `--live`; paid with it; `android`/`release` need the emulator |
| `uv run python -m app.jarvis_tasks migration-rehearsal --report <dir>` | Migrates a disposable clone of the configured database. | free |
| `uv run python -m app.jarvis_tasks rollout-rehearsal --report <dir>` | Isolated restore, restart and disablement fixtures. | free |
| `uv run --group codex-probe python -m app.jarvis_tasks probe-codex --report <file>` | The pinned Codex SDK/App Server contract (`--live` for session persistence). | free; paid with `--live` |

Billed pytest suites run only with their flag set: `JARVIS_LIVE_CODEX=1`
(`test_codex_task_runtime_live.py`, `test_result_platform_live.py`),
`JARVIS_LIVE_CODING=1` (`test_codex_coding_live.py`), `JARVIS_WORKFLOWS_LIVE=1`
(`test_reference_workflows_live.py`), `JARVIS_SUBSCRIPTION_LIVE=1`
(`test_subscription_provider_live.py`). All are marked `provider`.

## Android emulator harnesses

Run from `backend/` with `uv run python ../android/scripts/<script>`. Each
starts its own disposable backend that never reads the personal `.env`
([harness_support.py](../android/scripts/harness_support.py)); paid ones name
the one credential they use. See [android/README.md](../android/README.md).

| Script | Covers | Cost |
|---|---|---|
| `verify-result-platform.py` | A published Document opens on the phone as an app record, including external links. | emulator |
| `verify-show-on-phone.py` | "Show me the result" switches the phone to the app. | emulator |
| `verify-self-update.py` | The in-app updater across three signed releases. | emulator |
| `verify-audio-controls.py` | Notification audio controls with real native WebRTC audio and a fake voice provider. | emulator |
| `verify-capture-feasibility.py` | Native wake, visible session and capture privacy cases. | emulator |
| `verify-phone-browser-capture.py` | Paired browser → native capture → run-bound upload. | emulator |
| `verify-screen-workflow.py` | Screenshot → admission → Codex worker → result (`--orchestration` adds background decisions). | paid, emulator |
| `verify-managed-admission.py` | Managed GPT-Live task admission (`--mobile-result` adds the spoken research flow). | paid, emulator |
| `verify-audio-corpus.py` | Recorded audio through real wake inference and Live WebRTC against a prepared backend. | paid, emulator |
| `verify-announcements.py` | Driver for the L4 self-test (started by `app.selftest run --android`). | paid, emulator |

## Not covered autonomously

Physical-phone behavior (real audio, battery, OEM restrictions, lock screen)
is checked by hand at the end; see the
[phone guide](jarvis-tasks/phone-test-checklist.md).
