# One-trial optional-schema verification — 2026-10-05

The schema correction works in a real end-to-end client admission: the provider
returned `strict:false`, the selector emitted only `goal` and `intent_id`, and
exactly one correct durable task reached Android. Raw provider output recording
also worked. Admission took **6748.07 ms**, still above the four-second iteration
gate. This is one observation, not a p95 or full M8/M10 acceptance.

## Scope and artifacts

One explicitly authorized API37 emulator voice trial used the existing `ready07`
recorded audio, production WebRTC RTP, GPT-Live 1, GPT-5.6 Luna with low effort
and priority, SQLite admission and Android event-stream receipt. Workers,
orchestration and new speech synthesis were disabled. Client routing was selected
to expose HTTP and stream timings. Both routing modes share the corrected tool
catalog, but this trial does not constitute live verification of managed mode.

Before generation, **28 offline harness/diagnostic tests passed**, following the
previous correction's 67 passing offline adapter/intent/workflow tests. The
harness now enables output recording independently from reasoning summaries.
This run requested no summaries, preserving the ordinary model request settings.
The extra five-second observation window occurs after admission and does not
change its measured endpoint. No additional live trial followed the result.

- [Admission evidence](../../../data/admission-optional-schema-verification-2026-10-05/client/audio/acceptance.json).
- [Complete exposed selector outputs and stream analysis](../../../data/admission-optional-schema-verification-2026-10-05/model-output-summary.json).
- [Stage timings and available usage](../../../data/admission-optional-schema-verification-2026-10-05/timing-summary.json).
- Raw traces, Android logs and isolated database are retained under
  `data/admission-optional-schema-verification-2026-10-05/client/`.
- Live candidate: `a6b1e404c4997ed2a6d1c2f115fd0feff4adec121b24c4462e985b01bd8bca67`.

The correct task is waiting for a worker and its single execution run is queued,
as expected with workers disabled. There was one `dispatch_task`, no duplicate
admission, no capture-upload records, no provider errors/protocol warnings in the
durable event log, and no auth/quota/tier fallback. Both selector responses
completed on priority. The second was a short post-admission confirmation,
not another task or screenshot round trip. The ready-latency assertion remains
failed; the partial corpus remains blocked rather than being declared passed.

## Actual returned call

```json
{
  "goal": "Draft a plan for a simple notes application, covering core features, user flows, technical approach, data model, and an incremental implementation roadmap.",
  "intent_id": "create-notes-app-plan"
}
```

The actual wire argument string is **202 characters**, with no optional keys.
The returned tool metadata confirms `strict:false` and only the two intended
required fields. First-call usage was **63 output tokens: 8 reasoning and 55
nonreasoning**. The earlier ordinary client trial used 142 output tokens,
including 35 reasoning/107 nonreasoning. These single-sample counts support the
payload reduction; they are not a controlled latency or reasoning comparison.

## Timing findings

The complete partition on Android's monotonic clock is:

- Speech end → delegation event received: **2199.46 ms**.
- Delegation received → selector-start event received: **986.12 ms**.
- Selector-start received → validated-tool event received: **3274.96 ms**.
- Validated-tool event received → committed admission received: **287.52 ms**.
- Total: **6748.07 ms**. Android dispatch after receipt: **0.57 ms**.

Relay intervals include event delivery. On the server's own monotonic clock:

- Delegation → completed transcript correlation: **717.61 ms**.
- Transcript correlation → selector request stage: **161.94 ms**.
- Selector HTTP start → headers: **2627.90 ms**.
- HTTP start → first function-argument delta: **2839.38 ms**.
- Response-created event → first argument delta: **198.73 ms**.
- First → last argument delta: **221.16 ms** across 40 chunks / 202 characters.
- Largest argument-chunk interarrival gap: **16.79 ms**.
- Last argument delta → completed function-call item: **97.55 ms**.
- Headers → complete tool item: **530.23 ms**.
- First-call schema validation: **81.13 ms**.
- Complete tool item → server SSE emission: **417.96 ms**, including validation.

