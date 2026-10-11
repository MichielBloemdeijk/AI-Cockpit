# Phase 8 — Background Orchestration and Shared Attention

Status: completed for the accepted personal-use scope on 2026-10-11. The
[completion ledger](00-implementation-roadmap.md) owns status and accepted
limits; this document retains the phase contract and historical evidence.

## Completion

This phase is complete under the [final personal-use acceptance](reports/personal-use-accepted-2026-10-11.md).
The [reports index](reports/README.md) retains candidate-scoped verification,
failed observations and the accepted M8 caveats. Historical implementation
notes below do not assign new work or override the completion ledger.

## 1. Outcome and Selected Structure

Jarvis continues managing work without an active voice session. It resolves worker questions from available context, chooses recovery actions when mechanical recovery is insufficient, and retains questions and useful information for the user. Starting voice neither pauses autonomous work nor creates another automatic worker-feedback handler.

Use managed Responses delegation for voice as the sole routing path. There is
no client-selection fallback, shared model queue or background-orchestrator
approval step before voice task admission.

```text
Voice model -> managed delegated model ----+
                                          |
                                 shared task APIs
                                          |
                                 task execution queue
                                          |
                                     Codex workers
                                          |
                                 durable task events
                                          |
                          background feedback service
                             /                    \
                factual forwarding       one decision invocation
                     in code              at a time when needed
                         |                    /          \
                         |           shared task APIs     |
                         +-------------------------------+
                                          |
                               shared user-attention inbox
                                          |
                       active voice context / retained updates
```

The two decision paths share the same task backend, execution queue, Codex pool, capability rules, preferences, and canonical state. They do not converse with one another or seek one another's approval. Voice acts on the user's requests; background orchestration is the sole automatic decision path for worker feedback. Deterministic retries and worker supervision remain Phase 1 responsibilities.

## 2. Responsibilities and Availability

- **Voice model and managed delegate:** interpret spoken requests; dispatch, continue, inspect, answer, or control tasks through the shared APIs; converse and present grounded updates; acknowledge the exact updates communicated. No background inference is required for these operations.
- **Background feedback service:** durably receive task events, forward eligible factual updates in code, invoke reasoning for meaningful decisions, and record autonomous actions or unresolved questions. Event intake and factual forwarding continue while a model invocation is busy.
- **Background decision model:** inspect the relevant task/context, answer a question when supported by instructions/evidence, choose an allowed recovery/control action, delegate substantial work, or retain a question for the user. Do not invent an unavailable user preference or treat task content as new authority to broaden permissions.
- **Codex workers:** perform substantive research, coding, workflow execution, and repairs. A delegated repair is a normal task; its completion reaches the feedback service through normal events. The orchestrator does not retain its decision slot while waiting for that worker.
- **Backend:** enforce current-state checks, idempotency, permissions, budgets, task lifecycle, process ownership, and leases. The existing queue and supervisor perform ordinary scheduling; there is no second AI scheduler assigning every worker.

Start with one background decision invocation at a time. It is separate from worker execution and the managed voice delegate. Incoming voice work, deterministic feedback delivery, and task inspection must not wait for this slot, even if all Codex worker slots are occupied. Worker capacity, conflicting resource leases, and provider limits still affect execution; accepted work does not imply immediate worker start.

Several simultaneous complex blockers can wait for background judgment. Add background decision concurrency only if measured backlog justifies it. Do not add specialist planning, recovery, briefing, or verifier agents initially.

## 3. Durable Events and Decision Loop

Use the existing database and task event log, with durable consumption progress and pending-decision records as needed. Do not introduce a message broker or independent copies of task state. The task execution queue, operational feedback, and user-attention inbox have different purposes, even when all are stored in the same database.

1. A task transaction records an observed event. Live task history and current state remain inspectable immediately, without waiting for the feedback model.
2. The feedback service applies deterministic handling: project an eligible factual update or record a pending decision. Advance consumption progress only with durable recording of the handling outcome or pending work. Never advance past a decision merely because an in-memory model request was started.
3. Select a pending decision, refresh the relevant state, and discard an obsolete trigger. Coalesce redundant pending events for the same issue when appropriate, retaining source identities; do not drop distinct questions or new user instructions.
4. Construct compact context and invoke the model. It may perform bounded inspection and use the shared task tools. Substantial execution becomes a worker task. There is no obligatory planner, classifier, reviewer, or summary call around this invocation.
5. Validate each proposed action against current state and commit its effects/audit through the normal service. Mark the decision handled only after its result is durable. Reject stale actions and refresh state rather than forcing the old decision through.
6. Release the background decision slot. Wait for another meaningful event or an explicitly scheduled due-time wakeup.

