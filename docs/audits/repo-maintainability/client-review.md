# Frontend and Android maintainability review

Baseline: `0ab419d74d1595cfc9f3f803e9c069ae8d077f9a`, branch `codex/jarvis-voice-poc`, reviewed on 2026-10-29. This review changed no application code or application data and made no paid/provider requests. The only executable addition is the audit evidence probe requested by the coordinating reviewer.

## Conclusion and review depth

The clients contain useful existing boundaries: pure audio transitions, navigation policies, replay reducers, browser transport dependencies and shared maintained-result views. The main obstacle is uneven adoption of those patterns. The Jarvis route still assembles multiple protocols and lifecycles itself; Android's nominal audio owner exposes its concrete service through a global; result-type extension still requires changes outside the renderer registry. A reproducible asynchronous conversation-selection defect shows a practical cost of the inconsistent lifecycle rules.

This is a flow-level semantic review of the files and sections listed below, plus structural inventory of the remaining frontend/Android tree. It is not a claim that every client file received a line-by-line review. Findings distinguish executed reproduction from source-backed maintenance risks. `P2` denotes a material correctness or maintenance issue; small local cleanup is called out separately. No architectural recommendation is justified by line count alone.

Reviewed flows:

- Browser Jarvis initialization, state/domain replay, raw-provider and server transcript handling, wake-to-Live handoff, stream reconnect, page disposal, push-open receipts and task-card actions.
- Chat selection/detail loading, background polling, optimistic/live/committed message integration and selected send/select actions.
- Task detail controls, question answers, follow-up result intent, result rendering/copy/save, collections and the static companion wrappers.
- Native application composition, single audio service, wake recorder handoff, Live startup/readiness/close, native WebView bridge/session renewal, screen-capture coordination, notifications/display requests and updates.

## Findings

### C01 — P2: Older conversation loads can overwrite the newly selected conversation

**Status: reproduced against the actual loader implementation; high confidence.**

