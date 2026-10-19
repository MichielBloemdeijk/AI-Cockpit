# Natural assistant architecture comparison

Status: 228 standalone classifications and 24 exploratory browser connections are retained. Configuration 1 (subscriptions plus Responses bookkeeping, no Decisions) was the sole frozen finalist. Its confirmation stopped after 8 of 14 cases (9 of 16 connections): the interruption/reconnect case is an independently agreed material problem that blocks the unchanged candidate. No Android connections have run and the development default remains unchanged.

## Product standard

The user clarified that natural, useful behavior is the goal. Overlap, including
accidental simultaneous starts, is not inherently failure. Review yielding,
recovery, relevance and actual conversational effect. Ten seconds is a contextual
target, not an absolute cutoff. Measurements support agent judgment. Task truth,
preference persistence, identity and playback evidence remain factual checks.
A second agent independently reviews borderline findings, proposed blocking
failures and adoption. Transcript receipt is not playback. Reviewers explicitly
record whether they listened directly or examined waveform/timing and transcripts.
No claim of direct listening or physical hearing is made without that evidence.

Twelve saved cases now have separate observational projections under the new
rubric. The latest requested-during-speech case has an independently agreed
acceptable review: its 1.24-second overlapping promise was not the outcome;
Jarvis subsequently waited, responded to the topic and delivered the outcome at
an opening in 8.48 seconds. No material failure was established. Awkward wording,
causal uncertainty and unassessed prosody remain explicit. Original grades stay
unchanged, and historical evidence does not qualify changed source.

## Implementation and comparison

Production settings independently select input preference Decisions, authoritative
bookkeeper and existing/concise/separate question variants for each Decisions role.
The separate preference scope answer is advisory context for the normal bounded
backend review; only the normal tools can change preferences. Its scope accuracy
is reported separately. Single-task classification fixtures do not establish
multi-task association accuracy; concurrent native scenarios remain necessary.

Observer uncertainty is retained in attention context without marking an outcome
handled or commanding repetition. Context metadata does not include a changing
observation identifier, avoiding refreshes merely because another identical
assessment was saved. Requested-append deduplication now filters session generation,
so an uncommunicated previous-generation append cannot suppress new-generation
requested commentary.

The evaluator reuses production prompts, tools, routing, recording and playback.
The everyday reference keeps observer mode off, including its existing optional
preference tool fields and model-owned acknowledgment. New families enable durable
subscriptions and observer authority; this compares the actual everyday version,
not a reconstructed historical configuration with different tools.

Exploration compares reference, repaired baseline, Decisions preferences, Decisions
bookkeeping and both. Explicitly frozen diagnostics have unique identities and
share the exploration allowance. Confirmation freezes each family's variants;
Android cannot mix a different variant into a qualified browser candidate.

## Frozen allowances and request accounting

Maximum additional allowances: 240 text classifications, 24 exploration browser
connections, 32 confirmation browser connections (two finalists), six Android
connections. The specified fixture arithmetic totals **228**, not 240:
132 development (24 x 4 + 12 x 3), then 96 held-out (18 x 4 + 8 x 3).
The remaining twelve are unused, not an excuse for extra automatic attempts.

Request reservations are durable before inference. Interrupted reservations cannot
be counted as completed or automatically retried. Provider/authentication/billing/
quota and evidence-integrity failures stop paid work. Development-guided changes
require a fresh offline freeze and retain their identity; held-out source cannot
change after the first held-out reservation. No inference occurs inside database
transactions. Classification, observer, native Decisions, backend and transcription
usage are recorded separately; monetary cost remains unknown where unavailable.

Artifacts:
`data/announcement-evaluation/pilot-20261018-natural-experience-01/`.
Network-blocked gate:
`data/announcement-evaluation/offline-20261018-natural-experience-03/`.

