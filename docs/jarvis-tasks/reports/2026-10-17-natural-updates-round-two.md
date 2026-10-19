# Natural updates: second four-configuration comparison

The user authorized a fresh local comparison of all four configurations, a small
online screen of each, and full testing of the most promising candidates. This is
phase `natural-updates-four-configurations-v2`. The first phase remains unchanged
and is included in cumulative usage: 84 historical voice connections, 49 first-phase
calibration attempts and the retained failed idle screen.

## Frozen comparison design

1. Normal backend preference capture; Responses communication bookkeeper.
2. Native input Decisions detecting preference changes; Responses bookkeeper.
3. Normal backend preference capture; native output Decisions bookkeeper.
4. Native input and output Decisions, with separate responsibilities.

All use identical speech policy, notification routing, task tools, voice and
recorded caller audio. Requested outcomes enter commentary; other outcomes enter
thinking. Both bookkeepers now share the same semantic classification policy.
Neither Decisions nor the observer chooses when Live speaks.

Local network-blocked tests cover all four configurations, including trigger
selection, complete/partial/muted/stale outcomes, durable delivery and idempotency.
Shared suites cover admission freshness, preferences and source-event ordering.
These tests cannot measure model accuracy or rank configurations by judgment.

The ambiguity fixture now contains two plausible referents for “Done”: enabling
notifications and changing the layout. Its negative label is frozen before new
requests. Old fixture labels and their failed results remain unchanged.

The new ceiling is 60 standalone calibration requests (24 Responses bookkeeping,
24 Decisions bookkeeping and 12 Decisions preference cases), eight browser
screening connections (two per eligible configuration), full sixteen-connection
browser suites for at most two frozen finalists, and six Android connections for
the browser winner. Reconnects and failures count. No automatic replacement runs
or retries. Runtime backend/observer/native-trigger usage is recorded separately.

Offline validation precedes all paid work. A failed classifier cannot be
authoritative in voice screening. Service unavailability remains distinct from
classification failure. Authentication, billing, quota or evidence-integrity
failures stop paid execution. Production defaults remain unchanged until a
candidate qualifies. Confirmation and adoption are gated, not assumed.

## Execution record

The final offline freeze (`offline-20261017-natural-07`, source `c59156d8`)
passed 269 backend regressions, 69 observer/configuration tests, 18 frontend
tests, browser continuous-audio/duplex checks (42.67 ms calibration), and Android
debug build/unit checks. All four configurations passed the mocked delivery
authority checks. The earlier freeze also passed its tests, but correctly rejected
a source change during review and is not used for paid qualification.

The new Responses calibration passed all 24 frozen cases. Decisions preference
classification passed 12/12; Decisions bookkeeping passed 19/24. Its five label
failures were `real_admission_0`, `wrong_outcome`, `admission_homophone`,
`contradiction`, and `wrong_task`. In the homophone admission/promise example it
classified complete and selected a played source window, so shared deterministic
guards would permit an incorrect acknowledgment. Other failures included safe
taxonomy disagreements and empty source selections. Configurations 3 and 4 fail
the calibration gate; their native voice variants are not qualified or measured.
This is a result for the current question/input format, not a hard Decisions limit.

Configuration 1 passed idle screening: full outcome in 2,986.923 ms and correct
observer acknowledgment 4,247.822 ms later. Configuration 2 passed idle screening
in 1,983.799 ms, with acknowledgment 3,696.491 ms later. Its native input trigger
worked; the bounded backend review made no preference change because admission
already saved requested. Provider `Right` versus ASR `Great` was an isolated
introductory acknowledgment: direct review retained that token disagreement but
found no uncertainty affecting task, preference, outcome, interruption or deadline.
The original automatic assessment remains unchanged.

Configuration 2 also passed completion during speech: the complete outcome played
5,610 ms after the caller's opening, without substantive caller overlap, and was
acknowledged 6,342.823 ms afterward. A preceding ambiguous admission “Done” remained
unhandled. Slightly awkward hold-off wording at the opening is a quality note.
Configuration 1's paired speaking case delivered the correct outcome but finished
11,730 ms after the opening, missing the deadline. Its “I'll wait for you to finish”
promise overlapped the caller by 1,520 ms; the completion announcement did not.
Without a matched control, attribution of that promise to completion is unproven.
Unmatched ASR words in an admission stutter remain uncertain, independently of the
verified outcome and deadline. Configuration 1 did not qualify for confirmation.

Configuration 2 was frozen as the only finalist. Its first two confirmation cases
passed: idle outcome 2,673.451 ms; requested outcome during speech 4,620 ms after
the opening. Correct observer records followed both. Two separate brief listening
acknowledgments were graded separately, avoiding treating intervening silence as
speech; no additional provider requests were needed for this review correction.

The silence/status case correctly withheld the unsolicited outcome, rejected the
false prior-delivery premise, and later answered “It's complete.” The result stayed
pending because native transcript intervals placed the caller's final “now” beside
the answer's first delta. Independent recorded audio shows the caller actually
stopped 1,520 ms before the response. This is an application evidence-validation
defect, not an interruption of that status answer by Live. A separate spoken
correction overlaps the bookkeeping request and remains a conversation-quality
observation. The failed run remains failed.
One packed ASR acknowledgment crossed removed silence and retains uncertain timing;
it is not treated as twenty-one seconds of speech. The status outcome itself is
independently aligned and fully played.

