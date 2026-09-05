# Jarvis Product Gaps

Status: Active recovery backlog
Last updated: 2026-09-05

This document is the acceptance record for user-visible Jarvis shortcomings. A
feature is not considered complete merely because its code path or automated
tests exist; it remains open until the real interaction behaves correctly.

## Open acceptance gaps

### Reliable reminders and time

- The authoritative-time, reminder-routing, contradiction-checking, and durable
  delivery changes pass automated tests but still need end-to-end phone
  acceptance with real Realtime audio and notification timing.

### Situation awareness and task truth

- The authoritative situation projection and component-health state pass
  automated tests. Natural recall and truthful answers after real worker,
  scheduler, and session failures still need conversational acceptance testing.

### Conversation style

- Jarvis remains too verbose and too enthusiastic for routine interactions.
- The current acceptance target is a calm, collected, low-energy personal aide
  that consumes as little of the user's time and attention as possible. Every
  sentence must answer, clarify, warn, or confirm something necessary.
- Prompt and regression-corpus changes are implemented, but subjective vocal
  delivery and concision still require repeated real-session evaluation.

### Realtime latency and connection reliability

- Recent browser sessions sometimes took up to ten seconds to connect after the
  wake word, missed turns, or remained stuck on speaking/thinking after the
  floor had changed.
- The remediation is implemented and covered by automated tests: correlated
  browser/backend/provider timing traces, incremental task projection, a
  terminal-state response gate with watchdog recovery, explicit barge-in,
  non-blocking provider event ingestion, medium semantic VAD, bounded dynamic
  context, six-turn hydration, and background cleanup of superseded sessions.
- Real Pixel-browser measurements are still required to confirm the improvement
  under microphone, network, certificate, and playback conditions.

## Latest reminder incident

The 2026-09-02 production log provides the regression scenario:

1. At 22:16:15 local time, the user requested a reminder one minute from now.
2. Jarvis asked for the current time instead of using trusted system context.
3. At 22:16:32, the reminder was stored for 22:17:00, only 28 seconds later.
4. The scheduler completed it at 22:17:00.16, so the scheduler itself was on
   time for the incorrect stored instant.
5. The first proactive response repeated the old confirmation: "Queued. You'll
   get a reminder at 22:17."
6. A retry said "Reminder: Reminder" but also said it was queued. The phrase
   matcher nevertheless marked the update spoken.
7. A second explicit reminder request was sent through generic task dispatch.
   The task had no due time and remained queued.
8. The background worker's last heartbeat was already stale, but Jarvis was not
   given that health state and promised an update anyway.

## Implemented, awaiting device acceptance

- Realtime timing metadata is retained for seven days and correlates wake
  detection, call creation, provider responses, transcript/audio events, and
  response-gate timings without storing raw audio.
- A cancelled or failed response now emits a terminal event even when no text
  was generated. Late terminal events cannot release a newer response gate, and
  a watchdog cancels or closes genuinely stalled generations.
- Transcript deltas are coalesced to at most ten updates per second without
  delaying a lone delta, keeping streaming responsive while preventing database
  work from blocking the provider socket reader.
- New connections no longer wait for the previous provider call to hang up.
  Realtime receives stable session instructions once and a bounded, fresh
  situation only at response creation.
- Web and Android health views now identify each degraded runtime component and
  show its concise recorded reason instead of only showing a count.

- Technical task errors remain available in durable history and explicit
  diagnostic task lookups, while unsolicited speech, situation context, and
  notifications receive only a short classified explanation. Verify that a
  normal failure says only what failed and why, while “why exactly?” can still
  retrieve the underlying diagnostic.
- Jarvis now has an explicit least-interruption behavior contract and calm,
  even-paced vocal direction. It has no hard response token cap; explicitly
  requested long answers must finish naturally.

- User and assistant transcript deltas are streamed through resumable live
  events and rendered provisionally in the Jarvis page. Verify ordering,
  replacement, and interruption behavior on the Pixel browser and Android app.
- Provisional transcript bubbles are now reconciled against durable messages
  created after that provider item began, preventing the reminder-completion
  race from briefly rendering the same message twice. Verify on the Pixel.
- The phone layout uses a compact arm/sleep toggle and reduced conversation
  height. Verify that the listening banner, keyboard, composer, and safe-area
  insets require no page scrolling on the Pixel.
- Android consumes `notification.available` and acknowledges open/snooze
  actions. Verify one notification, the correct task deep link, and no duplicate
  notification from raw task events.
- The Android API 37 build succeeds. Assistant-role, lock-screen, screen-off,
  process-death, network-loss, and battery behavior still require physical-device
  acceptance.

## User input still required

- Manual recall and false-positive measurements for the new recall-first wake gate.
- Final Pixel-browser and Android acceptance for reminder timing, audible
  delivery, streaming, mobile layout, notifications, and subjective tone.

All architecture, backend, web, Android, migration, and automated-test work can
continue without additional user input.
