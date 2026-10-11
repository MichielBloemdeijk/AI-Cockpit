# Managed-only task routing — 2026-10-06

## Decision and implementation plan

The user selected managed routing after the matched same-input comparison.
Managed averaged 3.734 seconds versus 5.123 seconds for client routing across
two samples each. That supports this architecture choice, not a p95 claim.
The comparison remains [historical evidence](admission-routing-paired-2026-10-05.md).

The implementation follows three steps:

1. Make managed Responses delegation the single session configuration. Remove
   the application-owned selector request loop, copied conversation history,
   client result futures and client-specific confirmation path.
2. Keep completed-call schema validation, originating-turn correlation,
   durable gateway receipts, idempotency, capture preflight and managed result
   continuation. Selection overlaps transcript correlation; task admission
   still needs verified ownership. Do not add a routing fallback.
3. Replace dual-route profiling and acceptance with managed-only consumers,
   update executable candidate settings and the shared profile manifest, and
   verify locally before any further paid provider run.

All three implementation steps are complete. The existing GPT-Live voice
backend, Luna model/effort/tier, workflow tools and durable task contracts remain
in use. Managed selection owns its conversation input on OpenAI's side; Jarvis
does not construct another selection request from a settled transcript.

## Simplified task path

GPT-Live delegates to managed Luna → completed streamed tool item → local JSON
and frozen-catalog validation → correlated task gateway → durable task/result
receipt → managed function result → managed continuation and spoken response.

The client HTTP selector, two-second correlation polling loop, bounded copied
history, client result futures and mode-specific task-update/result handling
were removed. `OPENAI_LIVE_DELEGATION_MODE` and the `emulator_client` acceptance
profile no longer exist. No data migration or application-data reset was needed.
Existing source, credentials and account configuration were preserved.

`android/scripts/verify-managed-admission.py` replaces the comparison launcher.
It starts one isolated backend and one audio corpus, retains pilot caps and
cleanup, and writes `summary.json` directly in the requested report directory.
There is no `--mode` argument or old-launcher compatibility alias. Full M10
still executes audio and capture performance explicitly.

The model-output analyzer now recovers completed stream items when a managed
terminal envelope has `output: []`. Reanalysis of retained managed output
recovered the dispatch arguments without any provider request. Runtime dispatch
uses completed stream items, never terminal-array reconstruction.

## Local validation and timings

No paid API, worker or background-model calls were made for this change.
Configuration and transport are mocked in adapter tests. Gateway, managed
routing and capture profiling explicitly reject socket network access.

The initial focused suite passed 72 checks. A broader run passed 83 checks and
failed two: the result-delivery profiling comparison dropped two best-effort
observations on its existing 250 ms write deadline, and overlapping-screen
collection hit `SQLITE_BUSY` while updating device activity. Both isolated
checks subsequently passed together (2 passed); no retries were added to the
runtime and neither original failure is erased. This is residual SQLite/host
contention evidence, not proof that those cases are reliably solved.

The new single-backend/single-corpus launcher tests initially had an incorrect
mock loader; that fixture was repaired. The complete harness suite then passed
24 checks, including both full-corpus and capped-pilot launcher paths and cleanup.
The final combined adapter, managed delegation, diagnostics, harness, acceptance,
coordinator, intent, task and screen-collection suite passed **129 checks in
121.59 seconds**. The original contention failures remain recorded separately.

Completed managed profiles retain 20 samples each, with three excluded warmups:

- Delegation to simulated client SSE receipt: median **662.19 ms**, p95
  **1114.66 ms**, maximum **1476.28 ms**.
- Boundary/correlation and pre-tool handling: p95 **469.56 ms**.
- Completed tool validation: p95 **0.33 ms**.
- Validated call to shell: p95 **267.26 ms**.
- Session/device authority: p95 **82.18 ms**.
- Gateway: p95 **621.03 ms**.
- Gateway return to journal/adapter return: p95 **43.28 ms**.

These are nested and overlapping distributions; do not add their p95 values.
The full local fixture includes the real journal/SSE generator and zero-delay
provider events. It excludes real provider scheduling/generation, voice ASR,
HTTP framing, transport and Android receipt. It also excludes screen capture
latency from this ordinary-task case. These measurements are not a demonstrated
improvement over previous runs on a differently loaded host.

[The detailed local timing report](managed-offline-timings-2026-10-06.md) retains
gateway categories and stage distributions. Raw profiles are under
`data/managed-routing-only-2026-10-06/`; the separate successful contention check
uses the `isolated-check` prefix. The original broader run remains a failed run,
even though its completed managed routing profiles have zero admission failures.

## Acceptance and next step

Runtime changes create a new executable candidate. The earlier live comparison
supports selecting managed routing but cannot pass acceptance for this candidate.
M8/M10 remain open. Required verification is one frozen managed cohort with at
least 20 ready admissions, five independent cold starts, functional continuation,
clarification, cancellation, screen and recovery cases, plus the remaining M10
provider/browser/backend/frontend/Android assertions. M11 physical devices stay
deferred.

No account action or new credits are required to apply this change. A running
backend must be restarted to create new sessions using the current code. Preserve
the supported subscription profile login for eventual real worker acceptance.
Further paid testing should investigate a specific remaining seam after offline
results are stable. Managed provider request-start/prefill timestamps remain
unexposed; response-created and stream arrival are the available observations.
