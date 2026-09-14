# AI Cockpit

AI Cockpit is a locally hosted AI workspace with durable conversations, Agent and Council chat modes, reviewed knowledge, generated apps, and a persistent Jarvis assistant. The frontend is a Next.js application, the API is FastAPI, local state is stored in SQLite, and the optional Android 17 companion can act as the selected system assistant. GPT-Live powers Jarvis audio; delegated tasks default to a loopback OpenAI-compatible server such as llama.cpp, while OpenRouter remains an explicit cloud option.

This repository currently targets local development. Windows has convenience scripts and VS Code tasks; macOS and Linux can use the manual commands below.

## What You Can Do

- Run multi-step Agent conversations with native tool calls and an inline activity timeline.
- Ask an agent to create or update applications hosted at `/apps/<slug>`.
- Compare several models in Council mode and synthesize their responses.
- Keep conversations, branches, tool activity, reasoning summaries, and final responses across restarts.
- Review proposed knowledge before promoting it into local files.
- Reach the development UI from another device through Tailscale.
- Dispatch durable background tasks with ordered traces, isolated attempts, and explicit cancel/retry controls.
- Use Jarvis for streamed conversation, durable reminders, natural task follow-up, reviewed memory, local history recall, and phone or Android system-assistant access.

For implementation details, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). The living implementation plan is [docs/JARVIS_FOLLOW_UP_PLAN.md](docs/JARVIS_FOLLOW_UP_PLAN.md), active acceptance failures are tracked in [docs/JARVIS_GAPS.md](docs/JARVIS_GAPS.md), and wake-model provenance remains in [docs/JARVIS_WAKE_MODEL.md](docs/JARVIS_WAKE_MODEL.md).

## Quick Start

### 1. Install prerequisites

| Tool | Supported version | Notes |
|---|---:|---|
| Git | Current | Required to clone the repository |
| Python | 3.11 or 3.12 | Python 3.12 is recommended |
| Node.js | 20.19+ or 22.13+ | Node.js 22 LTS is recommended |
| uv | Current | Install with `python -m pip install uv` |
| Android SDK | API 37, optional | Required only to build the Android 17 companion |

OpenRouter is optional. Jarvis voice requires an OpenAI API key, while the default task model expects llama.cpp (or another OpenAI-compatible server) on `127.0.0.1:8080`.

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

Open `.env` and configure the OpenAI key plus either the local server or optional OpenRouter key:

```env
OPENROUTER_API_KEY=sk-or-v1-your-key-here
OPENAI_API_KEY=sk-proj-your-key-here
LOCAL_LLM_BASE_URL=http://127.0.0.1:8080/v1
LOCAL_LLM_ALIAS=local/gemma-4-12b
LOCAL_LLM_MODEL=google/gemma-4-12B-it-qat-q4_0-gguf:Q4_0
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

`uv sync` creates `backend/.venv` and installs the locked runtime and development dependencies. Add `--group notifications` when Web Push is enabled and `--group benchmark` to run the optional live Jarvis benchmark. The retired local speech stack has no separate dependency group.

Frontend:

```powershell
cd frontend
npm ci
cd ..
```

### 5. Start the application

On Windows, open three PowerShell terminals in the repository root:

```powershell
# Terminal 1
.\start-backend.ps1
```

```powershell
# Terminal 2
.\scripts\start-worker.ps1 -Foreground
```

```powershell
# Terminal 3
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
cd backend
uv run python -m app.worker
```

```bash
# Terminal 3
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
- Jarvis live workspace: [http://localhost:3000/jarvis](http://localhost:3000/jarvis)
- API documentation: [http://localhost:8000/docs](http://localhost:8000/docs)

With the default development configuration, enter any non-empty password on the login page.
The first typed Jarvis turn creates the current daily log and starts the fake provider. Prefix a turn with `delegate:` to exercise immediate background-task acknowledgement without a microphone or external provider.

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

Jarvis voice uses GPT-Live. Multi-step task turns default to the local provider. Council mode and other configured cloud models still use OpenRouter; model availability and pricing can change, so check the [OpenRouter model catalog](https://openrouter.ai/models) when using those models.

The main settings are:

| Variable | Development default | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | example value | Required only for configured OpenRouter model requests |
| `OPENAI_API_KEY` | example value | Backend-only key used to create GPT-Live Jarvis calls |
| `AVAILABLE_MODELS` | GPT-5.6 Luna/Terra/Sol, Gemini 3.5 Flash, Claude Sonnet/Opus 5, Grok 4.5 | Options shown in the model pickers |
| `COUNCIL_MODELS` | GPT-5.6 Terra, Gemini 3.5 Flash, Claude Sonnet 5 | Models queried in Council mode |
| `SYNTHESIZER_MODEL` | Claude Sonnet 5 | Combines Council responses |
| `LOCAL_LLM_ENABLED` | true | Enable the loopback OpenAI-compatible provider |
| `LOCAL_LLM_BASE_URL` | http://127.0.0.1:8080/v1 | llama.cpp-compatible local endpoint |
| `LOCAL_LLM_ALIAS` | local/gemma-4-12b | Public model alias used by AI Cockpit |
| `LOCAL_LLM_MODEL` | Gemma 4 Q4 checkpoint ID | Upstream model ID sent to the local server |
| `TASK_AGENT_MODEL` | local/gemma-4-12b | Default chat and multi-step Agent model; must support native tool calls |
| `NEXT_PUBLIC_JARVIS_WAKE_MODEL_URL` | blank | Browser path to the installed exact-word Jarvis ONNX model; blank keeps local wake disabled and preserves manual Arm |
| `AUTH_PASSWORD_HASH` | blank | Blank enables local development login |
| `SESSION_SECRET_KEY` | insecure example | Signs login cookies; replace for remote use |
| `ALLOWED_DIRS` | blank | Optional comma-separated absolute directories |
| `TAILSCALE_HOST` | blank | Optional fixed Tailscale IP or MagicDNS hostname; normal same-origin tailnet access requires no per-device list |
| `NTFY_TOPIC` | blank | Optional ntfy.sh topic |
| `WEB_PUSH_ENABLED` | false | Enable authenticated Web Push delivery |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` | blank | VAPID credentials for Web Push |
| `SESSION_COOKIE_SECURE` | false | Set true when serving the app over HTTPS |

Defaults for future conversations can also be changed from the Settings page after login.

## Jarvis

The current voice workspace is [http://localhost:3000/jarvis](http://localhost:3000/jarvis). It combines GPT-Live WebRTC audio with backend-only sideband control, durable daily logs and focus, typed fallback, task delegation, durable reminders, natural task references, provisional transcript streaming, replayable activity, and optional Web Push delivery. The retired `/voice` page permanently redirects here.

Set `OPENAI_API_KEY` in the repository root `.env`, start the backend, separate worker, and frontend, then send the first typed turn if today has no log. Click **Arm** and grant microphone permission to begin a GPT-Live session; **Sleep** tears down the browser and provider session. The local model and worker remain necessary for delegated tasks. There is no local STT/TTS fallback; a Live failure leaves typed interaction available.

Before committing Live voice changes, export the browser wake samples from **Settings → Wake calibration**, then run `cd frontend; npm run test:live-smoke -- --audio "C:\\path\\to\\jarvis-calibration-….json"`. The opt-in smoke test selects a positive `normal` sample, feeds it through a real Chromium microphone stream and the installed ONNX wake detector, negotiates GPT-Live over WebRTC, requires combined startup readiness, verifies remote audio plus persisted user and assistant voice segments, and closes the exact session. It runs against temporary backend data and a separate frontend port, so it does not alter the normal Jarvis conversation log. Once an export exists in Downloads, `npm run test:live-smoke` finds the newest export automatically. Use `--headed` to watch the run or `--sample-id <id>` to pin a recording. The test uses the configured OpenAI key and therefore creates a short billable Live session; private recordings and generated test reports remain outside Git.

The browser uses an off-main-thread Hey Buddy/ONNX wake runtime, bounded in-memory pre-roll, a direct-address gate, and one ordered activation audio stream that continues into the conversation. GPT-Live owns conversational turn detection and interruption behavior. The selected exact-word Jarvis model is installed at `/jarvis-wake/models/jarvis.onnx`; setting `NEXT_PUBLIC_JARVIS_WAKE_MODEL_URL` to that path enables foreground auto-arm, while a missing or failed model never removes the existing **Arm** fallback. Wake calibration samples stay in browser-local IndexedDB and can be reviewed, exported for GPU retraining, or deleted from Settings. Reinstall and integrity-record a future candidate with `cd frontend; npm run install:wake-model -- --wake-model /absolute/path/to/jarvis.onnx`.

Run the current local Jarvis acceptance benchmark with `python scripts/benchmark-jarvis.py --performance-trials 5`; use `--output <path>` to save the JSON report.

Voice, concision, wake behavior, developer visibility, and Android privacy preferences are persisted by the server. Marin is the default voice and Cedar is the alternate; a voice change applies to the next Live session.

## Android 17 Companion

The Kotlin application under `android/` targets API 37 and provides a `VoiceInteractionService`, Compose assistant overlay, WebView phone UI, local ONNX wake inference, native WebRTC audio, resumable task/reminder notifications, short-lived code pairing, and Keystore-backed device credentials.

Generate a five-minute pairing code from AI Cockpit Settings, then follow [android/README.md](android/README.md) to build, pair, and select Jarvis as the assistant. Foreground-app assist structure is opt-in, screenshots remain disabled, restart listening is opt-in, and the backend blocks general mutations while the paired device reports that it is locked.

## Phone Access with Tailscale

### Direct HTTP access

1. Install Tailscale on the development computer and phone and join both to the same tailnet.
2. Get the computer's address with `tailscale ip -4` or `tailscale status`.
3. Open `http://100.x.y.z:3000` from any device in the tailnet. You can use the computer's MagicDNS hostname instead.

The browser uses same-origin `/api` requests, and Next.js proxies them to the backend on the development computer. Port 8000 does not need to be reachable from other devices, and individual tailnet devices do not need to be added to `.env`. `TAILSCALE_HOST` remains available for unusual cross-origin setups.

### HTTPS with Tailscale Serve

For a tailnet-only HTTPS URL, set `TAILSCALE_HOST` to the computer's full MagicDNS name, restart the servers, and proxy the frontend with [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve):

```powershell
tailscale serve --bg 3000
tailscale serve status
```

Open the HTTPS URL printed by Tailscale. Tailscale terminates HTTPS and forwards traffic to the Next.js server on port 3000. To remove the proxy:

```powershell
tailscale serve reset
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
├── android/                 Android 17 system-assistant companion
├── docs/ARCHITECTURE.md     Current implementation architecture
├── docs/JARVIS_FOLLOW_UP_PLAN.md  Living Jarvis v2 plan
├── docs/JARVIS_GAPS.md            Active Jarvis acceptance gaps
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
- Android compilation and device acceptance require a local API 37 SDK and Android 17 hardware; the repository can resolve the Android dependency graph without them.
- The installed wake model has foreground POC acceptance, but the stricter v2 quiet/distant, false-wake, address-precision, battery, and screen-off acceptance gates still require calibrated real-device runs.
