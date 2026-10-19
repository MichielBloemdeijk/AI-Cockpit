# Natural task updates and conversation bookkeeping

Implemented both additions behind `JARVIS_CONVERSATION_OBSERVER_MODE` and tested
the production path. **The direction works in a verified idle example, but this
candidate is not qualified for adoption.** The existing provisional configuration
A remains the development default; the new mode is off outside isolated tests.

The frozen pilot stopped after two browser sessions because of an incorrect saved
notification preference, observer semantic errors caught by a guard, and recording
uncertainty. No tuning, replacement sessions, larger acceptance or Android voice
confirmation followed. M0–M11 acceptance and all historical evidence remain intact.

## Implementation

- Reused the existing requested/discretionary/suppressed preference history and
  admission/update-policy arguments. Requested terminal outcomes use commentary;
  other terminal facts use thinking. Preference reasons/revisions refresh context
  without repeating unchanged requested commentary. Pending requests survive
  session closure. Reminder, question, progress and away-notification contracts
  remain separate.
- Added a concise natural-conversation policy. Live owns relevance, wording and
  conversational timing. There is no forced-speech command, timed announcement
  retry, transcript preference parser or notification code in task definitions.
- Added durable observer work, assessments, inference attempts, usage and recovery
  leases. The observer uses gpt-5.6-luna, low reasoning and priority tier, with
  structured output and no task-action tools. It requires exact assistant citations;
  it cannot change preferences or write general memory.
- Added authenticated browser/Android playback telemetry: session/controller
  identity, monotonic origin bounds, receiving/concealment statistics, progression,
  mute/pause/volume and track/gap evidence. Uncovered clock uncertainty, caller
  overlap and incomplete playback leave attention pending. Late caller fragments
  are checked again before commit.
- Terminal model-report acknowledgments cannot bypass observer verification in
  enabled/shadow mode. Successful observations use the existing attention audit,
  with `handled_by=conversation_observer`; append receipts remain transport only.

The additive migration `20261015_0038` was applied to the project-owned development
database. No application data was reset. Existing voice sessions were not restarted.
The [implementation and commands](../conversation-bookkeeping.md) describe staged
configuration and the evaluation entry point.

## Offline and calibration evidence

Frozen gate: `data/announcement-evaluation/offline-20261015-bookkeeping-06`.

- **296 backend checks passed:** 269 existing regression checks and 27 observer
  checks, with external networking blocked.
- **13 migration/recovery tests passed** separately on the final source.
- Frontend type checking and **18 Live/playback tests passed**. Android debug build
  and unit tests passed.
- Continuous-silence, duplex and recording fixtures passed; measured fixture duplex
  timing was 170.67 ms. Cached historical positive/negative evaluation replay passed
  without provider requests. Shadow commit behavior was covered by offline fixtures.
- **All twelve fixed production-observer calibration fixtures passed.** These were
  classification requests without durable delivery commits. They included admission,
  promises, incomplete/ambiguous speech, quotations, false premises and muted/paused
  playback. They did not establish performance on all realistic conversation contexts.

After the pilot, a network-blocked shadow replay applied all six saved production
observer responses to private database copies. It reproduced the original full
guard results and left every presentation pending in shadow mode. No model request
was made and the original databases were unchanged. This was a post-pilot cached
response replay, not a claim that the new model was screened over all historical
sessions before the pilot.

Local development attempts remain preserved: the first expanded gate reached its
360-second timeout; subsequent checks passed but source-freeze guards rejected edits
made during those runs. Only the final unchanged gate authorized paid execution.

After calibration, Windows cp1252 decoding failed while reading UTF-8 report text,
before any voice session opened. Resuming with `PYTHONUTF8=1` reused all twelve
saved responses, with **zero repeated calibration requests**. This operational
requirement is documented; model configuration, prompts and source were unchanged.

## Browser session 1: requested completion while idle

Task `37d29a63-8e3a-4b30-96ae-a7fe926320b9`; session
`aa8b49c0-b45d-4aba-86ae-5d0fd0915d1a`, generation 1.

The preference was requested and the terminal outcome used commentary. Native
captions and independent recorded-audio transcription agree:

> Your Hello World page is ready.

The fully played outcome finished **3.583 seconds after durable completion**,
including application queueing. It did not overlap caller speech. The observer
recorded handled **4.513 seconds after the outcome's acoustic ending**. Live made
no acknowledgment handoff. This demonstrates requested notification and independent
delivery bookkeeping working together in this case.

