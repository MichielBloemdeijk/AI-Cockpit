# Phase 4 — Guided Agent Workflow Runtime

Status: completed for the accepted personal-use scope on 2026-10-11. The
[completion ledger](00-implementation-roadmap.md) owns status and accepted
limits; this document retains the phase contract and historical evidence.

## Completion

This phase is complete under the [final personal-use acceptance](reports/personal-use-accepted-2026-10-11.md).
The [reports index](reports/README.md) retains candidate-scoped verification,
failed observations and the accepted M8 caveats. Historical implementation
notes below do not assign new work or override the completion ledger.

## Summary

Use a small specialised agent guided by a reusable versioned task template, with efficient capabilities that batch predictable work and durable state supplied by the general task system.

The deliverable is a reusable fast-task platform. `recipe_from_screen@1` is a thin reference definition with a small registered result type; `research_screen@1` proves reuse with the existing document type. Recipe product features and exhaustive culinary optimisation remain outside scope. M5 has verified the generic Phase 5 app platform; M6–M10 close the remaining architecture contracts.

Speed is the primary reason for workflows. The template supplies the established procedure, evidence expectations, constraints, available capabilities, and completion criteria so the agent does not rediscover the process for every input. The agent retains judgment over meaningful ambiguity, alternatives, additional evidence, clarification, adaptation, and stopping.

Do not require a model decision between every low-level operation, a separate initial planner, or repeated verification on the ordinary path. A substantive model call can produce a result, ask a focused question, request a batch of capabilities, or report a classified failure. Extra model rounds remain available when new evidence, user feedback, or an error makes them useful. A two-call ordinary path is an optimisation opportunity, not a universal call limit.

Follow-ups are a general task-system feature, not a recipe/workflow subsystem. Every task retains its session and context after completion. A follow-up creates another execution run under the same task ID; recovery creates another attempt within the current run. Phase 1 owns that contract for both workflow and general-agent tasks.

## Selected Harness: Codex

Use the OpenAI Codex runtime for both general-agent and guided-workflow workers. OpenAI models are acceptable for the initial implementation. OpenCode and a separately maintained native agent loop are no longer parallel implementation candidates.

- Integrate through a small backend adapter to the local Codex App Server. Prefer the official Python SDK where it exposes the needed lifecycle/events; use the versioned App Server interface for remaining methods. Pin compatible SDK/runtime versions. Keep the protocol local to the backend; mobile clients use authenticated Jarvis APIs and events.
- Preserve the native coding loop for general tasks. Configure workflow instructions and a narrow set of Jarvis capabilities for workflows. The execution pattern below describes the contract implemented through Codex tools, events, and terminal outcomes; it is not a second model loop wrapped around Codex.
- Jarvis retains admission, scheduling, leases, process supervision, action persistence, publication, and recovery authority. Native session status or an interrupt acknowledgment does not independently establish a Jarvis terminal state or prove that all writers stopped.
- Map each task to its persisted Codex thread, and each execution run/attempt to the relevant native turns/items. Keep native session storage outside disposable workers and reuse it for compatible follow-ups and recovery. Preserve the portable evidence/context contract when a native session is unavailable or incompatible; record any replacement thread under the same Jarvis task.
- Stream visible messages, tool activity/results, file changes, errors, and available reasoning summaries into Phase 1's durable task timeline. Reasoning visibility is limited to what the model exposes; it is not a complete internal thought transcript. Persist completed action evidence separately from transient streaming deltas.
- Prefer supported configuration and tool integration over maintaining a runtime fork. Determine the enforcement boundary for native shell/file tools in the early integration milestone; do not silently weaken resource ownership or action-persistence requirements to retain a built-in tool.

This choice favors the native OpenAI coding runtime and subscription integration while retaining the speed controls required for guided tasks. It does not establish a benchmark speed advantage, unlimited subscription concurrency, or verified whole-process-tree cancellation.

## Model, Reasoning, and Cost Policy

