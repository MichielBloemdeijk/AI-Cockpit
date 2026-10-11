# Jarvis Task Control Plane

Status: completed for the accepted personal-use scope on 2026-10-11. The
[completion ledger](00-implementation-roadmap.md) owns status and accepted
limits; this document retains the phase contract and historical evidence.

## Completion

This phase is complete under the [final personal-use acceptance](reports/personal-use-accepted-2026-10-11.md).
The [reports index](reports/README.md) retains candidate-scoped verification,
failed observations and the accepted M8 caveats. Historical implementation
notes below do not assign new work or override the completion ledger.

## 1. Summary

Replace the single-worker and fixed-lane design with a homogeneous elastic pool:

```text
GPT Live / background orchestrator / Tasks UI
                 |
        durable task controls
                 |
     non-AI worker supervisor
                 |
    1 warm worker + up to 8 busy
                 |
    one run attempt per busy process
        /                    \
curated workflow       general agent
narrow capabilities    broader tool set
```

- GPT Live's managed delegate handles user-requested task actions directly. The background orchestrator owns automatic worker-feedback decisions, whether or not a voice session is active. Both can use importance, yield, suspend, cancel, answer, and recovery controls through the same APIs.
- The backend validates, persists, executes, fences, and audits those decisions.
- [Phase 8](08-background-orchestration.md) defines the independent background orchestrator and shared user-attention inbox. It uses the same APIs and events without introducing a second worker queue or blocking voice admission behind model inference.
- Use the trusted backend Live connection for durable tools and session control, consistent with the [OpenAI Live sideband interface](https://developers.openai.com/api/reference/typescript/resources/live/subresources/sideband).
- Every task is a persistent session that supports follow-up turns, whether it executes through a general agent or a workflow. Completion releases compute and reservations while preserving context for another execution run under the same task ID.
- The overarching architecture reflects this shared-pool design. Codex is the selected worker harness for general agents and guided workflows. Phase 4 owns its adapter, reusable instructions, and batched tools; workflow authoring uses readable instructions plus a small configuration file, with no comparative bakeoff required.

## 2. State, Scheduling, and Interfaces

### 2.1 Task and execution state

- Add `Task.execution_kind = agent | workflow` as its default execution kind; default existing tasks to `agent`. Pin the effective kind and permitted capabilities on each execution run rather than allowing follow-up text to silently broaden access.
- Add `paused` to the authoritative task lifecycle.
- Keep `importance = low | normal | high`, defaulting to `normal`.
- Add `queue_entered_at` so requeued work can move to the back of its importance tier.
- Give every task a stable private workspace reused across turns, execution runs, and attempts. A restart-from-beginning creates a new workspace generation while retaining the previous generation for debugging; an ordinary follow-up does not reset the workspace.
- Extend attempts with a random claim/fencing token, checkpoint version, parent-attempt reference, resume reason, last progress time, active operation, operation start/deadline, and recovery metadata.
- Require the current execution-run ID, attempt ID, and claim token for every checkpoint, progress event, artifact, result publication, and terminal transition. Completion from an older run must not complete or otherwise mutate a newer run of the same task.
- Validate autonomous control/answer decisions against the relevant goal/control/question revision or equivalent current-state preconditions. Audit source event and decision identity. Reject stale actions after a user cancellation, correction, answer, or run replacement; unrelated heartbeat/progress events need not invalidate them. Use short transactions and never hold task locks while a model thinks.
- Add durable, idempotent task-control commands containing action, parameters, requester, reason, provider call ID, state, target execution run/attempt, and timestamps.
- Add a supervisor lease/generation and extend worker records with supervisor generation, verified process identity, assignment, state, and heartbeat.

### 2.1.1 Persistent sessions, turns, runs, and attempts

- `Task` is the stable user-visible session identity, with a durable ordered conversation, private workspace, artifacts/results, and continuation state. It is the same object before and after a follow-up; do not create a linked task merely because the preceding request finished.
- Persist task turns with role, content, origin, idempotency identity, ordering, and run/question association. Initial requests, follow-ups, clarification exchanges, and final responses remain inspectable in the same task history.
- Introduce a logical `TaskExecutionRun` for each initial request or new follow-up requiring execution. It owns its input turn, effective execution kind/configuration, pinned workflow definition when applicable, context/result bindings, lifecycle outcome, and progress. Each run can have multiple recovery attempts. A follow-up is a new run; retry/restart recovers the existing run; a clarification answer resumes the run that asked the question.
- `Task.status` remains the authoritative current user-facing lifecycle field, updated transactionally with the active/current run. A completed task can become queued/running again only through an explicitly admitted follow-up. Earlier runs retain their completed/failed/cancelled outcomes. Run history is not a second independently controlled task system.
- Admit follow-ups through the same durable scheduling and authorization path as other work. Persist the new turn/run and emit events atomically; exact-call/intent duplicates return the existing turn/run. At most one run executes per task. Concurrent follow-ups are ordered and serialized; admitting queued follow-up work does not replace the active run or its current task status. Promote the next eligible run and update current task state atomically after the preceding run ends.
- Retain full durable history plus a compact working-context snapshot with references to completed tool results, decisions, constraints, source evidence, and artifacts. Continuation loads the relevant state without repeating successful work or requiring the entire history to fit in one model request. Compaction must preserve access to the original evidence/history.
- Persistence is independent of a provider conversation ID or a resident worker. A later run must be reconstructible after process/provider-session loss. Workers and resource reservations are released after completion; starting the follow-up reacquires relevant resources and checks changed external/shared state.
- Persist the task-to-Codex-thread binding and native turn/item associations for each run/attempt. Keep native session files/state outside disposable worker lifetimes and resume the compatible thread by default. If it is unavailable or incompatible, reconstruct from portable retained context under the same task and record the replacement binding. Session resumption restores conversation context, not a frozen process or an automatically reversible action.
- Maintain a durable, ordered projection of visible agent activity with stable native item identities for deduplication/reconciliation. Desktop/mobile clients and Jarvis inspect the same messages, tool evidence, changes, errors, and available reasoning summaries. Native context compaction must not erase retained evidence; do not promise access to complete internal reasoning.
- A new run pins its effective template/harness/model/capability versions. Reuse portable prior evidence and context where valid; do not resume an incompatible old executor checkpoint as if it belonged to the new run. Workflow continuation uses the general session contract rather than a workflow-specific conversation store.
- Use configured fast, economical defaults, accepting authorized Jarvis model/effort overrides at admission or supported later turn/continuation boundaries. Preserve the initial run configuration and append later setting decisions with reasons. Validate supported settings and permissions; record subscription/API execution mode and any paid handoff per Phase 4. Provider account settings own spending limits.
- New tasks are created when the user or Jarvis intentionally separates work. Preserve a reference to the originating task where helpful; separation is an explicit routing choice, not the default follow-up behavior.

### 2.2 Queue behavior

- With no intervention, all normal-importance tasks execute FIFO.
- Changing importance immediately moves the task into the corresponding scheduling tier; FIFO applies within each tier.
- Claim the oldest runnable task from `high`, then `normal`, then `low`.
- Skip tasks blocked by context readiness or a conflicting resource reservation.
- Do not automatically preempt running work. Jarvis explicitly yields, suspends, cancels, or restarts work when it judges that necessary.
- All tasks execute in the background through the same pool. Do not introduce an interactive/background task classification or reserve capacity by task origin or execution kind. Importance represents urgency.
- Admission remains independent of available compute. Under saturation, accepted tasks may queue; importance changes queue order but do not free occupied workers. Measure admission latency and execution-start latency separately, including pool saturation and resource blockers. There is no unconditional immediate-start guarantee.
- A yielded task receives a new `queue_entered_at`, placing it behind tasks already in its tier.
- Importance informs scheduling and Jarvis's reasoning but never mandates which update Jarvis must communicate first. Presentation policy and conversational relevance remain separate.

### 2.3 Resource safety

- Use directory-level reservations for shared filesystem edits in v1, scoped to the relevant project, app, or smaller working directory. Individual-file reservations are deferred. Normalize directory identities and reject overlapping ancestor/descendant reservations, not only identical paths. Each task's private workspace remains its own.
- Acquire the complete mutable target set atomically and in canonical order before execution. Initial executable mutable targets are filesystem projects. Keep non-filesystem targets in the reservation domain but reject mutation/reacquisition without a capability-specific state observer; implementing those integrations is future scope.
- Separate task-owned resource reservations from the current worker attempt's revocable execution authority. Preserve a task's reservations during an immediate automatic crash-recovery or restart handoff; do not let another task edit those resources between the old and replacement attempts.
- Release reservations on confirmed yield or suspension, completion, failure without recovery, interruption awaiting a decision, or cancellation. A pause/yield request alone does not release them: first establish that the old worker and any writing subprocesses have stopped.
- Resuming a paused/yielded task or retrying a task whose reservations were released must reacquire the complete target set and check relevant state for intervening changes before editing. Preserve checkpoints and the previous target list, but do not assume the shared files are unchanged.
- Fencing tokens reject stale internal writes but do not themselves stop filesystem writes. The supervisor must establish that old writers have stopped before transferring exclusive access; if that cannot be established, expose the blocker and keep the affected reservations. Use process-exit confirmation rather than a fixed multi-second shutdown grace period.
- Prefer atomic replacement for controlled file writes. An interrupted action can leave partial changes; inspecting affected state is part of normal agent continuation, not a separate recovery workflow.
- Starting over does not silently undo changes to external systems or shared files; the UI and Jarvis response must state this when applicable.

### 2.4 Public and Jarvis interfaces

- Add an idempotent `POST /api/tasks/{task_id}/control` supporting:
  - `set_importance`
  - `yield`
  - `suspend`
  - `resume`
  - `cancel`
  - `restart_execution`
  - `retry`
  - `restart_from_beginning`
- Use one canonical control contract and update its clients together. M10 may
  remove obsolete cancel/retry compatibility wrappers rather than preserve them
  solely for old development clients; control semantics and tests remain required.
- Add idempotent `POST /api/tasks/{task_id}/turns` and a reviewed `continue_task` Jarvis tool for follow-ups to any task. Both return stable task identity, admitted turn/run IDs, current status, and any existing result association. Keep new-task dispatch distinct from continuation and clarification-answer/retry controls.
- `yield` and `suspend` accept `immediate: boolean = false` in both the task-control API and Jarvis tools. Jarvis selects the mode using its judgment; the backend enforces the selected mode and reports requested versus confirmed state truthfully.
- Add `GET /api/tasks/pool` and authenticated administrative controls for repairing capacity, replacing one worker, and restarting the pool.
- Extend task detail with ordered turns and run history, current run ID, queue blockers, importance, pending control, checkpoint freshness, workflow stage, active operation, operation deadline, worker health, warnings, resumability, and result URL.
- Serve live activity and paginated durable history through authenticated Jarvis interfaces usable by the mobile app as well as desktop. Reconnect from a durable cursor and reconcile native items without duplicate actions or restarting work. UI/Live disconnection alone does not interrupt an accepted task.
- Expose intuitive Jarvis tools for setting importance, yielding, suspending, resuming, cancelling, and restarting tasks, plus worker-pool inspection and repair. Resolve natural task references server-side and never expose raw PIDs or checkpoint contents.
- Audit every autonomous Jarvis mutation. Jarvis is permitted to yield, suspend, resume, or cancel based on its judgment; spoken notification remains relevance-based.
- Extend the existing Tasks pages with pool health and all task controls rather than adding a separate operations application.

## 3. Supervisor, Recovery, and Admission

### 3.1 Elastic worker pool

- Run one non-AI supervisor as the sole task claimant and process manager.
- Maintain one fully initialized standby whenever fewer than eight workers are busy.
- When the standby receives a task, start its replacement immediately.
- Permit at most eight busy workers; the standby remains parked when the pool is saturated.
- Each busy child exclusively owns one task attempt and exits when that attempt completes, fails, waits, yields, pauses, or is cancelled.
- Scope every assignment and control to the task's selected execution run and attempt. A follow-up run receives normal queue admission and never inherits the previous worker's execution authority.
- Select the workflow or agent runtime from the server-resolved `execution_kind`. Enforce their different capability surfaces in code, not prompts.
- Both execution kinds use the selected Codex adapter. Scope native runtime processes, sessions, and writing subprocesses so stopping one attempt cannot stop unrelated tasks or leave its own writers alive. Native turn interruption is a request; Phase 4's integration milestone must establish actual writer termination and action persistence.
- Centralize claiming in the supervisor to reduce SQLite contention. Workers continue to write fenced task progress and results.
- M6 closes the current implementation mismatch: `TaskWorker.run_once` still
  claims work. Persist the elected supervisor's assignment to a registered
  ready child, binding generation, child identity, run, attempt and token.
  Children accept only their current assignment; duplicate delivery cannot
  start another attempt. A crash between claim and delivery is reconciled
  through the same durable assignment and confirmed-stop/fencing rules.
- A standby is an initialized worker process; it is not evidence of a resident
  warm Codex model. Report actual per-attempt App Server startup separately.
- If admission detects no healthy supervisor, commit the task first, asynchronously attempt to start the supervisor, and return immediately. The task reports `waiting_for_worker` until capacity is available.
- Competing supervisor starts use the durable generation lease; only one becomes authoritative.

### 3.2 Task controls

- `yield`: by default finish the current bounded action, persist its result and continuation state, end the attempt, release compute and reservations, and requeue automatically. With `immediate=true`, invalidate execution authority and stop the worker promptly instead; retain the last completed checkpoint and record the unfinished action.
- `suspend`: use the same default/immediate modes as yield, but transition to confirmed `paused` until Jarvis or the user resumes it. Confirm pause and release reservations only once old writers have stopped.
- `cancel`: immediately invalidate execution authority, prevent new actions, and stop the worker and its writing subprocesses without waiting for the current action to finish or for the heartbeat stale threshold. Retain history, workspace, artifacts, and debugging context. Expose stop confirmation separately from the accepted command; release reservations only once old writers have stopped. No mandatory multi-second grace window is added.
- `restart_execution`: immediately invalidate the claim token, stop the old writers, and resume from the latest completed checkpoint. Reuse completed results and inspect an unfinished action's outcome before repeating it when necessary.
- `retry`: resume failed, interrupted, or cancelled work from its latest compatible checkpoint, reacquiring resources and checking shared state where access was released. Phase 3 capture/upload failures are not automatically retried by this mechanism; a Jarvis-initiated retry must explicitly establish valid context.
- `restart_from_beginning`: discard active checkpoint state and create a new private workspace generation; preserve old state for inspection and warn that external side effects are not rolled back.
- These controls operate on the selected/current execution run. Completion, failure, or cancellation ends that run, not the persistent task session. A later explicit follow-up may execute under the same task ID; cancelling a run never schedules a continuation automatically. Late commands bound to an old run cannot stop a newer follow-up run.
- Use supported idempotency keys for external mutations. Persist the logical action identity, completed result, and agent continuation state before advancing. An external action that may have succeeded without a recorded response must not be silently replayed. Inspect or reconcile its outcome through normal agent tools; if straightforward safe continuation cannot be established, record a recovery-needed event for Phase 8. Process fencing guarantees internal commit ownership, not reversal of external side effects.

### 3.3 Failure recovery

- Observe supervised process exits directly and begin recovery immediately after confirming old writers have stopped. Do not wait for heartbeat expiry after a confirmed crash.
- Preserve the existing five-second heartbeat and twenty-second stale threshold as the fallback for workers that remain alive but stop reporting. Before replacement, fence and stop the old writers. Explicit cancellation and immediate controls do not wait for this watchdog.
- For ordinary transient provider/connection failures, use small bounded in-process retries where replay is safe; retain the worker and reservations. Return normal tool errors to the executing agent so it can adjust its approach. These are not separate task-recovery workflows. The Phase 3 capture/upload path explicitly has no automatic retry.
- On a worker crash, automatically resume both workflow and agent tasks once when the saved checkpoint and unfinished action allow straightforward safe continuation. Persist each completed action result and the continuation state needed to reuse it, not just a conversation transcript or stage name. Keep the same task workspace and reservations during this immediate handoff.
- Identify repeated crashes using the checkpoint digest/stage. Successful durable progress resets the consecutive recovery count.
- A second crash before progress transitions the task to `interrupted`, releases its reservations after old writers stop, and records a visible recovery issue for background handling.
- Repeated failure without progress or unresolved action outcomes produce a durable recovery-needed event containing checkpoint identity/freshness, last known operation state, and concise diagnostics, without exposing raw checkpoint contents. Release resources only after old writers have stopped. Phase 8's background orchestrator uses existing inspection/control tools regardless of voice availability; human action is not the default recovery mechanism.
- Do not introduce a specialist recovery agent. The single background orchestration role handles nontrivial recovery; if unavailable or not yet enabled, retain the issue and keep the task inspectable/interrupted. Voice can still execute explicit user controls. Straightforward automatic recovery remains available without a model.
- Manual restart does not consume the automatic crash-recovery allowance.
- A supervisor restart adopts healthy children by their persisted identities where possible; otherwise it relies on claim fencing and stale reconciliation.
- There is no overall task timeout. Every capability/action has a bounded timeout, and inspection reports suspected stalls without automatically cancelling them.

### 3.4 Workflow admission

- Implement one transaction-aware admission service shared by GPT Live, trusted tests, and authenticated APIs.
- Before admitting a screenshot-dependent task, consult authenticated capture status for the exact selected device/activation/capture. If pending, wait at most two additional seconds outside the database transaction, without blocking Live audio or provider-event handling. Admit only after Android reports that the image is captured, normalized, and ready to upload. Unavailable, expired, mismatched, or still-pending context rejects admission without creating a task or result page. An already committed idempotent replay returns the original admission without requiring capture again.
- For initial work, atomically create the task, input turn, execution run, initial attempt, pinned `WorkflowRun` associated with that execution run, stable result-page shell, required context request, task event, and targeted Live event. For continuation, append the turn/run to the existing task and reuse existing result identity when revising it; do not allocate another task or page by default.
- Pin workflow version/digest, capability versions, normalized inputs, origin session/generation, any required device/activation binding, and result identity per execution run, server-side. `WorkflowRun` is not one-to-one with the lifetime task: different runs can use their own immutable execution snapshots.
- Continuation normally reads retained task context and results; it does not require another Android activation or screenshot. Apply Phase 3's capture preflight/upload gate only when this new run explicitly needs newly captured context. Trusted run-entry requirements and ownership checks govern access to retained evidence.
- Persist the Android activation ID with the Live session rather than relying only on an in-memory coordinator.
- Follow Phase 2's exact-call and intent admission identities. Store an input fingerprint and reject changed arguments under the same identity; duplicate delivery must not start another capture wait or task.
- Capture readiness before admission is distinct from upload readiness after admission. Create the upload request in the admission transaction and keep upload-pending tasks unclaimable without a worker or resource reservations. Once upload is accepted, make them eligible using their original queue time.
- A confirmed capture/context or upload error after admission fails its execution run and associated initial result through the backend, without requiring a worker or active model. Preserve any previously published revision when an update fails, and change task status only if the failed run is current. Do not automatically retry, recapture, or wait for replacement input. Preserve the classified failure for Jarvis to decide whether to retry explicitly; duplicate failure delivery cannot reverse an already accepted upload or the originating run's terminal state.
- Return the committed task ID, status, result URL, context state, queue position, pool state, and a concise saturated-pool summary to GPT Live.

### 3.5 Workflow-template compatibility

- These are integration contracts. Phase 4 specifies the selected Codex facade, YAML/Markdown package and journaled tools. Its native enforcement is proved in roadmap M0. Exercise control-plane contracts with a deterministic fixture seam without inventing a second session, scheduler, or lifecycle inside the harness.
- A task template is a versioned workflow definition, not a special worker class.
- Each template declares its typed inputs and outputs, required context, checkpointable stages, capability versions, result schema, action timeouts, and evaluation fixtures.
- The control plane supplies admission, scheduling, importance, worker assignment, inspection, yielding, suspension, resumption, retries, crash recovery, cancellation, and stable result identity uniformly.
- Template execution must persist state at action boundaries and use idempotent capabilities. State held only in worker memory is not considered resumable.
- `recipe_from_screen@1` uses the workflow execution kind, requires activation-screen context, and runs through the constrained workflow capability surface.
- Adding a second template must require only its versioned definition, registry entry, schemas, fixtures, and renderer support when it introduces a new result type. Its eventual packaging follows Phase 4. It must not require changes to task control, supervision, Live transport, or Android context correlation.

### 3.6 Concrete schema and service placement

Keep the existing `Task`, `TaskAttempt`, `TaskEvent`, `TaskQuestion`, `TaskPresentation`, worker and global live-event structures. Add fields/tables through additive Alembic migrations; the following names are implementation targets:

- `task_turns`: stable ID, task ID, ordered sequence, role/content or artifact reference, origin, request identity/fingerprint, execution-run/question references and creation time. Unique task/sequence and request identity protect ordering and duplicates.
- `task_execution_runs`: stable ID, task ID, ordinal/input turn, lifecycle outcome, execution-kind/configuration snapshot, workspace generation, current attempt, context reference and timestamps. Unique task/ordinal; at most one executing run per task is enforced in the claim transaction.
- `task_native_bindings`: task/run association, native thread/profile IDs, pinned SDK/runtime compatibility data, storage reference and replacement reason. Native turns/items correlate through events/operations. Never persist credentials in these rows.
- `task_operations`: Phase 4's durable operation journal. Large artifacts live outside SQLite; result references, event and checkpoint advancement commit together. The current checkpoint refers to a compatible context version and completed operations, rather than duplicating all tool output.
- Extend `Task` with current run, goal revision and control revision; extend `TaskQuestion` with asking run and resolution revision. Progress/heartbeats do not increment decision-relevant revisions. Extend attempts with run association and the fencing/supervision fields above while preserving their existing task-wide attempt numbers.
- `task_control_commands` and `task_resource_leases`: durable command identities/states and canonical resource ownership, with run/attempt/supervisor generation, verified execution-boundary identity and release evidence. Normalize actual filesystem identity, case rules, parent/child overlap and symlinks; path spelling alone cannot bypass a directory lease.

Backfill one initial run per legacy task, attaching existing attempts/questions and available history without changing external IDs. Preserve unavailable historical fields as unknown. Existing queued/waiting tasks retain their intent; old running attempts are reconciled only after draining/fencing the legacy launcher.

`task_service.py` remains the mutation authority; `worker_coordinator.py` owns claims/supervision; `agent_execution.py` exposes the context/sink seam that the Codex facade implements. Keep provider operations outside database transactions. Reuse the existing live event projection and task presentation authority, with Phase 8 owning only its pending-decision records and consumption progress.

Requested stop invalidates new mutations promptly. Confirmed stop requires descendant termination plus completion/reconciliation of any backend tool operation that could still write the resource. A brokered write needs the same fence/lease checks as a native writer; killing the agent process alone does not cancel a write already handed to another process. Unknown writer state keeps the lease unavailable and visibly blocked until supervised reconciliation proves it safe. This exceptional state is not a fixed multi-second shutdown window or a reason to delay every cancellation.

## 4. Delivery and Rollout

- Extend the current Tasks list with supervisor health, warm/busy/starting/stale counts, queue depth, saturation, and repair controls.
- Extend task detail with an event timeline, active operation, progress age, held/blocking resources, worker assignment, recovery history, and context-aware action buttons.
- Provide follow-up input on every task, including completed tasks, and show earlier turns/runs in the same task history. Distinguish a new follow-up run from a retry of a failed run; keep the stable task/result links usable across both.
- Make this task view usable on mobile: live activity, retained history, follow-ups, and authorized controls use the same backend contract. Closing/reopening the app resumes observation, not execution. Show available reasoning summaries as summaries, and tool results as observed evidence.
- Require confirmation in the web UI for cancel, restart-from-beginning, worker replacement, and whole-pool restart. Jarvis may perform authorized actions autonomously and must supply an audited reason.
- Emit durable events for control requested/acknowledged/rejected, importance changed, yielded, suspended, resumed, worker fenced, automatic recovery, pool degraded, and pool repaired.
- Feed automatic question/recovery handling to Phase 8 only. Preserve immediate task history and deterministic factual attention forwarding while background inference is busy. Both decision paths share existing attention records; communication acknowledgment never counts as a task-question answer.
- Record admission, queue, claim, warm/cold start, action duration, pause latency, checkpoint age, SQLite lock retries, crash recovery, and pool utilization metrics.
- Roll out through coherent schema/API/client updates, supervisor flags and
  isolated acceptance before changing the launcher. Under AGENTS.md, disposable
  development data does not require additive-only migration or legacy readers.
- Prove capture, admission, shared-pool execution, directory exclusion, cancellation, and recovery through a deterministic integration slice with a minimal result view. Add Phase 4's early real-Codex session/stop/lease/recovery proof before expanding the full workflow and Phase 5 presentation layers. A fixture alone does not establish native tool cancellation or checkpoint correctness; workflow packaging remains separate.
- Stop and confirm the legacy worker before enabling the supervisor. Reconcile
  retained queued/running work; a separately scoped development-data reset is
  allowed but never substitutes for proving recovery and ownership semantics.
- Keep SQLite for v1. The eight-worker acceptance gate requires zero uncaught lock failures and workflow admission p95 below two seconds under simulated full-pool activity.

## 5. Test and Acceptance Plan

- Migration tests for existing tasks, attempts, workspaces, statuses, importance, and worker records.
- Backfill existing tasks into an initial execution run without changing public task IDs, workspace paths, artifacts, events, or attempt history. Test completed-task continuation after migration.
- Session tests for initial completion followed by a new turn under the same task ID, multiple ordered follow-ups, clarification within one run, retry within one run, provider/worker-session loss, context compaction and evidence retrieval, idempotent continuation, and late old-run writes/controls rejected after a new run starts. Exercise both general-agent and workflow tasks.
- Concurrent idempotent admission tests, including changed-payload rejection and context-upload races.
- Pool tests proving one standby, eight-busy maximum, replacement spawn, busy-child exit, offline auto-start, and supervisor failover.
- Scheduling tests for default FIFO, importance tiers, importance changes, yield-to-back behavior, context blocking, resource blocking, and blocked-task bypass.
- Control tests for every valid and invalid state transition, duplicate commands, Jarvis attribution, UI/API agreement, and default versus immediate pause/yield. Verify requested versus confirmed stop state and that cancel/immediate controls do not wait for the watchdog or a fixed grace interval.
- Checkpoint tests proving agent history, workflow stage, stable workspace, and result state survive yield, suspension, retry, crash, and process replacement.
- Fencing tests in which an old worker attempts progress, artifact, result, and terminal writes after restart or cancellation.
- Recovery tests for direct process-exit detection, heartbeat-only fallback, safe automatic recovery, reuse of completed action results, interrupted/ambiguous actions, repeated same-checkpoint crash, progress-reset recovery allowance, and manual restart. Verify deferred recovery-needed attention when no model is active.
- Reservation tests proving directory overlap exclusion, retained ownership across immediate crash handoff, release after confirmed pause/yield/cancel, no transfer while an old writing subprocess is alive, and reacquisition plus change checks after another task edits the directory.
- Codex integration tests proving native session survival across worker/host restart, same-task continuation, history replay after mobile reconnect, duplicate native-event reconciliation, Jarvis inspection, and actual descendant writer termination before lease release. Verify the native-tool action persistence boundary, not just event delivery.
- Capture admission tests for captured/pending/unavailable/expired status, two-second pre-admission wait, idempotent replay after capture expiry, and post-admission upload failure without automatic retry or a worker claim.
- SQLite stress tests with eight busy workers, heartbeat/event writes, concurrent admissions, controls, and result publication.
- Contract tests for Jarvis task tools and the authorization-reviewed gateway without WebRTC.
- Component tests for the Tasks UI using API fixtures; routine verification must not require browser or computer-control automation.
- Acceptance is reached when a saturated deterministic fixture demonstrates that Jarvis can inspect work, increase importance, yield or suspend another task, start the selected task, resume prior work from checkpoint, and recover a killed worker without duplicated durable output.

## 6. Fixed Assumptions

- One warm standby and eight busy workers are the configured v1 ceiling.
- Every task has persistent context and supports same-task follow-ups. Turns, execution runs, and recovery attempts are distinct; completion frees resources without deleting the session.
- Confirmed paused and yielded tasks consume no worker and release mutable resource reservations. Resume reacquires resources and checks relevant shared state.
- Both yield and suspend are supported, with graceful behavior by default and a Jarvis-selectable immediate mode.
- Shared filesystem edits use directory-level reservations initially. There is no separate interactive pool or reserved capacity.
- Jarvis and the user may resume suspended work.
- Jarvis may autonomously manage and cancel tasks; all such actions are durable and auditable.
- Cancelled tasks and screenshots remain retained privately for the initial debugging phase; v1 does not hard-delete them.
- Suspended tasks remain paused until an authorized resume command; opening voice or starting the background service does not itself resume them. Respect explicit user pause/cancel instructions during autonomous decisions.
- Phase 8 consumes the same task events and control APIs rather than becoming part of worker supervision. Its one background decision slot is independent of direct voice dispatch, deterministic updates, and the Codex worker pool.
## Completion evidence

See the [completion ledger](00-implementation-roadmap.md) and
[reports index](reports/README.md). Earlier progress reports are historical;
M0–M11 are closed under the user's accepted scope and caveats.
