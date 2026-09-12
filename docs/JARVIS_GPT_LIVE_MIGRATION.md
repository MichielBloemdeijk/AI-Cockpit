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
- Browser wake and follow-up audio share one continuous, bounded 30-second queue that starts feeding the negotiated WebRTC microphone track as soon as the connection is ready. It catches up only through conservatively classified silence, retains 500 ms of each pause for natural turn detection, and preserves uninterrupted speech; overflow requires an explicit retry.
- Android carries a bounded wake/startup PCM buffer into the Live WebRTC microphone path and applies the same silence-only catch-up policy before continuing with current capture.
- Timestamped Live transcript fragments are segmented by timeline rather than arrival order. Each `session.delegation.created.offset_ms` is retained under its opaque delegation ID across Responses continuations. Stable delegation correlations let tools execute immediately without borrowing an older turn, then repair task links only when a unique timeline match arrives; ambiguous matches remain unattributed.
- Timestamped assistant transcript segments are saved as generated conversation history independently of speech-delivery and notification-heard evidence. Response completion is only a hint; remaining fragments flush on session close.
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

- Backend: full pytest suite, `302 passed`.
- Browser: Vitest `196 passed`, ESLint clean, production Next.js build successful.
- Android: Gradle `test` successful.
- Physical Android latency: blocked on 2026-09-12 because ADB reported no attached USB or wireless device; no emulator result is substituted for this gate.
- Evaluation runner: `python scripts/compare-jarvis-voice-engines.py observations.jsonl`.

## Unresolved failures

No automated implementation failure is open. A real OpenAI project with
GPT-Live access, browser microphone, and Android device are still required to
verify end-to-end latency/audio handoff and collect the 20 matched
Realtime/Live observations. Realtime remains the default until those rollout
gates pass.
