# Jarvis Android companion

Jarvis targets Android 17/API 37. The native companion owns system-assistant invocation, local wake detection, GPT-Live audio, task/reminder notifications, and the phone WebView. AI Cockpit remains the durable source of truth.

Personal phone use is **accepted as of 2026-10-11**, including working tasks and screenshot-based recipe creation. See the [completion ledger](../docs/jarvis-tasks/00-implementation-roadmap.md). The tests below are maintenance tools; accepted M8 and OEM/battery limits retain their recorded scope.

## Agent-run capture feasibility

From `backend`, run `uv run python ../android/scripts/verify-capture-feasibility.py`.
This API-37 probe exercises accepted-wake invocation, foreground A/fresh B,
protected content, separate Android setting/app-op denial and secure lock.
It uses real native callbacks and private run-bound uploads without a model or
phone. It explicitly enables/restores the debug context preference and temporary
test PIN. Evidence and failures are saved in a unique temp folder; services are
cleaned up on exit. See the [pre-M3 report](../docs/jarvis-tasks/reports/pre-m3-feasibility.md)
for historical capture feasibility; current completion is recorded in the ledger.

## Build

1. Install Android Studio or the Android command-line SDK with API 37.0 and Build Tools 36.
2. Use Android Studio's bundled JDK, or JDK 17 or newer. The committed toolchain is verified with JDK 21 and Android Studio's bundled JDK 25.
3. Set `ANDROID_HOME` or add `sdk.dir=...` to an untracked `local.properties` file.
4. Run `./gradlew testDebugUnitTest assembleDebug` (or `gradlew.bat` on Windows).

The wake models are read directly from `frontend/public/jarvis-wake`, so build the app from this monorepo rather than copying the Android directory in isolation.

## Pair and enable

1. Start AI Cockpit on an address reachable by the phone.
2. In AI Cockpit settings, choose **Link phone** and scan/open its five-minute link.
3. Tap **Open Jarvis**. The link pairs automatically; manual address/code entry has one **Set up Jarvis** action.
4. Follow Android's prompts for Nearby devices (Android 17), microphone, notifications and assistant selection. Setup enables supported wake, boot restart and assist context together. If Android screen access is disabled, setup opens its system settings.
5. Already linked phones show the retained link and connection/listening status. **Finish setup** repairs incomplete permission steps; pause and unlink stay under **Advanced settings**.
6. Open Jarvis/results with the saved device identity; WebView sessions renew without an owner password.

See the [final phone checklist](../docs/jarvis-tasks/phone-test-checklist.md). The installable build supports Android 12+ (minimum API 31); automated acceptance uses API 37.

Device credentials are encrypted with Android Keystore. Background audio is evaluated locally and is never persisted. Guided setup enables foreground-app assist structure and wake/boot preferences. Unlocked assistant invocations request screenshots; Android's screen-context consent and protected-content controls remain authoritative. Images stay in process memory for up to 90 seconds and upload only for an admitted screen workflow. Mutating requests are blocked by the server while the device is locked. One native microphone service owns wake and Live. WebView and assistant controls use that owner; closing panels or viewing results does not stop Live. The quiet notification offers Listen now, Stop Live and Hey Jarvis according to actual state.

## Agent-run emulator verification

Install `system-images;android-37.0;google_apis;x86_64` (verified revision 6).
The Windows harness creates or boots `JarvisM3Api37` with 4 GB RAM, builds the
APKs, starts disposable backend/frontend/worker services, provisions the
assistant, and exercises native screenshot capture, private upload, real Codex
execution, notification navigation and result reconnect:

```powershell
cd backend
uv run python ../android/scripts/verify-screen-workflow.py
```

The API-37 fixture invokes the system assistant key. Android 17 restricts
background self-triggered capture, so this passing fixture does not yet verify
screen capture from the custom local wake detector. The supported wake
integration, fresh/protected native cases, and physical/provider timing remain
tracked in the task roadmap. To rerun the older API-36 fixture, set
`JARVIS_TEST_AVD=JarvisM2Full` and
`JARVIS_TEST_IMAGE=system-images;android-36;default;x86_64`.

Add `--orchestration` to the harness command to test M3 with the already-
authorized API credential. It starts a separate restricted decision profile on
the disposable backend, verifies a context answer and a presentation-policy
change that retains an unanswered question, opens both task histories in the
real WebView, and force-stops/reopens without creating another run or decision.
The harness also verifies token telemetry and preserves redacted evidence.
This is an opt-in billed test, not production enablement or physical/audio
acceptance. No interactive user input is required.

M4 adds two selectable examples to the same fixture, without changing the
production capture protocol:

```powershell
cd backend
uv run python -m app.jarvis_tasks android demo --workflow recipe_from_screen@1 --report data/m4-android-recipe.json
uv run python -m app.jarvis_tasks android demo --workflow research_screen@1 --report data/m4-android-research.json
```

These opt-in runs use a permitted menu fixture, actual Android screenshot
pixels, real guarded Codex and separately billed research. Both passed API37
notification/WebView/reconnect acceptance on 2026-10-01. Reports separate capture,
admission, upload, queue and execution timing. Fake Live admission is not physical
audio/provider/custom-wake sign-off; production flags and data remain untouched.

## Agent-run M5 production result acceptance

```powershell
cd backend
uv run python ../android/scripts/verify-result-platform.py --report ../docs/jarvis-tasks/reports/android-m5-api37-current.json
```

