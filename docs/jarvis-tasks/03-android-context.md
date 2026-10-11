# Phase 3 — Android Screen Context Capture

Status: completed for the accepted personal-use scope on 2026-10-11. The
[completion ledger](00-implementation-roadmap.md) owns status and accepted
limits; this document retains the phase contract and historical evidence.

## Completion

This phase is complete under the [final personal-use acceptance](reports/personal-use-accepted-2026-10-11.md).
The [reports index](reports/README.md) retains candidate-scoped verification,
failed observations and the accepted M8 caveats. Historical implementation
notes below do not assign new work or override the completion ledger.

## 1. Summary

Capture the screen automatically for every unlocked Jarvis activation, without adding a Jarvis privacy setting or delaying Live audio. The Android assistant screenshot callback becomes the only production capture source.

Each root activation gets a durable `activation_id`. Its screenshot remains only in Android process memory for up to 90 seconds unless a workflow requests it. A task-consumed screenshot is uploaded, retained privately as a durable task artifact, and may produce a searchable, workflow-validated visual summary.

Send capture-status metadata to Jarvis as soon as it changes. A screenshot-dependent task is admitted only after its selected image is captured and ready to upload; pending capture gets at most two additional seconds before admission is rejected. Capture or upload errors are not automatically retried. Jarvis retains the request/failure context and decides whether to try again.

These capture gates apply to an execution run that needs newly captured screen context. Follow-ups are handled by the general persistent task session: a new run may reuse its authorised retained image, analysis, and evidence without an active phone session or new screenshot. The same task ID can therefore have several independently bound context requests across its runs.