These server measurements overlap the Android partition; do not add them or
subtract timestamps across clock domains. Chunk arrivals are variable-sized
strings, not exact token timestamps. This trial demonstrates that most selector
delay precedes the first argument, with a short argument stream and no large
observed stream stalls. It still cannot separate network, provider queue/prefill
and hidden computation inside that initial delay.

Compared with the earlier ordinary client observation, end-to-end admission was
**0.96 seconds lower** (7.71 → 6.75 s), and headers-to-complete-tool was
**1.70 → 0.53 s**. This is consistent with shorter output, but candidate and
environment variation prevent claiming a guaranteed improvement. The separate
summary-request diagnostic previously reached 6.01 s with a much shorter header
wait; that is not a like-for-like baseline. Provider startup and the roughly
2.2-second pre-delegation interval remain the largest measured delays.

## Shutdown oddity and local correction

The raw export retained 100 events / 52,529 bytes with zero buffer drops and both
completed selector outputs. It was a provider-terminal checkpoint rather than
the adapter's final flush (`final_adapter_flush:false`). This is sufficient for
the captured response/timing evidence, but is not proof of final adapter cleanup.
The repaired checkpoint path prevented the previous complete export loss.

The bounded observation window closed the session during its spoken confirmation;
retained speech ends “Done, I created a task to draft”. The selector's complete
confirmation is “Task created: draft a plan for a simple notes application.”
The clipped voice tail reflects this harness cutoff, not a proven spontaneous
speech failure. Both fixture sessions ended inactive (setup generation superseded;
measured generation disconnected), and the harness stopped its backend/emulator.

One server warning followed clean provider closure: the closing sideband socket
attempted a second error report after the coordinator had committed disconnect.
The stale-generation fence rejected it. No traceback, duplicate admission or
persisted provider error resulted. Other warnings were the deliberate isolated
development backend's passwordless-auth notices.

After the paid run, a small receiver correction suppresses this duplicate error
report once the committed provider-close event has completed. Genuine transport
failures before clean closure still use the existing error path. Regression tests
exercise both normal iterator termination and socket exceptions after closure.
The final adapter/client/diagnostic suite passed **46 tests**.
This cleanup correction is verified offline only; it changes the executable
candidate after the retained live trial. No second paid test was run, and the
earlier live evidence is not relabeled as acceptance of the later candidate.

## Request counts and remaining scope

This run used **one voice-command trial**, **two completed selector responses**,
**4764 input / 79 output tokens**, including 2254 reported cached input tokens,
and **16 reported Live seconds**. The fixture also opened a superseded setup
generation; the available usage is not a guarantee of all account billing.
New worker/orchestration/TTS inference and response-retrieval requests: zero.
Exact cost and remaining account balance are unavailable.

No task-admission malfunction was observed beyond the latency miss. Further
voice-boundary and selector-startup optimization remain separate work; this
ordinary request does not validate multi-intent, screen uploads, cold starts,
worker completion or recovery. Required frozen-candidate M8/M10 acceptance stays
open and M11 physical-device testing remains deferred.

## Follow-up assessment: faster handoff or direct voice tools

Read-only analysis of this same raw trace finds the last input transcript fragment
arrived **30.20 ms before delegation**. The first assistant acknowledgment arrived
**516.42 ms after delegation**, and continued while the selector request ran.
These comparisons use the server recorder's single monotonic clock. They measure
received transcript events, not exact acoustic playback. The current voice prompt
already requires immediate delegation before composing an acknowledgment.
Suppressing “On it” is therefore not a demonstrated way to reclaim the observed
2.2-second speech-end-to-delegation interval: handoff already preceded the
acknowledgment, and the final transcript arrived only shortly before handoff.