The repair handles only a short, ordered prior question tail, requires subsequent
outcome words and full playback, and preserves rejection for actual later caller
fragments, longer overlaps, missing ordering and punctuation-only suffixes. All 75
observer/configuration checks passed. A separate evaluator typo omitted the adjective
“complete” from its recognized terminal predicates; that regression test also passes.
Neither repair changes model prompts, subscriptions, speech triggers or preferences.
Offline replay of saved classifications still yields Responses 24/24, Decisions
19/24, with zero provider requests. The saved status response now passes validation
in replay; its historical database and grade remain unchanged.

The repaired source `1f953c17` passed a fresh gate (345 backend tests, 18 frontend,
browser and Android checks) and two fresh screens: idle 2,639.130 ms and speech
3,100 ms. Noncritical acknowledgment spelling/timing uncertainty remains explicit
in direct reviews; all substantive outcome evidence is aligned. The restarted
confirmation idle case communicated its full two-clause outcome in 4,704.615 ms,
but remained pending under the old audio-quality policy.

Saved RTC samples and independent ASR showed the complete outcome words and
progressing playback, with 7.89% estimated concealment across its mapped range.
The old guard evaluated each overlapping fragment range separately and rejected
individual report estimates over 50 ms. This produced a conservative false-pending
record. Concealment remains a quality indicator, not proof that a word was lost.
The [W3C definitions](https://www.w3.org/TR/webrtc-stats/#dom-rtcinboundrtpstreamstats-concealedsamples)
include synthesized replacement samples; `silentConcealedSamples` describes silent
replacement output, not proof that the original content was silence. None of that
count is discarded as harmless.

The revised guard still requires coverage of every cited fragment and one track
and clock origin. It measures quality once over the union of mapped ranges, with
a 10% aggregate ceiling and at most 250 ms estimated replacement per report.
It never averages across uncovered gaps between disjoint cited ranges. These are
engineering usability thresholds, not standards-backed intelligibility guarantees.
Muted, paused, gapped, stalled and interrupted playback remains disqualifying.
New fixtures cover duplicated uncertainty ranges, sustained loss, muted required
content and track replacement across individually covered fragments.

Source `667e4909` passed the new offline freeze (`offline-20261017-natural-09`):
270 backend regressions and 77 observer/configuration tests, 18 frontend tests,
browser continuous recording/duplex checks and Android debug build/unit checks.
Zero-request classification replay remains Responses 24/24 and Decisions 19/24.
The saved quality-blocked outcome passes validation in replay; historical state
and grades remain unchanged. Final re-screening is complete. Six screening and four confirmation connections
were consumed before this source; the final two screens exhaust the eight-screen
allowance. No Android provider connection or default adoption occurred.

Evidence is retained in
`data/announcement-evaluation/pilot-20261017-natural-02/`. Screening order is frozen
as configurations 1–4 for idle completion, then 4–1 for completion during speech;
unqualified configurations retain unrun cells rather than replacement sessions.

Official references: [native Live Decisions triggers](https://developers.openai.com/api/docs/guides/decisions-voice)
and [Decisions questions](https://developers.openai.com/api/docs/guides/decisions).
Native triggers consume transcripts, can coalesce deltas and return out of order;
source-event correlation and playback validation remain necessary.


## Final review, 18 October

The final source's requested-idle screen delivered its outcome in 3,084.126 ms
and correctly recorded observer acknowledgment. The requested-during-speech
screen fully played the correct outcome 8,480 ms after the actual opening, with
no outcome overlap and correct observer acknowledgment. All substantive outcome
words agree between provider transcript and independent audio transcription.

However, “I'll wait until you're done” overlaps actual caller speech by 1,240 ms.
It starts about 900.5 ms after completion context receipt, so it cannot be dismissed
as preceding the update. It is a separate promise rather than the outcome. This
establishes a real conversational overlap, but not whether task context caused it;
there is no same-source matched no-completion control. A noncritical opening
“Sure” also crosses packed silence and retains uncertain exact timing. The direct
review therefore demonstrates requested selection, timing and bookkeeping, but
**does not qualify the complete interaction**. No historical grade is overwritten.

Configuration 2 is the strongest direction: input Decisions preference detection
plus the Responses bookkeeper. Configuration 1 missed a notification deadline.
Configurations 3 and 4 failed the Decisions-bookkeeper calibration, including an
unsafe admission-as-completion classification, and were not tested with native
voice. Local mocks establish integration behavior, not classification accuracy.
No configuration is an accepted winner, and the existing default remains unchanged.
The full suite and Android gate remain incomplete; they were not passed implicitly.

The eight screening connections are exhausted. Paid execution stops here rather
than bypassing the failed interaction gate or replacing its attempt. The next
specific diagnostic, in a separately bounded phase, should pair Configuration 2's
occupied completion with the identical no-completion conversation, and include a
floor instruction that needs no spoken acknowledgment. Preserve both variants as
controls: that distinguishes generic acknowledgment of the floor instruction from
completion-induced interruption without introducing forced speech. If that resolves
the attribution and any actual timing defect, freeze the candidate and run the
full browser suite before Android and default adoption. No hard GPT-Live limitation
is established by this result.

Round-two usage: 12 browser connections / 511 reported seconds (eight screening,
four prior-source confirmation), 37 managed backend requests, 60 standalone
calibration requests, 32 runtime observer requests, 331 observed native Decisions
completions (124,288 reported input tokens), and 12 independent transcriptions.
No simulated caller, real worker or voice semantic grader ran in this round.
Cumulative voice: 96 connections / 3,470 reported seconds; managed backend: 245;
independent transcription: 93; historical semantic grading: 115 unchanged.
Classifier usage remains separately recorded; monetary cost is unknown.
All owned case processes have exited, and all recorded voice sessions finalized.
The report and direct review were updated without provider requests.
