# Task-announcement evaluation: initial screening

No model-controlled candidate qualified. Production defaults remain unchanged,
and M0–M11 acceptance remains closed. The isolated harness is implemented with
`offline`, `pilot`, `acceptance` and replayable `report` stages.

Eight browser/WebRTC voice runs were recorded on 11–12 October 2026. The
thirteen-case diagnostic ceiling includes the original baseline and the retired
diagnostic described below. All eight Jarvis sessions finalized with usage.
There were no authentication, billing or quota failures. Paid screening stopped
on verified silence regressions; it did not continue sampling until a pass.

## Recorded result

- Baseline, explicit notification: completion was spoken in 3.23 seconds. The
  previously reported missed notification was not reproduced in this one run.
- Commentary plus relevance, explicit notification: completion was spoken in
  6.49 seconds. Application queueing accounted for 4.17 seconds. A brief “Sure”
  backchannel overlapped the caller; the substantive reply followed the caller.
- Commentary plus relevance, silence: after verified completion was delivered
  and its append receipt observed, Jarvis said “The Hello World page is ready.”
  This violated the caller's explicit request to stay quiet. Append delay was
  490 ms. The admitted task's stored `silent` policy remained unchanged.
- Thinking plus relevance: the explicit-notification outcome arrived in 3.39
  seconds, but the admission reply interrupted the caller. Its silence case also
  announced completion despite the caller's request.
- Thinking plus reconsideration: the explicit outcome arrived in 4.56 seconds;
  the interruption judgment was inconclusive because its cited span was a
  permitted backchannel. Its separate silence case announced completion and
  failed the mandatory silence check.

One earlier commentary silence run was retired as an inconclusive diagnostic.
Admission selected `silent`, and the production projector withheld terminal
context entirely. It therefore could not test Live's response to delivered
facts. Its acknowledgment, “I'll stay quiet when it finishes,” was initially
misgraded as unwanted speech. Original audio, provider events, source identities
and earlier judgments remain retained. The repaired experiment projects terminal
context through the normal service even for silent tasks, preserving their policy
and the acknowledgment contract. Production retains its existing projection rule.

The final screening record contains two passing runs, four failures and two
inconclusive diagnostics. Three candidates independently failed explicit silence.
No candidate reached the occupied-conversation or paired delivery-timing screen.
The unconditional-speech diagnostic was unnecessary because candidates did speak.

## Evidence and validation

The final replayable report is
`data/announcement-evaluation/pilot-20261011-04/report.json`. Each run directory
contains separate caller/assistant WAV recordings, word-timestamp transcription,
provider events and append receipts, verified durable task state, presentation
state, playback progression, clocks, source/configuration/prompt/corpus hashes and
rubric-bound semantic judgments. `aggregate-usage.json` deduplicates request
accounting across resumed reports.

Grading repairs distinguished task admission from worker completion, supplied
explicit word indices with bounded structured output, required cited negative
judgments, and checked interruptions against caller audio windows. A missing
append receipt or an unsupported semantic penalty cannot establish a pass.
Silence alone never establishes model intent. All earlier rubric judgments remain
saved; regrading did not reopen voice sessions.

A Windows default-codepage error interrupted report writing after the eighth
voice run. UTF-8 reads and durable request accounting were added, and the report
was reconstructed from retained evidence. Six successful grading responses whose
in-memory ledger was lost were recovered from their saved response IDs and usage;
their HTTP request IDs are unavailable. No voice session was repeated for this
repair.

Final local validation passes 111 tests and covers profiles and production isolation, fenced fixture
publication, silent-terminal projection, delivery receipts, deadlines, missing
evidence, premature acknowledgments, duplicates, cohort identity, reconnect
recording, UTF-8 accounting and offline networking. A network-blocked Chrome
exercise verifies continuous silence, actual playback and reconnect retention.
Android debug assembly and JVM tests pass; a fresh migrated evaluation host starts
with zero provider requests.

All seven presentations created in these voice runs remained pending at the
recorded observation boundary. No premature `handled` transition was observed.
This does not establish correct acknowledgment behavior across interruption,
duplication and reconnect; those acceptance scenarios remain unrun.

## Paid usage and scope

- Jarvis: eight voice sessions, all finalized; 21 unique managed backend
  Responses requests, all reporting priority tier.
- Transcription: eight successful `whisper-1` requests.
- Blind grading: 23 requests, comprising 22 successful responses and one initial
  HTTP 400 request retained during structured-output harness repair.
- Independent caller, Android Live confirmation and real worker: zero runs.
- Monetary cost: unknown. Available provider usage is retained separately.

The required 36 scripted, six adaptive and six Android acceptance cases were not
started because no screening candidate qualified. The emulator recorder and
independent caller are implemented but their paid acceptance paths remain
unvalidated. Physical acoustics and subjective voice naturalness are outside
the gate.

## Decision

Retain current production defaults. Commentary plus relevance produced a timely
requested update in screening, but it cannot be recommended while it violates
explicit silence. Thinking and reconsideration did not resolve that requirement.

The remaining blocker is reliable preference handling when a verified outcome
arrives. A subsequent experiment should make the user's notification/silence
preference explicit in the verified update context and examine acknowledgment
delivery separately. That would be a new candidate; this report does not approve
it or replace the failed results with unconditional speech.
