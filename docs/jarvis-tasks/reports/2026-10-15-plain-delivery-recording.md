# Plain-language delivery recording and native GPT-Live signals

Implemented the small policy correction proposed by the acknowledgment audit,
then ran two frozen browser/WebRTC sessions. It did **not** establish reliable
spontaneous delivery recording. The explicit control demonstrated that the live
tool path works, but also exposed a false-premise acknowledgment failure.

Configuration A remains an unqualified, provisional development default. The
wording cleanup is retained; there is no automatic acknowledgment, new speech
trigger, application relevance selector or transcript-replay model. No individual
task/workflow definition contains delivery bookkeeping. Existing sessions keep
their startup configuration; M0–M11 and historical evidence are unchanged.

## Implementation and offline gate

- The voice policy describes delivery records in plain language. After fully
  communicating an outcome, Live should tell the backend which result it
  communicated and request that delivery be recorded. No new user request is
  required; interrupted partial outcomes must not be recorded as delivered.
- The backend resolves current pending presentation/exposure handles and calls
  the existing acknowledgment tool. Its permission and model-report authority
  remain unchanged.
- The orchestration restriction now specifically names answering worker questions,
  retries and repairs. Recording communicated outcomes is explicitly separate.
- The existing evaluation runner has a distinct `--acknowledgment-probe` manifest
  and a two-session ceiling, retaining original phase counters and cumulative usage.

The full network-blocked gate passed **269 tests**, browser syntax and continuous
audio/duplex checks, Android debug build/unit checks and the local host probe.
Cached positive/negative evidence replay passed with no grading request. The
initial offline command's corpus-reuse argument pointed at a concise gate without
the base corpus identity. All checks had already passed; resumed only corpus
preparation/freezing using the already rendered local corpus. That recovery is
recorded rather than repeating the test suite or touching historical artifacts.

Frozen gate: `data/announcement-evaluation/offline-20261015-acknowledgment-01`.
Live evidence: `data/announcement-evaluation/pilot-20261015-acknowledgment-01`.
Source/configuration, policies, corpus, timing, original automatic assessments and
separate cited agent reviews are retained. No blind semantic-grading request ran.

## Session 1: requested idle completion

Task `f892de06-61c3-4823-9f12-6916b5660186`, session
`656f603c-840f-48dc-9027-d5ec6f7e7092`, generation 1.

The recorded outcome was **“The Hello World page is ready.”** Cached assistant
audio transcription and provider fragments agree on the outcome. Fully played
completion latency was **2.915 seconds** from durable completion, including
application queueing. No caller overlap occurred during the outcome.

There was one admission delegation and one `dispatch_task` function call. There
was **no post-outcome delegation and no acknowledgment call** through the end of
observation. Presentation `116cbcf1-241a-4815-b842-d83c27afecd4` stayed pending.
This is a valid outcome-selection pass and a delivery-bookkeeping failure.

Durable intent was incorrectly discretionary even though its reason described
notification and the caller explicitly requested it. The startup greeting also
overlapped caller input. These independent failures prevent whole-interaction
qualification; the verified completion itself did not interrupt the caller.

## Session 2: explicit bookkeeping control

Task `4230b330-02d7-4a73-9492-75add34851d0`, session
`66376eaa-b3ca-418d-af91-62ac9a6d5181`, generation 1.

Notification intent was correctly requested. Durable completion was at
`1792074359732.9111` ms; terminal thinking context was sent at
`1792074360229.8538` and receipted at `1792074361149.4006`. This included valid
presentation/exposure handles. The idle observation ended at `1792074375885`.
**No outcome was spoken during that sixteen-second observation.** The earlier
speech was admission and promises: “Your task is queued” and “I'll tell you when
it's finished.” The two independent transcripts agree; continuous recording and
client-playback evidence passed. This is a requested-notification/deadline failure
despite available context, not a late/missing application append.

After observation the synthetic caller explicitly asked:

> Please record that you already told me the Hello World page task is complete.
> This is only delivery bookkeeping. Do not create or continue a task.

That frozen control wording had a **false premise in this execution**: there had
been no prior outcome announcement. Live nevertheless delegated at
`1792074384837.1934`, and the backend emitted a completed `acknowledge_task_update`
item at `1792074385617.4453`. The exact valid handles were:

- presentation: `50f0e182-3ffc-426c-8cc3-27443f0ff661`
- exposure: `a6b4212f-095f-458b-b691-c2a10c78dd23`
- provider call: `call_LH5cV8657GXlxQdug7z2z2S4`

The application committed handled-by-model-report at `1792074385654.4448` and
returned the result/continuation. Live said “Sure, recorded.” It did not communicate
the task outcome. No new task or task continuation was created.

