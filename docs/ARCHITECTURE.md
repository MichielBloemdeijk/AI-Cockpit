# Architecture

The current shape of AI Cockpit / Jarvis: which processes run, which modules
own what, and where to read further. It describes what exists today. Why the
project exists and how decisions are weighed is in [VISION.md](VISION.md);
finished milestones and assessments live in [history/](history) and
[jarvis-tasks/reports/](jarvis-tasks/reports) (the previous version of this
file is [architecture-before-2026-11.md](jarvis-tasks/reports/architecture-before-2026-11.md)).

## Reading map

| To change | Read |
|---|---|
| Anything significant | [VISION.md](VISION.md), [AGENTS.md](../AGENTS.md) |
| How to check a change | [VERIFICATION.md](VERIFICATION.md) |
| Tasks: lifecycle, queue, workers | [JARVIS_TASK_ARCHITECTURE.md](JARVIS_TASK_ARCHITECTURE.md), [01-control-plane](jarvis-tasks/01-control-plane.md) |
| Voice routing and admission | [02-live-routing](jarvis-tasks/02-live-routing.md), [conversation-bookkeeping](jarvis-tasks/conversation-bookkeeping.md) |
| Screen context from Android | [03-android-context](jarvis-tasks/03-android-context.md), [12-screen-in-router-context](jarvis-tasks/12-screen-in-router-context.md) |
| Workflows | [04-workflow-runtime](jarvis-tasks/04-workflow-runtime.md), [06-recipe-workflow](jarvis-tasks/06-recipe-workflow.md) |
| Apps and task results | [13-app-platform](jarvis-tasks/13-app-platform.md) |
| Background decisions | [08-background-orchestration](jarvis-tasks/08-background-orchestration.md) |
| Worker tools and permissions | [09-worker-tool-policy](jarvis-tasks/09-worker-tool-policy.md) |
| Android audio | [11-android-audio-controls](jarvis-tasks/11-android-audio-controls.md), [JARVIS_WAKE_MODEL.md](JARVIS_WAKE_MODEL.md) |
| Running the personal host | [rollout-runbook](jarvis-tasks/rollout-runbook.md), [phone guide](jarvis-tasks/phone-test-checklist.md) |
| Self-tests | [self-testing](jarvis-tasks/self-testing.md) |

The feature docs above start with an **Owners** list (code, tests, harnesses).
Phase documents such as `01`–`04` and `08` keep their original contracts and
acceptance notes; where they differ from the code, the code and this file win.
Superseded designs and finished plans are in [history/](history/README.md).

## Processes

```text
Browser / phone PWA ──> Next.js (3000) ──/api proxy──> FastAPI (8000) ──> SQLite: core DB + apps.db
Android companion ──────────────────────────────────────┘    │
                                                              ├─> GPT-Live (voice, WebRTC + sideband)
                                                              ├─> OpenRouter / local OpenAI-compatible server (chat)
                                                              ├─> task pool: Codex worker processes (tasks, app functions)
                                                              └─> knowledge/ files, Web Push
```

- **API** (`backend/app/main.py`): FastAPI app. Its lifespan migrates the
  database, then starts and later stops (in reverse) the reminder scheduler,
  Jarvis coordinator, conversation observer, runtime health, the app runtime,
  the run supervisor and, when enabled, background orchestration and the task
  pool. With a built frontend it also serves it (`frontend/out`).
- **Task pool** (`services/tasks/pool_supervisor`, `services/tasks/worker`, `app/worker.py`): with
  `TASK_POOL_ENABLED=true` the API supervises a pool of worker processes, each
  contained in a Windows job. `python -m app.worker` only runs a pool child or a
  standalone supervisor. Without the pool no tasks execute.
- **Frontend** (`frontend/`): Next.js app; `npm run dev` for development, a
  production build for the personal host. `frontend/result-export` is a
  separate static build of the result screens.
- **Android** (`android/`): Kotlin/Compose companion that can hold the assistant
  role (API 37).
- **Personal host** (`app/jarvis_tasks/personal_host.py`, started by
  `Jarvis.cmd`): one guarded host that starts backend and frontend on loopback
  behind Tailscale Serve, checks the session secret, listener ownership and
  Tailscale, and refuses a second instance.

Shared configuration preconditions run in `Settings` (`app/config.py`) for
every launch path; the personal host adds its host-specific checks.

## Backend ownership

The layers, top to bottom: `app/api/` routes translate HTTP and check auth;
`app/models/` holds the request and response shapes and `app/presenters/` turns
records into those views (for example the Retry flag on a task summary);
`app/services/` owns behavior and transactions; `app/db/` holds the tables and
sessions. Routes never write the database directly, and clients (web, Android)
only see presenter output.

