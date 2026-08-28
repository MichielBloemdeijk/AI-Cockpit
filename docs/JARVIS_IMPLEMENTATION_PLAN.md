# Jarvis Full Implementation Plan — Daily Bridge Architecture

## Document control

- **Status:** approved implementation contract
- **Plan version:** 1.0
- **Saved:** 2026-08-27
- **Implementation baseline:** branch codex/jarvis-voice-poc at commit 281fc9f9cbe7fed44cc11b4b3ee7f2c7c56f2b97
- **Implementation state at save time:** Phase 0 complete; product implementation has not started
- **Execution record:** [JARVIS_IMPLEMENTATION_LEDGER.md](JARVIS_IMPLEMENTATION_LEDGER.md)
- **Normative architecture:** [JARVIS_TARGET_ARCHITECTURE.md](JARVIS_TARGET_ARCHITECTURE.md)
- **POC assessment:** [JARVIS_EVOLUTION_ARCHITECTURE.md](JARVIS_EVOLUTION_ARCHITECTURE.md)
- **Historical POC contract:** [JARVIS_VOICE_POC.md](JARVIS_VOICE_POC.md)

This document is the implementation contract for evolving the existing AI Cockpit and Jarvis POC. Future sessions should continue from the ledger, implement only the current phase, and update both the ledger and any contract that legitimately changes. The target and evolution documents explain the architecture at a high level; this document defines the sequence, boundaries, acceptance criteria, and verification needed to build it.

## 1. Outcome

Jarvis is a persistent, low-latency conversational bridge between the user and AI Cockpit's background agents. It is not a standard Agent mode and does not perform long inline tool workflows. It responds quickly, delegates substantial work, and naturally relays questions, progress, and results.

The product has five distinct concepts:

1. **Standard conversation:** a focused Agent or Council chat. A run may reason and use tools inline until it completes, becomes blocked, or asks for feedback.
2. **Jarvis daily log:** the durable transcript of Jarvis-shell interactions during one operational day or explicit same-day fresh-start segment.
3. **Background task:** durable agent work delegated by Jarvis or another surface. It has execution attempts and a detailed agent trace but is not a chat-list entry.
4. **Realtime session:** temporary OpenAI audio and provider state attached to the current Jarvis log.
5. **Jarvis live workspace:** the /jarvis command center showing the current log, all active tasks and questions, normalized activity, and voice state.

Standard chats and Jarvis logs share conversation, message, event, title, search, and transcript-rendering infrastructure. Product behavior remains separated by conversation_kind. Background tasks reuse the standard agent runtime but have their own global task identity and trace.

## 2. Locked product and architecture decisions

These decisions are closed for v1. A future change requires an explicit decision record and a plan revision; implementation convenience is not enough to silently change them.

### Conversation and history

- conversation_kind is standard or jarvis_daily.
- Existing standard chats cannot become Jarvis logs.
- Closed Jarvis logs cannot be reattached, continued, converted to Agent, branched, or resent.
- Jarvis may retrieve earlier standard chats and closed Jarvis logs only through delegated brain search.
- Exactly one Jarvis log is current.
- A current Jarvis entry appears in main history after its first durable turn.
- The entry shows Live during an active Realtime session and Current Jarvis while it remains the current log without a live call.
- Closed logs remain visible in main history and use the shared transcript renderer in read-only mode.
- closed_at means complete and visible. archived_at remains an independent user action that hides history.
- Main history filters are All, Standard, Jarvis, and hidden/archived.
- Tasks never appear as conversation-list entries.

### Operational day and rollover

- The timezone is Europe/Amsterdam.
- The operational day key is date(local timestamp minus four hours).
- An exchange crossing 04:00 is not split.
- The old log closes on the next accepted Jarvis activation after the exchange is idle.
- Task completion alone never creates a daily log.
- App launch alone never creates an empty daily log.
- The first accepted durable voice or typed Jarvis turn creates the log.
- An explicit start-fresh action appends an acknowledgement to the old log, closes it, creates the next same-day sequence, and starts a fresh provider session.
- Same-day titles are Jarvis — <date>, Jarvis — <date> (2), and so on.
- Tasks survive daily and explicit rollovers. Their origin remains on the old log; later presentation belongs to the current log through causal links.

### Jarvis versus Agent execution

- Voice and typed input on /jarvis both use execution_mode jarvis_shell.
- The Jarvis shell handles greetings, casual conversation, acknowledgement, clarification, stable general knowledge, and natural wording of structured facts.
- It delegates personal or current information, tools, mutations, actions, filesystem or browser work, Wiki access, and substantial work.
- Delegation returns a natural acknowledgement and task ID quickly.
- Standard Agent and Council chats retain inline tools and their existing focused execution model.
- Background tasks use the same agent runtime and trace presentation as standard Agent work, through a task-specific persistence sink.

### Voice and provider

