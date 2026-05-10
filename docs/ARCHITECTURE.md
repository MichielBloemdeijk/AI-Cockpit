# Architecture

## Purpose

This document describes the current architecture of AI Cockpit as it exists in the repository today. It is intentionally current-state focused: it explains what is implemented, how the main parts interact, where code lives, and which parts are still intentionally incomplete.

## Related Docs

- [README.md](../README.md) for setup, runtime entrypoints, and the high-level product overview
- [PLAN.md](../PLAN.md) for the remaining roadmap and next implementation phases

## System Overview

AI Cockpit is a locally hosted personal AI workspace built around these active capabilities:

- authenticated chat against OpenRouter-backed models
- council mode that fans a prompt out to multiple models and synthesizes the result
- durable conversation history with runs, events, transcript messages, artifacts, branches, archive state, and reviewed memory items
- configurable defaults for new chat sessions, including tool flags and council settings
- limited chat tools for workspace search and Python execution, with per-conversation workspace boundaries
- a chat-first agent loop with provider-native tool use, plan-first product behavior, resumable question states, and durable run trace artifacts inside per-conversation workspaces
- durable generated app hosting through a backend app registry, cockpit app-management pages under `/workspace/apps`, and real runtime routes under `/apps/[slug]`
- chat-resident generated-app bootstrap, conversation-owned lease semantics, and app-scoped write-root expansion for generated app work
- provider-aware prompt caching, prompt telemetry, transcript compaction, cold-cache microcompaction, raw visible-output artifacts, and provider-exposed reasoning capture across both chat and agent flows

## Required App-Builder Direction

The required product direction for generated apps is a chat-resident agent loop:

1. the user starts an Agent chat
2. the user asks for a new app or an update to an existing app
3. the agent acquires or refreshes the app lease directly from the chat flow
4. the agent bootstraps or reattaches the generated app contract
5. the agent reads and writes files inside the app write roots from the same chat session
6. the agent returns to the user in that same chat thread when the work is complete

This is now the primary control plane for interactive generated-app work. Background runs remain intentionally disabled in the current product surface.

The product is currently split into a FastAPI backend, a Next.js frontend, a local SQLite database for durable conversation state, and a file-backed knowledge directory.

