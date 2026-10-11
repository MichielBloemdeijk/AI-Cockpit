# Matched managed/client admission comparison — 2026-10-05

Managed routing admitted the ordinary notes-plan task in **3.551 / 3.918 seconds**;
client routing took **5.456 / 4.791 seconds**. Managed was **1.389 seconds (27.1%)
faster on the two-sample mean**. All four admissions were correct. This is a
targeted diagnostic comparison, not a p95 estimate or M8/M10 acceptance pass.

## Controls and scope

The user explicitly requested the same input twice through each route. After
**68 offline adapter/client/harness/diagnostic tests passed**, four single-case
pilots ran in managed/client/client/managed order. Each used the existing
`ready07` recording: “Create a task to draft a plan for a simple notes application.”
No additional paid retries or model sweeps followed.

All trials used GPT-Live 1, GPT-5.6 Luna, low reasoning effort, priority, the same
shared tool definitions and routing instructions, no reasoning summaries, the
same debug APK and API37 software-rendered emulator configuration. Diagnostics
were enabled in both modes. Each trial used a new isolated backend/database and
began its command after Live readiness. Managed mode uses provider-assembled
conversation context; client mode builds its own transcript snapshot. Context
assembly is part of the comparison, not forced to byte-identical provider input.

The retained analysis verifies identical input PCM, executable source digest,
runtime, and settings except route mode and isolated directory paths. Candidate
IDs differ because those intentional configuration differences are part of
candidate identity. Backend/runtime/database identity and stability checks were
retained individually; no evidence was relabeled to another candidate.

- Executable source digest: `250a23bbe3d89aed0ded698678bb4cda30c017bc64bbf6fe5b86d000e6bfcb29`.
- Recorded command WAV SHA256: `cae522eda620faefa41e28b95523825220d5dea5453199fab89ec22643db17a0`.
- Debug APK SHA256: `268f14e1b82221f3d7075e407fc30d3823b7676dcd7a4478ba239cbd4f31d86b`.
- Runtime: Python 3.12.9, pinned Codex 0.155.1, Windows 10 build 19045.
- Workers, orchestration and new TTS generation were disabled. These trials test
  end-to-end **task admission**, not execution of the admitted task.

## Complete admission timings

These intervals all use Android's monotonic clock and sum to each trial's
committed-admission receipt. Stage notifications include their delivery time.
“Input end” is the harness's command-WAV end before the activation handoff queue,
not a detected acoustic word boundary or final outbound RTP frame.

| Trial | Route | Input end → delegation | Delegation → validated tool | Validated tool → admission receipt | Total |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | Managed | 2.259 s | 1.050 s | 0.242 s | **3.551 s** |
| 2 | Client | 2.232 s | 3.043 s | 0.181 s | **5.456 s** |
| 3 | Client | 2.134 s | 2.344 s | 0.312 s | **4.791 s** |
| 4 | Managed | 2.684 s | 1.019 s | 0.215 s | **3.918 s** |

Two-sample means:

| Stage | Managed | Client |
| --- | ---: | ---: |
| Input end → delegation | 2.471 s | 2.183 s |
| Delegation → validated tool | 1.034 s | 2.693 s |
| Validated tool → admission receipt | 0.229 s | 0.247 s |
| Complete admission | **3.734 s** | **5.123 s** |

The managed advantage concentrates after delegation: **1.659 seconds** on that
stage's mean. Managed's pre-delegation interval happened to be 0.288 seconds
longer, leaving a net 1.389-second advantage. This does not establish that the
route systematically changes GPT-Live's pre-delegation behavior.

Both managed observations were below four seconds; both client observations
exceeded four seconds. The two-second target was not met. The harness retains a
failed ready-latency gate and blocked partial corpus even for managed because
the required sample counts and other cases are absent. The one-sample `p95_ms`
values in individual harness statistics are not a meaningful p95 claim.

## Server and provider breakdown

The following uses only the server's monotonic clock. It is a zoom into the
pipeline and must not be added to the Android relay intervals above.

| Measurement | Managed trial 1 | Managed trial 4 | Client trial 2 | Client trial 3 |
| --- | ---: | ---: | ---: | ---: |
| Delegation → correlated transcript | 656 ms | 65 ms | 720 ms | 733 ms |
| Delegation → actual client HTTP start | Unexposed | Unexposed | 773 ms | 796 ms |
| Delegation → managed response observed | 32 ms | 95 ms | N/A | N/A |
| Client HTTP start → headers | Unexposed | Unexposed | 1655 ms | 949 ms |
| Client HTTP start → first argument fragment | Unexposed | Unexposed | 1918 ms | 1241 ms |
| Managed response created → first argument fragment | 682 ms | 650 ms | N/A | N/A |
| First → last argument fragment | 233 ms | 148 ms | 185 ms | 216 ms |
| Last argument fragment → completed call item | 34 ms | 37 ms | 121 ms | 54 ms |
| Tool validation | 66 ms | 60 ms | 58 ms | 59 ms |
| Complete call item → admission-event emission, including validation | 337 ms | 331 ms | 267 ms | 398 ms |

