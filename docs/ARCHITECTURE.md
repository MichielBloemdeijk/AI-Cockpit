# Architecture

## Purpose

This document describes the current architecture of AI Cockpit as it exists in the repository today. It is intentionally current-state focused: it explains what is implemented, how the main parts interact, where code lives, and which parts are still intentionally incomplete.

## Related Docs

- [README.md](../README.md) for setup, runtime entrypoints, and the high-level product overview
- [JARVIS_VOICE_POC.md](JARVIS_VOICE_POC.md) for the implemented Jarvis voice prototype contract and acceptance evidence
- [JARVIS_TARGET_ARCHITECTURE.md](JARVIS_TARGET_ARCHITECTURE.md) for the agreed Jarvis end-state architecture
- [JARVIS_EVOLUTION_ARCHITECTURE.md](JARVIS_EVOLUTION_ARCHITECTURE.md) for the high-level POC-to-target assessment
- [JARVIS_IMPLEMENTATION_PLAN.md](JARVIS_IMPLEMENTATION_PLAN.md) for the approved phased Jarvis implementation contract
- [JARVIS_IMPLEMENTATION_LEDGER.md](JARVIS_IMPLEMENTATION_LEDGER.md) for checkpoint evidence and the next-session entry point

## System Overview

AI Cockpit is a locally hosted personal AI workspace built around these active capabilities:

- authenticated chat against provider profiles for the loopback local OpenAI-compatible server or explicit OpenRouter cloud turns
- council mode that fans a prompt out to multiple models and synthesizes the result
- durable conversation history with runs, events, transcript messages, artifacts, branches, archive state, and reviewed memory items
- configurable defaults for new chat sessions, including tool flags and council settings
- limited chat tools for workspace search and Python execution, with per-conversation workspace boundaries
- a chat-first agent loop with provider-native tool use, plan-first product behavior, resumable question states, and durable run trace artifacts inside per-conversation workspaces
- durable generated app hosting through a backend app registry, cockpit app-management pages under `/workspace/apps`, and real runtime routes under `/apps/[slug]`
- chat-resident generated-app bootstrap, conversation-owned lease semantics, and app-scoped write-root expansion for generated app work
- provider-aware prompt caching, prompt telemetry, transcript compaction, cold-cache microcompaction, raw visible-output artifacts, and provider-exposed reasoning capture across both chat and agent flows
- a Jarvis voice PWA with browser VAD, local STT/TTS endpoints, a server-owned primary voice conversation, first-class global tasks with replayable traces, and optional Web Push delivery

## Required App-Builder Direction

The required product direction for generated apps is a chat-resident agent loop:

1. the user starts an Agent chat
2. the user asks for a new app or an update to an existing app
3. the agent acquires or refreshes the app lease directly from the chat flow
4. the agent bootstraps or reattaches the generated app contract
5. the agent reads and writes files inside the app write roots from the same chat session
6. the agent returns to the user in that same chat thread when the work is complete

This remains the primary control plane for interactive generated-app work. Background delegation from chat or the compatibility `/voice` surface creates a first-class global task. A separate `python -m app.worker` process claims and executes global tasks; the temporary child run is retained only as a compatibility projection and never owns execution.

The product is currently split into a FastAPI backend, a Next.js frontend, a local SQLite database for durable conversation state, and a file-backed knowledge directory.

```text
Browser / Phone PWA
        |
        v
Next.js frontend  ->  FastAPI backend  ->  loopback llama.cpp / explicit OpenRouter
                          |
                          +-> SQLite conversation store
                          |
                          +-> per-conversation workspace folders
                          |
                          +-> knowledge/notes markdown files
                          +-> optional VAPID Web Push service
```

## Runtime Topology

### Frontend

- Runs as a Next.js app during development on port 3000.
- Uses cookie-authenticated fetch requests to the backend.
- Maintains client-side UI state for the active conversation, recent conversation list, transient live agent status, and generated-app management surfaces.
- Supports phone-first use through responsive layouts and the existing PWA shell.

### Backend

- Runs as a FastAPI application on port 8000.
- Initializes the database during app startup and marks any previously running conversation runs as interrupted.
- Serves API routes for auth, chat, conversations, knowledge, and apps.
- Can serve a static frontend export when `frontend/out` exists.

### Persistence and Files

