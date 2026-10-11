# Jarvis Modular Task Architecture

Status: **completed and accepted for personal use on 2026-10-11.** The user
confirmed working tasks, including a recipe created from a screenshot, and
accepted all work as complete. The [completion ledger](jarvis-tasks/00-implementation-roadmap.md)
and [acceptance record](jarvis-tasks/reports/personal-use-accepted-2026-10-11.md)
own current status. Native Windows coding remains gated; explicitly selected
trusted commands and the accepted M8 caveats remain the supported personal mode.

This document replaces the former workflow plans and defines the implemented
modular task architecture and its contracts. Focused references live under
[`docs/jarvis-tasks/`](jarvis-tasks/). The completed
[personal setup](jarvis-tasks/10-personal-setup-and-phone-acceptance.md) uses the
existing Windows/Tailscale host, optional password, one-action startup and
one-time QR/link pairing. Device/privacy and durable task guarantees continue
to apply; a full backup is optional for disposable development data.

Aligned with the thin-reference scope decision of 2026-10-01 and later closure decisions. Detailed phase contracts and the current roadmap supersede historical status paragraphs. Codex is the worker harness for general agents and guided workflows; reusable workflow guidance and batched capabilities provide the fast path. Managed voice delegation is the sole route after the measured comparison and user decision. An independent background orchestrator owns automatic worker-feedback decisions. Phase 4 owns workflow format, Codex execution and authentication contracts. Universal continuation belongs to the control plane and applies to both task kinds.

`ARCHITECTURE.md` remains the description of the code that exists today. This document retains the modular task system's detailed contracts.

## 1. Goal

Jarvis should turn a short spoken request into durable work without making the conversation wait for the work to finish. The first reference task is:

> While a dish is visible on the Android screen, ask Jarvis to find a recipe. Jarvis retains the invocation screen and captures the current screen during delegation, identifies the dish, researches source-backed recipes, publishes a useful recipe page, and returns a stable link that opens on the phone.

The system must also make the second and later workflows cheap to add. A workflow should be source-controlled, versioned, testable without a phone for most development, and demonstrable end to end on an Android emulator without ongoing user input.

The product goal is a reusable platform for an expanding set of fast, useful Jarvis tasks. The recipe is a small reference task: shared capabilities do the work, a definition supplies the procedure, and a minimal registered recipe type supplies the presentation. M4 added `research_screen@1` to prove reuse without core changes. Finish when the request is adequately supported; recipe-specific feature expansion and exhaustive culinary optimisation remain outside scope. M5 has verified the generic app catalog, collections, actions and storage.

### Performance objectives

- On the supported ready, unlocked Android path, the activation screenshot is available no later than two seconds after the spoken request ends. Because Android supplies assistant context when the assistant session opens, capture should normally happen while the user is still speaking.
- Durable admission and a stable result URL target two seconds. The accepted completion gate is
  one final ready-path sample below 4.5 seconds from command end; a full p95 cohort is unproven. Phase 7 measures command-end
  to client receipt of durable admission on one Android monotonic clock.
- Screenshot-dependent admission requires on-device captured/ready-to-upload confirmation. If capture is still pending, wait at most two additional seconds before rejecting admission. This failure timeout does not extend the performance objective; capture waiting remains part of measured admission latency.
- The first validated recipe is a soft 30-second objective. The page may show factual progress before then, but it must not show an unverified recipe.
- Version 1 has no overall workflow deadline. After 30 seconds the result page reports that work is taking longer, while Jarvis can inspect current progress and the user can cancel it. Individual provider and network calls still have bounded timeouts so a workflow cannot disappear inside one stuck operation.
- Admission does not wait for a free worker. Execution uses a shared pool and may queue under saturation or resource contention. Report admission, queue, and execution-start latency separately; there is no unconditional immediate-start guarantee or reserved interactive capacity.

The admission threshold is a required completion gate; the 30-second recipe
result remains a soft objective. Measurements separate ready/cold, capture wait,
admission, worker queue and result delivery. The accepted M8 policy retains four explicit exceptions
and measured sample scope; it does not establish a full-cohort timing guarantee. Emulator acceptance does not establish physical/OEM performance.

## 2. Scope

Version 1 includes:

- GPT-Live conversation with managed Responses delegation for routing and tool selection;
- automatic Android screen capture for unlocked assistant activations, subject to Android settings and app/device policy;
- one generic `start_workflow` voice tool, the existing general-task fallback, and `continue_task` for either kind;
- persistent task sessions with ordered turns, execution runs, saved context, and follow-ups under the same task identity;
- Codex-backed workers with live task activity and durable history visible in AI Cockpit on desktop/mobile and inspectable by Jarvis;
- durable workflow admission, version pinning, progress inspection, cancellation, retries, and recovery;
- a non-AI supervisor and homogeneous elastic worker pool for both general agent and workflow tasks;
- independent background task management without an active voice session, using one event-driven decision invocation at a time and the existing shared user-attention inbox;
- versioned workflow guidance and typed, batchable shared capabilities, with Codex adapter and authoring integration detailed in Phase 4;
- maintained, data-backed result pages with stable URLs and revisions;
- the small `recipe_from_screen@1` and `research_screen@1` reference workflows;
- deterministic, live-service, browser-rendering, and Android-emulator test layers;
- structured latency, quality, and failure evidence.

