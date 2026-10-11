# Personal phone setup and troubleshooting

**M11 personal use was accepted on 2026-10-11**, including working tasks and
screenshot-based recipe creation. See the [acceptance record](reports/personal-use-accepted-2026-10-11.md).
The checks below are optional regression/troubleshooting steps, not outstanding
completion requirements. Earlier automated reports and accepted M8 caveats keep
their recorded scope. Install updates over the linked app to preserve pairing.

## Start and install

1. On the Windows host, double-click `Jarvis.cmd`. Wait for **Ready**.
   Terminal alternatives: `Jarvis.cmd Start`, `Jarvis.cmd Status`, `Jarvis.cmd Stop`.
2. Keep Tailscale connected on the host and phone. Open
   [Jarvis](https://sarah.tail68fb8f.ts.net).
3. Install [Jarvis.apk](https://sarah.tail68fb8f.ts.net/api/jarvis/devices/android.apk)
   on Android 12 or later. Allow this installation source when Android asks.
4. In Jarvis Settings choose **Link phone**. Scan its QR using the system camera,
   or open the link on the phone. Tap **Open Jarvis**; pairing starts automatically.
   The code expires after five minutes and works once; generate another if needed.
   Manual server/code entry is available.
5. Follow Android's microphone/notification and assistant prompts. Supported
   wake, restart after reboot and screen context are enabled together. If Android
   has disabled screen access, setup opens its system settings. Open Jarvis;
   no separate app password or repeated pairing is required.

Already installed and linked: install this APK **over the existing app**, open
Jarvis and select **Finish setup** if it is shown. Keep its app data and existing pairing.
The main screen and phone Settings show **This phone is linked**; voice controls are also available on the main screen, and unlink
is under **Advanced settings**. Retry voice after setup completes.

## Optional physical regression checks

- From the paused notification outside Jarvis, tap **Listen now**. Confirm
  “Live conversation active” and talk while Chrome is foreground. Close any
  assistant panel and confirm the conversation continues.
- Tap **Stop Live**. Confirm microphone capture/playback stop and the status
  becomes “Microphone off”; an already admitted task should still finish.
- Tap **Hey Jarvis**. Confirm waiting, then say the wake phrase. A naturally
  ended conversation should return to your previous paused/wake choice.
- Dismiss controls: they should stay dismissed until you open Jarvis or
  explicitly use its voice controls. Try Android system Stop; reopening must
  never automatically start a paid Live conversation.
- Lock/unlock while Live runs: audio follows Android/OEM behavior, but
  screenshots while screen-off/locked are unavailable. Try a background screen
  task after unlocking and confirm both original/current context are useful.
- Briefly disconnect Tailscale: failed Live should release audio; reconnect
  and start deliberately. Revoke microphone permission, then use notification
  repair to reopen setup. If testing unlinking, do it last: controls should
  disappear, and pairing will be needed again afterward.
- Record phone model/Android version and APK identity from the final report.
- Ordinary task in your voice, then a screen-based task: correct request,
  screen context and useful result. Try ordinary and fresh-screen same-task follow-ups.
- Tap a completion notification, reopen/save the result and request a change
  on the same task. Cancel active work and confirm actual stopped state.
- Background and lock/unlock: notifications, selected wake behavior and screen
  privacy. Note obvious heat/drain/OEM termination over a short idle interval;
  record protected-screen observations or mark them unavailable.
- Disconnect/reconnect Tailscale, restart the app and restart the host:
  pairing, history and results survive without duplicate tasks or another login.
  Check reboot wake behavior subject to Android/OEM background restrictions.
- When troubleshooting, record the exact failed step/time. Diagnose
  using local logs before repeating paid tests.

## Recovery and known limits

An expired link needs a new link. A revoked device or cleared Android app data
needs pairing again. Settings shows linked devices with **Unlink**; Android also
has **Unlink phone**. A failed connection should be retried after the host/Tailscale
returns. Existing signing secret, subscription login and phone credentials are
preserved through ordinary updates/restarts.

The selected trusted Windows command mode inherits the Windows user's access;
file edits remain broker-constrained and descendant cancellation/leases remain
active. M8 speech/provider recognition, full speech/cold/capture-performance
cohorts and physical behavior retain the previously accepted caveats. This short
phone session does not prove all-day battery life. Real connectors and public or
multi-user deployment remain separate work.

Private local logs: `.cockpit/jarvis/logs/`. Never paste `.env`, permanent device
credentials or provider authentication into chat. A full dev-data backup is optional.

Updated APK SHA256: `c82f30ee093b8a1d33fdfcb325c89f704232a55eb9e355a621c8630f0755627b` (164,234,855 bytes).
