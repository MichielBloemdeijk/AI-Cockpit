# AI Cockpit

AI Cockpit is a locally hosted AI workspace with durable conversations, Agent and Council chat modes, reviewed knowledge, and generated apps. The frontend is a Next.js application, the API is FastAPI, model requests go through OpenRouter, and local state is stored in SQLite.

This repository currently targets local development. Windows has convenience scripts and VS Code tasks; macOS and Linux can use the manual commands below.

## What You Can Do

- Run multi-step Agent conversations with native tool calls and an inline activity timeline.
- Ask an agent to create or update applications hosted at `/apps/<slug>`.
- Compare several models in Council mode and synthesize their responses.
- Keep conversations, branches, tool activity, reasoning summaries, and final responses across restarts.
- Review proposed knowledge before promoting it into local files.
- Reach the development UI from another device through Tailscale.

For implementation details, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick Start

### 1. Install prerequisites

| Tool | Supported version | Notes |
|---|---:|---|
| Git | Current | Required to clone the repository |
| Python | 3.11 or 3.12 | Python 3.12 is recommended |
| Node.js | 20.19+ or 22.13+ | Node.js 22 LTS is recommended |
| uv | Current | Install with `python -m pip install uv` |

You also need an [OpenRouter](https://openrouter.ai/) API key. Creating a key is free, but the default models are paid and require available credit. You can replace the configured model IDs with models accessible to your account.

### 2. Clone the repository

```powershell
git clone https://github.com/MichielBloemdeijk/AI-Cockpit.git
cd AI-Cockpit
```

### 3. Create the local configuration

PowerShell:

```powershell
Copy-Item .env.example .env
```

macOS/Linux:

```bash
cp .env.example .env
```

Open `.env` and replace the example OpenRouter key:

```env
OPENROUTER_API_KEY=sk-or-v1-your-key-here
```

The copied file is safe for localhost development:

- `AUTH_PASSWORD_HASH` is blank, so any non-empty password is accepted.
- `ALLOWED_DIRS` is blank, so it contains no machine-specific paths.
- `SESSION_SECRET_KEY` is deliberately insecure and must be replaced before remote or shared use.

### 4. Install dependencies

Backend, from the repository root:

```powershell
cd backend
uv sync
cd ..
```

`uv sync` creates `backend/.venv` and installs the locked runtime and development dependencies.

Frontend:

```powershell
cd frontend
npm ci
cd ..
```

### 5. Start the application

On Windows, open two PowerShell terminals in the repository root:

```powershell
# Terminal 1
.\start-backend.ps1
```

```powershell
# Terminal 2
.\start-frontend.ps1
```

The launchers resolve `uv` and `npm` from `PATH`; they do not depend on machine-specific installation paths.

On macOS/Linux, or to run the processes manually:

```bash
# Terminal 1
cd backend
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

```bash
# Terminal 2
cd frontend
npm run dev
```

VS Code users can alternatively run `Tasks: Run Task` → `Start All`.

### 6. Verify the installation

Check the backend:

```powershell
Invoke-RestMethod http://localhost:8000/api/health
```

Expected response:

```json
{"status":"ok","version":"0.1.0"}
```

Then open:

- UI: [http://localhost:3000](http://localhost:3000)
- API documentation: [http://localhost:8000/docs](http://localhost:8000/docs)

With the default development configuration, enter any non-empty password on the login page.

The backend creates and migrates its SQLite database automatically on first startup. No separate migration command is required.

## Secure Authentication

Development mode is convenient on localhost but should not be used when another device can reach the application.

Generate a session secret:

```powershell
cd backend
uv run python -c "import secrets; print(secrets.token_hex(32))"
```

Generate a bcrypt password hash:

```powershell
uv run python -c "from passlib.hash import bcrypt; import getpass; print(bcrypt.hash(getpass.getpass('Password: ')))"
```

Put both values in the repository-root `.env` file and restart the backend:

```env
SESSION_SECRET_KEY=<generated-secret>
AUTH_PASSWORD_HASH=<generated-bcrypt-hash>
```

## Model Configuration and Cost

The defaults are ordinary paid OpenRouter models. Model availability and pricing can change, so check the [OpenRouter model catalog](https://openrouter.ai/models) if a request returns a model-not-found or payment error.

The main settings are:

| Variable | Development default | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | example value | Required for model requests |
| `COUNCIL_MODELS` | Claude Sonnet 4.5, Gemini 2.5 Pro, GPT-4o | Models queried in Council mode |
| `SYNTHESIZER_MODEL` | Claude Sonnet 4.5 | Combines Council responses |
| `TASK_AGENT_MODEL` | Claude Haiku 4.5 | Runs multi-step Agent conversations; must support native tool calls |
| `AUTH_PASSWORD_HASH` | blank | Blank enables local development login |
| `SESSION_SECRET_KEY` | insecure example | Signs login cookies; replace for remote use |
| `ALLOWED_DIRS` | blank | Optional comma-separated absolute directories |
| `TAILSCALE_HOST` | blank | Optional Tailscale IP or MagicDNS hostname |
| `NTFY_TOPIC` | blank | Optional ntfy.sh topic |

Defaults for future conversations can also be changed from the Settings page after login.

## Phone Access with Tailscale

### Direct HTTP access

1. Install Tailscale on the development computer and phone and join both to the same tailnet.
2. Get the computer's address with `tailscale ip -4` or `tailscale status`.
3. Set the reachable IP or MagicDNS hostname in `.env`, for example:

   ```env
   TAILSCALE_HOST=100.x.y.z
   ```

4. Restart both development servers so backend CORS and Next.js development origins are rebuilt.
5. Open `http://100.x.y.z:3000` from the phone.

The browser uses the frontend origin for `/api` requests, and Next.js proxies them to the backend on the development computer. Port 8000 does not need to be opened separately to the phone.

### HTTPS with Tailscale Serve

For a tailnet-only HTTPS URL, set `TAILSCALE_HOST` to the computer's full MagicDNS name, restart the servers, and proxy the frontend with [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve):

```powershell
tailscale serve --bg 3000
tailscale serve status
```

Open the HTTPS URL printed by Tailscale. Tailscale terminates HTTPS and forwards traffic to the Next.js server on port 3000. To remove the proxy:

```powershell
tailscale serve off
```

No router port forwarding is required for either approach.

## Generated Apps

Ask for a new app or an update from an Agent conversation. The relevant paths are:

- `frontend/generated-apps/<slug>` — authoritative editable source for an app
- `frontend/public/apps/<slug>` — public assets for that app
- `frontend/app/apps/<slug>` — synchronized Next.js route copy used by the live development server
- `/workspace/apps` — app management UI
- `/apps/<slug>` — live app URL

Generated app and conversation runtime data are intentionally ignored by Git.

## Development Commands

Backend:

```powershell
cd backend
uv run pytest
```

Frontend:

```powershell
cd frontend
npm test
npm run lint
npm run build
```

## Troubleshooting

### `uv` or `npm` is not recognized

Confirm the command is available in a new terminal:

```powershell
uv --version
npm --version
```

Install uv with `python -m pip install uv`. Install a supported Node.js release if `npm` is missing.

### Login always says the password is invalid

For development mode, confirm the copied `.env` contains an actually empty value:

```env
AUTH_PASSWORD_HASH=
```

Restart the backend after changing it. If a hash is configured, only the corresponding password will work.

### The frontend says it cannot reach the backend

Open [http://localhost:8000/api/health](http://localhost:8000/api/health). If it fails, inspect the backend terminal before debugging the frontend. The development proxy expects the backend on port 8000.

### A model request returns 402, 404, or a provider error

Check that the OpenRouter key is valid, the account has sufficient credit, and every configured model ID is currently available. Update `.env` or choose different defaults from Settings.

### Tailscale loads the page but hot reload is blocked

Set `TAILSCALE_HOST` to the exact IP or hostname used in the browser and restart the frontend. Next.js reads allowed development origins only when it starts.

### Port 3000 or 8000 is already in use

Stop the other process using the port. The frontend proxy and documented URLs assume the default ports, so changing them also requires updating `frontend/next.config.ts`.

## Repository Layout

```text
AI-Cockpit/
├── backend/                 FastAPI API, agent runtime, persistence, tests
├── frontend/                Next.js UI and generated-app route host
├── docs/ARCHITECTURE.md     Current implementation architecture
├── knowledge/               Approved local knowledge and preference template
├── .env.example             Safe local configuration template
├── start-backend.ps1        Portable Windows backend launcher
└── start-frontend.ps1       Portable Windows frontend launcher
```

Interactive API details are available from `/docs` while the backend is running.

## Current Limitations

- This is a development-oriented local application, not a hardened production deployment.
- Agent mode can execute tools and write within configured conversation/app workspaces. Review model-produced changes before relying on them.
- Generated apps do not yet have browser-driven verification and automatic repair loops.
- Interrupted runs are persisted but are not automatically resumed after a backend restart.