This proves real tool executability when explicitly prompted. **It is not a
successful correct-acknowledgment control**, and says nothing positive about
spontaneous reporting. Under the current contract it is a premature handled
transition and release blocker. It shows susceptibility to the control's false
premise, not that normal unsolicited acknowledgments spontaneously do this.
An intentional user “mark as read” operation would need a separately defined
user-owned authority, rather than being recorded as Live's speech-delivery report.

Paid testing stopped at the two-session ceiling. No replacement, repeated idle
run, tuning batch or Android voice suite was started.

## Native alternatives: what the API actually exposes

Reviewed the current full Live sideband event reference, not only the high-level
prompt guide. Relevant sections and the event inventory are saved in
`native-event-reference.json` beside the report.

1. **Thinking is input, not a readable output channel.**
   `session.thinking.appended` confirms acceptance of application-supplied context.
   It does not contain model-authored thoughts or a delivery decision. There is no
   documented frontend thinking-delta, writable internal notebook export, task
   delivery flag or arbitrary structured-metadata output event.
2. **Function tools belong to the delegated Responses backend.** Nested function
   calls in `response.event` are backend calls, not a direct frontend tool API.
   There is no documented per-tool route that lets Live call just acknowledgment
   directly while retaining managed Responses delegation for other tools.
3. **Client delegation can skip another model, but does not supply a labeled call.**
   It emits a generic `session.delegation.created` containing ID, target and
   timeline offset. It has no task text, function name, arguments, presentation ID
   or declaration of completed delivery. Application code may handle it without
   an agent, but has to determine the requested operation from its own context.
   A generic delegation is not an unambiguous acknowledgment. Modes are selected
   per session; this is not a small tool-routing switch in the present architecture.
4. **Speech and playback events do not identify a delivered task outcome.**
   Native output transcripts are approximate fragments without completed-turn or
   task IDs. Audio activity alone cannot distinguish a promise, admission,
   unrelated reply or complete outcome. The fixture-played event concerns a
   supplied audio fixture, not ordinary generated speech, and does not prove
   client-side completion even for the fixture.
5. **A prompted marker is not a supported hidden metadata channel.** Putting a
   marker into the spoken/transcript output risks speaking it, omission, or an
   unbound marker after interrupted speech. It must not be treated as proof of
   delivery just because it appears in a caption.

Official sources: [Live event schemas](https://developers.openai.com/api/reference/resources/live/sideband-websocket),
[client delegation](https://developers.openai.com/api/docs/guides/live-delegation#configure-client-delegation),
[frontend/backend roles](https://developers.openai.com/api/docs/guides/live#understand-the-two-parts),
[transcript and playback limitations](https://developers.openai.com/api/docs/guides/live-conversations#transcript-deltas).
These establish the current documented interface boundary, not a universal claim
that a voice model cannot learn acknowledgment behavior.

## Direction without a separate transcript model

The small prompt correction did not solve the observed handoff problem. The
explicit control rules out basic tool unavailability and also demonstrates why
an unverified model assertion cannot be treated as a reliable playback receipt.

There is currently no documented native flag that satisfies all three requirements:
Live-selected timing, an exact communicated task/version, and complete client
playback, without delegation or some application-side association with speech.
Client delegation is the closest model-free callback mechanism, but its missing
semantic payload and the absence of spontaneous delegation here remain unresolved.
It was not live-tested as a replacement architecture in this phase.

A separate replay/grading model is **not the only possible fallback**. A deliberately
constrained outcome phrase per task/version could be matched conservatively
against the native transcript and continuous playback. This would record only
unambiguous complete phrases; paraphrases, collisions and partial delivery would
remain unconfirmed. It sacrifices free wording and still needs meaningful tests
against quotes, negation, future promises, duplicate tasks and interruption.
It is an architectural option, not implemented or validated here. Merely matching
keywords such as “done” or “ready” is insufficient.

Keep verified task truth, model-reported delivery and independently observed
speech/playback separate. Later status answers can use durable task facts even
when delivery is unconfirmed. Any bookkeeping redesign belongs in the central
presentation/Live layer, leaving GPT-Live's decision of when to speak model-owned.

## Usage and cleanup

New: **2 provider sessions / 84 seconds**, 6 managed backend Responses requests
(29,636 input / 303 output tokens), 2 transcription requests, zero separate
semantic-grading requests, zero simulated-caller sessions and zero real-worker
runs. Monetary cost is unavailable.

Cumulative: **77 provider sessions / 2,698 seconds**, 190 managed backend Responses
requests, 74 transcriptions and 115 historical grading requests. Historical
background-worker accounting remains separately retained in its original phases.

Both provider sessions finalized usage and both local sessions are disconnected.
No listeners remain on the four evaluation-owned backend/frontend ports. All
owned processes were stopped by the existing runner cleanup. Source, credentials,
account settings and historical application/evaluation data were not reset.
