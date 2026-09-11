# Jarvis workflow implementation plan

Status: architecture recorded incrementally on 2026-09-10/11; feature implementation has not started in this documentation pass. Scope: [agreed framework plan](JARVIS_WORKFLOW_FRAMEWORK_PLAN.md).

**2026-09-11 update:** The user requested a GPT-Live 1 assessment before continuing. See [the assessment and recommended migration gate](JARVIS_GPT_LIVE_ASSESSMENT.md). D1-D7 below describe the Realtime-grounded design already investigated; D4 is a fallback design, not the proposed Live implementation. D8 remains pending the provider decision. No runtime provider was changed.

This document records decisions against the current working tree, including existing uncommitted voice-latency work. Those changes must be preserved. Each decision will include implementation contracts, integration points, failure behavior, and verification before the next decision is developed.

## Decision progress

- [x] D1. Workflow packages, shared capabilities, and routing
- [x] D2. Durable task state, interactive worker ownership, and recovery
- [x] D3. Android activation context, transport, and lifetime
- [x] D4. Realtime dispatch and acknowledgment overlap
- [x] D5. Model/search adapters and bounded reference workflows
- [x] D6. Result identity, revisions, rendering, and notifications
- [x] D7. Codex authoring, Android emulator demos, and evidence
- [ ] D8. Migrations, delivery batches, rollout, and acceptance

## Planning log

- 2026-09-10: Saved the agreed plan and initialized this implementation plan before architectural elaboration. Implementation has not started. Existing repository modifications are unrelated to this documentation pass.
- D1 resolved: use source-controlled Python workflow packages, a shared capability registry, and one generic voice entry point. No dynamic code installation or runtime workflow generation.
- D2 resolved: retain the task state machine, introduce lane-scoped worker ownership, and keep context-pending tasks out of worker claims. Global resource leases and publication fencing remain mandatory across lanes.
- D3 resolved: use opt-in, activation-scoped Android assistant snapshots with authenticated device binding, targeted replayable requests, and an upload readiness barrier. No MediaProjection or desktop helper in v1.
- D4 resolved: dispatch a completed workflow call before the containing response ends when possible, but keep generation ownership until the provider actually finishes. Acknowledgment overlaps execution, not a second concurrent voice generation.
- D5 resolved: give interactive workflows a direct Responses API adapter and bounded source retrieval. Keep the existing text-only chat/OpenRouter abstraction unchanged; choose source-backed quality over inventing content to meet a deadline.
- D6 resolved: publish versioned result data through a maintained Apps route; keep generated-code apps intact. Task artifacts record each publication, and device/browser page context identifies the intended revision for edits.
- D7 resolved: provide one Codex-facing CLI, isolated backend fixtures/live runs, and a real Android-emulator integration demo. The installed WebRTC library exposes input-buffer and playback-sample hooks, so prerecorded audio can use the actual RTP path without a second audio transport.
- 2026-09-11: Paused final sequencing at the user's request to assess GPT-Live 1. Recommended a bounded migration comparison before Realtime-specific voice work; provider-independent workflow decisions remain reusable. See the linked assessment for evidence and unresolved measured outcomes.

## D1. Workflow packages, shared capabilities, and routing

### Decision and repository fit

The existing `agent_tools/registry.py` registers tools for the general agent, but `ToolExecutionContext` assumes a code workspace and filesystem permissions. Keep that interface intact. Add a workflow-specific typed capability context and adapters to shared service implementations; do not give the interactive runtime the general agent's shell/filesystem toolset.

Place packages under `backend/app/workflows/<workflow_id>/v1/`, with a manifest, `workflow.py`, fixtures, and evaluation expectations. Package discovery scans this fixed source-controlled root for manifests; import paths must resolve inside their own package. Production activation is an exact `id@version` allowlist in configuration. Empty allowlist and `workflows_enabled=false` are the shipped defaults. Tests use an explicitly isolated test configuration. A new package does not require editing a central routing switch.

### Contracts

- `WorkflowDefinition`: ID, immutable version, title, concise routing description, example requests, Pydantic input/output schemas, required capability IDs/versions, supported output kinds, deadline policy, and async entry point.
- `WorkflowContext`: task/attempt IDs, pinned definition, validated input, authenticated origin and context references, checkpoint store, cancellation signal, event sink, capability executor, and absolute server deadline. It carries no user-supplied executable paths.
- Entry point: `async run(context) -> WorkflowResult`. The result contains validated page content/provenance or a typed request for clarification; it does not directly commit a task completion.
- `WorkflowCapability`: versioned ID, typed input/output, effect classification, timeout, concurrency policy, and implementation. All calls flow through `context.call(...)`, which enforces the workflow's declaration and records start/end/error/usage. Python packages are reviewed trusted application code, not a security sandbox; enforce the no-direct-network/service-access convention with package validation and code review.
- Shared initial capabilities: `screen_context.get`, `model.analyze_image`, `web.search`, `web.read`, `model.compose`, `artifact.read`, `artifact.publish`, and `artifact.revise`. Model/provider details stay within these adapters.
- `start_workflow` arguments: `workflow_id`, `request`, `context_requirement` (`activation_screen` or `none`), optional `artifact_id` and `base_revision`. Version, device ID, activation ID, and execution lane are server-resolved, never invented by the model. Reject incompatible context requirements against the registered schema.
- Register `research_screen@1`, `recipe_from_screen@1`, and internal `revise_result@1`. The last handles conversational edits and uses the same task/runtime contract. The startup fixture workflow is test-only and never appears in production voice tools.