- Start from a configured fast, economical worker model and reasoning-effort default. Templates may supply defaults. The delegated voice model or Phase 8 background orchestrator may override them when requested by the user or when it judges different settings better for the task, within their shared task-control contract.
- Make that judgment during existing delegation/control work, without a mandatory separate routing call. The backend validates supported settings, permitted models and known provider-access/billing blockers; the provider enforces the configured API budget. Lower reasoning effort is a preference, not a hard thinking-time limit; retain the no-overall-task-timeout decision.
- Pin the initial effective settings with the run. Apply any later authorized override at a supported turn or controlled continuation boundary and append its settings/reason to the execution history; never rewrite the original configuration or change settings halfway through an in-flight model request.
- Prefer Codex-managed ChatGPT subscription authentication for eligible private Jarvis use, then separately billed API fallback as authorized by the user. Rely on provider account spending settings; Jarvis has no cutoff-proof or duplicate budget engine. Retain cost visibility and economical defaults. Jarvis may wait when that better fits the task, but no new human permission is required for the agreed fallback. On actual billing exhaustion, preserve context and expose the blocker without immediate repeated paid requests.
- Use supported authentication flows and account entitlements. Subscription access to Codex does not fund separate GPT Live/Responses routing, vision, retrieval, or other API services. Include those costs, hidden model calls, and retries in cost accounting; exact model IDs and budget amounts remain deployment configuration.
- Record subscription/API mode, effective model/effort, available quota/usage, and known or estimated API cost with clear provenance. Subscription limits still constrain the pool: eight workers is a local ceiling, not a guarantee of eight simultaneous subscription requests.
- Treat a subscription/API handoff as an explicit, recorded continuation decision. Verify compatible session reuse; otherwise restore portable context under the same task. Never replay an uncertain side effect merely to change billing mode.

## Selected Execution Pattern

1. Load the run's immutable template/configuration and the relevant persistent task context: current request, constraints, prior decisions, evidence, completed tool results, and artifacts/results.
2. Invoke the model with the reusable procedure and narrow permitted capability set. Fold judgment into this substantive call rather than invoking a separate routing/planning/grading model at every step.
3. Accept a typed decision: publish/complete, clarify, request capabilities, or fail. Revising an existing result is a normal completion/publication operation targeting its ID and base revision, not a separate conversation mechanism.
4. Validate requested capability identities, argument schemas, permissions, ownership, cancellation, and run/attempt fencing in code.
5. Use native hosted search/page reading for research. Recover from source errors by trying alternatives. Respect dependencies and avoid conflicting mutations. Jarvis retains lifecycle, permissions and publication tools.
6. Persist each completed capability result and continuation state before advancing. Feed the relevant observations into the next substantive model call only when further judgment is needed.
7. Validate hard output requirements and commit completion/publication through the shared task/result services. Clarification checkpoints the current run and waits for its answer; a later follow-up starts a new run in the same session.

The minimum control flow is this small decision loop, named observable stages, and bounded batches of independent capability calls. A general graph compiler, separate planner fleet, and claim-by-claim dependency graph are not initial requirements.

## Template Responsibilities

The selected authoring format is a source-controlled workflow folder containing readable Markdown instructions and a small declarative configuration file. Instructions describe the reusable procedure, evidence expectations, ambiguity handling, and completion criteria. Configuration declares identity/version, permitted capabilities, input/output schema references, and defaults. Shared tools contain executable Python or other implementation code; workflow definitions are not Python programs or rigid step graphs.

Use `workflow.yaml` plus `instructions.md` under `backend/workflows/<workflow_id>/<version>/`, with fixture files under that version's `fixtures/` directory. Parse YAML with the existing PyYAML dependency using safe loading and validate with Pydantic using forbidden unknown fields. Validate definitions before enablement and pin the instructions/configuration/capability revisions for each run. Editing instructions cannot silently grant a tool permission or alter an active run. Self-authored/runtime-generated workflow enablement remains outside the initial scope.

A versioned template declares:

- purpose, routing examples, and supported input/output contracts;
- reusable procedure and named stages where order or evidence matters;
- permitted capabilities, including any stage-specific restrictions;
- evidence requirements, uncertainty guidance, and completion criteria;
- input requirements for initial execution and continuation, including when newly captured context is actually needed;
- per-capability timeout/concurrency policy, normalisation, and deterministic validation;
- model configuration and semantic guidance that favour direct completion over repetitive planning or review;
- deterministic fixtures and representative quality/latency acceptance cases.

