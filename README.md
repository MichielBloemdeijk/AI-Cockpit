# AI Cockpit

AI Cockpit / Jarvis is a personal AI assistant on its own host: a FastAPI
backend, a Next.js web and phone UI, SQLite storage and an optional Android 17
companion that can be the phone's system assistant. Jarvis talks through
GPT-Live, runs durable background tasks on a supervised pool of Codex workers,
keeps reviewed memory, and builds and maintains apps for its owner. Chat also
offers single-model and Council conversations through OpenRouter or a local
OpenAI-compatible server.

Where to read next:

- [docs/VISION.md](docs/VISION.md): why the project exists and how decisions are weighed
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): processes, module ownership and a reading map
- [docs/VERIFICATION.md](docs/VERIFICATION.md): every check, its cost and when to run it
- [AGENTS.md](AGENTS.md): rules for agents working in this repository

There are two ways to run it. The **development setup** works on any machine
and is what you change code against. The **personal host** is the owner's
guarded always-on Windows setup that the phone uses; it needs extra
preparation.

## Development setup

### Prerequisites

| Tool | Version | Notes |
|---|---:|---|
| Git | current | |
| Python | 3.12 | |
| uv | current | `python -m pip install uv` |
| Node.js | 22 LTS (20.19+ works) | |
| Android SDK | API 37 | Only to build the Android companion |

Windows has launcher scripts and VS Code tasks; on macOS/Linux use the manual
commands.

### Configure

```powershell
git clone https://github.com/MichielBloemdeijk/AI-Cockpit.git
cd AI-Cockpit
Copy-Item .env.example .env      # macOS/Linux: cp .env.example .env
```

The copied file starts a passwordless local instance with code defaults. Fill
in what you use:

- `OPENAI_API_KEY` for Jarvis voice (GPT-Live) and API-billed task workers.
- `OPENROUTER_API_KEY` for cloud chat models and Council mode, or a local
  OpenAI-compatible server on `127.0.0.1:8080` (`LOCAL_LLM_*`).
- `TASK_POOL_ENABLED=true` to execute tasks. Workers sign in either with the
  dedicated subscription profile (`CODEX_SUBSCRIPTION_ENABLED=true`, then once
  `uv run python -m app.jarvis_tasks subscription-login` in `backend/`) or with
  `OPENAI_API_KEY`.

`SESSION_SECRET_KEY` in the template is a placeholder: fine on your own
machine, replace it before anyone else can reach the instance. The development
servers listen on all interfaces so other devices on your Tailscale network can
reach them; with the empty password that gives them an owner session.

### Install and start

```powershell
cd backend; uv sync; cd ..
cd frontend; npm ci; cd ..
```

Add `--group notifications` to `uv sync` if you enable Web Push.

Then, in two terminals from the repository root:

```powershell
.\start-backend.ps1
.\start-frontend.ps1
```

macOS/Linux:

```bash
cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
cd frontend && npm run dev
```

The API supervises the task pool itself when `TASK_POOL_ENABLED=true`; no
separate worker terminal is needed. VS Code users can run the `Start All` task.
The backend creates and migrates its SQLite database on startup.

