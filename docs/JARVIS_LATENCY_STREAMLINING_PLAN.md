# Jarvis latency streamlining implementation plan

Prepared: 2026-09-08. Status: phases 0–3 implemented and automated gates verified
on 2026-09-09; measured checkpoint and manual/device matrix remain pending.

This document is the handoff contract for the next implementation agent. It
replaces architectural choices with named interfaces, state transitions,
configuration defaults, test obligations, and completion gates. Code snippets
are structural skeletons, not copy-paste implementations. The implementer may
adjust private helper names, but must preserve the contracts and gates below.

## Outcome and implementation boundary

Keep the local wake detector, warm OpenAI Realtime connection, native audio
understanding, backend response authority, and existing browser microphone
permission model. Remove competing ownership of audio commits, bind every
cancel to the response that was actually present at speech onset, and make the
wake-to-conversation audio stream continuous.

The required delivery is phases 0–3 followed by the measured checkpoint. Phase
4 is an alternative endpointing experiment and may start only if the checkpoint
proves it is necessary. Phases 5 and 6 are subsequent optimizations and must not
be mixed into the phases 0–3 latency baseline.

This plan does not authorize changes to the Jarvis product roadmap, wake-model
training, prompts, task semantics, voice selection, Android architecture, or
provider model. It also does not treat a prerecorded acknowledgement as a
Jarvis response.

## Evidence and known measurement limits

The source for the baseline was read-only inspection of
`backend/data/ai_cockpit_jarvis.db`, specifically `voice_session_events` and
`jarvis_realtime_trace_events`. Times below are Europe/Amsterdam on 2026-09-07.

Five new wake activations, generations 179–183, used Immediate mode:

- Generation 179 at 20:08:31: first playback proxy 1,835.7 ms; response request
  to first provider audio 891 ms.
- Generation 180 at 20:08:49: first playback proxy 2,321 ms; request to provider
  audio 922 ms.
- Generation 181 at 20:09:09: first playback proxy 2,251.2 ms; request to provider
  audio 875 ms.
- Generation 182 at 20:09:56: first playback proxy 1,356.8 ms; request to provider
  audio 907 ms.
- Generation 183 at 20:10:13: response created and then cancelled before first
  audio. A later follow-up produced audio and must not be counted as the first
  activation response.

The four completed initial responses have a provisional median of 2,043.5 ms.
The fifth activation remains a cancellation in the reliability denominator.
This is not enough data for a production success rate or p95 claim.

Generation 184 used Transcript rules and recorded a 2,969.8 ms playback proxy,
including a reported 532 ms boundary-to-transcript interval. Earlier
transcript-gated playback proxies varied from approximately 1.64 to 4.59
seconds. Earlier transcription waits were approximately 0.47–1.08 seconds.
This supports removing transcription from the speed baseline, but it is not a
controlled percentage improvement.

Measurement caveats that the implementation must preserve:

- The browser playback marker means inbound audio bytes increased while
  playback was enabled. It is not direct proof that sound reached the speaker.
- Generations 179, 181, and 182 were attributed through a synthetic follow-up
  boundary. Existing per-turn summaries are not reliable enough to repair this
  after the fact.
- Missing transcription is valid for Immediate mode and cannot turn otherwise
  successful audio into a failure.
- A connection that became ready before wake is not part of post-speech latency.
  All five Immediate runs had warm standby approximately 3.5–16.3 seconds old.
- Existing browser arbitration intervals can span turns. Backend arbitration
  for the five initial requests was 31–78 ms.
- Existing boundary-to-transcript timing uses a session-wide last boundary and
  may be overwritten by another input item.
- Existing gate duration begins at activation registration and includes speech
  time. It is not pure post-command intent-classification latency.
- Later `ConnectionClosedError` events occurred near shutdown and are not proof
  that speech was interrupted.

## Current causes to remove

1. The session is configured with `semantic_vad`, while both warm
   `activateCandidate()` and cold `submitWakeAudio()` manually send
   `input_audio_buffer.commit`. Disabling automatic response creation does not
   disable provider VAD chunking.
2. Backend `user_speaking` handling starts a delayed cancel that looks up the
   current response only after 300 ms. It can therefore cancel a response that
   did not exist when speech began.
3. Warm candidate capture stops at the local endpoint and waits for a gate
   decision before connecting the live microphone. Audio in that transition is
   not owned by one ordered stream.
4. The local endpoint is derived from completed wake-inference windows. The
   configured 300 ms is neither a wall-clock guarantee nor a sample-timeline
   guarantee under worker load.
5. Warm activation does not initialize the local barge-in observer used by the
   cold path.
6. Developer benchmark mode returns to `transcript_rules` on page mount, so
   reloads can silently change the experiment.
7. Turn summaries infer identity from event order instead of carrying
   activation, input item, response request, and response IDs end to end.

Primary implementation locations are:

- `frontend/lib/jarvis-realtime.ts`
- `frontend/lib/jarvis-wake-runtime.ts`
- `frontend/lib/jarvis-wake-engine.ts`
- `frontend/lib/jarvis-hey-buddy-inference.ts`
- `frontend/lib/jarvis-wake-worker.ts`
- `frontend/lib/jarvis-wake-protocol.ts`
- `frontend/lib/jarvis-vad.ts`
- `frontend/app/(app)/jarvis/page.tsx`
- `frontend/lib/api.ts`
- `backend/app/config.py`
- `backend/app/models/jarvis.py`
- `backend/app/api/jarvis.py`
- `backend/app/services/voice_session_adapter.py`
- `backend/app/services/openai_realtime_adapter.py`
- `backend/app/services/jarvis_coordinator.py`
- `backend/app/services/jarvis_realtime_telemetry.py`

## Fixed design decisions

The following are decided and are not left to the implementer.

1. The phase-2 default turn policy is provider-owned `server_vad` with a 350 ms
   silence duration, threshold 0.5, and 300 ms prefix padding.
2. `create_response` and `interrupt_response` stay `false`. Only the backend
   response arbiter creates responses; browser and provider VAD never do.
3. A session has exactly one input commit owner. `server_vad` and `semantic_vad`
   are provider-owned. The phase-4 `client_vad` alternative disables provider
   turn detection and is client-owned. No configuration may combine enabled
   provider VAD with a client commit.
4. Warm and cold wake activations use data-channel PCM as the sole microphone
   input for the active conversation. The outgoing WebRTC audio track stays
   silent. This removes the unordered PCM-to-RTP transfer from phases 1–3.
   Manual Arm retains its existing direct-RTP path for phases 0–3 and is excluded
   from wake benchmark cohorts.
5. Candidate capture starts immediately at local wake detection, before network
   or backend readiness. Bounded PCM is flushed as soon as the data channel is
   ready. Capture does not stop at the local endpoint.
6. The local endpoint becomes an observation and watchdog signal only. It does
   not commit, stop capture, authorize a response, or switch transports.
7. The provider `input_audio_buffer.committed` event is the authoritative input
   boundary for phases 1–3. `speech_stopped` is observational.
8. Response creation requires two facts for a wake activation: the exact input
   item has committed and the exact activation gate has accepted. Immediate
   mode is accepted at activation registration and never waits for transcript
   finalization; response creation still waits for the matching commit.
9. A cancel intent captures the response request ID present at speech onset. A
   delayed callback may act only if that same attempt is still current. It must
   never query and cancel a replacement attempt.
10. A known empty manual commit is recoverable only when its event ID matches a
    client commit sent by this session and another commit already produced the
    input item for that activation. Unknown or uncorrelated protocol errors
    remain failures.
11. One terminal result is emitted per activation. Generation outcome,
    playback observation, cancellation, gate result, and transcription state
    are separate fields.
12. No raw audio or transcript content is added to latency telemetry.
13. The backend provider adapter is the sole sender of `response.cancel`. The
    browser mutes immediately and reports a correlated barge-in intent; it does
    not race the backend by sending its own provider cancel.