### M4 registry extension and compatibility

Extend the existing loader rather than replace the task runtime. Resolve declared input/context requirements, capability identities and output types through shared registries. The current two fixed input/output profiles and output-schema-based tool selection are integration scaffolding, not the final extensibility boundary. Tool exposure follows the validated declared capabilities; screen discovery follows required context, not a `document.v1` check.

Include a concise purpose and routing examples in the definition's validated catalog metadata. Build the voice catalog from enabled version/digest entries through the existing gateway. Adding a supported definition must not require editing routing prompts, creating a per-workflow tool or changing admission/control semantics. Registry validation remains authoritative at admission.

Use the current v2 manifest and input contracts throughout definitions, fixtures and pinned snapshots. Do not retain obsolete schemas solely for disposable development data. Both screen reference workflows use `screen-request.v2` and the labeled `screen_images` collection. Text-only definitions remain supported without screen requirements. Never reinterpret a pinned digest or broaden its permissions.

Reusable operations live in shared tools, while task-specific procedure lives in instructions. Register `recipe.v1` through the same backend validation/publication and frontend renderer interfaces as `document.v1`; result allocation, URLs, revisions and delivery remain generic. Once these shared extensions exist, adding `research_screen@1` requires only a definition, instructions, fixtures and enablement configuration.

The template supplies guidance and constraints without encoding a decision tree for every possible dish, user preference, or failure. Multiple valid preparations can be selected, compared, or presented as alternatives when appropriate. Clarification is available when ambiguity materially affects the deliverable; a universal maximum of one question is not a runtime rule.

## Persistent Context and General Continuation

- Use Phase 1's task turns, execution runs, and attempts. Do not introduce a workflow-owned conversation store or require a resident process to preserve context.
- Retain a simple durable context bundle: user constraints, relevant interpretation/decisions, evidence with source identities, completed tool results, current artifacts/results, and references to full history.
- Load a compact working context and retrieve additional retained detail when necessary. Full durable history remains available; a provider cache or provider-side session is an optional optimisation, not the source of truth.
- A follow-up can change criteria, revise content, add a section or artifact, ask a question, or request further research under the same task ID.
- Let the model judge which prior evidence remains applicable and what needs revisiting. Deterministic checks still enforce ownership, freshness requirements where declared, and schema/version compatibility. Do not build a general per-claim invalidation graph initially.
- Reuse existing image analysis and uploaded context when relevant. Ordinary follow-ups do not require an active Android session or another screenshot. A run that explicitly needs a new screen uses Phase 3's capture preflight and fail-on-error upload contract.
- New execution runs pin their own immutable definition/harness/capability configuration and initial model settings. Later authorized model/effort changes are appended as execution decisions per the policy above. Reuse portable prior context, but do not reinterpret an incompatible old execution checkpoint as a new run's checkpoint.
- Result edits use the existing result ID and expected base revision. Additions may update that result or create another artifact/result within the same task as appropriate. A failed update preserves the last good revision.
- Completion releases the worker and directory reservations. A subsequent run reacquires necessary resources and checks relevant external/shared changes before editing.

## Fixed Runtime Requirements

- Jarvis owns task admission, the shared queue, worker supervision, session/run lifecycle, cancellation, yield/suspend, retry, fencing, heartbeat, checkpoint, artifact, and result semantics. A harness must not replace those systems.
- Every capability call and durable write carries task, execution-run, attempt, stage, fencing-token, and logical-operation identities. Stale callbacks from an earlier run cannot mutate a later follow-up.
- Pin the exact template revision, harness revision, initial model configuration, capability versions, and normalised run inputs before execution. Editing a visible template does not change an active run; explicit model/effort overrides follow the recorded-boundary policy above.
- Capability access is enforced in code. Arbitrary shell, browser, filesystem, subagent, and application-control tools are absent from workflow execution unless a future trusted template explicitly permits a narrowly wrapped capability.
- Side-effecting capabilities declare safe replay/idempotency and reconciliation behaviour. Ambiguous external outcomes cannot be silently replayed.
- Persist completed results and continuation state before proceeding. Resume reuses successful work and follows Phase 1's safe automatic-recovery limits.
- Emit structured stage/capability timing, usage, retry, and failure events. Inspection exposes current progress and available controls without leaking prompts or credentials.
- Keep Jarvis's capability contracts and stored evidence independent of provider-specific schemas where practical. Codex/OpenAI is the selected initial worker stack; broad multi-provider runtime support is not a v1 requirement. Vision and retrieval remain replaceable capability adapters.
- Adding another template must not change task continuation, admission, scheduling, worker supervision, Android context correlation, or control semantics.