This scope does not add runtime-generated workflows, a visual editor, unrestricted shell/browser control inside workflows, unattended cross-app mutation, a desktop screenshot helper, public unauthenticated result sharing, or specialist recovery/planning/briefing agents. Phase 8's single background orchestration role handles task decisions beyond mechanical recovery. Phase 4 defines the workflow representation and permitted capability envelope and extends the implemented constrained loader in M4.

## 3. Implemented foundation

The repository already has:

- GPT-Live WebRTC sessions for browser and Android, with managed Responses delegation and an authorization-reviewed tool gateway;
- durable tasks, attempts, ordered events, artifacts, questions, targets, cancellation, retries, presentations, and notification delivery;
- a general task worker and agent runtime;
- paired Android device authentication, assistant-role integration, native Live audio, resumable events, notifications, and an in-app WebView;
- generated application hosting and existing Apps navigation;
- fake-provider and browser Live smoke-test foundations.

M0–M5 additionally supply durable turns/runs, the shared pool and controls, guarded Codex execution, background decisions/shared attention, telemetry, shared capability/context/result registries, two small reference workflows, the generic result platform and an API-37 capture/private-upload/result-delivery emulator slice. These are implemented and verified for the guarded route; they do not establish all production paths or performance objectives.

M6–M11 completed supervisor-owned project execution, shared intent/preferences,
native wake/capture/audio, subscription-first ownership/API handoff, combined
verification and the personal phone setup. The user accepted current task use
and screenshot-based recipe creation on 2026-10-11. Historical report scope and
the accepted M8 limits are retained in the completion ledger.

