# Phase 2 — GPT Live Task Routing

Status: completed for the accepted personal-use scope on 2026-10-11. The
[completion ledger](00-implementation-roadmap.md) owns status and accepted
limits; this document retains the phase contract and historical evidence.

## Completion

This phase is complete under the [final personal-use acceptance](reports/personal-use-accepted-2026-10-11.md).
The [reports index](reports/README.md) retains candidate-scoped verification,
failed observations and the accepted M8 caveats. Historical implementation
notes below do not assign new work or override the completion ledger.

## 1. Summary

Extend the existing GPT Live/managed Responses integration into the authoritative entry point for both versioned workflows and general agent tasks.

Every task is a persistent session. Route a follow-up to its existing task, creating a new execution run with retained context rather than another task. This applies equally to general agents, workflows, questions about prior work, edits, and additions.

GPT Live and its delegated model retain responsibility for interpreting intent, deciding whether clarification is necessary, selecting a workflow or general task, and phrasing responses. The backend remains responsible for authorization, durable admission, idempotency, lifecycle correctness, and truthful state.

[Phase 8](08-background-orchestration.md) adds an independent background feedback handler. Retain managed Responses delegation and direct task APIs: a voice command never waits for that handler's model, approval, or decision queue. Both paths share state, tools, and attention records; only the background path automatically reacts to worker feedback. Voice still directly handles user requests, questions about work, answers, corrections, and controls.

The ready-path target is durable task admission—and therefore any required Android context request—within two seconds of the user finishing the request. The later workflow phases own the secondary 30-second recipe-result objective.

For screenshot-dependent tasks, admission first requires confirmation that the selected capture is ready on the phone. A pending capture may add at most two seconds of asynchronous waiting before admission; this is an exceptional-case failure timeout, not an extension of the performance objective. Include that wait in measured admission latency. Upload follows admission and remains a separate readiness barrier.

Continue using managed Responses delegation initially, following OpenAI's delegation protocol: collect function calls from nested `response.output_item.done`, persist function results, append them, and explicitly continue with `response.create`. A nested `response.completed` is not a recovery source for missed function calls and does not prove that final Live speech finished. See the [GPT Live delegation guide](https://developers.openai.com/api/docs/guides/live-delegation).

## 2. Key Implementation Changes

### 2.1 Routing contract

- Add the reviewed gateway function `start_workflow(workflow_id, request, intent_id, importance?, screen_context_mode?)` for a new workflow task, with the activation/fresh selection contract from Phase 3.
- Add `continue_task(task_reference, request, intent_id, result_id?, base_revision?, screen_context_mode?)` for a follow-up to any existing task. Resolve the stable task and applicable continuation requirements server-side; append a turn/execution run and load its saved context. An ordinary continuation needs no new activation or screenshot.
- Default `importance` to `normal`.
- Initial dispatch and continuation accept optional `execution_preferences` with `model`, `reasoning_effort`, and `override_reason`. Omitted fields inherit the resolved profile; changing model/effort requires a nonempty reason. Default to Phase 4's fast/economical configuration; Jarvis may override it when requested or justified. Backend allowlists, supported settings, and provider access/budget limits remain authoritative. Validate through one shared schema rather than adding a separate model-selection hop.
- Resolve workflow version, activation, device, required context, result identity, and capability versions server-side.
- Use `intent_id` across `start_workflow`, `dispatch_task`, and `continue_task`:
  - Scope the intent to a durable backend-owned Live user turn and authenticated
    session/controller generation. The delegate chooses a per-outcome ID within
    that scope; model arguments cannot supply another turn's authority.
  - It identifies one requested outcome within the current delegated user turn.
  - Distinct explicit requests receive distinct IDs and may create multiple tasks.
  - Reusing one ID with a different tool or arguments produces an idempotency conflict instead of a second task.
- Return only durable facts:
  - acceptance status;
  - task ID and concise title;
  - admitted task-turn and execution-run IDs, including whether this is initial work or continuation;
  - task and context state;
  - stable result URL;
  - queue position and concise pool state;
  - classified rejection or failure when admission did not commit.
- Keep tool execution short. `start_workflow` performs capture readiness preflight when required and then durable admission; recipe identification, search, synthesis, and page generation run in the worker pool. Pending capture waits asynchronously for at most two additional seconds, outside the admission transaction, without blocking Live audio, interruption, or provider-event handling.
- Resolve an already committed idempotent admission before requiring capture readiness again. Exact-call replays reuse the stored result, and duplicate in-flight delivery shares the original wait/deadline rather than starting another capture or timeout.
- Preserve the authorization-reviewed gateway. GPT Live never receives process controls, raw PIDs, unrestricted shell access, or direct worker-kill capability.
- A clarification answer goes to the pending question and resumes its current run. A retry/restart targets the failed/interrupted run. A new follow-up uses `continue_task` and creates a new run; these operations must not be conflated.