Managed response observation precedes transcript correlation in trial 1. Its
selection therefore overlaps the 656 ms local grouping interval. In trial 4,
correlation took only 65 ms; managed still reached a validated tool in about one
second after delegation. The benefit is consequently not just removing a fixed
750 ms wait. Request/context preparation and provider startup differ as well.

Client's correlation-to-selector marker took 27/36 ms, and the awaited marker
callback-to-actual-HTTP interval took 25/27 ms. The earlier sample's 162/209 ms
local overhead did not recur at that size here. Client's major variability was
HTTP start-to-headers: 1.655 versus 0.949 seconds. Network/connection work,
provider queueing, input processing and internal inference remain unsplit in
that interval. Managed exposes response stream events rather than an equivalent
application HTTP-start timestamp, so an exact connection-time comparison is
unavailable.

Actual argument streaming remained short in both modes. First-call reasoning
token counts were **41/15 managed**, **20/9 client**. More reported reasoning
tokens did not imply a slower observed managed admission. These are exposed
usage counts, not a hidden-thinking timeline.

## Correctness and output differences

Every isolated database contains exactly one `dispatch_task` admission, one
faithful notes-application planning goal, and one queued execution run. The
task status is `waiting_for_worker`, consistent with workers being disabled.
Android received the committed task/run identity in all four trials. There were
no persisted provider errors, protocol warnings, tier fallbacks, duplicate tasks
or image-upload records. Both selector responses in each trial completed on
priority. The second response generated post-admission confirmation and did not
create another task.

The goals vary in wording and level of detail, but all preserve the requested
outcome. Managed trial 1 additionally emitted `importance: normal` and
`presentation_policy: follow_progress`. Managed trial 4 and both client trials
emitted only `goal` and `intent_id`. Optional arguments were not required; this
is model output variation rather than another schema-normalization failure.

Raw diagnostic recorders report zero dropped events for all four trials. Managed
terminal response envelopes omit their output arrays; the complete tool arguments
and confirmation text are retained in streamed `response.output_item.done`
events. The read-only comparison analysis explicitly retains those completed
items and calls rather than treating an empty terminal array as no tool call.

The bounded five-second post-admission observation tail clipped client trial 2's
spoken confirmation after “It's waiting to start and”. Its complete selector
confirmation is retained. This is not full spoken-playback acceptance; it does
not invalidate the earlier committed-admission endpoint. No real task repair,
screen-task context selection, multi-intent, recovery or cold-start acceptance
is established by these four ordinary requests.

## Usage, artifacts and next step

Available usage for this comparison is **four voice-command trials**, **eight
completed selector responses** (four task selections and four confirmations),
**23,627 selector input / 406 output tokens**, and **60 reported Live seconds**.
Managed totals were 14,058 input / 212 output tokens; client totals were
9,569 input / 194 output tokens. Managed supplied more input context. Prompt
cache state differed naturally: first selections had zero cached input, later
ones had partial cached input. A larger cohort would be needed to separate
cache, ordering and route effects.

The fixture opened a superseded setup generation as well as each measured
generation. Available usage does not guarantee complete account billing for
those setup sessions. Exact monetary cost remains unknown. Worker/orchestration/
new-TTS inference was zero. No extra paid trial followed the requested four.

- [Complete comparison, controls, calls, outputs and usage](../../../data/admission-routing-paired-2026-10-05/complete-comparison.json).
- [All within-clock timing summaries](../../../data/admission-routing-paired-2026-10-05/timing-summary.json).
- [Read-only analysis script](../../../data/admission-routing-paired-2026-10-05/analyze.py).
- Individual `trial*/` folders retain isolated databases, Android logs, acceptance
  observations, provider traces and model-output summaries.

Managed remains the preferable existing default for this ordinary workload.
The next local investigation should split the **2.26–2.68 second managed
pre-delegation interval**, especially activation-buffer residence and final-frame
release. This evidence does not justify speculative routing or a different voice
backend. Keep the managed configuration and investigate that audio delivery seam
offline before another paid comparison. M8/M10 still need the required frozen
candidate cohort and all functional/recovery cases; M11 remains deferred.

The test harness stopped its own backend/emulator process trees. Only evidence
and report documentation changed during this task; runtime routing behavior did
not change.

## Documentation and trace investigation — 2026-10-06

The managed advantage is consistent with documented preparation and context
ownership, rather than evidence that the identical model decodes tokens faster.
This follow-up used source inspection, retained traces and public sources only.
No additional inference requests or runtime changes were made.

### What public sources establish