Meaningful triggers include a newly pending worker question, a failure remaining after Phase 1's simple recovery, a substantive blocker or suspected stall requiring judgment, and a relevant new answer/instruction or delegated repair result. Ordinary heartbeats, token deltas, successful intermediate tools, and unchanged healthy states do not require model calls. Straightforward completion updates are forwarded automatically.

"Always on" means durably available, not a model continuously reasoning. Code may check timers and service health; do not poll healthy workers with AI. Reuse existing bounded transport retries and process supervision. If the model/service fails, retain pending decisions and expose health/backlog; worker execution, voice dispatch, and factual forwarding remain usable.

Deduplicate automatic decision work by the issue/event and relevant state generation. Processing an orchestrator's own audit event must not restart the same decision indefinitely. Repeated identical failures without progress do not create endless retry or repair tasks: retain the unresolved issue and wait for meaningful new evidence, an explicit due-time retry within policy, or user input. Preserve Phase 1's bounded automatic-recovery rules; orchestration does not reset their history.

## 4. Shared Context and Autonomous Judgment

- Keep one canonical source for relevant user instructions/preferences, task goals, current controls, worker availability, blockers, questions, artifacts, and decision reasons.
- Supply a compact common situation snapshot to both decision paths. Voice additionally receives recent dialogue; background reasoning receives its triggering event and retrieves needed task detail. Do not copy every worker transcript into both prompts.
- Persist accepted task-changing user instructions before applying them. Store autonomous decisions with their reason, source event, affected task/run/question, and observed relevant revision. Do not rely on one model remembering what the other decided.
- Retain full evidence and task history for retrieval. Context caches or native model sessions may reduce overhead but are not the durable authority. No endlessly synchronized master conversation is required.
- Let model judgment resolve ordinary ambiguity and exceptions using the user's goals and retained context. If information only the user can supply is needed, retain a question and allow unrelated work to proceed. Do not route every worker question to the user by default.
- Keep autonomous actions within existing task/standing instructions, permissions, and spending limits. A recorded assumption is not a claim that the user supplied that fact. Explicit user instructions take precedence over conflicting uncommitted autonomous actions.

## 5. Minimal Coordination at the Shared APIs

Both paths may create, start, continue, update, inspect, answer, and control tasks through the same validated interfaces. Only the background path reacts autonomously to worker feedback; voice acts on user requests and reads shared facts. Task results do not independently trigger a second recovery loop in voice.

Use short transactions for action validation and commit. Never hold a database lock or global orchestration lock during model inference or worker execution.

- Bind controls and decisions to the intended task, execution run, and question where applicable. Check relevant goal/control/question revisions or equivalent preconditions before applying an autonomous mutation. Do not invalidate decisions merely because a heartbeat or unrelated progress item arrived.
- Preserve Phase 1's idempotency, attempt fencing, and directory reservations. Shared context does not replace these checks.
- Example: if voice cancels a task while background reasoning prepares a retry, the retry based on the older state is rejected. A later explicit user retry remains possible through normal controls.
- Example: if the user answers a question through voice before an autonomous answer commits, the obsolete autonomous answer is rejected. If the autonomous answer already committed, process the user's correction against the new state as a normal task update; never claim earlier effects were rolled back.
- User answers go directly to the task-question API, without waiting for the background model. The resulting event makes any pending background decision obsolete and refreshes its context normally.

This is backend concurrency control, not agent negotiation. No orchestrator-to-orchestrator message channel, exclusive global planning lease, or general multi-agent coordination framework is required. A simple single-consumer claim may prevent duplicate background decision execution during service restart/deployment.

## 6. One User-Attention Inbox

Reuse `task_presentations` as the communication authority and `TaskQuestion` as the question/answer authority. Extend these records if needed rather than creating competing voice and background inboxes. Notification transport remains separate from both.