- The primary model is OpenAI gpt-realtime-2.1 with low reasoning and Marin voice.
- Realtime 2.1 Mini is outside v1 implementation and testing. Realtime 1.5 is not implemented or tested.
- The foreground PWA uses WebRTC to OpenAI through backend-proxied SDP using the unified call interface.
- FastAPI captures the provider call ID and attaches a server-side sideband WebSocket.
- OpenAI event shapes stop at a provider-neutral VoiceSessionAdapter.
- One device controls live audio. Other authenticated views may observe durable streams read-only.
- Auto-arm is attempted when /jarvis opens. Browser-policy failure exposes an Arm button.
- Wake detection is browser-local and open source, using a Hey Buddy ONNX model trained for the exact word Jarvis.
- The wake-free follow-up window is 60 seconds.
- Provider sessions rotate at 55 minutes and are treated as temporary state.
- Hosted voice failure falls back to typed Jarvis through the local brain. There is no local speech-to-speech fallback in v1.
- Jarvis never speaks over the user. The user may interrupt Jarvis.
- Raw audio and the wake ring buffer are never retained.

The Realtime choices were checked against current official OpenAI documentation when this plan was saved:

- [GPT-Realtime-2.1 model](https://developers.openai.com/api/docs/models/gpt-realtime-2.1)
- [Realtime WebRTC](https://developers.openai.com/api/docs/guides/realtime-webrtc)
- [Realtime server-side controls](https://developers.openai.com/api/docs/guides/realtime-server-controls)
- [Realtime conversation lifecycle](https://developers.openai.com/api/docs/guides/realtime-conversations)
- [Realtime VAD](https://developers.openai.com/api/docs/guides/realtime-vad)

Provider details must be rechecked against official documentation immediately before Phase 6 implementation because event schemas can change.

### Tasks, worker, and correctness

- Tasks are global durable objects shared by Jarvis, task UI, and other authorized surfaces.
- One separate worker executes tasks.
- Scheduling is strict FIFO by creation time and stable ID, with no preemption.
- A lease-blocked queue head is not bypassed.
- Failed, interrupted, and cancelled work retries as a new attempt under the same task only after explicit user action.
- Questions checkpoint the task, release leases, and move it to waiting_for_input. Answering creates a new attempt.
- Mutable resources use typed, exclusive, all-or-nothing leases over a sorted target set.
- Discovering a new mutable target checkpoints and requeues the task after releasing all leases and expanding the target set.
- API restart does not interrupt worker-owned execution.
- Worker or PC loss marks unfinished work interrupted during the next worker takeover. It is never automatically resumed or retried.
- Push notifications are limited to terminal task states and blocking questions.

### Scope and posture

- This remains a personal, single-user, local-first project.
- Existing API key protection, authentication, workspace boundaries, idempotency, audit records, and leases remain required correctness controls.
- Multi-user authorization, enterprise policy, and advanced approval gates are outside v1.
- Existing database content has no migration value. A verified backup is retained, then Phase 1 cuts over to a clean schema.
- Real acoustic behavior is verified manually. Deterministic state, persistence, provider simulations, and UI projections are automated.

## 3. System topology and ownership

~~~mermaid
flowchart LR
    PWA[Jarvis PWA /jarvis] <-->|WebRTC media and data| OAI[OpenAI Realtime 2.1]
    PWA <-->|SDP bootstrap and durable streams| API[FastAPI modular monolith]
    OAI <-->|Sideband WebSocket| COORD[Jarvis coordinator]
    API --> COORD

    COORD --> CONV[Conversation service]
    COORD --> BRAIN[Restricted brain gateway]
    COORD --> PROJ[Context projection]
    BRAIN --> TASKS[Global task service]
    TASKS --> WORKER[Separate FIFO worker]
    WORKER --> RUNTIME[Shared agent runtime and tools]
    WORKER --> LEASES[Resource leases]
    BRAIN --> WIKI[Wiki and conversation search]

    CONV --> DB[(SQLite WAL)]
    TASKS --> DB
    LEASES --> DB
    WIKI --> DB
    PROJ --> DB
~~~

### Frontend ownership

The frontend owns microphone permission, local wake inference, the short in-memory audio ring buffer, WebRTC media, immediate playback mute, visible live state, typed Jarvis input, and read-only projections. It does not own durable delivery truth, task state, provider secrets, or tool execution.

### Coordinator ownership

The coordinator owns the active provider session generation, sideband lifecycle, floor state, provider normalization, context hydration, shell tool mediation, delivery accounting, safe presentation boundaries, and rollover coordination. It does not execute unrestricted tools or long-running tasks.

### Conversation service ownership

The conversation service owns standard conversations and Jarvis daily logs, turns, transcript messages, branches where allowed, monotonic per-conversation events, titles, lifecycle, archival, and audible-history truth.

### Task service ownership

The task service owns global task identity, state transitions, attempts, questions, artifacts, targets, results, cancellation, retry, presentation candidates, idempotency, and the monotonic global task cursor.

### Worker ownership

The worker owns claiming and executing one task, heartbeats, attempt workspaces, checkpoints, cancellation observation, lease renewal, agent-runtime integration, and task events. The FastAPI process never owns task execution.

### Wiki ownership

The Wiki service owns approved knowledge reads, proposal writes, review, provenance, and search. The Realtime shell never receives direct Wiki or archive access.

## 4. Durable data contracts

Names below are contractual domain names. Exact SQLAlchemy filenames may follow current repository conventions, but schema meaning and invariants must remain stable.

### conversations

Extend the common conversation record with:

- conversation_kind: standard or jarvis_daily;
- lifecycle_status: open or closed;
- closed_at: nullable timestamp;
- jarvis_day_key: nullable local operational date;
- jarvis_day_sequence: nullable positive integer;
- archived_at: existing independent nullable timestamp;
- existing title, workspace, timestamps, and branch metadata.

Constraints:

- Standard conversations have null Jarvis day fields.
- Jarvis daily logs have a day key and positive sequence.
- The pair jarvis_day_key and jarvis_day_sequence is unique for Jarvis logs.
- Closed Jarvis logs reject content mutation at the service boundary.
- Archival never changes lifecycle_status.

### jarvis_state

One singleton row stores:

- current_log_id;
- the day key and sequence used to create it;
- a state revision for compare-and-swap updates;
- created_at and updated_at.

It points only to an open jarvis_daily conversation. Creation, rollover, and pointer replacement occur in one transaction.

### conversation_messages

conversation_messages.id remains the public turn_id. Add:

- modality: voice, text, or system;
- execution_mode: jarvis_shell, agent, or council;
- communication_state: completed, interrupted, unheard, or transcription_failed;
- source_task_id;
- source_task_event_id;
- presented_in_turn_id where needed for causal presentation.

The common transcript renderer consumes these fields. Standard chats retain their existing behavior; Jarvis-specific write rules live in the Jarvis application service rather than frontend heuristics.

### speech_deliveries

Each generated Jarvis utterance records:

- internal delivery ID and assistant turn ID when a bubble exists;
- provider session, response, and item IDs;
- full generated transcript;
- best-effort heard transcript;
- state: generating, speaking, completed, interrupted, or unheard;
- generation, playback, interruption, and completion timestamps;
- source task, task event, presentation, and provider causality;
- optional normalized timing and usage summary.

Rules:

- Completed speech writes the full transcript as the heard assistant message.
- Interrupted speech writes only the estimated heard prefix and interruption state.
- Entirely unheard speech creates delivery and activity records but no empty chat bubble.
- Hydration uses heard content only.
- Raw audio is absent from this and every other durable table.

### conversation_events

Each conversation has an atomic monotonic cursor. Significant events include accepted turns, committed transcript messages, delivery completion/interruption, task facts actually presented, lifecycle changes, and metadata changes. Events and current-state mutations commit together.

### realtime_sessions and voice_session_events

A Realtime session stores internal identity, current generation, attached log, provider adapter name, lifecycle state, controller identity, timestamps, and non-secret timing/usage summaries. Provider call IDs remain backend-only and may be encrypted or omitted from API projections.

Normalized session events use a monotonic per-session cursor and include:

- connecting, ready, disconnected, superseded;
- listening, user-speaking, assistant-generating, assistant-speaking;
- interruption and truncation;
- shell tool dispatch and result;
- presentation reservation and outcome;
- reconnect and provider errors;
- timing and usage summaries.

Audio frames and high-frequency transcript/audio deltas are never persisted.

### tasks

A task stores stable identity, origin surface, origin conversation and turn, goal, normalized status, creation order, idempotency key, current attempt, cancellation state, importance/presentation policy, timestamps, and terminal summary.

Statuses are queued, running, waiting_for_input, completed, failed, interrupted, and cancelled.

### task_attempts

An attempt stores task ID, attempt number, worker, isolated workspace, checkpoint, claim and heartbeat data, started/ended timestamps, terminal reason, agent-runtime references, and usage summary. Attempt numbers are unique and monotonic within a task.

### task_events

Task events share one atomic global cursor. Events carry task ID, attempt ID where relevant, event type, structured payload, causal IDs, timestamp, and idempotency key. State transitions and the corresponding event commit together.

### task_questions, task_artifacts, and task_targets

- Questions are durable, answerable once, and carry asked/answered state plus causal attempt IDs.
- Artifacts retain the existing agent artifact vocabulary and provenance.
- Targets identify every mutable resource by stable resource_type and resource_key.

### resource_leases and worker_instances

- Leases are exclusive over resource type/key, owned by one attempt, expire after 30 seconds, and renew every five seconds.
- Target sets are acquired atomically in stable sorted order.
- Worker instances heartbeat every five seconds and become stale after 20 seconds.
- Worker takeover reconciles stale claims into interrupted tasks without retry.

### task_presentations

A presentation connects a task fact or task event to its conversational delivery. It stores candidate type, coalescing key, importance, state, reservation/session generation, required facts, source event, presented turn/delivery, timestamps, and retry/defer information.

States should distinguish pending, reserved, presented, folded, deferred, dismissed, and superseded. Questions cannot be silently superseded. UI visibility never counts as spoken presentation.

### notification_outbox

The outbox stores terminal task and blocking-question notification intents, dedupe keys, delivery attempts, acknowledgement, deep links, and errors. Notification acknowledgement is independent of speech presentation.

## 5. Ordering, consistency, and causality

There is no universal total event clock.

- Each conversation has a monotonic conversation cursor.
- The task domain has one global monotonic task cursor.
- Each Realtime session has a monotonic session-event cursor.
- The /jarvis activity feed merges projections by display timestamp only.
- Cross-domain relationships use origin_turn_id, task_id, source_task_event_id, presentation_id, and presented_in_turn_id.

Context projection reads conversation state and task state within one SQLite snapshot, returns both independent cursors, and then replays newer committed events independently. A snapshot must never advertise a cursor whose corresponding row is missing.

Idempotency is required at browser retries, shell tool retries, provider duplicate events, task dispatch, event append, question answer, cancellation, retry, presentation reservation, and notification enqueue boundaries.

## 6. Daily-log lifecycle

The authoritative operational day function is:

~~~text
jarvis_day_key(timestamp) = date(convert_to_Europe_Amsterdam(timestamp) - 4 hours)
~~~

Activation resolution is transactional:

1. Do nothing on page load unless a durable turn is accepted.
2. Resolve current day key on the first accepted voice or typed interaction.
3. If there is no current open log, create sequence 1 and update jarvis_state.
4. If the current log belongs to an earlier day and no exchange is active, close it and create sequence 1 for the new day.
5. If an exchange is active across 04:00, retain the old log until the next activation after idle.
6. For explicit fresh start, append and deliver the acknowledgement on the old log, close it, create the next same-day sequence, update jarvis_state, supersede the provider generation, and establish a clean session only when needed.
7. Never cancel or reparent active tasks during rollover.
8. Never create a log solely to present task completion. Presentation waits for a current user-created log or a later interaction.

## 7. Transcript and interruption truth

The assistant transcript represents what the user probably heard, not everything the model generated.

During playback, the browser maintains local playout telemetry and immediately mutes output when local speech activity indicates user barge-in. The coordinator combines browser telemetry with provider interruption/truncation. A configurable 250 ms transcript-delta margin is only the initial fallback estimator; it is not considered the final accuracy mechanism unless measured browser/provider traces show that it meets the manual heard-prefix gate.

On interruption:

1. Mute output immediately.
2. Cancel/truncate the provider response.
3. Finalize the speech_delivery with full generated and heard transcripts.
4. Commit only the heard prefix to the assistant transcript.
5. Preserve an interruption marker in the event/context projection.
6. Return any incompletely heard task fact to pending presentation.
7. Generate a natural response to the user's interruption using heard context and task facts; never resume at the next token mechanically.

Ordinary unheard speech may be abandoned after a topic change. Structured task facts remain pending until presented, dismissed, superseded by a terminal fact, or no longer relevant.

## 8. Context projection

Each new provider session receives:

- up to the latest 60 communicated turns from the current Jarvis log;
- a 30,000-character maximum dialogue budget;
- interruption markers and heard assistant text only;
- compact running and waiting task summaries;
- pending task questions;
- important undelivered presentation candidates;
- brain-supplied approved Wiki or archive facts when explicitly retrieved;
- independent conversation, task, and session cursors.

A new operational day automatically loads no earlier dialogue. Older logs and standard chats remain discoverable only through delegated search. Context refresh must update structured task summaries without injecting worker noise into conversational history.

## 9. Realtime session contract

### Establishment

1. Browser captures permission and creates a WebRTC offer.
2. Browser posts SDP to AI Cockpit.
3. AI Cockpit creates the OpenAI call with its server-side API key.
4. Backend captures the provider call ID and SDP answer.
5. Backend attaches the sideband WebSocket.
6. Coordinator configures gpt-realtime-2.1, low reasoning, Marin, near-field noise reduction, semantic VAD with low eagerness, automatic response creation, and interruption.
7. Coordinator hydrates current log and task projections.
8. Sideband readiness becomes a prerequisite for returning success. Failure hangs up the partial provider call.
9. Browser receives only internal session identity and SDP answer.

### Controller and generation rules

- Exactly one controller generation may mutate the current log.
- A new controller supersedes the old session and increments generation.
- Every provider callback and browser telemetry write checks internal session ID and generation.
- Stale callbacks may be logged diagnostically but cannot mutate conversation, delivery, or presentation truth.

### Reconnect and rotation

- Retry a lost active session after 0.5, 1, and 2 seconds.
- Rehydrate every replacement from durable state and independent cursors.
- Enter typed fallback after retries fail.
- Rotate a continuously active session at 55 minutes using the same supersession and hydration path.

### Restricted shell capabilities

The shell receives only:

- dispatch_task
- get_task
- answer_task_question
- cancel_task
- start_new_jarvis_log
- end_voice_session

It never receives filesystem, browser, calendar, Wiki, code execution, generated-app mutation, or general tool-registry access.

## 10. Task and worker lifecycle

### Dispatch

The shell or another surface submits goal, origin, declared targets, importance, presentation policy, and an idempotency key. Dispatch commits the queued task and task event atomically and returns accepted identity quickly.

### Claim and execution

The one worker:

1. heartbeats;
2. selects the oldest queued task by creation time and stable ID;
3. does not skip a lease-blocked head;
4. atomically acquires the entire sorted target set;
5. creates the next attempt and isolated workspace;
6. runs the shared agent runtime through AgentExecutionContext and AgentExecutionSink;
7. renews leases and checks cancellation;
8. commits structured progress, questions, artifacts, and terminal results through task events;
9. releases leases on every terminal, waiting, checkpoint, or requeue path.

### Retry, question, cancellation, and loss

- Retry is allowed for failed, interrupted, or cancelled tasks and creates a new attempt under the same task.
- A question checkpoints execution, releases leases, and transitions to waiting_for_input.
- An answer is accepted once, queues the same task, and creates a new attempt when claimed.
- Cancellation is idempotent; queued work cancels immediately, running work observes cancellation and exits safely.
- API restart has no ownership effect on worker execution.
- Worker staleness reconciliation interrupts the task, expires claims and leases, and does not requeue automatically.

## 11. Proactive presentation

Candidates come from explicit update requests, important milestones, blocking questions, and terminal outcomes.

Deterministic coalescing rules run before model judgement:

- terminal outcomes supersede pending progress for the same task/topic;
- newer progress supersedes older progress for the same task/topic;
- questions are never silently removed;
- UI visibility does not alter spoken state;
- no candidate may be presented twice.

At a safe boundary, an out-of-band text-only Realtime request returns exactly PRESENT, FOLD, or DEFER. Invalid output, timeout, or provider failure is DEFER.

Reservation is atomic and generation-bound. A candidate becomes presented only after an uninterrupted response or when the durable heard prefix includes all required facts. Otherwise it returns to pending. Jarvis never initiates proactive speech while the user owns the floor.

## 12. Public API plan

Exact request/response schemas are finalized in the implementing phase and must be typed in backend Pydantic and frontend TypeScript from one documented contract.

### Jarvis

- GET /api/jarvis/state
- POST /api/jarvis/logs/new
- POST /api/jarvis/turns/text
- GET /api/jarvis/logs/{id}/events?after=
- GET /api/jarvis/sessions/{id}/events?after=
- POST /api/jarvis/realtime/calls
- POST /api/jarvis/realtime/sessions/{id}/playback
- DELETE /api/jarvis/realtime/sessions/{id}

GET /api/jarvis/state returns the current log summary and operational day, durable transcript, active tasks and questions, pending presentations, voice/session state, worker health, and conversation/task/session cursors.

POST /api/jarvis/turns/text returns HTTP 202 and the accepted turn ID. Direct shell output and later task facts arrive through the same streams as voice.

### Conversations

Existing conversation APIs gain filters and response metadata for kind, lifecycle, current/live state, operational day, sequence, and archival. Write APIs reject closed Jarvis logs. Current Jarvis selection routes to /jarvis; closed Jarvis selection uses read-only transcript rendering.

### Tasks

- GET /api/tasks
- GET /api/tasks/{id}
- POST /api/tasks/{id}/cancel
- POST /api/tasks/{id}/retry
- POST /api/tasks/{id}/questions/{question_id}/answer
- GET /api/tasks/events?after=

Task detail includes identity, origin, state, attempts, ordered trace, questions, targets and lease state, artifacts, results, presentations, and controls allowed by current status.

## 13. Jarvis live workspace

Desktop /jarvis contains:

- **Header:** armed, connecting, listening, user speaking, thinking, assistant speaking, text fallback, worker health, current title, and explicit fresh-start control.
- **Conversation panel:** current transcript, live partial indicators, typed Jarvis composer, microphone/arm/sleep controls, and connection state.
- **Tasks panel:** all queued/running/waiting tasks, related-task highlighting, queue position, questions, cancel/retry, and task-detail links.
- **Activity panel:** normalized coordinator, brain, worker, delivery, task, presentation, and provider lifecycle events.

Mobile uses the same projection in stacked panels or tabs. There is one live controller; other views may observe read-only streams.

Main history immediately includes a non-empty current Jarvis log. Clicking current opens /jarvis. Clicking a closed daily log uses the shared transcript renderer without composer, branch, resend, or tool controls.

## 14. Verifiable implementation phases

Each phase begins only after its predecessor's exit checkpoint passes. If a checkpoint fails, fix it in the same phase or record a genuine blocker; do not begin dependent work.

### Phase 0 — Plan, baseline, and reversible cutover preparation

**Objective:** make the target, execution sequence, initial evidence, and rollback path durable without changing product behavior.

**Deliverables:**

- this implementation contract;
- updated target and evolution documents using daily logs and the 04:00 boundary;
- the implementation ledger with locked decisions and next-session entry point;
- full backend/frontend baseline evidence;
- integrity-checked ignored database backup;
- an explicit list of preserved user-owned and POC assets.

**Exit gate:**

- all documents link correctly;
- backend tests, frontend tests, lint, and production build have recorded outcomes;
- backup exists, is ignored, has a SHA-256 hash, and passes SQLite integrity check;
- active POC database remains recoverable;
- no product implementation files changed.

### Phase 1 — Clean schema and ordered-domain contracts

**Objective:** establish the durable Jarvis-era truth model on a clean database while preserving standard-chat capabilities in schema and repositories.

**Scope:**

- create one clean Jarvis-era Alembic baseline;
- add conversation lifecycle/day fields, jarvis_state, speech deliveries, Realtime sessions/events, global tasks, attempts, questions, artifacts, targets, leases, presentations, workers, and notification outbox;
- retain standard chat, Council, branches, artifacts, generated apps, and Wiki review structures;
- add atomic conversation/task/session sequences;
- add idempotent task dispatch and event append;
- add consistent multi-domain snapshots;
- switch runtime to a fresh database only after the baseline migration and tests pass.

**Likely change zones:** backend/app/db, backend/alembic, repositories/services that currently allocate conversation sequences, test fixtures, and configuration/bootstrap scripts.

**Automated exit tests:**

- blank database upgrades successfully;
- table/constraint contract inspection passes;
- concurrent writers cannot duplicate sequences;
- snapshot cursors never advertise missing rows;
- repeated idempotency keys return the same task/event;
- standard conversation persistence remains green;
- old backup remains readable and is not used by the new runtime.

**Rollback:** restore the configured database path to the Phase 0 backup or original POC file. No semantic migration rollback is required.

### Phase 2 — Standard conversations and Jarvis daily lifecycle

**Objective:** replace the primary-voice singleton with correct daily-log lifecycle and shared history presentation.

**Scope:**

- implement operational-day function and transactional current-log resolution;
- implement lazy first-turn creation, 04:00 rollover, active-exchange deferral, explicit fresh start, daily titles, and immutable closed logs;
- write modality, execution mode, communication state, and causal metadata;
- expose kind/lifecycle filters and badges;
- route current entries to /jarvis and closed entries to read-only shared transcript rendering;
- preserve standard Agent/Council behavior.

**Automated exit tests:**

- page load creates no log;
- first accepted durable turn creates sequence 1;
- DST and timestamps around 04:00 produce the correct Europe/Amsterdam day key;
- an active exchange is not split;
- next activation rolls over;
- explicit fresh start produces sequence 2 and closes sequence 1;
- competing first turns create one current log;
- closed logs reject turns, branch, and resend;
- active tasks retain identity across rollover;
- archival and closure remain independent;
- standard conversations remain unchanged.

### Phase 3 — First-class tasks and task UI

**Objective:** make global tasks the single source of background-work truth and expose their full trace outside the chat list.

**Scope:**

- implement task lifecycle, attempts, questions, artifacts, results, cancellation, retry, presentations, and events;
- replace voice child-run identity and run-metadata projections;
- make Jarvis, standard chat, and task UI call one task service;
- build /tasks and /tasks/{id} using reusable Agent trace components;
- redirect /background to /tasks;
- show queue position, worker health, attempts, events, questions, results, cancel, and retry.

**Automated exit tests:**

- dispatch is idempotent;
- task cursor is ordered and replayable;
- retry adds an attempt to the same task;
- a question is answered once;
- cancellation follows the state machine;
- event replay deduplicates;
- tasks do not appear in conversation history;
- task detail renders a representative Agent trace.

### Phase 4 — Worker, agent-runtime seam, and resource leases

**Objective:** move durable background execution out of FastAPI and reuse the existing agent runtime safely.

**Scope:**

- extract AgentExecutionContext and AgentExecutionSink from conversation-specific execution;
- retain standard-chat sink and add task sink;
- add uv run python -m app.worker entry point;
- implement heartbeat, strict FIFO claim, cancellation, checkpoint, takeover, and isolated attempt workspaces;
- generalize generated-app leases to typed atomic target sets;
- implement target-expansion checkpoint/requeue;
- add start-worker.ps1 and health projection.

**Automated exit tests:**

- only one worker becomes active;
- only one worker claims a task under contention;
- strict FIFO is stable across restart;
- a lease-blocked head is not bypassed;
- multi-target acquisition is atomic and sorted;
- question/cancel/checkpoint releases every lease;
- target expansion requeues the same task and creates a new attempt;
- API restart leaves worker work running;
- stale worker takeover marks interrupted without retry;
- a deterministic real agent task completes through the worker.

### Phase 5 — Coordinator, fake provider, and live workspace

**Objective:** prove the complete product state machine without microphone or provider dependence.

**Scope:**

- implement provider-neutral VoiceSessionAdapter and coordinator state;
- add fake provider with deterministic event injection;
- build /jarvis conversation/tasks/activity/header projections;
- stream conversation, task, and session domains independently;
- add restricted brain gateway and stale-generation checks;
- coordinate rollover and session supersession;
- build a developer conversation lab.

**Required simulated scenarios:**

- direct shell response;
- delegation and immediate acknowledgement;
- fast and slow task completion;
- completion while user owns the floor;
- task question and answer;
- explicit fresh log;
- daily rollover with active tasks;
- interruption and natural continuation;
- duplicate/reordered delivery events within supported ordering rules;
- reconnect and stale-session rejection;
- coalesced and deferred presentations;
- controller supersession with observer views.

**Exit gate:** every scenario passes through backend tests and frontend state tests, and the live workspace is usable with the fake provider.

### Phase 6 — OpenAI Realtime and WebRTC

**Objective:** connect the proven coordinator to OpenAI without leaking provider concerns or secrets.

**Preparation:** reread current official OpenAI model, WebRTC, server-control, conversation, VAD, and event-reference documentation. Record any API drift as a plan deviation before coding.

**Scope:**

- implement backend SDP proxy and provider call-ID capture;
- implement explicit sideband WebSocket readiness and control;
- configure Realtime 2.1 low, Marin, noise reduction, semantic VAD, response creation, and interruption;
- normalize OpenAI events behind the adapter;
- implement browser offer/answer, mic track, remote audio, data/control channel, teardown, and reconnect;
- implement 0.5/1/2 second retries, typed fallback, and 55-minute rotation;
- keep provider API key and call ID backend-only.

**Automated exit tests:**

- fake OpenAI HTTP/sideband exchanges validate request and event contracts;
- secrets are absent from API response schemas and frontend bundles;
- sideband failure closes partial calls;
- stale provider events cannot mutate current state;
- reconnect hydrates from durable cursors;
- rotation uses normal supersession.

**Manual exit checks:**

- live speech commits a user transcript and Marin response;
- typed /jarvis turns use the shell, not standard Agent execution;
- live reconnect preserves conversational continuity.

### Phase 7 — Barge-in and audible-history truth

**Objective:** make interruption immediate, natural, and truthful in durable history.

**Research checkpoint before implementation tuning:**

- reproduce the observed sentence-scale cutoff drift and capture monotonic browser timestamps for received audio, actual playout, local mute, provider interruption, transcript deltas, and final truncation;
- compare Web Audio playout-clock instrumentation, applicable WebRTC inbound/jitter-buffer statistics, and provider `audio_end_ms` truncation acknowledgement instead of assuming generated transcript timing equals audible timing;
- measure early and late cutoff error over short and long responses, different speaking rates, and at least desktop Chrome plus the intended phone/PWA path;
- write the selected estimator and fallback hierarchy into the ledger before fixing the durable delivery contract;
- retain the 250 ms delta-age heuristic only as a bounded fallback when more direct playout evidence is unavailable.

**Scope:**

- keep local speech activity detection active while Jarvis speaks;
- immediately mute output on detected user speech;
- combine playback telemetry with provider truncation;
- estimate heard prefix from measured playout/truncation evidence, with the configurable 250 ms delta-age margin as fallback;
- persist generated and heard transcripts separately;
- hydrate only heard content;
- regenerate natural continuations;
- return incompletely heard task facts to pending.

**Automated exit tests:** deterministic telemetry/provider timelines cover completed, interrupted, unheard, late/double events, reconnect, task-fact recovery, and hydration. The selected estimator must also have trace-fixture tests for both early and late cutoff error; a full-sentence discrepancy is a failed gate, not an acceptable approximation.

**Manual exit checks:**

- speaking over Jarvis stops audible output immediately;
- Jarvis never starts over user speech;
- unheard tails are absent after refresh/reconnect;
- continue avoids repeating the whole response;
- topic changes abandon ordinary tails without losing task facts.

### Phase 8 — Brain delegation and proactive updates

**Objective:** enforce the thin-shell boundary and make asynchronous work conversational.

**Scope:**

- finalize system prompt and restricted tools;
- enforce delegation categories;
- standardize progress, question, result, fact, source, and presentation envelopes;
- implement coalescing, reservation, OOB timing judgement, presentation completion, and interruption recovery;
- update compact task summaries without polluting dialogue.

**Automated exit tests:**

- shell cannot invoke unrestricted tools;
- dispatch retries do not duplicate work;
- substantial requests are acknowledged and delegated;
- questions become presentation candidates;
- completion during user speech waits;
- interrupted facts remain pending;
- terminal outcomes supersede progress;
- invalid OOB output defers;
- a presentation cannot complete twice.

### Phase 9 — Browser-local wake and follow-up lifecycle

**Objective:** enable a responsive local Jarvis wake path without retaining audio.

**Scope:**

- integrate Apache-2.0 Hey Buddy ONNX browser runtime with ONNX Runtime Web;
- train and version a single-word Jarvis model and record its data/license provenance;
- run inference off the UI thread;
- maintain a 2.5-second in-memory ring buffer;
- auto-arm or expose Arm fallback;
- establish WebRTC on detection while retaining the command tail;
- replay one-shot utterance before live track;
- use temporary sideband injection only if data-channel replay duplicates or rejects audio;
- re-arm after 60 seconds idle or explicit sleep.

**Automated exit tests:** worker lifecycle, arm state, ring-buffer bounds/destruction, one-shot handoff state, follow-up timeout, and UI responsiveness are deterministic.

**Manual acceptance:**

- at least 18 of 20 deliberate invocations succeed;
- no more than one false wake in 30 minutes of mixed speech/media;
- activation does not visibly stall the PWA;
- Jarvis alone receives a natural acknowledgement;
- Jarvis, followed by a command preserves the command;
- browser-blocked auto-arm exposes a functional button.

Allow at most two tuning passes. If acceptance still fails, retain the current open-source local VAD plus faster-whisper wake gate temporarily and document it. Kokoro does not return.

### Phase 10 — Wiki/search, notifications, and archive experience

**Objective:** connect durable history and reviewed knowledge to the brain and finish user-facing lifecycle surfaces.

**Scope:**

- expose reviewed knowledge as /wiki and redirect /knowledge;
- add brain-facing Wiki and conversation search over standard chats and closed Jarvis logs;
- begin with recent browsing plus exact/substring matching;
- preserve proposal review and provenance;
- add durable Push outbox for terminal states and blocking questions;
- deep-link to task/question;
- finalize history badges, filters, titles, read-only archive, and related-task links;
- change PWA start URL to /jarvis.

**Automated/manual exit checks:**

- a brain task retrieves an earlier daily log;
- shell cannot query archives directly;
- Wiki writes remain review proposals;
- ordinary progress does not push;
- terminal states/questions push once;
- notification acknowledgement does not mark speech presented;
- history filters and read-only rules hold across desktop/mobile.

### Phase 11 — Retire POC paths and final verification

**Objective:** leave one production architecture with no hidden dependency on the retired POC.

**Scope:**

- remove batch voice-turn, speech, transcription, voice-task, and browser-delivery APIs;
- remove Kokoro runtime/config/assets;
- remove faster-whisper unless the documented wake fallback was selected;
- remove JarvisSpeechScheduler, primary-voice flags, cloud-next-turn toggle, and browser-only delivery truth;
- remove API-process ownership of background tasks;
- retain the completed `/voice` to `/jarvis` redirect and redirect `/background` and `/knowledge` to their final destinations;
- update README, architecture, startup, configuration, and POC-versus-target documentation;
- run dead-code and dependency checks;
- record latency and Realtime cost observations without making them release gates.

**Final gate:**

- fresh setup creates schema and starts backend, worker, frontend, and Jarvis;
- complete backend/frontend tests, lint, and production build pass;
- every fake-provider scenario passes;
- every manual voice scenario passes;
- daily rollover, explicit fresh start, task continuation, reconnect, API restart, worker restart, and PC restart match this contract;
- no raw audio is retained;
- no browser response or bundle exposes the OpenAI API key or provider call ID;
- no shipped feature depends on retired POC code.

## 15. Cross-phase verification matrix

Automate:

- operational-day and DST boundaries;
- lifecycle and immutability;
- atomic ordering and replay;
- idempotency and duplicate provider events;
- task transitions, FIFO, retry, cancellation, and questions;
- worker claim, heartbeat, loss, and API independence;
- lease acquisition, expiry, expansion, and release;
- consistent snapshots and context budgets;
- session generations, reconnect, rotation, and stale rejection;
- speech-delivery state using simulated timelines;
- presentation coalescing, reservation, and recovery;
- notification outbox dedupe;
- frontend projection, badges, filters, read-only rules, and responsive state.

Keep manual:

- wake quality and false activations;
- actual microphone/browser permission behavior;
- real barge-in latency and no-talk-over feel;
- heard-prefix perceptual accuracy;
- natural continuation and proactive timing quality;
- Marin voice quality;
- multi-hour real-session rotation.

## 16. Multi-session execution protocol

Every implementation session must:

1. Read this document, the ledger, and any applicable AGENTS.md before editing.
2. Confirm branch, HEAD, worktree status, current phase, and unrelated user changes.
3. Work only on the current phase or an explicitly recorded prerequisite.
4. Add tests with each deterministic contract rather than deferring all coverage to the checkpoint.
5. Run the narrowest relevant checks during development and the complete phase gate before completion.
6. Update the ledger with exact commands, outcomes, deviations, unresolved observations, files/subsystems changed, and the next entry point.
7. Update target/evolution/current architecture documents only when their described truth changes.
8. Finish with a reviewable commit containing only in-scope changes. The ledger identifies the phase commit by subject and records its hash from the following session because a commit cannot contain its own final hash.
9. Stop before dependent phases when the exit gate fails.

Recommended commit subjects are phase-scoped, for example docs: establish Jarvis implementation contract or feat(jarvis): add daily log persistence.

## 17. Preserved assets and cutover rules

Preserve unless a later phase explicitly retires them:

- user Wiki/knowledge files and approved reviewed-memory provenance;
- generated-app source and registry metadata;
- the historical Jarvis POC document and evidence;
- standard chat, Council, branch, artifact, and app-builder capabilities;
- unrelated dirty/untracked user files;
- the verified Phase 0 database backup.

The POC database receives no semantic migration. Phase 1 creates and validates the new baseline first, then changes runtime configuration or replaces the active database. The backup is never edited in place.

## 18. Explicitly outside v1

- Realtime 2.1 Mini selection or comparison;
- Realtime 1.5 implementation or tests;
- semantic/vector retrieval;
- automatic Wiki approval;
- multiple task workers;
- task preemption or queue-head bypass;
- ambient unsolicited speech;
- Jarvis speaking over or interrupting the user;
- LiveKit transport;
- multiple simultaneous audio controllers or floor arbitration;
- local speech-to-speech fallback;
- continuation of closed Jarvis logs;
- conversion between standard and Jarvis conversation kinds;
- advanced roles, tenancy, or authorization policy.

## 19. Remaining implementation research, not open architecture

No blocking architectural decision remains. The following are bounded implementation investigations with predefined fallbacks:

- verify current OpenAI event/config schemas in Phase 6;
- complete the Phase 7 measured playout/truncation comparison and calibrate the 250 ms heuristic only as its fallback;
- validate SQLite transaction/locking behavior with the separate worker under deterministic load;
- exercise browser reconnect and provider interruption edge cases;
- evaluate PRESENT/FOLD/DEFER consistency behind the replaceable timing policy;
- tune Hey Buddy for two passes, then use the documented VAD plus faster-whisper wake fallback if required.

If research contradicts a locked invariant, stop and request an explicit architecture revision. If it only changes an adapter detail or threshold, record the deviation and continue within the phase.

## 20. Assumptions

- Europe/Amsterdam remains the operational timezone.
- The AI Cockpit PC, FastAPI process, and worker are required for Jarvis.
- One device controls live audio.
- Authenticated observers may view durable streams read-only.
- Standard and Jarvis conversations share infrastructure but never change kinds.
- Closed Jarvis logs are searchable/readable but immutable.
- Background tasks are global execution objects, not conversations.
- SQLite WAL remains the local durable store for v1.
- Provider and browser capabilities are allowed to fail into typed Jarvis fallback without local voice continuity.