Open [http://localhost:3000](http://localhost:3000) (Jarvis:
[/jarvis](http://localhost:3000/jarvis), API docs:
[localhost:8000/docs](http://localhost:8000/docs)). Check the backend with
`Invoke-RestMethod http://localhost:8000/api/health`.

Before committing, run the routine checks in
[docs/VERIFICATION.md](docs/VERIFICATION.md#routine-checks).

## Personal host

`Jarvis.cmd` (or `scripts\jarvis.ps1 Start|Status|Stop`) starts one guarded
host: backend and production frontend on loopback, the task pool, and phone
access through Tailscale Serve. It refuses to start until these hold:

- a private `SESSION_SECRET_KEY` of at least 32 characters, kept across restarts
  (`python -c "import secrets; print(secrets.token_hex(32))"`);
- `TASK_POOL_ENABLED=true` and a signed-in worker profile (see above);
- a production frontend build (`cd frontend; npm run build`);
- Tailscale running with `tailscale serve` pointing at port 3000, and
  `JARVIS_PUBLIC_URL` set to the resulting HTTPS origin;
- ports 3000 and 8000 free, and no other Jarvis host running.

`uv run python -m app.jarvis_tasks.personal_host check` (in `backend/`) checks
the Tailscale part on its own. The [runbook](docs/jarvis-tasks/rollout-runbook.md)
covers operation and backups; the [phone guide](docs/jarvis-tasks/phone-test-checklist.md)
covers linking the phone. Never replace an existing private `.env` with
`.env.example`.

Settings → Link phone creates a five-minute, one-use link and QR code. The
phone keeps a revocable device credential; passwords and API keys never go into
the link.

### Access modes

- `JARVIS_ACCESS_MODE=personal` with an empty `AUTH_PASSWORD` (default): no
  password form; signed sessions still apply.
- `JARVIS_ACCESS_MODE=password` with `AUTH_PASSWORD=<private password>`: an
  owner password is required. Password mode without a password refuses to start.

## Configuration

`.env.example` documents every setting. The main ones:

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | blank | GPT-Live voice and API-billed workers (backend only) |
| `OPENROUTER_API_KEY` | blank | OpenRouter chat models |
| `AVAILABLE_MODELS` / `COUNCIL_MODELS` / `SYNTHESIZER_MODEL` | see `.env.example` | Chat model pickers and Council mode; check the [OpenRouter catalog](https://openrouter.ai/models) for current IDs |
| `LOCAL_LLM_ENABLED`, `LOCAL_LLM_*` | enabled, `127.0.0.1:8080` | Optional local OpenAI-compatible chat model |
| `TASK_AGENT_MODEL` | `local/gemma-4-12b` | Default chat model for new conversations and titles |
| `TASK_POOL_ENABLED` | false | The supervised worker pool; required to execute tasks |
| `CODEX_SUBSCRIPTION_ENABLED` | false | Workers use the dedicated subscription profile first |
| `TASK_COMMAND_MODE` | native | `trusted` runs worker commands with the Windows user's access (the personal host's choice) |
| `TASK_WORKFLOW_ALLOWLIST` | blank | Core workflows enabled by exact `id@version:digest`; app workflows are enabled by their app |
| `TASK_ORCHESTRATION_ENABLED` | false | Background decisions on task feedback |
| `JARVIS_ACCESS_MODE` / `AUTH_PASSWORD` | personal / blank | Access mode, above |
| `JARVIS_PUBLIC_URL` | blank | Origin the phone reaches (used for pairing) |
| `SESSION_SECRET_KEY` | placeholder | Signs sessions and device credentials |
| `SESSION_COOKIE_SECURE` | false | true when served over HTTPS |
| `TAILSCALE_HOST` | blank | Only for unusual cross-origin setups |
| `WEB_PUSH_ENABLED`, `VAPID_*` | off | Web Push delivery |

Relative paths in settings are relative to `backend/`. Defaults for new
conversations can also be changed in Settings.

To create Web Push keys:

```powershell
cd backend
uv run --group notifications python -c "import base64;from cryptography.hazmat.primitives.asymmetric import ec;from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat;k=ec.generate_private_key(ec.SECP256R1());b=lambda v:base64.urlsafe_b64encode(v).rstrip(b'=').decode();print('VAPID_PRIVATE_KEY='+b(k.private_numbers().private_value.to_bytes(32,'big')));print('VAPID_PUBLIC_KEY='+b(k.public_key().public_bytes(Encoding.X962,PublicFormat.UncompressedPoint)))"
```

## Jarvis voice

[/jarvis](http://localhost:3000/jarvis) combines GPT-Live WebRTC audio with
backend-only sideband control, durable daily logs, typed fallback, task
delegation, reminders and replayable activity. **Arm** starts a Live session;
**Sleep** ends it. There is no local speech fallback; typed interaction keeps
working when Live fails.

The browser runs an ONNX wake model (`/jarvis-wake/models/jarvis.onnx`);
setting `NEXT_PUBLIC_JARVIS_WAKE_MODEL_URL` to that path enables foreground
auto-arm. Wake calibration samples stay in browser storage and can be exported
from Settings → Wake calibration. Install a new candidate model with
`cd frontend; npm run install:wake-model -- --wake-model <path>`; provenance is
in [docs/JARVIS_WAKE_MODEL.md](docs/JARVIS_WAKE_MODEL.md).

## Apps

Ask Jarvis for an app or a change to one. It builds, tests and installs the app
as a package on the [app platform](docs/jarvis-tasks/13-app-platform.md).
Installed apps are listed at `/workspace/apps` and open at `/apps/<slug>`.
Recipes and Documents are built-in packages (`backend/apps/`); task results are
records in those apps.

## Android companion

The Kotlin app under `android/` targets API 37: assistant role, native audio and
wake, notifications, screen context and the phone UI in a WebView. Link it with
Settings → Link phone and see [android/README.md](android/README.md).

## Phone access over Tailscale

Join the computer and the phone to the same tailnet. For development, open
`http://<tailscale-ip>:3000`; the browser calls `/api` on the same origin and
Next.js proxies to the backend, so port 8000 never needs to be reachable. For
HTTPS, run `tailscale serve --bg 3000` and open the printed URL (the personal
host requires this). No router port forwarding is needed.

## Troubleshooting

- **`uv` or `npm` not found:** check `uv --version` and `npm --version` in a new
  terminal.
- **Login says the password is invalid:** in personal mode `AUTH_PASSWORD` must
  be empty; in password mode it must be set. Restart after changing it.
- **The frontend cannot reach the backend:** open
  [localhost:8000/api/health](http://localhost:8000/api/health) and read the
  backend terminal.
- **A model request returns 402/404:** check the OpenRouter key, credit and
  that every configured model ID still exists.
- **Hot reload blocked over Tailscale:** set `TAILSCALE_HOST` to the address in
  the browser and restart the frontend.
- **Something looks wrong in Jarvis:** `uv run python -m app.selftest doctor`
  (in `backend/`) lists findings with proposed fixes, for free.

## Repository layout

```text
backend/     FastAPI app (app/), tests, Alembic baseline, workflows, built-in apps
frontend/    Next.js app; result-export/ is a separate static build
android/     Android companion; scripts/ holds the emulator harnesses
docs/        Vision, architecture, verification, design docs and history reports
knowledge/   Approved knowledge notes and preferences
scripts/     jarvis.ps1 (the personal host launcher) and old experiment summarizers
```