## Terminology and invariants

- **Session key:** `(session_id, generation)`. It rejects stale events but does
  not identify a turn.
- **Activation ID:** browser-generated UUID created at wake detection and used
  unchanged by runtime, API, coordinator, telemetry, and tests.
- **Input item ID:** provider ID for one committed user audio item.
- **Request ID:** backend-generated ID for one authorized `response.create`.
- **Response ID:** provider-generated ID for one response.
- **Sample range:** half-open `[start_sample, end_sample)` positions in the
  activation's 16 kHz capture timeline.
- **Warm activation:** wake attaches to an already ready standby session.
- **Connecting activation:** wake attaches to the one standby connection already
  in progress.
- **Cold activation:** no healthy or connecting standby exists, so the existing
  bounded candidate is used while one new connection is created.

Global invariants:

- One microphone acquisition per armed page lifecycle.
- One active candidate per session key.
- One owner for every sample range and one append at most for every sample range.
- One provider commit per logical utterance in provider-owned modes.
- One response request at most for `(activation_id, input_item_id)`.
- One active response attempt per session key, preserving existing task-update
  serialization.
- One terminal benchmark record or explicit failure per wake activation and per
  committed follow-up input item.
- No response or tool execution for rejected, timed-out, stale, or uncommitted
  wake input.
- No cancellation without a captured request ID from speech onset.
- No microphone-bearing RTP track while the wake listener is merely armed.

## Target event flow

The normal warm path after phases 1–3 is:

```text
wake worker        browser controller       backend coordinator       provider
    | wake + PCM            |                         |                    |
    |---------------------->| create activation UUID |                    |
    | continuous chunks     |------------------------>| register gate      |
    |---------------------->| append ordered PCM ------------------------->|
    | local endpoint        | trace only              |                    |
    |---------------------->|                         |                    |
    |                       |<---------------- input item committed --------|
    |                       |------------------------>| committed(item_id)  |
    |                       |                         | gate + commit ready |
    |                       |                         |---- response.create>|
    |                       |<---------------- response.created/audio ------|
```

Transcript-rules mode may deliver the transcript before or after the commit.
The coordinator stores both prerequisites and creates the response exactly once
when both are true. Immediate mode accepts at activation registration; it never
waits for a transcript.

## Public and internal contracts

### 1. Backend audio policy

Add a validated policy object in `backend/app/config.py`. Environment field
names are fixed so experiments are reproducible:

```python
JarvisRealtimeTurnMode = Literal["server_vad", "semantic_vad", "client_vad"]

class Settings(BaseSettings):
    jarvis_realtime_turn_mode: JarvisRealtimeTurnMode = "server_vad"
    jarvis_realtime_server_vad_silence_ms: int = 350
    jarvis_realtime_server_vad_threshold: float = 0.5
    jarvis_realtime_server_vad_prefix_ms: int = 300
```

Validation requirements:

- silence is 100–2,000 ms;
- threshold is 0.0–1.0;
- prefix is 0–2,000 ms;
- `client_vad` produces `turn_detection: null`;
- all provider-owned modes produce no browser commit;
- no compatibility flag may re-enable dual ownership.

Derive and expose this immutable session policy:

```python
@dataclass(frozen=True, slots=True)
class RealtimeAudioPolicy:
    configuration_id: str
    turn_mode: Literal["server_vad", "semantic_vad", "client_vad"]
    commit_owner: Literal["provider", "client"]
    silence_duration_ms: int | None
    vad_threshold: float | None
    prefix_padding_ms: int | None
    semantic_eagerness: Literal["low", "medium", "high", "auto"] | None
    create_response: Literal[False] = False
    interrupt_response: Literal[False] = False
```

The builder assigns all mode-dependent fields; callers do not fill them in:

- `server_vad`: provider owner, configured silence/threshold/prefix, null
  semantic eagerness;
- `semantic_vad`: provider owner, configured semantic eagerness, null
  silence/threshold/prefix;
- `client_vad`: client owner and null for every provider-VAD field.

`configuration_id` is deterministic, for example
`server_vad:s350:t0.5:p300`, `semantic_vad:emedium`, or `client_vad:v1`. It is
recorded on connection and activation events.

Carry the value through existing coordinator result objects rather than reading
global settings in API handlers:

```python
@dataclass(frozen=True, slots=True)
class ControllerBootstrap:
    session: SessionRecord
    audio_policy: RealtimeAudioPolicy
    sdp_answer: str | None = None

@dataclass(frozen=True, slots=True)
class WakeActivationRegistration:
    activation: _WakeActivation
    audio_policy: RealtimeAudioPolicy
```

`register_wake_activation()` returns `WakeActivationRegistration` after loading
the session's persisted policy. Existing null-policy sessions are not eligible
for warm activation; reconnect them once to obtain an explicit policy.

### 2. API policy and activation contract

Add `JarvisRealtimeAudioPolicyView` to `backend/app/models/jarvis.py` and the
matching TypeScript interface to `frontend/lib/api.ts`. Add `audio_policy` to
every `JarvisRealtimeCallResponse`: it is non-null for controller calls and for
an observer attached to a session, and null only when an observer has no session.

Persist the immutable policy JSON on each new `RealtimeSession` row as
`audio_policy_json`. Existing rows may be null after migration. Compute the
policy once in `bootstrap_controller()`, pass that same object to the adapter,
return it in the call response, and read it for later warm activation responses.
Never recompute policy from changed process settings for an already-created
session.

Wake activation IDs become required before `createJarvisRealtimeCall()` is
called. Preserve the existing activation endpoint for warm/connecting sessions.
The response remains authoritative:

```typescript
interface JarvisRealtimeAudioPolicy {
  configuration_id: string;
  turn_mode: "server_vad" | "semantic_vad" | "client_vad";
  commit_owner: "provider" | "client";
  silence_duration_ms: number | null;
  vad_threshold: number | null;
  prefix_padding_ms: number | null;
  semantic_eagerness: "low" | "medium" | "high" | "auto" | null;
  create_response: false;
  interrupt_response: false;
}

interface JarvisRealtimeActivationResponse {
  session_id: string;
  generation: number;
  activation_id: string;
  gate_state: "pending" | "accepted" | "rejected" | "timed_out";
  gate_mode: "transcript_rules" | "immediate";
  gate_timeout_ms: number;
  audio_policy: JarvisRealtimeAudioPolicy;
}

interface JarvisBargeInRequest {
  generation: number;
  speech_event_id: string;
  source: "browser_local_vad" | "browser_provider_vad";
  request_id: string;
  response_id: string | null;
  input_item_id: string | null;
}

interface JarvisBargeInResponse {
  accepted: boolean;
}
```

Add `POST /api/jarvis/realtime/sessions/{session_id}/barge-in`. It returns 202
after validating the current generation and capturing the exact attempt, and is
idempotent on `(session_id, generation, speech_event_id, request_id)`. A stale or
replacement request ID returns 202 with `accepted: false` and performs no cancel.

The activation request/context also accepts nullable `benchmark_run_id` and
`benchmark_fixture` strings, each at most 64 characters. The API accepts them
only while Developer Lab is enabled, copies them into metadata-only telemetry,
and never puts them in conversation prompts or transcript events.

The backend rejects an activation ID already active on a different session key
with HTTP 409. Re-registering the same ID on the same session key is idempotent
and returns the existing state. Maintain a process-local activation-ID index
through session close plus a five-minute tombstone period, capped at 1,024
entries and pruned oldest-expiry-first; durable session events remain the audit
record, not a new activation database table.

### 3. Ordered candidate audio contract

Replace callback-only raw arrays with an explicit chunk type shared by
`jarvis-wake-runtime.ts` and `jarvis-realtime.ts`:

```typescript
interface JarvisAudioChunk {
  activationId: string;
  sequence: number;
  sampleRate: 16_000;
  startSample: number;
  endSample: number;
  capturedMonotonicMs: number;
  samples: Float32Array;
}

interface JarvisLocalEndpoint {
  sampleRate: 16_000;
  speechEndSample: number;
  observedAtSample: number;
  trailingSilenceSamples: number;
  processingLagMs: number;
}

type JarvisCandidateCloseReason =
  | "rejected"
  | "timed_out"
  | "stale_generation"
  | "disconnected"
  | "rearmed"
  | "sleep"
  | "audio_backlog_overflow";

interface JarvisCandidateAudio {
  activationId: string;
  sampleRate: 16_000;
  subscribe(listener: (chunk: JarvisAudioChunk) => void): () => void;
  observeEndpoint(): Promise<JarvisLocalEndpoint>;
  promoteToConversation(): void;
  close(reason: JarvisCandidateCloseReason): void;
}
```

Rules:

- The first chunks include bounded pre-roll. Sequence begins at zero and sample
  ranges are contiguous.
- The runtime splits capture into 20 ms logical chunks (320 samples at 16 kHz).
  The sender may combine at most two chunks into one 40 ms append.
- Late subscription drains queued chunks before live chunks.
- Only one subscriber is allowed. A second subscription throws.
- `observeEndpoint()` resolves once and has no ownership side effects.
- `promoteToConversation()` disables wake classification but continues VAD
  observation and chunk delivery.
- `close()` is idempotent, zeroes unsent PCM, and stops delivery without stopping
  the shared microphone track.
- Soft backlog is 500 ms. Hard retained backlog is 30 seconds. Crossing the hard
  limit fails the activation closed and records `audio_backlog_overflow`.
- PCM is zeroed after successful encoding or terminal discard.

Put the reusable queue/state implementation in a new
`frontend/lib/jarvis-activation-audio.ts`, with tests in
`frontend/lib/jarvis-activation-audio.test.ts`. Keep browser capture mechanics in
`jarvis-wake-runtime.ts`.

### 3A. Wake inference and speech-observation contract

Split the stateful Silero VAD pass from the expensive spectrogram, embedding,
and wake-classifier pass. Use this interface between `jarvis-wake-engine.ts` and
`jarvis-hey-buddy-inference.ts`:

```typescript
interface JarvisSpeechObservation {
  startSample: number;
  endSample: number;
  speechProbability: number;
  completedMonotonicMs: number;
}

interface JarvisWakeInference {
  observeSpeech(
    frame: Float32Array,
    range: { startSample: number; endSample: number },
  ): Promise<JarvisSpeechObservation>;
  classifyWake(window: Float32Array): Promise<{ wakeProbability: number }>;
  resetWakeState(): void;
  destroy?(): Promise<void> | void;
}
```

The implementation rules are fixed:

- `observeSpeech()` receives non-overlapping 1,920-sample frames in capture
  order. Do not feed overlapping rolling windows to stateful VAD.
- Speech frames use a FIFO and are never replaced by a newer frame. The existing
  latest-only coalescing is permitted only for wake-classification windows.
- Before detection, run `classifyWake()` on the latest 17,280-sample rolling
  window after its corresponding speech observation. Do not run it until a full
  window exists.
- At detection, stop scheduling spectrogram, embedding, and wake-classifier work.
  Continue `observeSpeech()` on every sequential frame until the activation
  closes. Rearm calls `resetWakeState()` and permits wake classification again;
  model sessions remain loaded.
- Candidate PCM delivery is independent of inference completion. A slow VAD may
  delay only the local observation, never provider-bound audio.
- Bound the speech-observation FIFO to 30 seconds. In a provider-owned mode,
  overflow records `speech_observer_overflow` and disables local endpoint
  observation for that activation without dropping provider audio. In
  `client_vad`, the same overflow fails the activation closed before any commit.
- Report processing lag as the duration from the frame's ending sample capture
  time to observation completion. Endpoint silence is accumulated from the
  ranges of sequential below-threshold frames, not inference call count.

`HeyBuddyOnnxInference` owns the ONNX sessions and VAD recurrent state. The wake
engine owns scheduling, sample ranges, mode transitions, thresholds, and queue
limits. Neither layer may commit input or stop microphone capture.

### 4. Provider-neutral input event contract

Replace the misleading normalized event kind `wake_audio_committed` with
`user_input_committed`. Update the fake adapter and all tests. During the adapter
rename commit, the coordinator accepts the old name as an alias so intermediate
commits remain runnable. Remove the alias before the phase-2 gate; no production
adapter emits the old name after phase 2.

Normalized payloads are:

```python
VoiceProviderEvent(kind="user_speaking", payload={
    "item_id": str,
    "audio_start_ms": int | None,
})

VoiceProviderEvent(kind="user_stopped", payload={
    "item_id": str,
    "audio_end_ms": int | None,
})

VoiceProviderEvent(kind="user_input_committed", payload={
    "item_id": str,
    "previous_item_id": str | None,
})

VoiceProviderEvent(kind="user_transcript", payload={
    "item_id": str,
    "transcript": str,
    "timing_ms": {"item_commit_to_transcript": float} | {},
})
```

Do not keep `last_user_boundary_monotonic` as the source for transcript timing.
Store boundary timestamps by input item ID in `_OpenAISession`, prune them after
the transcript and terminal turn result, and cap the map at 64 entries.

`assistant_generating`, `assistant_speaking`, `assistant_response`, and
`assistant_interrupted` must carry all available correlation fields:

```python
{
    "activation_id": str | None,
    "input_item_id": str | None,
    "request_id": str,
    "response_id": str,
}
```

Add `activation_id` and `input_item_id` to response metadata in
`OpenAIRealtimeAdapter.create_response()`. Extend the provider-neutral adapter
method with required keyword-only nullable arguments so every caller states its
correlation explicitly; update the fake adapter in the same commit.

### 5. Coordinator activation and response contracts

Expand `_WakeActivation` and `_ResponseAttempt`; do not create a second parallel
gate implementation.

```python
@dataclass(slots=True)
class _WakeActivation:
    activation_id: str
    wake_score: float
    gate_mode: Literal["transcript_rules", "immediate"]
    verification_state: Literal["pending", "accepted", "rejected", "timed_out"]
    lifecycle_state: Literal["candidate", "conversation", "terminal"]
    registered_monotonic: float
    committed_monotonic: float | None = None
    primary_input_item_id: str | None = None
    candidate_item_ids: set[str] = field(default_factory=set)
    response_requested_for: set[str] = field(default_factory=set)

@dataclass(slots=True)
class _ResponseAttempt:
    request_id: str
    reason: str
    activation_id: str | None
    input_item_id: str | None
    # existing presentation/state/timing fields remain
```

Use one helper as the only place that authorizes the first wake response:

```python
async def _maybe_queue_wake_response(
    self,
    session_id: str,
    generation: int,
    activation: _WakeActivation,
) -> None:
    """Queue once only when gate accepted and primary input committed."""
```

The helper returns without side effects unless the activation is current,
accepted, has `primary_input_item_id`, and that item is not already in
`response_requested_for`. It inserts the item into the set before awaiting
response creation and changes `lifecycle_state` to `conversation`. If provider
submission fails, existing arbiter retry state is retained, but no unrelated
provider boundary may start a new request.

Provider `speech_stopped` never calls the arbiter. `user_input_committed` sets
the activation's primary item if unset and calls the helper. A transcript event
updates only the matching item, makes the gate decision when still pending, and
calls the same helper. Late transcript metadata may be persisted after response
creation without creating another response.

Immediate mode is marked accepted during activation registration. Transcript
rules remain pending until a matching transcript. Their two-second timeout starts
when the primary input item commits, not when the user begins speaking; record
both registration-to-decision and commit-to-decision durations.