The provider delegation offset was 6600 ms, while the final transcript fragment
covered 6600–6800 ms. This misses the adapter's safe `fragment.end <= offset`
condition for immediate flush and uses the bounded 750 ms transcript-gap path;
the measured delegation-to-correlated-transcript interval was 717.61 ms. This is
a concrete remaining wait, not a fresh unconditional two-second delay. Removing
it blindly risks cutting off later details and corrections. Managed selection
can run while correlation settles, retaining the correlation requirement before
durable admission.

The current [GPT-Live API delegates tool selection to a backend](https://developers.openai.com/api/docs/guides/live-delegation).
Its client delegation event supplies metadata, not structured task arguments.
There is no documented top-level Live tool-call setting that bypasses the selector.
Treating every delegation as `dispatch_task` would also confuse reads, cancellation,
clarification, continuations and multi-intent requests. Parsing spoken JSON or
keywords would not preserve the intended routing contract.

[Realtime direct function calling](https://developers.openai.com/api/docs/guides/realtime-conversations#function-calling)
can remove the separate selector request by having the voice model choose tools.
The application can still validate calls and retain the existing durable gateway,
intent identity, captures, permissions and cancellation. It requires a different
voice transport/event adapter, context restoration and conversation tests; it is
not an easy A/B toggle within GPT-Live. Tool-selection work moves into the voice
model, so the entire measured selector duration cannot be assumed to disappear.
The [migration guide](https://developers.openai.com/api/docs/guides/live-migration)
documents the distinct speech/turn/event lifecycles. A prototype would revisit
the original two-routing-mode scope, without adding an unvalidated production route.

The smallest worthwhile evaluation sequence is:

1. Run the corrected minimal schema once through existing managed routing with
   output capture and the same ordinary audio/model/tier. The earlier managed
   4.76-second observation predates the schema fix and has a failed receipt report;
   it cannot establish the current route's timing. Official documentation says
   managed mode prepares known configuration and maintains persistent Responses
   WebSockets. It is already the default route and needs no new router.
2. If worthwhile, test a compact voice prompt organized under the documented
   [delegation policy headings](https://developers.openai.com/api/docs/guides/live-prompting#delegation),
   changing only that prompt. Measure actual delegation arrival, not merely faster
   acknowledgment. Offline checks precede a single pilot; paused speech,
   corrections and multi-intent cases follow only if it is promising. Do not
   infer accuracy or p95 from one request.
3. If client routing remains useful, test prepared Responses WebSocket request
   state using documented [`generate:false` warmup](https://developers.openai.com/api/docs/guides/websocket-mode#connect-and-create-responses).
   The existing HTTP client already reuses its pool; simple HTTP-client reuse is
   not a missing fix. Measure connection phases and provider startup separately.
   Warmup creates no model output, but is not asserted to have zero billing.
4. Consider an isolated Realtime prototype only if those smaller changes do not
   meet the responsiveness needed. Replay the same gateway fixtures offline,
   then use a bounded voice comparison covering ordinary creation, paused/corrected
   input and multiple outcomes before any broader rollout or model sweep.

Earlier handoff alone cannot establish the four-second target: subtracting even
the entire measured 2.2-second pre-delegation interval from 6.75 seconds leaves
about 4.55 seconds. That is an illustrative bound on this observed critical path,
not a prediction. Reducing selector startup or overlapping stages is necessary
as well. Durable records and plural screenshots do not themselves require two
models; they constrain when a validated action may commit.

This follow-up used existing evidence and official documentation only. It made
no new generation calls and changed no routing or model settings.

## Follow-up: complete dependency and timing interpretation

The 6.748-second observation is one **client-routing** diagnostic trial, not a
p95 or a fresh measurement of the default managed route. GPT-Live remains the
voice model in both routes. Workers were disabled: selector latency is not task
execution latency.

On the Android monotonic clock, the complete partition is:

- Recorded command end to delegation-event receipt: **2.199 seconds**.
- Delegation receipt to selector-start event receipt: **0.986 seconds**.
- Selector-start receipt to validated-tool event receipt: **3.275 seconds**.
- Validated-tool receipt to committed-admission receipt: **0.288 seconds**.

These are client-visible relay intervals, including delivery of telemetry. They
sum to 6.748 seconds. They must not be combined with server absolute timestamps.

The server's own monotonic clock gives this finer sequential breakdown from
delegation receipt through emission of the admission event:

- Group and correlate the input transcript: **717.612 ms**.
- Correlation to selector-start telemetry: **161.945 ms**.
- Selector-start telemetry to actual HTTP start: **209.293 ms**.
- Actual HTTP start to response headers: **2627.902 ms**.
- Headers to complete tool-call item: **530.226 ms**.
- Validate the tool call: **81.130 ms**.
- Validation through shell preparation and authority readiness: **107.192 ms**.
- Authority readiness through durable commit: **98.848 ms**.
- Commit through admission-event emission: **130.788 ms**.

Total: **4.665 seconds**, entirely within the server clock. In particular,
`selector_request_start` precedes an awaited event callback; it is not the actual
HTTP start. Its 209 ms interval is application callback/scheduling work rather
than selector inference. The preceding 162 ms also needs finer instrumentation
before attribution to specific local operations.

Within the 530 ms after headers, approximately 212 ms precedes the first argument
fragment, 221 ms streams the arguments, and 98 ms completes the tool-call item.
The selector reported eight reasoning tokens. Network/connection time, provider
queueing, input processing and internal inference within the 2.628 seconds before
headers are not individually observable in this trace.

### Important correction to the speech-end marker

The harness's `command_last_sample_ns` marks the end of the prerecorded command
WAV at input injection. It is not an acoustic end-of-speech detector and is
recorded **before** `LiveActivationAudioHandoff.processWebRtcBuffer`. The file can
also contain trailing silence. Consequently the first 2.199 seconds includes an
unmeasured local audio-buffer/send interval as well as network, provider and event
delivery. Calling all of that interval transcription or voice-model thinking is
not supported by the evidence.

Startup readiness claims the activation audio buffer but does not prove that it
has drained. The handoff queue emits older retained audio while accepting new
audio. Its 250 ms backlog target trims only confident silence and is not a hard
delay ceiling. The retained trace does not record queue age at command end or
when that command's final sample exits the handoff queue. A material contribution
from this buffer is possible, but its size is presently unknown.

GPT-Live emits continuous transcript fragments with approximate timestamps and
no definitive completed conversational-turn event, as documented in
[transcript deltas](https://developers.openai.com/api/docs/guides/live-conversations#transcript-deltas).
Jarvis's transcript-ready event is therefore local grouping/correlation, not a
provider assertion that transcription has just finished. Here the final received
fragment preceded delegation by only 30 ms, but its approximate end offset
exceeded the delegation offset. The 750 ms gap heuristic consequently contributed
the observed 718 ms wait. A provider delegation decision can use audio before
the matching transcript fragments have all arrived, so fragment arrival alone
does not measure when the model understood the request.

### Parallel work and next measurements

The initial acknowledgment began about 516 ms after delegation on the server
recorder and overlapped selector work. Fresh capture ran concurrently, with
approximately 101 ms to the capture callback and 79 ms for encoding. This ordinary
task required no screen upload. Neither acknowledgment completion nor image
analysis gated this admission. A second selector request generated the spoken
confirmation after admission; it adds audible-confirmation latency and usage,
but is outside the 6.748-second committed-admission interval. Worker execution
would follow admission and any required uploads; it was not exercised here.

The next useful offline work is to track command-frame progress and queue age
through the activation handoff, and split the local correlation/event callbacks.
Provider-side startup remains a separate large interval. Existing managed routing
can overlap selector work with local transcript correlation; its benefit requires
a controlled same-candidate pilot after offline checks. No change of voice backend
is needed for these investigations. This assessment changed only this report and
used no additional paid requests.
