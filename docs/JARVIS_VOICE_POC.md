# Jarvis Voice POC

This document is the saved implementation contract for the local Jarvis voice prototype.

## Runtime contract

- The `/voice` PWA uses local VAD, faster-whisper STT, llama.cpp/Gemma task inference, and Kokoro TTS.
- Voice turns are authenticated and use one server-owned primary conversation.
- Interactive runs have priority over background runs. Background work is owned by `RunSupervisor` and continues after an HTTP/SSE observer disconnects.
- Local inference is the default. Cloud is used only for an explicitly marked turn; there is no automatic per-turn fallback.
- Unfinished queued, running, or paused work is marked interrupted on restart and is never automatically resumed.

## Durable lifecycle

Runs use `queued`, `running`, `paused`, `completed`, `failed`, `cancelled`, and `interrupted`. A queued run must be atomically claimed before execution. Questions persist in run metadata and are resumed with `POST /api/voice/runs/{run_id}/answers`; cancellation is explicit. Reset returns a conflict while any run is unfinished.

The voice APIs are:

- `GET /api/voice/health`
- `GET /api/voice/state`
- `POST /api/voice/turns`
- `POST /api/voice/runs/{run_id}/answers`
- `POST /api/voice/tasks/{run_id}/cancel`
- `POST /api/voice/transcriptions`
- `POST /api/voice/speech`

Conversation events are authoritative. Assistant replies, questions, task progress, terminal results, and interruption notices include `voice_delivery` metadata. The client persists event delivery state in IndexedDB and acknowledges `spoken` only after complete audio playback.

## Local setup and launcher

Keep downloaded runtime assets under `backend/models/` (the directory is git-ignored). Run the idempotent preflight from PowerShell:

```powershell
.\scripts\setup-jarvis.ps1 -GemmaModelPath .\backend\models\gemma-4-12b-it-qat-q4_0.gguf -GenerateVapidKeys
```

The script discovers `llama-server` on `PATH` or anywhere under `tools\llama.cpp`, validates the Gemma and Kokoro files, syncs the `voice` and `notifications` dependency groups (plus `benchmark` when configured), reports Tailscale state, and only fills blank `.env` entries. Use `-CheckOnly` or `-DryRun` to inspect prerequisites without syncing or writing files; generated key material is never printed.

Start or stop the loopback services with:

```powershell
.\scripts\start-jarvis-local.ps1 -GemmaModelPath .\backend\models\gemma-4-12b-it-qat-q4_0.gguf
.\scripts\stop-jarvis-local.ps1
```

The launcher binds llama.cpp to `127.0.0.1:8080`, the backend to `127.0.0.1:8000`, and Next.js to `127.0.0.1:3000`. Child processes run hidden, with logs under `.cockpit\jarvis\logs`; use Tailscale Serve for an HTTPS phone URL.

## Browser behavior

The state machine is `stopped → armed → listening → transcribing → thinking/working → speaking → follow-up`. Follow-up lasts 45 seconds and then requires the wake word again. VAD pauses during transcription, agent work, and TTS. User barge-in dismisses the current utterance, preserves later durable updates, and resumes listening. Ordinary task updates wait for a safe speech boundary; hidden pages rely on Web Push.

Only a visible `/voice` client suppresses a system notification. Notification clicks open `/voice?update=<event-id>&delivery=<delivery-id>`, and authentication preserves that relative deep link.

## Verification gates

- Backend and frontend unit/integration tests pass, including run races, cursor replay, question resume, push policy, service-worker routing, and exactly-once speech acknowledgement.
- Gemma produces at least 19/20 valid single-tool calls and completes 8/10 representative multi-step tasks.
- Local inference reaches 20 generated tokens/second, p90 first-token latency below 1.5 seconds, and peak combined VRAM below 11.5 GB.
- Android Chrome over Tailscale reaches median end-of-speech to first audio below 2.5 seconds and p95 below 4 seconds, with fewer than 1/50 false wake activations.
- Twenty notification jobs have no missed or duplicate requested completions.
- For this personal POC, authenticated APIs, sandboxed tools, audit events, cancellation, and loopback-only llama.cpp access remain enabled. A real bcrypt password and secure HTTPS cookie can be enabled later without changing the voice architecture.

### Local acceptance evidence (2026-08-24)

- Gemma passed the automated brain gate: 20/20 schema-valid single-tool calls and 10/10 representative production-agent tasks.
- Median generation throughput was 86.48 tokens/second, p90 first-token latency was 0.355 seconds, and peak combined LLM plus warm STT VRAM was 10,051 MiB.
- A real WAV segment transcribed to `Jarvis status check.` with the wake word stripped correctly; Kokoro produced the corresponding WAV response while Gemma remained resident.
- A delegated live request acknowledged in 746 ms, created a separately observable child run, accepted another conversational turn while that child ran, and published a durable terminal summary.
- Restarting the PC-side stack during a real child task marked it `interrupted`, retained its trace, and did not resume it.
- Cursor replay, Web Push configuration, PWA assets, service-worker routing, and the real `/voice` browser update path were exercised against the running stack.

The remaining performance and reliability gates require the actual Android device: microphone behavior, wake-word false activations, end-of-speech-to-audio latency over Wi-Fi/Tailscale, lock-screen notification delivery, installability, and exactly-once speech after a notification click.

Vision/video capture and native Android wake-word support remain future work. If the local agent-quality gate fails, benchmark Qwen3.5-9B Q4; if both fail, configure the existing cloud task agent explicitly. If local speech latency/quality fails, evaluate the documented realtime speech pivot.