### Routing and activation

Build the compact voice catalog from enabled, validated definitions when the session is configured. The voice model selects the workflow directly; no additional classification-model round trip. Keep `dispatch_task` for unmatched substantial work. Invalid/disabled IDs produce a recoverable tool result and create no task. Never silently dispatch the same request down both routes.

Existing in-flight tasks pin their package version and capability versions. Keep old package versions until those tasks drain. Removing a version with outstanding tasks fails startup validation rather than rerunning them with different code. Enabling/disabling workflows takes effect on new sessions and new task admission; disabling does not destroy already accepted tasks.

### Verification

Validate duplicate IDs, schemas, missing capabilities, illegal import paths, disabled candidates, and unavailable pinned versions. Prove the second reference workflow can be discovered, invoked, and tested without changes to voice transport, worker dispatch, or result routing. Fixtures replace capabilities through the same interface, not a second implementation of the workflow.
## D2. Durable task state, interactive worker ownership, and recovery

### Storage and admission

Add `Task.execution_lane` (`general` default, `interactive`), nullable `workflow_id`, `workflow_version`, `workflow_inputs_json`, `workflow_context_json`, and `workflow_deadline_at`. Pin capability versions and the definition digest within workflow metadata. Add `WorkerInstance.execution_lane` with the same default and indexes on lane/status/order and lane/heartbeat. Existing rows migrate to `general`; do not add another task lifecycle state.

Add an internal workflow dispatch service and authenticated `POST /api/workflows/runs`, with the same validated request contract as the voice gateway plus a client idempotency key. A read-only `GET /api/workflows` returns enabled workflow descriptors. Ordinary task dispatch cannot choose an arbitrary lane or executable workflow. Responses reuse task identity and include acceptance, workflow/version, context state, and result reference when one already exists.

Dispatch creates the task, context-request row (D3), ordered task event, and targeted live event in one short database transaction. Return only after commit. If event delivery is delayed, the durable request is replayable; do not depend on an untracked `asyncio.create_task` for acceptance correctness. Repeated admission with the same key returns the same task; changed arguments with that key are rejected. Voice keys include session identity and provider `call_id`.

### Two workers, one lock domain

Extend `python -m app.worker` with `--lane general|interactive`; omission remains `general`. Start one process per lane with separate logs/PIDs and health records. The interactive process runs `WorkflowRuntime`, while the general process retains `TaskAgentRuntime`. Reuse heartbeat, cancellation, attempt, event, and lease services. Local launch/stop scripts must supervise both processes when workflows are enabled.

Lane filtering is required throughout `worker_coordinator.py`: activation exclusivity, availability reconciliation, queued-task claiming, promotion/holding of waiting tasks, and stale-worker reconciliation. In `task_service.py`, worker eligibility, queue position, retry, and task-detail health must use the task's lane. Keep the existing worker-health endpoint defaulting to general and add explicit lane selection; overall Jarvis health reports both components.

Worker startup still obtains SQLite's writer lock before deciding the owner. At most one healthy worker per lane is admitted. Claim the oldest **ready** task within that lane. A workflow awaiting a screenshot has status `queued` or `waiting_for_worker` with a context-pending reason, but is not ready and occupies no worker slot. Context failure/expiry becomes `failed` with an actionable explanation. Only count ready tasks when reporting runnable queue position; expose pending-context status separately.

Retain strict FIFO among ready tasks, including the existing head-of-queue behavior for resource conflicts. Do not broaden this change into a new priority scheduler. All lanes consult the same `ResourceLease` table. Revision tasks declare a mutable `result_page:<id>` target before claiming; new pages have a task-specific target. No network, model call, or image encoding may hold a database transaction open.

### Attempts, deadlines, and recovery

Persist context acquisition independently of worker attempts. A workflow has a 20-second soft delivery target and a 60-second default hard deadline from acceptance. Queue time consumes that deadline. A periodic interactive reconciliation pass checks pending context and expired queued jobs even while its execution slot is occupied. Use a 100 ms idle poll for this lane; retain the general worker's configured poll.

