# M4 acceptance — 2026-10-01

Historical checkpoint: M4 is complete for the guarded API-authenticated
Windows/API37 emulator route. At this checkpoint the M5 platform was still
pending; it has since passed. Remaining implementation is now M6–M10 and physical
rollout is deferred M11 under the [roadmap](../00-implementation-roadmap.md).
Research/workflow production enablement was off; these original results do not
establish custom-wake, subscription, account cutoff or physical acceptance.

## Implemented scope

Independent input/capability/result registries and compatible v2 workflow
manifests now drive catalog metadata and exact worker tools. Shared history/image,
research, clarification, finish and publication capabilities serve both reference
definitions. Research journals discovery and independent concurrent public reads;
successful evidence is reusable across runs. DNS/address/redirect, response-size,
deadline and credential-bearing URL safeguards stay in the common reader.

The minimal recipe type is registered alongside documents. One common service
allocates stable URLs, validates evidence ownership, atomically publishes immutable
revisions and completes runs. Updates require the expected base revision; replay
requires exact arguments and original attempt authority. Failed and confirmed
cancelled updates preserve the previous content. A clarification ends its asking
attempt, and an early answer cannot turn stream drainage into lost authority.

`research_screen@1` uses the same services and document renderer. Its package
contains only a manifest, instructions and compact fixture index: no additional
transport, admission, scheduler, routing-prompt or result-lifecycle code.
Recipe-specific executable code is only the small schema and renderer.

## Verification

- Full backend regression: **447 passed, nine opt-in skips**. Subsequent targeted
  regression includes source-URL repair diagnostics and queued/running cancelled
  update preservation.
- Full frontend suite: **166 passed**, including safe recipe/document rendering.
  Frontend production build also passed. Standalone `tsc --noEmit` still reports
  existing unrelated test/e2e fixture type errors; the production build passes its
  TypeScript check.
- Both versioned definitions pass strict validation. Six reference scenarios
  run through real isolated task/result services: clear result, clarification,
  partial/all source failure, invalid-output correction, follow-up and preserved
  failed/cancelled update. Shared security/recovery suites retain broader coverage.
- Final real-provider set: tomato pasta and lentil soup for each definition;
  **four initial publications and four same-task revisions** passed. All four
  revisions reused sources without an additional search request. Worker model
  was `gpt-5.6-luna`, low effort; search was `gpt-5.5`, low effort, using the
  separately billed Responses `web_search` capability. No production judge call.
- Both actual API37 emulator examples passed native screenshot, upload identity,
  guarded Codex execution, sourced publication, completion notification, phone
  WebView rendering and force-stop/reconnect without another worker attempt.
  Admission uses fake Live and system-assistant invocation, not microphone audio.

See [redacted machine-readable evidence](m4-current.json). Native reports and
private profiles/evidence remain under ignored `backend/data/`; raw captured
pixels, source bodies and credentials are not committed. Future eval reports
retain a private source-evidence sidecar so pytest's temporary-database cleanup
does not discard the material needed for an agent quality review.

## Timing and usage

Each native sample starts a fresh task/profile and App Server, then measures a
same-task follow-up with retained profile/context. An App Server still starts
per attempt; this is not evidence of a resident prewarmed model process.

- Recipe initial cold execution: median **25.63 s**, nearest-rank p95 **27.94 s**,
  n=2; individual samples 23.33 and 27.94 s.
- Research initial cold execution: median **36.40 s**, p95 **37.41 s**, n=2;
  individual samples 35.39 and 37.41 s.
- Retained-context follow-up: recipe median **12.59 s**, p95 **12.89 s**, n=2;
  research median **14.39 s**, p95 **17.25 s**, n=2.
- Emulator admission-to-ready: recipe **34.17 s**, research **26.27 s**, n=1 each.
  API37 reports retain preflight, capture, upload, queue and active-execution
  stages independently. Ready-call admission itself was 63 ms in both runs.