The [official delegation latency guide](https://developers.openai.com/api/docs/guides/live-delegation#reduce-backend-latency)
explicitly describes persistent Responses WebSockets, advance preparation of
known request configuration and possible reuse of prior response state in
managed mode. Its client guidance recommends similar preparation and warmup.

OpenAI's [GPT-Live engineering account](https://openai.com/index/continuous-voice-interaction-with-gpt-live/)
describes preparing the frontier model and tools at voice-session startup,
prefilling initial conversation context, and retaining inference-session affinity.
It also distinguishes provisional conversation state from finalized transcript
records. This describes the underlying design, not a per-request API guarantee
of a dedicated worker or proof that every incoming speech fragment is prefilling
our Luna request. The API guide confirms the narrower public preparation contract.

The [Responses WebSocket engineering account](https://openai.com/index/speeding-up-agentic-workflows-with-websockets/)
explains that persistent connections can reuse request state, rendered tokens,
tool definitions and routing decisions. Avoiding repeated API processing can
improve first-output latency independently of the model's token-generation speed.
Its multi-step agent benchmarks cannot predict savings for our one-call admission.

The [WebSocket API guide](https://developers.openai.com/api/docs/guides/websocket-mode#connect-and-create-responses)
exposes `generate:false` warmup: prepare known input/configuration without model
output, then continue from its response ID with new input. Preparation need not
generate a speculative task call or require knowing the eventual command. It is
not asserted to be free of billing.

External primary sources support the integration distinction:

- [LiveKit's GPT-Live plugin guide](https://docs.livekit.io/agents/models/realtime/plugins/gpt-live/#client-delegation)
  describes client delegation as an ID plus application-built context. Its
  `pending_transcript` supplies the current provisional user turn, illustrating
  that client work need not always wait for a finalized chat-history item. This
  property is constructed by LiveKit, not additional task text supplied by the
  raw OpenAI delegation event. LiveKit-specific instruction/update restrictions
  are not used to override OpenAI's API contract or Jarvis's own adapter.
- [Vercel's first-party WebSocket comparison repository](https://github.com/vercel-labs/ai-sdk-openai-websocket)
  provides a persistent Responses transport and an HTTP-versus-WebSocket
  time-to-first-byte demo. It supplies an independent implementation example,
  not proof of GPT-Live's internal scheduling or a Jarvis latency guarantee.
- [The DistServe research paper](https://arxiv.org/abs/2401.09670)
  distinguishes input prefill/time-to-first-token from output decoding/time per
  token. This supports measuring those phases separately; it does not identify
  OpenAI's deployed inference architecture.

### Additional evidence from the four retained traces

All comparisons below use the server recorder's single monotonic clock.
The last input transcript fragment was received before delegation in every
trial, and the traces contain no input fragments after that delegation:

- Managed trial 1: **144.952 ms before** delegation.
- Client trial 2: **17.927 ms before** delegation.
- Client trial 3: **17.356 ms before** delegation.
- Managed trial 4: **413.704 ms before** delegation.

Thus the 720/733 ms client correlation intervals added no new words in these
samples. They protected grouping/identity certainty under approximate transcript
timestamps; they were not time spent transmitting more context to Luna.

Managed response creation was observed 32/95 ms after delegation, with first
argument fragments about 714/745 ms after delegation. Client HTTP started
773/796 ms after delegation and first arguments followed about 1.918/1.241 s
later: roughly 2.690/2.037 s after delegation. These received events show the
managed work starts while client mode is still preparing its request. They do
not reveal the exact provider-side generation-start or prefill timestamps.

Managed's first response-created-to-first-argument interval was 682/650 ms,
versus client's 262/290 ms once its response-created event finally arrived.
Argument streaming averaged about 191 ms managed and 201 ms client. This is
consistent with savings before/around request startup rather than materially
faster output streaming. A Responses `response.created` event is a lifecycle
notification, not a guarantee that the next output token has already been sampled.

First managed/client selections both reported zero cached input tokens; later
managed/client selections reported 2923/2231. An ordinary prompt-cache hit alone
cannot explain the initial managed advantage. Preparation and persistent state
are separately documented, but their individual savings are not measured here.

The client code already shares an HTTP connection pool. Each isolated trial's
first selector HTTP request nevertheless starts only after correlation; it does
not open a persistent Responses WebSocket or run advance `generate:false`
preparation. Subsequent client requests send a rebuilt conversation input.
Managed receives its model, tools and instructions at voice-session creation.

### Interpretation and options

There is support for **early preparation**, and observed support for **overlap
with local transcript grouping**. There is no public evidence that managed Luna
generates an actionable tool call from an intentionally incomplete command before
GPT-Live delegates, nor that it accepts arbitrary new speech fragments into an
already-generating response. The engineering account's provisional conversation
view should not be equated with speculative admission.

If client mode were needed, persistent Responses WebSockets plus warmup could
move known configuration/connection work earlier while preserving GPT-Live and
Luna. Starting from the current transcript at delegation could remove more local
waiting, but needs explicit handling of late fragments, corrections, ambiguous
turn identity, cancellation and replay before admission. It is not a safe blanket
removal of the correlation requirement. Managed already supplies the useful
provider-side preparation and overlaps selection with our durable checks.

Keep managed as the default and prioritize local audio-buffer/final-frame
measurement. The same model can have a much lower end-to-end response time when
input preparation and connection work happen before the measured handoff. The
two-per-route comparison still does not separate provider queueing, connection
setup, prefill and hidden inference precisely, or establish a population p95.