Persist completed stage checkpoints on `TaskAttempt.checkpoint_json`: context ID, analyzed result, retrieved source references/content, and validated draft. Existing retry creates a new attempt and copies only compatible completed workflow checkpoints; preserve pinned inputs/version. An explicit retry gets a new hard execution deadline. Missing expired context requires re-invocation, never an implicit new screenshot of a different screen.

Preserve existing stale-worker behavior: fence/end the lost attempt and mark the task interrupted; the existing retry operation resumes checkpointed work. No invisible automatic full-task rerun in v1. Read-only cloud calls may repeat after a crash, but every effect uses a durable task/stage idempotency key. Before publication, check the current attempt, live lease, task status, cancellation flag, and base revision in the publication transaction. A worker that lost its claim cannot publish, complete, or emit successful stage facts.

### Verification

Exercise simultaneous registrations (same lane rejects one; different lanes both activate), a busy general worker plus an interactive task, lane-specific health/queue reporting, missing context without slot occupancy, cross-lane resource contention, cancellation during retrieval, lease loss immediately before publication, process restart, and checkpoint-preserving retry. Run existing reminder and general-worker tests unchanged alongside new lane tests.

## D3. Android activation context, transport, and lifetime

### Existing integration gaps

`JarvisVoiceInteractionService.onReady` currently disables screenshots, `invokeFromWake` does not request screenshot context, and `JarvisVoiceInteractionSession.onHandleScreenshot` discards the callback. The settings UI says screenshots are always disabled. All four must change together. `CockpitApi.createRealtimeCall` currently sends only source/score for Android wake activation; align it with the existing backend activation ID/telemetry contract rather than introducing a second unrelated identifier.

`/api/jarvis/live` and its replay endpoint currently authenticate broadly; screen requests need server-side recipient filtering. `create_realtime_call` also needs the HTTP-authenticated device identity, rather than treating a caller-provided `controller_id` as proof of device ownership.

### Android lifecycle

- Add `screenContextOptIn` in `CredentialStore`, off by default and independent of assist-structure access. Settings explain local capture at activation and upload only for screen tasks; update the Android README.
- Generate a UUID activation ID before a local-wake show request and carry it in show arguments, connection context, screenshot state, and device telemetry. For system gesture invocation, allocate the same state when the session is prepared. Maintain one capture generation per session instance; finish a prior invocation rather than rebinding its callbacks to a new activation. Test callback-before/after-show ordering and late callbacks explicitly.
- Use `SHOW_WITH_SCREENSHOT` only when opted in and unlocked; stop disabling that flag when allowed. Recompute the disabled-context policy when preferences change. Do not enable text/assist-structure collection as a side effect.
- Snapshot state is `pending`, `available`, `unavailable`, or `expired`, held by an activation-scoped store. Record local monotonic invocation/callback times. Wait at most five seconds after invocation for a callback, then report unavailable; null callbacks report unavailable immediately. Do not infer a protected app merely from a null/black frame.
- Keep at most one owned bitmap for the activation. The original expires 30 seconds after invocation, or on session end, lock, opt-out, or replacement. Encode on `Dispatchers.Default`, not the UI/audio thread: JPEG quality 85, maximum long edge 1600 px, maximum encoded upload 2 MiB. Do not write it to Android disk. If an accepted upload has already copied encoded bytes, that bounded in-flight upload can finish after the overlay closes; it cannot fetch a new frame.
- Image data does not go into the voice prompt. The background workflow consumes its immutable reference. Requesting a fresh screen during a long-lived conversation requires reopening Jarvis; the activation frame is explicitly not a continuously refreshed screen view.

### Server transport and lifetime

Add a `WorkflowContextRequest` row: request/task/device/session/activation IDs, kind, state, server deadline, storage reference/hash, dimensions, device-reported timing, and failure code. Context state transitions are compare-and-set and idempotent. A request expires ten seconds after admission unless completed sooner. Android independently enforces the 30-second local frame lifetime; do not compare its monotonic timestamps with server time.

Add nullable `JarvisLiveEvent.recipient_device_id`. Existing public events remain visible as before; context requests are returned only to the authenticated target device in both SSE and replay. A browser must not receive another device's actionable request. Cursor progression must advance across filtered rows so reconnects do not loop over invisible events; retain global sequence numbers and test sparse pages.

Endpoints under `/api/jarvis/context-requests`:

- `GET /pending`: authenticated device's unexpired pending requests, for reconciliation after SSE reconnect or listener races.
- `POST /{id}/image`: raw JPEG body, correlated activation metadata, device bearer authentication. Verify the credential owns the request and the task is still eligible before accepting bytes. Decode and validate actual dimensions/size/MIME; use bounded request-body reads. A repeat with the same hash returns the existing reference; a different image for that request conflicts.
- `POST /{id}/unavailable`: a typed reason (`opted_out`, `locked`, `expired`, `session_ended`, `capture_unavailable`). It cannot overwrite an already accepted image.