These small samples establish functionality, not production latency percentiles.
The thirty-second first-result goal remains soft and is not universally met.
Reports retain actual outer-worker token snapshots and search model/tool usage.
Native `total` is cumulative per thread; resumed attempts' totals must not be
added together. `last` is the last model-call snapshot, not whole-turn usage.
Provider invoice cost is unavailable, not zero; no pricing estimate is invented.

## Quality assessment and defects found

Method: implementation agent reviewed canonical initial/revised content against
retained tool evidence/excerpts and independent re-reads of the same public
sources. Rubric: useful completeness, source support for material facts/amounts,
explicit constraint handling, readable Unicode and truthful image uncertainty.
This is a small agent assessment, not a calibrated judge or human culinary review.

The final one-pan pasta preserves the fetched PBS quantities and preparation,
with cheese explicitly optional and vegetarian-labelled. The lentil recipe
matches the Nutrition.gov quantities and method and uses vegetable broth.
Research results provide useful preparation/background with linked fetched
evidence; the lentil result distinguishes a documented Egyptian tradition from
the unknown style of the pictured menu dish. Ordinary wording revisions preserve
facts and constraints. Approximate total timing remains an agent estimate where
the source specifies cooking steps but not a total time.

Earlier failed or questionable runs were retained separately, not silently
averaged into the final passing sample set:

- PDF-heavy discovery returned inaccessible/unsupported candidates. Discovery now
  prefers cited readable pages, filters obvious non-text formats and reports
  individual fetch failures. It does not bypass paywalls or promise every site
  is fetchable.
- Duplicate tracking variants confused source-ID/URL pairing. Tracking-only query
  parameters are removed; functional queries remain. Publication still rejects
  wrong evidence identities and returns the exact retained URL for repair.
- Incoming native MCP Unicode was decoded with Windows' legacy code page. Stdio
  now explicitly uses UTF-8; a subprocess test forces cp1252 and verifies a
  Unicode round trip. Misdeclared source charsets are also covered.
- Reasoning consumed a too-small discovery response budget. The output allowance
  is bounded at 4,000 tokens; completed search URLs can survive a truncated final
  discovery answer. Incomplete discovery without candidates fails and retains
  usage. No automatic billable retry or unsupported result is introduced.
- A prior vegetarian recipe prescribed ordinary Parmesan with a caveat. Reference
  guidance now requires vegetarian-labelled cheese or omission. This is ordinary
  instruction-level constraint handling, not a dietary substitution feature.
- One exploratory research run clarified an uncertain image. The opt-in fixture
  can answer such questions from its authoritative input without a person; this
  does not enable production automatic answers.

## Reproduction

From `backend`, after the existing host/API and emulator prerequisites:

```powershell
uv run python -m app.jarvis_tasks validate recipe_from_screen@1
uv run python -m app.jarvis_tasks validate research_screen@1
uv run python -m app.jarvis_tasks fixtures recipe_from_screen@1 --report data/m4-recipe-fixtures.json
uv run python -m app.jarvis_tasks fixtures research_screen@1 --report data/m4-research-fixtures.json
uv run pytest -q -W error::pytest.PytestUnhandledThreadExceptionWarning --tb=short
```

Paid/native and emulator runs are explicit opt-ins. Use fresh eval directories;
the tools return nonzero on missing or failed required evidence:

```powershell
uv run python -m app.jarvis_tasks eval recipe_from_screen@1 --live --samples 2 --report data/m4-recipe-eval-new
uv run python -m app.jarvis_tasks eval research_screen@1 --live --samples 2 --report data/m4-research-eval-new
uv run python -m app.jarvis_tasks android demo --workflow recipe_from_screen@1 --report data/m4-recipe-android-new.json
uv run python -m app.jarvis_tasks android demo --workflow research_screen@1 --report data/m4-research-android-new.json
```

All of these run without human input on this configured host. No new installation
or user choice was required for M4. Physical microphone/wake/OEM/lock-screen/
battery testing cannot be established by an emulator; it remains deferred to M5.
M5 also retains the full generic catalog, collection search/pagination, registered
actions, namespaced storage and progressive production rollout. Subscription
execution and enforced account/provider budgets retain their separate gates.