- Durable conversational state lives in SQLite through SQLAlchemy async and Alembic-managed schema.
- Per-conversation generated files live under the configured conversation workspace root.
- Authoritative generated app source lives under `frontend/generated-apps/<slug>`, public assets live under `frontend/public/apps/<slug>`, and the registry synchronizes healthy source trees into `frontend/app/apps/<slug>` for Next.js routing.
- Verifier scenarios exercise the same generated-app builder path but clean any transient `verifier-*` scaffold and asset roots after each run so the route tree stays reserved for durable surfaces.
- Approved memory is written into markdown files under `knowledge/notes/` or structured entries in `knowledge/preferences.yaml`.
- Approved knowledge is browsable in-app and readable by chat tools, without a separate retrieval index yet.

## Backend Architecture

### Entry Point and Lifecycle

The backend entrypoint is `backend/app/main.py`.

On startup it:

1. ensures the database exists and migrations are applied
2. initializes the async SQLAlchemy engine
3. marks unfinished interactive and legacy conversation runs as `interrupted` while leaving worker-owned global tasks untouched
4. starts local speech-model warmup and retries queued Web Push deliveries

It then registers these active routers:

- `auth` for login, logout, and auth status
- `chat` for compatibility chat endpoints, model discovery, and chat settings
- `conversations` for durable transcript, event, artifact, branch, tool, and memory operations
- `knowledge` for review queue and approved knowledge APIs
- `apps` for the generated app registry and runtime app metadata
- `voice` for local speech, the primary voice conversation, asynchronous turns, and background-task control
- `jarvis` for the current daily log, provider-neutral controller sessions, independent conversation/session replay, fake-provider injection, and presentation reservation
- `tasks` for global dispatch, status, ordered event replay, attempts, questions, targets, artifacts, cancellation, and retry
- `notifications` for authenticated VAPID subscriptions and event-linked delivery acknowledgements

The separate worker process registers through `worker_instances`, heartbeats independently of FastAPI, claims the strict FIFO head, acquires the complete sorted mutable target set, and executes through `AgentExecutionContext` plus `AgentExecutionSink`. Attempt checkpoints, task events, questions, results, and terminal state remain in SQLite, so an API restart has no ownership effect.

The FastAPI process owns live Jarvis coordination but not background execution. `VoiceSessionAdapter` is the provider boundary; the deterministic fake adapter and the OpenAI Realtime sideband adapter emit the same normalized coordinator events. The coordinator enforces one mutable controller generation, rejects stale callbacks, persists heard delivery truth, exposes only the restricted brain allowlist, and binds task-presentation reservations to a session generation. OpenAI credentials and provider call IDs remain backend-only.

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
- local LLM loopback URL/model alias, voice model paths, VAD/STT/TTS limits, and VAPID Web Push credentials
- knowledge notes and preferences paths

### API Surface

#### `backend/app/api/auth.py`

Handles cookie-based session auth. If `AUTH_PASSWORD_HASH` is empty, the app behaves in development mode and accepts any password.

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

- list conversations
- include archived conversations on demand
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

#### `backend/app/api/voice.py` and `backend/app/api/notifications.py`

The voice API keeps one server-recognizable `mode_hint="voice"` conversation marked as the active primary. Voice turns are accepted as queued runs and owned by `RunSupervisor`; the HTTP request and event-stream observer can disconnect without cancelling work. The event stream replays after a monotonic conversation cursor. Background task completion, failure, cancellation, and restart interruption are persisted as task events plus assistant messages, which lets the frontend speak them in the same conversation.

The notification API stores browser subscriptions and deduplicated deliveries. Push payloads reference the durable event ID and the `/voice` deep link. Delivery is attempted only for requested, important, failed, interrupted, or explicitly tested updates; disabled or missing VAPID configuration never causes a cloud fallback.

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

### Persistence Design Notes

- The event log is the richest trace surface.
- Transcript messages are a read-optimized projection for chat UX.
- Artifacts hold larger structured or textual payloads that do not fit cleanly in transcript rows.
- Run metadata is now a checkpointed projection of live runtime state rather than an every-step source of truth for agent execution.
- Agent resume can reconstruct deferred tool-step state from persisted events, which lets the runtime checkpoint less aggressively without losing durable continuity.
- Memory promotion is manual and review-driven.
- SQLite is the canonical local durable store today.

## Frontend Architecture

### App Structure

The frontend uses the Next.js app router.

Current route groups:

- `frontend/app/login` for authentication
- `frontend/app/apps` for synchronized generated app routes and the metadata/error fallback
- `frontend/app/(app)/chat` for the main chat surface
- `frontend/app/(app)/workspace/apps` for cockpit app management and app detail pages
- `frontend/app/(app)/tasks` for the global task list and full task trace; the old background routes redirect here
- `frontend/app/(app)/settings` for editable model and task-agent defaults for future sessions
- `frontend/app/(app)/knowledge` for the review queue, approved knowledge list, and file-backed document browser

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
- shows archive controls, archived conversation filtering, and branch switching
- exposes the limited tool surface and the active conversation workspace file list
- offers recent conversation navigation in desktop sidebar and mobile chips

