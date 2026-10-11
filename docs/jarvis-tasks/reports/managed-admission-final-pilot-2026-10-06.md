# Managed admission end-to-end verification — 2026-10-06

The user requested one final end-to-end timing check after the managed-only
implementation. The ready07 prerecorded request reached the Android receipt of
committed admission in **3774.6038 ms**. This is below the **4000 ms** iteration
threshold, within the previous managed observations of **3551.0514–3917.585 ms**,
and above the two-second target. Margin to four seconds is **225.3962 ms**.

## Method and correctness

Ten offline managed delegation/diagnostic checks passed first. Exactly one live
voice trial then ran on API37 through the production custom-wake/audio handoff,
real WebRTC RTP, GPT-Live, managed Luna, guarded task gateway, durable journal
and Android event listener. The same ready07 audio was reused without another
audio-generation request. Worker pool, orchestration and new TTS inference were
disabled. Normal reasoning settings and priority tier were retained; no model
output recording or reasoning-summary request was enabled.

The transcript requested a plan for a simple notes application. Exactly one
correct task and one queued execution run were committed. There was one shell
call, one resolved delegation origin, assistant playback and 28,155 reported
outbound RTP bytes. There were no provider errors, protocol warnings or tier
fallbacks. No task operations or screenshot uploads were created. Fresh capture
still ran through the Android channel concurrently, but this ordinary request
did not require screen-dependent admission. Run execution was intentionally not
part of the admission test.

## Full admission interval

These successive intervals use Android's own clock. Relay timestamps include
delivery of the corresponding server event; they do not isolate provider-internal
work or one-way transport:

- Speech end → delegation event received: **2295.3543 ms**.
- Delegation event received → validated tool event received: **1092.6697 ms**.
- Validated tool event received → durable admission received: **386.5798 ms**.
- Total speech end → durable admission received: **3774.6038 ms**.

The largest remaining interval is still before delegation. It includes the
voice/media handoff, endpointing/transcription, delegation and transport; those
parts cannot be separated from these observations alone.

Independent server-clock observations show managed response-created at
**48.0273 ms** after delegation receipt, while local transcript correlation
completed at **626.4663 ms**. Selection therefore overlapped correlation. The
completed tool arrived at **1012.7783 ms**. Schema validation took **82.6999 ms**,
validated-tool to shell **67.7372 ms**, authority reads **44.7396 ms**, gateway
to task commit **140.8404 ms**, commit to gateway return **126.7123 ms**, and
commit to SSE emission **168.7414 ms**. These intervals nest/overlap and must not
be added to the Android breakdown or subtracted across clock domains.

Fresh request to screenshot callback was **79.7782 ms** and callback to encoding
finish **105.9334 ms**, concurrently with selection. Client listener dispatch
took **0.5302 ms**. Capture regression acceptance still requires its controlled
baseline/enabled cohort; this single ordinary request does not establish it.

## Usage and acceptance limits

This was one live voice trial with one completed routing response and one
managed continuation observed starting. Available completed-response usage was
**3390 input / 72 output tokens**, including **9 reasoning tokens**. Reported
Live usage was **15 seconds**. Continuation/setup usage not reported at shutdown
remains unknown; exact cost remains unknown. No further paid trial followed.

The raw single-sample statistics mechanically contain a p95 field equal to the
one observation. It is **not a meaningful p95 estimate**. The harness retains
`ready_latency: failed` because fewer than 20 samples were supplied and
`audio_corpus: blocked` because this was a partial pilot; `voice_provider` passed.
The nonzero pilot exit status is intentional. No acceptance status was rewritten.
M8/M10 remain open pending a frozen full managed cohort and required functional,
cold, capture, recovery and provider cases. M11 remains deferred.

Evidence is in `data/managed-admission-final-pilot-2026-10-06/`: raw Android log,
`audio/acceptance.json`, isolated `fixture.db`, `summary.json` and clock-separated
`timing-summary.json`. Candidate ID:
`e320e081f7151981f3d6ea003ef82cab26bb7a5fdb0b6318f2275620b2c221dc`;
executable source digest:
`c53d20dce3d4713227308f0fcf7d037073eb92b34193e430e9cb5e0646435f36`.
The harness stopped its backend/emulator trees and cleared its fixture state.
