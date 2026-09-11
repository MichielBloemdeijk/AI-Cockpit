# GPT-Live 1 assessment for Jarvis

Assessed 2026-09-11 against current official documentation and the working-tree Jarvis implementation. This is a recommendation and migration evaluation outline, not an implemented switch. No GPT-Live session, paid benchmark, or Android trial has been run in this assessment.

## Recommendation

Prioritize a small GPT-Live integration and comparison before implementing the Realtime-specific voice changes in D4 of the [workflow implementation plan](JARVIS_WORKFLOW_IMPLEMENTATION_PLAN.md). GPT-Live is a substantially better architectural match for the desired conversational experience. Keep Realtime 2.1 available until measured Android behavior justifies changing the default.

Retain the workflow framework design. The release changes the voice/controller boundary, not the need for local context capture, durable jobs, shared capabilities, fast worker capacity, reliable publishing, or tests. Finish the provider-independent decisions while treating voice sequencing as provisional. Do not build a large Realtime turn-control refactor and immediately replace it.

## What the release establishes

The API release is dated September 10, 2026. OpenAI reports a 30-percentage-point Full Duplex Bench improvement over GPT-Realtime-2.1. That supports evaluating the model for pauses, interruptions, backchannels, and conversation timing. It is not a Jarvis latency measurement. The release's stronger task-benchmark results use a separate backend (including Astra at medium reasoning), so they do not establish that Live plus Luna outperforms Realtime 2.1 on every task. [API launch](https://openai.com/index/introducing-gpt-live-1-in-the-api/)

## Capability differences that matter here

