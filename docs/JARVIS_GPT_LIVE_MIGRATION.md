# Jarvis GPT-Live migration

Status: implementation complete; matched device evaluation pending

Baseline commit: `a58bbf2`

## Fixed decisions

- Add `openai_live` alongside the existing `openai` and `fake` voice providers.
- Keep Realtime 2.1 as the default until matched comparative evaluation passes.
- Use `gpt-live-1` with voice `marin` for the experimental Live path.
- Use managed Responses delegation with GPT-5.6 Luna at low reasoning for the first implementation.
- GPT-Live owns continuous conversation, acknowledgements, interruptions, and spoken updates; context-append acknowledgements are tracked separately from confirmed playback.
- AI Cockpit owns authorization, tool execution, idempotency, durable tasks, and notifications.
- Browser and Android expose an experimental engine selector that applies to the next session.
- Local wake detection opens a Live conversation directly; transcript-based intent gating is not part of this migration.
- Browser wake and follow-up audio share one continuous, bounded 30-second queue that starts feeding the negotiated WebRTC microphone track as soon as the connection is ready; overflow requires an explicit retry.
- Android carries a bounded wake/startup PCM buffer into the Live WebRTC microphone path before continuing with current capture.
- Timestamped Live transcript fragments are persisted as conversation turns and used to attribute delegated tool calls to the originating request.
- Live sessions close after 60 seconds without meaningful conversational activity once router/tool exchanges settle; silent media and housekeeping events do not extend the session.

## Delivery checkpoints

- [x] Provider capability contracts and deterministic backend tests
- [x] Shared authorized Jarvis tool catalog
- [x] Live session creation and sideband adapter
- [x] Responses delegation event handling and idempotent tool results
- [x] Browser vertical slice
- [x] Android vertical slice
- [x] Task updates, interruption, lifecycle, and usage accounting
- [x] Matched Realtime/Live evaluation harness
- [ ] Physical-device matched evaluation and rollout decision

## Verification evidence

- Backend: full pytest suite, `297 passed`.
- Browser: Vitest `194 passed`, ESLint clean, production Next.js build successful.
- Android: Gradle `test` successful.
- Evaluation runner: `python scripts/compare-jarvis-voice-engines.py observations.jsonl`.

## Unresolved failures

No automated implementation failure is open. A real OpenAI project with
GPT-Live access, browser microphone, and Android device are still required to
verify end-to-end latency/audio handoff and collect the 20 matched
Realtime/Live observations. Realtime remains the default until those rollout
gates pass.