- Persist each user-facing item with a stable identity and source event/task/run/question references. Automatically forward configured factual completion/status updates using maintained metadata/templates; no model review or paraphrase is required before queueing them. Routine progress stays in live task history unless the existing presentation policy makes it attention-worthy.
- Raw worker questions and recovery events first reach background handling. Surface unresolved user questions through this inbox after judgment establishes that user input is needed. They remain directly inspectable in task details while pending; a stalled background model cannot hide task state from an explicit user inspection.
- With no voice session, retain the item. With an active session, project the same pending item and subsequent semantic changes into voice context. Context delivery is not a separate item and does not mark it communicated.
- Do not automatically read the entire backlog on session startup. Routine updates/questions remain available for on-demand briefing and contextually relevant conversation. Preserve explicit reminder/notification preferences; this plan does not introduce a new unconditional interruption policy for worker events.
- Voice acknowledges the exact item after reporting that it communicated it, using the existing session-bound acknowledgment contract. Queue insertion, context exposure, model generation, and notification transport delivery are not that acknowledgment. Retain the existing explicitly opened-notification handling path.
- Keep communication and resolution separate. A question can be communicated while still awaiting an answer. An acknowledgment suppresses duplicate announcements, not the underlying unanswered question. Situation snapshots and requested briefings can still include unresolved questions after their announcement is handled.
- When an answer or changed task state makes an item obsolete, resolve/supersede the appropriate records and refresh voice context. A late acknowledgment of an old item must not handle its replacement. Retain history rather than deleting evidence.
- Voice self-report remains the accepted communication mechanism, with its existing limitations. Do not add a verifier agent or treat delivery as independent proof that the user heard every word.

Compose briefings when the user asks. The voice model reads current pending information and unresolved questions, retrieves detail as necessary, and answers. Do not run a dedicated briefing agent, periodically rewrite summaries, or maintain another briefing lifecycle.

## 7. Implementation Work Packages

1. **Shared action boundary:** extend task APIs with the relevant stale-decision preconditions and provenance; verify direct voice actions retain the short admission/control path. Reuse existing task and attention services.
2. **Durable feedback consumption:** add consumption progress/pending decision persistence, deterministic factual forwarding, and idempotent restart behavior. Keep intake/forwarding independent of model execution and the busy-worker pool.
3. **Single background reasoning adapter:** expose bounded inspection and shared task-control tools, shared situation construction, and durable decision audit. Connect meaningful event triggers; delegate long work through the existing worker queue.
4. **Attention integration:** route unresolved questions and interpreted issues to the existing inbox, project quiet context to active voice, preserve exact acknowledgments, and compose requested briefings from current records. Remove any automatic voice-side recovery ownership.
5. **Acceptance and additive rollout:** start with fixtures, then opt-in live reasoning. Verify independent voice dispatch under a deliberately blocked background call before enabling unattended task management. Continue to use the early Codex writer/lease/session proof from Phase 4.

This work can proceed once the shared task contracts exist; it need not wait for recipe authoring or expanded result-page collections. Phase 8 does not replace the non-AI supervisor and need not use a separate distributed service deployment.

## 8. Verification and Observability

- Hold background inference unresolved; demonstrate voice admission/control, event intake, factual completion queueing, and UI history still progress. Separately report worker queue/resource/provider delays rather than attributing them to orchestration.
- With voice absent, resolve a context-answerable worker question and recover an allowed failure without human action. Preserve a genuinely unresolved question for a later session.
- Confirm a worker question does not trigger two autonomous handlers when voice is active. Verify a direct user answer resumes the task without background approval.
- Reconnect voice repeatedly, interrupt delivery, and replay events. Confirm stable inbox identities, exact acknowledgment handling, and no duplicate task actions. Verify an announced but unanswered question remains retrievable.
- Race voice cancellation, user correction/answer, background retry/answer, and run replacement. Reject stale mutations and stale presentation acknowledgments without blocking unrelated tasks.
- Crash before/after decision action commit and before/after feedback progress commit. Replay without losing a pending issue or repeating a committed action. Reconcile uncertain outcomes through the existing recovery contract.
- Feed repeated heartbeats/progress events and duplicate failure events. Assert no periodic model reasoning, no self-triggering recovery loop, and no repeated repair task for unchanged evidence.
- Verify context and decision reconstruction after model/session loss, model/budget denial, and no available orchestrator. Tasks remain inspectable; code-only updates and voice dispatch continue.
- Record event-to-forwarding latency separately from decision queue/model/action latency; pending count/oldest age, model calls/usage/cost, stale-action rejections, duplicate suppression, and worker versus orchestration delay. Correlate source event, decision, task/run/question, and attention item without logging credentials.