- **Conversation:** Realtime 2.1 combines voice, reasoning, and tool selection in a turn/response API. GPT-Live runs continuous speech interaction and delegates substantive work. This should reduce the need to make application turn detection decide exactly when the assistant may start thinking or talking. It does not remove network/startup time. [Architecture](https://developers.openai.com/api/docs/guides/live)
- **Acknowledgment during work:** Live's conversation continues independently of backend execution. Jarvis can acknowledge and accept a spoken correction while a tool or workflow runs. Realtime already supports tools and asynchronous work, but our current coordinator explicitly sequences tool results and spoken continuations. Live fits this separation more naturally.
- **Tool selection:** GPT-Live's voice frontend is not a drop-in replacement for Realtime's structured function emitter. A managed Responses model or our application-owned backend selects and executes tools. Budget for that routing/model work when measuring command-to-dispatch. [Migration](https://developers.openai.com/api/docs/guides/live-migration)
- **Screen input:** Realtime 2.1 accepts image input directly. Live's audio frontend does not; send the image to a vision backend and return findings as text. The planned activation snapshot and workflow image adapter already fit this. [Realtime model](https://developers.openai.com/api/docs/models/gpt-realtime-2.1), [Live visual context](https://developers.openai.com/api/docs/guides/live-delegation#add-images-and-visual-context)
- **Attention and interruptions:** Live is promising for the earlier side-conversation problem, and its prompt supports selective responses. This is model behavior, not guaranteed address detection. Keep the local wake gate and explicit end-session behavior initially. Avoid simultaneously reopening the per-sentence intent-gate project. [Live prompting](https://developers.openai.com/api/docs/guides/live-prompting)
- **Control:** Live has no speech equivalent of the Realtime `response.done` event and does not use application `response.create` to grant the voice model a turn. Application-level silence enforcement must happen in the playback path if required. This is a real migration cost for Jarvis's response arbiter, speech-delivery records, and progress presentation settlement. [Lifecycle changes](https://developers.openai.com/api/docs/guides/live-migration#adapt-the-connection-and-audio-lifecycle)
- **Memory:** Both current documented configurations provide a 128K context window. Live manages compaction automatically, but confirmed task state still belongs in Cockpit. Do not replace durable history with model memory. Its prompting guide's advice to use a small prompt is not evidence of a smaller numeric context limit. [Live context management](https://developers.openai.com/api/docs/guides/live-conversations#manage-longer-conversations)

## Delegation mode: start with managed Responses

For the first comparison, configure Live's Responses delegation with a small backend tool catalog mapping to the existing restricted gateway. Use Luna/low as the initial cost-conscious router, and Terra/low as a comparison if routing or corrections fail. Preserve the actual backend choice in every benchmark report.

This is a hybrid at the application level: Live handles conversation; the managed Responses backend selects `dispatch_task`, task queries, cancellation, and later `start_workflow`; Cockpit still executes and owns durable work. The router's dispatch tool returns acceptance promptly, while long tasks run independently. Do not move the full workflow into one long Live-managed response loop.

Client delegation is attractive later for custom execution control, but its `session.delegation.created` event contains metadata/timing, not utterance text, tool arguments, or raw audio. We would need to accumulate transcripts and reconstruct intent ourselves. Given the user's observed mismatch between ASR text and voice understanding, that is an unnecessary first migration risk. Managed delegation prepares conversation context for us, although its correctness still needs tests; it is not a guarantee of lossless acoustic understanding. [Delegation choices](https://developers.openai.com/api/docs/guides/live-delegation#choose-a-delegation-mode)

Receive function calls from nested `response.event` envelopes. Completed backend snapshots can have empty output lists, so the Realtime fallback of scanning terminal `response.output` cannot be copied. Collect completed function items, return every required function output, and continue delegated work. Route task updates with the supported Live context/commentary controls, keeping task IDs separate from delegation IDs. [Function-result protocol](https://developers.openai.com/api/docs/guides/live-delegation#complete-a-client-actionable-function-call)

## Latency expectations

The strongest expected benefit is conversational responsiveness: greeting, acknowledgment, pauses, interruptions, and corrections while work continues. This is precisely the part the user currently finds slow. A sub-two-second acknowledgment is a sensible acceptance target, not something established by release benchmarks for our network and Android app.

Faster speech does not prove faster action. A Live acknowledgment could precede backend routing and tool admission; measure command-to-accepted-task as a separate result. Managed delegation can keep backend connections prepared, but still introduces reasoning/tool-selection latency. Do not count a pleasant acknowledgment as completion of the user's task.

The recipe's 15–20-second target still depends on image analysis, source access, model output, and page publication. Live does not eliminate those steps. The local activation snapshot protects the screen-capture deadline independently of delegation speed: the frame can already exist before the backend decides which workflow to start.

Cold wake remains a separate benchmark. Session creation, ICE/media setup, and wake-to-input continuity can still dominate the first interaction. Use real WebRTC input on Android, without an additional PCM stream on the server sideband.

## Cost and always-listening behavior

The launch rate is $0.05 per active voice minute, with backend model/tools billed separately. Duration includes silence and time waiting for tasks. This implies $3 per active hour and $72 per continuous 24-hour day for the voice layer alone; these are arithmetic estimates at the published rate. Do not keep a cloud Live session open all day merely because the phone is listening locally. [Published rate](https://openai.com/index/introducing-gpt-live-1-in-the-api/), [duration accounting](https://developers.openai.com/api/docs/guides/voice-latency-cost?api=live)

Keep local wake listening, open Live for engaged conversation, and close it when done. WebRTC session creation bills 15 seconds during initialization, credited against running duration; it is not an extra 15 seconds to add to every running session. Include failed/speculative creations and frequent reconnects in cost tests. Muting the microphone does not close a billable session. Realtime uses token accounting, so actual cost comparison requires traces rather than a universal claim that Live is cheaper.

## Concrete migration surface in this repository

1. Add an `openai_live` provider beside `openai` and `fake`. Preserve the current Realtime adapter/default for A/B testing and rollback. Use capability-specific orchestration rather than pretending every adapter implements manually triggered voice turns.
2. Replace call setup for the Live path: JSON `POST /v1/live/sessions` returns the session ID and SDP answer; attach the server to `/v1/live/sessions/{id}/attach`. The current adapter instead parses a Realtime Location header and attaches with `?call_id=`. [WebRTC setup](https://developers.openai.com/api/docs/guides/voice-webrtc?api=live), [sideband setup](https://developers.openai.com/api/docs/guides/voice-server-controls?api=live)
3. Create the Android event data channel before the SDP offer and observe `session.started`. Keep media on WebRTC. Add Live-specific transcript, usage, delegated-response, and close-event handling.
4. Split voice personality/delegation instructions from backend tool instructions. Bypass manual audio commits, response-create arbitration, and transcript-final-turn assumptions for Live only. Preserve wake/action authorization, cancellation, stale-generation fencing, and durable dispatch idempotency.
5. Replace per-response spoken-delivery assumptions with Live-compatible playback/timeline tracking. A context-append acknowledgment is not proof the user heard a task update. Retain conservative delivery state where audible completion cannot be established.
6. Support quiet state updates versus spoken findings. Apply new corrections to task revisions and reject late superseded results. Do not equate stopping speech with cancelling background work.
7. Record duration usage and graceful session closure. Keep raw reflected audio out of persistent logging. Sideband input reflections must never be sent back as additional microphone input.

## First evaluation before committing to the switch

Build only the provider bridge, Android playback instrumentation, and a small set of existing gateway tools first. It does not need the complete workflow framework. Include a test-only delayed tool to prove acknowledgment/playback overlap with backend work, with explicit simulated-result labeling.

Run the same prerecorded commands through Realtime 2.1 and Live with the chosen backend, then do a physical Android comparison. Cover warm/cold greeting, tool dispatch, a short "hey Jarvis", natural pauses, alphanumeric details, a correction while a tool runs, speaking to another person, interruption versus task cancellation, denied wake, lock state, reconnect, and a repeated provider event. Preserve the current working-tree baseline rather than comparing with an older release.

Measure command end to audible acknowledgment, command end to durable admission, tool-result commit to spoken result, actual successful task state, unintended replies/actions, corrections, lost words at wake, duplicate jobs, and total cost. For this first comparison use at least 20 matched scenarios and report median/p95 with sample counts and failures. Small-sample p95 is descriptive, not a reliability guarantee.

Promote only if acknowledgment meets the two-second target on ready sessions, task admission/correctness does not materially regress, corrections work, and no permission/duplicate-action regression appears. If voices feel better but admission is slower, optimize the router or continue the comparison instead of declaring the whole pipeline faster.

## Effect on the saved implementation plan

- D1: keep packages/capabilities; move the enabled catalog to the delegated router for Live.
- D2: keep lanes, checkpoints, fencing, and durable acceptance.
- D3: keep activation snapshots, ownership, transport, and expiry; adapt session binding to Live.
- D4: preserve the written design as the Realtime fallback. Replace its Live path with continuous conversation plus delegated backend events; do not implement its response arbiter for Live.
- D5: keep workflow model/search adapters; the Live router is separate from the workflow executor.
- D6: keep versioned pages and notification links; send visible-page facts through Live context updates and revise spoken-delivery tracking.
- D7: keep fixture/Android tests and PCM hooks; add provider-specific event fixtures and A/B reporting.
- D8: insert the Live comparison/migration gate before the Realtime-specific startup refactor, then finalize workflow delivery sequencing.

Recommended next work is a bounded Live migration plan and spike, followed by the measured provider decision. The current assessment does not authorize changing runtime defaults or claim the targets are already met.