Artifacts and durable transcript events are mapped into UI-ready message data via `frontend/lib/chat-history.ts`, while transient agent status lives outside the transcript projection.

### Task UI Flow

The task pages live under `frontend/app/(app)/tasks`; `/background` is now a compatibility redirect.

Current behavior:

- lists global tasks independently from conversations and accepts direct task dispatches
- exposes queue position, worker health, attempts, targets, questions, artifacts, results, cancellation, and explicit retry
- renders the ordered task event stream through the reusable Agent trace components
- keeps legacy child-run identifiers only as execution references while new conversation task cards project the global task identity

### Jarvis Live UI Flow

The live workspace lives at `frontend/app/(app)/jarvis` and uses the common conversation/task contracts rather than the legacy `/voice` projection.

Current behavior:

- lazily creates a daily log on the first accepted typed turn and supports either the deterministic fake provider or an armed OpenAI Realtime controller
- lets the browser own microphone permission, WebRTC offer/answer, remote audio playback, data-channel establishment, bounded reconnect, typed fallback, and 55-minute rotation through normal controller supersession
- proxies unified-call SDP through the authenticated backend and binds a backend-only sideband socket to the provider call
- renders header/controller state, durable transcript, active tasks and questions, pending presentations, worker health, and normalized activity
- replays conversation, global task, and per-session domains with independent cursors; session replacement resets only the session cursor and daily rollover preserves global task activity
- exposes controller take-over and observer semantics, explicit sleep/fresh-log controls, and stale-generation rejection
- records completed, interrupted, and unheard speech deliveries while adding only heard content to the transcript/context projection
- exposes a restricted shell gateway containing only task control, fresh-log, and session-end capabilities
- coalesces task progress, preserves questions, defers presentation while the user owns the floor, and marks facts presented only through a generation-bound delivery
- includes a developer conversation lab for deterministic floor, response, interruption, delegation, presentation, supersession, and provider-error injection

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

### Knowledge UI State

The knowledge page now exposes the full review-first knowledge workflow.

Current behavior:

- lists reviewed memory items by status
- allows approval and rejection from the review queue
- shows approved knowledge entries that were written into `knowledge/`
- allows approved knowledge items to be deleted while tombstoning the backing DB row
- provides a direct browser for the file-backed knowledge documents

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

### Knowledge and Documentation

- `knowledge/preferences.yaml`: structured preferences store
- `knowledge/notes/`: approved memory notes and future knowledge corpus
- `docs/ARCHITECTURE.md`: current-state architecture reference

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

The frontend also includes API, chat-history, hook, stream-state, Jarvis independent-cursor, and chat-component tests, while production `next build` validates the route tree and TypeScript surfaces.

## Current Gaps and Intentional Incompleteness

These are not accidental omissions; they are the main boundaries of the current implementation.

- The v1 worker is intentionally single-active and does not automatically retry interrupted work; stale takeover records interruption and waits for explicit user retry.
- The current durable agent runtime is intentionally single-agent and does not yet include subagents, MCP integration, or plugin loading.
- `/api/chat` still acts as a compatibility-facing entrypoint even though persistence is conversation-first underneath.
- Run state is durably restored after restart, but active execution is not automatically resumed; interrupted runs need an explicit resume.
- Generated apps can now be created and hosted durably, but browser-driven verification evidence and automatic repair loops are still future work.
- OpenAI Realtime/WebRTC media, sideband transport, and browser rotation/reconnect are implemented. Physical-microphone, audible voice, typed-shell, and live reconnect acceptance remain before Phase 6 closes; accurate playback telemetry and local barge-in remain Phase 7 work.

## Architectural Direction

The codebase has crossed the durable conversation, global task, separate worker, provider-neutral Jarvis coordinator, and OpenAI Realtime/WebRTC boundaries without moving provider-specific event shapes into conversation or task services. The next Jarvis step is a short real-device Phase 6 acceptance pass, followed by Phase 7 barge-in and audible-history truth.

For the current POC, this direction intentionally avoids a vector database requirement. The near-term goal is inspectable, file-backed knowledge plus browser-backed verification and cleaner generated-app hygiene, not advanced retrieval infrastructure.

This document should be updated whenever the underlying architecture changes materially.