### 2.2 Workflow discovery and model behavior

- At Live-session creation, build a compact catalog from enabled, validated workflow definitions:
  - workflow ID;
  - one-line purpose;
  - a few short positive routing examples;
  - required context category.
- Expose enabled IDs as the `start_workflow.workflow_id` enum and include the compact descriptions in delegated routing context. Do not add a separate discovery tool round.
- In M4, source purpose and routing examples from validated workflow metadata. Discover input requirements from the declared context contract, not from a particular output schema. Both `recipe.v1` and `document.v1` can be outputs of screen workflows. Adding a supported definition must not require editing routing prompts or adding a bespoke gateway tool.
- Persist `workflow_catalog_revision` with the Live session. The snapshot remains fixed for that session; new sessions receive current definitions.
- Admission always validates current registry state. A workflow disabled after session creation is rejected without creating a task.
- Include authenticated capture metadata in live routing context: selected activation/capture identity, mode, pending/captured/unavailable/expired state, timestamp/expiry, and a classified reason when known. Update it when capture status changes; it is not part of the fixed workflow catalog snapshot. `captured` means the phone has normalized bytes ready for upload, not that the backend has received or interpreted the image.
- Use the actual activation/fresh-capture callback status rather than periodic screen polling. Global assistant-context settings cannot prove that a particular app screen is capturable. Never promote an unavailable callback into a claim that the entire app forbids screenshots, or treat an old activation's success as current availability.
- Keep routing instructions minimal:
  - continue the relevant existing task for follow-up requests, using its result/task identity and retained context;
  - start a separate task only when the user or Jarvis intentionally separates the work;
  - prefer an enabled workflow for a clear match;
  - otherwise use `dispatch_task` for substantial work;
  - ask for clarification only when plausible interpretations would materially change the deliverable;
  - never route the same `intent_id` through more than one admission path;
  - use separate intent IDs when the user explicitly requests multiple outcomes;
  - describe only state returned by tools.
- Do not implement a hardcoded ambiguity classifier, mandatory acknowledgment sentence, or fixed fast-result speech policy.
- Choose worker-setting overrides within existing delegation when needed; do not add a mandatory planner/router call. Prefer eligible subscription execution and use the already-authorized API fallback within Phase 4's verified account boundaries. Provider account settings own spending limits; no cutoff-proof gate is required. These worker settings do not change the voice/router model configuration below.
- After durable admission, GPT Live decides whether and how to acknowledge. It must never claim acceptance, capture, identification, progress, or completion before the corresponding durable result exists.
- Publish the stable result link immediately through the task event/card so it need not be spoken as a raw URL.
- A continuation preserves the task card/route and, when editing a result, that result's URL. Acknowledgments distinguish a new task from further work on an existing one. Only a continuation that explicitly requires newly captured context uses the pre-admission capture gate.

### 2.3 Delegation lifecycle correctness

- Replace per-response completion ownership with a delegation state machine spanning every nested Responses `response_id`:
  1. Live delegation begins.
  2. Completed function items are collected.
  3. Each call is authorized and executed once.
  4. Its result is persisted before provider submission.
  5. All results for that phase are appended.
  6. `response.create` continues the delegated response.
  7. Further tool rounds repeat under the same delegation.
  8. Ownership ends only on final Live assistant completion, interruption, terminal failure, or session supersession.
- Execute calls only from fully parsed nested `response.output_item.done` events.
- Remove the obsolete `response.done` recovery behavior. Missing completed function items produce protocol telemetry and safe failure, never inferred execution.
- A nested delegated `response.completed`, a `shell_call`, or an intermediate tool round must not:
  - emit `provider.assistant_response`;
  - set `response_finished`;
  - release the controlled-response floor.
- Emit separate internal events for delegated phase completion and actual Live speech completion.
- Retain exact-call deduplication using session, generation, delegation, provider call ID, and input fingerprint.
- Serialize tool handling within one delegation while allowing the provider receiver to continue processing interruption, closure, and generation-change events.
- Fence provider result submission by session generation:
  - if admission commits before the session is superseded, the task continues;
  - no result or speech is injected into the stale session;
  - the durable task event/card remains the recovery path.

### 2.4 Service tier and failures