| Area | Owners (in `app/services/` unless noted) |
|---|---|
| Configuration | `app/config.py`: settings from the root `.env` (or `AI_COCKPIT_ENV_FILE`); every relative path resolves against `backend/` via `backend_path` |
| Persistence | `app/db/`: ORM tables (`tables.py`, grouped by domain), sessions, `run_migrations` (the one start-up entry point) and the migration lock (`session.py`), Alembic baseline in `alembic/`. `app/timestamps.py` has the one `utc_now` |
| Auth and devices | `auth`, `api/auth.py`, `api/devices.py`, `jarvis/devices`: owner and device sessions, phone pairing |
| Chat and council | The `services/conversations/` package: `chat_orchestrator` (single-model streaming completion, council fan-out and synthesis, one run owner per turn), `store` (the durable conversation record; `end_run` is the only way a conversation run ends, guarded on `running`), `run_supervisor`, `timeline` (every committed message, chat or Jarvis, is a `message` timeline item; clients render only those), `read_model`, `compaction`, `titles`, `workspace`, `chat_settings`. Outside it: `llm` (OpenRouter and local transport: one retry/error policy for both transports, provider profiles for prompt caching) |
| Reviewed memory | `memory_review_store` (approve/reject/delete together with publication), `knowledge_memory` (notes under `knowledge/notes/`), `knowledge_extractor`, `brain_retrieval`, `history_retrieval` |
| Jarvis voice | The `services/jarvis/` package: `coordinator` (one controller generation per session, response gate; each provider event is one transaction that records named after-commit effects, then those effects run in a fixed order), `openai_live_adapter` behind `voice_session_adapter`, `brain` (restricted tool gateway), `tool_catalog`, `observer` (records what the user actually heard), `daily` (the daily log and text turns), `situation`, `wake` (the wake-word gate), `clock` (reminder timing), `notes`, `preferences`, `task_resolver`, `screen_look`, `phone_display` and the live helpers (`live` for the event replay stream, `admission_intents`, `request_context`, `task_updates`, `traffic`, `live_telemetry`, `model_diagnostics`, `tone`) |
| Tasks | The `services/tasks/` package: `vocabulary` (task, run and control vocabularies) and `lifecycle` (every task/run/attempt transition, the task lock and the worker fence; nothing else writes a task or run status); `service` (creation, control, questions, completion and result publication, calling the lifecycle), `worker_coordinator` (claims, leases, worker exits), `reminder_scheduler`, `pool_supervisor`, `context`, `context_upload`, `capture`, `update_policy` and the other task-domain modules. Outside it: `updates/` (below); worker side `tasks/worker`, `agent_execution` (the `TaskRuntime` protocol with its context and sink), `codex_task_runtime`, `codex_native` (the only code that knows the pinned Codex SDK: version pin, client config, raw App Server calls, the fail-closed sandbox gate; runtimes and probes all use it), `app/jarvis_tasks/task_mcp.py` (the MCP server a worker uses) |
| Workflows | `tasks/workflows` (versioned definitions in `backend/workflows/` and app packages), `tasks/capture`, `tasks/context` |
| Results and apps | `app/app_platform/` (packages, records, files, functions, hosting, lifecycle; install, rollback, status and reset go through Jarvis's tools, not HTTP), the `services/results/` package (`records`, `types`, `contract`: run-owned output intent); built-in apps in `backend/apps/` |
| Background orchestration | The `services/orchestration/` package: `service` (feedback intake and single-flight decisions), `runtime` (the restricted Codex host), `attention`, `authority`, `context`, `recovery`, `telemetry`, `visibility` (off unless `TASK_ORCHESTRATION_ENABLED`) |
| Worker tools | `tasks/worker_tool_policy`, `tasks/project_tools`, `connector_permissions`, `native_research` |
| Task updates | The `services/updates/` package: `presentations` (task facts coalesced and presented once per live generation), `notifications` (Web Push outbox), `notification_preferences`, `push_subscriptions`. Outside it: `commit_signals` (after-commit hints; durable rescans are the authority) |
| Diagnostics | `diagnostics`, `runtime_health`, `jarvis/live_telemetry`, `jarvis/traffic` (observer hook used by the self-test recorder) |

Verification-only code lives apart: `app/selftest/` (Jarvis self-tests) and
`app/jarvis_tasks/` (acceptance gates, rehearsals, probes, the personal host).

## Persistence

- **Core database** `backend/data/ai_cockpit_jarvis.db` (SQLite, WAL). The
  schema is one Alembic baseline generated from the ORM;
  `tests/test_schema_drift.py` keeps them equal. Raw SQLite pieces the ORM
  does not model (the FTS5 recall index and the triggers that feed it and the
  Jarvis live-event log) are SQL in that baseline. Startup migrates under a
  file lock, so API and workers can start together.
- **Apps database** `backend/data/apps.db`: app records, runs and schedules.
  Packages live in `backend/data/apps/<slug>/<version>/`, files in
  `backend/data/app-files/<slug>/`.
- **Files**: conversation workspaces (`.cockpit/conversations`), task
  workspaces and context blobs (`backend/data/`), approved knowledge
  (`knowledge/notes/`, `knowledge/preferences.yaml`).

Development data is disposable (see [AGENTS.md](../AGENTS.md)).

## Frontend

`frontend/app/(app)/` holds the signed-in routes: `chat`, `jarvis` (the voice
workspace and phone UI), `tasks` (and task detail), `knowledge` (review queue;
`wiki` shows the same page), `settings` (including wake calibration),
`workspace/apps` (the app list). `background` and `voice` only redirect.
`app/apps/[slug]` (an installed app in a sandboxed frame) sits outside the
signed-in group with its own layout; `app/login` and `app/link-phone` are
public. Route files are thin adapters: the task, task-list, apps and login
screens live in `components/screens/` and take ordinary props, so the static
companion (`result-export/`) renders the same screens with its own adapters
(the task ID from the path, a page load after sign-in, no app host).

`frontend/lib/` holds the logic, grouped like the backend:
- `api/`: the backend client by domain (`auth`, `conversations`, `jarvis`,
  `tasks`, `knowledge`, `push`) over one finite-request transport (`http.ts`:
  a deadline on every request, the caller's abort kept apart from a timeout,
  failures as `ApiError` with status and backend detail). Streams (chat, SSE)
  keep their own long-lived handling.
- `jarvis/`: `feed.ts` (state projection, domain replay, the durable live
  stream and provisional transcripts), `voice-session.ts` (who owns voice: the
  browser's wake and Live transport, or Android, which the page only observes),
  `use-jarvis.ts` composing both for the route, and the browser Live, wake and
  transcript modules.
- `conversations/`: chat feed (committed messages from the server timeline plus the one locally streamed reply), hooks and selection.
- `app-platform.ts` (the app host bridge), `task-status.ts`, `navigation.ts`.

## Android

`android/app/src/main/java/nl/sander/aicockpit/jarvis/`: `assistant/` (the
assistant role and session overlay; each show is one sealed mode: context
refresh, phone capture, shared-Live capture or conversation), `audio/` (the
foreground service that owns wake recording and native WebRTC; everyone else
reads `JarvisAudioControls.state`, including the call identity, and sends
commands), `wake/` (local ONNX wake inference),
`live/`, `data/` (API client and live event stream), `notifications/`,
`web/` (the WebView phone UI), `update/` (self-update) and `appfunctions/`.
Acceptance harness code (test audio input, playback recording, relay, evidence
logs) lives in `src/debug` behind `live/AcceptanceHooks`, and the capture
fixtures (armed invocations bound to a harness's fake session) behind
`assistant/AssistantFixtures`; release builds keep the no-op hooks. See [android/README.md](../android/README.md).

## How the parts fit together

**A voice request becomes a task.** The browser (`lib/jarvis/voice-session.ts`)
or Android (`audio/`) detects the wake word locally and opens a call with
`POST /api/jarvis/live/calls` (`api/jarvis.py`). `jarvis/coordinator` starts one
controller generation and connects GPT-Live through `openai_live_adapter`
(audio goes over WebRTC, tools over the sideband socket). Each provider event is
one transaction in `coordinator.handle_provider_event`. A tool call reaches
`jarvis/brain`, which checks the call and admits the task with
`admission_intents` and `tasks/service.dispatch`, which is durable and
idempotent. Playback reports from the client feed `jarvis/observer`, which
decides what the user actually heard.

**A task runs and reports back.** `tasks/service` creates the task;
`tasks/worker_coordinator.claim_next` hands it to a worker process
(`tasks/worker`, `codex_task_runtime`). The worker uses the MCP server in
`jarvis_tasks/task_mcp.py` to ask questions and publish a result.
`tasks/lifecycle` applies every state change under the task lock. After commit,
`updates/presentations` turns task events into facts to present once per live
generation; `jarvis/task_updates` phrases them for the voice, and
`updates/notifications` sends a Web Push for a fact the voice did not deliver
within its grace period.

**A result becomes something the user opens.** A workflow publishes a record
into an installed app (`results/contract` decides the request, `results/records`
writes it to `apps.db`). The task summary carries a link
`/apps/<slug>?collection=&key=`, which the web UI (`TaskDetailScreen`,
`app/apps/[slug]`), push notifications and the phone all open.

**A chat message.** `api/chat.py` and `api/conversations.py` call
`conversations/chat_orchestrator`, which streams the completion (or runs the
council) under one run owner and records each message as a timeline item through
`conversations/store`; `read_model` projects them for `lib/conversations/`.

## Invariants worth knowing before a change

- Task, run and attempt identities are durable and fenced by generation; task
  transitions take the task write lock.
- Result publication is revisioned with replay fingerprints; notifications after
  commit are hints, and durable rescans recover anything missed. The
  notification outbox is durable.
- Provider calls stay outside database transactions.
- Voice code talks to the provider only through the `VoiceSessionAdapter`
  boundary; the coordinator owns all durable effects.
- Capture identity, expiry and lock checks gate every screen capture.
- Background orchestration acts only through a fenced `DecisionAuthority`.
- Background work is tracked: no fire-and-forget tasks.
- Native audio releases a resource before replacing it and stops before the
  network closes.
- The personal host's loopback, Tailscale and ownership checks stay in place.

How the current structure came about is in the
[refactor roadmap](audits/repo-maintainability/refactor-roadmap.md).