The Android application-level event handler owns request handling, rather than relying exclusively on the visible overlay listener. On reconnect it queries pending requests, because the existing event cursor can advance before upload succeeds. Check request activation against the capture store before upload. Retry a failed transfer at most once while its deadline and local frame lifetime allow; send a failure for unavailable context rather than substituting a new frame.

Bind the voice session's origin device on the server from bearer authentication; validate controller ownership at creation. Browser-created sessions have no Android capture device by default. Store the origin binding and activation reference with task admission. Device revocation/lock and task cancellation are rechecked at upload. Existing voice intent/generation checks remain in force before task admission.

Write uploaded bytes to a private context directory using a temporary file and atomic rename, then commit the accepted reference and context-ready fact. If the DB commit loses a race, remove the orphan through the same cleanup path. Workers only claim tasks after the reference is committed. Image files are not served as public static assets or included in routine logs/FTS. Keep accepted context for 24 hours for explicit retry, then delete via periodic cleanup; keep metadata/hash for trace provenance. Fixture images used by tests are separate, deliberately retained test assets.

### Verification and platform boundary

Test two paired devices, forged controller IDs, filtered SSE/replay cursors, upload before/after callback, duplicate requests, replay after client restart, differing bytes for one request, revoked credentials, expiry, lock, opt-out, oversized images, and null screenshots. The device's callback is the production capture source; ADB capture cannot substitute for it.

