# Architecture

## Purpose

This document describes the current architecture of AI Cockpit as it exists in the repository today. It is intentionally current-state focused: it explains what is implemented, how the main parts interact, where code lives, and which parts are still intentionally incomplete.

Jarvis worker tools passed M10 on 2026-10-09: native hosted Codex research,
persisted global/workflow/task tool choices, approved skills, native subagents
and exact human connector permissions are implemented. The explicit trusted
Windows command broker remains the tested coding mode; native coding is gated
by Python temporary-directory permissions. Fifteen required final assertions
passed with four accepted M8 caveats. M11 personal phone use was accepted on
2026-10-11, including working tasks and screenshot-based recipe creation. See
the [final acceptance record](jarvis-tasks/reports/personal-use-accepted-2026-10-11.md).
See the [worker policy](jarvis-tasks/09-worker-tool-policy.md) and
[completion evidence](jarvis-tasks/reports/worker-tools-closure-2026-10-08.md).

## Related Docs

- [README.md](../README.md) for setup, runtime entrypoints, and the high-level product overview
- [JARVIS_TASK_ARCHITECTURE.md](JARVIS_TASK_ARCHITECTURE.md) for the implemented modular task architecture and phase contracts
- [JARVIS_GAPS.md](JARVIS_GAPS.md) for accepted limits and archived user-visible observations
- [JARVIS_WAKE_MODEL.md](JARVIS_WAKE_MODEL.md) for wake-model provenance, evaluation, and calibration gates

## System Overview

AI Cockpit is a locally hosted personal AI workspace built around these active capabilities:

- authenticated chat against provider profiles for the loopback local OpenAI-compatible server or explicit OpenRouter cloud turns
- council mode that fans a prompt out to multiple models and synthesizes the result
- durable conversation history with runs, events, transcript messages, artifacts, branches, archive state, and reviewed memory items
- reviewed memory proposals and approved Wiki documents, plus a unified FTS5 recall index over user-visible conversations, tasks, and approved memory
- configurable defaults for new chat sessions, including tool flags and council settings
- limited chat tools for workspace search and Python execution, with per-conversation workspace boundaries
- a chat-first agent loop with provider-native tool use, plan-first product behavior, resumable question states, and durable run trace artifacts inside per-conversation workspaces
- durable generated app hosting through a backend app registry, cockpit app-management pages under `/workspace/apps`, and real runtime routes under `/apps/[slug]`
- chat-resident generated-app bootstrap, conversation-owned lease semantics, and app-scoped write-root expansion for generated app work
- provider-aware prompt caching, prompt telemetry, transcript compaction, cold-cache microcompaction, raw visible-output artifacts, and provider-exposed reasoning capture across both chat and agent flows
- a phone-first `/jarvis` PWA with GPT-Live WebRTC, provisional transcript streaming, durable daily logs and focus, first-class tasks and reminders, natural task references, local wake/address detection, and optional Web Push delivery
- an Android 17/API 37 companion that can hold the assistant role, invoke a native Compose session, run the same local wake model, stream Live audio and durable events, and host the phone UI in a constrained WebView

## Required App-Builder Direction

The required product direction for generated apps is a chat-resident agent loop:

1. the user starts an Agent chat
2. the user asks for a new app or an update to an existing app
3. the agent acquires or refreshes the app lease directly from the chat flow
4. the agent bootstraps or reattaches the generated app contract
5. the agent reads and writes files inside the app write roots from the same chat session
6. the agent returns to the user in that same chat thread when the work is complete

This remains the primary control plane for interactive generated-app work. Delegation from chat creates a first-class global task directly; the retired `/voice` page permanently redirects to `/jarvis`. A separate `python -m app.worker` process claims and executes global tasks. FastAPI records only the causal delegation event and task card, not a child conversation run.

The product is split into a FastAPI backend, a Next.js frontend/PWA, an optional native Android client, a local SQLite database for durable state and text indexes, and a file-backed approved-knowledge directory.

### Guarded task and background execution

The opt-in Windows/API task route now has serialized per-task execution runs,
a homogeneous supervised pool (up to eight busy workers), isolated pinned
Codex hosts, brokered action receipts and confirmed process-tree termination.
The legacy route remains the default. Native continuation and portable
task/run context are durable; unknown effects cannot be blindly replayed.

`TASK_ORCHESTRATION_ENABLED` selects one code-only unified-event feedback owner
and an independent single-flight decision loop. The loop uses an isolated
restricted Codex/MCP profile, never a worker slot or active voice session.
Common task services commit action receipts and task mutations together under
current-state/generation guards. Accepted instructions and original evidence
are bounded/retrievable; stale actions fail closed. Existing canonical attention
records distinguish communicated updates from answered worker questions.
Unjudged question/failure items stay pending but are hidden from proactive
delivery until retained through a backend receipt; flag rollback preserves
their identities. Factual completions forward without model approval.

