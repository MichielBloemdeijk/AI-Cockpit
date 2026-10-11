# Persistent Android Jarvis controls

The Android audio service owns wake recording, native WebRTC capture/playback and
one quiet notification. Assistant panels and the Jarvis WebView observe this
owner; closing them does not stop audio. Ordinary browsers keep browser voice.

## Controls

- Paused: **Microphone off** — Listen now / Hey Jarvis.
- Waiting: **Waiting for ‘Hey Jarvis’** — Listen now.
- Connecting: **Starting Live…** — Stop Live / Hey Jarvis.
- Live: **Live conversation active** — Stop Live / Hey Jarvis.

Listen now starts one conversation. Stop Live releases capture/playback before
bounded backend cleanup and selects paused. Hey Jarvis ends Live and selects
local wake listening. Natural endings return to the previous idle choice.
Background tasks and task-result notifications are independent.

Paused uses an ordinary ongoing notification with no microphone service.
Missing permission opens setup; failed connections release audio. Tapping the
body opens Jarvis. Dismissal is respected until explicit app opening/control
use. Android system Stop/process death never restores a paid Live conversation;
only the persisted idle choice may resume on explicit opening or supported boot.
Unlinking locally or from the host removes controls and releases audio.

## Ownership and privacy

A serialized transition owner fences asynchronous startup, waits for prior
recorders to release, and deduplicates repeated Listen/Wake taps. Stop intents
carry immutable activation/session/generation identity; old actions cannot
stop replacement sessions. Native WebView messaging allows only listen, stop,
wake and state, from the configured exact origin and main frame. It carries
no credentials, audio or pixels; browser microphone permission is denied there.

Screenshot capture remains separate. A temporary capture-only assistant window
can obtain invocation/latest images while Live continues. Their existing
activation/turn/session/run binding, local expiry and upload promises remain.
Screen-off and keyguard checks reject capture even on devices without a PIN.
Callback timeouts are explicit unavailable observations, never successful images.

## Verification and delivery

Run from `backend`, with installed frontend dependencies and the API37 test AVD:

```powershell
.venv/Scripts/python.exe ../android/scripts/verify-audio-controls.py --report ../data/audio-controls-unique
```

Use a fresh project-owned report folder. This clears only disposable emulator
app data and creates an isolated backend with empty provider keys. A loopback
Chromium WebRTC peer receives real native microphone RTP and sends synthetic
playback. UI actions use actual notification PendingIntents. No model is called.
The debug transport is emulator-only; production provider routing is unchanged.
Capture-to-worker tests use actual worker processes with protocol fixtures.

See the [feature report](reports/android-audio-controls-2026-10-11.md) for observed
results and limits, and the [phone guide](phone-test-checklist.md) for future
troubleshooting. Personal use is [accepted](reports/personal-use-accepted-2026-10-11.md). Install the new APK over the linked app without clearing
its data. The existing encrypted pairing and idle preference remain intact.
