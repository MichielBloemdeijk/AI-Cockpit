# AI-Cockpit / JARVIS maintainability audit

**Status: complete at the depths recorded.** All seven batches in [progress.md](progress.md) are closed. Every first-party area has an explicit read record in `evidence/review-*.json`, and the remaining gaps are listed below. The [refactor roadmap](refactor-roadmap.md) is final, with the owner's product decisions applied.

Audited on **29 October 2026**, branch **`codex/jarvis-voice-poc`**, commit **`0ab419d74d1595cfc9f3f803e9c069ae8d077f9a`** (757 tracked paths; source identity in [baseline.json](evidence/baseline.json) and [coverage.csv](coverage.csv)). The baseline was unchanged throughout. Only audit documents, inventories and probes were added; no application code, data, credentials or provider accounts were changed, and no paid or device tests ran.

## Assessment

The repo has strong behavioral foundations that are unevenly expressed. Durable task/run/attempt identities with generation fencing, revisioned result publication, the after-commit/rescan recovery model, the Live adapter boundary, orchestration's fenced authority and native audio's ordering rules are all sound, and the roadmap preserves them. The main cost is how much hidden knowledge a change needs:

- **Lifecycle transitions without one owner.** Control effects, attempt creation and status writes are repeated across modules. One writer skips the lock (T03). The status vocabulary is bare strings, which is how B01's `pause` got in.
- **Side effects hidden behind generic calls.** `append_event` runs projections; services use each other's private helpers; production imports from probe modules.
- **Two of everything.** There are two launch paths with different safety checks (S01), two conversation-feed reconstructions where the newer one lost features (C14), two wake handoff protocols running at once (C13), and two route families for the same operations (E03).
- **Contributor surface.** Docs mix history with current state, verification is spread across many entry points, harnesses inherit personal configuration, and there is no Python static checking.

The direction is **clearer ownership inside the existing application**: three named owners (task lifecycle, conversation run lifecycle, startup preconditions), narrow typed contracts at the seams, and feature-owned client screens. There is no case for a rewrite, a new service topology, a layered framework or size limits.

## Start here

The owner's decisions are recorded in the [roadmap](refactor-roadmap.md#decisions-owner-answers-2026-10-29):

- Retire the unused legacy executor and Chat-page agent; this closes I01, I02 and I04.
- Keep council mode with updated models.
- Remove unused routes.
- Reset the dev DB freely.
- Treat security as secondary unless severe or trivial. S01 is therefore documented, with an optional five-line fix.

Fix **C11** first: browser Stop leaves the microphone and WebRTC connection open until an unbounded HTTP DELETE returns.

Then the demonstrated P2 defects, each reproduced by a saved isolated probe unless noted: **B01, B02, B03, T03, C01, C10, C14, P01, X01**, plus **A01**, which is source- and library-confirmed. The full register with priorities, evidence status and phase is in the [roadmap](refactor-roadmap.md#findings-register).

| Report | Findings |
|---|---|
| [Backend review](backend-review.md) | B01–B08, P01–P03, I01–I06, T01–T03 and addenda |
| [Web and Android review](client-review.md) | C01–C14, A01–A03 |
| [Cross-system review](cross-system-review.md) | X01–X02, S01, E01–E03, O01 |
| [Onboarding review](onboarding-review.md) | ONB-01–06 and the first-pass reader exercises |
| [Architecture map](architecture.md) | Process, concept and feature reading paths |

There are 50 finding entries in total (O01, on folder organization, was added after the owner review), with disclosed thematic overlap. Reproduced defects, source-confirmed risks and design assessments are labeled separately throughout.

## Verification

- First pass, reused because the baseline didn't change: backend offline suite **792 passed, 2 skipped**; web **202 passed** across 44 files, with typecheck and both builds passing; Android **43 JVM tests passed**, with both debug builds passing; tracked-source ESLint **3 errors, 18 warnings** (the canonical lint command entered generated bundles and was stopped). Details are in [verification.md](verification.md).
- Continuation probes, all isolated (temporary DB/directories, no personal config, no network), re-run on this baseline: `backend-lifecycle-probes.py`, `memory-probe.py`, `client-stale-conversation-probe.cjs`, `web-state-probes.cjs`, `legacy-tool-confinement-probe.py`, `reminder-race-probe.py`, `default-access-probe.py` (in-process ASGI, no socket), `feed-parity-probe.cjs`, `schema-consistency-probe.py`. The probes assert the *current defects* and are evidence, not regression gates.
- Mechanical scans used to direct reading and as before/after trend measures: `private-import-scan.py`, `private-attribute-scan.py`, `long-functions-scan.py`, `route-usage-scan.py`.

## Coverage and limits

Read depth per file is recorded in `evidence/review-persistence.json`, `review-backend-interactive.json`, `review-web-state.json`, `review-android.json`, `review-durable-live.json` and `review-entrypoints.json`, alongside the first-pass `coverage.csv`. Large modules were read at entry points and decision sites chosen by the scans, not line by line. Each record says `complete`, `partial` (with line ranges), `targeted`, `structural` or `inventory`.

Not covered:

- Individual test bodies: tests are inventory-only, and their pass results are reused.
- Individual migration bodies: checked as a whole by the schema probe.
- Per-route request/response models and presenters.
- Acceptance-gate bodies.
- Shared web components beyond an unused-file scan.
- Android sources outside the core service/controller/client, which rely on the first-pass flow review.
- Any emulator, physical-device or paid-provider behavior.
- The fresh-context reader exercises planned for batch 7. These need an agent with no audit context and are scheduled in the roadmap's final assessment.

Model/runtime binaries, lockfiles and historical evidence were classified rather than reviewed. Local databases, credentials, private configuration (including the personal `.env`), generated apps and unrelated untracked files were excluded.
