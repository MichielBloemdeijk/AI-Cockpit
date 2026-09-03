# Jarvis Product Gaps

Status: Active recovery backlog
Last updated: 2026-09-03

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