Run through the existing `app.announcement_eval.runner` with `--natural-experience`.
Stages remain `offline`, `pilot`, `acceptance`, and network-free `report`.
`--calibrate --fixture-set development|heldout --live` performs the bounded text
comparison. Voice stages require `--live`, a frozen offline identity and directly
reviewed selection records. This phase preserves all 96 historical voice sessions
and M0–M11 acceptance.


Native integration follows the [official OpenAI Decisions voice documentation](https://developers.openai.com/api/docs/guides/decisions-voice): input/output transcript triggers,
source-event correlation and acknowledged configuration updates. These triggers
supply transcript context, not independent proof of client speech delivery.


The corrected freeze passed 365 backend tests, 18 frontend tests, continuous audio/
duplex recording checks and Android debug build/unit checks. The preceding offline
freeze also passed; its first paid-stage invocation stopped before any request due
to tuple/list manifest serialization. That local bug was fixed and a JSON round-trip
regression added before the refreshed gate and first paid requests.


## Development comparison

132 requests completed with no provider failures. Product consequences, rather
than exact taxonomy agreement:

- Responses bookkeeping: 24/24.
- Existing Decisions bookkeeping: 23/24, with one unsafe contradiction (failure
  accepted as communication of a verified success).
- Concise Decisions bookkeeping: 22/24, contradiction plus wrong-task delivery.
- Separate Decisions bookkeeping: 18/24, six missed positive communications and
  no unsafe delivery.
- Existing preference policy: 12/12; concise and separate: 10/12 each, with a
  quoted-language question and a completion statement mistaken for requests.
  Separate scope agreement: 8/12; this is not multi-task association proof.

Independent review confirmed these mechanisms. Before held-out testing, targeted
policy refinements clarify actual instructions, human-readable task association
and factual agreement; separate bookkeeping now asks consistency independently.
Existing policy references remain unchanged. The original development transformed
results are preserved. Raw component choices were not retained for standalone
bookkeeping, so the precise rejecting component of separate's positive misses is
unresolved; subsequent requests retain raw answers and HTTP request identity.
No development rerun is authorized or performed. The refined source is undergoing
`offline-20261018-natural-experience-04`; only the still-unrun 96 held-out requests
will follow. No browser or Android provider connection has yet been used.


Preference classification errors above are detector errors, not demonstrated wrong
persisted preferences. An extra requested detection can trigger a bounded backend
review that correctly makes no change. Native voice testing must inspect actual
tool receipts and saved revisions. Conversely, the bookkeeping false deliveries
passed the same validation that authorizes handling, so they represent a direct
potential ledger error. Conservative uncertain findings remain pending with
explicit uncertainty; they are not automatically a conversational failure.


## Initial reference voice cases and observation repair

The everyday reference's idle case fully played the verified outcome in3.806s.
It nevertheless saved discretionary despite an explicit notification request and
promise, and left delivery pending. Independent review identifies a durable-memory
problem while preserving the successful immediate announcement.

The speaking reference saved requested correctly, then gave lengthy desk advice.
Recording cut active speech: final ASR word ended23ms before WAVEOF, with substantial
waveform activity. A provider 'Oh,' arrived300ms after observationend; its meaning
and playback cannot be inferred. Eventual notification is inconclusive, not proven
forgotten. The late/unrequested advice remains a contextual quality concern.

This exposed a measurement defect. The repaired harness retains nominal boundaries
and original deadline origins, and can observe active output until2s acousticquiet,
with one shared30s additional budget percase (including finalcapture). Silence is
not treated as proof of an intentional decision or completed providerturn.
Browser virtual-clock/continuous-media checks pass. Android uses its existing
recorded acoustic activity interface. No prompt, speech trigger or retry is added.

Production hash before/after repair is identical:
`ac2ef0711b4e32cd956fda1127bc040ff08e2c7755144031e812eca34ed95b59`.
Classifier questions/fixtures and provider configuration also match. A reviewed
repair proof retains source transitions, both original cases and all228 requests.
The first repair freeze was intentionally refused after the budget correction
changed source during checks; finalfreeze is `offline-20261018-natural-experience-06`.
One reserved diagnostic will check the repaired observation protocol; it counts as
another session and will not replace either original result.


## Held-out comparison and first repaired-baseline result

All 228 classification requests are complete (132 development, 96 held-out).
The stated held-out fixture matrix totals 96, despite the earlier plan calling it
108; no extra requests were added to consume the difference.
Responses and revised concise Decisions bookkeeping each matched all 18 held-out
product consequences. Existing Decisions had two unsafe delivery findings;
separate Decisions had two conservative misses caused by uncertain association.
Preference detectors each matched seven of eight held-out primary labels. A
status question falsely detected as a request remains a detector concern, not
proof of an incorrect saved preference: native bounded backend review must be
checked separately. Existing preference policy was selected for the voice screen
because it had the strongest development result and tied held-out alternatives.

The repaired reference diagnostic was not truncated. Jarvis said it would hold
onto the completion update after the caller had finished, then remained quiet
for about ten seconds. It retained the request but did not clearly state the
verified result. Both reviews classify this as inconclusive/mixed over-deferral,
not permanent forgetting or an overlap failure.

The repaired baseline idle case saved the trailing notification request,
communicated the full result in 4.212 seconds from durable completion and then
recorded handled through the conversation observer. Client playback supports the
outcome. The ASR backchannel spelling difference is harmless. This is a promising
individual result, not qualification or an adoption decision.


## Speaking baseline: confirmed application evidence defect

Five browser connections have now been consumed in this phase, including the
retained reference diagnostic. The baseline speaking case delivered a truthful
result after the caller finished, then repeated it. Both reviews found that the
first result was rejected by the observer validator because a provider input
fragment said “ready.” The recorded microphone channel was digitally silent
during that announcement. Input transcript text alone therefore incorrectly
established interruption. This is not evidence that Live talked over the caller.

The first result’s acoustic packing segment ends at audio time 45.68 seconds,
about 9.77 seconds after actual caller speech ended. Its ASR final word spans
removed silence into the repeated announcement; that artificial interval cannot
establish notification latency. The final database records handled after the
repetition. The earlier pending snapshot is stale, not a permanent acknowledgment
failure. An earlier mistaken completion classification cited a promise and was
correctly rejected by the outcome-exposure guard.

Paid comparison paused for a shared production evidence repair. Browser and Android
playback reports now also carry compact microphone-source energy/duration deltas
when the platform supplies them. Only continuous exact-zero input energy with
advancing duration, stable source/clock and padded coverage can disprove a
transcript-only interruption. Missing or noisy measurements remain uncertain;
this does not establish a solution for every physical-device echo case.

Delayed negative findings remain in the audit history but cannot refresh Live’s
context after newer assistant speech. Compact findings identify the assessed
sequence and validation reason. Context refreshes may have contributed to the
repeat, but causality is unresolved. All original recordings, grades, source
identities and request counts remain retained; a repair does not reset budgets.


Repair implementation references the standard cumulative local-source statistics
in [W3C WebRTC statistics](https://www.w3.org/TR/webrtc-stats/#dom-rtcaudiosourcestats-totalaudioenergy).
It stores metadata, not microphone samples. Exact-zero evidence is intentionally
narrow; normal noisy input may remain uncertain. Any positive uncovered gap breaks
the silence chain. Changed silence support participates in observer evidence
identity so newly available evidence can be reconsidered without invented speech.
The qualification gate also rejects mixed production sources in confirmation and
Android. Independent review accepted source
`66a3670f6f3ab90bf3590d6882b61fac08940a72462e365a48584a892ed45aed`,
subject to a fresh passing offline freeze.

The preceding offline attempt passed 379 backend tests, 19 frontend tests, Android
build/unit checks and continuous duplex checks, but correctly refused to freeze
because code corrections arrived while it ran. No paid work used that unsealed
attempt. The fresh freeze is `offline-20261018-natural-experience-08`.


## Frozen input-evidence repair and sampling follow-up

`offline-20261018-natural-experience-08` passed 379 backend tests, 19 frontend
tests, Android build/unit checks and continuous browser audio checks. The first
voice case on that repair saved requested and communicated the result in 3.482
seconds, then recorded handled correctly. Browser input energy metadata was
available in all 138 reports (115 exact-zero intervals).

The speaking diagnostic communicated a truthful result after approximately
16 seconds at the opening, with no repeated outcome and no outcome overlap.
Its lengthy desk reflection is a contextual concision/latency concern, not
forgetting. Bookkeeping remains inconclusive: a 39.9 ms catch-up polling interval
reported no new received samples despite progressing playback. Broad clock
uncertainty makes it intersect one cited word. Replaying final evidence still
fails coverage, so waiting alone cannot retrospectively establish support.
The harness also stopped its backend with one observer request still running;
its attempt is counted and its final usage is unknown.

The next narrow repair chains browser polling after the previous sample finishes
and adds a bounded, read-only observer drain after closing voice. It never fills
old gaps, retries inference or marks attention. A frozen sidecar retains final
same-result communication state separately from the original driver snapshot.
Independent review accepted the approach, subject to the fresh offline gate09.
All seven browser connections and 228 classification requests remain retained.
No further voice attempt was made while investigating these evidence issues.


## Preference-review gateway repair (eleven connections retained)

The input-Decisions occupied-conversation run exposed a local integration defect:
a Responses HTTP 200 was followed by `RestrictedBrainGateway._execute()` rejecting
a missing `origin_turn_id`. Voice and task execution continued, but the observer
stopped. This is not evidence that Live ignored context. The failed run has no
independent transcription and remains ungraded for conversational quality; it is
not replaced or rerun. Its old response usage and proposed actions are unavailable,
not zero. The paid stop remains until the fresh offline gate passes.

The repair passes explicit `None`, saves provider identity/usage/proposed calls
before local execution, and permits normal preference metadata changes for terminal
tasks without reopening execution. Four network-blocked tests exercise the real
gateway with only HTTP mocked: active, completed, newer context and wrong target.
Related policy tests also pass. Independent review accepted the approach subject
to full source-bound offline validation.

The preceding idle cases for input Decisions, output Decisions and both all saved
the requested preference, communicated truthful outcomes, and recorded handled
only after supported playback. Measured completion latencies were approximately
1.83, 3.48 and 3.18 seconds respectively. One-task 'it is finished' was assessed
in context. This small sample establishes neither reliability nor an incremental
preference-detector benefit. Conversation, late corrections and cross-session
comparisons remain necessary. Native output bookkeeping was fast in its first
idle case, but one sample is insufficient to select it over Responses.


## Native bookkeeping association defect (twelve connections retained)

Independent review caught an error missed in the primary initial ledger inspection:
the occupied output-Decisions run really played its truthful completion, but the
accepted finding cited a later desk-help offer. Correct native complete events
existed at the actual result. The adapter selected the globally latest event and
attached its containing window, discarding the earlier correctly anchored result.
Later native concise labels also classified unrelated output as complete. Final
handled happened to agree with reality; that does not establish valid authority.

The local repair keeps the latest source-ordered assessment per growing window and
preserves the earliest surviving complete citation. Later speech cannot supply
missing playback. An explicit associated contradiction makes communication uncertain.
Same-window negative, partial and refusal results supersede earlier positives.
Both saved snapshots now cite the actual outcome and remain unsupported under the
shared caller-overlap guard. Original records are unchanged. This repairs mapping,
not the concise classifier's semantic error or conservative overlap bookkeeping.
An unsupported earliest positive can also keep a later genuine repetition pending.

Concise native authority remains stopped across source repairs. The already
calibrated separate association/consistency variant is reserved for bounded native
diagnostics; there is no new classifier wording or paid classification batch.
Responses and preference-only families do not use the defective native helper.
The repaired gate11 must pass before any further provider work.

Conversationally, the 1.42-second outcome began over actual caller speech after an
explicit request to finish speaking. Jarvis subsequently returned to the desk topic.
Independent review calls this mixed/awkward timing without demonstrated serious
harm, rather than an automatic reminder-selection failure. Review used transcript,
synchronized waveform activity and playback logs, not direct listening.

Current accounting: twelve new voice sessions / 488 seconds, 34 managed backend
requests, eleven independent transcription requests, 228 standalone classifications,
19 observer jobs and 120 native Decisions events. Observer jobs and native events
are not interchangeable request counts. Cumulative retained voice sessions:108 /
3958 seconds. Cumulative backend requests:279; historical transcription/grading plus
new transcription:219. Available detailed usage remains in the ledger; unknown
monetary cost and interrupted/failed request usage remain unknown.


## Native recovery provenance

Further inspection found two native-authority recovery defects. Queued Decisions
jobs could read the current global variant after restart, dropping the original
association/consistency requirements. A late decision was also tagged with the
presentation context current at arrival rather than at its source transcript.
The adapter now freezes the session output variant and snapshots accepted result
context, variant and question identity at each transcript source. Native assessment
requires that recorded provenance and never falls back to current global settings
for delivery authority. Unknown/evicted sources remain uncertain; raw events stay
durable for inspection. The source cache is bounded to512 events. Decision-before-
source does not acquire authority automatically when the source later arrives.
Punctuation alone cannot establish spoken communication.

Thirty focused native/recovery/authority tests pass, with expanded sideband tests
included in the new full gate12. Gate11 passed its earlier source but was not used
online. No additional provider session or classification request was consumed.


## Late correction comparison and rejected model proposals

The baseline late-silence diagnostic saved suppression revision2 and answered the
later status question accurately. However, it announced the result immediately
before acknowledging silence. Completion occurred0.34 seconds after the correction
clip ended, before preference persistence. Requested revision1 context was sent
while still authoritative in storage; speech started just before suppression was
refreshed, and the refresh arrived midannouncement. Live already had the spoken
correction. This is an isolated real request-following miss, with application timing
and model choice both involved, not incorrect final memory or evidence that an
already received suppression refresh was ignored. Independent review agreed it
should remain visible without invalidating every baseline condition. It is not rerun.
The admission ASR contraction/spelling difference and a word crossing removed
silence do not undermine the unambiguous outcome evidence.

The paired input-Decisions run stopped before independent transcription. Its
reviewer proposed suppressed plus critical_only; the normal gateway correctly
rejected the contradiction. Expected tool rejection then terminated the observer.
Final suppressionrevision2 exists through normal Live handling, but conversational
quality and timing remain ungraded. Paid response identity/proposals/usage were
retained:1,284 and2,630 tokens. This is a model-proposal error plus an application
isolation defect, not a provider access failure.

The repair clarifies suppression=silent and validates schema, exact target and the
shared preference contract before execution. Rejected proposals are durable
completed/rejected observations, with no rewritten arguments, retry or preference
write. Only explicit superseded/newer-context execution outcomes are nonfatal;
unexpected gateway ValueError and TypeError remain failed and stop the observer.
Eight network-blocked real-gateway tests pass, covering active/completed tasks,
newer input, wrong target, conflicting policy, malformed JSON and unexpected
execution exceptions. Gate13 was stopped as owned work after review required this
narrowing; it was never frozen or used online. Gate14 is the fresh full gate.
The independent reviewer had conditionally accepted the repair with that narrowing;
a subsequent final-patch review hit its account usage limit. No provider work resumes
on an assumed review pass.

Current phase usage:14 voice connections /615seconds;46 managed backend requests;
12 independent transcription requests;228 standalone classifications. Durable
background work:12 Responses-bookkeeping jobs, six preference-review jobs, and four
native processing jobs. Native trigger events:161 /74,363 reported tokens. Native
processing jobs are not additional API requests. Cumulative retained voice:110
connections /4,085seconds;291 managed backend requests;220 historical plus current
transcription/grading requests. Detailed ledgers retain available usage; monetary
cost remains unknown. Earlier canceled/preaudit failed request usage stays unknown.


The independent reviewer was available on the later user-requested continuation
and verified the final narrowed implementation. Acceptance is conditional on
passing gate14 and freezing its exact source. The earlier usage-limit interruption
is retained as history; it is not a continuing review blocker. No additional voice
connection was consumed while review and local validation were pending.


## Repaired preference diagnostic and prior-question false negative

The one explicitly reserved changed-source diagnostic respected silence and gave
an accurate status answer. It does not demonstrate extra input-Decisions benefit:
all four triggered backend reviews made no proposed changes; normal Live saved
suppression revision2. Completion also occurred about six seconds after correction,
versus0.34seconds in the baseline. The adaptive settling rule produced different
actual races, so pass/fail alone cannot establish better preference capture.

The status answer began1.52seconds after actual caller voice ended, with silent
microphone input and usable progressing playback. Yet the observer stayed pending:
provider 'now' and the entire 'It is completed' answer shared a coarse200ms interval,
followed only by punctuation. A validator exception demanded a later alphanumeric
fragment, making equivalent split and coalesced answers behave differently.
This was a conservative bookkeeping false negative, not actual interruption.

The narrow repair removes that fragmentation-dependent requirement. It retains
source ordering, a short prior-question tail within250ms of answer start, semantic
completeness and full client playback validation. Later caller fragments, unknown
ordering, long input, partial findings and punctuation-only citations remain
ineligible. Fourteen focused tests pass. Read-only saved replay supports both
actual answer findings; original grades, pending records and databases are untouched.
Independent review accepted conditionally on the full gate15/source freeze. This
fix does not establish physical hearing or qualify general interruption behavior.

If baseline reaches confirmation, its one preplanned changed-silence sample is
explicit replication of the retained known race. It is not a claimed behavior fix
or a replacement. A pass cannot erase the original miss, and no additional retries
follow. Independent review clarified that fixed confirmation replication is valid;
its earlier absolute do-not-repeat wording applied to rerunning until green.


## Gate15 and cross-session baseline

Gate15 passed:403 backend checks,20 frontend checks, Android build/unit checks
and continuous duplex browser checks. The frozen source is
`d5f72ed7220d55434d2f27c2f0c92c041d640f81ce7fa27fd8e2c40d06ea829a`.

Baseline case16 consumed two connections. Jarvis spontaneously provided the
correct requested result4.069seconds after resumed eligibility,9.548seconds
after durable completion, then accurately answered a later status question.
No unsolicited repetition occurred. Independent review found the conversation
acceptable; this supports natural cross-session memory.

Automatic acknowledgment remains unqualified: startup clock uncertainty leaves
possible early playback coverage unproven, and the later question's provider
fragment order resembles interruption despite recorded caller speech ending
1.47seconds before the answer. No concrete clock error or actual barge-in was
found. Preserve pending uncertainty; this is not evidence of a reminder-selection
failure and does not justify a speculative timeline redesign or voice rerun.


## Input Decisions cross-session comparison

Case17 used two connections, bringing exploration to19. Requested outcome was
remembered, announced promptly in the resumed conversation and answered accurately
later. A second unsolicited result followed roughly8.8seconds after the first.
Independent synchronized activity review disproved the initial recovery hypothesis:
actual wake speech ended2.4–2.8seconds before initial playback. Provider wake
fragments instead overlapped coarse outcome intervals, producing a conservative
false-negative communication assessment. The later handled record correctly cites
the second actual outcome and usable playback; it is not a wrong acknowledgment.

The input-triggered review also introduced an unnecessary policy change: normal
admission already saved requested intent/reason at revision1 with critical_only;
the review retained the same intent/reason but changed policy to follow_progress
and created revision2. The user requested completion notification, not progress.
This provides no demonstrated capture benefit and is a concrete additional-role
quality/cost regression. No unchanged C2 rerun is planned.


## Separate-question native bookkeeping pilot

Case18 (twentieth exploration connection) succeeded: requested preference stayed
revision1/critical_only, truthful result fully played2.699seconds after completion,
and native questions progressed from partial/uncertain to complete with task
association and result consistency both yes. The authoritative finding cites the
actual outcome with source-time provenance and validated playback; handling followed
speech. Five native events reported6,786tokens. This demonstrates the repaired
variant can work in an idle case, not superiority or general qualification.

Remaining fixed exploration: this variant during speech and across sessions, then
one both-roles speaking diagnostic. These fit the remaining four connections.
No further standalone classification requests or unchanged C2 reruns are planned.


Case19 separate-native speaking comparison delivered the truthful requested result
7.16seconds after the caller opening, without outcome overlap or repetition.
A separate floor acknowledgment ('Sure, go ahead, I am listening') overlapped the
description by about1.62seconds; this is a conversational-quality observation, not
an automatic completion-update failure. A short desk reflection preceded the
result; the later promise to stay quiet after the caller had finished is mildly
awkward. Native classification first rejected non-outcome speech, then cited the
actual outcome with correct task/result provenance and supported playback.
It produced24trigger events /39,074reported tokens. Correctness in this case does
not establish an incremental benefit over Responses bookkeeping.


Case20 separate-native cross-session is inconclusive, not a confirmed reminder
miss. Provider text asserts the correct requested outcome and later status, but
Whisper omits both resumed response intervals. Local independent review confirmed
substantial original recorded audio, live progressing unmuted playback, continuous
new-track coverage, and byte-identical packed slices at31.46–33.30seconds and
50.28–52.28seconds. Original/packed audio is mono16-bit24kHz; the runner uploads that
exact packed file. No deterministic recording, packing or upload defect was found.
Intelligibility and semantic playback remain unverified without direct listening;
attention correctly remains pending. Neither successful notification nor a hard
Live limitation follows. No transcription retry or replacement case was made.
After the local integrity check, the final fixed both-roles diagnostic proceeds;
it reaches the24exploration connection ceiling.


## Finalist freeze

The final both-roles speaking case gave the correct outcome5.63seconds after the
opening; playback and silent caller input support delivery. Native questions
nonetheless classified the complete result partial (association/consistency yes),
then classified later desk reflection none. This is a missed communication record,
not harmless label variation; pending avoids an unsupported handled claim. Input
review was a no-op.87native events reported91,476tokens. Minor floor acknowledgment
overlap and verbose reply are quality notes, not automatic rejection.

Independent review recommends **Configuration1 only** for frozen confirmation.
No Decisions role demonstrated incremental benefit sufficient for another full
suite. The combined family cross-session case remains unrun at the24connection
ceiling; no claim of full coverage for that family is made. The selected16connection
cohort uses source d5f72ed7220d55434d2f27c2f0c92c041d640f81ce7fa27fd8e2c40d06ea829a
and existing/existing variants. This is a finalist decision, not default adoption.

Exploration accounting:24voice connections /955provider seconds,71managed backend
requests,19independent transcription requests,228standalone classifications. Durable
background records:22Responses-bookkeeping jobs,12preference-review jobs and17native
local-processing jobs (two retained failed jobs).353native trigger events reported
254,177tokens; local native processing is not another API request. Cumulative retained
voice:120connections /4,425seconds,316managed backend requests,227historical plus
current transcription/grading requests. Monetary cost and earlier missing/canceled
request usage remain unknown.


## Confirmation in progress

The first requested-idle and requested-during-speech cases were acceptable: fully
played outcomes2.818seconds after completion and5.95seconds after caller opening.
Both were correctly handled by the Responses observer afterward. Independent audit
confirmed correct result/source/playback; bookkeeping followed speech by roughly
4.47seconds and9.34seconds respectively (separate from notification latency).
Sparse backchannels grouped across silence cannot establish continuous interruption.

The silence/status case respected suppression and answered status accurately. Its
false-premise bookkeeping probe exposed an isolated real wording defect: the normal
acknowledgment tool returned verification_pending/statepending, but the backend
summary and Live said Recorded. This was inaccurate receipt summarization. Durable
authority remained correct: two observer findings were none before the actual
status answer, then the genuine played outcome cleared attention. Independent
review considered the overall case acceptable/borderline, keeping the misleading
claim visible without interrupting the frozen suite for one artificial probe.
Repeated behavior or false durable handling would warrant stronger action. Auxiliary
preference-mismatch flags on admission/status were false positives, nonauthoritative.


## Confirmation stopped: interruption and reconnect (material problem)

The remaining confirmation cases (concurrent, changed silence, ordinary update,
matched control) were accepted as previously summarized. The concurrent case's
duplicate publications stayed quiet. Changed silence passed as a fixed
replication, but that pass does not erase the earlier 0.34-second race. The
ordinary update arrived as a brief "By the way" at a natural opening. The matched
control made no invented claim.

The ninth confirmation run (`interruption_reconnect`, two connections) is a
**material problem**, independently agreed:

- Requested notification. Jarvis said only "It's finished." about 1.25 s after
  completion (assistant.wav 24.60–25.00 s). It did not name the task or result.
  The scripted caller barged in at 24.75 s and overlapped most of that phrase.
- Jarvis yielded with mild backchannels. The caller then said they wanted to
  hear a short result after finishing. Jarvis replied "I'll keep it brief and
  wait till you're done", then stayed silent for 13 s. After reconnect it said
  only "Go ahead". The result never effectively reached the user, and the
  away-notification outbox was superseded.
- The Responses observer committed `handled` at 28.46 s, before the caller had
  finished. Provider input timestamps placed "Hold" at 20.8 s, after the outcome
  at 19.0–19.4 s, so the transcript-only barge-in check saw no overlap. Client
  microphone energy is used only to *disprove* transcript barge-ins, never to
  establish one. A roughly 2.07 s clock-mapping uncertainty also limits
  energy-based alignment. The observer never saw the caller's reaction.
- The handled thinking note reached Live at 28.84 s, and the reconnect context
  also carried handled. This plausibly suppressed a restatement; causality
  remains uncertain, because Live may have been waiting for further speech.

Effect: a requested update was not effectively received, the communication was
incorrectly remembered, and Jarvis made a misleading promise. The fault is
systematic (the observer commits before the user's reaction is known), not a
one-off phrasing issue. Per the plan, the unchanged candidate is stopped: the
remaining six cases (`between_sessions`, two repeats, `observer_restart`,
`reminder_clarification`, `long_request_correction`) were not run. Review used
waveform energy, timing, transcripts and DB records, not direct listening. The
independent reviewer for this and future cases is a Claude Code subagent, which
differs from the earlier Codex reviewer.

Candidate repair direction (not implemented): defer observer assessment of an
outcome until the user's next turn after it has ended or a quiet window has
passed. Include that reaction in the semantic input, and treat reactions such as
"hold on / tell me after / what was that" as uncertain rather than complete.
This is semantic judgement, not a phrase parser. Live's prompt should also make
outcome updates self-contained (name the task and result), because "It's
finished" alone is fragile under any overlap.

Confirmation accounting: 9 of 16 connections used. Cumulative retained voice:
129 connections.

