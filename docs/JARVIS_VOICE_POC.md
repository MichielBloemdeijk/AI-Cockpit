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

## Browser behavior

The state machine is `stopped → armed → listening → transcribing → thinking/working → speaking → follow-up`. Follow-up lasts 45 seconds and then requires the wake word again. VAD pauses during transcription, agent work, and TTS. User barge-in dismisses the current utterance, preserves later durable updates, and resumes listening. Ordinary task updates wait for a safe speech boundary; hidden pages rely on Web Push.

Only a visible `/voice` client suppresses a system notification. Notification clicks open `/voice?update=<event-id>&delivery=<delivery-id>`, and authentication preserves that relative deep link.

## Verification gates

- Backend and frontend unit/integration tests pass, including run races, cursor replay, question resume, push policy, service-worker routing, and exactly-once speech acknowledgement.
- Gemma produces at least 19/20 valid single-tool calls and completes 8/10 representative multi-step tasks.
- Local inference reaches 20 generated tokens/second, p90 first-token latency below 1.5 seconds, and peak combined VRAM below 11.5 GB.
- Android Chrome over Tailscale reaches median end-of-speech to first audio below 2.5 seconds and p95 below 4 seconds, with fewer than 1/50 false wake activations.
- Twenty notification jobs have no missed or duplicate requested completions.
- Real bcrypt authentication, non-default session secret, secure HTTPS cookies, authenticated APIs, sandboxed tools, audit events, cancellation, and loopback-only llama.cpp access are verified before phone testing.

Vision/video capture and native Android wake-word support remain future work. If the local agent-quality gate fails, benchmark Qwen3.5-9B Q4; if both fail, configure the existing cloud task agent explicitly. If local speech latency/quality fails, evaluate the documented realtime speech pivot.