GPT Live can choose the original activation screenshot or request a fresh screenshot after clarification. Android officially supports requesting foreground-app screenshots when showing a voice session, while the callback may return `null` because of user settings, application policy, or protected content. See [VoiceInteractionService](https://developer.android.com/reference/android/service/voice/VoiceInteractionService) and [VoiceInteractionSession](https://developer.android.com/reference/android/service/voice/VoiceInteractionSession).

## 2. Key Implementation Changes

### 2.1 Android capture lifecycle

- Stop disabling `SHOW_WITH_SCREENSHOT`; retain the existing independent assist-structure preference.
- Android 17 requires `SHOW_WITH_ASSIST` for screenshot delivery as well as
  screenshot consent. The current implementation still couples that basic
  context to the existing rich-structure preference. Decouple them using the
  platform's separate screen-content controls, with an emulator opt-out
  regression, before enabling the automatic production path. Do not silently
  change the user's stored preference or add a new privacy setting.
- Request a screenshot for every unlocked local-wake activation. System assistant gestures and buttons use the screenshot flag supplied by Android.
- On API 37, background custom-wake requests lose their capture flags. Prove
  and wire a single initial request after the assistant window is actually
  visible; `onShow` alone is not that signal. This is initial capture staging,
  not an automatic retry of a failed image or another Live session.
- For fresh requests, hide the assistant content before requesting context,
  allowing frame commits while retaining the foreground assistant window.
- Assign an `activation_id` to every invocation:
  - preserve the wake detector's existing ID;
  - generate one in `onShow` for system-originated invocations;
  - send it to the backend for every Live session, not only wake-word sessions.
- Expand activation source values beyond `wake_word`; wake score and wake-gate fields become optional and remain valid only for wake activations.
- Have `onHandleScreenshot` immediately transfer the bitmap and monotonic callback time to an application-scoped capture coordinator. Perform no compression, hashing, logging, or networking on the callback thread.
- Normalize screenshots on a background dispatcher:
  - JPEG, quality 88;
  - maximum long edge 2560 pixels;
  - maximum encoded size 5 MiB;
  - SHA-256 over the transmitted bytes;
  - no EXIF or other source metadata.
- Keep the original activation image and turn-bound fresh captures for the root activation until their individual expiry. A newer turn must not replace pixels promised to an earlier admission.
- Never write unused captures to disk, preferences, logs, telemetry, or Android backups.
- Clear an unused capture after 90 seconds, on device lock, process death, or replacement by a new root activation. Hiding the overlay alone does not shorten the 90-second lifetime.
- A successful upload may release its bitmap immediately, while its encoded bytes remain available until expiry for another explicit task from the same activation.
- Record a classified unavailable state when the callback is null. Use `getUserDisabledShowContext()` to distinguish Android's global screenshot setting where possible; otherwise report `platform_or_app_policy` without guessing.
- A non-null bitmap is not necessarily usable: API-37 `FLAG_SECURE` redacts
  app pixels to black while system chrome may remain. Reject black central
  content conservatively before encoding/upload. This also rejects genuinely
  black screens; it is not a reliable general protected-content detector.
- Locked invocations remain read-only: discard any delivered pixels and never upload them.

### 2.2 Capture status and pre-admission readiness

- Maintain capture metadata independently of task context requests, since no task exists during the initial callback. Bind each report to the authenticated device, Live-session generation, root activation, capture ID, capture mode, and a monotonic status revision.
- Report `pending`, `captured`, `unavailable`, or `expired`, with capture time, expiry, and a classified reason where known. `captured` means normalization succeeded and encoded bytes are ready to upload; it does not mean the image has been uploaded or its contents understood. Reject out-of-order reports that would resurrect expired, superseded, or failed captures.
- Include the latest metadata in Live-session initialization when available and propagate subsequent status changes to persisted session/routing context. Capture and audio startup run concurrently; do not delay Live audio waiting for the image.
- Request actual capture at activation, then use callback/status events. Do not periodically capture the background screen to test permission. Android's `getUserDisabledShowContext()` exposes global settings, not a reliable per-screen capturability check. A previous capture result is evidence only for that capture, not a permanent allow/deny classification for the app.
- Before admission of a screen-dependent task, resolve the exact requested activation or fresh capture. If captured and unexpired, proceed. If pending, wait asynchronously for at most two additional seconds outside the database transaction. A known error rejects immediately; expiry, mismatch, or pending status at the deadline also rejects. Rejection creates no task, result shell, or upload request.
- The two-second wait is an exceptional-case failure timeout; include it in end-to-end admission measurements rather than increasing or resetting the two-second performance objective. Revalidate capture binding/expiry and controller generation at admission commit.
- Preserve exact-call idempotency across this preflight. Duplicate in-flight calls share a capture/deadline, and committed admissions replay without rechecking whether the original local image still exists. A deliberate retry after rejection requires a new call identity; it is not a replay of the failed call.
- Only metadata travels before admission. The task-linked context request authorizes image upload afterward. Required-capture errors reject before admission; optional unavailable roles are exposed; context/upload errors after admission fail the task. Never automatically substitute another screen or turn these errors into a wait for replacement context.
- References: [capture callback](https://developer.android.com/reference/android/service/voice/VoiceInteractionSession#onHandleScreenshot(android.graphics.Bitmap)) and [global context setting limitations](https://developer.android.com/reference/android/service/voice/VoiceInteractionSession#getUserDisabledShowContext()).

### 2.3 Invocation and latest capture collection

- On the first user-originated provider delegation, asynchronously request the latest screen while selection runs. Deduplicate by session, generation and delegated-turn correlation. Preserve invocation pixels in process memory alongside every still-valid delegated-turn capture; expire and erase each after 90 seconds and on lock or activation replacement.
- `start_workflow` admits screen work in one selector decision. `screen_context_mode: activation | fresh` chooses the required role: invocation for activation, latest for explicit current-screen requests. Both available roles enter the frozen collection; an unavailable optional role is recorded. There is no standalone fresh-screen selector tool.
- Ordinary `continue_task` calls omit screen context selection and reuse the retained collection. Explicit fresh-screen continuations prepare capture internally using the same originating delegation identity.
- Serialize native refresh requests because Android screenshot callbacks have no request identity. Bind the callback before asynchronous encoding; retain each capture independently so newer speech cannot replace earlier pixels.
- Admission waits only for required capture readiness, never worker analysis. Freeze labeled roles, timestamps, device/session/activation provenance in `task-screen-context.v2`; create one upload record per unique capture.
- Upload distinct captures concurrently after committed admission. Deduplicate identical JPEG bytes in blob storage and worker image delivery while preserving both role labels. Every promised upload must finish before execution; partial failure fails the requesting run.
- Workers inspect both distinct valid images with `task.context.images@2`, select relevant evidence using the user request, and must not silently merge conflicting screens. Earlier uploaded context remains available across restart and ordinary continuation.

### 2.4 Durable context requests and upload

- Add durable context-request records containing:
  - stable task, execution run, and optional workflow-run binding;
  - context kind;
  - capture mode;
  - exact capture ID and readiness-report revision used at admission;
  - activation and Live-session generation;
  - target device;
  - state and failure reason;
  - request, expiry, fulfillment, and consumption timestamps;
  - resulting context-blob and task-artifact references.
- Use states `pending`, `ready`, `unavailable`, and `expired`.
- Create the initial context request in the same workflow-admission transaction as the task, result shell, and waiting-for-upload state, after capture readiness was confirmed. Bind it to the exact capture ID as well as activation/device/generation.
- Emit a replayable `context.requested` event targeted to the controller device. Add target-device filtering to the unified event stream so other paired devices do not receive it.
- Add an authenticated pending-request query. Android calls it on startup and every SSE reconnection, preventing a locally advanced event cursor from permanently losing a request.
- Return a short-lived signed upload token only to the target device through the pending-request response; never persist or log the usable token.
- Add a binary upload endpoint with:
  - paired-device authentication;
  - signed request token;
  - request, device, activation, capture ID, and generation correlation;
  - capture wall-clock and monotonic timestamps;
  - dimensions, MIME type, content length, and SHA-256;
  - streamed 5 MiB enforcement.
- Accept only the planned JPEG representation initially. Validate the decoded image and dimensions server-side with Pillow and reject malformed or decompression-bomb payloads.
- Make upload idempotent:
  - repeating the same completed request and checksum returns the existing context reference;
  - different bytes for a fulfilled request return a conflict;
  - stale, expired, locked, revoked-device, or mismatched requests cannot create artifacts.
- Idempotent handling protects against duplicate delivery; it is not an automatic retry policy. Do not loop or resend image bytes after a reported upload error, and do not recapture automatically. A new attempt is Jarvis's explicit decision.
- Provide an idempotent failure endpoint so Android can report `callback_null`, `android_screenshot_disabled`, `platform_or_app_policy`, `locked`, `activation_replaced`, `session_unavailable`, `encoding_failed`, or `upload_failed`.
- Keep upload-pending tasks unclaimable without a worker or resource reservations. A validated accepted upload makes the task runnable.
- The backend owns failure transitions: an unavailable/expired required context or an upload error fails the admitted execution run and its initial result, emits the classified failure, and leaves the request available for inspection. Update task status only if that run is current. No worker or active voice model is required to finalize failure. A durable request-expiry handler covers device disconnects where a failure report cannot arrive; do not leave an unclaimable run pending indefinitely.
- Order upload acceptance, failure reports, expiry, and cancellation transactionally. A failure report caused by a lost success response must not reverse an already accepted upload; late bytes must not revive a failed/cancelled request or its execution run. Preserve already published content when a later required-context update fails.
- Scope those transitions to the originating execution run. A late upload/failure from an earlier run cannot satisfy or fail a newer follow-up under the same task ID. Retained context is reused only through an explicit authorised run binding, not by accepting an old upload against a new request.
- If the Live session ends after upload, the admitted task and context remain valid.

### 2.5 Private storage and searchable memory

- Add a configured private context-blob directory outside the frontend and static roots.
- Store validated uploads content-addressably by SHA-256 and reuse the blob when multiple task requests upload identical bytes.
- Keep filesystem paths private in a dedicated context-blob record. Expose only an opaque context reference such as `context://<id>`.
- Link every fulfilled request to a standard task artifact containing safe metadata and the opaque reference.
- Retain task-consumed images indefinitely for the initial personal and debugging phase, including images belonging to failed or cancelled tasks. Add no automatic deletion policy in this phase.
- Never embed raw pixels or local paths in task events, prompts, result pages, logs, or searchable text.
- Add an authenticated task-context diagnostic view:
  - metadata listing through task detail;
  - separate image-content endpoint;
  - owner authentication;
  - `Cache-Control: private, no-store`;
  - no public result-page access.
- After a workflow's image-analysis capability validates its description, let the fenced workflow runtime publish one idempotent `task_context_summary` recall document.
- The searchable summary includes visible evidence, uncertainty and confidence, task and context provenance, and capture time. It must not turn inferred hidden ingredients or uncertain identification into factual memory.
- Context summaries are task-derived recall documents, not automatically approved user-preference memories.

## 3. Interfaces and Correlation

- Extend the persisted Live session and API view with `activation_id` and activation source.
- Add correlated capture-status metadata and status revision independently of task-linked upload-request state. Live context distinguishes `captured` on-device from `ready` after backend upload acceptance.
- Extend `JarvisLiveActivationContext` so all invocation sources carry an ID while wake-specific fields remain optional.
- Add:
  - `PUT /api/jarvis/captures/{capture_id}/status` for authenticated, revision-checked device metadata reports without image bytes;
  - `GET /api/jarvis/context-requests/pending`
  - `PUT /api/jarvis/context-requests/{request_id}/content`
  - `POST /api/jarvis/context-requests/{request_id}/unavailable`
  - authenticated task-context metadata and content endpoints.
- Add nullable `target_device_id` to unified Live events. Browser owners may inspect redacted event metadata, but only the target device receives actionable context requests or upload tokens.
- Add telemetry milestones:
  - root activation created;
  - screenshot requested;
  - callback received or null;
  - normalization started and completed;
  - capture status reported and received by routing context;
  - pre-admission capture wait started, satisfied, or timed out;
  - context request committed and delivered;
  - upload started, accepted, or rejected;
  - task made runnable;
  - fresh capture requested and completed;
  - capture expired or replaced.
- Extend Android Live-event handling to timestamp local speech-stop events using the same monotonic clock as the screenshot callback. This allows direct measurement of `capture_callback - user_speech_end`; a negative value is expected when capture finishes while the user is still speaking.
- MediaProjection is not a fallback. Its consent and session requirements conflict with hands-free capture, and the production callback must remain independently testable.

## 4. Autonomous Test Plan

**2026-09-30 implementation checkpoint:** The Windows agent harness now uses
the full `android-37.0;google_apis;x86_64` revision-6 image and passes native
system-gesture capture through real Codex publication, notification navigation,
and process reconnect. It also tests local-network permission denial and
allow-to-pair continuation without user input. See
[API-37 evidence](reports/android-m2-api37-current.json). Fresh screen B,
`FLAG_SECURE`, native lock/expiry cases and production timing remain separate
acceptance work.

Android 17 requires the assistant's `usesAssistData` and
`usesAssistScreenshots` resource declarations; they are now present. Its
background self-trigger restriction also means the custom local wake detector's
`showSession` call does not yet establish permission to capture another app.
The API-37 fixture arms debug correlation, then invokes the real system
assistant key. It does not enable a compatibility bypass. Supported wake
integration and the real device's screen-context consent flow must be verified
before enabling wake-based screen workflows. The subsequent
[pre-M3 report](reports/pre-m3-feasibility.md) proves the accepted-wake invocation
path can show the assistant, followed by real foreground screen A/fresh B
captures under one activation/session. The debug probe explicitly opts into
the existing context preference; it does not bypass Android's capture policy.
It separately verifies `FLAG_SECURE`, Android screenshot disablement,
`READ_SCREEN_CONTEXT` denial and secure keyguard rejection without task creation.
Fresh admission in this feasibility harness calls the shared services directly;
M2's public Live workflow gateway still accepts only activation captures.

- Pure JVM tests use injected clocks, codec, and uploader interfaces to cover:
  - activation correlation;
  - 90-second expiry;
  - initial and fresh slot behavior;
  - replacement and lock clearing;
  - memory limits;
  - checksum and upload replay;
  - null callbacks and classified failures.
  - status transitions before/after Live-session initialization, normalization failure, stale reports, and no background permission-polling capture;
  - no automatic capture/upload retries after errors.
- Backend tests cover:
  - atomic context-request admission;
  - target-device event filtering;
  - pending-request replay;
  - signed-token scope and expiry;
  - device, activation, and generation mismatch;
  - lock and revocation races;
  - size, MIME, checksum, and decoded-image validation;
  - idempotent repeat versus changed-content conflict;
  - private diagnostic authorization;
  - content deduplication;
  - searchable summary provenance.
  - admission rejection without task/result allocation for unavailable/expired/mismatched capture;
  - at-most-two-second asynchronous pending-capture wait, success just before the deadline, and rejection at expiry;
  - committed admission replay after local image expiry and shared preflight for concurrent duplicate calls;
  - post-admission upload failure/request expiry finalizing the task without a worker or active model;
  - upload-acceptance versus failure/cancellation races, including a lost success response;
  - explicit fresh capture before admission and model-initiated retry identities.
  - same-task continuation using retained context without capture, explicit fresh context for a later run, and rejection of late prior-run upload/failure events.
- Add an Android instrumentation fixture app with:
  - deterministic colored and labelled screen A;
  - different screen B for fresh capture;
  - a `FLAG_SECURE` protected screen.
- Provide one CLI harness that an agent can run without browser or computer-control tools. It:
  1. creates or boots a pinned API-37 emulator;
  2. builds and installs Jarvis and the fixture;
  3. provisions Jarvis as the assistant using Android shell role and service commands;
  4. enables the Android system assist-screenshot setting;
  5. pairs against an isolated test backend without UI;
  6. opens fixture A and invokes the real assistant with the system assistant key (`input keyevent 219`); API-37 Google images deny the shell's `cmd voiceinteraction show` permission;
  7. verifies capture-status metadata reaches routing context, then admits a fixture task only after captured/ready-to-upload confirmation;
  8. verifies the uploaded image contains fixture A's markers;
  9. changes to fixture B, requests fresh context, and verifies B;
  10. runs null or protected, lock, expiry, SSE-replay, and duplicate-upload cases.
- The harness must obtain pixels only from `onHandleScreenshot`; ADB screenshots and injected bitmap shortcuts are forbidden.
- Routine tests use the fake Live provider and deterministic workflow admission. A separate opt-in run exercises real GPT Live without changing the Android capture path.
- M8 adds recorded wake/request audio through the production handoff and WebRTC
  input-buffer seam, plus playback observations. Verify one activation/session
  and that no request audio is lost or replayed at cutover. Test-only audio source
  selection is unavailable in release builds; workflow images remain real callbacks.
- The same timing harness can run on a connected physical phone for future
  diagnostics. M11 personal use is accepted; routine agent testing remains
  emulator-based and unattended, with the accepted M8 measurement limits explicit.

## 5. Acceptance Criteria and Assumptions

- Capture is always enabled in Jarvis for unlocked activations; there is no app-specific on or off setting.
- Android's global screenshot control and protected-content policy remain
  authoritative. Null or conservatively detected blank/redacted content is
  unavailable; do not infer a precise protection policy solely from a bitmap.
- `onHandleScreenshot` synchronous work stays below 5 ms p95; image processing never runs on the main thread.
- Enabling screenshot capture adds no more than 150 ms to Live-ready p95 against interleaved capture-disabled starts. Compare normalized frame observations over equal windows; baseline missed frames alone do not fail capture. Missing observations, failed captures, audio underruns and ANRs remain failures or unknowns.
- The initial screenshot callback occurs no later than two seconds after detected user speech end on the ready path; normally it occurs before speech ends.
- A cached activation image begins uploading within 500 ms p95 of context-request delivery on an already connected event stream.
- A screenshot-dependent admission uses the selected capture's ready status; pending capture waits at most two additional seconds and then rejects without allocating a task. This wait is included in admission latency, not hidden in a separate success budget.
- Known capture errors reject immediately; upload errors after admission fail the associated run. Neither path automatically retries or substitutes a new screenshot. Jarvis can explicitly request a retry using valid context.
- The backend finalizes required-context failure even when no worker or Live session is active. Duplicate/late reports cannot reverse an accepted upload or terminal execution run, or affect another run of the task.
- Fresh capture request-to-callback remains below two seconds p95.
- Used images remain private durable task artifacts; validated descriptions become searchable with provenance.
- Unused images exist only in Android process memory and are gone after 90 seconds.
- A new root activation replaces older unused context; a fresh capture within the same activation does not create another Live conversation.
- The control-plane admission and Phase 2 routing contracts are prerequisites for enabling the production workflow path.
## Completion evidence

See the [completion ledger](00-implementation-roadmap.md) and
[reports index](reports/README.md). Earlier progress reports are historical;
M0–M11 are closed under the user's accepted scope and caveats.
