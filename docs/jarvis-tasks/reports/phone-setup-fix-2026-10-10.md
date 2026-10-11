# Physical phone setup repairs — 2026-10-10

The first M11D phone trial exposed two application issues. Backend access logs
show successful device-session renewal and authenticated task/voice reads, but
phone Settings requested owner-only device, worker-tool and model settings and
offered an owner-only pairing-code action. Those requests correctly returned
401; the existing phone credential was still valid.

Saved Live telemetry records six `Could not start audio source` failures between
08:03:47 and 08:04:13 UTC. These occur before microphone readiness or a provider
connection request. This is local audio startup, not a selector or worker error.
The same `NotReadableError` was reproduced with a real emulator microphone
after native wake had already released it. The APK was missing
`MODIFY_AUDIO_SETTINGS`, also declared by [Chromium's WebView shell](https://chromium.googlesource.com/chromium/src/+/lkgr/android_webview/test/shell/AndroidManifest.xml).
Adding it made the real audio fixture pass. Code inspection also found
uncoordinated native wake/WebView ownership; an explicit handover removes that
contention. Physical hardware still needs the updated APK retest.

## Changes

- Authentication status exposes safe owner/device scope and the current device
  label. Phone Settings displays its persistent pairing and avoids owner controls
  and their unauthorized requests. Device permissions remain scoped and revocable.
- Opening a valid pairing link pairs automatically and starts guided setup.
  One **Finish setup** action handles existing incomplete setups. Supported
  microphone/notification/assistant/screen context, wake and boot preferences
  are enabled together, subject to Android's genuine consent/settings controls.
  Main setup shows paired, connection and actual native listening status.
  Pause, permission review and unlink are under advanced settings. The unavailable
  AppFunctions reflection probe was removed, rather than presented as a working feature.
- The APK declares the missing normal audio-settings permission, with no new
  runtime permission dialog. A WebView microphone lease stops native wake capture and waits for actual
  recorder release before granting same-origin audio permission. Closing the
  WebView restores native wake unless paused. Missing Android microphone
  permission can be requested from the WebView; foreign origins remain denied.
  Recorder failures become a visible unavailable status rather than an uncaught
  coroutine error. Saved setup resumes wake when the app is reopened.
- Telemetry distinguishes microphone request/readiness and startup error name;
  wake startup/failure metadata is no longer discarded. No audio recordings,
  credentials or new spending system were added.

## Targeted verification

Eight backend personal/auth tests pass. All 193 frontend tests pass, including
phone Settings making no owner settings requests. Frontend production/type build
and Android build/30 unit tests pass. Real Chrome personal-link verification
passes QR/link handling, device status, no unauthorized Settings requests and
revocation. API37 result acceptance passes automatic link persistence/reopen,
cookie renewal, notification navigation, saving and external source navigation.
These runs call zero models.

The final real WebView microphone fixture passes in
`data/phone-setup-fix/microphone/report.json` on APK
`8dcadef7a7ee450ee2d0cb58df820bb5532521f70f60ef50df2d35a247d899e0`.
It verifies automatic pairing/defaults, retaining wake on pages without audio,
native release before web permission grant, a live microphone track and running
audio context, native wake resuming after close, foreign-origin denial and saved
pairing when a consumed link is reopened. Android's actual microphone is used;
this is not speech recognition or a provider request.

Failed attempts are retained: missing emulator Tailscale DNS; an incomplete
automatic audio fixture; the exact reproduced missing-permission error; and a
502 when a final test overlapped the host rebuild/restart. The fixture now uses a
real tap for browser audio, private DNS without disabling HTTPS verification,
and captures audio/platform diagnostics before cleanup. A subsequent stable-host
run passes every assertion. No paid live calls or generated speech were used.

Host/frontend rebuilt and restarted, retaining phone credentials and application
history. Final browser verification also passes on the running host. APK size is
164,234,855 bytes. Current source identity is recorded in
`data/phone-setup-fix/candidate.json`; these are targeted checks, not a new combined
acceptance pass.

## Acceptance boundary and phone update

The fifteen-gate combined pass on source4f58a725 remains historical evidence.
This changed executable candidate has targeted regression checks; no fresh full
provider corpus or paid live request is claimed. M11D remains open until the
updated APK works on the user's phone. Install the APK over the existing app,
keep its saved credential, select **Finish setup**, accept Android's remaining
prompts and retry voice. No data reset or new subscription login is required.
