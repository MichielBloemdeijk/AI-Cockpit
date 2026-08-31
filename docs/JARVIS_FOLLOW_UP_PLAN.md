# Jarvis Follow-Up Plan v2

Status: Implemented; release acceptance pending
Date: 2026-08-31

## Implementation Checkpoint

The v2 software path is implemented across the backend, phone-first web client,
local wake/address flow, and Android 17 companion. The repository now has one
living plan plus the current architecture and wake-model record; superseded
planning documents remain available through Git history.

Completed implementation includes:

- durable preferences, reminders, focus/reference continuity, FTS5 recall,
  reviewed-memory proposals, resumable live events, paired devices, and locked
  capability enforcement;
- provisional transcript streaming, mobile navigation/sheets, local calibration
  storage/export, and deterministic direct-address filtering;
- an API 37 Kotlin assistant application with native invocation, Compose UI,
  WebView, local ONNX wake inference, WebRTC, Keystore pairing, event replay,
  lock reporting, and notification actions.

Release acceptance is intentionally not claimed yet. The API 37 debug build is
verified; remaining evidence requires an Android 17 device for device scenarios,
plus user calibration recordings and held-out runs for the stricter wake/address,
battery, screen-off, process-death, and network-loss gates. The currently
installed model has foreground POC acceptance but does not by itself prove the
v2 quiet/distant or one-false-wake-per-hour thresholds.

Repository verification on 2026-08-31:

- backend: `213 passed`;
- frontend: ESLint passed, `119 passed`, and the production Next.js build passed;
- Android: API 37 `testDebugUnitTest assembleDebug` passed and produced a debug APK;
- documentation: local links resolve and no maintained document references the
  retired Jarvis plan/ledger/POC files.

## Summary

Build Jarvis into a persistent, concise assistant with streamed responses, durable reminders, natural task continuity, fast local recall, improved wake/address detection, a phone-first interface, and an Android 17 native assistant that replaces Gemini.

The Android client handles invocation, listening, notifications, and presentation. AI Cockpit remains the durable source of conversations, tasks, memory, scheduling, and Jarvis's current focus.

## Phased Implementation

### Phase 0 — Baseline and documentation reset

- Record the current passing backend, frontend, and wake-model baseline.
- Replace completed historical planning documents with this living follow-up plan; retain Git history and update README/architecture links.
- Keep `docs/ARCHITECTURE.md` and `docs/JARVIS_WAKE_MODEL.md`; leave the unrelated root `PLAN.md` untouched.
- Remove obsolete Jarvis implementation, ledger, target, evolution, and POC documents after migrating their remaining references.

### Phase 1 — Conversational feel, streaming, voices, and mobile UI

- Stream provisional assistant transcript deltas to `/jarvis` through authenticated resumable SSE. Reconcile provisional text with the durable final or heard-prefix transcript after completion or barge-in.
- Show provisional user text only when the provider supplies reliable transcription deltas.
- Default Jarvis to one or two short sentences, elaborating when requested or required for safety or clarity.
- Persist voice selection per user. Provide Marin as the current/feminine default and Cedar as the alternate/masculine voice, changeable through settings or `set_jarvis_voice`.
- Rotate the Realtime session at a safe boundary when changing voice.
- Make streaming deltas participate in newest-message scrolling and interruption reconciliation.

Phone-first redesign:

- Use a `100dvh` conversation surface with independent message scrolling, safe-area insets, and a composer that remains above the keyboard and navigation.
- Replace the seven-item mobile navigation with Jarvis, Chat, Tasks, and More; place Apps, Wiki, Settings, and Logout in More.
- Show the conversation as the primary Jarvis surface. Move tasks and activity into badge-bearing bottom sheets/tabs.
- Hide the developer lab behind developer settings.
- Replace the dense status header with a compact listening/status control that expands into diagnostics.
- Use at least 48dp touch targets and layouts supporting 320, 360, 390, and 430px phone widths.
- Preserve the existing multi-column desktop workspace.

### Phase 2 — Durable reminders through the task system

- Treat reminders as ordinary delegated tasks, not client-owned timers.
- Add `waiting_until`, `resume_at`, wait reason, and ordered wait/resume events.
- Add the task-agent-only `task_wait_until` control.
- Run an independent scheduler that leases due tasks, survives restarts, and delivers overdue reminders exactly once using idempotency keys.
- Support one-shot relative and absolute reminders, plus list, cancel, and snooze.
- Store UTC instants and interpret unqualified times using Europe/Amsterdam.
- Ask a concise clarification only when the requested time is genuinely ambiguous.
- When Jarvis is active, present the reminder naturally at a safe boundary. When inactive, issue a notification only.
- Keep the PC-online requirement. The phone does not independently mirror the schedule.