Android documents that screenshots are supplied for the foreground app at assistant invocation and may be null: [VoiceInteractionSession](https://developer.android.com/reference/android/service/voice/VoiceInteractionSession#onHandleScreenshot(android.graphics.Bitmap)). This does not establish callback timing or OEM reliability; those remain emulator and physical-phone acceptance checks.

## D4. Realtime dispatch and acknowledgment overlap

### Critical repository behavior

The current adapter extracts calls only from `response.done` and returns early when calls exist. The coordinator treats `shell_call` as `response_finished=True`, then awaits the gateway, sends a result, and queues `tool_continuation`. Moving the same event earlier without changing ownership would release the response arbiter while the provider is still generating. `_execute_shell_call` also assigns the latest transcribed user message, which can be the wrong turn when a call arrives before transcription.

### Event handling decision

Handle `response.output_item.done` for function-call items with explicit `status=completed` as the early execution point for `start_workflow`. Parse and validate the full JSON object against the tool schema; retain call/item/response/activation/input-item correlation. Do not dispatch on argument deltas or on `response.function_call_arguments.done` alone: the official SDK documents that the latter is also emitted for interrupted/incomplete/cancelled responses. [Official argument event definition](https://github.com/openai/openai-python/blob/main/src/openai/types/realtime/response_function_call_arguments_done_event.py), [item event definition](https://github.com/openai/openai-python/blob/main/src/openai/types/realtime/response_output_item_done_event.py).

Keep `response.done` as the completed-call fallback, running through the same normalizer and deduplication path. If an item lacks an explicit completion status, defer execution to the completed response fallback. Existing other tools retain terminal-response dispatch initially. Do not convert malformed JSON into an executable empty argument object.

Deduplicate adapter observations by session generation plus `call_id`, and enforce durable idempotency at workflow admission across event replays. Recheck current session generation, activation acceptance, authenticated device state, and known cancellation before committing the task. A later interruption cancels only speech if the task has already been committed; explicit task cancellation remains separate.

Add a provider-neutral `response_generation_done` event, distinct from function readiness and audible delivery completion. Process response status and transcript finalization even when a response contains tool items; remove the existing early return that skips that lifecycle work. The response arbiter tracks generation-in-flight separately from the existing speech floor. Early `shell_call` must not finish the owning response. Terminal generation clears generation ownership; existing playback state still prevents talking over audio already playing. Text/transcript delivery remains finalized by the current playback-aware path.

Carry provider input-item ID/activation ID into gateway admission. Resolve `origin_turn_id` only from that correlated item; if transcription has not persisted yet, leave the turn FK/reference unset and retain provider correlation for later reconciliation. Never wait for transcription and never attach a task to an unrelated previous message.

### Default speech sequence

1. The voice model emits `start_workflow` immediately for a matching task; no introductory sentence is required before it.
2. Gateway admission commits the task and targeted context request, then returns `{accepted, task_id, workflow_id, context_state}`. No image upload, model call, or retrieval happens synchronously in this handler.
3. Send the function result to the provider and queue a `workflow_ack` continuation through the existing arbiter. It waits for generation ownership to clear, not for background work to finish. The coordinator must not block provider event ingestion while waiting for that release.
4. Generate one short acknowledgment describing intention/acceptance. Example: "I'll find a recipe for that and put it in your cockpit." Disable tool selection for this dedicated acknowledgment response so it cannot dispatch the task again.
5. Context upload and interactive execution run independently. Later milestone/terminal facts use the existing presentation policy; reserve ordinary progress facts outside the acknowledgment so they do not lengthen it.

Add `workflow_ack` as a distinct response purpose with task/call correlation. Queue it at tool-continuation priority; a currently speaking user keeps the floor. Multiple accepted calls in one response receive all function outputs but one combined acknowledgment. If the task fails before acknowledgment starts, speak the actual failure; if it completes first, deliver completion once rather than stale startup text plus completion. A closed/superseded voice session cannot prevent the durable task from finishing or notifying.

The generic silence instruction remains valid for synchronous tools. Add an explicit asynchronous-startup policy and acknowledgment-specific instructions. We are not depending on mixed audio-and-call emission in the same model response. If such output occurs, process both correctly and wait for its audible floor to clear before a continuation.

### Verification and timing

Adapter/coordinator integration tests replay: completed item then response.done; response.done alone; duplicate/reordered delivery; valid-looking partial arguments on cancellation; malformed JSON; mixed speech/tool output; multiple calls; stale activation; user barge-in before and after acceptance; provider disconnect after commit; and missing ASR transcript. Assert exactly one task per call and no concurrent `response.create` ownership.

Record item completion, dispatch start, committed acceptance, function-output sent, generation completion, acknowledgment requested, audio-buffer started, and Android audio playback separately. Set an initial local admission target of 150 ms p95, excluding remote provider and device upload time. Provider audio-buffer start is a proxy, not proof that the phone speaker has emitted sound; D7 measures the Android side as well.

## D5. Model/search adapters and bounded reference workflows

### Provider implementation

The current `Message.content` is a string, and the nonlocal `llm.py` client routes through OpenRouter. Do not retrofit image and Responses tool semantics into every existing chat consumer. Add an async workflow model client using the existing `httpx` dependency, a persistent connection pool per interactive process, the configured OpenAI API credential, and a dedicated configurable Responses base URL. Default model is `gpt-5.6-luna`, reasoning effort `low`; log the actual returned model and usage for each stage. No automatic change to the general task model.

Implement typed image input for analysis and strict structured output for analysis/composition, with `store=false`. Parse refusals, incomplete responses, HTTP failures, and schema errors explicitly. Use at most one schema-repair request for the entire workflow. Keep provider retry separate: at most one retry for 429/temporary transport/5xx failures when time remains, honoring Retry-After within the hard deadline. Do not retry credentials, unsupported model/tool, or validation mistakes as transient failures.

Implement `web.search` as one Responses request with native `web_search`, asking for a short source shortlist and requesting `web_search_call.action.sources`. Derive source IDs/URLs from actual tool results and citation metadata, not model-invented URLs. This introduces a model-backed search stage, whose latency must be measured rather than described as a zero-cost search API. Bound the provider request by timeout and record actual provider tool calls; one search request may contain provider-internal work.

Use `web.read` to fetch the top three distinct candidate pages concurrently. Permit public HTTP(S) only, with URL/DNS/redirect checks against loopback, private, link-local, metadata, and local file targets. Revalidate and pin the approved address through the connection rather than checking DNS and then resolving independently. Limit redirects to three, response bodies to 2 MiB, and each extracted source to 20,000 characters. No logged-in browser, JavaScript execution, or shell browsing fallback. Parse HTML text and Recipe JSON-LD using a maintained HTML parser; JSON-LD is an extraction input, not proof of correctness. Add bounded image decoding and HTML parsing dependencies to the backend lockfile during implementation.

Source results include canonical URL, title, retrieval time, extracted text/structured fields, content hash, and fetch status. Do not put fetched text or images into ordinary logs; persist bounded source checkpoints with task provenance. Treat instructions inside sources as content, never as new workflow/tool permissions. Preserve citations to fetched sources and distinguish adaptations from the source recipe.

Official capability references: [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna), [web-search source metadata](https://developers.openai.com/api/docs/guides/tools-web-search#sources), [structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs). Availability and latency on this account are not verified by documentation; the live evaluation preflight checks them without silently substituting another model.

### Shared staged executor

Use named, checkpointable calls rather than a general autonomous loop. Initial stage ceilings: image analysis 10 s, search 12 s, parallel page reads 6 s, composition 15 s, optional repair 8 s; every call is also capped by the remaining 60-second workflow deadline. These are failure ceilings, not predicted durations. Context acquisition has its separate ten-second ceiling from D3. A 20-second soft deadline generates a factual overrun event, not cancellation or false completion.

Set initial output budgets to 600 tokens for image analysis, 800 for search summary, and 2,500 for page composition. Repair receives only the validation errors, required source excerpts, and draft. Stage input includes the request and relevant bounded context, not the whole daily conversation. Batch independent page reads; do not parallelize composition before source evidence exists. Model refusal or absent usable evidence yields an actionable failed task or the existing clarification/question flow, with no completed result page.

### Reference workflow behavior

`research_screen@1`: analyze the activation image for the subject, user-requested research goal, visible evidence, ambiguities, and one query. Retrieve candidates, read the top three, and compose a concise document with source-linked sections and explicitly identified uncertainty. Default to at least two fetched sources for comparative claims; a direct authoritative source can support a simple factual result. When the referent itself is unresolved, ask one clarification using the existing task-question mechanism and resume from the analysis checkpoint when answered.

`recipe_from_screen@1`: use the same capabilities and execution machinery. Image analysis returns likely dish/visible ingredients and ambiguity; it never asserts hidden ingredients as observed. Read complete candidate recipes, prefer candidates matching the dish and explicit user constraints, and compose one recipe result. Require a source-backed title, positive servings, nonempty ingredients with usable quantities where appropriate, ordered steps, and a working original-source link. Validate that steps and ingredient lists agree and that requested constraints are represented. Render paraphrased instructions and clearly label adaptations. If no complete usable recipe is fetched, report that failure rather than filling missing quantities from a search snippet.

`revise_result@1`: load the base revision and apply supported deterministic presentation changes directly. Use one bounded composition call for content changes; retrieve new sources only when the change requires new evidence. A dietary adaptation retains original attribution and marks the changed recipe as adapted. Keep the stable page identity; publication is governed by D6.

All three workflows call the same capability interfaces. Recipe-specific prompts, validation, and fixtures live in the recipe package, not the runtime or voice gateway. The general screen workflow is a reusable research pattern, not a catch-all promise to complete arbitrary actions.

### Verification

Test provider errors/refusals/truncation, one repair maximum, deadline/cancellation propagation, source provenance, redirect/address validation, oversized content, inaccessible/JavaScript-only pages, malicious page instructions, ambiguous images, missing ingredient quantities, inconsistent steps, and requested dietary changes. Fixture mode must produce deterministic output; live mode records quality and actual stage durations, including failures and rate limiting. Do not benchmark cached fixtures as if they measured cloud performance.

## D6. Result identity, revisions, rendering, and notifications

### Storage and API

Existing `GeneratedApp` records describe source files, build status, and app leases; `TaskArtifact` records have no stable revision pointer. Add separate `ResultPage` and `ResultPageRevision` tables rather than pretending a data page has generated source or a successful build.

- `ResultPage`: UUID, kind (`document` or `recipe`), title, current revision number, nullable originating task reference, created/updated timestamps. Keep pages when a task is removed; source FKs use SET NULL as needed.
- `ResultPageRevision`: page ID, sequential revision, content schema version, validated JSON, provenance, source task/attempt/event IDs, publication idempotency key, and timestamp. Unique `(page_id, revision)` and publication key. Revisions are immutable.
- Reuse `TaskArtifact` as the append-only task association: `artifact_type=result_page`, stable URI, page/revision identity, and provenance. The page's current revision is authoritative; task artifacts show what that task produced at the time. In voice/API contracts, `artifact_id` means the stable ResultPage ID, not a per-task TaskArtifact row ID; document that distinction explicitly.
- `GET /api/results` lists pages; `GET /api/results/{id}` returns current validated content and revision; `GET /api/results/{id}/revisions/{revision}` reads history. Require existing cookie or paired-device authentication for result reads.
- `POST /api/results/{id}/revisions` accepts `base_revision`, requested change, and idempotency key; creates a `revise_result@1` task and returns 202/task identity. Stale base revision at admission returns 409. Recheck at publication; a race after admission fails that task with a refresh/retry explanation, never a silent rebase.
- `artifact.publish` and `artifact.revise` are internal service operations. In one short transaction, verify attempt/lease/cancellation, create the page revision and task artifact, update the current pointer, append completion, and enqueue its notification. Refactor existing task completion helpers to accept a caller-owned transaction for this path; do not nest independent commits. A replay of an already committed publication returns the same page/revision.

### Content and rendering

Use a versioned discriminated content model: title, short summary, sections containing paragraphs or ordered/unordered lists, and source references. Recipe content additionally contains servings, ingredient lines (display text plus optional structured quantity/unit), ordered preparation steps, optional timing, and attribution/adaptation notes. Validate source references against retrieved provenance. Text is data, not raw executable HTML/JS; external links accept HTTP(S) only.

Add a maintained route `/workspace/apps/results/[id]` within the existing authenticated app layout. The actual Apps inventory is `/workspace/apps`; extend its UI to show result cards alongside existing generated apps, using a discriminated frontend card type and a separate results API. Do not change `/api/apps` responses into a union or make code-app operations accept result pages. Inspect the bundled Next.js documentation required by `frontend/AGENTS.md` before implementation.

Provide mobile-responsive document/recipe components, source links, ingredient checkboxes, print styling, and revision display/history. Serving controls scale only explicitly structured numeric amounts; nonnumeric quantities remain unchanged. This control is local presentation state until the user explicitly saves a change. Content edits produce a new revision through the task path. Undo restores chosen prior content as a new revision, preserving history rather than deleting revisions.

No per-result dependency installation, source generation, or frontend build. The application itself still needs its normal build once when adding the renderer. When an open result receives its result-updated event, refetch the current revision; do not show a successful save before the server commits.

### Visible artifact context

The result route maintains `{artifact_id, revision}` in the existing browser client context and sends authenticated context updates when a Jarvis browser session is active. If the result page is not visible, clear that hint. Extend the session client-context contract with an optional visible-result reference; validate that the page/revision exists before it becomes a tool target.

For Android WebView, use an AndroidX WebKit message listener restricted to the configured Cockpit origin and main frame. It accepts only result identity/revision messages, never arbitrary native commands or credentials. The native app stores the visible result reference and includes a snapshot of it in the next assistant activation only when the Cockpit WebView was the foreground activity at invocation. Clear on navigation away; lifecycle tests must cover opening the assistant overlay without losing the immediately preceding result reference, and switching to another app without retaining a stale target.

Resolve edit targets from explicit user selection or that activation/session reference. An ambiguous "change it" with no valid reference asks for clarification. Do not use a global last-generated page across devices. Workflows requiring only a current result set `context_requirement=none` and do not upload a screenshot.

The WebView currently has separate cookie login from native bearer pairing. Preserve the existing first-use web login for v1 and explain it in setup; do not place the device bearer credential in URLs or JavaScript. The unattended emulator harness logs into its dedicated test instance through the existing login flow. Notification deep links remain on the configured Cockpit origin.

### Notifications and verification

Set successful result-task notification URLs to `/workspace/apps/results/{id}` inside the completion transaction. Failure, interruption, and clarification continue to open task details. Android already reads the event payload URL before falling back to `/tasks/{id}`; test this path and the browser push path. Avoid duplicate spoken acknowledgment/completion through the existing presentation settlement mechanism described in D4.

Test idempotent publication after crash, stale revision races, cancellation immediately before commit, task deletion with retained page provenance, unknown content versions, unsafe HTML/link content, responsive rendering, notification click navigation, browser/native context propagation, explicit no-context clarification, and one voice-requested revision. Assert no code-app builder/verifier call occurs while publishing either reference result.

## D7. Codex authoring, Android emulator demos, and evidence

### CLI and authoring contract

Add a cross-platform Python entry script `scripts/workflow.py` with subcommands:

```text
python scripts/workflow.py doctor
python scripts/workflow.py scaffold <workflow_id> --version 1
python scripts/workflow.py validate <workflow_id>@<version>
python scripts/workflow.py evaluate <workflow_id>@<version> --mode fixture|live
python scripts/workflow.py demo-android <workflow_id>@<version> --mode fixture|live
```

`doctor` inspects Python/Java/SDK/Gradle, emulator acceleration, system images, available API configuration (presence only), and test-build prerequisites. It emits a machine-readable report and precise missing-prerequisite actions. `scaffold` writes a disabled package and tests using the D1 template; it must not edit production activation configuration. `validate` loads definitions in an isolated subprocess and checks contracts/fixtures/declarations. `evaluate` runs the same workflow implementation with either fixture capabilities or real providers. `demo-android` orchestrates the complete device path below. Commands return nonzero on failed required assertions, and distinguish unavailable dependencies from passes.

Document these commands, package conventions, tool extension rules, result schemas, budgets, and review evidence in `docs/JARVIS_WORKFLOW_AUTHORING.md`; add a focused instruction file under the workflow package root so Codex finds the guide while editing packages. A new shared tool requires adapter unit tests and its own contract fixture in addition to workflow tests. No workflow authoring endpoint or in-app coding agent is introduced.

### Isolated test environment and activation

Each run receives its own directory under `.local/workflow-evals/<run_id>`, SQLite database, context storage, logs, and backend/frontend ports. Test startup must reject paths/ports colliding with the normal instance. Start API, general worker, interactive worker, and frontend with an explicitly marked test configuration and candidate allowlist. Reuse built frontend assets across runs; do not count APK/frontend build or emulator boot as workflow runtime.

Candidate packages have a manifest release state and require both test deployment mode and the test candidate allowlist. Production startup rejects test mode overrides/candidate manifests in its enabled list. Promotion changes the reviewed package release state and production allowlist. Existing accepted tasks keep their pinned version. Secrets are read from the process environment, never included in reports, screenshots of setup fields, or command-line echo.

Create one emulator-specific test account/session in the isolated backend using its configured test password. Use the normal authenticated pairing-code endpoint and pair the debug app through its UI. Use the existing web login for the test WebView. There is no production auth bypass. Test fixture injection endpoints, if needed by deterministic provider events, are mounted only by the test application factory, absent from the production router.

### Emulator setup and device demo

The host has `adb.exe`, `emulator.exe`, and SDK platform/build tools, but the read-only inspection found no configured AVD or installed system-image directory. Treat image download/AVD setup as required implementation infrastructure, not a completed test. Use a dedicated API 37 Google APIs x86_64 AVD on this Windows host; the CLI detects host architecture for other machines. Never wipe an unrelated AVD. If hardware acceleration is unavailable, report the prerequisite; do not pretend a backend-only run exercised Android.

Add an Android test fixture application module and instrumented UI Automator tests. The fixture app displays a workflow-provided image/text, with accessible IDs and a known visual marker, and can enable `FLAG_SECURE` for the protected-content case. It is not packaged in the release app.

The demo command performs the following bounded, asserted sequence:

1. Start the isolated services and AVD, wait for boot/readiness, and install Jarvis debug, fixture, and instrumentation APKs.
2. Pair, select Jarvis as the system assistant, grant microphone/notification access, enable screen context, and establish test WebView login through UI Automator. Record whether any platform setup remains unavailable.
3. Display a workflow fixture in the separate app. Invoke Jarvis through a debug instrumentation hook that calls the same assistant `showSession` path as production wake invocation. Android must deliver `onHandleScreenshot`; verify the uploaded visual marker/content belongs to this fixture. Do not upload an ADB screenshot as input.
4. Inject a prerecorded command through the debug audio hook in live mode; in fixture mode replay provider events through the same adapter/coordinator contract and label voice/model behavior simulated.
5. Assert task acceptance, context-ready state, acknowledgment activity, and ordered workflow facts. Await committed result and Android completion notification.
6. Tap the actual notification, verify the expected result ID/title/content in WebView, reopen Jarvis while that page is visible, and issue a prerecorded edit. Verify stable URL and incremented revision with the requested change.
7. Save evidence and terminate only processes/AVD instances owned by this run. Preserve reports and failed-test diagnostics.

Use real UI selectors and bounded state waits rather than fixed-coordinate scripts. A notification or page assertion is not satisfied by a backend status alone. External source links need not be followed during the latency run, but their valid canonical URLs and fetched provenance are checked.

### Audio injection and measurement

The locally installed `io.github.webrtc-sdk:android:144.7559.14` AAR was inspected read-only. `JavaAudioDeviceModule.Builder` exposes `setAudioBufferCallback` and `setPlaybackSamplesReadyCallback`; `AudioBufferCallback.onBuffer(ByteBuffer, format, channels, rate, bytesRead, captureTimeNs)` gives the input seam, and playback callbacks expose PCM samples. Use these existing hooks rather than adding WebSocket audio alongside RTP.

Add an injectable audio-hooks interface with production no-op implementation and `src/debug` fixture implementation, activated only by instrumentation. The input hook replaces the outgoing capture buffer with correctly formatted prerecorded PCM, paced by the real audio callback, returning the appropriate capture timestamp. Silence-fill outside the recording and never mix physical mic samples into a fixture run. Validate sample rate/channel conversion and captured outgoing samples in instrumentation before using these runs as evidence. Record the fixture's annotated first-speech and final-speech sample indices on the device monotonic clock.

The playback hook records the first non-silent decoded output samples and playback duration, without blocking the audio thread. Store sampled evidence only in debug runs. This measures delivery to the device audio path, not acoustic speaker output; physical-phone tests remain necessary. Production telemetry may retain coarse timing events but never raw PCM or debug file/IPC controls. Inspect the release APK to assert fixture controllers, test receivers, and audio-injection code are absent.

### Evidence and quality gates

Every run writes `report.json` and a readable Markdown report containing code/definition/fixture hashes, workflow/provider versions, emulator/API/build identity, test mode, assertions, stage and end-to-end timings, task/context/result/revision IDs, usage, and explicit failures/skips. Include Android screen recording, key UI screenshots, sanitized traces/logcat, and debug playback evidence when available. Screen recordings alone do not prove audio overlap; correlate the task facts with device playback timings.

Each reference package ships at least eight fixtures covering success, ambiguity, source failure, invalid content, constraints, and edits. A live benchmark uses ten cases per reference workflow and reports sample count, median, p95, timeout/failure fraction, and quality outcomes. Do not exclude failed cases from headline deadline-success rates. Quality checks include schema completeness, valid cited-source membership, required content/constraints, correct result targeting, and rendered-page assertions. Semantic quality uses fixture-specific expected facts/rubrics; mark subjective judgments for review instead of treating JSON validity as correctness.

Compare runs with and without a long general task, cold versus ready sessions, and source delays. Tests bypassing physical wake/microphone must say so in the report. Passing automated tests produces a reviewable candidate and demo, not automatic production activation. [Android emulator control](https://developer.android.com/studio/run/emulator-commandline), [UI Automator](https://developer.android.com/training/testing/other-components/ui-automator).