## Speed, Flexibility, and Quality

- Optimise the common path by supplying the procedure in advance, batching routine operations, parallelising independent reads, reusing saved evidence, and keeping working context focused.
- Count all model calls, including any hidden inside composite capabilities. Moving a model call behind a tool does not remove its latency or cost.
- Do not impose an exact model-call count for every request. Additional evidence or clarification is allowed when useful; finish promptly once the requested outcome is adequately supported.
- One adequate provider-backed source can support a reference result. Corroboration, extra alternatives and further research are conditional on the request or an evidence gap, not mandatory production stages. Use a small fixed evaluation set and bounded tuning of measured bottlenecks; report remaining performance misses.
- A separate production semantic grader and automatic repeated self-review loop are excluded initially. Normal model judgment and structured generation remain available.
- Code checks schemas, required fields, ownership, declared constraints that are mechanically checkable, and source-reference integrity. Code cannot generally prove factual support, cooking-method correctness, or usefulness; those judgments require model behaviour and representative offline evaluation.
- A concrete validation error or recoverable tool failure is returned to the agent, which may adjust its output/tactic, choose another permitted source, clarify, or fail. Do not impose a universal repair-once rule.
- Small bounded adapter retries are allowed for safe classified transient failures. Phase 3 capture/upload errors are the explicit no-automatic-retry exception; Jarvis decides whether to initiate another attempt.
- Preserve the existing soft result objective and per-operation timeouts. There is no overall task deadline or automatic failure when a recipe takes longer than thirty seconds.

## Concrete Workflow Definition

The existing loader uses `workflow-definition.v1`; M4 extends its registries and adds validated catalog metadata with compatible versioning as described above. This abbreviated target example shows the common execution fields, not the complete extended catalog schema:

```yaml
schema: workflow-definition.v2
id: recipe_from_screen
version: 1
purpose: Find a useful source-backed recipe for the dish visible on screen.
routing_examples:
  - Find me a recipe for this dish.
instructions: instructions.md
input_schema: screen-request.v2
output_schema: recipe.v1
required_initial_context: [screen_images]
capabilities:
  - task.context.read@1
  - task.context.images@2
  - research.native@1
  - task.question.ask@1
  - task.result.publish@1
  - task.finish@1
defaults:
  execution_profile: fast
fixtures: fixtures/cases.yaml
```

Schema names resolve through a maintained Python registry. Capability identities resolve to installed, versioned implementations. YAML cannot import Python, invoke a shell, choose credentials, grant filesystem permissions or change global concurrency. Restrict definition-relative paths to the version folder, reject duplicate YAML keys, unknown capabilities and incompatible schema versions, and require UTF-8 instructions plus a valid fixture index. Enablement is a separate explicit deployment allowlist of ID/version/digest, disabled by default.

The `fast` profile initially resolves to configured Luna/low, subject to model/effort/modality validation at setup. Preserve Jarvis's permitted overrides. Resolve the profile and tool versions into the run snapshot, alongside a digest of the manifest and instructions; never leave a mutable profile alias as the sole record of what ran. Source-controlled folders are the authoring source; immutable stored copies/digests preserve already-admitted runs across deployment.

## Codex Facade and Execution Profiles

Implement a small asynchronous `CodexRuntimeAdapter` behind the existing execution context/sink seam:

- `inspect_runtime()` returns protocol/version/model/effort/auth capabilities without exposing secrets.
- `open_session(binding, profile)` starts or resumes a compatible native thread and persists the resulting binding before work begins.
- `start_turn(input, operation_id)` and `stream_events(cursor)` correlate native turns/items to the Jarvis run/attempt. `inspect_turn()` reconciles reconnects before another turn is allowed.
- `steer_turn(input, expected_turn)` is used only where supported and consistent with serialized run/control rules. Otherwise persist the input for the next safe boundary.
- `interrupt_turn()` requests interruption; `stop_execution_boundary()` and `confirm_stopped()` establish the independent process/resource stop guarantee.
- `close_host()` releases disposable execution resources while retaining session storage. Native history inspection and portable-context export remain available after completion.