### Phase 3 — Natural task continuity, grep-shaped recall, and reviewed memory

Natural task references:

- Store a durable internal Jarvis focus containing the currently discussed task and a recency-ordered task-reference stack. Restore it after refresh, app restart, device handoff, or Realtime reconnection.
- Update focus whenever Jarvis:
  - creates or delegates a task;
  - discusses a task's progress or result;
  - receives a task question;
  - resolves a user's natural reference to a task.
- Resolve references deterministically:
  - “this,” “that,” “it,” or “that task” means the focused task;
  - “current task” means the focused active task, otherwise the most recently discussed active task;
  - “last task” means the most recently discussed or delegated task;
  - “last completed task” means the most recently completed task;
  - descriptions such as “the wake-word task” search task goals, summaries, questions, and user-visible updates.
- If two plausible tasks remain, ask a descriptive clarification such as “Do you mean the wake-word tuning or the Android assistant work?” Never present or request task IDs.
- Use the same resolver for status questions and follow-up actions such as cancel, retry, answer, or snooze. Mutating actions must not proceed when the reference is ambiguous.
- Keep task IDs in database relations, internal tool results, URLs, traces, and diagnostics only. Never speak them or render them in normal Jarvis responses, task cards, notifications, or clarification questions.
- Add an explicit acceptance requirement that the user can retrieve any active or historical task using natural recency, status, title, or subject references without knowing its ID.

Fast recall:

- Create a unified SQLite recall document table covering:
  - current and closed Jarvis messages;
  - standard and archived conversations;
  - task goals, internal IDs, statuses, questions, user-visible updates, and terminal summaries;
  - approved memory documents.
- Exclude hidden reasoning, secrets, raw provider payloads, and internal-only traces.
- Maintain FTS5 `unicode61` and trigram indexes transactionally and provide an idempotent rebuild command.
- Add a restricted `history_grep` tool whose argument is an `rg`-style command:
  - `-F/--fixed-strings`
  - `-i/--ignore-case`
  - `-w/--word-regexp`
  - `-x/--line-regexp`
  - `-g/--glob`
  - `-A/--after-context`
  - `-B/--before-context`
  - `-C/--context`
  - `-m/--max-count`
  - `--sort relevance|newest|oldest`
- Follow `rg [OPTIONS] PATTERN [PATH...]` syntax with virtual paths such as `conversation/**`, `task/**`, and `memory/**`.
- Parse commands internally without invoking a shell or filesystem `rg`. Bound result counts, scan time, and regex execution.
- Return structured matches containing internal source identity, timestamp, descriptive title, matching text, and requested context.
- Give the Realtime shell direct access for narrow lookups. Delegate broad multi-result synthesis to a background task using the same service.
- Target p95 search below 500ms at 100,000 messages and 10,000 tasks, with narrow voice lookups answered within three seconds.
- The `rg`-shaped interface uses familiar conventions based on OpenAI's documented shell-tool and constrained custom-tool support. [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.2)

Reviewed memory:

- Generate memory proposals from closed Jarvis logs and completed tasks.
- Show their source evidence in the existing Wiki/Knowledge review queue.
- Allow accept, edit, reject, and delete.
- Only approved entries become authoritative memory.

### Phase 4 — Wake-word calibration and address detection

- Add a local calibration wizard for normal, quiet, distant, noisy, and hard-negative samples.
- Store calibration recordings locally with review, export, retrain, and delete controls. Never retain ordinary background audio.
- Retrain and evaluate the wake model using the available GPU.
- Keep a two-stage local gate:
  1. small wake-word model;
  2. direct-address classifier using timing, VAD context, surrounding speech, and on-device transcription when available.
- Remain speaker-agnostic.
- Accept direct commands and reject incidental mentions such as “I was talking about Jarvis.”
- Retain a wake-free follow-up window after accepted interaction.
- Initial gates:
  - at least 95% normal-volume wake recall;
  - at least 90% quiet/distant recall;
  - at most one false wake per evaluation hour;
  - at least 95% address precision and 90% address recall.
- Retain manual Arm whenever automatic wake is unavailable or unreliable.

### Phase 5 — Android 17 native system assistant

- Add an Android 17/API 37 Kotlin application with:
  - `VoiceInteractionService`;
  - separate session service and session UI;
  - Compose assistant overlay;
  - WebView-hosted phone UI;
  - native microphone, wake inference, Realtime audio, notifications, and pairing.
