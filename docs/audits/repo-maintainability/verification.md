# Verification evidence

Source candidate: `0ab419d74d1595cfc9f3f803e9c069ae8d077f9a`; clean application working tree at start, 2026-10-29. No provider requests, application-data resets, credentials changes, service enablement, device installation or production-source edits were performed by this audit.

## Executed baseline

- **Backend:** from `backend`, set `JARVIS_EVAL_NETWORK_BLOCKED=1`, then `uv run --offline pytest -m "not provider and not browser and not diagnostic" -q`. **792 passed, 2 skipped, 170.99s.** Existing pytest configuration selected parallel execution. Personal `.env` is excluded by `tests/conftest.py`. Evidence: [backend-tests.txt](evidence/backend-tests.txt). `uv --offline` used installed/cached dependencies and rebuilt the local editable package; it made no dependency-source changes.
- **Web unit tests:** `npm run test` from `frontend`. **44 files / 202 tests passed.** Evidence: [frontend-tests.txt](evidence/frontend-tests.txt).
- **Web types:** `npm run typecheck`. **Passed (exit 0).** Evidence: [frontend-types.txt](evidence/frontend-types.txt).
- **Web main build:** `node node_modules/next/dist/bin/next build`, using isolated `AI_COCKPIT_NEXT_DIST_DIR=.next-audit`, `AI_COCKPIT_TSCONFIG_PATH=.audit-tsconfig.json` copied from the tracked config, and `NEXT_TELEMETRY_DISABLED=1`. **Passed.** Direct invocation avoided the npm prebuild copying over tracked VAD assets. Evidence: [frontend-build.txt](evidence/frontend-build.txt).
- **Web companion:** `npm run build:results`. **Passed.** Evidence: [frontend-results-build.txt](evidence/frontend-results-build.txt).
- **Android:** from `android`, `.\gradlew.bat --offline :app:testDebugUnitTest :app:assembleDebug :screen-fixture:assembleDebug`. **BUILD SUCCESSFUL; 16 JVM suites / 43 tests / zero failures, errors or skips.** XML totals read from `app/build/test-results/testDebugUnitTest`. Evidence: [android-build.txt](evidence/android-build.txt).
- **Canonical web lint:** `npm run lint` entered pre-existing generated `.next-jarvis-agent-test` bundles and was stopped explicitly. **Not a pass or completed source lint.** Evidence: [frontend-lint.txt](evidence/frontend-lint.txt).
- **Source-scoped fallback lint:** invoked installed ESLint on 163 Git-tracked TS/TSX/JS/MJS/MTS files, excluding `public/vad` third-party code. **Exit 1: 3 errors / 18 warnings.** Errors occur in phone pairing and the result/task static shells (`react-hooks/set-state-in-effect`). Evidence: [frontend-lint-tracked.txt](evidence/frontend-lint-tracked.txt). This establishes a maintained-source failure, not that every warning is a behavior defect.

The isolated main-build outputs were retained under ignored `frontend/.next-verify-maintainability-audit`; generated `next-env.d.ts` now references that matching generated route definition. The temporary config is stored inside that output directory. No tracked source/config files were changed. Existing application/dev processes were not stopped.

## Deterministic defect probes

These execute current source with local substitutes; they are evidence for missing contracts, not substitutes for later integration regression tests.

- `node docs/audits/repo-maintainability/evidence/client-stale-conversation-probe.cjs`: delayed load A overwrites the already-resolved selection B using the actual transpiled loader. React/API/feed collaborators are substituted. See client review for the exact scope.
- `backend/.venv/Scripts/python.exe docs/audits/repo-maintainability/evidence/backend-lifecycle-probes.py`: AST-isolated lifecycle/control probes, no real application startup, DB or providers. See backend review for each expected diagnostic outcome.
- `backend/.venv/Scripts/python.exe docs/audits/repo-maintainability/evidence/memory-probe.py`: actual proposal PATCH body and actual temporary knowledge-file publication/listing, with fake persistence; deleted status leaves the file visible.

Probe assertions intentionally describe the existing failure. They are audit reproductions, not regression tests asserting desired behavior.

## Deliberately unexecuted acceptance

No paid Live/Codex/API calls, billable replay, microphone corpus, browser media session, emulator app installation, notification/device opt-in, or physical phone acceptance was run. The build/JVM evidence does not prove Android keyguard, screen-off, battery, OEM, WebRTC or audio acceptance. Existing historical reports retain their original scope and candidate identity.

Refactors changing those seams must first pass focused offline tests, then use the repository's targeted pilot and stable-candidate acceptance discipline. Report request purpose/counts and known usage; unavailable cost remains unknown. Documentation-only changes do not justify paid reruns.

## Coverage interpretation

`coverage.csv` inventories every tracked path at baseline, with content hashes and review depth. `structural-indexed` means parsed signatures/headings or classified source/config, not full semantic inspection. `semantic-reviewed-sections` means a reviewer read the relevant implementation/contract sections and recorded evidence. Historical records and third-party artifacts are assessed for role/provenance/navigation rather than certified line by line. Review appendices disclose deeper paths and limits. This is a repository-wide maintainability assessment with selected behavioral reproductions, not exhaustive functional/security certification.

## Systematic continuation (batches 1–7)

Same baseline `0ab419d`; the first-pass suite results above were reused, not re-run. Every probe below was re-run during batch 7 and reported the defect it targets. Each one uses temporary storage, no personal configuration and no network; `default-access-probe.py` drives the app in-process through `httpx.ASGITransport` without opening a socket.

| Probe | Finding | Result on baseline |
|---|---|---|
| `backend/.venv/Scripts/python.exe evidence/backend-lifecycle-probes.py` | B01, B02, B03 | all three `defect_reproduced: true` |
| `backend/.venv/Scripts/python.exe evidence/memory-probe.py` | X01 | approved file remains and is listed |
| `node evidence/client-stale-conversation-probe.cjs` | C01 | older load wins |
| `node evidence/web-state-probes.cjs` ([output](evidence/web-state-probes.txt)) | C10, C11, C12 | all three reproduced |
| `backend/.venv/Scripts/python.exe evidence/schema-consistency-probe.py` | P01 | 46 autogenerate differences, 8 missing FKs, orphan accepted |
| `backend/.venv/Scripts/python.exe evidence/legacy-tool-confinement-probe.py` ([output](evidence/legacy-tool-confinement-probe.txt)) | I04 | `_io.open` and shell `python -c` escape |
| `backend/.venv/Scripts/python.exe evidence/reminder-race-probe.py` ([output](evidence/reminder-race-probe.txt)) | T03 | committed cancel overwritten with `completed` |
| `backend/.venv/Scripts/python.exe evidence/default-access-probe.py` ([output](evidence/default-access-probe.txt)) | S01 | empty-password login 200; Python tool returns `42` |
| `node evidence/feed-parity-probe.cjs` ([output](evidence/feed-parity-probe.txt)) | C14 | council/usage/task/branch fields lost on timeline path |

Paths are relative to this folder; run them from the repository root with the `docs/audits/repo-maintainability/` prefix. The probes assert today's defects. After each fix, replace the probe with a proper regression test of the desired behavior rather than keeping it as a gate.

Not executed: emulator/device runs (A01 and A03 are source-confirmed only), paid provider calls, and the fresh-context reader exercises.
