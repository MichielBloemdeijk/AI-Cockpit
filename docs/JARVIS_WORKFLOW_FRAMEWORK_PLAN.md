# Jarvis workflow framework with Android capture and automated demos

Status: agreed scope, saved 2026-09-10. Architecture decisions and implementation sequencing are developed incrementally in [JARVIS_WORKFLOW_IMPLEMENTATION_PLAN.md](JARVIS_WORKFLOW_IMPLEMENTATION_PLAN.md). Saving this plan does not mean the feature has been implemented or its latency targets verified.

## Summary

Build a reusable framework that lets Codex add a workflow, test it against the backend and Android emulator, and produce a reviewable demo before enabling it.

The framework will support immediate screen context, voice acknowledgment during task startup, background execution, editable result pages, and completion notifications. Start with two reference workflows: **research the current screen** and **find a recipe from the current screen**.

Use shared tools for common capabilities. Workflows define how to combine those tools, their task-specific instructions, and their quality checks. Codex may introduce a new tool when needed, but that tool must be separately registered and tested.

No in-app workflow authoring, automatic optimization, or separate Windows/Linux client in this implementation.

## 1. Workflow contracts and execution

- Add a versioned workflow registry. Each workflow package contains its identifier, description and example requests, input/output schemas, required capabilities, implementation, fixtures, and evaluation criteria.
- Use ordinary Python implementations behind a small common interface. Avoid introducing a workflow language, visual editor, or mandatory multi-agent orchestration.
- Provide shared capabilities for screen-context retrieval, multimodal model calls, web search, source reading, artifact creation, and artifact revision. Tools own authentication, timeouts, validation, and common telemetry.
- A workflow can contain domain-specific parsing and validation. New external integrations or device permissions belong in registered tools, rather than being hidden inside workflow code.
- Extend durable tasks with workflow ID/version, structured inputs, context references, and an execution lane. Existing tasks retain their current behavior.
- Add an independently supervised interactive worker alongside the general worker. Scope worker registration and claiming by lane while retaining global resource leases, cancellation, recovery, and idempotency. Start with one interactive execution slot.
- Pin each task to its workflow version. Retries resume that version and cannot duplicate a published result.
- Expose enabled workflow descriptions to the voice model. Unmatched requests continue through the existing general task dispatcher; automatic creation of optimized workflows comes later.

**Reference workflows:** both share capture, retrieval, and publishing. The recipe workflow adds ingredient/instruction validation and recipe presentation. Adding the second workflow must require no changes to voice transport or task scheduling.

## 2. Android screen context and overlapping acknowledgment

- Add a separate screen-context opt-in to the Android app. When enabled and unlocked, request Android's assistant screenshot when Jarvis activates.
- Hold the activation snapshot in memory for up to 30 seconds, associated with that activation. Upload it only when an accepted workflow needs screen context; discard unused snapshots on expiry or session end.
- Use Android's existing `VoiceInteractionSession` screenshot callback. It can return no image when capture is disabled by the user, application, or policy; handle this explicitly. [Android screenshot API](https://developer.android.com/reference/android/service/voice/VoiceInteractionSession#onHandleScreenshot(android.graphics.Bitmap))
- Add authenticated, device-scoped context requests over the existing event stream, with a correlated upload endpoint. Bind snapshots to the requesting device, activation, and task. Never substitute another session's image.
- For the first version, "this screen" means the screen visible at assistant activation. If that snapshot has expired or a later request needs a different screen, ask the user to reopen Jarvis on that screen.
- Add one voice tool, `start_workflow`, carrying the selected workflow, request, context requirement, and optional target artifact. The server creates the durable task and starts context acquisition immediately, then returns acceptance without waiting for upload or research.
- Request a short spoken acknowledgment after acceptance. Capture/upload and worker startup proceed during speech generation and playback.
- Dispatch completed, validated function calls as soon as their completion event is available. Keep `response.done` as a deduplicated fallback; never execute partial arguments.
- Preserve one owner for voice response generation. Change the current silence policy specifically for asynchronous startup: acknowledgment may describe accepted work, but must not claim capture or completion before those events occur.
- Speech interruption stops the acknowledgment, not an already accepted task. Explicit cancellation cancels the task.

This uses Realtime's existing function-call/result mechanism; it does not require replacing GPT-Realtime 2.1. [Realtime function calling](https://developers.openai.com/api/docs/guides/realtime-conversations#function-calling)