Routine checks use deterministic model fixtures and the real shared services/database. Live latency and native Codex checks remain opt-in. No competing orchestrator architectures or A/B bakeoff are required.

## 9. Initial Model and Billing Configuration

- Use GPT-5.6 Luna with low reasoning as the default background decision configuration. This supersedes the briefly considered medium default. Retain the shared ability to choose a different permitted setting when requested or justified; increase the default later if observed mistakes warrant it. Do not add a grading agent or automatic tuning loop solely to manage this setting.
- Prefer eligible subscription execution, then use authorized API fallback under the provider account's spending settings. Use supported runtime/authentication routes; do not assume standalone API calls consume subscription credits.
- M9 makes this policy a required completion gate. Share one subscription
  execution/refresh lease with workers, not a separate competing auth owner.
  Busy/unavailable subscription capacity uses the authorized API route; code-only
  intake, forwarding and direct voice admission never wait for the lease. Keep
  the orchestration tool inventory restricted in either authentication mode.
- Rely on provider account spending settings. Remove cutoff-proof requirements and runtime blockers; actual billing, quota and authentication failures remain durable. Optional costs remain unknown when unavailable.
- On an actual spend-limit or exhausted-credit error, retain the pending decision/context, expose the billing blocker, and avoid immediate repeated billable retries. Resume when a permitted subscription route becomes available or API access is restored. Do not automatically purchase credits or raise limits. Distinguish billing exhaustion from transient request-rate limits.
- Provider enforcement is not necessarily instantaneous and can permit a small overrun. See [OpenAI spend limits](https://developers.openai.com/api/docs/guides/spend-limits). Voice/model paths using the same exhausted API account can also be affected; code-only intake, history and factual forwarding remain independent.

## 10. Concrete Runtime and Persistence

Use Phase 4's Codex facade with a restricted `orchestration` profile. Run its one model invocation independently of worker-pool occupancy, with no lease on a task workspace and no native shell, file-edit, browser or subagent tools. Substantive work is created through the common task service. This reuses the selected subscription-capable harness without building a second agent loop. Authentication concurrency still follows the M0-proven arrangement; unavailable subscription capacity may use authorized API fallback. Neither provider access nor an occupied model slot can block voice admission or code-only services.

Implement two independently scheduled asynchronous loops in the backend service: feedback intake/projection and decision execution. Do not perform blocking SDK/process work on the API event loop. If an adapter is blocking, isolate it in its supervised runtime host. No separate distributed deployment is necessary initially.

Reuse `JarvisLiveEvent` as the monotonic source and a named `TaskProjectionCheckpoint` for the feedback cursor. Filter task-domain events; ignored events may advance the cursor. Commit projected attention, a pending decision if needed, and cursor progress together in a short transaction. Use event wakeups plus the roadmap's one-second safety scan and 100-event batches. An inference request is never part of this transaction.

Add `orchestration_decisions` with:

- stable ID, issue key, source event IDs, task/run/question IDs and relevant observed goal/control/question revisions;
- status (`pending`, `running`, `deferred`, `completed`, `obsolete`, `failed`), due time, claim generation/owner and timestamps;
- immutable input-context references/digest, effective model/effort/auth profile, native binding, outcome and decision reason;
- committed action IDs and last classified error. Large tool traces remain in durable event/artifact storage.

The issue key combines trigger identity and relevant state generation. Duplicate events coalesce into the existing unhandled issue; distinct questions/instructions remain distinct. A running decision's observed state is immutable. New relevant state invalidates its mutation preconditions and queues or refreshes subsequent work as necessary. Claim one eligible decision transactionally in due-time/event order; do not infer an expired claim proves its process stopped. On restart, fence the old service generation, reconcile the runtime/action log, and requeue only unfinished work.

Keep action idempotency in the common service: an action key identifies a decision plus logical operation, and changed arguments under that key are rejected. A crash after an action commit but before marking the decision complete reuses the committed action outcome. Creation of a repair task therefore cannot create a second repair on replay. The decision row is durable pending work and audit, not a second canonical copy of task status.

## 11. Context and Tool Contract

Build a fresh issue-specific context from durable records for each decision. Native context may be reused while working that issue, but do not append unrelated issues to an endless master thread. Initial context targets 12,000 tokens; prioritize standing instructions, current request, the trigger/question, relevant evidence and actions already attempted. Retrieve more history when needed. Session loss reconstructs from these records.

Expose the existing validated operations through a small tool set: inspect task/history/evidence and situation, answer a specific question, continue/create a task, set importance or execution preferences, control a task, and retain an unresolved user question/update. Tool schemas reuse the same task service contract as voice. Backend-supplied identity, expected revisions and decision provenance travel with each call; model arguments cannot replace those authority fields.

The prompt has five responsibilities: understand the relevant user goal; act when existing instructions/evidence support an answer; record meaningful assumptions and reasons; delegate substantial work; retain a focused question if only the user can supply needed information. It also states that user pause/cancel and permission boundaries remain binding. No separate classifier, planner, grader or periodic summary prompt is required.

A successful autonomous answer resumes the asking run through the question API. An unresolved question creates/updates the existing presentation record and completes this decision, while the question itself remains unanswered. A delegated repair leaves a durable relationship to the blocked task and releases the decision slot immediately; its result is a later event. Do not repeatedly defer an unchanged issue with an immediate due time. Rate-limit waits use provider guidance; exhausted billing/access waits for a relevant availability change or an explicit retry.

Use the roadmap's bounded transport recovery and no-progress timeout; inspect an uncertain native turn before restarting it. There is no hardcoded total decision tool-round count. Report long model activity and queued decisions so later tuning is based on observed backlog. Cost limits remain provider-enforced, not another orchestration budget engine.

## 12. Readiness and Future Scope

The implementation sequence and acceptance gates are in the [roadmap](00-implementation-roadmap.md). The remaining unknowns are empirical: pinned-runtime tool enforcement, native writer termination/action persistence, supported subscription concurrency, and measured latency/quality. They are not unresolved choices between orchestration architectures.

The [pre-M3 feasibility pass](reports/pre-m3-feasibility.md) verifies the pinned
restricted API runtime with eight occupied worker slots and tests deliberately
blocked host work off the API event loop. Admission, cancellation, factual
completion projection and history continue. This reuses the M0 MCP fixture;
it does not claim the M3 decision tools, feedback cursor or recovery loops
already exist. Preserve this separation in implementation and add the full
restart/stale-action/duplicate-event cases above. Subscription and provider
hard-budget enforcement remain separate rollout gates.

Optional future extensions include parallel background decisions, specialist agents, richer interruption/notification preferences, and proactively originating work beyond existing task or standing instructions. They are not prerequisites for the agreed autonomous task management.

## 13. Implementation Progress — 2026-09-30

Historical checkpoints follow. Their then-pending work is resolved by later
entries where stated; current completion and evidence ownership are in this
file's completion section and the roadmap. Subscription ownership acceptance is
now required M9, rather than an optional post-implementation rollout item.

Historical first-slice checkpoint: M3 was **in progress**. Its first implementation slice
adds the durable feedback cursor, independent intake/decision loops, decision
claims and atomic action receipts, guarded common task actions, existing-inbox
handoff, a contained restricted Codex host, and read-only diagnostics at
`GET /api/orchestration`. All new orchestration flags default to false.

Deterministic tests use the real database and shared task APIs. They cover
context-supported answers, unresolved-question retention, direct user answer/
cancel/correction/run-replacement races, single-flight claims, cursor rollback,
repair creation replay/result correlation, unchanged failure suppression,
billing deferral, blocked reasoning independence, and native uncertain-turn
deferral. The opt-in native test uses Luna/low with a dedicated API profile and
eight occupied worker slots, exercising the actual M3 tools rather than the M0
journal fixture. Approval is configured per allowlisted MCP tool; a server-level
approval setting is insufficient with global deny-all approval.

Verification this slice: full backend regression `371 passed, 6 skipped`.
That run exposed a shutdown thread warning; graceful loop shutdown replaced
immediate transaction cancellation. The subsequent affected-service suite
passed `64 passed, 1 skipped` with unhandled thread warnings treated as errors.
The final supervisor-recovery attention case is included in that targeted
rerun. The full suite still needs rerunning after subsequent M3 work.

Agent-runnable checks (from `backend`):

```powershell
uv run pytest tests/test_task_orchestration.py tests/test_codex_orchestration_runtime.py tests/test_migration_recovery.py -q
# Authorized isolated API test only; does not change rollout flags or desktop auth:
$env:JARVIS_LIVE_CODEX = "1"
uv run pytest tests/test_codex_orchestration_runtime.py -q
Remove-Item Env:\JARVIS_LIVE_CODEX
```

The latest native decision evidence is
[codex-m3-decision-current.json](reports/codex-m3-decision-current.json).
Two native tests passed together (40.62 seconds), including the eight-worker
case. A later rerun reached the provider's actual "no credits remaining"
cutoff; its decision became deferred with `billing_exhausted`. Further billable
testing was stopped. The evidence file distinguishes the earlier successful
run from the latest provider-blocked run; it is not an all-green live report.
The default test run skips billed integration. No device or user interaction is
required for either suite when the already-authorized API credential is present.

Remaining work at that historical checkpoint (superseded by the third slice below):

- Finish the remaining guarded execution-preference capabilities. Fresh bounded
  context, approved standing instructions, shared situation, original evidence
  retrieval, presentation preference changes and deferred recovery are now
  implemented; see the next implementation slice below.
- Finish forwarding/queue/model/action latency and usage observability, flag
  transition/backlog behavior, and combined active-voice/reconnect/emulator
  acceptance. Existing attention acknowledgment semantics are unchanged, but
  their new integrated M3 scenarios still need verification.
- Complete regression and native acceptance after these additions. Actual
  contained-process termination with a committed action is now covered locally;
  the expanded native tool inventory still needs its authorized live recheck.

No physical-device testing or new installation is needed for these items.
Dedicated subscription continuation and verification of an **enforced** API
account cutoff remain external rollout gates. Neither is claimed by the
isolated API integration test; unattended live operation remains disabled.

### Second implementation slice: context and deferred recovery

Decision input is rebuilt at claim time, not reused indefinitely from intake.
It includes a bounded view of the shared canonical situation, approved
preference/standing-instruction records, original-source references and earlier
committed action receipts. Proposed/rejected/deleted memories are not accepted
instructions. The complete approved catalog participates in the stale-action
digest, including instructions outside the initial eight excerpts. Catalog
paging and original instruction retrieval are available through `inspect`.

The initial context targets 12,000 tokens with a hard 48,000-byte UTF-8 JSON
ceiling; this is a transport bound, not an exact tokenizer assertion. Truncated
goals, events, turns, artifacts and instructions can be retrieved in 8,000-byte
ASCII-escaped JSON chunks with total length, next offset and SHA-256. Evidence
selectors cannot read another task or open an artifact URI/native file. A
supported presentation-preference change uses the common atomic task service,
records an idempotent receipt, and retains the still-unanswered issue through
the existing inbox according to the chosen policy.

Native recovery uses the pinned SDK's `thread/read(includeTurns=true)` only;
it never starts or resumes a turn during inspection. This follows the
[official app-server read contract](https://learn.chatgpt.com/docs/app-server).
Before inspection, the exact recorded host PID/creation stamp must be verified
gone on the same machine. Timeout, absent/corrupt history, an active/unknown
turn or an unverifiable host does not authorize a duplicate generation. New
hosts persist their process identity before accepting model input. Legacy
bindings without verifiable process identity remain safely deferred.

Deferred native bindings are inspected once automatically. Inspection alone
does not restart paid work. Authenticated, agent-callable endpoints are:

- `POST /api/orchestration/{decision_id}/inspect`: inspect/reconcile only.
- `POST /api/orchestration/{decision_id}/resume`: explicit resumption request
  with `operation_key` and `reason`; rechecks current task/question/instruction
  authority, reconciles committed receipts first, fences the old generation,
  and audits the old binding and outcome before queueing eligible fresh work.
  Replaying a key returns its original outcome; changed arguments are rejected.
  A new attempt after an earlier blocked request needs a new operation key.

Neither endpoint buys credits, logs in, changes account/model settings or
enables the default-off live route. Even a scheduled provider retry cannot
skip reconciliation of an existing native binding. Rate-limited native work
currently uses this explicit recovery path rather than automatically starting
another turn merely because its retry delay elapsed.

Tests cover original Unicode evidence reconstruction, catalog paging, omitted
instruction changes, preference races/replay, exact inspection identities,
terminal versus uncertain statuses, unavailable process proof, concurrent
resumption, direct user answers during blocked inspection, receipt recovery,
and endpoint authentication/flag/schema gates. A real Windows Job Object test
crashes a host after its child commits a guarded answer, verifies the exact
child process has stopped, and recovers from the one receipt without another
model invocation. No provider call or physical device is used for these tests.

Agent-runnable focused checks (from `backend`):

```powershell
uv run pytest tests/test_orchestration_recovery.py tests/test_task_orchestration.py tests/test_codex_orchestration_runtime.py tests/test_jarvis_situation.py tests/test_task_projection.py -q --tb=short -W error::pytest.PytestUnhandledThreadExceptionWarning
```

At this second-slice checkpoint, the restricted native inventory also included `presentation`. The earlier
successful six-tool live run is historical evidence, not verification of this
expanded inventory. Billable tests remain stopped after the documented credit
cutoff. Remaining telemetry, flag-transition/backlog, execution-preference and
combined active-voice/reconnect/emulator acceptance work is still required
before M3 can be marked complete.

Verification of this slice: full backend regression **398 passed, 6 skipped**
with unhandled thread exceptions treated as errors. An earlier full attempt
had one startup failure in the new subprocess fixture; host/child diagnostics
were added, the full recheck passed, and three further real crash-test runs
passed. After the final recovery-backlog query fix, the affected-service suite
including task controls passed **78 passed, 1 skipped** under the same strict
warning policy. That fix prevents 100 already-inspected bindings from starving
later deferred work. Module compilation and whitespace checks also passed.
No billable native integration was run during that second slice.

### Third implementation slice: preferences, telemetry and rollout acceptance

Shared voice/background delegation and agent continuation now accept only
administrator-allowlisted `model` and `reasoning_effort` preferences. An empty
`TASK_EXECUTION_MODEL_ALLOWLIST` permits only `CODEX_TASK_MODEL`; efforts are
low/medium/high. Explicit preferences require the Codex route rather than being
silently ignored by the legacy executor. Continuations inherit the previous
run's preferences and pin overrides only on the new run. Replaying an older
continuation returns its original configuration even after a newer run changes
it. Worker configuration, credentials, sandbox and capabilities are not model
arguments. Workflows use the same preference validation, and the decision
context shows the permitted options and current run settings. Administrator
allowlisting does not itself prove provider availability for every model;
unsupported provider choices still fail safely through the runtime.

Migration `20260930_0030` adds metadata-only `orchestration_metrics`, outside the
unified event stream. `/api/orchestration` reports factual source-to-forwarding
latency separately from decision waiting, runtime-plus-tools and action-attempt
duration; pending counts/oldest age, exact native turn counts, unique committed
receipts, stale/validation rejection and replay outcomes are distinct. Recent
sample p95/max are explicitly bounded samples, not a production SLA. Native
turn counts are not provider HTTP request counts: one turn can contain several
model/tool rounds, and the provider's internal request count is not inferred.
`thread/tokenUsage/updated` snapshots replace the prior snapshot for the same
claim, preventing cumulative notifications from being added repeatedly. The
[official app-server contract](https://learn.chatgpt.com/docs/app-server) exposes
token usage, not an invoice: cost remains `null`/`provider_cost_unavailable`,
never an invented price or zero. Actual native stream usage is now tested.

Rollout uses no second inbox or new attention state. All original worker
question/failure candidates remain canonical `pending` records. While the flag
is enabled, only backend-retained source identities with committed action
receipts are exposed for proactive voice/checklist/notification delivery.
Worker/model facts cannot forge that authority. Enabling the flag also holds
legacy pending items; disabling it exposes the same items without rewinding a
cursor, deleting history or duplicating candidates. Held transport leases do
not block unrelated factual updates. Old voice exposure cannot acknowledge a
currently held item. A communicated question remains unanswered until the
separate exact-question answer action.

Automated tests combine an active fake-Live controller, a blocked background
decision, eight occupied worker slots, admission/control/factual forwarding,
controller replacement, stale acknowledgments and exact duplicate handling.
Existing race/crash/recovery tests remain in the regression gate.

The user restored API availability and authorized isolated paid checks. The
expanded seven-tool native host passed context answering and actual
`presentation` execution with eight occupied workers. Both cases verify one
native turn, actual provider token usage, confirmed host exit and read-only
`thread/read(includeTurns=true)` inspection of the exact persisted completed
turn. An initial assertion used the transport tool name instead of the common
service receipt name (`set_update_policy`); the assertion was corrected and
the live suite passed. Evidence is in
[answer/native inspection](reports/codex-m3-expanded-current.json) and
[presentation/native inspection](reports/codex-m3-expanded-current-presentation.json).
The earlier credit-exhaustion report is retained as historical blocker evidence.

The extended API-37 emulator harness passes actual native screenshot/private
upload/worker/result/notification reconnect, plus M3 autonomous answering and
presentation-with-unanswered-question retention. Android WebView force-stop
and reopen preserve task runs, questions and one decision/native turn per
issue. The first attempt used an older debug APK restricted to result paths;
the debug-only driver now also permits a task-UUID history path, and the rebuilt
run passed. See [API-37 M3 evidence](reports/android-m3-api37-current.json).
This uses fake Live admission and real Android/Codex execution; it is not real
speech, custom-wake capture or physical-device acceptance.

Agent-runnable checks (from `backend`, no interactive input):

```powershell
uv run pytest -q --tb=short -W error::pytest.PytestUnhandledThreadExceptionWarning
$env:JARVIS_LIVE_CODEX = "1"
try { uv run pytest tests/test_codex_orchestration_runtime.py -q --tb=short }
finally { Remove-Item Env:\JARVIS_LIVE_CODEX }
uv run python ../android/scripts/verify-screen-workflow.py --orchestration
```

The last two commands are opt-in billed checks using the already-authorized
credential and a fresh dedicated API profile. The emulator harness enables
orchestration only on its disposable backend and cleans up owned services.
Real production flags/account limits are unchanged. Existing installed API-37
SDK/emulator, frontend dependencies and pinned Codex package suffice; no new
installation or user interaction was required.

Verification history: full backend regression **416 passed, 7 skipped** before
the last rollback safeguard; affected-service recheck **53 passed** afterward;
latest native suite **3 passed** including both paid cases and exact history
inspection; Android debug build/unit tests and integrated API-37 harness passed.
The next full attempt passed 417 cases but exposed an older reminder test's
fixed 50 ms delivery wait. It now waits for the actual committed transport
outcome with a bounded timeout. A new rollout test also exposed a held legacy
lease still occupying the unique session-lease constraint; reservation now
releases invisible transport ownership without changing attention state.
The affected attention/projection suite passed **14 tests** after that fix.
A subsequent full attempt exposed a real standby-startup race: a child could
register after the occupancy snapshot but before its identity lookup, causing
an extra child spawn. The supervisor now checks unregistered children before
reading occupancy; a deterministic interleaving test covers the race. The
affected pool/worker/orchestration suite passed **45 tests**.

Final full backend regression after all safeguards: **420 passed, 7 skipped**
with unhandled thread exceptions treated as errors. Module compilation and
CRLF-aware whitespace checks passed. M3's exit is met for the guarded API
route; at that checkpoint the next milestone was M4. Current closure proceeds
through M6–M10. Do not interpret these fixture samples as a production p95/quality
distribution.

Closure ownership now follows M7 shared preferences, M8 real voice/capture,
M9 required subscription ownership and M10 final regression. M11 closes the accepted
personal phone rollout. The earlier M3 checkpoint does not pass these later gates;
account setup does not block independent guarded API implementation.
## Completion evidence

See the [completion ledger](00-implementation-roadmap.md) and
[reports index](reports/README.md). Earlier progress reports are historical;
M0–M11 are closed under the user's accepted scope and caveats.