[`use-conversation-detail.ts:56–84`](../../../frontend/lib/use-conversation-detail.ts#L56-L84) starts two asynchronous requests and unconditionally commits detail, events, artifacts and messages when they finish. Its selection effect at [99–105](../../../frontend/lib/use-conversation-detail.ts#L99-L105) has no request generation or cancellation cleanup. [`selectConversation` at 577–580](../../../frontend/lib/use-conversation-actions.ts#L577-L580) changes the selected identity independently. If A starts first, B starts second, B finishes first and A finishes last, A replaces B's displayed detail. These setters remain shared between selections.

The preserved [no-network probe](evidence/client-stale-conversation-probe.cjs) transpiles the current source and invokes the real loader with controlled API promises and fake React setters. `node docs/audits/repo-maintainability/evidence/client-stale-conversation-probe.cjs` produced B after B resolved, then A after the older A resolved. This is an executed loader-level reproduction, not a mounted React/browser reproduction. The probe intentionally detects the audit baseline defect; it is not a permanent passing regression test for a repaired implementation.

**Direction:** make the selected conversation/branch generation explicit and reject superseded responses before all state writes, including loading/error state. Apply the same lifecycle rule to task polling: [`tasks/[taskId]/page.tsx:56–71`](../../../frontend/app/%28app%29/tasks/[taskId]/page.tsx#L56-L71) starts overlapping interval/action refreshes without an in-flight or response-order guard. That second case is a source-backed risk, not separately reproduced here. [`ResultCollection.tsx:18–36`](../../../frontend/components/ResultCollection.tsx#L18-L36) already provides a small local generation-guard pattern; a universal state-management framework is unnecessary.

**Acceptance:** deferred A/B conversation and branch loads resolved in both orders; selection cleared during a load; action refresh overlapping a timer; old errors/loading completions cannot affect the new selection.

### C02 — P2: The Jarvis route owns protocol and lifecycle orchestration as well as presentation

**Status: source-backed maintenance finding; high confidence.**

The route's responsibilities are coupled through shared refs and callbacks:

- State/domain replay and projection assembly: [`page.tsx:218–262`](../../../frontend/app/%28app%29/jarvis/page.tsx#L218-L262).
- Native observer selection, browser transport construction, raw-provider transcript translation, telemetry, wake model lifecycle, media/network changes and teardown: [264–486](../../../frontend/app/%28app%29/jarvis/page.tsx#L264-L486).
- A separate durable SSE connection, one-second replay polling and server-event transcript translation: [488–580](../../../frontend/app/%28app%29/jarvis/page.tsx#L488-L580).
- Push receipts and voice commands: [591–725](../../../frontend/app/%28app%29/jarvis/page.tsx#L591-L725); fake-provider lab actions: [644–655](../../../frontend/app/%28app%29/jarvis/page.tsx#L644-L655), [876–914](../../../frontend/app/%28app%29/jarvis/page.tsx#L876-L914).

The declared [`JarvisVoiceClient`](../../../frontend/lib/jarvis-voice-client.ts#L8-L14) is not the contract used to compose this page. The page stores a concrete browser/native union at [150](../../../frontend/app/%28app%29/jarvis/page.tsx#L150) and switches with `instanceof` at [678–706](../../../frontend/app/%28app%29/jarvis/page.tsx#L678-L706). Browser transport events and server-domain events each translate transcript state in the route. A protocol or ownership change therefore requires understanding unrelated view, wake, replay and native branches.

**Direction:** extract a Jarvis session/feed owner and a small voice/wake composition owner with explicit observed state, commands and disposal semantics. Keep browser/native implementations separate where behavior differs: disposing the native page must remain observation-only. Move the developer lab out of the normal orchestration closure. Leave simple presentation helpers local. Do not replace this route with dozens of forwarding hooks or a new application-wide framework.

**Acceptance:** a model-free route integration fixture covers browser/native startup, replay plus provider transcript deduplication, disposal during startup/replay, network reconnect and native navigation that leaves Live active. Existing controller/reducer unit tests remain useful but do not directly exercise the route's integration effect; no `jarvis/page.test.tsx` exists in this baseline.

### C03 — P2: Native audio has one physical owner but a porous public ownership contract

**Status: source-backed maintenance finding; high confidence.**

[`JarvisAudioService.kt:27–39`](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/audio/JarvisAudioService.kt#L27-L39) correctly states that the service alone owns wake/WebRTC audio. However, [`JarvisAudioControls.kt:13–20`](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/audio/JarvisAudioControls.kt#L13-L20) exports a writable `MutableStateFlow`, a concrete service `owner` and previous-release synchronization state. The assistant reaches through the owner for call identity and health/acceptance operations at [`JarvisVoiceInteractionSession.kt:284–324`](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/assistant/JarvisVoiceInteractionSession.kt#L284-L324), [426–438](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/assistant/JarvisVoiceInteractionSession.kt#L426-L438). Other UI/notification paths issue commands through intents instead. Consumers must learn both access paths and which fields may be changed safely.

**Direction:** expose read-only audio state and command methods/intents; retain the concrete service and release coordination inside the audio owner. Publish the call identity already present in `AudioControlState` for consumers that need it. Move acceptance-only control access behind the debug seam described in C04. No general dependency-injection container is needed. [`AppUpdater.kt:49–50`](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/update/AppUpdater.kt#L49-L50) already demonstrates private mutable state with a public `StateFlow`.

**Acceptance:** notification, WebView and assistant commands target the same activation/session/generation; stale Stop remains harmless; hiding/destroying the sheet preserves active audio; service replacement waits for release; external consumers cannot write audio state or call the service directly.

### C04 — P2: Acceptance implementation details are compiled into and called throughout production audio/capture paths

**Status: source-backed boundary finding; high confidence. No release bypass or measured performance regression is claimed.**

The installation entrypoints check debuggability, which is a meaningful existing safeguard: [`AcceptanceAudioInput.kt:56–70`](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/live/AcceptanceAudioInput.kt#L56-L70), [`AcceptanceAudioStream.kt:35–43`](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/live/AcceptanceAudioStream.kt#L35-L43), [`AcceptancePlaybackRecording.kt:30–34`](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/live/AcceptancePlaybackRecording.kt#L30-L34). Debug receivers are under `src/debug`.

Nevertheless, the implementations live under `src/main` and are called directly from every wake/WebRTC input and output callback: [`WakeMicrophone.kt:55–57`](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/wake/WakeMicrophone.kt#L55-L57), [`LiveController.kt:63–78`](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/live/LiveController.kt#L63-L78). That controller also directly collects acceptance readiness, RTP and reflection-based health observations at [94–109](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/live/LiveController.kt#L94-L109). Event-stream, assistant and screenshot code know about `AcceptanceAudioInput` as well; for example [`JarvisEventStream.kt:81–91`](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/notifications/JarvisEventStream.kt#L81-L91) and [`ScreenCaptureManager.kt:201–209`](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/assistant/ScreenCaptureManager.kt#L201-L209). Production-path comprehension consequently includes recording, relay, sample-injection and evidence-collection lifecycles.

**Direction:** retain a narrow, explicit audio/capture observation and debug-input seam in main code; supply the acceptance implementation from the debug source set and a trivial release implementation. Preserve the real capture/playback path used by acceptance. Avoid introducing abstractions around unrelated audio work merely to split files.

**Acceptance:** release build cannot install an input override or start a recorder/relay; debug local-audio and capture harnesses still measure the actual transport; audio callbacks remain bounded and do not write files; missing health counters remain unknown rather than passing as zero.

### C05 — P2: The result renderer registry is only a partial extension boundary

**Status: source-backed type/change-locality finding; high confidence. No malformed server-response incident is claimed.**

[`api.ts:738–747`](../../../frontend/lib/api.ts#L738-L747) widens `result_kind` to `string` and places document/recipe content in an uncorrelated union. Each renderer accepts that same union and casts it to its preferred type at [`result-renderers.tsx:15–16`](../../../frontend/app/results/result-renderers.tsx#L15-L16), [32–33](../../../frontend/app/results/result-renderers.tsx#L32-L33). The registry at [51–54](../../../frontend/app/results/result-renderers.tsx#L51-L54) therefore cannot prove that kind and content agree. Copy separately assumes every non-document is a recipe at [`ResultScreen.tsx:88–93`](../../../frontend/app/results/ResultScreen.tsx#L88-L93). Adding a result kind requires discovering and changing this second dispatch site even if rendering was registered correctly.

**Direction:** define a discriminated client result model for known kinds plus an explicit unsupported-kind representation, and keep per-kind rendering and copy/export behavior together. Keep result metadata/transport independent of those implementations. The backend remains authoritative; do not add an elaborate generic result framework. Keep backend fixtures and browser contracts aligned through a focused contract test when this interface changes.

**Acceptance:** document and recipe rendering/copy/save through both builds; unsupported types render source-task fallback without entering a known-content cast; a third test-only result type can be registered without changing the shared screen's kind-specific logic; mismatched known kind/content fails a type/contract test.

### C06 — P2: Task control presentation infers semantics from English summary text and differs by surface

**Status: confirmed source-level coupling; high confidence. Backend authority enforcement was not bypass-tested.**

[`tasks/[taskId]/page.tsx:85–93`](../../../frontend/app/%28app%29/tasks/[taskId]/page.tsx#L85-L93) decides whether to request resource-change acknowledgement partly through `terminal_summary?.includes("cannot be checked")`. A copy or summary-generation change alters retry interaction. The same page also interprets workflow capability identifiers to decide follow-up result behavior at [76–79](../../../frontend/app/%28app%29/tasks/[taskId]/page.tsx#L76-L79). Meanwhile the Jarvis card uses simpler retry/cancel wrappers at [`jarvis/page.tsx:1138–1142`](../../../frontend/app/%28app%29/jarvis/page.tsx#L1138-L1142), while the detail page submits explicit run/control revisions at [101](../../../frontend/app/%28app%29/tasks/[taskId]/page.tsx#L101). Contributors must reconstruct which surface is responsible for which control prerequisites.

**Direction:** project the minimum explicit control prerequisites/capabilities from the task domain and use them in both surfaces; user-facing summaries remain presentation. Preserve server-side validation and idempotency. Consolidate client command construction around the intended control API as part of the backend control-contract review; do not add a client-side parallel policy engine.

**Acceptance:** resource changed versus unverifiable resource, changed summary wording, stale control revision, workflow answer-only support, cancel/retry from both surfaces, and pending control interaction. Summary wording must not determine request semantics.

### C07 — P2: Static companion reuse depends on route-page implementations instead of reusable screens

**Status: source-backed framework-coupling finding; high confidence. Both builds currently passing is not disputed.**

[`result-export/app/task-shell/page.tsx:4–16`](../../../frontend/result-export/app/task-shell/page.tsx#L4-L16) imports the normal dynamic Next route page and constructs a `Promise` of params from `window.location.pathname`. The static task/app index wrappers also import normal route modules: [`tasks/page.tsx:1–4`](../../../frontend/result-export/app/tasks/page.tsx#L1-L4), [`workspace/apps/page.tsx:1–4`](../../../frontend/result-export/app/workspace/apps/page.tsx#L1-L4). Normal app presentation now contains static-runtime branches, for example [`workspace/apps/page.tsx:49–55`](../../../frontend/app/%28app%29/workspace/apps/page.tsx#L49-L55). A routing, params or layout change must be assessed against both build systems even when the underlying task behavior is unchanged.

There is already a cleaner local example: both result entrypoints use `ResultScreen(publicId)` and [`CockpitLink`](../../../frontend/components/CockpitLink.tsx#L7-L9) owns the static/full navigation distinction.

**Direction:** make route entrypoints thin adapters around task/app/login screens with ordinary data props and explicit supported actions. Preserve the focused static build and shared views. Keep pathname-to-ID decoding in the static adapter and Next params in the normal adapter; neither needs to emulate the other's route component signature.

**Acceptance:** full build and `build:results`, direct future result/task IDs, malformed IDs, authenticated redirects, native cookie renewal and same-source rendering in both shells. No extra run/attempt/result should be created by reopening a page.

### C08 — P2: Unused client wake policies and obsolete surfaces create misleading sources of truth

**Status: unused policy callers verified with repository search; high confidence for the gates, targeted follow-up for legacy inference.**

[`jarvis-address-detection.ts:19–20`](../../../frontend/lib/jarvis-address-detection.ts#L19-L20) describes itself as the second-stage local address gate. [`JarvisAddressGate.kt:5–6`](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/wake/JarvisAddressGate.kt#L5-L6) makes the corresponding native claim. `git grep` finds only their own unit-test callers, with no production call site for `classifyJarvisAddress` or `JarvisAddressGate`. The Kotlin API also accepts an unused `continuedMs` argument. [`jarvis-wake-gate.ts`](../../../frontend/lib/jarvis-wake-gate.ts) is a re-export whose sole importer is its own test; the route imports the underlying benchmark-preferences module directly. These files/tests look like active behavior to a new contributor but do not establish the shipped admission policy.

Related dead route state is concrete: `wakeGateBackendMode` and `wakeGateMismatch` are initialized at [`jarvis/page.tsx:122–123`](../../../frontend/app/%28app%29/jarvis/page.tsx#L122-L123), only reset to null at [392–393](../../../frontend/app/%28app%29/jarvis/page.tsx#L392-L393), and displayed conditionally at [820–828](../../../frontend/app/%28app%29/jarvis/page.tsx#L820-L828).

The wake engine additionally supports two full inference modes selected by optional-method presence at [`jarvis-wake-engine.ts:171–180`](../../../frontend/lib/jarvis-wake-engine.ts#L171-L180); the old path explicitly calls the "legacy analyze method" at [560–576](../../../frontend/lib/jarvis-wake-engine.ts#L560-L576). The shipped worker creates `HeyBuddyOnnxInference`, which supplies the split methods, at [`jarvis-wake-worker.ts:50–56`](../../../frontend/lib/jarvis-wake-worker.ts#L50-L56). Unlike the unused address gates, legacy inference removal still needs a full consumer/configuration check and migration of relevant tests; this review did not prove every alternate harness unnecessary.

**Direction:** remove the verified unreferenced gates/re-export/dead state and their self-only tests, or explicitly move a still-required experiment into a named experiment harness. Verify then remove obsolete inference compatibility. Test the actual current address-admission seam, owned by the backend coordinator, rather than wiring these old policies back into production to justify their existence.

**Acceptance:** repository-wide caller/import check, current split-inference unit cases, real worker integration, and offline backend admission-policy cases; retained tests must exercise reachable intended behavior.

### C09 — P2: Browser request lifetime and error semantics vary between otherwise similar domain operations

**Status: source-backed reliability/change-locality risk; no hanging-network browser run was performed.**

There is a bounded fetch helper at [`api.ts:26–54`](../../../frontend/lib/api.ts#L26-L54), used by result/catalog requests at [1123–1150](../../../frontend/lib/api.ts#L1123-L1150). Jarvis state and task detail use unbounded raw fetch at [859–862](../../../frontend/lib/api.ts#L859-L862), [1114–1120](../../../frontend/lib/api.ts#L1114-L1120). Jarvis refresh suppresses all later refreshes while one is in flight at [`jarvis/page.tsx:218–220`](../../../frontend/app/%28app%29/jarvis/page.tsx#L218-L220), releasing that condition only when its promise settles at [258–260](../../../frontend/app/%28app%29/jarvis/page.tsx#L258-L260). A stalled state/replay request can consequently prevent the page's subsequent polling from recovering until the browser settles the original fetch.

Error handling is also endpoint-specific: Live calls extract a backend detail at [`api.ts:1016–1019`](../../../frontend/lib/api.ts#L1016-L1019); task controls expose raw response text at [1201](../../../frontend/lib/api.ts#L1201); save UI detects conflicts by searching the message for `409` at [`ResultSavedControl.tsx:14–17`](../../../frontend/components/ResultSavedControl.tsx#L14-L17). The same broad API module mixes authentication, chat, task, result, Jarvis and app/knowledge types and transport functions, so there is no obvious per-domain interface owner when correcting these differences.

**Direction:** use a small shared finite-request transport with abort propagation and a structured status/detail error, then group public client functions/types by domain. Streaming transports keep their explicit long-lived policy. Treat telemetry as intentionally best effort. Do not solve this with a new cross-language runtime or generated SDK unless the contract audit demonstrates a separate need.

**Acceptance:** finite state/detail request timeout unblocks later refresh; upstream abort stays distinguishable from timeout; 401/409/backend validation detail survives transport as structured data; SSE is not accidentally given a page-load timeout; telemetry failure never blocks result rendering.

## Batch 3 findings: web state and audio (continuation)

Read records: [review-web-state.json](evidence/review-web-state.json). C10–C12 are reproduced by [web-state-probes.cjs](evidence/web-state-probes.cjs) ([output](evidence/web-state-probes.txt)). The probe loads the real TypeScript with fake platform dependencies; it is not a browser or ONNX integration run. The first pass wrote the probe but never wrote up its results; the source was re-read for this write-up.

### C11 — P1: Browser Stop keeps the microphone and WebRTC connection open until an unbounded HTTP request finishes

[`jarvis-live.ts:403-420`](../../../frontend/lib/jarvis-live.ts#L403-L420) `stop()` calls `dispose(true)`. At [665-672](../../../frontend/lib/jarvis-live.ts#L665-L672), `dispose` awaits `closeBackendSession` *before* it closes the data channel and peer connection and stops the local tracks. `closeJarvisLiveSession` ([`api.ts:1095-1103`](../../../frontend/lib/api.ts#L1095-L1103)) is a raw `fetch` with no timeout. While that request is pending or slow, the microphone stays live and the peer connection can keep sending audio to the provider after the user pressed Stop. The probe shows the track still running while the close is pending and stopped only after it resolves.

The native client already does this in the right order: [`LiveController.kt:382-399`](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/live/LiveController.kt#L382-L399) stops local audio before waiting for the network close.

**Direction:** stop tracks and close the peer and data channel first, then send the backend close as best-effort, bounded cleanup. **Acceptance:** with a never-resolving close request, tracks are stopped and the peer is closed synchronously within `stop()`; the backend close is still attempted once per session generation.

### C10 — P2: A superseded wake-runtime failure overwrites the new runtime's state

[`jarvis-wake-runtime.ts:345-352`](../../../frontend/lib/jarvis-wake-runtime.ts#L345-L352) checks the operation token, awaits `releaseResources`, then publishes `failed` without checking the token again. If the user restarts wake detection while the old capture is still being destroyed, the new runtime reaches `ready` and the old failure then sets it to `failed`. Reproduced: the status goes `ready` → `failed` after the old cleanup resolves. **Direction:** re-check the operation after every await before publishing, as `start()` already does. **Acceptance:** the probe scenario ends in `ready`.

### C12 — P3: Partial ONNX initialization leaks the sessions that did load

[`jarvis-hey-buddy-inference.ts`](../../../frontend/lib/jarvis-hey-buddy-inference.ts) `create()` builds four inference sessions with `Promise.all`. If one fails, the others are never released. The probe creates three sessions, fails the fourth, and releases none. Each retry after a missing or corrupt model leaks WASM memory. **Direction:** use `Promise.allSettled` and release the fulfilled sessions before rethrowing.

### C01 addendum — the streaming send path has the same ownership gap

[`use-conversation-actions.ts:306-399`](../../../frontend/lib/use-conversation-actions.ts#L306-L399) writes streamed events, timeline messages and status into shared state without checking that the streamed conversation is still the selected one. `onMetadata` at [314-320](../../../frontend/lib/use-conversation-actions.ts#L314-L320) forces the selection back to the streamed conversation and to branch `main`, even though the request was sent with `activeBranchKey`. That is a source-backed risk, not reproduced: switching conversations mid-stream can be undone, and a send on a non-main branch can jump the view to `main`. The C01 generation guard should cover stream callbacks as well as loads.

### C13 — P2: The production wake path still runs the pre-phase-3 handoff protocol alongside the current one

- In split-inference mode (the only mode the shipped worker uses), [`jarvis-wake-engine.ts`](../../../frontend/lib/jarvis-wake-engine.ts) calls `onCandidateAudio` and then `onCandidateChunk` for every post-wake sample. [`jarvis-wake-worker.ts:81-100`](../../../frontend/lib/jarvis-wake-worker.ts#L81-L100) posts both `candidate_audio` and `candidate_chunk` messages, plus a pre-roll copy on `detected`. In the split path, [`jarvis-wake-runtime.ts:220-290`](../../../frontend/lib/jarvis-wake-runtime.ts#L220-L290) zeroes the pre-roll and sends `candidate_audio` to a `candidateController` that is `null`. Every microphone sample after a wake is therefore copied and transferred across the worker boundary twice, and one copy is dropped without being zeroed, unlike every other buffer in this pipeline.
- `JarvisCandidateAudio.preRoll`/`endpoint` are marked "compatibility-only fields used by the pre-phase-3 handoff adapter" ([`jarvis-activation-audio.ts:36-38`](../../../frontend/lib/jarvis-activation-audio.ts#L36-L38)); their only readers are assertions in `jarvis-wake-runtime.test.ts`.
- **C08 is resolved by this pass:** the legacy `analyze` inference mode has no caller other than `jarvis-wake-engine.test.ts`. No harness, Android script or backend acceptance path constructs the engine with it, so it can be removed with its tests. `components/AgentStatusCard.tsx` is likewise referenced only by `chat-ui.test.tsx`.
- **Direction:** remove the legacy inference mode, the `candidate_audio`/pre-roll/utterance-promise handoff and its compatibility fields together, so the engine keeps one state set instead of `split*`-prefixed duplicates. **Acceptance:** current split-path unit tests and the real worker integration still pass; the worker posts exactly one copy of each post-wake sample.

### C14 — P2: The timeline feed dropped council answers, usage, task links and branch locks for every current conversation

Found during batch 7 while closing I05's open condition, and reproduced. `conversation_store` writes a timeline item for every user and assistant message, so [`conversation-feed.ts:34-39`](../../../frontend/lib/conversation-feed.ts#L34-L39) always takes the timeline path for current conversations. Four things are attached only by the legacy reconstruction in [`chat-history.ts:497-530`](../../../frontend/lib/chat-history.ts#L497-L530): `councilData` (the per-model council panel), `usage` (cost/token line), `taskId` (link to the originating task) and `branchable`/`branchBlockReason`. These are the only sources for [`plain-renderers.tsx:57-58,102-113,144`](../../../frontend/components/chat-message/plain-renderers.tsx#L57-L144). The backend `TimelineItemView` (`backend/app/models/conversations.py:107-124`) and `build_assistant_message_timeline_item` carry none of these fields.

[feed-parity-probe.cjs](evidence/feed-parity-probe.cjs) loads the real `conversation-feed.ts` with one council run. Without timeline items, all four appear; with them, none do ([output](evidence/feed-parity-probe.txt)). The probe is not a browser run, but there is no other code path that could supply these fields. `chat-history.test.ts` covers only the legacy path, so nothing flagged the loss.

**Direction:** extend the timeline item contract with the message's usage, source task, council responses (or a reference to the run's council artifacts) and branch eligibility, populated by the server where it already has the data. Then map them in `mapTimelineItemToChatMessage` and remove the reconstruction. This replaces I05's "delete once verified" with "complete the contract, then delete". **Acceptance:** the probe's timeline path matches the legacy path; a council conversation reloaded in the browser shows the model panel and costs.

## Batch 4 findings: Android (continuation)

Read records: [review-android.json](evidence/review-android.json). The service/controller core holds up well under a full read: one controller per Listen, attempt-epoch fencing on every callback, exact-call cleanup on failure, and local audio released before any network close. The new issues sit at the edges the JVM tests don't reach.

### A01 — P2: The phone's live event stream times out between server heartbeats

[`CockpitApi.kt:87`](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/data/CockpitApi.kt#L87) builds a default `OkHttpClient` (10 s read timeout), and `liveEvents` (`:265-278`) opens the SSE stream with that same client. okhttp-sse 4.12.0 does not change the read timeout: its `RealEventSource` only calls `timeoutEarlyExit`, which affects the call timeout, as checked in the cached jar. The backend sends a heartbeat every 15 s (`backend/app/services/jarvis_live.py:21`, `ping=None` at `api/jarvis.py:217`). On a quiet system the stream fails with a read timeout before each heartbeat. [`JarvisEventStream.kt:53-68`](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/notifications/JarvisEventStream.kt#L53-L68) then reconnects with backoff that doubles to 30 s and resets only when a `jarvis` event arrives, not on heartbeats. The result is constant reconnect churn and up to 30 s delay before a phone-targeted event (delivery, stop, unlink) is seen.

Status: confirmed from source and library bytecode; not run on a device or emulator. A device log showing repeated `Live event stream disconnected` with `SocketTimeoutException` would confirm it.

**Direction:** give the stream its own client with `readTimeout(0)` (or longer than two heartbeats), reset backoff on open/heartbeat, and share one `OkHttpClient` across the app instead of creating one per `CockpitApi` (14 construction sites, each with its own pool and dispatcher). **Acceptance:** a JVM test with a fake SSE server that sends only heartbeats keeps one connection for over a minute; the backoff resets after a successful open.

### A02 — P3: The assistant session encodes four show modes in mutable flags

[`JarvisVoiceInteractionSession.kt:152-238`](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/assistant/JarvisVoiceInteractionSession.kt#L152-L238) handles context refresh, phone capture, shared-live capture and local-wake/fixture shows in one `onShow`, coordinated through `phoneCaptureId`, `activationCaptureOnly`, `stagingInitialCapture`, `initialCaptureStaged`, `initialScreenshotPending`, `refreshCaptureId`, `fixtureSession` and `showEpoch`. `onHandleScreenshot` and the delayed callbacks reinterpret the same flags. The `Build.VERSION.SDK_INT >= 37` staging rule appears twice as a bare literal. **Direction:** one sealed show-mode value chosen at the start of `onShow`, with per-mode handlers; move the fixture mode behind the C04 debug seam. Not urgent: behavior appears correct, but every capture change has to re-derive which flags apply.

### A03 — P2: Android verification harnesses inherit the personal `.env`

The backend test suite isolates configuration by pointing `AI_COCKPIT_ENV_FILE` at an empty file (`backend/tests/conftest.py:19`). None of the eleven `android/scripts/verify-*.py` harnesses does this. They start a backend with `{**os.environ, ...}` plus a hand-picked override list. The most careful one, [`verify-show-on-phone.py:62-71`](../../../android/scripts/verify-show-on-phone.py#L62-L71), overrides keys, auth and eight data directories. `verify-audio-controls.py` overrides only the settings in its configuration file and `DATABASE_URL`. Any setting not on a given script's list (for example `TASK_PROJECT_ROOTS`, `TASK_ORCHESTRATION_PROFILE`, `CODEX_SUBSCRIPTION_PROFILE`, Tailscale hosts, VAPID keys, and provider keys in the scripts that don't blank them) comes from the personal configuration, and each new setting silently joins that set. The harnesses also import each other by filename (`verify-show-on-phone.py` loads `verify-result-platform.py`, which loads `verify-screen-workflow.py`) as a shared library. **Direction:** one small harness support module that starts a backend with `AI_COCKPIT_ENV_FILE` set to a fixture env, plus an explicit per-scenario settings dict; the scripts import that module instead of each other. **Acceptance:** running any harness with a deliberately poisoned personal `.env` (bogus keys and roots) produces the same result. Not run in this pass, since the harnesses need an emulator.

## Existing patterns to preserve

- [`AudioControlMachine`](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/audio/AudioControlState.kt#L35-L65) makes transitions and stale-action epochs pure. Its tests explicitly cover repeated Listen, stale Stop and interrupted startup. [`JarvisAudioService.kt:111–119`](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/audio/JarvisAudioService.kt#L111-L119) releases audio ownership before replacement; [`LiveController.kt:382–399`](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/live/LiveController.kt#L382-L399) stops local audio before network close acknowledgement. Preserve these durable cancellation semantics.
- [`NativeVoicePolicy`](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/web/NativeVoiceBridge.kt#L12-L16) and [`ResultNavigation`](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/web/ResultNavigation.kt#L5-L19) are small explicit boundaries with focused tests. The bridge carries controls/state, and WebView microphone requests are denied at [`JarvisWebViewActivity.kt:88–92`](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/web/JarvisWebViewActivity.kt#L88-L92). The TypeScript observer's disposal does not stop native audio at [`jarvis-phone-voice.ts:89–94`](../../../frontend/lib/jarvis-phone-voice.ts#L89-L94).
- [`watchResult`](../../../frontend/lib/result-live.ts#L23-L88) owns SSE identity, reconnect/fallback and disposal; [`ResultScreen.tsx:27–31`](../../../frontend/app/results/ResultScreen.tsx#L27-L31) rejects older state revisions. Its small focused tests exercise offline/reconnect and stale snapshots. Preserve the distinction between recovery fallback and obsolete development compatibility.
- [`ScreenCaptureManager.kt:51–55`](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/assistant/ScreenCaptureManager.kt#L51-L55), [131–159](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/assistant/ScreenCaptureManager.kt#L131-L159), [183–210](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/assistant/ScreenCaptureManager.kt#L183-L210) explicitly bind identity/expiry, reject lock/protected-content cases and bound uploads to admitted requests. Simplification must retain these checks and memory clearing.
- [`hooks.ts:29–100`](../../../frontend/lib/hooks.ts#L29-L100) composes conversation list, detail, polling and actions instead of placing all behavior in the chat route. [`JarvisLiveStreamState`](../../../frontend/lib/jarvis-live-state.ts#L14-L84) keeps domain cursors and a bounded replay reducer explicit. Their boundaries are worth keeping while repairing lifecycle edges.
- [`AppRelease.parse` and `decideUpdate`](../../../android/app/src/main/java/nl/sander/aicockpit/jarvis/update/AppRelease.kt#L17-L51) separate manifest validation/update policy from installation side effects. Native cookie renewal, navigation and update/capture policies are meaningful boundaries; avoid flattening them into one generic controller.

## Verification and unresolved checks

This reviewer executed the no-network C01 loader probe successfully and ran repository caller searches. Unit/type/build/lint/Android gates are run centrally by the coordinating reviewer; their exact commands and logs belong to the consolidated verification record. Passing existing suites does not establish missing selection-race or route-integration coverage. No provider, paid speech, phone opt-in, release publication or physical-device run was performed.

The coordinating reviewer reported an additional development-tool issue to consolidate with the tooling audit: [`eslint.config.mjs:9–15`](../../../frontend/eslint.config.mjs#L9-L15) ignores `.next` and `.next-live-smoke`, while [`verify-screen-workflow.py:155`](../../../android/scripts/verify-screen-workflow.py#L155) uses `.next-jarvis-agent-test-m2`; `frontend/.gitignore` already ignores `.next-jarvis-agent-test*`, and `frontend/tsconfig.json` includes that acceptance build's generated types. Canonical lint entered preexisting generated build artifacts. Track this as a verification-boundary finding once centrally reconciled, not as new application failures inferred from generated code.

Remaining semantic follow-up includes the complete browser activation-audio/wake numerical implementation, all remaining chat-history/presenter branches, every settings/knowledge/generated-app route, native `OnnxWakeEngine`, complete setup UI and platform service callback timing, individual harness isolation/cleanup internals, and provider/physical/OEM behavior. The current source review establishes the reported ownership/coupling findings without claiming these areas are exhaustively reviewed.

## Coverage appendix

The following paths received flow-level semantic inspection. For files marked "selected", only the stated behavior was reasoned through; remaining sections retain structural coverage. Tests listed separately were inspected for relevant cases, not all assertions. Unlisted client paths have only the central inventory/import/symbol coverage and must not be upgraded to semantic review solely because an adjacent file was read.

### Frontend semantic inspection

- `frontend/app/(app)/jarvis/page.tsx` — orchestration, disposal, transcript, controls, lab and main presentation.
- `frontend/app/(app)/tasks/[taskId]/page.tsx` — refresh, controls, result-intent construction, task/trace/question presentation.
- `frontend/app/(app)/workspace/apps/page.tsx` — selected catalog loading and static-shell branches.
- `frontend/app/(app)/layout.tsx` — authentication/layout boundary.
- `frontend/app/results/ResultScreen.tsx`
- `frontend/app/results/result-renderers.tsx`
- `frontend/app/api/jarvis/live/route.ts`
- `frontend/components/ChatMessage.tsx`
- `frontend/components/CockpitLink.tsx`
- `frontend/components/LinkPhone.tsx`
- `frontend/components/ResultCollection.tsx`
- `frontend/components/ResultSavedControl.tsx`
- `frontend/components/Sidebar.tsx` — navigation and mobile/desktop surface structure.
- `frontend/components/TaskToolOverrides.tsx`
- `frontend/components/WorkerToolSettings.tsx`
- `frontend/components/chat/ConversationListPanel.tsx`
- `frontend/lib/api.ts` — selected transport/error policies, task/result/Jarvis contracts and operations; other endpoint families structurally indexed.
- `frontend/lib/hooks.ts`
- `frontend/lib/use-conversation-detail.ts`
- `frontend/lib/use-conversation-list.ts`
- `frontend/lib/use-conversation-polling.ts`
- `frontend/lib/use-conversation-actions.ts` — selected send/select/stop/archive/branch flows.
- `frontend/lib/conversation-feed.ts`
- `frontend/lib/conversation-message-lifecycle.ts`
- `frontend/lib/jarvis-live.ts` — selected startup/readiness, identity, dependency and public lifecycle composition; full activation bridge numerical implementation not reviewed.
- `frontend/lib/jarvis-live-state.ts`
- `frontend/lib/jarvis-phone-voice.ts`
- `frontend/lib/jarvis-voice-client.ts`
- `frontend/lib/jarvis-wake-state.ts`
- `frontend/lib/jarvis-wake-engine.ts` — selected mode selection, buffering guards and legacy branch; not exhaustive DSP review.
- `frontend/lib/jarvis-wake-worker.ts` — selected initialization and emitted event contract.
- `frontend/lib/jarvis-address-detection.ts`
- `frontend/lib/jarvis-wake-gate.ts`
- `frontend/lib/navigation.ts`
- `frontend/lib/result-live.ts`
- `frontend/lib/result-telemetry.ts`
- `frontend/lib/task-status.ts`
- `frontend/lib/task-trace.ts`
- `frontend/public/sw.js`
- `frontend/result-export/app/AuthenticatedShell.tsx`
- `frontend/result-export/app/task-shell/page.tsx`
- `frontend/result-export/app/result-shell/page.tsx`
- `frontend/result-export/app/tasks/page.tsx`
- `frontend/result-export/app/workspace/apps/page.tsx`
- `frontend/result-export/app/login/page.tsx`
- `frontend/result-export/app/collections/documents/page.tsx`
- `frontend/result-export/next.config.ts`
- `frontend/next.config.ts`
- `frontend/package.json`
- `frontend/vitest.config.mts`
- `frontend/playwright.live.config.ts` — configuration and paid/live test boundary, not a live run.
- `frontend/eslint.config.mjs`
- `frontend/tsconfig.json`
- `frontend/tsconfig.live-smoke.json`
- `frontend/README.md` and `frontend/AGENTS.md` — contributor entry guidance; broader documentation audit belongs to the coordinating review.

### Android semantic inspection

Paths below are relative to `android/app/src/main/java/nl/sander/aicockpit/jarvis/`:

- `JarvisApplication.kt`
- `audio/AudioControlState.kt`
- `audio/JarvisAudioControls.kt`
- `audio/JarvisAudioService.kt`
- `assistant/PhoneCaptureCoordinator.kt`
- `assistant/PhoneScreenPrivacy.kt`
- `assistant/ScreenCaptureManager.kt`
- `assistant/JarvisVoiceInteractionSession.kt` — selected state ownership, event handling, screenshot callbacks, teardown and acceptance access; complete Android onShow timing not certified.
- `data/CockpitApi.kt`
- `data/CredentialStore.kt`
- `data/PairingLink.kt`
- `live/LiveController.kt`
- `live/AcceptanceAudioInput.kt`
- `live/AcceptanceAudioStream.kt`
- `live/AcceptancePlaybackRecording.kt`
- `notifications/JarvisEventStream.kt`
- `notifications/JarvisNotifications.kt`
- `notifications/NotificationActionReceiver.kt`
- `update/AppRelease.kt`
- `update/AppUpdater.kt`
- `wake/BootReceiver.kt`
- `wake/JarvisAddressGate.kt`
- `wake/WakeMicrophone.kt`
- `web/JarvisWebViewActivity.kt`
- `web/NativeVoiceBridge.kt`
- `web/PhoneDisplay.kt`
- `web/ResultNavigation.kt`

Build/instruction coverage: `android/app/src/main/AndroidManifest.xml`, `android/app/build.gradle.kts`, `android/build.gradle.kts`, `android/settings.gradle.kts`, `android/README.md`. Scripts were inventoried and searched for build/live seams; no script receives full semantic coverage from that search alone.

### Tests inspected for relevant cases

- `frontend/lib/hooks.test.tsx` — selected deferred-load, committed/live and polling cases.
- `frontend/lib/hooks.actions.test.tsx` — selected archive/resend cases.
- `frontend/lib/api.test.ts` — selected request/idempotency/replay expectations.
- `frontend/lib/jarvis-live.test.ts` — startup/readiness, cancellation, preparation and activation-audio case coverage.
- `frontend/lib/jarvis-wake-runtime.test.ts` — ownership, handoff, rearm and initialization-failure cases.
- `frontend/lib/jarvis-wake-engine.test.ts` — old/split mode caller use and bounded audio cases.
- `frontend/lib/jarvis-phone-voice.test.ts`
- `frontend/lib/jarvis-address-detection.test.ts` and `frontend/lib/jarvis-wake-gate.test.ts` — caller search and reachability.
- `frontend/lib/result-live.test.ts` — reconnect/offline/disposal cases.
- `frontend/app/results/[publicId]/page.test.tsx` — registered types, stale snapshots and slow work cases.
- `frontend/app/(app)/tasks/[taskId]/page.test.tsx` — selected control/follow-up cases.
- `android/app/src/test/java/nl/sander/aicockpit/jarvis/audio/AudioControlStateTest.kt`
- `android/app/src/test/java/nl/sander/aicockpit/jarvis/web/NativeVoicePolicyTest.kt`
- `android/app/src/test/java/nl/sander/aicockpit/jarvis/live/LiveSessionPolicyTest.kt` — selected case inventory.
- `android/app/src/test/java/nl/sander/aicockpit/jarvis/live/LiveStartupReadinessTest.kt` — selected case inventory.
- `android/app/src/test/java/nl/sander/aicockpit/jarvis/web/ResultNavigationTest.kt` — selected policy case inventory.
- `android/app/src/test/java/nl/sander/aicockpit/jarvis/wake/JarvisAddressGateTest.kt` — caller search and reachability.

Cross-client backend seam inspected only for the activation source model/caller relationship: `backend/app/models/jarvis.py` and selected coordinator search hits. The backend review owns their semantic coverage and authoritative policy conclusions.