```text
Browser / Phone PWA
        |
        v
Next.js frontend  ->  FastAPI backend  ->  OpenRouter
                          |
                          +-> SQLite conversation store
                          |
                          +-> per-conversation workspace folders
                          |
                          +-> knowledge/notes markdown files
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
- Generated app runtime files for durable app surfaces live under `frontend/app/apps/<slug>`, with public assets under `frontend/public/apps/<slug>`.
- Verifier scenarios exercise the same generated-app builder path but clean any transient `verifier-*` scaffold and asset roots after each run so the route tree stays reserved for durable surfaces.
- Approved memory is written into markdown files under `knowledge/notes/` or structured entries in `knowledge/preferences.yaml`.
- Approved knowledge is browsable in-app and readable by chat tools, without a separate retrieval index yet.

## Backend Architecture

### Entry Point and Lifecycle

The backend entrypoint is `backend/app/main.py`.

On startup it:

1. ensures the database exists and migrations are applied
2. initializes the async SQLAlchemy engine
3. marks any `running` conversation runs as `interrupted`

It then registers these active routers:

- `auth` for login, logout, and auth status
- `chat` for compatibility chat endpoints, model discovery, and chat settings
- `conversations` for durable transcript, event, artifact, branch, tool, and memory operations
- `knowledge` for review queue and approved knowledge APIs
- `apps` for the generated app registry and runtime app metadata

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
- relays transient `agent_stream` progress and thought updates during active agent runs so the frontend can update status before durable reloads catch up
- projects durable `agent.thought.summary` and `agent.progress.summary` events back into the transcript while keeping a separate live status surface for the currently active step
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
- expose low-level list and fetch operations consumed by the read-model and memory-review seams
- create, list, fetch, approve, reject, and delete memory items
- mark abandoned running work as interrupted on startup

This service wraps the repository layer and keeps write-path transaction boundaries narrow.

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
- records prompt metrics, raw `llm.response.visible_output` artifacts, streamed thought text, progress summaries, tool events, plan artifacts, and run-summary artifacts into the conversation store
- prefers provider-exposed reasoning for transcript thought rows when available, and falls back to visible assistant text only when no reasoning blocks were returned
- derives short progress summaries separately from transcript thought text so the UI can show status without depending on raw tool-call narration
- falls back to a dedicated summary-model pass when the native tool loop returns no useful visible progress text
- uses a larger progress-summary response budget and explicitly disables summary-model reasoning so fallback progress rows are less likely to be clipped by hidden thinking tokens
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
- `agent.progress.summary`
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

Transcript projection for the normal chat UI. This lets the frontend load the durable transcript without replaying the full event stream. Agent progress summaries stay in the event stream and drive the dedicated live-status surface instead of being projected as normal transcript rows.

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
- Memory promotion is manual and review-driven.
- SQLite is the canonical local durable store today.

## Frontend Architecture

### App Structure

The frontend uses the Next.js app router.

Current route groups:

- `frontend/app/login` for authentication
- `frontend/app/apps` for durable generated app routes
- `frontend/app/(app)/chat` for the main chat surface
- `frontend/app/(app)/workspace/apps` for cockpit app management and app detail pages
- `frontend/app/(app)/background` for the background run dashboard
- `frontend/app/(app)/settings` for editable model and task-agent defaults for future sessions
- `frontend/app/(app)/knowledge` for the review queue, approved knowledge list, and file-backed document browser

Shared layout and chrome live under `frontend/app/layout.tsx`, `frontend/app/(app)/layout.tsx`, and shared components.

### API Client and Hooks

The browser integration boundary is `frontend/lib/api.ts`.

It currently provides typed functions for:

- auth requests
- chat streaming and council execution
- conversation listing, transcript/event retrieval, archive controls, branching, and tool execution
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

### Background UI Flow

The background page is currently a placeholder shell.

Current behavior:

- shows that background runs are intentionally disabled in the current product mode
- directs the user back to the main chat surface for all active agent work
- keeps the route surface available without reintroducing a second active control plane

### Generated Apps UI Flow

The apps surfaces are split intentionally:

- `frontend/app/(app)/workspace/apps` is the cockpit management UI
- `frontend/app/apps/[slug]` is the real generated app runtime surface

Current behavior:

- the cockpit apps page lists generated apps, their status, verification state, and route path
- the cockpit app detail page shows contract paths, provenance, write roots, and manifest data
- the runtime app route renders the scaffolded app page directly from the generated app directory
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

### Knowledge and Planning

- `knowledge/preferences.yaml`: structured preferences store
- `knowledge/notes/`: approved memory notes and future knowledge corpus
- `PLAN.md`: forward-looking product roadmap
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

The frontend also includes API, chat-history, hook, stream-state, and chat-component tests, while production `next build` validates the route tree and TypeScript surfaces.

## Current Gaps and Intentional Incompleteness

These are not accidental omissions; they are the main boundaries of the current implementation.

- The background route remains intentionally disabled while the product stays chat-first for active agent work.
- The current durable agent runtime is intentionally single-agent and does not yet include subagents, MCP integration, or plugin loading.
- `/api/chat` still acts as a compatibility-facing entrypoint even though persistence is conversation-first underneath.
- Run state is durably restored after restart, but active execution is not automatically resumed; interrupted runs need an explicit resume.
- Generated apps can now be created and hosted durably, but browser-driven verification evidence and automatic repair loops are still future work.

## Architectural Direction

The codebase has already crossed the main runtime, persistence, and frontend decomposition boundaries for the current architecture. The next architectural step is not another large chat/runtime rewrite. It is to extend the generated-app substrate into stronger verification and cleaner generated-app storage without introducing a second trace system.

For the current POC, this direction intentionally avoids a vector database requirement. The near-term goal is inspectable, file-backed knowledge plus browser-backed verification and cleaner generated-app hygiene, not advanced retrieval infrastructure.

That direction is reflected in `PLAN.md` and should remain the default assumption for future changes unless the underlying architecture changes materially.