- Configure only the delegated GPT-5.6 Luna Responses hop with `service_tier: "priority"`, the wire value for the selected Fast/priority serving mode. See [managed Live delegation](https://developers.openai.com/api/docs/guides/live-delegation).
- Leave GPT Live audio and background workers on their independent configurations.
- Record requested tier, returned serving tier, token counts, cost estimate, and routing latency for every delegation.
- If Fast is explicitly reported unsupported, retry the routing request once using the default tier and record the fallback. Do not retry arbitrary provider failures as Standard.
- Resolve pricing from current provider configuration and record the actual returned tier and usage. Do not bake a historical token price or fixed multiplier into the task contract.
- Failure behavior:
  - malformed or partial function item: no execution;
  - unknown or disabled workflow: classified tool rejection, no task;
  - screen workflow without a valid current activation: no task; GPT Live asks the user to invoke Jarvis again on the intended screen;
  - required capture known unavailable, expired, or mismatched: reject before admission with a classified reason; no task or result page;
  - required capture still pending: wait at most two additional seconds, then reject if it has not become ready; do not mistake pending for an immediate capture error;
  - capture/context or upload error after admission: the backend fails the admitted task/result and surfaces the failure; no automatic upload retry or screenshot substitution;
  - admission failure: never claim acceptance;
  - result-submission failure after commit: retain the task and surface it through durable events;
  - interrupted acknowledgment: accepted task continues;
  - unmatched new work: route through `dispatch_task`;
  - Live failure before admission: preserve the existing typed-chat fallback.
- Jarvis retains the task/request context and decides whether to retry a rejected or failed screen request. This is an explicit new attempt through normal tools, using still-valid selected context or a deliberately requested fresh capture; a replay of the failed provider call must not silently become a retry.

### 2.5 Shared attention and background independence

- Use the existing `task_presentations` inbox and task-question records across active/inactive voice periods. Eligible factual updates are queued in code; questions needing user input are promoted by the background handler after autonomous resolution is considered. Do not add a second voice-owned feedback/recovery loop.
- Give voice a compact common situation snapshot plus its current dialogue. Refresh semantic changes from shared state and retrieve task detail when asked; do not synchronize entire model histories.
- Project pending items into quiet voice context and retain them without a session. Do not equate context delivery with speech or automatically recite the backlog on startup. On-demand briefings use current inbox facts and unresolved questions, without a briefing agent. Preserve explicit reminder/notification preferences.
- Keep exact, session-bound acknowledgment of communicated items. An announced question remains unresolved until answered; a later answer or changed state can supersede obsolete presentation. No new verifier agent or transcript-matching acknowledgment mechanism is introduced.
- User answers and controls act directly through the shared task APIs. Validate relevant current task/run/question state and refresh on conflicts; a stale background answer/retry cannot overwrite a newer user answer/cancellation. An already committed autonomous action may require a normal correction, not a fictional rollback.
- Background inference, event intake, and factual forwarding have independent execution paths. Provider or worker capacity may delay execution, but waiting for the background decision model is not part of voice admission/control.

## 3. Interfaces and Observability

- Persist `activation_id` and `workflow_catalog_revision` on the Live session in addition to generation and controller identity.
- Persist correlated capture-status metadata before admission so the router and admission gate use the same authoritative device report. Keep the screenshot itself off the server until task admission authorizes its upload.
- Add a durable routing/admission record containing:
  - session, generation, delegation, response, and provider call IDs;
  - `intent_id`;
  - selected route and workflow ID, if any;
  - normalized argument fingerprint;
  - task ID;
  - task-turn ID, execution-run ID, and resolved existing-task/result reference when continuing;
  - admission/result state;
  - requested and actual service tier;
  - timing milestones and error classification.
- Add trace milestones for:
  - user speech end;
  - delegation creation;
  - completed function item;
  - gateway entry;
  - capture preflight started, readiness observed, or rejection/timeout;
  - admission commit;
  - context request creation;
  - provider result submission;
  - continuation request;
  - first acknowledgment audio;
  - final assistant completion.
- Link these records to the existing Live event log and task timeline so later Android tests can correlate speech, capture requests, admission, execution, and result publication without UI inspection.

## 4. Test and Acceptance Plan

- Fake-provider protocol tests:
  - completed, duplicate, reordered, partial, malformed, and missing function events;
  - multiple calls and multiple delegated response rounds;
  - exact replay after a stored tool result;
  - no execution from `response.completed`;
  - explicit continuation only after every persisted result;
  - intermediate completion and `shell_call` do not release response ownership.
- Routing contract tests:
  - clear recipe request selects `recipe_from_screen`;
  - a request for a concise sourced explanation of the visible subject selects `research_screen`, using the same generated catalog and gateway;
  - adding the second definition requires only its package and enablement configuration; existing text-only definitions remain discoverable without screen requirements;
  - unmatched substantial new request selects `dispatch_task`;
  - materially ambiguous request asks instead of admitting;
  - explicit multi-intent request creates the intended distinct tasks;
  - repeated `intent_id` cannot create workflow and general-task duplicates;
  - unknown, disabled, stale-catalog, missing-activation, and changed-payload cases create no unintended task.
  - capture status is present before routing when available, and later updates are generation/capture scoped;
  - known capture failures create no task; pending capture can succeed within two seconds or reject without admission;
  - upload failure after admission fails the task without automatic retry, while an explicit Jarvis retry has a distinct attempt identity;
  - committed admission replay survives local capture expiry and concurrent duplicates do not extend the capture wait.
  - completed general-agent and workflow tasks accept follow-ups under the same task ID, with a new run and retained relevant context;
  - clarification answers and retry controls do not accidentally create follow-up runs;
  - simultaneous/duplicate follow-ups are ordered/idempotent and never execute concurrent runs within one task;
  - edits and additions reuse result identity/context without requiring a fresh screenshot; an explicitly requested new screen still uses Phase 3;
  - old-run completion and control messages cannot change a later run's state or produce a misleading completion acknowledgment.
- Lifecycle tests:
  - session interruption before and after admission;
  - generation supersession during gateway execution;
  - provider-result delivery failure after commit;
  - final speech completion, interruption, and provider failure each release ownership exactly once.
- Fast-mode tests:
  - request contains `service_tier: "priority"`;
  - returned tier and usage are recorded;
  - explicit unsupported-tier errors receive one Standard retry;
  - general errors are not silently retried on another tier.
- Run routing-quality evals against real GPT Live/Responses as an opt-in integration suite. The corpus covers positive workflow matches, negative matches, ambiguous requests, edits, cancellations, follow-ups, and explicit multi-intent turns.
- Routine CI uses deterministic protocol fixtures and gateway contracts; it requires no browser, microphone, emulator UI, or computer-control automation.
- Hold a background model call open while admitting a new voice task, answering a pending question, cancelling a task, and forwarding a factual completion. Verify those paths do not wait for background inference. Race user actions with stale background decisions and test exact attention acknowledgment across reconnects.
- Acceptance requires:
  - exactly one durable admission per intent;
  - no unsupported spoken progress claims;
  - no premature response-floor release;
  - accepted work surviving interrupted speech or disconnected Live sessions;
  - one final ready-path admission/context-request below 4.5 seconds (two-second target) under M8's fixed
    corpus and Phase 7's same-clock measurement protocol; this is a required gate;
  - capture preflight wait is included in admission timing, with timeout/rejection counts reported separately; execution queue/start latency and saturation are reported independently of acceptance;
  - enough trace data for a later autonomous Android end-to-end harness to diagnose every missed timing objective.

## 5. Fixed Assumptions

- Managed Responses delegation is the only task-routing path. OpenAI owns Luna's
  conversation input and selection loop; Jarvis validates completed streamed tool
  calls and routes them through the reviewed gateway and durable admission receipts.
  Selection may run while local transcript correlation completes, but admission
  requires verified originating-turn ownership. Model, tools and reasoning are
  session snapshots; factual context may refresh without adding a local selector.
- Require one final managed ready admission below 4.5 seconds, with a two-second target. Retain the functional ready corpus and five independent cold starts. If it misses after a focused
  correction, report stage/provider delays before expanding scope. Do not add a
  background-orchestrator approval hop or a silent client-routing fallback.
- GPT-5.6 Luna remains the delegated routing model with low reasoning and Fast mode.
- Workflow catalogs are session snapshots; registry validation at admission is authoritative.
- GPT Live may create multiple tasks only for explicit multiple requested outcomes.
- Model judgment—not backend heuristics—controls clarification and spoken acknowledgment.
- The stable result page is allocated during workflow admission and may initially show progress.
- Continuation is a general task capability with stable task identity, durable context, ordered turns, and separate execution runs/attempts. It is not a workflow-specific edit tool or a default new-task dispatch.
- Codex executes delegated worker tasks; the voice/managed Responses routing path remains separate. The voice delegate and Phase 8 background orchestrator use shared task/model/cost controls without direct agent-to-agent interaction or a shared serial inference gate.
- Screenshot-dependent admission waits at most two additional seconds for pending capture and requires captured/ready-to-upload status. Upload starts only after admission, and capture/upload errors are not automatically retried.
## Completion evidence

See the [completion ledger](00-implementation-roadmap.md) and
[reports index](reports/README.md). Earlier progress reports are historical;
M0–M11 are closed under the user's accepted scope and caveats.