For non-wake follow-ups, `user_input_committed` queues exactly one normal user
response for its item ID. Once the activation is in `conversation`, any new item
ID is a follow-up and does not reuse the wake gate; the retained activation still
correlates the first response and late first-turn transcript. Rejected and timed
out activations become `terminal` and never enter conversation. Maintain a
per-session insertion-ordered set of handled input item IDs, capped at 128, so
duplicate provider delivery is idempotent; evict the oldest terminal item first.

### 6. Barge-in cancellation contract

Introduce a captured cancel intent. The backend adapter is the sole sender of
provider `response.cancel`. The browser immediately mutes local playback after
confirmed local or provider-observed speech and calls the barge-in endpoint with
its captured response correlation. Provider `user_speaking` received by the
backend remains the 300 ms fallback. Neither path is allowed to discover a
response later.

```python
@dataclass(frozen=True, slots=True)
class _BargeInIntent:
    speech_event_id: str
    speech_item_id: str | None
    request_id: str
    response_id_at_onset: str | None
    scheduled_monotonic: float

async def _capture_barge_in_intent(...) -> _BargeInIntent | None:
    # Under gate.lock: return None if no attempt exists.

async def _cancel_if_same_attempt(..., intent: _BargeInIntent) -> None:
    # Under gate.lock: require attempt.request_id == intent.request_id.
```

Additional rules:

- Do not schedule when there is no `_ResponseAttempt` at speech onset.
- Do not cancel when `speech_item_id` equals the attempt's `input_item_id`; that
  is a repeated boundary for the input that created the response.
- At callback time, require the exact request ID and a nonterminal attempt.
- If no response ID existed at onset, use the same attempt's later response ID
  after the request-ID check. A replacement attempt may never be used.
- Record cancel source (`browser_local_vad`, `browser_provider_vad`, or
  `backend_fallback`), reason, speech event/item, request ID, input item ID, and
  response ID.
- Browser `bargeInTimer` follows the same rule: capture a response-attempt object
  in the closure, not `connection.activeResponseId` at callback time.
- Browser code does not send `response.cancel`. The response gate's
  `cancel_requested_monotonic` makes endpoint and provider-event retries
  idempotent; exactly one adapter cancellation is sent per request ID.

Replace `BrowserConnection.activeResponseId` with a small response-correlation
object populated from `response.created` metadata. Continue immediate local
playback mute, but unmute only for the same terminal response or explicit
connection cleanup.

```typescript
interface BrowserResponseCorrelation {
  requestId: string;
  responseId: string;
  activationId: string | null;
  inputItemId: string | null;
  state: "generating" | "speaking" | "cancelling";
}
```

### 7. Telemetry and terminal result contract

Phase 0 adds nullable `audio_policy_json` to `RealtimeSession` using
`backend/alembic/versions/20260908_0019_realtime_audio_policy.py`. Phase 1 adds
nullable `provider_input_item_id` to `JarvisRealtimeTraceEvent` and the API
telemetry models using
`backend/alembic/versions/20260908_0020_realtime_turn_correlation.py`. If another
change claims either revision before implementation starts, shift both to the
next consecutive free revisions while preserving their order and suffixes.

Every persisted event has any known IDs in top-level columns, not only in JSON:
activation ID, session ID, generation, provider input item ID, request ID, and
provider response ID. `detail_json` holds non-identifying stage metadata.

Replace the present transcript-dependent `outcome` inference with this terminal
record shape in the TypeScript trace summarizer:

```typescript
interface JarvisRealtimeTurnResult {
  schema: "jarvis.realtime_turn_result.v3";
  turn_key: string;
  activation_id: string | null;
  session_id: string | null;
  generation: number | null;
  input_item_id: string | null;
  request_id: string | null;
  response_id: string | null;
  generation_outcome: "completed" | "cancelled" | "timed_out" | "failed" | "rejected";
  playback_state: "observed" | "not_observed" | "not_applicable";
  transcript_state: "available_before_response" | "available_late" | "missing";
  gate_state: "accepted" | "rejected" | "timed_out" | "not_applicable";
  connection_cohort: "warm" | "connecting" | "cold";
  configuration_id: string;
  latency_ms: Record<string, number>;
  clock_uncertainty_ms: number | null;
  failure_code: string | null;
}
```

`turn_key` is the activation ID for the first wake turn and
`session_id:generation:input_item_id` for a follow-up. The result reducer is
ID-driven. Event order breaks ties only among events with matching IDs. Keep the
raw trace download. A transcript arriving after playback updates
`transcript_state` in the in-memory summary or emits an amendment event; it never
changes `generation_outcome` or creates a second terminal result.
Deduplicate browser events by `client_sequence` and backend/provider events by
their durable event ID before applying them to a turn.

Use non-overlapping latency intervals:

- `local_speech_end_to_provider_commit`
- `provider_commit_to_gate_ready` (zero when gate was already ready)
- `gate_ready_to_response_create_sent`
- `response_create_sent_to_response_created`
- `response_created_to_first_provider_audio`
- `first_provider_audio_to_playback_proxy`

Connection preparation and speech duration are separate metadata, not parts of
post-speech latency. Cross-process intervals include the recorded clock
uncertainty. Browser-local and backend-local durations use monotonic clocks.

Add `backend/app/services/jarvis_latency_report.py` and
`scripts/report-jarvis-latency.py`. The report accepts UTC start/end, optional
configuration ID, and optional benchmark run label. It outputs JSON plus a
human-readable summary with total activations, every terminal outcome,
playback-observed latency percentiles, and missing/ambiguous records. Failed or
cancelled activations remain in the denominator and receive no fabricated
latency value.

### 8. Protocol error classification

In `openai_realtime_adapter.py`, normalize errors to either
`provider_protocol_warning` or `provider_error`.

An `input_audio_buffer_commit_empty` error is a warning only when all are true:

- `error.event_id` matches a locally generated commit event ID recorded for the
  same session key;
- a committed item is already correlated to the same activation;
- the provider connection remains healthy.

The warning records the original code and correlation IDs, resolves any obsolete
commit waiter using the existing committed item, and does not disconnect the
session. Failure of any condition leaves it as `provider_error`. Do not suppress
other provider errors or mark an uncommitted activation successful.

## File-by-file implementation map

### Existing frontend files

- `frontend/app/(app)/jarvis/page.tsx`: restore developer benchmark selection
  after preferences load; show selected and backend-effective modes; generate
  one activation ID at wake detection; label benchmark runs; do not own audio
  sequencing.
- `frontend/lib/api.ts`: add audio policy, barge-in request, and input-item
  telemetry fields; require wake activation IDs in wake call paths.
- `frontend/lib/jarvis-realtime.ts`: consume the ordered audio owner, attach wake
  to ready/connecting/cold sessions, obey `commit_owner`, track response
  correlation, remove event-order turn inference, and keep playback diagnostics.
- `frontend/lib/jarvis-wake-runtime.ts`: keep the capture alive after local
  endpoint, create the activation audio owner, and transition it between wake,
  candidate, and conversation modes.
- `frontend/lib/jarvis-wake-engine.ts`: stop wake classification after detection;
  keep sequential low-cost speech observation independent from wake inference;
  emit sample-based endpoint observations without ending capture.
- `frontend/lib/jarvis-hey-buddy-inference.ts`: expose separate stateful speech
  observation and wake-classification methods; retain loaded ONNX sessions while
  resetting per-arm wake history.
- `frontend/lib/jarvis-wake-worker.ts`: carry chunk sequence/sample positions and
  distinct candidate/observer events.
- `frontend/lib/jarvis-wake-protocol.ts`: define the new worker commands/events
  and retain the 16 kHz source contract.
- `frontend/lib/jarvis-vad.ts`: keep the local interruption observer available
  for both warm and cold activation; it never commits input.
