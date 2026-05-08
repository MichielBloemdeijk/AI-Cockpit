# AI Cockpit

A locally-hosted personal AI command center. Use a configurable LLM council from the web UI, keep durable conversation history, review promoted knowledge, and grow toward background agent sessions that can build and verify local tools while staying reachable from your phone via Tailscale.

## Related Docs

- [PLAN.md](PLAN.md) for the forward-looking roadmap
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the current implemented architecture and codebase layout

```
Android Phone (PWA)  ──HTTPS──▶  FastAPI Backend  ◀──▶  OpenRouter (LLMs)
                                  Next.js Frontend
```

---

## Quick Start

### Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.11+ | [python.org](https://python.org) |
| Node.js | 20+ | [nodejs.org](https://nodejs.org) |
| uv | latest | `pip install uv` |

### 1 — Clone & configure

```powershell
git clone <repo-url>
cd AI-Cockpit
copy .env.example .env
```

Open `.env` and set your **OpenRouter API key**:

```env
OPENROUTER_API_KEY=sk-or-v1-your-key-here
```

Get a free key at [openrouter.ai](https://openrouter.ai). All other defaults work out of the box for local development.

Optional: if you want to seed local reviewed preferences manually, copy the example file:

```powershell
copy knowledge\preferences.example.yaml knowledge\preferences.yaml
```

If you skip that step, the app can create `knowledge/preferences.yaml` automatically later when approved preferences are written from the UI.

### 2 — Install dependencies

**Backend** (from repo root):
```powershell
cd backend
uv venv .venv
uv pip install -e ".[dev]"
cd ..
```

**Frontend:**
```powershell
cd frontend
npm install
cd ..
```

### 3 — Run

**Option A — VS Code (recommended)**

Open the repo in VS Code and use the built-in tasks:

- `Ctrl+Shift+P` → **Tasks: Run Task** → **Start Backend**
- `Ctrl+Shift+P` → **Tasks: Run Task** → **Start Frontend**

Or use **Tasks: Run Task** → **Start All** to launch both at once in split terminals.

**Option B — PowerShell scripts**

```powershell
# Terminal 1
.\start-backend.ps1

# Terminal 2
.\start-frontend.ps1
```

**Option C — Manual**

```powershell
# Terminal 1 — backend
cd backend
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 — frontend
cd frontend
npm run dev
```

### 4 — Open

- **Browser**: [http://localhost:3000](http://localhost:3000)
- **API docs**: [http://localhost:8000/docs](http://localhost:8000/docs)

Sign in with any password (auth is in dev mode when `AUTH_PASSWORD_HASH` is empty in `.env`).

---

## Project Structure

```
AI-Cockpit/
├── backend/                  # FastAPI Python backend
│   ├── app/
│   │   ├── main.py           # App entry point, router registration
│   │   ├── config.py         # Settings from .env
│   │   ├── api/
│   │   │   ├── auth.py       # Login / logout / session
│   │   │   ├── apps.py       # Generated app registry APIs
│   │   │   ├── chat.py       # Compatibility chat + chat settings
│   │   │   ├── conversations.py # Conversation-first chat, branches, tools, memory
│   │   │   └── knowledge.py  # Knowledge review + approved knowledge APIs
│   │   ├── db/               # SQLAlchemy tables, sessions, repositories
│   │   ├── models/
│   │   │   ├── chat.py       # Chat request/response types
│   │   │   ├── conversations.py # Durable conversation DTOs
│   │   │   ├── knowledge.py  # Knowledge and memory DTOs
│   │   │   └── apps.py       # Generated app DTOs
│   │   └── services/
│   │       ├── app_builder.py # Generated app scaffolding + app contract helpers
│   │       ├── app_registry.py # Durable generated app registry
│   │       ├── agent_runner.py # Planning-first agent loop
│   │       ├── llm.py        # OpenRouter client, council logic
│   │       ├── chat_orchestrator.py # Conversation-aware chat execution
│   │       ├── chat_settings.py # Chat defaults resolution and persistence
│   │       ├── chat_tools.py # Workspace search + Python execution
│   │       ├── conversation_store.py # Durable conversation writes and reads
│   │       ├── conversation_workspace.py # Per-conversation workspaces
│   │       ├── knowledge_extractor.py # Proposed memory extraction from conversations
│   │       ├── knowledge_memory.py # File-backed knowledge promotion/deletion
│   │       └── auth.py       # Password verify, session tokens
│   └── pyproject.toml
│
├── frontend/                 # Next.js 16 + Tailwind frontend
│   ├── app/
│   │   ├── (app)/            # Authenticated layout
│   │   │   ├── chat/         # Chat page
│   │   │   ├── background/   # Placeholder route
│   │   │   ├── workspace/apps/ # Cockpit generated-app management
│   │   │   ├── knowledge/    # Knowledge review + approved docs
│   │   │   └── settings/     # Settings page
│   │   ├── apps/             # Runtime generated app routes (/apps/[slug])
│   │   └── login/            # Login page
│   ├── components/           # Reusable UI components
│   └── lib/
│       ├── api.ts            # Typed API client
│       └── hooks.ts          # useChat, useAuth hooks
│
├── knowledge/                # File-backed approved knowledge + local preferences
│   ├── notes/
│   ├── preferences.example.yaml
│   └── preferences.yaml      # Local-only, optional, gitignored
│
├── .env                      # Local secrets (not committed)
├── .env.example              # Template — copy to .env
├── start-backend.ps1         # Quick-start script
└── start-frontend.ps1        # Quick-start script
```

---

## Configuration (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` | *(required)* | Your OpenRouter API key |
| `AUTH_PASSWORD_HASH` | *(empty = dev mode)* | bcrypt hash of your password |
| `SESSION_SECRET_KEY` | *(auto-generated)* | Signs session cookies |
| `COUNCIL_MODELS` | `claude-sonnet-4-5, gemini-pro-1.5, gpt-4o` | Comma-separated OpenRouter model IDs |
| `SYNTHESIZER_MODEL` | `claude-sonnet-4-5` | Model that synthesizes council responses |
| `ALLOWED_DIRS` | *(empty)* | Directories the file browser can access |
| `NTFY_TOPIC` | *(empty)* | ntfy.sh topic for push notifications |
| `TAILSCALE_HOST` | *(empty)* | Optional Tailscale hostname or IP used for remote dev-host allowance and backend CORS |

### Enabling password auth

```powershell
cd backend
uv run python -c "from passlib.hash import bcrypt; print(bcrypt.hash('yourpassword'))"
```

Paste the output into `.env` as `AUTH_PASSWORD_HASH=...`.

---

## Features (current)

- **Conversation-first chat** — Durable conversations persist runs, events, transcript messages, and council artifacts across reloads and restarts
- **Agent and council chat modes** — Agent chats now use a full multi-step in-chat agent loop, while council mode fans out to multiple models and stores the synthesized answer plus per-model artifacts
- **Chat-first execution timeline** — New conversations start in Agent mode, can be switched to Council before the first turn, and render tool calls, wait states, thought rows, and final results inline in the main transcript
- **Dedicated live agent status + inline progress** — Agent conversations keep a separate live status surface for what the agent is doing now, while durable thought and progress rows still appear inline in the transcript where they help explain the run history
- **Reasoning-aware agent traces** — Agent-side model requests now persist `llm.response.visible_output` artifacts, including visible content, provider-exposed reasoning when available, streamed visible deltas, and tool-call payloads for later debugging
- **Progress-summary hardening** — The fallback progress-summary path now uses a larger response budget and disables summary-model reasoning so short inline progress rows are less likely to arrive clipped
- **Branching resend flow** — Editing and resending an earlier user turn creates a durable branch instead of overwriting history
- **Archive controls** — Conversations can be archived and restored from the chat UI
- **Per-conversation workspaces** — Each conversation has an isolated workspace folder that chat tools can write into safely
- **Chat tools + app bootstrap** — Workspace search and Python execution remain available in chat, and app work runs directly through the in-chat agent loop with app bootstrap and lease handling
- **Reviewed knowledge workflow** — Conversations can produce proposed memory items that are reviewed, approved into `knowledge/`, and deleted later with tombstoned metadata; local preference approvals can also populate `knowledge/preferences.yaml`
- **Generated app registry** — Apps are tracked durably, exposed in the cockpit at `/workspace/apps`, and hosted as real product routes at `/apps/<slug>`
- **Session auth** — Cookie-based auth with bcrypt password
- **Mobile-first UI + PWA shell** — Responsive layout with Android install support

## UI Overview

- **Chat** — Recent conversations live in the sidebar, new chats offer Agent or Council, and Agent conversations show a dedicated live status card plus inline thought/progress rows and the durable execution timeline.
- **Background** — Background is currently an empty placeholder while the product runs fully chat-first agent execution.
- **Settings** — The settings page now manages future-session defaults for Agent chat, Council, and the default agent loop model without extra roadmap or onboarding panels.

## Generated App Flow

- Ask for app creation or app changes directly in Agent chat.
- Open `/workspace/apps` to browse generated apps, inspect status, and jump to details.
- Open `/apps/<slug>` to view the live generated app surface.
- Generated app code lives under `frontend/app/apps/<slug>`, while app public assets live under `frontend/public/apps/<slug>`.

## Target App Flow

The intended steady-state product flow for generated apps is:

1. Start an Agent chat.
2. Ask the agent to create an app.
3. The agent calls the app bootstrap tool, receives the app directory and scaffold, and keeps working in that same chat.
4. The agent reads and writes files in the generated app directory directly from the in-chat tool loop.
5. The agent returns in the same chat when the change is done.
6. On later turns, the agent fetches or refreshes the app lease if needed, makes further edits, and returns in that same chat again.

Current behavior: app work runs through the same in-chat full agent loop used for Agent mode. The lease is fetched and persisted in agent context, and can be taken over by another actively running agent when the current run is no longer active.

## Roadmap

| Phase | Status | Description |
|-------|--------|-------------|
| **1-4** | ✅ Done | Durable chat, reviewed knowledge, planning-first agent tasks, and runtime-efficiency groundwork |
| **5** | ✅ Substrate shipped | Generated app registry, `/workspace/apps`, real `/apps/<slug>` routes, explicit app write roots, and in-chat app bootstrap |
| **6** | 🔜 Next | Browser automation, screenshots, DOM inspection, and runtime interaction tooling |
| **7+** | 📋 Planned | Deterministic verification, stronger app-builder specialization, and richer productized app management |

See [PLAN.md](PLAN.md) for the full roadmap and verification criteria.

---

## Phone Access (Tailscale)

1. Install [Tailscale](https://tailscale.com) on your PC and phone, then sign both into the same tailnet.
2. On the PC, get the reachable address with `tailscale status` or `tailscale ip -4`.
3. If you want to use a MagicDNS name instead of the raw `100.x.y.z` IP, set `TAILSCALE_HOST=your-machine.tail1234.ts.net` in the repo root `.env`.
4. Start both servers. The backend already listens on `0.0.0.0:8000`, and the frontend dev server already listens on `0.0.0.0:3000`.
5. Open the frontend from your phone at `http://100.x.y.z:3000` or `http://your-machine.tail1234.ts.net:3000`.
6. No router port forwarding is needed as long as both devices stay connected to Tailscale.
7. In Chrome or Safari, install it as a home-screen app if you want the PWA shell.

The backend at port `8000` is still the API and docs endpoint. The browser entrypoint is the frontend on port `3000` unless you later ship a static frontend build behind FastAPI.

For HTTPS (useful for stricter cookie handling and some PWA features), run `tailscale cert your-machine.tail1234.ts.net` and configure uvicorn with the cert.

---

## API Reference

Interactive docs available at **[http://localhost:8000/docs](http://localhost:8000/docs)** when the backend is running.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/auth/login` | POST | Login with password |
| `/api/auth/logout` | POST | Clear session |
| `/api/auth/status` | GET | Check if authenticated |
| `/api/chat` | POST | Compatibility chat endpoint for streaming or council mode |
| `/api/chat/settings` | GET, PUT | Read or update defaults for new conversations |
| `/api/chat/models` | GET | List configured models |
| `/api/conversations` | GET, POST | List conversations or create a conversation |
| `/api/conversations/{id}` | GET | Fetch transcript detail, branches, memory, and workspace info |
| `/api/conversations/{id}/messages` | POST | Append a user message and execute a turn |
| `/api/conversations/{id}/branches/resend` | POST | Create a branch by editing and resending a prior user turn |
| `/api/conversations/{id}/tools` | POST | Run chat tools such as workspace search or Python execution in the active conversation |
| `/api/conversations/{id}/archive` | POST | Archive a conversation |
| `/api/conversations/{id}/unarchive` | POST | Restore an archived conversation |
| `/api/conversations/{id}/events` | GET | Inspect conversation events and artifacts |
| `/api/apps` | GET, POST | List generated apps or create one directly through the registry |
| `/api/apps/{id}` | GET, PATCH | Inspect or update a generated app |
| `/api/apps/slug/{slug}` | GET | Fetch a generated app by route slug |
| `/api/knowledge/review-items` | GET | List proposed and reviewed memory items |
| `/api/knowledge/documents` | GET | Browse approved knowledge documents |
| `/api/knowledge/memory-items/{id}/approve` | POST | Approve a reviewed memory item into `knowledge/` |
| `/api/knowledge/memory-items/{id}` | DELETE | Delete an approved knowledge item and tombstone the DB row |
| `/api/health` | GET | Health check |