This model-free Windows/API37 harness builds the actual static companion and
debug APK, uses a disposable password-backed API, and drives native UI through
ADB/UIAutomator. It tests a future result ID, real wrong/correct login, cookie
loss followed by the actual **Open result** notification action, no completion
auto-launch, saved state and login after force-stop, task/result backlinks,
external system-browser intent and unchanged result/attempt counts on reopen.
Open receipts use the paired device credential; subscription management remains
cookie-only. No login bypass, browser debugging port, paid calls or production
enablement are needed. Debug pairing controls and the fixture publication API
are absent from the release app/production backend.

The installed AVD/image and frontend dependencies are required. The harness
waits for the emulator network as well as boot completion and uses a bounded
read-only hierarchy reader in the separate test APK. This avoids SystemUI's
global-idle timeout without restarting Jarvis/Chrome or reading stale XML; it
masks password fields. It retains XML/logs/a final PNG/report in its
printed temporary directory. Default cleanup stops only services/emulator trees
it starts. `--keep-emulator` is available for iterative debugging; an already
running test AVD is reused without being stopped. This is result-delivery
acceptance, not new capture, provider, physical speech or OEM sign-off.

## M8 prerecorded audio acceptance

The debug APK has an `AudioAcceptanceReceiver` input seam before production
wake inference and WebRTC input buffers. It requires a debuggable app, private
bounded mono S16LE/16 kHz samples and an explicit sample identity. Release APKs
have no receiver and reject installation. Real outbound RTP, assistant playback,
durable admission identities and capture timings are logged as metadata.

Render the fixed `scripts/audio-corpus-v1.json` with
`scripts/generate-audio-corpus.ps1 -OutputDirectory <fresh-folder>`. Preparation
rejects empty/invalid WAVs; this host currently produces empty speech output.
For an authorized API render, use `scripts/generate-api-audio-corpus.py --live
--output <fresh-folder>` from the backend environment. It uses the existing API
credential, a pinned speech model and two synthetic voices, records request IDs
and audio hashes, and performs no automatic retries. This is a billable test run.
The emulator harness transfers generated audio with `adb push`, copies it into
private app storage, removes the staging file and verifies its checksum before
feeding the input seam.
Run `scripts/verify-audio-corpus.py --live --serial emulator-5554 --render-dir
<folder> --database <isolated-backend.db> --backend-url <loopback-origin>
--report <fresh-folder>` from the backend
environment against an already paired API37 emulator and authorized Live backend.
The backend's authenticated startup candidate and database identity must match
the CLI's actual configuration. Run both with the same explicit environment.
For backend authentication, provide `JARVIS_ACCEPTANCE_DEVICE_TOKEN` or
`JARVIS_ACCEPTANCE_PASSWORD`; a passwordless isolated backend uses its normal
login flow. Credentials are never written to evidence. The same backend instance
must remain alive, with unchanged source/configuration, through the final check.
The composed acceptance CLI requires `--backend-url` with its emulator options.
Add `--prepare-scenarios` only for the project-owned isolated database under
`data`, with its worker pool stopped. This creates normal task-service
prerequisites for spoken cancellation, question answers and same-task follow-ups.
The harness also runs ambiguity, nonmatch and separate-outcome audio. Fresh screen
testing requires actual initial recipe admission and spoken clarification before
changing the foreground fixture to B; command-only audio continues the same ready
Live activation through WebRTC. No voice calls, transcripts or pixels are injected.
`--prerequisites` can instead provide persisted task/question/run identities.
Missing prerequisites, failed corpus cases and changed candidates cannot pass.

Use the same command with `--capture-performance` on the prepared isolated
backend to collect twenty screenshot-disabled baselines, twenty activation
uploads and twenty same-session fresh continuations. It uses actual custom wake,
RTP, playback and foreground screenshots. The emulator's Android screenshot
setting is restored and checked afterward. `--performance-samples 1` is a short
diagnostic; fewer than twenty repetitions cannot pass the performance gate.
Raw intervals and explicit health checkpoints are saved separately. Debug
collection reads the pinned WebRTC playout track's underrun count, Android
process/system ANR observations and assistant-window frame deadlines. Missing
counters, no frames or dropped frame observations block the gate. Counter reads
run away from the microphone/screenshot paths and hold no audio-input lock.
First-draw frames and overruns are reported separately from later-frame jank,
as recommended by Android's [FrameMetrics reference](https://developer.android.com/reference/android/view/FrameMetrics).
At least one later frame and complete observations are required in every sample.
See [current progress](../docs/jarvis-tasks/reports/m6-m10-progress.md) for the
historical scenario, performance and account/provider observations.

## Persistent native audio controls (2026-10-11)

See [audio controls](../docs/jarvis-tasks/11-android-audio-controls.md) and
[verification report](../docs/jarvis-tasks/reports/android-audio-controls-2026-10-11.md).
Build with `gradlew.bat :app:testDebugUnitTest :app:assembleDebug :screen-fixture:assembleDebug`.
From `backend`, run `.venv/Scripts/python.exe ../android/scripts/verify-audio-controls.py --report ../data/audio-controls-unique`.
This uses actual native WebRTC capture/playback with a local peer and simulated
backend provider, with zero paid calls. It tests notification actions in Chrome,
shared WebView ownership, plural screenshots, independent worker completion,
stale Stop actions, interruption, recovery, permission loss and unlinking.
Paused has no microphone service. Browser microphone ownership/lease handover
has been removed from the Android WebView; ordinary external browsers retain it.
Install the APK over the existing app to keep pairing. Physical phone acceptance
and provider recognition remain separate from this local transport proof.