- `frontend/lib/jarvis-realtime.test.ts`, `jarvis-wake-runtime.test.ts`,
  `jarvis-wake-engine.test.ts`, `jarvis-hey-buddy-inference.test.ts`,
  `jarvis-wake-protocol.test.ts`, and `api.test.ts`: update existing cases and
  add the matrices below. The inference test uses injected ONNX-session fakes to
  prove VAD/classifier call separation without loading model assets.

### New frontend files

- `frontend/lib/jarvis-activation-audio.ts`: activation-scoped ordered chunk
  queue, sample ranges, backlog bounds, state transitions, zeroing, and
  subscription ownership.
- `frontend/lib/jarvis-activation-audio.test.ts`: deterministic continuity and
  overflow tests.
- `frontend/lib/jarvis-benchmark-preferences.ts`: storage parsing and effective
  developer mode resolution without React dependencies.
- `frontend/lib/jarvis-benchmark-preferences.test.ts`: reload, corruption, and
  Developer Lab gating tests.
- `frontend/lib/jarvis-realtime-turns.ts`: ID-driven reducer for v3 turn results.
- `frontend/lib/jarvis-realtime-turns.test.ts`: reordered, duplicate, late, and
  missing-event correlation tests.

### Existing backend files

- `backend/app/config.py`: validated turn mode and server-VAD settings.
- `backend/app/db/tables.py`: nullable provider input item telemetry column and
  immutable session audio-policy JSON.
- `backend/app/models/jarvis.py`: policy views, barge-in request/response,
  telemetry input item field, and updated fake provider event literal.
- `backend/app/api/jarvis.py`: return effective policy; preserve backend
  authorization of Immediate mode; idempotent activation and barge-in endpoints.
- `backend/app/services/voice_session_adapter.py`: provider-neutral event names,
  response correlation arguments, and fake-adapter capture hooks.
- `backend/app/services/openai_realtime_adapter.py`: policy-driven session config,
  item-scoped boundary timing, response metadata, and error classification.
- `backend/app/services/jarvis_coordinator.py`: exact activation/input/request
  state, one response eligibility helper, and captured barge-in intents.
- `backend/app/services/jarvis_realtime_telemetry.py`: accept the new top-level
  input item field without adding synchronous database work.
- `backend/tests/test_openai_realtime_adapter.py`,
  `backend/tests/test_jarvis_coordinator.py`,
  `backend/tests/test_jarvis_realtime_telemetry.py`, API tests, and migration
  recovery tests: update and extend as listed below.
- `.env.example`: document the four new turn-policy settings and their defaults.
- `README.md`: after promotion only, describe provider-owned turn commits and
  the report command. Do not claim measured targets until the checkpoint passes.

### New backend files

- `backend/alembic/versions/20260908_0019_realtime_audio_policy.py`: nullable
  session audio-policy JSON and a reversible downgrade.
- `backend/alembic/versions/20260908_0020_realtime_turn_correlation.py`: nullable
  provider input item ID and a reversible downgrade.
- `backend/app/services/jarvis_latency_report.py`: pure grouping/statistics logic
  over persisted v3 terminal records.
- `backend/tests/test_jarvis_latency_report.py`: denominators, cohorts,
  percentiles, amendments, and ambiguous data.
- `scripts/report-jarvis-latency.py`: CLI wrapper only; business logic remains in
  the service module.

## Phase 0 — Freeze the baseline and enforce the ownership rail

Purpose: ensure later improvements can be attributed and make the known
dual-commit behavior impossible before any other latency work lands.

Steps:

1. Before changing behavior, record current backend and frontend test results
   and save one raw trace export from a warm Immediate activation. Do not rewrite
   historical trace rows.
2. Add the validated `RealtimeAudioPolicy`, API view, and deterministic
   `configuration_id`. For the phase-0/1 comparison baseline, explicitly select
   `semantic_vad`; change the code default to the final `server_vad` value only
   in phase 2.
3. Add the session policy migration and persist the exact policy used by every
   newly created controller session.
4. Wire browser behavior to `commit_owner` and remove manual commits from both
   current warm and cold paths when the owner is `provider`. The dormant
   `client_vad` branch is the only branch allowed to call the commit helper.
5. Add a policy-builder unit test for all three valid modes and invalid ranges.
6. Add an assertion in frontend candidate submission: provider owner cannot call
   the manual-commit helper; client owner must have `turn_mode === "client_vad"`.
7. Add an equivalent backend session-config assertion. It must be impossible to
   construct enabled VAD with a client commit owner.
8. Add telemetry of the effective configuration on `call.response_ready`,
   `realtime_connection_ready`, and `wake.gate.started`.

Phase-0 completion gate:

- Policy objects match in backend response, browser trace, and persisted trace.
- Invalid owner/mode combinations fail a unit test before any provider call.
- Provider-owned phase-0/1 traces contain no browser manual commit.
- Existing tests pass; the saved pre-change trace remains the comparison baseline.
- The rollback choices are `server_vad` and `semantic_vad`, both provider-owned;
  the known dual-owner behavior is not a rollback option.

## Phase 1 — Reliable benchmark, exact correlation, and safe cancellation

Phase 1 may change telemetry and cancellation but must not yet change the
turn-detection policy used by production.

### 1A. Persist and display benchmark mode

Use storage key `jarvis.developer.wakeGateMode.v1`. The helper accepts only
`transcript_rules` or `immediate`; missing/corrupt values resolve to
`transcript_rules`.

Page initialization order is fixed:

1. Render the selector disabled/initializing while preferences are unknown.
2. Load backend preferences.
3. Load the saved browser selection.
4. Compute the effective mode with `resolveJarvisWakeGateMode(developerMode,
   selectedMode)`.
5. Show both selected and effective mode next to listening status and in the lab.
6. A selector change is saved immediately but applies only to the next activation.
7. Disabling Developer Lab makes the effective mode `transcript_rules` without
   deleting the stored selection. Re-enabling restores the stored selection.
8. Record the backend-returned gate mode on every activation and display a
   visible diagnostic mismatch if it differs from the requested effective mode.

### 1B. Carry IDs and item-scoped timing

Implement the telemetry migration and provider event payload contracts first.
Update the adapter normalizer to preserve `item_id`, `audio_start_ms`, and
`audio_end_ms`. Replace the session-wide transcript boundary timestamp with the
bounded item map. Add response metadata before changing the summarizer.

The frontend reducer moves to `jarvis-realtime-turns.ts`. Keep existing
`summarizeJarvisRealtimeTrace()` as a compatibility wrapper returning v2
connection data plus v3 turn results, and make it delegate all turn correlation
to the new reducer.

Record separate local events for raw event receipt and handler completion. Do
not perform telemetry persistence on the provider socket reader. Preserve the
existing bounded queues and best-effort behavior.

### 1C. Bind cancel to the onset attempt

Implement `_BargeInIntent` and make capture happen under `_ResponseGate.lock` at
`user_speaking` receipt. The scheduled task receives the immutable intent.
Update browser local and provider speech-start fallback timers to capture the
browser response-correlation object in the same way.

Required tests:

- Speech starts with no attempt; a response starts within 300 ms; no backend
  cancel occurs.
- Speech starts during request A; A completes and request B starts before the
  callback; B is not cancelled.
- Speech starts during request A before it has a provider response ID; the same
  A later receives an ID; A is cancelled.
- Confirmed speech starts during an existing response; browser mutes immediately
  and exactly one cancellation path acts.
- Repeated `speech_started` for the input item that created the response does not
  cancel it.
- A stale generation and duplicate provider event do nothing.

### 1D. Separate result dimensions and add reporting

Implement the v3 result reducer, persistence, report service, and CLI. Keep the
old trace download readable, but new benchmark acceptance uses v3 only.

Required reducer/report fixtures:

- Immediate success without any transcript.
- Transcript before commit.
- Transcript after response audio.
- Response created then cancelled before audio.
- Gate rejection and gate timeout.
- Response timeout, provider error, and no playback observation.
- Duplicate commit boundary and duplicate terminal event.
- Events delivered out of order but carrying complete IDs.
- Two turns in one session with deliberately interleaved browser/backend events.

Phase-1 completion gate:

- Reload visibly preserves selected and effective benchmark modes.
- Backend-confirmed mode and configuration ID exist on every new activation.
- Each fixture yields one and only one v3 terminal result.
- Immediate audio success remains completed when transcription is missing.
- All cancellation race tests pass and existing true barge-in behavior remains.
- Reporter totals equal `completed + cancelled + timed_out + failed + rejected`;
  nothing silently drops from the denominator.
- A verified redundant empty commit is a warning; an unverified one is a visible
  failure.
- No raw transcript or PCM appears in telemetry rows.

## Phase 2 — Adopt server VAD and make provider commits authoritative

### 2A. Switch the provider session policy

Change `_session_config()` to build turn detection only from
`RealtimeAudioPolicy`:

```python
if policy.turn_mode == "server_vad":
    turn_detection = {
        "type": "server_vad",
        "threshold": policy.vad_threshold,
        "prefix_padding_ms": policy.prefix_padding_ms,
        "silence_duration_ms": policy.silence_duration_ms,
        "create_response": False,
        "interrupt_response": False,
    }
elif policy.turn_mode == "semantic_vad":
    turn_detection = {
        "type": "semantic_vad",
        "eagerness": policy.semantic_eagerness,
        "create_response": False,
        "interrupt_response": False,
    }
else:
    turn_detection = None
```

Start at 350 ms. Do not simultaneously change voice, model, reasoning effort,
noise reduction, prompt, or context size.

### 2B. Replace endpoint-bound and bulk senders

Replace the phase-0 endpoint-bound warm sender and whole-utterance cold sender
with one streaming behavior in both `activateCandidate()` and
`submitWakeAudio()`. For provider-owned modes it only appends ordered PCM and
observes provider boundaries. Delete the `WakeCommitWaiter` dependency from the
provider-owned path.

Rewrite both warm `activateCandidate()` and cold `submitWakeAudio()` paths in
this phase. Neither may wait for the local endpoint before starting upload, and
neither may bulk-upload a completed utterance in provider-owned modes. Phase 3
then extracts their shared queue/lifecycle into the activation audio owner.

For `client_vad`, keep one explicit commit helper behind an exhaustive policy
branch. Phase 4 is the only code allowed to select that policy.

### 2C. Make commit the only response boundary

Update the adapter to emit `user_input_committed`. Update the coordinator so
`user_stopped` records floor/telemetry only. Both gate arrival orders call
`_maybe_queue_wake_response()`.

For residual or duplicate boundaries:

- same committed item ID: ignore idempotently;
- second commit with a new item while a wake activation is pending: add it to
  candidate items but do not replace the primary item without an explicit new
  speech start;
- a silence-only item with no matching speech start/stop: record and discard it,
  never create a response;
- a genuine new item after the accepted activation's first request is a normal
  follow-up and may cancel an existing response only if speech began during it.

### 2D. Keep capture flowing until the provider commits

Change the wake engine/runtime so the local endpoint resolves observation only.
The candidate stream continues through trailing silence and until the provider
commit is observed. After commit, promote the same source to conversation mode
for follow-ups. Local wake inference stops immediately after activation, so it
cannot delay the forwarding path.

Phase-2 required tests:

- Session config is exactly `server_vad` with 350/0.5/300 and both automatic
  response/interruption flags false.
- Warm and cold candidates send no `input_audio_buffer.commit` under provider
  ownership.
- One provider commit plus transcript-before/after ordering creates one response.
- Missing transcript in Immediate mode creates one response.
- Two provider boundary events for one item create one response.
- Residual silence and a delayed boundary do not cancel the response.
- A phrase with a pause after “Hey Jarvis” and a phrase with a pause inside a
  longer command retain their full sample ranges.
- Rejection deletes all committed candidate items and creates no response/tool.
- Known empty-commit warning does not disconnect; unknown errors still do.

Phase-2 completion gate:

- New traces show zero browser commit events in provider-owned modes.
- Exactly one logical provider commit and one response request exist for every
  successful fixture.
- No `input_audio_buffer_commit_empty` occurs in a clean provider-owned run.
- Immediate mode has no finalized-transcript dependency.
- Full commands survive designated pause fixtures.
- `semantic_vad` rollback uses the same provider-owned flow and passes ownership
  tests before it is considered usable.

## Phase 3 — One continuous activation and capture owner

### 3A. Introduce the activation audio owner

Implement `jarvis-activation-audio.ts` and make `JarvisWakeRuntime` create one
owner per detected wake. The runtime state machine is:

```text
idle -> wake_listening -> candidate -> conversation -> closing -> wake_listening
                               |             |
                               +-> rejected -+
                               +-> failed ----+
```

`candidate` begins at wake detection with pre-roll already queued.
`conversation` begins after gate acceptance plus the primary commit. Rejection,
timeout, sleep, stale generation, and disconnect close and zero the candidate.
Rearm reuses the same microphone stream and creates a fresh classification state.

### 3B. Separate wake classification from sequential speech observation

Apply the `JarvisWakeInference` split defined in the contracts section. Replace
the engine's single `queuedWindow`/`analyze()` path with two schedulers:

- a FIFO speech scheduler that consumes each complete 1,920-sample range once;
- a latest-only wake scheduler that runs only in `wake_listening` and may
  coalesce obsolete rolling windows under load.

On detection, transition scheduling state before invoking callbacks, so no new
wake-classifier job can be queued reentrantly. An already-running classifier may
finish, but its result is ignored unless it belongs to the current arm token.
Candidate endpoint events carry the exact `speechEndSample`,
`observedAtSample`, trailing-silence sample count, and processing lag from the
contract. They do not emit the legacy completed `utterance` audio as a second
candidate payload. Retain a compatibility event only within an intermediate
commit and remove it before the phase-3 gate.

### 3C. Attach candidates to connection state

Refactor `JarvisRealtimeController` to maintain one
`standbyConnectionPromise`. Wake handling follows this deterministic order:

1. If a healthy standby exists, register and stream on it.
2. Else if standby is connecting, attach the candidate to that promise and
   buffer meanwhile. Do not increment the operation or start another call.
3. Else create exactly one cold connection carrying the same activation ID and
   bounded audio owner.
4. If an attached connecting standby fails, reuse the retained candidate for the
   normal cold retry sequence. Never reacquire the microphone or create a second
   candidate.

Register the gate before the first append whenever a ready session already
exists. For a cold call, include activation context in call creation so the gate
exists before the provider can process flushed PCM.

### 3D. Use one PCM source for the active conversation

Keep the silent WebRTC sender track used to establish the standby call. Feed all
wake command and follow-up microphone PCM through ordered data-channel appends.
Do not call `createHandoffTransport().connectLive()` for wake activations. The
local barge-in observer reads the existing shared microphone stream but does not
send another copy.

Backpressure behavior is fixed:

- enqueue 20 ms logical chunks;
- send at most 40 ms per append;
- stop draining while the data channel is not open or its buffered amount is
  above 64 KiB;
- resume from `bufferedamountlow`;
- record backlog duration separately from spoken duration;
- fail closed at 30 seconds retained audio;
- never drop the oldest or newest chunk silently.

If browser support prevents reliable data-channel PCM in a target environment,
fail that cohort explicitly and keep `semantic_vad` provider-owned rollback.
Do not add an unordered live RTP switch as an emergency workaround.

### 3E. Initialize interruption observation uniformly

Create the local barge-in detector when the shared mic and controller become
ready, not only on the cold active branch. Pause its cancellation actions while
only armed; enable them in candidate/conversation states. Preserve local speech
start/end traces for warm, connecting, and cold cohorts.