These are Jarvis facade operations, not claims that every method is a Python SDK method. Prefer the official [Python SDK](https://learn.chatgpt.com/docs/codex-sdk); map unsupported lifecycle operations to the pinned [App Server](https://learn.chatgpt.com/docs/app-server) protocol over local stdio. Generate/validate protocol types against that exact runtime. Do not require experimental WebSocket or dynamic-tool registration for v1. Use native legacy session storage initially; Jarvis pagination is implemented over its own durable history.

Profiles are: general coding (journaled file and command tools within leased projects), workflow (declared Jarvis capabilities), and background orchestration (inspection and shared task actions only). Profiles reuse the facade, not a second agent loop. Use the local stdio MCP bridge with a short-lived server-issued run/attempt identity; it cannot accept a model-supplied task ID or token as authority. Explicitly enumerate enabled tools, disable unwanted native/MCP/subagent surfaces and validate the resulting inventory. MCP alone is not a filesystem sandbox.

### M6 general coding completion

The current brokered profile exposes only private-workspace `file_read`,
`file_write` and `task_history`. Extend it to inspect/search and edit leased
project roots and run build/test commands. Resolve a tool's target and working
directory against server-authorized resources; reject escapes and stale leases.
Command tools execute through the pinned runtime's tested sandbox inside the
attempt's owned process tree, outside database transactions. Journal started,
completed and uncertain outcomes with retained output/artifact references before
returning success. Native local actions remain gated until their workload and
recovery contracts pass; no unrestricted retry is allowed. Bounded command execution
and confirmed descendant stop must remain usable while a model turn is active.

Prove a source edit/build/test cycle, retained observations after restart,
changed-project inspection after reacquisition, and cancellation/replacement of
a command with writing descendants. External mutation connectors remain future
scope until their own observation/reconciliation capabilities exist.

Use host-supported OS containment with a dedicated execution identity and access only to authorized workspaces. On Windows, a Job Object can track/terminate descendants, but is not itself filesystem access control; on Linux, process groups alone likewise do not constrain filesystem access or escaped children. M0 must select and test the actual host's sandbox/access controls plus descendant containment. Unknown or unsupported enforcement fails closed for that profile. Native tools remain enabled only if they satisfy action-persistence and stop/resource contracts. A supported wrapper is preferable to a fork; any required narrow runtime change must be named and tested before integration proceeds.

## Action Journal and Portable Context

Use a versioned JSON context bundle, `task-context.v1`, containing current constraints, accepted decisions/assumptions, evidence IDs, operation/result references, artifact references and current result revisions. Keep large source text, images and native transcripts in private durable storage referenced by ID and digest. Context is a compact retrieval index, not a second complete transcript or an assertion of hidden reasoning.

Store each logical tool operation in `task_operations`: ID, task/run, originating attempt, capability/version, normalized argument digest, state, result/artifact reference, external idempotency key when supported, timestamps and reconciliation details. Native thread/turn/item IDs are correlation fields. Recovered attempts refer to the original successful operation; they do not relabel it as fresh execution.

1. Validate authorization, current run/attempt fence and resource access; durably record `started` before the effect. Operation IDs are issued by the adapter. A repeated request with changed arguments is a conflict.
2. Execute outside a database transaction. Persist large result data atomically before committing its reference, terminal operation state and task event/checkpoint together. Return tool success to Codex only after this commit.
3. On replay, return the retained successful observation for the same authorized operation. New user requests remain distinct operations even if arguments match. Read caching uses explicit evidence/freshness policy, not blanket argument deduplication.
4. An interrupted operation becomes `uncertain` when its external outcome is unknown. Reconcile external identity/state before retrying a side effect. Completed independent reads remain reusable. Stale attempts cannot publish completion or advance the current run.

Local database publication can be atomic/idempotent; arbitrary shell commands and third-party effects are not automatically exactly once. If native execution cannot enforce persistence before advancing, M0 must supply a real boundary or classify the unsupported surface. A post-hoc event collector is insufficient for the promised per-action behavior.

The shared broker tool result envelope contains `operation_id`, status, typed data, evidence/artifact references, warnings and classified errors with replay guidance. Common terminal tools are `task.question.ask`, `task.result.publish` and `task.finish`. Publication accepts schema ID, content, native `source_event_sequences` and optional result ID/base revision; it commits the validated publication and run completion together through Phase 5's transaction. `task.finish` handles explicit answer-only completion or classified failure, rather than requiring a second completion call after publication. A plain native final message alone does not bypass Jarvis's durable terminal validation. Question creation checkpoints the asking run and leads to waiting-input after confirmed stop; no worker stays occupied waiting for the user.

## Storage, Authentication and Failure Boundaries

Keep the dedicated Jarvis Codex profile/session store outside disposable worker directories. Store per-task native bindings with profile ID and runtime compatibility metadata; never store OAuth/API secrets in task rows, artifacts, prompts or copied portable bundles. Separate subscription and API authentication profiles rather than modifying credentials underneath an active host. Compatible native continuation is tested; otherwise create a replacement native binding with retained portable context under the same Jarvis task.

Use supported account flows and persisted refresh ownership. Do not copy a rotating `auth.json` into multiple concurrent jobs. M0 establishes the supported concurrency arrangement; the conservative initial subscription concurrency is one until proven otherwise, with authorized API fallback for additional work. See [private automation authentication](https://learn.chatgpt.com/docs/auth/ci-cd-auth). Authentication expiry/revocation can still require user sign-in; autonomous recovery cannot manufacture account access.

### M9 required authentication completion

Subscription-first execution is required for final implementation completion.
Use one shared credential execution/refresh lease across worker and orchestration
hosts, bound to the actual owner process and generation. Do not release it on
heartbeat expiry until the old owner is confirmed stopped. Keep runtime profiles
and filesystem authority separate from authentication selection; a subscription
profile must not give the background model coding tools.

When subscription capacity is busy, unavailable or ineligible, use the authorized
API route without blocking task admission. Persist the route and handoff reason;
switch only at a safe turn boundary, using portable restoration when native
session reuse is incompatible. Uncertain effects remain blocked for reconciliation.
Test actual subscription continuation and actual API fallback, including a forced
quota/availability fixture that does not exhaust the real account. Actual
authentication, quota and billing errors remain classified and durable. Missing account authorization/evidence blocks M9's live
exit, while implementation and deterministic fixtures continue.

Use the [roadmap defaults](00-implementation-roadmap.md#implementation-defaults-and-limits) for bounded transport/read retries and timeouts. Connection recovery inspects the existing native turn before considering a replacement. Capture/upload retains its explicit no-retry exception. Provider budget exhaustion is a durable blocker, not a transient retry loop. Pin actual installed/tested SDK/runtime versions during M0; do not select a version number by assumption in this design.

## Early Codex Integration Milestone

Alongside the deterministic control-plane slice, prove one real Codex-backed task before expanding the full workflow and presentation layers:

1. Start a task, expose live activity, finish it, release its worker, and continue through the same saved task/session after restarting the worker host.
2. Disconnect/reconnect the mobile task view, retrieve durable history without restarting execution, and demonstrate Jarvis inspection of the same completed actions and results.
3. Exercise confirmed pause/cancel with a writing subprocess; transfer the directory only after every old writer has stopped. Resume after another task edits that directory, reacquire access, and inspect the changes. Do not depend on a fixed grace period or treat native turn interruption as process-exit confirmation.
4. Crash between actions and during an action. Reuse durably completed work, preserve uncertain outcomes for inspection, and reject stale internal writes/events. Map how each permitted native tool records completion before subsequent dependent work; a streamed event alone does not establish an authorization or checkpoint barrier.
5. Prove the workflow tool allowlist cannot be bypassed by native shell/file/subagent tools. Verify supported model/effort overrides and budget-controlled fallback with deterministic fixtures; real billed calls remain opt-in.

Record any required wrappers, containment, or narrow runtime changes before broad integration. If a native-tool contract cannot be met, report the specific gap rather than claiming the milestone passed. This validates Codex, not a renewed harness-selection exercise.

## Verification for the Selected Implementation

Normal correctness, quality, and latency verification remains required; the decision to skip A/B testing does not remove these acceptance checks.

- Provide one CLI path for validating a template and running the selected runtime against deterministic fake model, vision, search, and publication adapters.
- Routine tests need no browser, Android UI, microphone, or live service.
- Use compact reference cases for completion, material ambiguity, partial/all source failure, invalid output, context-reusing follow-up and a failed/cancelled update. Keep adversarial content, capability denial, stale publication, pause/yield and recovery coverage in shared platform suites; link those results rather than duplicating a culinary matrix.
- Test same-task follow-ups for both general agents and workflows: a simple addition, changed criteria requiring new evidence, preserved context after worker/provider-session loss, compacted-context retrieval, and stale prior-run callbacks.
- Test native thread/turn mapping, process-independent session storage, mobile history replay, duplicate event reconciliation, default/overridden model settings, and authorized API handoffs versus actual provider quota/billing denial. No assertion may depend on access to hidden internal reasoning.
- Verify that clarification resumes its current run, a follow-up starts a new run, and recovery attempts do not create another task or user turn.
- Verify simple revisions can reuse saved evidence without another screenshot or repeated retrieval, while evidence-changing requests can request the additional work they need.
- Use agent-run opt-in live examples to assess usefulness, evidence support, clarification appropriateness, repeated-run consistency and latency. Preserve the evidence and assessment rubric in the report. Subjective human recipe review is optional feedback, not a gate for M4; an optional offline judge is not part of production execution. Schema validation alone cannot establish semantic quality.
- Prove that adding `research_screen@1` after the shared extensions changes only its package and enablement configuration. Test catalog discovery, correct routing, shared retrieval and document publication without new core branches. Verify existing manifests and pinned runs still load with their original permissions.
- Record end-to-end and stage latency, queue time, provider/capability counts, token usage, cache usage where available, failures, and trace/result links. Separate initial requests from follow-ups and active processing time from time waiting for user answers.
- Store acceptance evidence as machine-readable data and a concise report. No comparative arms or harness ranking are required.

## Evidence Informing the Choice

These sources motivate the selected pattern; their benchmark gains are not Jarvis performance guarantees.

- [Codex App Server](https://learn.chatgpt.com/docs/app-server): native session history/resume, lifecycle controls, and streamed events for a custom client. Validate the selected methods against the pinned runtime.
- [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk): official Python integration with the local App Server and a pinned runtime dependency.
- [Codex authentication](https://learn.chatgpt.com/docs/auth) and [private automation guidance](https://learn.chatgpt.com/docs/auth/ci-cd-auth): subscription versus API authentication and supported account-management boundaries. These do not make subscription usage interchangeable with general API credit.

- [Anthropic: Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents): combine simple workflow/agent patterns and add complexity where it improves outcomes.
- [ReAct](https://arxiv.org/abs/2210.03629): observation-driven decisions support adaptation and exception handling.
- [ReWOO](https://arxiv.org/abs/2305.18323): separating routine tool execution from repeated model reasoning can reduce redundant processing.
- [LLMCompiler](https://arxiv.org/html/2312.04511v3): independent tool execution can be parallelised while preserving dynamic replanning.
- [Self-Refine](https://arxiv.org/abs/2303.17651): iterative feedback can improve outputs, but introduces additional generation rounds and is not a reason to grade every successful request.

## Boundaries

- Phase 1 owns persistent task sessions and continuation for all execution kinds.
- Phase 2 owns Live routing to new tasks versus continuation and accurate acknowledgments.
- Phase 3 owns new screen capture, availability, and upload.
- Phase 5 owns stable results and immutable revisions.
- Phase 6 defines the recipe-from-screen template on this runtime.
- [Phase 8](08-background-orchestration.md) defines background feedback handling, independent direct voice dispatch, and shared attention. Workers emit durable facts/questions; they do not choose competing voice/background recovery owners. Background decisions do not occupy a worker slot, and long repairs are ordinary worker tasks.
## Completion evidence

See the [completion ledger](00-implementation-roadmap.md) and
[reports index](reports/README.md). Earlier progress reports are historical;
M0–M11 are closed under the user's accepted scope and caveats.