`/api/orchestration` exposes queue/runtime/action/forwarding timing, pending
age, receipt counts and actual native token usage. Metadata metrics do not
generate feedback events. Deferred native outcomes can be inspected read-only
and explicitly resumed after verified host exit/history reconciliation; there
is no periodic AI health check or automatic paid retry of an uncertain turn.
Background live execution remains default-off and budget-gated. The guarded
API/native and Android emulator acceptance does not enable subscription use,
establish physical/audio timing, or verify the account's enforced cutoff.
See the [M3 implementation and evidence](jarvis-tasks/08-background-orchestration.md#13-implementation-progress--2026-09-30)
for the current scope and agent-runnable tests.

The guarded route also implements M4's reusable workflow platform. Versioned
definitions declare independent context, capability and output contracts;
enabled packages supply catalog/routing descriptions without changing the voice
gateway. Trusted result registrations serve document and minimal recipe views
through one allocation/revision/delivery lifecycle. Shared MCP capabilities
provide retained images/history, durable clarification/finish, fenced publication
and batched research. Research journals separately billed Responses discovery
and bounded public source reads with DNS/redirect/decoded-size safeguards.
Follow-ups reuse task context and sources and edit the same URL with an expected
base revision; failed updates preserve the last good result. MCP stdio explicitly
uses UTF-8 on Windows. `recipe_from_screen@1` and `research_screen@1` are thin
reference packages, not custom integrations. Actual provider and API37 acceptance
is recorded in [M4 evidence](jarvis-tasks/reports/m4-acceptance.md).
`TASK_RESEARCH_ENABLED` and exact workflow/digest allowlisting remain default-off;
M5 is in progress: unified catalog/current collections, shared saved state,
bounded optimistic JSON storage with revocable HTTP capabilities, document
references/retained text attachments, durable result SSE and a production static
result shell have autonomous coverage. Browser copy/print and offline cursor
recovery pass. General agents can opt into Documents at admission; their final
Markdown uses the shared fenced publication transaction with atomic notification
intent. Run-owned new/update/answer associations support multiple same-task
results, fixed-base edits and non-mutating questions. Task/Jarvis inspection and
the task UI expose these choices. General and workflow workers recognize committed
attempt outcomes during cleanup rather than reporting false claim loss.
Generated apps remain trusted same-origin code; storage capability scope is not
an untrusted-code sandbox. Focused deployment login-next and result/collection/task
backlinks now pass the password-backed production browser journey. The export
reuses maintained screens with document navigation for future-ID aliases;
generated-app management stays in the full Next frontend. Personal access uses explicit configuration and signed owner/device sessions;
optional private plaintext passwords are compared in constant time.
Metadata-only result timing, publication outcomes, SSE emission and client
milestone/delivery receipts now have durable authenticated inspection and
structured logs. Short best-effort observations do not participate in task
authority. Actual API process restart preserves results, saved state and login
cookies while disabled workflows remain blocked. Updated API37 acceptance now
passes with the production shell: direct activity notification actions, paired-device
open receipts (subscription management stays cookie-only), real password login/return,
persisted WebView cookies, system-bar-safe backlinks and external browser navigation.
The combined backend/frontend/Android regression checkpoint passes. Broader physical,
provider/account and explicit production opt-in gates remain deferred; see
[M5 progress](jarvis-tasks/reports/m5-progress.md).

The repository CLI now supports read-only `rollout-check`, disposable configured-DB
`migration-rehearsal` and isolated Windows `rollout-rehearsal`. Online SQLite
snapshots include WAL data, refuse existing destinations and validate integrity/FKs.
The actual restored API preserves result/saved/history/auth state. Reports are
redacted and never grant enablement; production data/configuration is not migrated
or changed. The [operational runbook](jarvis-tasks/rollout-runbook.md) records full
backup, confirmed writer stop and progressive rollout boundaries.

```text
Browser / Phone PWA ----> Next.js frontend ----> FastAPI backend
                                                   |       |
Android assistant ---------------------------------+       +-> GPT-Live
                                                   |       +-> llama.cpp / OpenRouter
                                                   +-> SQLite + FTS5
                                                   +-> workspace folders
                                                   +-> approved knowledge files
                                                   +-> optional Web Push
```

## Runtime Topology

### Frontend

- Runs as a Next.js app during development on port 3000.
- Uses cookie-authenticated fetch requests to the backend.
- Maintains client-side UI state for the active conversation, transient streams, independent Jarvis cursors, wake inference, and generated-app management surfaces.
- Uses a `100dvh` phone layout with safe-area-aware bottom navigation and task/activity sheets while preserving the multi-column desktop workspace.

### Backend

- Runs as a FastAPI application on port 8000.
- Initializes the database during app startup and marks any previously running conversation runs as interrupted.
- Serves API routes for auth, chat, conversations, knowledge, apps, Jarvis continuity, reminders, recall, memory proposals, and paired devices.
- Can serve a static frontend export when `frontend/out` exists.

### Android

- Targets Android 17/API 37 with Kotlin and Compose.
- Implements the assistant role through `VoiceInteractionService`, a separate session service, a separate recognition service, and a Compose session overlay.
- Uses Android Keystore for the paired credential, reports lock state, maintains a resumable SSE cursor, and presents descriptive task/reminder notifications with Open, Stop, and Sleep actions.
- Runs the shared four-model ONNX wake pipeline locally through a user-started microphone foreground service and falls back to manual/system assistant invocation when wake listening is unavailable.
- Never persists ambient audio. Assist structure is transient and opt-in; screenshot capture is disabled.

### Persistence and Files

- Durable conversational state lives in SQLite through SQLAlchemy async and Alembic-managed schema.
- Per-conversation generated files live under the configured conversation workspace root.
- Authoritative generated app source lives under `frontend/generated-apps/<slug>`, public assets live under `frontend/public/apps/<slug>`, and the registry synchronizes healthy source trees into `frontend/app/apps/<slug>` for Next.js routing.
- Verifier scenarios exercise the same generated-app builder path but clean any transient `verifier-*` scaffold and asset roots after each run so the route tree stays reserved for durable surfaces.
- Approved memory is written into markdown files under `knowledge/notes/` or structured entries in `knowledge/preferences.yaml`.
- User-visible conversations, task facts, and approved memory are projected transactionally into SQLite recall documents with `unicode61` and trigram FTS5 indexes.

## Backend Architecture

### Entry Point and Lifecycle

The backend entrypoint is `backend/app/main.py`.

On startup it:

1. ensures the database exists and migrations are applied
2. initializes the async SQLAlchemy engine
3. marks unfinished conversation runs as `interrupted` while leaving worker-owned global tasks untouched
4. retries queued first-class task Web Push deliveries
5. starts the independent reminder scheduler, which leases due waits and performs idempotent overdue delivery

It then registers these active routers:

- `auth` for login, logout, and auth status
- `chat` for compatibility chat endpoints, model discovery, and chat settings
- `conversations` for durable transcript, event, artifact, branch, tool, and memory operations
- `knowledge` for review queue and approved knowledge APIs
- `apps` for the generated app registry and runtime app metadata
- `jarvis` for the current daily log, provider-neutral controller sessions, independent conversation/session replay, fake-provider injection, and policy-driven presentation timing/delivery
- `tasks` for global dispatch, status, ordered event replay, attempts, questions, targets, artifacts, cancellation, and retry
- `notifications` for authenticated VAPID subscriptions and event-linked delivery acknowledgements
- `retrieval` for bounded, parsed `rg`-shaped history queries without shell execution
- `memory` for evidence-backed reviewed memory proposals
- `devices` for short-lived Android pairing codes, scoped device credentials, lock state, privacy state, event cursors, and revocation

The separate worker process registers through `worker_instances`, heartbeats independently of FastAPI, claims the strict FIFO head, acquires the complete sorted mutable target set, and executes through `AgentExecutionContext` plus `AgentExecutionSink`. Attempt checkpoints, task events, questions, results, and terminal state remain in SQLite, so an API restart has no ownership effect.

The FastAPI process owns live Jarvis coordination but not background execution. `VoiceSessionAdapter` is the provider boundary; the deterministic fake adapter and the GPT-Live sideband adapter emit the same normalized coordinator events. The coordinator enforces one mutable controller generation across browser and Android, rejects stale callbacks, persists playback/transcript history for diagnostics, exposes only the restricted brain allowlist, and binds short transport leases and durable attention exposure tokens to a session generation. The conversation observer is the sole voice handling authority: it records which results and reminders the user actually heard (see `docs/jarvis-tasks/conversation-bookkeeping.md`); OpenAI credentials and provider call IDs remain backend-only.

### Configuration

Runtime settings live in `backend/app/config.py` and are loaded from the repository `.env` file.

Key configuration areas:

- OpenRouter API endpoint and key
- prompt caching strategy, cache TTL, runtime retry/backoff, and explicit cache breakpoints
- session auth secret and password hash
- council model list and synthesizer model
- chat settings defaults for new conversations
- SQLite database URL and Alembic config path
- conversation workspace root path
- conversation compaction, microcompaction, cold-cache pruning, and heavy-content guardrails
- allowed directories for future file tooling
- Tailscale host and notification settings
- local LLM loopback URL/model alias, GPT-Live settings, browser wake-model URL, and VAPID Web Push credentials
- knowledge notes and preferences paths

### API Surface

#### `backend/app/api/auth.py`

Handles signed owner and device sessions. Explicit personal mode with no configured password bootstraps an owner session on the private host; password mode requires the private plaintext password. Native pairing exchanges a revocable bearer credential for a device-scoped cookie, never an owner cookie. Browser writes require an allowed origin; device capture capabilities require native bearer authentication.

#### `backend/app/api/chat.py`

Acts as the compatibility-facing chat endpoint.

- Agent-mode compatibility chat resolves through the same conversation-aware orchestration and agent runtime used by the conversation-first APIs
- council mode runs multiple models in parallel and returns a synthesized result
- both paths are conversation-aware and persist work through the conversation store
- chat settings can be read and updated for future conversations

This endpoint remains for compatibility, but the main product flow is now conversation-first.

#### `backend/app/api/conversations.py`

Provides the durable conversation APIs.

Implemented responsibilities:

- list conversations with All, Standard, Jarvis, and archived-only projections
- create an empty conversation or create one by starting a run immediately from an initial message
- fetch transcript-focused conversation detail, including branch metadata and workspace state
- fetch raw events and artifacts for a conversation
- append a new message to an existing conversation and execute either an Agent-mode or Council turn
- archive and unarchive conversations
- branch by editing and resending an earlier user message
- execute limited chat tools within the active conversation workspace
- create proposed memory items for a conversation span
- approve or reject memory items and write approved items into the durable knowledge directory

#### `backend/app/api/apps.py`

Provides the generated app registry APIs.

Implemented responsibilities:

- list generated apps
- create generated apps directly through the registry
- fetch generated apps by id or slug
- update app status, verification state, provenance, manifest data, and error state

#### `backend/app/api/knowledge.py`

Provides the review-first knowledge APIs.

Implemented responsibilities:

- list proposed, approved, rejected, or deleted reviewed memory items
- list approved knowledge documents from the file-backed knowledge layer
- approve or reject memory items independently of the conversation detail page
- delete approved knowledge and tombstone the backing memory item metadata

#### `backend/app/api/notifications.py`

The notification API stores browser subscriptions and transport history. First-class task notifications are written to `notification_outbox` in the same transaction as terminal task states or blocking questions, deduplicated by task-event ID, and delivered with exact `/tasks/{task_id}` or `/tasks/{task_id}?question={question_id}` links. Ordinary progress never enters this outbox. Opening a notification handles the exact task attention row; push transport remains history and disabled or missing VAPID configuration never causes a cloud fallback.

#### `backend/app/api/jarvis.py`

Provides current Jarvis state, preferences, reminders, typed turns, daily-log lifecycle, Live session/call control, resumable live events, natural task resolution, and provider-neutral test controls. Mutable paired-device calls pass through the server's locked-device capability policy.

#### `backend/app/api/retrieval.py`, `memory.py`, and `devices.py`

- `retrieval` accepts a bounded `rg [OPTIONS] PATTERN [PATH...]` argument string, parses it internally, and returns structured matches from virtual conversation, task, and memory paths.
- `memory` exposes generated proposals with source evidence and review transitions; only approved entries enter authoritative memory.
- `devices` issues one-use five-minute pairing codes, returns a credential plus the current live cursor, scopes state updates to the paired device, and supports administrative revocation.

### Service Layer

#### `backend/app/services/llm.py`

Owns the shared OpenRouter transport and prompt-shaping runtime used by both chat and agent flows.

Implemented responsibilities:

- provider-aware prompt cache policy resolution for Anthropic-style explicit caching and implicit prefix-stability providers such as OpenAI and DeepSeek
- prompt segment rendering with stable cache-candidate segments
- prompt metrics generation, including cached-token usage, stable-prefix fingerprints, cache-break detection, retry counts, and cold-cache detection
- session-level prompt cache state tracking used by higher-level orchestrators for time-based microcompaction decisions
- retry/backoff for retryable HTTP and transport failures
- non-streaming, structured, and streaming chat completion helpers
- streaming native-decision capture for all configured native-tool models, including incremental assistant-text deltas and streamed tool-call assembly
- extraction of provider-exposed `reasoning` and `reasoning_details` fields from non-streaming and streaming OpenRouter responses when the provider returns them
- streamed reasoning-delta capture for agent turns so hidden reasoning can be persisted and projected separately from user-facing visible text

#### `backend/app/services/chat_orchestrator.py`

Owns the conversation-aware execution flow above the LLM transport layer.

Implemented responsibilities:

- creates a conversation when a turn starts without an existing conversation id
- starts a run for each user turn
- writes the accepted user message into both the event log and transcript projection
- stores a prompt snapshot artifact for the full message context
- builds stable prompt segments and records prompt metrics artifacts for every model request
- compacts long conversation history into durable summary artifacts and reuses those summaries on later turns
- applies more aggressive cold-cache microcompaction when session cache state indicates that prompt reuse is likely expired
- persists streaming chunks as events for Agent mode
- relays transient assistant text, reasoning, and tool-lifecycle updates during active agent runs so the frontend can update in place before durable reloads catch up
- projects durable assistant text, thought rows, and tool activity into the ordered timeline while keeping a separate live status surface for the active run
- persists per-model events and artifacts for council branches
- persists synthesized output as the final assistant transcript message
- carries branch metadata and parent event references through resend flows
- marks runs as completed or failed

This is the main orchestration seam for compatibility chat requests and council execution. Interactive multi-step agent work continues inside the shared `agent_runner` runtime.

#### `backend/app/services/conversation_store.py`

Provides the high-level write-path persistence API used by the orchestrator, agent runner, and conversation endpoints.

Implemented responsibilities:

- create and fetch conversations
- ensure default session metadata, workspace state, and a main branch exist for older conversations
- list and create conversation branches
- archive and unarchive conversations
- start runs and update run status
- append immutable events
- project user and assistant messages into transcript rows
- attach artifacts
- batch agent tool lifecycle writes so `agent.tool.called`, tool artifacts, `agent.tool.completed`, and due run-metadata checkpoints can persist together
- expose low-level list and fetch operations consumed by the read-model and memory-review seams
- create, list, fetch, approve, reject, and delete memory items
- mark abandoned running work as interrupted on startup

This service wraps the repository layer and uses a mix of narrow write paths and batched write paths. The notable hot-path optimization is the agent tool lifecycle batch, which reduces per-tool write amplification without changing the durable event and artifact model.

#### `backend/app/services/conversation_read_model.py`

Provides the branch-aware read projection for transcript, event, artifact, and summary presentation.

Implemented responsibilities:

- assemble branch-visible transcript rows from stored lineage
- assemble branch-visible events and artifacts
- present conversation summaries and detail payloads through the presenter layer
- keep branch filtering aligned across messages, events, and artifacts

This is the main read seam for conversation detail APIs and runtime consumers that need branch-aware transcript facts.

#### `backend/app/services/memory_review_store.py`

Provides the review-oriented memory operations separately from the main runtime write path.

Implemented responsibilities:

- create proposed memory items
- list conversation-scoped and global reviewed memory items
- approve, reject, and delete memory items

This seam keeps knowledge-review operations out of the agent and conversation lifecycle orchestration paths.

#### `backend/app/services/conversation_compaction.py`

Provides shared conversation-compaction helpers reused by both the chat orchestrator and the agent runner.

Implemented responsibilities:

- microcompact older tool-heavy transcript content
- apply shared truncation rules for compaction summaries and guardrails
- keep cold-cache and normal compaction behavior aligned across chat and agent flows

#### `backend/app/services/conversation_workspace.py`

Provides the per-conversation workspace helpers.

Implemented responsibilities:

- create the workspace directory for a conversation
- normalize stored workspace paths for frontend display
- resolve safe workspace-relative file listings for the chat UI

#### `backend/app/services/chat_tools.py`

Implements the limited tool layer available during normal chat turns.

Current tools:

- workspace search over allowed roots
- Python execution with reads from allowed roots and writes constrained to the active conversation workspace

The broader agent tool surface now lives under `backend/app/services/agent_tools/`, including generated-app bootstrap and app-aware execution tools used by the chat-first agent runtime.

#### `backend/app/services/app_registry.py`

Implements the durable generated app registry.

Current behavior:

- persists generated app metadata, contract paths, verification state, manifest JSON, and provenance
- maps each app to a real runtime route under `/apps/<slug>`
- treats `lease_conversation_id` as the authoritative active owner for interactive chat editing while keeping `lease_task_run_id` as provenance or active-holder detail
- exposes explicit write roots for frontend code and public assets

#### `backend/app/services/app_builder.py`

Implements the generated app scaffold and app contract helpers.

Current behavior:

- creates or reuses a generated app record
- scaffolds `layout.tsx`, `page.tsx`, `loading.tsx`, `error.tsx`, `styles.css`, `cockpit-app.json`, and README contract files
- prepares generated-app metadata so the chat agent can attach to an app and stay inside explicit write boundaries

#### `backend/app/services/agent_runner.py`

Implements the durable single-agent execution loop used directly by chat turns.

Implemented responsibilities:

- drives a transcript-based native tool loop over the shared agent tool registry
- preserves the product's plan-first behavior through native plan/finalize/ask-user tool semantics rather than a bespoke decision-envelope hot path
- pauses for plan feedback by default, with an explicit skip-feedback path at task creation time
- compacts older task history into durable summary artifacts for later decisions
- records prompt metrics, raw `llm.response.visible_output` artifacts, streamed assistant text and reasoning, tool events, plan artifacts, and run-summary artifacts into the conversation store
- batches tool-call durability and only checkpoints active-run metadata on forced lifecycle boundaries, every 10 tool steps, or after 5 seconds of active execution time
- prefers provider-exposed reasoning for transcript thought rows when available, and falls back to visible assistant text only when no reasoning blocks were returned
- emits deterministic tool-lifecycle descriptions such as `Reading…` → `Read…`, using a stable activity id so the frontend updates one row instead of appending replacements
- treats assistant text as optional on tool-call turns, persists model-provided visible text when present, and does not invoke a secondary progress-summary model
- instructs the model not to narrate routine tool calls; user-facing text before completion is reserved for material findings, corrections, or blockers
- restores deferred tool-step history and step counters from persisted events before resuming a run, so resume does not depend on every live progress change having been checkpointed
- enforces workspace boundaries through the shared tool context

#### `backend/app/presenters/`

Provides response mapping for conversation, app, and knowledge APIs.

Implemented responsibilities:

- convert conversation records into summary, detail, event, artifact, branch, and memory DTOs
- convert generated app records into API-facing view models
- convert reviewed knowledge records into API-facing view models

This presenter layer keeps route modules thin and avoids repeating record-to-response mapping across endpoints.

#### `backend/app/services/knowledge_memory.py`

Implements reviewed knowledge promotion and deletion.

Current behavior:

- approved note-style items are written under `knowledge/notes/`
- approved preference-style items are written into `knowledge/preferences.yaml`
- approved knowledge can be deleted later, with the file-backed entry removed and DB provenance retained through tombstoned metadata

This is the current bridge between conversation persistence and the future knowledge layer.

#### `backend/app/services/brain_retrieval.py` and `backend/app/services/agent_tools/retrieval.py`

Provide the brain-facing durable-memory read boundary.

Current behavior:

- browses recent approved file-backed Wiki documents or matches exact/substring queries, returning excerpts and stored provenance
- browses or searches standard chats and closed Jarvis daily logs, including archived history while excluding the current/open Jarvis log
- exposes `wiki_search` and `conversation_search` as read-only Agent tools
- retains those broad synthesis tools for delegated work while the Live voice session uses bounded `history_grep` for narrow direct lookups over the unified recall projection
- leaves all Wiki writes in the existing proposal/review workflow

#### `backend/app/services/task_notifications.py`

Owns durable first-class task Web Push delivery.

Current behavior:

- enqueues only terminal task events and blocking questions in the task transaction
- deduplicates each notification intent by its durable task-event ID
- claims, sends, retries, and recovers queued/failed outbox rows across API restarts
- removes stale browser subscriptions and preserves queued intent when no active subscription remains
- treats push as transport-only: opening a notification handles the exact linked
  `TaskPresentation`, while delivered transport rows remain historical

#### Jarvis v2 continuity services

- `jarvis_live.py` merges conversation, task, and voice-session events into a monotonic resumable SSE stream used by the browser and Android.
- `jarvis_preferences.py` persists voice, concision, wake behavior, developer visibility, and Android privacy options; voice changes apply to the next Live session.
- `reminder_scheduler.py` models reminders as waiting tasks, leases due work independently of the task worker, handles overdue startup, and deduplicates due transitions.
- `natural_task_resolver.py` maintains focused/recent task continuity and resolves recency, status, title, and subject references before any mutation. Ambiguous mutations stop for descriptive clarification.
- `history_retrieval.py` parses the constrained `rg` grammar, searches the FTS recall projection, applies virtual paths/context/sort/limits, never invokes a shell, and provides the idempotent recall rebuild path.
- `knowledge_extractor.py` and `memory_review_store.py` generate and retain evidence-linked reviewed-memory candidates from closed Jarvis logs and completed tasks.
- `jarvis_devices.py` owns code pairing, hashed/revocable device credentials, browser-or-device authentication, and locked-device capability enforcement.

## Persistence Model

SQLAlchemy models live in `backend/app/db/tables.py`.

### Core Tables

#### `conversations`

One row per user-visible thread.

Important fields:

- `id`
- `title`
- `mode_hint`
- `session_metadata_json`
- `workspace_path`
- `created_at`
- `updated_at`
- `archived_at`

#### `conversation_branches`

Stores durable branch lineage for resend-based conversation splits.

Important fields:

- `conversation_id`
- `branch_key`
- `parent_branch_key`
- `source_message_id`
- `source_event_id`
- `created_at`

#### `conversation_runs`

One execution unit within a conversation.

Examples:

- an Agent-mode assistant turn
- a council turn
- a linked task run

Important fields:

- `kind`
- `status`
- `parent_run_id`
- `metadata_json`

#### `conversation_events`

Append-only trace log for conversational and task activity.

Examples of current event types:

- `conversation.user_message.accepted`
- `conversation.assistant.stream.started`
- `conversation.assistant.stream.chunk`
- `conversation.assistant.message.completed`
- `council.model.started`
- `council.model.completed`
- `council.synthesis.started`
- `council.synthesis.completed`
- `agent.run.started`
- `agent.plan.created`
- `agent.plan.feedback.skipped`
- `agent.thought.summary`
- `agent.text.output`
- `agent.tool.called`
- `agent.tool.completed`
- `agent.question.asked`
- `agent.question.answered`
- `agent.run.resumed`
- `agent.run.completed`
- `llm.request.completed`
- `context.compacted`
- `run.interrupted`
- `run.failed`

#### `conversation_messages`

Transcript projection for normal user and final assistant messages. Ordered reasoning, visible model text, plans, questions, and tool activity are stored as durable timeline items so the frontend does not need to reconstruct the current UI from the raw event log.

#### `conversation_artifacts`

Stores larger or more structured data attached to the trace.

Current examples:

- prompt snapshots
- prompt metrics
- conversation compaction summaries
- per-model council responses
- synthesis prompt
- synthesis response
- agent plans
- agent run summaries
- agent tool outputs

#### `generated_apps`

Stores durable generated app metadata.

Important fields:

- `slug`
- `title`
- `status`
- `verification_status`
- `route_path`
- `frontend_root`
- `frontend_entry_path`
- `source_task_run_id`
- `source_conversation_id`
- `manifest_json`
- `last_error`

#### `memory_items`

Stores proposed and approved reviewed memory extracted from conversations.

Current statuses:

- `proposed`
- `approved`
- `rejected`
- `deleted`

Additional metadata tracked on approved or deleted items includes:

- `knowledge_path`
- `deleted_at`

#### Jarvis, task, recall, and device tables

- `jarvis_state` stores the current log, state revision, focused internal task, recent task-reference stack, and live cursor.
- `jarvis_preferences` stores voice, concision, wake behavior, developer visibility, and Android privacy JSON.
- `jarvis_live_events` is the global monotonic replay stream projected from conversation, task, and voice-session domains.
- `tasks` stores waiting/resume timestamps, wait reason, scheduled-delivery metadata, exactly-once delivery state, and normal durable task fields; attempts, events, questions, artifacts, targets, and presentation reservations remain normalized child tables.
- `recall_documents` stores bounded user-visible text and virtual paths. SQLite-managed `unicode61` and trigram FTS5 tables index this projection without exposing hidden reasoning, secrets, or raw provider payloads.
- `jarvis_devices` stores hashed/revocable Android credentials, lock/privacy state, and resumable event cursor; `jarvis_pairing_codes` stores hashed one-use codes and expiry/consumption timestamps.
- `task_presentations` is the sole durable attention authority (`pending`, `handled`, `superseded`); the conversation observer and opened notifications handle one exact row, while transcript/playback and `notification_outbox` remain diagnostics or transport history.
- `notification_outbox` stores durable browser notification transport intent and outcome separately from attention state.

### Persistence Design Notes

- The event log is the richest trace surface.
- Transcript messages are a read-optimized projection for chat UX.
- Artifacts hold larger structured or textual payloads that do not fit cleanly in transcript rows.
- Run metadata is now a checkpointed projection of live runtime state rather than an every-step source of truth for agent execution.
- Agent resume can reconstruct deferred tool-step state from persisted events, which lets the runtime checkpoint less aggressively without losing durable continuity.
- Memory promotion is manual and review-driven.
- Internal task IDs remain relational keys and diagnostic/URL values; user-facing Jarvis text, task references, clarifications, and native notifications use descriptive labels.
- SQLite is the canonical local durable store today.

### Attention authority and notification races

`task_presentations` is the only authority for whether Jarvis still owes an update. A valid, session-bound model self-report or an opened notification conditionally changes one row from `pending` to `handled`; task execution state, speech transcripts, playback completion, append acknowledgements, and notification transport state never perform that transition. Every successful transition writes a global `jarvis_live_events` `attention.handled` audit in the same transaction and stores that global event ID in `handled_event_id`. Exposure tokens remain voice-session event IDs because they prove which session generation received the checklist item.

The live model and delegated Responses model receive the same bounded, ordered checklist. Session startup includes the initial page, later semantic changes refresh the complete page and append individual deltas, and an explicit empty snapshot removes stale context. Transport leases, retry counters, timestamps, and outbox status are excluded from the semantic fingerprint. Routine changes use quiet thinking context; due reminders, blocking questions, and high-importance terminal results get one commentary attempt. A reconnect never repeats an attempted commentary item and instead restores it as quiet context.

Notification handling supersedes only `queued` and `failed` transport work. A `sending` row belongs to the sender until it records the real result: a successful in-flight push remains `delivered`, while an unsuccessful one becomes `superseded` if attention closed and otherwise becomes `failed`. Startup recovery is different because no old sender remains: closed `sending` rows become `superseded`, and open ones return to `queued`. While voice is connected, push delivery waits until 30 seconds after initial queueing so the conversation observer can record that the user heard it; after a session ends it waits a further 15 seconds for the observer's last assessment.

The accepted limitation is deliberate: a valid model self-report can be premature or absent. A premature report is trusted, while a missing report leaves attention pending and push-eligible. Transcript matching and a second verifier are intentionally not used.

For the disposable development database after changing migration 0021, stop the API and worker, remove only `backend/data/ai_cockpit_jarvis.db` plus its optional `-wal` and `-shm` companions, then restart the backend (or run `uv run alembic upgrade head` from `backend`). Do not apply that reset procedure to a database whose history must be retained.

## Frontend Architecture

### App Structure

The frontend uses the Next.js app router.

Current route groups:

- `frontend/app/login` for authentication
- `frontend/app/apps` for synchronized generated app routes and the metadata/error fallback
- `frontend/app/(app)/chat` for the main chat surface
- `frontend/app/(app)/workspace/apps` for cockpit app management and app detail pages
- `frontend/app/(app)/tasks` for the global task list and full task trace; the old background routes redirect here
- `frontend/app/(app)/settings` for model/task defaults, server-persisted Jarvis preferences, Android pairing, browser Push, and local wake calibration
- `frontend/app/(app)/wiki` for the review queue, approved Wiki search, and file-backed document browser; `/knowledge` permanently redirects here

Shared layout and chrome live under `frontend/app/layout.tsx`, `frontend/app/(app)/layout.tsx`, and shared components.

### API Client and Hooks

The browser integration boundary is `frontend/lib/api.ts`.

It currently provides typed functions for:

- auth requests
- chat streaming and council execution
- conversation listing, transcript/event retrieval, archive controls, branching, and tool execution
- global task listing, dispatch, detail, cancellation, retry, and question answers
- generated app registry reads and writes
- knowledge review and approved document browsing
- model discovery
- Jarvis state, preferences, reminders, natural task resolution, live-event URLs, memory proposals, history recall, Live calls, and Android pairing codes

`frontend/lib/hooks.ts` contains the public composed hooks:

- `useAuth` for session status
- `useChat` as a composition layer over narrower helpers for conversation-list ownership, detail loading, polling, transient stream state, and action handlers

Supporting chat-state modules now include:

- `frontend/lib/use-conversation-list.ts`
- `frontend/lib/use-conversation-detail.ts`
- `frontend/lib/use-conversation-polling.ts`
- `frontend/lib/use-conversation-actions.ts`
- `frontend/lib/chat-history.ts` for durable persisted timeline projection
- `frontend/lib/agent-stream-state.ts` for the transient live overlay path
- `frontend/lib/chat-state-types.ts` for explicit UI state models

### Chat UI Flow

The main chat UI lives in `frontend/app/(app)/chat/page.tsx`.

Current behavior:

- loads recent conversations from the backend
- loads transcript, branch metadata, workspace state, and artifacts for the selected conversation
- starts new conversations implicitly when the user sends a message without an active conversation
- starts each new draft in Agent mode and exposes only a minimal Agent/Council choice before the first turn
- supports Agent-mode streaming through the existing single-model backend path
- supports council mode with synthesized output and model metadata
- keeps detailed model defaults in the dedicated settings page instead of the new-chat surface
- renders a dedicated live agent-status surface for progress updates while keeping thought rows, tool rows, and final answers in the transcript
- composes shared `ConversationListPanel` and `ConversationDetailsPanel` components for mobile and desktop detail surfaces
- routes transcript rendering through a thin `ChatMessage` dispatcher with dedicated renderer modules for user, assistant, council, and timeline rows
- shows explicit All, Standard, Jarvis, and Archived filters across desktop/mobile
- renders archived conversations read-only until restored and keeps closed Jarvis logs permanently read-only
- follows exact conversation deep links, including archived history, and exposes related-task links on task-backed assistant messages
- exposes the limited tool surface and the active conversation workspace file list
- offers recent conversation navigation in desktop sidebar and mobile chips

Artifacts and durable transcript events are mapped into UI-ready message data via `frontend/lib/chat-history.ts`, while transient agent status lives outside the transcript projection.

### Task UI Flow

The task pages live under `frontend/app/(app)/tasks`; `/background` is now a compatibility redirect.

Current behavior:

- lists global tasks independently from conversations and accepts direct task dispatches
- exposes queue position, worker health, attempts, targets, questions, artifacts, results, cancellation, and explicit retry
- renders the ordered task event stream through the reusable Agent trace components
- projects the global task identity directly in conversation task cards without creating compatibility child runs
- accepts exact notification links to the task or blocking question, acknowledges the opened notification, and links back to the originating conversation

### Jarvis Live UI Flow

The live workspace lives at `frontend/app/(app)/jarvis` and uses the common conversation/task contracts rather than the legacy `/voice` projection.

Current behavior:

- lazily creates a daily log on the first accepted typed turn and supports either the deterministic fake provider or an armed GPT-Live controller
- lets the browser own microphone permission, WebRTC offer/answer, remote audio playback, data-channel establishment, bounded reconnect, and typed fallback
- proxies unified-call SDP through the authenticated backend and binds a backend-only sideband socket to the provider call
- renders provisional assistant transcript deltas immediately, reconciles them with durable completed/interrupted speech, and includes provisional content in newest-message scrolling
- opens existing logs at their newest message and follows new user or assistant messages automatically
- owns the installable PWA start route and notification deep links; `/voice` is now a permanent compatibility redirect
- uses a phone-first conversation surface with safe-area bottom navigation, compact expandable status, and badge-bearing task/activity sheets while retaining the desktop workspace
- replays one authenticated global live stream with a resumable cursor while preserving domain-specific conversation/session replay where diagnostics require it
- exposes controller take-over and observer semantics, explicit sleep/fresh-log controls, and stale-generation rejection
- records completed, interrupted, and unheard speech deliveries as diagnostics; transcript events never settle task attention
- exposes a restricted shell gateway for natural task lookup/actions, reminders, bounded `history_grep`, voice selection, fresh-log, and session-end capabilities
- coalesces typed task-fact envelopes, preserves questions, keeps pending attention authoritative, and accepts one session-bound model self-report after an update is communicated
- hides the deterministic developer conversation lab unless developer mode is enabled
- runs the exact-word wake model off the main thread, associates wake scores with recent speech instead of same-frame VAD, accepts one strong or two low-confidence hits, prepares browser WebRTC locally while armed, and holds the first backend response for a deliberately permissive transcript-stage check

### Generated Apps UI Flow

The apps surfaces are split intentionally:

- `frontend/app/(app)/workspace/apps` is the cockpit management UI
- `frontend/generated-apps/<slug>` is the authoritative editable app source
- `frontend/app/apps/<slug>` is the synchronized Next.js runtime route
- `frontend/app/apps/[slug]` is the metadata/error fallback when no healthy synchronized route exists

Current behavior:

- the cockpit apps page lists generated apps, their status, verification state, and route path
- the cockpit app detail page shows contract paths, provenance, write roots, and manifest data
- registry reads and updates synchronize healthy generated source into its exact live route without pruning unrelated apps
- verifier runs clean up any transient `verifier-*` app scaffold after the scenario completes instead of leaving scratch routes behind
- interactive edits to generated apps are tracked through conversation runs and app lease metadata rather than a separate task-first control plane

### Wiki UI State

The canonical `/wiki` page exposes the full review-first knowledge workflow; `/knowledge` is a permanent compatibility redirect.

Current behavior:

- lists reviewed memory items by status
- allows approval and rejection from the review queue
- shows approved knowledge entries that were written into `knowledge/`
- allows approved knowledge items to be deleted while tombstoning the backing DB row
- provides a direct browser for the file-backed knowledge documents
- searches approved items and documents locally and links provenance back to the source conversation

## Current Request and Execution Flows

### Single-Model Chat

1. The user sends a message from the chat page.
2. The frontend creates a conversation or posts to `/api/conversations/{conversation_id}/messages`, while `/api/chat` remains available as a compatibility path.
3. The orchestrator creates or reuses a conversation, starts a run, writes the user message, and stores a prompt snapshot artifact.
4. OpenRouter streaming chunks are relayed back to the frontend and also recorded as conversation events.
5. The final assistant message is persisted as a transcript message and the run is marked completed.

### Council Chat

1. The user sends a message in council mode.
2. The frontend continues the active conversation through the conversation-first API, while the compatibility chat endpoint still supports the same council execution shape.
3. The orchestrator persists the turn and fans the prompt out to all configured council models in parallel.
4. Each model response is persisted as an event and artifact.
5. A synthesizer model receives a generated synthesis prompt.
6. The synthesized answer is persisted as the final assistant transcript message.

### Branch Resend

1. The user selects an earlier user message and edits it.
2. The frontend posts to `/api/conversations/{conversation_id}/branches/resend`.
3. The conversation store creates a new branch linked back to the source message and parent branch.
4. The orchestrator executes the resent turn on that branch while the original transcript remains intact.
5. The frontend can switch between the main branch and derived branches when loading the transcript.

### Limited Tool Execution

1. The user runs a tool from the chat surface.
2. The frontend posts to `/api/conversations/{conversation_id}/tools`.
3. The backend writes tool start and completion events into the durable conversation trace.
4. Workspace search reads across allowed roots, while Python execution can only write inside the active conversation workspace.
5. Tool outputs are stored as transcript-visible assistant messages so the result survives reloads.

### Chat-Started App Editing Flow

1. The user asks for app creation or app edits in the main chat flow.
2. The agent runtime acquires or refreshes the generated app lease through the app registry using the active conversation context.
3. The backend bootstraps or attaches the generated app, scaffolds the app contract if needed, and stores the app context in the active agent run metadata.
4. The generated app is available in the registry, in `/workspace/apps`, and at `/apps/<slug>` immediately.
5. The active agent run carries explicit write roots for the generated app so later edits stay inside the app boundary.
6. The agent loop persists plan events, thought summaries, tool events, question pauses, compaction summaries, prompt metrics, and final run summaries into the conversation trace.
7. After a backend restart, previously running runs are marked interrupted and can be resumed manually because the durable run metadata, events, and artifacts remain in SQLite.

### Reviewed Memory Approval

1. Proposed memory items are created against a conversation.
2. Approval updates the memory item state.
3. The approved item is written into `knowledge/notes/` or `knowledge/preferences.yaml`, depending on memory scope.
4. Provenance back to the source conversation is preserved in frontmatter and in the database row.
5. Later deletion removes the file-backed approved knowledge entry and tombstones the backing memory row instead of erasing provenance.

## Codebase Map

### Backend

- `backend/app/main.py`: app startup, router registration, static frontend serving
- `backend/app/config.py`: environment-backed settings and path helpers
- `backend/app/api/`: route definitions
- `backend/app/models/`: Pydantic request and response models
- `backend/app/services/`: orchestration, auth, LLM access, agent tools, generated apps, conversation read/write seams, and knowledge bridge
- `backend/app/presenters/`: API response mapping for conversation, apps, and knowledge
- `backend/app/db/`: SQLAlchemy base, tables, sessions, repositories
- `backend/alembic/`: schema migrations
- `backend/tests/`: backend coverage for persistence, conversations, generated apps, compaction, runtime behavior, and reviewed memory

### Frontend

- `frontend/app/`: route entrypoints and layouts
- `frontend/components/`: chat, sidebar, app-management, and shared presentation components
- `frontend/components/chat/`: shared chat page panels and formatting helpers
- `frontend/components/chat-message/`: dedicated per-kind chat renderers
- `frontend/lib/api.ts`: typed backend client
- `frontend/lib/hooks.ts`: public auth and composed chat hooks
- `frontend/lib/use-conversation-*.ts`: narrower chat state ownership hooks
- `frontend/lib/chat-history.ts`: durable persisted timeline projection
- `frontend/lib/agent-stream-state.ts`: transient live overlay state
- `frontend/lib/jarvis-live-state.ts`: independent Jarvis domain cursors and merged display-only activity
- `frontend/lib/jarvis-wake-runtime.ts`: one-microphone browser capture, AudioWorklet resampling, and dedicated-worker lifecycle
- `frontend/lib/jarvis-wake-engine.ts`: bounded pre-roll, wake/command collection, and raw-audio destruction rules
- `frontend/lib/jarvis-live.ts`: armed-state WebRTC preparation, wake one-shot-to-live handoff, reconnect, and Live session controls
- `frontend/lib/jarvis-address-detection.ts`: recall-first wake verification with explicit incidental-mention rejection
- `frontend/lib/jarvis-calibration-store.ts`: browser-local calibration sample lifecycle

### Android

- `android/app/src/main/java/.../assistant/`: assistant, session, recognition, and Compose overlay services
- `android/app/src/main/java/.../live/`: native GPT-Live WebRTC offer/answer and microphone controller
- `android/app/src/main/java/.../wake/`: shared ONNX inference pipeline, temporal recall-first gate, permissive address check, foreground listener, and reboot opt-in
- `android/app/src/main/java/.../notifications/`: resumable SSE consumer and native actions
- `android/app/src/main/java/.../data/`: paired API client and Keystore-backed credential store
- `android/app/src/main/java/.../web/`: constrained same-origin WebView host

### Knowledge and Documentation

- `knowledge/preferences.yaml`: structured preferences store
- `knowledge/notes/`: approved memory notes and future knowledge corpus
- `docs/ARCHITECTURE.md`: current-state architecture reference
- `docs/JARVIS_TASK_ARCHITECTURE.md`: target modular-task architecture, performance contract, and focused plan index
- `docs/JARVIS_GAPS.md`: active user-visible acceptance gaps
- `docs/JARVIS_WAKE_MODEL.md`: wake-model provenance, training commands, evaluation, and calibration gates

## Validation Surface

Current backend test coverage lives under `backend/tests/` and includes:

- conversation store behavior
- conversation API behavior
- conversation compaction behavior
- generated app registry behavior and explicit write-root enforcement
- chat-started app bootstrap behavior inside the chat-first agent runtime
- agent runtime and reviewed memory behavior
- workspace and limited-tool boundaries
- slashless collection route coverage for the frontend rewrite path
- provider-independent Jarvis controller, delivery, session-generation, brain-gateway, presentation, rollover, and fake-provider scenarios
- preferences, reminders, natural task references, recall grammar/index rebuild, reviewed memory proposals, resumable live events, device pairing, and locked-device policy

The frontend also includes API, chat-history, hook, stream-state, provisional Jarvis transcript, direct-address, wake lifecycle, mobile viewport, and component tests, while production `next build` validates the route tree and TypeScript surfaces. The Android project includes a pure Kotlin address-gate test and a verified API 37 debug compile; emulator, battery, keyguard, and gesture checks still require an Android 17 device.

## Current Gaps and Intentional Incompleteness

These are not accidental omissions; they are the main boundaries of the current implementation.

- The default legacy v1 worker is single-active; the separately opt-in guarded Codex route has the supervised pool/recovery boundary described above. Neither route blindly retries uncertain effects.
- The current durable agent runtime is intentionally single-agent and does not yet include subagents, MCP integration, or plugin loading.
- `/api/chat` still acts as a compatibility-facing entrypoint even though persistence is conversation-first underneath.
- Run state is durably restored after restart, but active execution is not automatically resumed; interrupted runs need an explicit resume.
- Generated apps can now be created and hosted durably, but browser-driven verification evidence and automatic repair loops are still future work.
- Browser and Android source paths now cover Live media, sideband transport, controller handoff, streamed transcripts, reminders, natural task references, indexed recall, reviewed memory, paired notifications, and local wake/address gating. The API 37 debug build passes; Android release acceptance still requires physical-device checks for assistant selection, keyguard, screen-off listening, process death, network loss, and battery drain.
- The selected exact-word model remains less consistent at low volume and its prior automated false-activation result does not meet the stricter v2 target. Calibrated quiet/distant/noisy/hard-negative recordings, GPU retraining, and held-out real-device address evaluation remain release gates rather than claimed passes.

## Architectural Direction

The codebase now has one production path across durable conversations, global tasks and reminders, the separate worker, the provider-neutral Jarvis coordinator, GPT-Live/WebRTC, heard and provisional transcript truth, proactive presentation, deterministic local wake/address logic, indexed local recall, reviewed memory, durable notifications, paired Android control, and archive lifecycle. Retired provider-specific and batch local-speech paths are no longer part of the maintained documentation set; Git history retains them.

This direction intentionally avoids a vector database requirement. Recall is local, bounded, inspectable SQLite text search; authoritative memory remains review-driven and file-backed.

This document should be updated whenever the underlying architecture changes materially.