## 3. Result pages, research, and updates

- Add a maintained result-page renderer under Apps, backed by typed, versioned content. Start with document sections, lists, source links, and recipe components.
- Publish results at stable URLs without generating application source or running a production frontend build per result.
- Reuse task artifacts for provenance and associate each published result with its originating task. Completion notifications open the result; unsuccessful tasks link to task details.
- Add an artifact revision operation accepting artifact ID, base revision, and requested change. Validate and atomically publish revisions; reject stale edits and retain the previous version.
- Send visible artifact identity from the Cockpit page/WebView into conversation context so follow-up edits target the right result.
- Implement direct multimodal and web-retrieval adapters for workflows. Start with configurable GPT-5.6 Luna for background model calls; keep provider handling outside workflow definitions.
- Bound reference workflows to identification, a search round with parallel source reads, and structured composition. Allow one validation repair attempt. Report unresolved ambiguity or inaccessible sources instead of manufacturing a result.
- Emit progress from actual execution events. Show intermediate updates in the UI; voice defaults to acknowledgment and completion, with existing progress preferences controlling additional speech.

## 4. Codex authoring and end-to-end testing

- Provide a workflow scaffold command, validation command, evaluation runner, and an Android demo command, documented in a repository guide for Codex.
- Scaffold new workflows as disabled candidates. Test environments explicitly enable the candidate version; production activation is a reviewed registry/config change.
- Include three test levels:
  - Deterministic fixtures for workflow logic, tool contracts, validation, and failure handling.
  - Live backend evaluations using real model/search calls, recording quality, latency, and usage.
  - Android emulator demos exercising assistant activation, actual assistant screenshot delivery, dispatch, result rendering, notification navigation, and an edit.
- Automate emulator setup, debug APK installation, test pairing, screen fixtures, invocation, assertions, and evidence collection through ADB and UI Automator. These are supported automation paths; a separate desktop client is unnecessary. [Emulator control](https://developer.android.com/studio/run/emulator-commandline), [UI Automator](https://developer.android.com/training/testing/other-components/ui-automator)
- Add a debug-only prerecorded-audio input and output-audio measurement seam for repeatable voice tests. Clearly label these tests as bypassing the physical microphone and wake detector.
- Use a fixture app to display known content and protected-content cases. ADB screenshots are evidence only; workflow input must come through the production Android capture path.
- Produce a report containing workflow version, assertions, trace IDs, timings, result links, screenshots, and a demo recording. Failed or unavailable checks remain visible.
- Keep test pairing, audio injection, and candidate-enablement mechanisms unavailable in release builds.

Codex should be able to run this suite unattended once the host SDK, emulator acceleration, and test credentials are configured.

## 5. Delivery order and acceptance

1. **Framework and harness:** implement contracts, shared tool interfaces, interactive execution lane, candidate registry, and deterministic runner.
2. **Android startup slice:** implement activation snapshots, correlated upload, asynchronous dispatch, acknowledgment overlap, and emulator capture tests. Use a fixture workflow before adding research.
3. **Reusable results:** implement publishing, revisions, deep links, and both reference workflows.
4. **Complete demonstrations:** add live evaluations, Android demo automation, failure scenarios, and the Codex authoring guide.

Measure first spoken word, command end, snapshot availability, accepted task, first audible acknowledgment, worker start, retrieval completion, and usable page separately. Use device-local clocks for device timings rather than subtracting unsynchronized timestamps.

Acceptance criteria:

- For short commands on a ready device, target snapshot availability within five seconds of speech start and acknowledgment within two seconds of command end.
- Target median usable-result time within 20 seconds of speech start for reference workflows. Report p95, failures, and quality alongside latency; these targets are not yet established guarantees.
- Demonstrate that a long general task does not block interactive task startup.
- Verify duplicate events, reconnects, cancellation, missing/protected screenshots, slow sources, worker restart, stale edits, and notification navigation.
- Prove extensibility by adding the second workflow through its package and declared tools, without editing core orchestration.
- Run a physical Android checkpoint after the startup slice and again at completion. Emulator tests cannot establish real microphone, wake-word, OEM capture, or background-service behavior.

Implementation can proceed through automated testing without repeated user input. Physical-device results are required before claiming the Android latency targets are met. Keep the feature disabled by default until that checkpoint passes.