- Request the assistant role through `RoleManager`. Once selected, Jarvis replaces Gemini and can be invoked through supported assist gestures, assistant buttons, or long-press power mappings. Exact gestures remain device-dependent. [Assistant role](https://developer.android.com/reference/androidx/core/role/RoleManagerCompat), [VoiceInteractionService](https://developer.android.com/reference/android/service/voice/VoiceInteractionService)
- Enable keyguard invocation. While locked, allow conversation, reminders, and read-only history/task/memory lookup; block other task creation and mutations.
- Request foreground-app assist structure only after explicit opt-in. Do not request screenshots by default or persist screen context unless explicitly requested. [VoiceInteractionSession](https://developer.android.com/reference/android/service/voice/VoiceInteractionSession)
- Use the selected assistant service as the primary background wake host, with a user-started microphone foreground service as the fallback where required by the device.
- Persist Sleep state. Wake listening after reboot is opt-in.
- Maintain an authenticated resumable event stream for task updates and reminder notifications.
- Add Stop, Sleep, Snooze, and Open Task notification actions. Notifications use descriptive task labels and never expose IDs.
- Probe AppFunctions as a caller for authorized read-only device-state functions. Keep this feature-gated because AppFunctions remains experimental and allowlisted. [AppFunctions](https://developer.android.com/ai/appfunctions)
- Do not build against Gemini's private automation or Android Computer Control, which currently requires an OEM-preloaded privileged assistant. [Android Computer Control](https://developer.android.com/ai/computer-control)

### Phase 6 — Security, handoff, continuity, and release acceptance

- Enforce one active mutable voice controller across browser and Android.
- Pair Android devices using short-lived codes and store credentials in Android Keystore.
- Enforce locked/unlocked capability policy on the backend.
- Record structured diagnostics without raw ambient audio.
- Restore the conversation, focused task, recent task-reference stack, active tasks, pending questions, reminders, voice preference, and event cursors after restart or handoff.
- Complete privacy, battery, migration, recovery, and natural-task-reference acceptance testing before marking the plan complete.

## Public Interfaces and Types

- `GET/PATCH /api/jarvis/preferences`: voice, concision, wake behavior, and Android privacy options.
- `GET /api/jarvis/live?cursor=…`: resumable transcript, task, reminder, and controller events.
- `POST /api/retrieval/history` with `{ "argv": string }`.
- `GET/PATCH /api/memory/proposals`.
- Task projections gain `waiting_until`, `resume_at`, and scheduled-delivery metadata.
- Jarvis continuity state gains an internal focused task and recent task-reference stack.
- Add a read-only natural task resolver shared by Jarvis, history retrieval, notifications, and task actions.
- Jarvis's shell gains natural task lookup, `history_grep`, and `set_jarvis_voice`.
- Task IDs remain available inside trusted tool responses but are explicitly marked internal and non-user-facing.
- Android bridge events cover audio state, wake decisions, takeover, notifications, lock state, and optional assist context.

## Test and Acceptance Plan

- Run complete backend and frontend suites at every phase gate.
- Test streaming order, reconnection, barge-in reconciliation, voice rotation, and concise responses.
- Test natural requests including:
  - “How is that going?”
  - “What is the current task doing?”
  - “What did the last task find?”
  - “What was the last completed task?”
  - “Check the wake-word task.”
  - “Cancel that.”
  - “What tasks are currently running?”
- Test multiple active tasks, stale focus, ambiguous descriptions, restart restoration, device handoff, and mutation refusal after ambiguous references.
- Assert that normal assistant text, speech, notifications, task cards, and clarification prompts never contain raw task IDs.
- Add mobile tests at 320/360/390/430px for navigation, sheets, safe areas, keyboard behavior, streaming scroll, and accessibility.
- Test reminders across restarts, DST, overdue startup, cancellation, snooze, and duplicate scheduler runs.
- Test every grep option, escaping, virtual paths, regex limits, injection attempts, rebuilds, and scale targets.
- Evaluate wake/address models on held-out quiet, distant, noisy, incidental-mention, media, and multi-speaker samples.
- Test Android assistant selection, gesture invocation, keyguard, screen-off listening, notifications, process death, network loss, battery drain, and credential revocation.
- Verify locked-device requests cannot create general tasks or perform mutations; reminders remain the explicit exception.

## Assumptions and Defaults

- The user is never expected to copy, remember, say, or hear a task ID.
- Natural references and descriptive clarification are mandatory across voice, text, notifications, and mobile UI.
- Android 17/API 37 is the only native target.
- Jarvis replaces Gemini as the selected assistant.
- The web/PWA remains foreground-only; native Android provides background listening.
- The PC must be online for conversation, recall, tasks, and reminders.
- Recall uses local SQLite text indexes only; no embeddings or cloud indexing.
- Wake/address detection is local and speaker-agnostic.
- Screen context is opt-in and screenshots are off by default.
- Inactive reminders notify but do not speak.
- Recurring reminders, daily briefings, external integrations, autonomous cross-app mutation, and a fully offline phone agent remain future work.