The original automated conversation grade remains inconclusive because ASR stretched
early backchannel words across removed silence. Direct review retained that grade
and used original PCM islands plus native captions to distinguish the listening
backchannels from substantive responses: “Mm-hmm” had 330 ms of voiced overlap;
“Sure” had 900 ms of voiced audio, with 276 ms over the caller. Neither was a task
outcome or substantive answer interrupting the request. The completion itself had
valid continuous playback evidence without this timing ambiguity.

One diagnostic flag was wrong: the observer called an already-recorded notification
promise unrecorded because fulfillment had not yet been spoken. The flag changed
neither preferences nor attention state and is retained as an observed defect.

## Browser session 2: requested completion during caller speech

Task `ccc79d13-2b6b-489f-bd88-c17abd5d1aeb`; session
`d1384388-6a9a-43fe-98b3-8eb64b676a6a`, generation 1.

Three separate findings prevent qualification:

1. **The managed admission model supplied the wrong preference.** Its raw
   `dispatch_task` arguments contain `notification_intent="discretionary"` and
   reason “Notify when the Hello World page task is complete.” The caller explicitly
   requested notification and Live promised it. Storage retained the supplied value;
   it did not change requested into discretionary. Consequently the outcome used
   thinking. This is not a clean occupied-conversation test of requested commentary.
2. **The observer twice mislabeled admission/promise speech as completion.** It cited
   “I've created the task to write a Hello World page” and then the same admission
   followed by a future notification promise. Both proposals were rejected because
   the cited speech preceded verified outcome context. Neither caused a premature
   handled transition. The twelve-fixture calibration was insufficiently representative;
   these real contexts are saved as new negative regression evidence.
3. **Independent recording has gaps during the outcome.** Native captions and ASR
   contain “Also, your Hello World page task is done.” It follows the caller's speech,
   and the nominal acoustic endpoint suggests **7.27 seconds** from the next opening.
   However, three callback gaps of approximately 123, 107 and 123 ms overlap the
   recorded phrase. Full independent client-playback verification is inconclusive;
   **7.27 seconds is not counted as a confirmed deadline pass.**

The observer later cited the actual terminal statement and marked the update handled.
Its production playback telemetry supported that decision, and its commit followed
the captured outcome. The independent recording gaps prevent complete-interaction
qualification. Startup greeting overlap is recorded separately; it is not evidence
that task completion caused interruption.

No matched-control session ran, so whether completion adds interruption beyond
ordinary conversation remains unanswered. Silence, concurrent outcomes, duplication,
reconnect, cross-session announcements and observer restart have offline evidence
but no new live qualification in this phase.

## Usage, artifacts and disposition

New usage:

- **2 voice sessions, 75 reported seconds**, both finalized.
- **5 managed backend Responses:** 22,790 input and 332 output tokens.
- **12 observer calibration requests:** 6,635 input and 1,400 output tokens.
- **6 production observer requests:** 30,387 input and 2,343 output tokens.
- **2 transcription requests; zero new separate semantic-grading, simulated-caller
  or real-worker requests.** Monetary cost is unknown.

Cumulative retained usage is **79 voice sessions / 2,773 seconds**, 195 managed
backend Responses, 76 transcriptions and 115 historical semantic-grading requests.
The eighteen new observer requests are reported separately from those categories.

Live artifacts are under `data/announcement-evaluation/pilot-20261015-bookkeeping-01`:
the report, calibration responses, per-session audio/transcripts/events and source
identities, frozen observer evidence, cited agent reviews, new negative fixtures,
encoding recovery and cleanup/identity records. Original automatic assessments remain
unchanged. A network-blocked report replay made **zero provider requests**.

Both owned sessions are disconnected and none of the four owned server ports is
listening. Source/configuration identity still matches the frozen offline candidate.
The remaining fourteen browser connections and all six gated Android connections
were not consumed. Android build/unit success is not Android voice qualification.

The next candidate should correct notification-intent capture in normal admission,
calibrate the observer against the saved realistic admission/promise negatives,
and repair recording continuity before more paid voice work. Retain the temporal
and playback guards. These findings do **not** establish a hard GPT-Live limitation
or justify introducing forced speech or per-task acknowledgment logic.