GPT-Live remains the conversation model, not the vision or research executor. GPT-Live supports audio and text but not image input, and delegates reasoning/tool work to a backend. Screen images therefore enter the workflow capability path, never the Live media stream. See the [GPT-Live model reference](https://developers.openai.com/api/docs/models/gpt-live-1) and [delegation guide](https://developers.openai.com/api/docs/guides/live-delegation).

## 4. Architecture

```text
Android invocation
  |-- assistant screenshot --> activation buffer (device memory)
  |                              `-- capture-status metadata --+
  `-- microphone/WebRTC --> GPT-Live / managed Responses <-----'
                                    |
                start_workflow / dispatch_task / continue_task
                                    |
                  capture preflight when a NEW image is required
                  (pending: wait at most 2 additional seconds)
                                    |
                         durable admission transaction
                   task session + run + applicable result/context
                                    |
                       authenticated screenshot upload
                         (if required; fail on error)
                                    |
                         runnable task / shared queue
                                    |
                           non-AI supervisor
                       1 warm standby, up to 8 busy
                                    |
                        workflow or general agent
                                    |
                  result revision + task event + notification
```

### 4.1 Conversation plane

GPT-Live owns listening, speaking, turn-taking, and interruption. Its managed Responses backend performs narrow routing and calls approved Jarvis tools. It does not conduct recipe research or receive the screenshot.

The router chooses:

- `continue_task` for a follow-up to an existing task, retaining its context and identity;
- `start_workflow` for new work matching an enabled curated workflow; or
- `dispatch_task` for substantial new work that does not match a curated workflow.

The router must never send one requested outcome down multiple paths. Phase 2's `intent_id` distinguishes explicit multiple outcomes within a delegated turn and prevents duplicate admission or continuation under the same intent. A clarification reply resumes the run that asked the question; a follow-up after completion creates a new run within the same task. Create a separate task only when the user or Jarvis deliberately separates the work. The current low-reasoning GPT-5.6 Luna managed backend with Fast mode remains the initial routing configuration and is benchmarked rather than assumed to meet the handoff objective.

### 4.2 Activation context plane

Android requests the assistant screenshot as an unlocked assistant session opens, without a separate Jarvis capture toggle. It keeps unused captures only in memory for up to 90 seconds, bound to device, root activation, capture ID, and timestamp. Android's own settings and protected-content policy remain authoritative. An explicit fresh capture stays within the same root activation and does not restart Live audio.

Send authenticated capture-status metadata to routing context before admission: pending, captured, unavailable, or expired. Captured means normalized bytes are ready on the phone, not uploaded or understood. Use callback/status events rather than background screen polling; global assistant-context settings cannot reliably predict per-screen capture restrictions.

Before admitting work requiring a new screenshot, require the selected capture to be captured and unexpired. Pending capture can wait asynchronously for at most two additional seconds, outside the transaction and without blocking Live audio. A known error rejects immediately; timeout, expiry, or mismatch also rejects without creating a task or result. For an existing task, rejection preserves its history and does not create the requested new run. Already committed idempotent admissions replay without needing another capture. Ordinary follow-ups can reuse retained, owned evidence without another screenshot or an active phone session; the preflight applies only when new capture is required.

The screenshot is uploaded only after the server accepts a workflow whose registered definition requires `activation_screen`. The workflow definition, not model-provided arguments, is authoritative about required context. This prevents a routing mistake from silently omitting the image.

Android's assistant screenshot may be unavailable because of user settings, the foreground application, device policy, or protected content. Capture errors reject admission; required-context or upload errors after admission fail the associated execution run through the backend without waiting for a worker or model. This path has no automatic retry, recapture, or replacement-input wait. Jarvis retains the request/failure context and can explicitly retry using valid selected context. Never silently substitute another screenshot or ask a model to guess.

### 4.3 Durable task control plane

`Task` remains the persistent user-visible session and lifecycle authority. Phase 1 adds ordered turns and execution runs, `execution_kind = agent | workflow`, `paused`, and `queue_entered_at`, retaining importance tiers. An initial request and each subsequent follow-up have their own execution run within that task; crash/retry attempts belong to a run. All tasks execute in the background through the same pool; task origin does not define a scheduling class. Workflow-specific, context-specific, and result-specific data live in dedicated records.

The target records are:

- task turns and `TaskExecutionRun`: ordered requests/replies, immutable completed-run outcomes, effective execution kind/version, and saved continuation context;
- `WorkflowRun`: metadata associated with a workflow execution run, including a pinned definition, normalized inputs, version identities, and checkpoints; it is not one record for the entire task lifetime;
- capture-status metadata: device/session/generation/activation/capture binding, revision, readiness, timestamps, expiry, and classified reason, available before a task exists;
- `WorkflowContextRequest`: owning task/execution run, required context kind, exact capture/device/activation binding, upload-request state, private object reference, checksum, expiry, and failure reason;
- `ResultPage`: stable public identity, owning app and task, result kind, publication state, and current revision;
- `ResultPageRevision`: immutable schema version, validated content, provenance, base revision, and creation event;
- result events and app-catalog/storage records as detailed in Phase 5;
- execution-run attempts with claim/fencing tokens, completed-action checkpoints, active-operation state, and recovery metadata;
- durable task-control commands, supervisor generation/lease, and verified worker process identities.

Task status remains the user-facing authority for queued, running, paused, waiting for input, completed, failed, interrupted, and cancelled states, updated atomically with its current run. Explicit follow-ups can reopen a completed task without changing earlier run outcomes. Only one run executes per task; concurrent follow-ups are ordered and serialized. Upload-request state determines claim readiness; result-page state determines what the stable URL renders. `WorkflowRun` does not introduce a competing lifecycle authority.

After capture preflight when required, admission is one short database transaction. It validates the enabled definition and applicable context binding, resolves execution kind/version, applies exact-call and intent idempotency, then creates the task, input turn, execution run, initial attempt, pinned execution reference, and applicable result/context records and events. Continuation appends a turn and run to the existing task, preserving context and reusing a result when editing it. Changed arguments under an existing identity conflict; committed replays return the same task, turn, and run identities. The tool result is returned only after commit. Capture preflight and image upload do not run inside this transaction.

Keep the full task history, tool results, decisions, and artifact references durably, with compact working context for efficient continuation and access to original evidence when needed. Workers and directory reservations are released between completed runs; keeping context does not require a live process. Controls, claims, events, publications, and notifications carry execution-run identity so late work from an older run cannot affect a follow-up.

Reuse persisted Codex threads as the normal continuation path, with native storage surviving worker replacement and Jarvis records retaining portable evidence when a thread is unavailable or incompatible. AI Cockpit's desktop and mobile task views read the same durable timeline and reconnect to backend events. Closing a view does not cancel work. Jarvis can inspect the same actions, results, errors, and available reasoning summaries; complete internal model reasoning is not a promised interface.

Jarvis can inspect queue blockers, pool health, current stage, last durable progress, active operation, heartbeat, checkpoint freshness, pending controls, warnings/questions, result URL, and classified failure. Its audited controls set importance, yield, suspend, resume, cancel, restart, or retry through backend APIs; raw process control is not exposed to the model.

Directory-level reservations protect shared filesystem edits initially, including overlapping parent/child directories. Task reservations are separate from worker execution authority. Retain reservations across immediate crash/restart handoff; release them after confirmed pause/yield or terminal stop. Before transfer, establish that the old worker and writing subprocesses have stopped: database fencing alone cannot stop a filesystem writer. Resume after released access reacquires the targets and checks relevant changes.

Pause and yield finish the current bounded action by default; Jarvis may choose `immediate=true` to stop promptly from the latest completed checkpoint. Cancellation always revokes execution authority and stops promptly without waiting for the current action, heartbeat expiry, or a fixed grace period. Requested and confirmed stop state remain distinct; exclusive access is never transferred while an old writer may still run.

### 4.4 Workflow execution plane

Phase 4 selects a small guided agent using reusable workflow instructions, batched capabilities, and saved context. The template supplies the usual procedure, constraints, and completion criteria; tools handle routine operations and parallel independent reads. Avoid mandatory initial planning calls, a model decision for every mechanical step, or repeated graders. The agent can publish, clarify, request additional evidence/capabilities, or report failure, with extra judgment calls when the request needs them. A short common path is an optimization target, not a rigid call or clarification limit.

Use Codex through a small backend adapter to its local App Server, preferring the official Python SDK where it covers the required surface. Retain the native coding loop; configure guided workflows with narrowly permitted Jarvis tools. Jarvis owns task control and durable action/publication contracts rather than implementing another agent loop. Workflow folders contain `instructions.md` and validated `workflow.yaml`, with executable operations in shared tools. Phase 4 specifies the facade, manifest, action journal and enforcement requirements. The roadmap's M0 establishes actual native compatibility before broad integration. No comparative A/B test or multi-harness bakeoff is required. Code validates structure, ownership, and mechanically checkable properties; semantic quality still requires appropriate agent judgment and offline evaluation.

Start the fast worker profile and background decisions from Luna/low; Jarvis may override permitted settings when requested or when the task warrants it, using the existing delegation/control step. Validate and record settings at supported execution boundaries. Prefer eligible Codex subscription usage, then authorized separately billed API fallback, relying on provider account spending settings. Jarvis has no hard-cutoff verification or budget admission gate. Include other paid capabilities in accounting; Codex subscription access does not fund the separate voice/router or arbitrary API calls. M9 requires a separately authorized subscription profile and one shared execution/refresh lease across workers and background hosts. Busy/unavailable subscription capacity uses authorized API fallback without holding voice admission. M0's API-route pass does not establish this subscription contract.

A non-AI supervisor owns one homogeneous elastic pool: one warm standby and at most eight busy processes, each owning one attempt. Workflow and general-agent runtimes have different enforced capability surfaces but use the same scheduling, resource reservations, and lifecycle. Claim the oldest runnable task in high, normal, then low importance; skip blocked work. No automatic priority preemption or reserved capacity is introduced. Voice delegation or the background orchestrator may issue validated yield/suspend/cancel decisions through shared controls. Upload-pending tasks occupy no worker or mutable reservation. Background decision inference is separate from the worker pool and cannot block direct voice admission.

Persist each completed action result and the agent continuation state before advancing. Straightforward safe crash recovery reuses those results and the same workspace, with one automatic recovery before durable progress is required. Confirmed process exits trigger immediate recovery; the five-second heartbeat and twenty-second stale threshold remain a fallback for alive-but-unresponsive workers. Use small bounded in-process retries for safe transient provider failures, except for Phase 3's fail-on-error capture/upload path. Normal tool errors return to the executing agent for judgment.

An unfinished action may have partially changed files or succeeded externally before its response was saved. Reconcile through normal tools before replay where necessary. Unresolved outcomes or repeated failure produce durable recovery-needed events for the background orchestrator, whether or not voice is active. If background reasoning is unavailable, retain the pending issue and keep it inspectable rather than guessing or discarding it. This scope adds no specialist recovery agent. Old writers must stop before reservations are released.

There is no automatic overall timeout in version 1. Workers must continue to heartbeat and emit stage/capability progress. A stalled-operation detector records and exposes suspected stalls without cancelling the task automatically. The user can ask Jarvis what is happening and cancel the task through the normal task controls. Phase 8 invokes background judgment for meaningful events; it does not continuously poll healthy workers with a model.

### 4.5 Progressive result plane

Admission allocates a stable `/results/<public-id>` URL immediately. That URL renders a maintained application route rather than generated source code or a per-result frontend build.

Before publication, the page shows only observed state such as waiting for screen context, identifying the dish, checking sources, or validating the recipe. A tentative dish label may be shown as tentative. Ingredients and instructions appear only in an immutable validated revision.

Result content is typed and versioned. Phase 5 provides the shared registries and collection infrastructure; Phase 6 registers a small `recipe.v1` schema, minimal renderer and Recipes metadata alongside `document.v1` and Documents. M4 needs typed publication and the minimal recipe view; M5 completes the generic catalog/collections/actions/storage. Recipes uses that common infrastructure without specialised controls or browsing. Maintained collections and generated apps share a catalog while retaining their distinct runtimes. Follow-up edits run within the same task and use the result ID and base revision, publishing a new revision at the same URL. Stale revisions are rejected instead of overwriting newer content, and failed updates preserve the last good revision. A question-only follow-up need not publish a revision; additions can create another artifact or result within the same task.

The Android notification opens the authenticated result route in the app WebView. Authenticated browser/PWA sessions can open the same URL. Public share links are a separate future feature.

### 4.6 Events, attention, and notification plane

Workflows emit progress only after an observed stage transition. Task events remain the durable source; the existing live-event, presentation, and notification mechanisms project those facts to active clients.

GPT-Live decides whether and how to acknowledge or present progress using conversational relevance and existing presentation policy. There is no mandatory acknowledgment sentence or fixed fast-result speech sequence. Spoken claims must follow observed durable facts. Interrupting acknowledgment does not cancel accepted work; cancellation uses an explicit task-control action.

Successful result publication creates a revision and producing-run completion event atomically, then routes a notification whose primary target is the result page. Runs that only answer a question can complete without publishing a revision. Failure notifications link to task details and expose a concise classified reason. Deduplication and event application are scoped to the producing run so an older run cannot complete or fail newer work.

Use one durable user-attention inbox across voice-active and voice-inactive periods. Forward eligible factual updates in code without waiting for orchestration inference. Background judgment handles worker questions/recovery and promotes unresolved user questions to the same inbox. Sending an item into voice context does not mark it communicated or force an interruption; voice acknowledges the exact communicated item. A communicated question remains unresolved until answered. Compose briefings from current records when requested, without a separate briefing agent or periodic model summary.

### 4.7 Background orchestration plane

Phase 8 defines two independent decision paths with one task backend. The managed voice delegate directly starts, continues, answers, inspects, and controls tasks in response to the user. The background orchestrator alone automatically handles worker feedback and can use those same APIs. It continues with voice absent; opening voice does not transfer ownership or stop it. The two paths have no direct agent-to-agent dialogue.

Keep one background decision invocation active at a time initially. Durable event intake, factual forwarding, voice admission, and Codex execution proceed independently. Long research and repairs are worker tasks; ordinary scheduling and mechanical recovery remain code. Use meaningful event triggers and explicit due-time wakeups, not periodic AI monitoring of healthy tasks.

Both paths read compact shared state and retrieve relevant detail. Persist user instructions, action evidence, and decision reasons rather than synchronizing two full model conversations. Shared task APIs deduplicate actions and validate relevant current task/run/question state in short transactions: an old autonomous retry cannot override a newer user cancellation. Do not hold locks across inference or invalidate decisions for unrelated heartbeat updates. Phase 8 details durable feedback replay, attention semantics, and acceptance.

## 5. Reference request lifecycle

1. Android opens the assistant session with an activation ID and asks the platform for screen context.
2. The screenshot callback stores the activation bitmap in memory and records a device-monotonic timestamp. Android normalizes it in the background and reports capture readiness metadata to Live context.
3. The user says, for example, “Find me a recipe for this.”
4. GPT-Live delegates; the managed Responses router calls `start_workflow` with `workflow_id=recipe_from_screen` and the normalized request.
5. The backend requires the selected capture to be ready on the phone, waiting at most two additional seconds if pending. Errors/timeout reject before creating a task. It then pins the enabled version, admits transactionally, allocates the stable result page, and requests upload from the originating Android device.
6. The tool result lets GPT-Live acknowledge acceptance while Android uploads the already captured bitmap.
7. The upload endpoint validates device, activation/capture, request, size, media type, and checksum. The task becomes claimable only after upload acceptance. An upload error fails the admitted task without automatic retry.
8. A shared-pool worker executes the pinned definition when queue priority and resource availability permit, producing durable action results and task events.
9. The recipe workflow publishes one source-backed validated revision or reports a typed failure/clarification. It never publishes an unverified placeholder recipe.
10. The stable page updates and the final notification opens it only when tapped. Follow-ups create new execution runs in the same task, using retained context. Edits publish revisions at the same URL; new related outputs can become additional artifacts. A new screenshot is requested only when needed.

## 6. Timing and telemetry contract

Record the following named milestones with monotonic duration fields and correlated wall-clock timestamps:

- assistant invocation requested;
- screenshot callback received;
- normalized capture ready/unavailable metadata received by routing context;
- user speech end / provider turn boundary;
- delegation created;
- completed function item observed;
- capture preflight started and satisfied/rejected/timed out;
- workflow admission started and committed;
- continuation turn/run admitted, saved context loaded, and context restored after process loss;
- context request delivered;
- upload started and accepted;
- queue entry, pool saturation/resource blockers, worker claim and execution start;
- control requested/confirmed, writer stop confirmed, resource release/reacquisition, process exit, and automatic recovery;
- every capability start/end;
- provisional identification available;
- validated revision committed;
- completion notification queued/opened;
- first and last acknowledgment audio samples.

Device-only intervals use the Android monotonic clock. Server-only intervals use the server monotonic clock. Cross-device analysis uses correlated wall time but never presents it as sub-second proof. Reports include median, p95, failure count, cold/warm classification, model and workflow versions, and task/turn/run/attempt trace identities. Separate initial requests from follow-ups and active execution from queue or user-input waiting. Count model calls inside batched tools as well as visible agent calls.

Capture preflight waiting counts toward end-to-end admission latency. Report rejected/timed-out requests alongside successful admissions so fail-fast behavior cannot conceal capture failures. Queue/start timing remains distinct from acceptance; shared-pool saturation is an expected scheduling condition.

The managed routing approach remains the default only if ready-path measurements meet the admission objective after bounded tuning. If routing is the demonstrated bottleneck, move only workflow selection to client delegation while preserving the same tool and admission contract.

## 7. Privacy, security, and retention

- Capture is enabled for unlocked Jarvis activations without an app-specific toggle; Android's global settings and app/device policies remain authoritative.
- A bitmap is activation-scoped and may satisfy only a context request for the same authenticated device and activation.
- Unused captures remain only in Android process memory for up to 90 seconds and are cleared on lock, process death, or replacement by a new root activation. Hiding the overlay does not itself expire them. After upload, encoded bytes may remain within this lifetime for another explicit task from the same activation.
- The backend stores consumed images privately outside the web root. Task artifacts contain safe metadata and opaque context references, not exposed filesystem paths or inline pixels. Only authorized image-analysis calls and authenticated diagnostic image endpoints receive image content; logs, traces, result pages, and searchable summaries do not contain raw pixels.
- For the initial personal-project phase, the backend retains the private screenshot with its workflow run for debugging and reproducibility. It has no automatic retention deadline. A future explicit cleanup/retention feature is required before broader deployment, and the UI must identify retained screen context clearly.
- Context endpoints enforce size, dimensions, MIME, checksum, expiry, lock state, and one-time request semantics.
- Test pairing, prerecorded audio, candidate workflow enabling, and fixture-provider controls are debug/test-only and absent from release builds.

## 8. Failure and recovery rules

- Missing, stale, protected, or mismatched new capture: reject before admission with a classified reason, preserving any existing task history. Pending capture gets at most two additional seconds. A capture/context or upload error after admission fails its execution run and any associated initial result through backend handling, without automatic retry or replacement-input waiting. Failed edits preserve the last published revision. Jarvis decides whether to retry explicitly; research never runs without the required image, which ordinary continuation may reuse from retained evidence.
- Ambiguous dish: publish tentative candidates as status and ask a focused question when the plausible candidates would produce materially different recipes. Otherwise proceed with the best candidate, label the uncertainty, and preserve the analysis checkpoint. Further clarification remains available when materially needed; there is no universal one-question limit.
- Provider or source timeout: retain completed work; small safe transient retries are allowed and normal errors return to the executing agent for permitted recovery decisions. Workflow-specific tactics remain Phase 4/6 work. The capture/upload path is the explicit no-automatic-retry exception. There is no overall workflow timer.
- No trustworthy source: do not publish ingredients or instructions.
- Worker crash: recover immediately on confirmed process exit when continuation is straightforward and safe, keeping the task's reservations across replacement. Use the five-second heartbeat/twenty-second stale threshold only as fallback. One automatic recovery is allowed without durable progress; unresolved outcomes/repeated failure enter durable background decision handling independently of voice availability.
- Duplicate provider/live events: return the stored admission or capability result without duplicating a task, continuation turn/run, upload request, result page, or revision. Late events remain bound to their originating run.
- Cancellation: revoke execution authority and stop the worker/writing subprocesses promptly without waiting for an action boundary or fixed grace period. Fence late internal results, retain the last published revision/debugging context, and release reservations once old writers have stopped. Cancellation never automatically resumes the task.
- Pause/yield: default to finishing the current bounded action; Jarvis may select immediate interruption. Release reservations on confirmed stop. Resume reacquires resources and checks relevant changes before editing.
- Stale result edit: reject with the current revision; never silently merge or overwrite.

## 9. Autonomous test strategy

Most verification runs below the UI:

1. Workflow fixture tests use deterministic fake capabilities and golden typed outputs.
2. Service integration tests exercise admission, universal same-task continuation, serialized runs, context restoration/correlation, workers, progress inspection, checkpoints, result revisions, run-scoped events and controls, retries, cancellation, and migrations against an isolated database.
3. Fake-Live replay tests feed completed provider items and verify the real gateway and admission contract without WebRTC or a browser.
4. Opt-in live evaluations call the real model/search adapters and record quality, latency, usage, and source accessibility.
5. Playwright verifies the maintained result renderer and the existing Live browser smoke path. Browser screen capture is not part of the Android workflow contract.
6. One Android emulator instrumentation scenario uses a fixture app, the real assistant screenshot callback and upload path, prerecorded RTP audio through a debug seam, the real event stream, and the result WebView. Gradle, ADB, and UI Automator drive it without coordinate-based desktop control.
7. Physical Pixel checkpoints validate real wake, microphone, OEM screenshot behavior, protected content, network, battery, and notification handoff before the latency goal is claimed.

Computer/browser-control tools are diagnostic fallbacks and final visual checks, not routine test dependencies. The unattended emulator suite assumes the Android SDK, a licensed API 37 system image, emulator acceleration, and test credentials have been configured once on the host.

## 10. Completed implementation map

The [implementation roadmap](jarvis-tasks/00-implementation-roadmap.md) orders the work, migrations, operational defaults and acceptance gates. The focused plans own the detailed contracts:

1. [Task control plane and admission](jarvis-tasks/01-control-plane.md)
2. [GPT-Live routing and acknowledgment](jarvis-tasks/02-live-routing.md)
3. [Android activation context](jarvis-tasks/03-android-context.md)
4. [Guided agent workflow runtime](jarvis-tasks/04-workflow-runtime.md)
5. [Result pages, revisions, and delivery](jarvis-tasks/05-result-pages.md)
6. [Recipe-from-screen reference workflow](jarvis-tasks/06-recipe-workflow.md)
7. [Autonomous testing and observability](jarvis-tasks/07-testing-observability.md)
8. [Background orchestration and shared attention](jarvis-tasks/08-background-orchestration.md)

Phase numbers identify areas of responsibility, not a requirement to finish every feature in one area before connecting the next. The delivery dependency is:

```text
M0: narrow Codex session/tool/stop/auth contract probe + telemetry baseline
  -> M1: task contracts + shared pool + directory leases + deterministic and native execution
  -> M2: Live + Android capture + minimal result + handoff
  -> M3: independent background decisions + shared attention + voice-busy proof
  -> M4: shared workflow capabilities + minimal recipe type + two reference tasks
  -> M5: result pages/collections/storage + browser/emulator platform acceptance

Completed closure:
M6: supervisor assignments + general project coding
M7: intent/preferences/tier/fresh-context routing -> M8: native capture/audio + strict timing
M9: subscription-first execution + API handoff + dedicated credential ownership
M6 + M7 + M8 + M9 -> M10: final autonomous acceptance + current development setup
M10 -> M11: personal phone/task use accepted on 2026-10-11
```

M0 completed on 2026-09-22 for pinned Codex `0.155.1` on the current Windows
host using the guarded API-authenticated route. The live probe passed all 18
gating assertions. Subscription execution was outside that historical enabled
route; M9 subsequently proved the dedicated subscription profile and API handoff.
See the [M0 evidence and safeguards](jarvis-tasks/reports/README.md) and the
[implementation roadmap](jarvis-tasks/00-implementation-roadmap.md).

Begin with a narrow native contract probe before committing to broad adapter-dependent work. The integrated slice then uses a deterministic fixture executor to prove invocation, capture-status preflight, screenshot correlation, admission/upload failure handling, shared-pool scheduling, directory exclusion, interruption/recovery, minimal result rendering, and notification navigation. Repeat the relevant tests through real Codex, including durable continuation, mobile history replay and confirmed writer termination before lease transfer. A fixture alone cannot prove native cancellation or checkpoint behavior. Broader Phase 5 collections/storage follow these gates and need not delay the first recipe. Phase 7's tests/telemetry begin with M0.

M6/M7 may proceed independently, and M9 preparation may proceed alongside them.
M8 uses M7's interfaces. M10 requires all M6–M9 evidence, including real
subscription execution. Existing M3 evidence covers core background behavior;
M8/M9/M10 extend and revalidate it with real voice and shared authentication.
M5's former all-architecture/rollout exit was not met; moving those obligations
into M6–M11 explicitly preserves them. No numbered phase is added or renumbered.

## 11. Rollout and definition of done

Workflow execution ships disabled by default. The completed personal rollout
used isolated candidate verification and explicit enablement for the selected
host/phone; M11 normal use is accepted on 2026-10-11. Development schema/configuration cleanup follows AGENTS.md; legacy
compatibility is not required solely to preserve disposable data. Never run
competing worker launchers or transfer resources before confirmed writer stop.

**Implementation complete (M10)** requires M6–M9 and the following current-candidate
checks. **Physical deployment accepted (M11)** additionally requires physical/OEM
checks and explicit personal-use acceptance. M11 is now closed by the user's
confirmation; historical reports retain their original measured scope. Missing
subscription/account evidence cannot be deferred out of M10.

- the shared platform, general project coding and both thin reference workflows
  pass deterministic, service, live and emulator gates; M11 separately verifies
  physical delivery without requiring exhaustive culinary evaluation;
- the screenshot checks pass and one final ready-path admission is below 4.5 seconds
  (two-second target), including capture waiting; four M8 cohort exceptions remain accepted;
- median and p95 final-result latency, quality failures, and cold starts are visible in reports;
- saturated-pool tests demonstrate truthful queueing and Jarvis-controlled importance/yield/suspend, with no reserved capacity or automatic priority preemption;
- reconnects, duplicates, cancellation, missing/protected context, source failures, restarts, and stale edits are demonstrated;
- both execution kinds support same-task follow-ups with durable context, serialized runs, process-independent restoration, and fencing against older-run controls/events; retained evidence can support continuation without a fresh screenshot;
- Codex session storage survives worker replacement, desktop/mobile views reconnect to durable history without restarting work, and Jarvis can inspect the same activity; model overrides and subscription/API budget boundaries are verified;
- background management resolves supported questions/failures with no voice session; a busy background model does not block voice admission, user answers/controls, event intake, or factual updates; shared attention survives reconnect and distinguishes communicated from answered;
- Jarvis can report the current stage and worker health of a long-running workflow and can cancel it without affecting unrelated work;
- process-exit recovery, heartbeat fallback, default/immediate pause/yield, confirmed resource release, directory-overlap exclusion, and changed-file checks on resume are demonstrated;
- capture availability reaches Jarvis before admission when known, pending preflight is bounded to two additional seconds, and capture/upload failure handling needs neither a worker nor an active model;
- `research_screen@1` is added through its definition, instructions, fixtures and enablement configuration without changes to Live transport, routing prompts, admission, scheduling, lifecycle or result service;
- the full generic catalog, collection search/pagination, registered actions and namespaced storage work for Documents and Recipes; a shared saved flag demonstrates actions/storage without changing canonical result content;
- `ARCHITECTURE.md`, setup docs, and accepted limits are updated to reflect the implemented system.

## 12. Decisions already made

- Android is the production screen-context target; browser capture is not required for version 1.
- Capture occurs at assistant invocation and is uploaded only after a matching accepted workflow request.
- Capture-status metadata reaches routing context before admission. Pending capture waits at most two additional seconds; known failure rejects immediately. Capture/upload errors have no automatic retry, and Jarvis decides whether to try again.
- Managed Responses delegation is the initial router, with a measured latency gate rather than an unconditional commitment.
- Preserve direct managed voice dispatch alongside one independent event-driven background decision invocation. Both use the same task APIs, queue, Codex workers, state, and user-attention inbox; there is no direct orchestrator dialogue or shared serial reasoning gate.
- Background orchestration is the sole automatic worker-feedback decision path. Code forwards eligible factual updates and handles mechanical recovery; model calls address meaningful questions/exceptions. Delegate substantial execution, avoid periodic AI health checks, and prepare briefings on demand.
- Use compact shared state and relevant retrieved context, with durable decisions and task-level stale-action checks. Do not build a continuously synchronized master conversation, a second scheduler, or specialist planning/recovery/briefing agents.
- Curated versioned workflows handle common fast tasks; the general agent remains the fallback.
- Recipe is a thin reference task with a minimal registered result type. One adequate fetched source can support completion; additional research follows concrete needs. Recipe product features and exhaustive culinary optimisation are deferred. Subjective human recipe review is optional feedback, not an M4/reference-workflow enablement dependency.
- Codex is the worker harness for both general tasks and guided workflows. Integrate the native runtime through a small backend adapter; OpenCode and a custom agent loop are not parallel implementation targets.
- OpenAI models are acceptable initially. Use fast, economical defaults with Jarvis/user-directed overrides, decided during existing delegation/control work. Prefer subscription usage then authorized API fallback under the account's enforced budget; preserve cost visibility and stop billable retries on actual budget exhaustion.
- Expose live activity and durable task history in AI Cockpit on desktop/mobile and to Jarvis. Persist native sessions plus portable action evidence; follow-ups reuse context. Reasoning visibility covers available summaries, not guaranteed complete internal thoughts.
- Use guided agents with reusable workflow instructions and batched capabilities. Permit clarification, alternate approaches, and additional research when substantive needs justify them; avoid mandatory planning/grading loops. Implement and evaluate this selected approach without a comparative A/B or multi-harness bakeoff.
- Every task is a persistent session. Initial requests and follow-ups have separate execution runs under the same task identity; recovery attempts belong to a run, and clarification replies resume the asking run. Preserve history and compact working context after completion, releasing workers and reservations between runs.
- Follow-ups are a shared task capability, including general-agent tasks. Reuse retained evidence, edit results through revisions at their existing URL, and allow additional outputs within the same task. Separate tasks only deliberately; scope late events and controls to their originating run.
- Both execution kinds use a homogeneous pool with one warm standby and up to eight busy workers. Importance and model-issued controls govern scheduling; there are no reserved interactive slots or automatic priority preemption.
- Persist each completed action result and continuation state. Straightforward safe failures recover with bounded local retries or one automatic checkpoint resume; uncertain/repeated failures reach the background orchestrator through durable events. No specialist recovery agent is introduced.
- Detect confirmed worker exits immediately; retain the five-second heartbeat and twenty-second stale threshold as fallback. Cancellation and immediate controls do not wait for this threshold or a fixed shutdown grace period.
- Shared filesystem reservations are directory-level initially. Release on confirmed pause/yield/cancel after old writers stop; retain across immediate crash/restart handoff. Reacquire and check shared-state changes before resuming after release.
- Pause/yield finish the current action by default, with `immediate=true` available for Jarvis to choose.
- Results are progressive, but recipe content appears only after source-backed validation.
- Result pages use the existing authenticated Cockpit/Tailscale access model; version 1 adds no public or signed sharing mechanism.
- The 30-second result objective is soft and version 1 has no overall workflow deadline. Progress remains inspectable and the task remains cancellable.
- Backend screenshots are retained privately with their workflow runs for initial debugging; there is no automatic retention deadline yet.
- Jarvis asks for dish clarification only when uncertainty could materially change the recipe.
- Result pages are maintained typed views, not generated applications.
- Automated testing is API/service-first, with one real Android emulator path and limited UI-control tooling.
- Prove capture, execution, controls, and recovery with a minimal result view first; expand result pages, collections, and app storage afterward.

The historical M0–M5 evidence establishes the guarded runtime, durable task and
background core, reusable reference workflows and result platform. The recorded
M5 full backend checkpoint passed 499 tests (12 opt-in skips), followed by a
30-test targeted pass; these counts are not a current whole-system sign-off.
M6 scheduling/coding, M7 routing, M8 native voice/capture, M9 subscription/API
handoff, M10 combined acceptance and M11 personal phone use are completed under
the accepted scope and caveats. The roadmap is the completion
ledger; phase files own detailed contracts and reports preserve evidence.


## Current admission and execution contract — updated 2026-10-11

A user-originated delegation requests a fresh capture concurrently with selection. The request
is deduplicated and bound to its exact session, generation, activation and delegated turn.
The selector admits screen work directly; there is no standalone fresh-screen selector tool.
Screen workflows freeze `task-screen-context.v2`: labeled invocation/latest images, times,
provenance and missing roles. Existing per-capture upload records carry distinct uploads
concurrently; identical bytes share a blob without losing roles. Workers use
`task.context.images@2`, retain conflicting contents separately and start after all promised
uploads finish. Explicit-current admission requires latest; an unavailable additional image
is reported. Ordinary continuation restores the retained collection without another upload.
On-device images expire after 90 seconds and are cleared on lock or activation replacement.

`TASK_COMMAND_MODE=native` uses elevated native Windows sandbox provisioning, project-local
scratch, inherited directory permissions, explicit writable roots and no implicit shared
TEMP allowance. Representative Python and Node scratch/subprocess workloads must pass.
`TASK_COMMAND_MODE=trusted` is the explicitly selected weaker alternative: ordinary child
commands inherit the Windows user's filesystem access. Both modes retain broker journals,
directory leases, stale-run fencing and Job Object descendant termination; neither mode
silently retries a denied command with broader permissions. Credentials stay outside scratch.

Executable workflow YAML/Markdown and the shared acceptance configuration manifest are part
of candidate identity. Report-only documentation is excluded. Full M10 evidence must refer to
one frozen executable candidate, preserve runtime/backend/database identity and include the
fifteen required passing assertions and four explicit M8 accepted exceptions.
The former bulk speech/cold/fresh/capture cohorts are not required by the accepted
completion policy. Report failures, available sample statistics and per-clock
stage intervals separately. M11 personal use is accepted on 2026-10-11.