### 3F. Rotate only at safe boundaries

Do not rotate a session while a candidate is pending, a response attempt exists,
or buffered PCM remains. When the rotation timer expires, mark rotation pending
and execute after the response is terminal and the floor is listening. A fresh
rotation connection is a separate connection cohort and never contributes to a
warm turn's post-speech interval.

Phase-3 required tests:

- Pre-roll through provider commit has contiguous, non-overlapping sample ranges.
- Subscription after delayed activation registration loses no chunks.
- Wake during standby connection uses one call and one candidate.
- Connection failure and retry send each retained range exactly once to the
  successful provider connection.
- Rejection and timeout zero retained audio and restore silent standby.
- Rearm creates a new activation ID without reacquiring the microphone.
- Follow-up audio uses the same source without rerunning wake inference.
- Stateful VAD sees every 1,920-sample frame once and in order; wake inference
  receives no call after detection and restarts only after rearm.
- Provider audio continues when the observation FIFO overflows; client-VAD mode
  fails closed without committing.
- No mic-bearing RTP track is installed before or after wake in the data-channel
  wake path.
- Warm and cold paths have identical commit/gate/response invariants.
- Rotation waits for a safe boundary.
- Soft backlog records diagnostics; hard backlog fails visibly.

Phase-3 completion gate:

- There is no missing or duplicated command prefix/suffix in deterministic
  sample fixtures.
- One source owns every sent sample range.
- No competing connection starts when wake occurs during standby setup.
- Warm, connecting, and cold activations all produce one terminal record.
- Warm activation has the local interruption observer.
- Manual Arm still works and is labeled `manual`, not included in wake latency
  cohorts.

## Measured checkpoint after phases 1–3

Do not proceed to phase 4 and do not claim success before this checkpoint.

### Fixture set

Use repeatable audio with known last-voiced sample positions:

- `wake_only`: “Hey Jarvis.”
- `direct_short`: “Hey Jarvis, what time is it?”
- `direct_long`: one 8–12 second command.
- `pause_after_wake`: 500 ms pause after “Hey Jarvis,” then the command.
- `internal_pause`: 500 ms pause inside a longer command.
- `barge_in`: follow-up begins during response audio.
- `incidental_mention`: mention Jarvis without direct address, for gate behavior.
- `silence/noise`: bounded non-command inputs that must not create responses.

Record the spoken fixtures locally with the intended test microphone and store
PCM under the ignored `.cockpit/benchmarks/fixtures/jarvis-latency-v1/`
directory. Store labels, SHA-256 hashes, sample rate, and known last-voiced
positions in that run's manifest; do not commit personal voice recordings.
Generate synthetic silence/noise fixtures deterministically using a seed stored
in the manifest. Live microphone runs supplement fixtures for acoustics, pauses,
and real interruptions; they do not replace fixture continuity checks.

### Cohorts and counts

For each silence setting under evaluation, collect:

- at least 30 warm first turns, balanced across wake-only, short, long, and pause
  fixtures;
- at least 20 follow-ups;
- at least 10 cold/reconnect first turns as a separate cohort;
- visible and background-tab runs;
- the intended output device;
- every failed, rejected, cancelled, and ambiguous activation.

Alternate 250, 350, and 450 ms server-VAD settings instead of running each in a
single time block. Record build revision, configuration ID, benchmark run label,
standby readiness/age, fixture label/hash, tab state, output device, and clock
uncertainty.

### Selection rule

Apply this rule mechanically:

1. Eliminate any setting with a lost command, duplicate response,
   self-cancellation, or premature cutoff on designated pause fixtures.
2. Among remaining settings, eliminate any that miss p90 2.0 seconds for warm
   speech-end-to-audible-response.
3. Choose the remaining setting with the lowest warm median.
4. If medians differ by less than 75 ms, keep 350 ms.
5. If no setting survives, retain 350 ms, mark the checkpoint failed, and enter
   phase 4 only when endpoint timing is the measured cause.

### Acceptance targets

- Warm speech end to audible response: median at most 1.5 seconds and p90 at
  most 2.0 seconds.
- No lost commands, unintended duplicate responses, self-cancellations, or
  premature cutoff in the acceptance run.
- Exactly one usable terminal record or explicit failure per activation.
- Browser playback proxy is validated against a short output-loopback recording.
  Report both proxy and measured acoustic onset plus uncertainty.
- The latency budget is represented by the non-overlapping v3 intervals.

A working allocation to validate, not a forecast, is 250–450 ms endpointing,
100–250 ms provider commit delivery, at most 100 ms backend dispatch,
approximately 0.9 seconds response request to provider audio, and at most 150 ms
additional playback delivery.

Do not report p95 from the initial four successes. Gather at least 100
representative activations, including failures, before a later tail-latency
report. Percentiles use successful playback observations; reliability uses the
full denominator.

Checkpoint artifacts:

- raw trace export;
- report JSON;
- human-readable report;
- fixture manifest and hashes;
- loopback validation notes;
- explicit pass/fail for every target;
- promoted configuration ID or recorded reason phase 4 is required.

## Phase 4 — Conditional client endpointing experiment

Entry condition: phases 1–3 are complete and corrected measurements show either
that provider endpointing prevents the latency target or that every safe
server-VAD threshold prematurely cuts natural commands. Do not enter based on
intuition or a single phrase.

Implementation:

1. Select `jarvis_realtime_turn_mode=client_vad`, which sets provider
   `turn_detection` to `null` and `commit_owner` to `client`.
2. Reuse the phase-3 low-cost speech observer. Wake inference remains stopped
   after candidate creation; do not introduce a second VAD implementation.
3. Continue processing speech frames sequentially. Count silence by sample
   duration, not completed inference calls. Record processing lag; skipped frames
   are a terminal activation failure in this mode.
4. Send exactly one manual commit after the local sample-based endpoint. Append
   and commit remain ordered on one data channel.
5. Wait for the matching `input_audio_buffer.committed` item before the backend
   response helper may act.
6. Compare CPU load, foreground/background behavior, echo, natural pauses,
   barge-in, false endpoints, and latency against the promoted server-VAD policy.

Phase-4 gate:

- All phase-3 continuity and response-ownership tests pass in client mode.
- Exactly one manual commit and no provider auto-commit path exist.
- Client mode beats the server policy by at least 100 ms at warm median or fixes
  a demonstrated premature-cutoff failure without missing the latency target.
- CPU/background regressions are reported. If the gate fails, keep server VAD.

## Phase 5 — Remove smaller backend and provider costs

Entry condition: a stable single-owner input path passes functional acceptance,
and v3 intervals identify a remaining stage above its working allocation.

### 5A. Backend snapshot work

Only act if `gate_ready_to_response_create_sent` exceeds 100 ms at p90.

- Move `task_presentation_service.sync_candidates()` off the direct user-response
  path. Refresh it on task/presentation events and expose an immutable,
  versioned snapshot.
- Add a fast `read_response_snapshot()` contract containing situation payload,
  pending presentation candidates, snapshot version, and creation time.
- The response arbiter reads one version and reserves against that version.
  On a reservation conflict it retries once; it never speaks unreserved updates.
- Preserve generation checks, response serialization, tool authorization,
  update priority, and delivery settlement.
- Promote only if it reduces the measured stage and passes task-update
  correctness tests. Existing 31–110 ms backend work makes this lower priority.

### 5B. Provider generation and playback

Only act if response-create-to-provider-audio or provider-audio-to-playback is
the measured constraint.

- Profile the two intervals separately.
- Preserve native audio understanding.
- Test context reduction, voice, or model one variable at a time under a new
  configuration ID.
- Do not count a chime or prerecorded acknowledgement as response onset.
- Promote only when reliability gates remain clean and the selected interval
  improves by at least 100 ms median or 10 percent, whichever is larger.

### 5C. Native host fallback

