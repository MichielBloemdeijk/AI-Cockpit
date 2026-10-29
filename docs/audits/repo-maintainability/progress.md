# Systematic audit progress

The first pass produced useful hypotheses, selected reproductions and baseline checks. It did not complete the agreed full audit. This continuation uses explicit per-file/range records instead of deriving review status from a path appearing in a report.

Baseline remains `0ab419d74d1595cfc9f3f803e9c069ae8d077f9a`. All 757 tracked file hashes will be checked when reconciling batches. Existing same-candidate offline evidence is reused; no paid reruns or application refactors are part of this audit.

## Batches and order

1. **Configuration, persistence and schema:** settings, DB/session/repositories, schema models, migrations and relevant recovery/DB tests. **Done** ([review-persistence.json](evidence/review-persistence.json)); findings P01–P03 in the backend review. The individual migration bodies are inventory-only: their combined effect is checked mechanically by the schema probe, and P01 recommends replacing them.
2. **Interactive backend:** compatible providers, conversation execution/history/compaction, tools, generated apps/catalog and knowledge; corresponding tests. **Done at stated depth**: every file in the batch now has a record. Large modules are `partial` with ranges; tests are `inventory`. Findings I01–I06 and an X01 addendum.
3. **Web state and presentation:** complete audio/wake pipeline, replay/history/hooks/presenters, shared UI/settings and corresponding tests. **Done at stated depth** ([review-web-state.json](evidence/review-web-state.json)): C10–C13, a C01 addendum, an I05 addendum, and C08 resolved (legacy inference is test-only). Shared components and settings screens are covered by an unreferenced-module scan and a skim, not a full read.
4. **Android:** complete production/debug/platform source, JVM tests and harness isolation. **Done at stated depth** ([review-android.json](evidence/review-android.json)): `LiveController` and `JarvisAudioService` read in full; API client, event stream, updater and assistant session at their decision sites; tests and harnesses inventoried and checked for isolation. Findings A01–A03. The remaining main sources rely on the first-pass flow review. Nothing was run on an emulator or device.
5. **Durable task and live backend:** finish all previously sampled lifecycle/control/worker/orchestration/Live code and its tests. **Done at stated depth** ([review-durable-live.json](evidence/review-durable-live.json)): reading was directed by mechanical scans (private access, long functions, status literals/assignments, attempt construction, task tracking). Findings T01–T03 (T03 reproduced) plus B04/B08 addenda. Large handlers already covered by B05/B06 were not re-read; tests are inventory-only.
6. **Remaining entrypoints and tooling:** route/model/presenter reconciliation, acceptance/selftest CLIs, scripts/build/configuration, remaining web screens and test coverage. **Done at stated depth** ([review-entrypoints.json](evidence/review-entrypoints.json)): launchers mapped, personal host read in full, CLIs inventoried, scripts reference-checked, route usage scanned mechanically. Findings S01 (reproduced in-process), E01–E03. Not done: per-route model/presenter reading and individual acceptance-gate bodies.
7. **Cross-system reconciliation:** resolve duplicate/contradictory findings, complete maintenance exercises with fresh context, update target direction and order refactors. **Done**, except the fresh-context exercises. 49 findings were reconciled into one register with final priorities, evidence status and phase ([refactor-roadmap.md](refactor-roadmap.md)). Closing I05's open condition found and reproduced C14 and reversed I05's recommendation. C08's open question was closed in batch 3. Overlaps are mapped to three owners (task lifecycle, conversation run lifecycle, startup preconditions). Five product decisions gate phases 2 and 6. The fresh-context reader exercises need an agent with no audit context and are moved to the roadmap's final assessment.

## Final status

All batches are closed at the depths their records state; see the README's *Coverage and limits*. The completion rule above is met with these explicit, justified gaps: test bodies, migration bodies, per-route models, acceptance-gate bodies and some Android/web sources were inventoried or scanned rather than fully read, and no device or paid-provider behavior was checked.

Batch reports identify complete reads, partial ranges and outstanding files. `review-*.json` evidence records path, depth, actual line ranges and notes; only explicit records may upgrade systematic coverage. A complete read establishes maintainability inspection, not mathematical correctness or device/provider acceptance.

## Re-verification of the first pass (continuation, same baseline)

HEAD is still `0ab419d`; only `docs/audits/` is untracked. All saved probes were re-run and the cited source was re-read by a fresh reviewer rather than trusted from the earlier reports.

| Finding | Result | Notes |
|---|---|---|
| B01 | Confirmed | `codex_task_runtime.py:488` still sends `pause`; `CONTROL_ACTIONS` has `suspend`. No test references the `tool-policy-revoked` path. |
| B02 | Confirmed | `agent_runner.py:554-618` runs after the claim and outside the `try` at 619; `run_supervisor._on_done` discards the retrieved exception. |
| B03 | Confirmed | `main.py:62-77` starts components before the `try` at 78. |
| C01 | Confirmed | `use-conversation-detail.ts` loader commits unconditionally; probe output unchanged. |
| X01 | Confirmed, **scope corrected** | The `PATCH /api/memory/proposals` route has no first-party caller (UI, Android, tools); it is reachable only through the authenticated API. A wider instance exists: both reject routes (`api/knowledge.py:58-64`, `api/conversations.py:473-482`) set `rejected` on an approved row without withdrawing its published file. The shipped Wiki UI only offers Reject on the proposed queue, so the leak is API-reachable rather than UI-reachable. Priority lowered from high to medium; the ownership recommendation stands. |
| B05 | Confirmed, **evidence extended** | Private probe helpers are also imported by `jarvis_tasks/native_boundary.py:11` and `jarvis_tasks/orchestration_host.py:145`. |
| B08, C06, C08, ONB-02 | Confirmed by spot check | Aliases, summary-text test, self-only gate callers and the rejected hash task are all still present. |
| Schema probe | Reproduced | Same content; only list order varied between runs. Written up as new finding P01 (batch 1). |

Test/build results from the first pass are reused: the baseline commit is unchanged.

## Completion rule

The full audit is not complete until every maintained first-party file has an explicit disposition, all partial source-review gaps are closed or specifically justified, every subsystem/contract has been reconciled, and the final limitations are stated. Historical evidence, dependencies, generated assets and binaries require role/provenance classification rather than pretending to inspect their internals as application code.
