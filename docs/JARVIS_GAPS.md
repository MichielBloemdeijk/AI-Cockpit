# Jarvis Product Gaps

Status: Active recovery backlog
Last updated: 2026-09-02

This document is the acceptance record for user-visible Jarvis shortcomings. A
feature is not considered complete merely because its code path or automated
tests exist; it remains open until the real interaction behaves correctly.

## Critical open gaps

### Reliable reminders and time

- Jarvis has no trusted device timezone or current-time context in the voice
  contract, so it may ask the user for the time or calculate relative reminders
  incorrectly.
- Reminder intent can be routed into the generic task queue instead of the
  deterministic reminder scheduler.
- A due reminder can trigger a voice response that repeats stale queue language.
- Delivery is currently accepted when a required phrase appears anywhere in the
  transcript, even when surrounding words contradict the fact.
- Jarvis does not consistently know whether an update was spoken, opened as a
  notification, or is still owed.

### Situation awareness and task truth

- The model-facing task snapshot omits `waiting_until` reminders and recent
  terminal tasks.
- Worker and scheduler health are not included in Jarvis's response context.
- A task may remain described as queued after its worker has gone offline.
- Old dialogue and tool outputs can outweigh newer durable state in a Realtime
  response.
- Historical conversation recall exists, but current task state, recent events,
  and outstanding communication are not projected as one authoritative view.

### Conversation style

- Jarvis remains too verbose and too enthusiastic for routine interactions.
- It uses filler such as acknowledgements, process narration, repeated requests,
  and generic offers of further help when a shorter response would suffice.
- The target is a calm, understated butler: polite, direct, and normally one
  short sentence.

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

- User and assistant transcript deltas are streamed through resumable live
  events and rendered provisionally in the Jarvis page. Verify ordering,
  replacement, and interruption behavior on the Pixel browser and Android app.
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

- Wake-word calibration recordings and held-out negative recordings.
- Final Pixel-browser and Android acceptance for reminder timing, audible
  delivery, streaming, mobile layout, notifications, and subjective tone.

All architecture, backend, web, Android, migration, and automated-test work can
continue without additional user input.
