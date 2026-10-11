# Persistent Android controls — 2026-10-11

Implemented the [approved audio controls](../11-android-audio-controls.md). One
native microphone foreground service owns wake and Live; notification, assistant
and exact-origin main-frame WebView controls share it. Browser-owned Android
WebView audio and its lease/wake handover are removed. Ordinary browsers retain
their existing voice path. Paused keeps an ordinary ongoing notification and no
microphone foreground service. Closing panels leaves Live running. Background
tasks and result notifications continue independently.

## Candidate and delivery

Frozen executable source digest: `df2c5a28a391dc4f7afac744faa196b5693ef7ab00f7a555af92fd00b33a34dc`.
APK SHA256: `c82f30ee093b8a1d33fdfcb325c89f704232a55eb9e355a621c8630f0755627b` (164,234,855 bytes).
Host restarted Ready; its authenticated runtime candidate matches the local
candidate, and the private APK endpoint serves this exact-size artifact.
Install over the current app without clearing data to preserve encrypted pairing.
Only disposable emulator app data/test databases were reset. No physical phone
was changed. Report-only documentation does not change executable identity.

## Observed verification

- Android: **38 unit tests passed**, debug app/test APK builds passed.
- Frontend: **32 voice/bridge tests passed**, TypeScript and production build passed.
- Backend: **57 targeted tests passed** using the shared capture_privacy profile;
  capture, images, worker subprocesses/history/publication, unlink and auth.
- [Final audio emulator report](../../../data/android-audio-controls/final-2/report.json):
  **18 of 18 checks passed** on API37, with actual JavaAudioDeviceModule capture,
  native WebRTC RTP, playback from a local Chromium peer and simulated provider.
  At the Chrome checkpoint the peer received 707 packets and sent 706.
- [Final privacy report](../../../data/android-audio-controls/privacy-final/report.json):
  **11 native checks and 18 backend contracts passed**, including protected,
  disabled, denied and locked capture and invocation/latest session binding.

The audio run covered actual notification Listen/Stop/Hey actions outside Jarvis,
pausing with no service, one recorder, panel/WebView closure, main-frame origin
restrictions, repeated Listen, background invocation/latest uploads, screen-off
privacy, immediate local stop and exact backend close, notification dismissal,
worker completion and same-task restart after Stop, stale actual Stop PendingIntent,
natural-end idle recovery, connection loss with no Live retry, process death with
wake/pause persistence and no Live restart, interrupted startup, missing microphone
permission repair and both host/local unlinking. The worker read two images and
history, published a typed recipe, restarted in the same task and replayed the
publication without creating a second result.

The [ordinary browser capture report](../../../data/android-audio-controls/browser-final/report.json)
also passed **6 of 6 cases**, including Chrome invocation/latest screenshots,
run-bound upload, real worker image/history reads and recipe publication, fresh
screen replay deduplication and unchanged Android capture policy.

The [saved-result delivery report](../../../data/android-audio-controls/results-final-2.json)
passed real result notification opening, cookie loss/device-scoped return,
saving, force-stop/reopen persistence, task backlinks, external browser navigation
and reopening without another execution. A preceding run stopped when the test
reopened Jarvis during Chrome’s no-display launcher handoff; the runner now waits
for the actual external page. Its failed evidence is retained. This runner-only
correction changes candidate identity; privacy/browser/unit observations above
were collected on the identical production code/APK before that correction.

## Repairs discovered during verification

Local-peer ICE needed emulator NAT handling in the debug harness. SystemUI tests
needed the actual controls row rather than task notifications and bounded
interactive-window observation. Production fixes include early session binding
for screenshots, fenced callback watchdogs starting after the visible capture
request, an explicit callback_timeout unavailable reason, screen-off privacy on
phones without a PIN, and a proactive missing-permission notification. Failed
pilots remain in `data/android-audio-controls/pilot-*`; they are not counted as
passes. The final repeated audio run includes the dismissal and permission corrections
and matches the delivered host source snapshot after the runner correction.

## Limits and remaining confirmation

**Zero paid model calls** across this feature's tests. Local capture/playback
proves audio lifetime and transport, not provider speech recognition, physical
wake accuracy, OEM battery policy or new admission p95. Worker research and recipe
choices use protocol fixtures. Existing M8 recognition/startup/full-cohort caveats
and historical M9/M10 provider evidence remain unchanged. Final physical M11D
confirmation is the remaining user step; use the [updated phone checklist](../phone-test-checklist.md).