If background browser scheduling alone prevents predictable capture, extend the
existing native client/audio host using the same activation, policy, event, and
telemetry contracts. Do not fork backend semantics or rebuild the stack based on
foreground browser results.

## Phase 6 — Add audio-aware intent detection beside the stream

Entry condition: phases 1–3 meet the speed baseline. Intent work must not obscure
endpointing or transport measurements.

Define one provider-neutral interface before choosing an implementation:

```typescript
interface JarvisIntentDecision {
  activationId: string;
  decision: "accept" | "reject" | "abstain";
  confidence: number;
  reasonCode: string;
  decidedAtSample: number;
}

interface JarvisStreamingIntentDetector {
  start(activationId: string): void;
  push(chunk: JarvisAudioChunk): void;
  finish(endSample: number): Promise<JarvisIntentDecision>;
  reset(): void;
}
```

Implementation sequence:

1. Run detector candidates in shadow mode on the same bounded PCM already sent
   to Realtime. They do not block or authorize responses.
2. Evaluate direct calls, wake-only calls, incidental mentions, media, pauses,
   noise, and multi-speaker examples. Record only decision metadata.
3. Select a detector only if held-out address precision is at least 95 percent,
   recall at least 90 percent, and added post-command wait p90 at most 150 ms.
4. When promoted, replace transcript rules with an `audio_intent` gate mode. The
   two prerequisites remain exact input commit plus accepted intent.
5. Finalized transcription stays optional asynchronous metadata. Missing the
   word “Jarvis” in ASR is never proof it was not spoken.
6. A strict timeout rejects or explicitly rearms. It never silently accepts and
   never authorizes tools.
7. Optional local conversational context is attached only after confirmed
   address and has explicit retention bounds. It is not synchronous for the
   first reply.

Phase-6 gate:

- Shadow report meets accuracy and latency targets on held-out data.
- One input commit plus one accepted intent creates one response.
- Rejection/timeout creates no response or tool call and deletes candidate items.
- Immediate benchmark mode remains available only to Developer Lab and bypasses
  the detector solely for controlled latency measurement.

## Required automated test matrix

Backend coordinator tests must cover:

- gate-before-commit and commit-before-gate;
- Immediate without transcript;
- late transcript after audio;
- duplicate/reordered item boundaries;
- follow-up item after a wake item;
- rejection/timeout cleanup;
- delayed cancel after replacement response;
- genuine barge-in;
- stale session generation;
- task update coalescing during a pending wake;
- provider submission failure and retry with the same correlated item.

OpenAI adapter tests must cover:

- exact config for all three turn policies;
- normalized item IDs and audio offsets;
- item-scoped transcript timing under interleaved items;
- response metadata round trip;
- known redundant empty commit warning;
- unknown/uncorrelated provider error;
- cancelled response terminal event without transcript.

Frontend realtime tests must cover:

- warm, connecting, cold, retry, rejection, rearm, and rotation;
- no commit under provider ownership;
- one commit under client ownership;
- continuous sample ranges and bounded backpressure;
- no mic RTP source in the wake PCM path;
- local and provider barge-in with captured response identity;
- playback attribution to the matching response;
- one v3 result per activation.

Wake runtime/engine tests must cover:

- wake detection with bounded pre-roll;
- 20 ms ordered chunks and contiguous sample positions;
- endpoint observation while streaming continues;
- wake inference stops after detection;
- low-cost speech observation continues;
- late subscription, close, zeroing, overflow, and rearm;
- worker overload cannot fabricate elapsed silence.

API, migration, and reporting tests must cover:

- audio policy serialization;
- activation registration idempotency and conflict;
- Immediate authorization;
- provider input item telemetry persistence;
- migration upgrade/downgrade and recovery;
- result denominator and percentile rules;
- no raw PCM/transcript telemetry.

## Verification commands

Run focused tests while implementing, then the full gates from repository root.

```powershell
cd backend
uv run pytest tests/test_jarvis_coordinator.py tests/test_openai_realtime_adapter.py tests/test_jarvis_realtime_telemetry.py tests/test_jarvis_latency_report.py
uv run pytest

cd ..\frontend
npm test -- jarvis-activation-audio.test.ts jarvis-realtime-turns.test.ts jarvis-realtime.test.ts jarvis-wake-runtime.test.ts jarvis-wake-engine.test.ts
npm test
npm run lint
npm run build
```

Also run the migration recovery test using the repository's normal test setup.
Do not use a production database for upgrade/downgrade verification.

Manual/device verification is required after automated gates:

- real microphone warm/cold/reconnect;
- natural pauses and a long command;
- interruption while generating and while speaking;
- page reload with persisted benchmark mode;
- visible/background tab;
- intended speaker/output device;
- one loopback validation sample;
- shutdown during idle, candidate, response, and rotation.

## Recommended reviewable delivery order

Keep the work reviewable in this sequence. Each item must leave tests green and
must not rely on a later commit to restore a safety invariant.

1. Audio policy types, validation, API exposure, configuration telemetry, and
   phase-0 assertions.
2. Provider input item correlation, telemetry migration, response metadata, and
   item-scoped timing.
3. Captured backend/browser barge-in intent and regression tests.
4. Benchmark preference persistence, v3 result reducer, report service, and CLI.
5. Promote provider-owned `server_vad`, add continuous warm/cold appends, and
   enforce commit-only response eligibility.
6. Activation audio owner and wake engine/runtime continuous streaming.
7. Connecting-standby attachment, cold retry reuse, uniform local observer, and
   safe rotation.
8. Measured checkpoint artifacts and documentation of the promoted policy.

Do not combine phase 5 or phase 6 work with these deliveries.

## Completion definition for the required project

Phases 0–3 are complete only when all of the following are true:

- The source tree contains no enabled-provider-VAD path that sends a manual
  input commit.
- Warm and cold wake paths share one activation audio owner and the same
  gate/commit/response rules.
- Every activation, input item, response request, provider response, playback
  observation, and cancellation can be joined by explicit IDs.
- A delayed cancel cannot target a response absent at speech onset.
- Local endpoint computation cannot pause, commit, or cut candidate capture.
- Immediate success is independent of transcription.
- Failures, cancellations, rejection, timeout, missing playback, and missing
  transcript remain distinct and visible.
- Full backend and frontend suites, lint, build, migration recovery, and the
  manual matrix pass.
- The measured checkpoint artifacts exist and state whether latency targets
  passed. Documentation makes no stronger performance claim than those results.

## Implementation handoff checklist

Before editing, the future agent must:

- inspect `git status` and preserve unrelated/user-owned changes;
- read `frontend/AGENTS.md` and the installed Next.js documentation relevant to
  any page change;
- verify the current latest Alembic revision before naming the migration;
- capture the baseline test state and one raw trace;
- implement in the reviewable order above;
- stop at any phase gate failure, record the exact failed invariant, and repair
  it before starting the next phase;
- use the checkpoint selection rule rather than choosing an endpoint threshold
  subjectively.

## Source guidance

Official OpenAI documentation states that enabled VAD automatically chunks input
and emits speech boundary events. `server_vad` is silence-based and exposes
`threshold`, `prefix_padding_ms`, and `silence_duration_ms`; semantic VAD uses an
utterance-completion classifier. See [Voice activity detection](https://developers.openai.com/api/docs/guides/realtime-vad).

Manual `input_audio_buffer.commit` and `input_audio_buffer.clear` belong to the
flow where VAD is disabled with `turn_detection: null`. WebRTC media and control
events use distinct mechanisms, which is why this plan avoids an unprovable
mid-activation PCM/RTP ownership transfer. See [Realtime conversations](https://developers.openai.com/api/docs/guides/realtime-conversations).

This plan supersedes earlier speculative latency-stage attribution. It does not
replace `docs/JARVIS_FOLLOW_UP_PLAN.md` or the broader Jarvis acceptance backlog.
