# Preference-aware announcement screening

The follow-up produced evidence that GPT-Live can announce a requested outcome
through thinking and remain quiet for a suppressed outcome. Neither new candidate
qualified for acceptance. Production defaults remain unchanged, and M0–M11 stays
closed. Paid screening stopped after the original thirteen diagnostic runs.

## Implementation and isolation

Two profiles were added to the isolated evaluation host:

- `thinking_preference`: a shorter Live prompt with the documented
  Backchannel/Interruption/Delegation structure, and verified terminal context
  containing the saved notification preference. All terminal context uses thinking.
- `preference_routed`: the same prompt and preference recording, with commentary
  selected only when the saved intent is requested. Discretionary and suppressed
  outcomes use thinking.

The backend tool catalog accepts requested, discretionary, or suppressed intent
and a brief reason on normal admission and policy-update calls. Model-authored
intent is stored in ordered durable task events, with revision, voice-session,
generation and tool-call provenance. Later preferences supersede earlier ones.
Stable action receipts prevent replaying an older policy call from overwriting a
newer preference. No fixture or transcript parser supplies the model's decision.

Terminal appends read the latest saved preference and provide short verified facts
with opaque acknowledgment handles. Silent outcomes remain visible in these
experiments so that silence after receipt can actually be tested. The normal
coordinator, gateway, projector, worker fencing and browser/WebRTC media path run.
All changes to tools, prompts and routing are process-local. Production retains
its policy gate and acknowledgment semantics.

Acknowledgment observations are reported separately: receipt of context, recorded
complete speech, client playback and durable handled state are different facts.
The observer does not auto-handle presentation rows. A runtime mechanism that
records communication from correlated playback evidence has not been implemented
or accepted by this experiment.

## Five additional live runs

The prior eight runs were retained with their original source identities and
grades. The follow-up added five fresh sessions on 12 October 2026:

1. **Thinking/preference, explicit notification: passed.** The backend saved
   requested intent before completion. Jarvis said “That page is ready.” The
   complete outcome was played 2.232 seconds after durable completion; application
   append delay was 558 ms, and the thinking receipt was observed.
2. **Thinking/preference, explicit silence: passed.** Suppressed intent was saved,
   included in terminal context and acknowledged by the provider. No completion
   announcement was recorded. Append delay was 1.176 seconds. This establishes
   observed compliance, not the model's private reason for silence.
3. **Thinking/preference, changed notification to silence: failed interruption.**
   Requested intent at revision 1 was changed through `set_task_update_policy` to
   suppressed at revision 2. The completion append carried revision 2, and no
   completion announcement was recorded. The failure concerned an admission
   acknowledgment, “I'll get that set up, I'll…”, overlapping the caller's
   correction. It did not concern speaking the completed outcome against silence.
   A separate signal-energy check confirms overlapping caller/assistant audio;
   it does not classify words or replace the preserved ASR/semantic judgment.
4. **Preference routing, explicit notification: failed preference persistence and
   notification.** Admission happened early and saved discretionary intent from
   the initial creation request. Live subsequently heard the notification request
   and said “I'll let you know when it's finished,” but made no policy-update
   call. The completion therefore used thinking, not requested commentary. No
   completion outcome was spoken. Append delay was 1.352 seconds and its receipt
   was observed. This run does not establish that requested commentary routing
   fails; the saved preference never selected that branch.
5. **Direct commentary diagnostic: passed.** Completion was spoken in 6.691
   seconds, with a 1.395-second append delay. This confirms functioning delivery
   in that case. Unconditional speech remains diagnostic-only and cannot qualify.

The five new grades are three passed, two failed, and zero inconclusive. Across
the thirteen retained runs there are five passes, six failures and two
inconclusive diagnostics. These are screening observations, not reliability
estimates or final acceptance samples. No failed voice case was repeated.

All five new presentations remained pending at the observation boundary,
including the two whose complete outcomes were spoken. The model made five
admission calls and one policy-update call, but zero acknowledgment calls. There
were no premature handled transitions; successful delivery bookkeeping is still
unproven and the missing acknowledgment behavior remains an architectural issue.

## Local repair after screening

The first preflight opened no voice session. It rejected regenerated corpus files
whose hashes differed from the original cache. Adding the changed-preference
phrase shifted the renderer's alternating voice assignments by manifest position.
The corrected offline workflow verifies and reuses the original nineteen cached
command recordings, then retains the new diagnostic clip alongside them. Audio
files and checksums were frozen before the five live runs.

The routed failure also exposed a payload wording defect: saved discretionary
intent was expressed as “No completion announcement was requested.” That was
stronger than the application state justified. A stored preference can be stale
when the user finishes a sentence or Live makes a later promise.

The local correction describes what the backend recorded, explicitly allows
later user instructions and promises to supersede it, and asks Live to save the
correction. Its new regression test prevents claiming absence of a notification
request from discretionary state. **This correction is offline-only; it has no
live acceptance evidence.** Original recordings, prompts, profile/source hashes
and failed grades are preserved. The correction does not change the conclusion
or reset the diagnostic count.

Final local validation passes 126 backend/harness tests, the network-blocked Chrome
continuous-silence/playback/reconnect exercise, Android debug assembly and JVM
tests, and fresh migrated host startup with zero provider requests. Report replay
used saved transcription and semantic responses and opened no new voice session.

## Recorded evidence and usage

The live report and individual recordings are under
`data/announcement-evaluation/pilot-20261012-preference-02/`:

- `report.json`: all thirteen diagnostic cases and their original identities.
- `assessment.json`: five-run findings and separate new/cumulative usage counts.
- `aggregate-usage.json`: deduplicated provider sessions, backend responses and
  transcription/grading requests across both screening rounds.
- `voiced-overlap-diagnostic.json`: independent audio-energy observations for
  the changed-preference case, without altering its grade.
- Each case retains caller/assistant WAVs, word-timestamp transcription,
  semantic judgments, appends/receipts, tool calls, durable task/presentation
  state, playback progression, clocks, prompts and configuration/source hashes.

The live candidate source digest was
`e3dc97cbcb088d97c6f99ad27d2dd05520a386d6a4ce0245cec9ea8df04e609c`.
The frozen live corpus identity was
`230cee8f9bd25287e81cd0084136f9dcab4df67cc005b795fe614e66fd9139ea`.
Final corrected-source offline evidence is
`data/announcement-evaluation/offline-20261012-preference-04/`; it is distinct
from the live candidate's `offline-20261012-preference-03/` evidence.

New paid usage: five Jarvis voice sessions, finalized with 144 provider-reported
seconds; thirteen unique managed backend Responses requests, all priority; five
successful Whisper transcription requests; five successful blind-grader requests.
There were no authentication, billing, quota or HTTP failures in this follow-up.

Cumulative screening: thirteen finalized Jarvis sessions, 360 provider-reported
seconds, thirty-four unique managed backend requests, thirteen successful
transcriptions, and twenty-eight grader requests. The grader total includes the
one retained HTTP 400 from the initial harness repair. Monetary cost is unknown;
available token and voice usage remains saved.

Simulated-caller, real-worker and Android Live requests remain zero. All owned
voice sessions closed and evaluation processes stopped. Existing application
data and physical-device configuration were not reset.

## Decision and next architectural work

Do not adopt either candidate yet. The 36 scripted, six adaptive and six Android
acceptance runs were not started because neither candidate passed screening.
Occupied-conversation screening and the paired delivery-timing comparison were
also not reached. Keep existing listening-based delivery timing.

This evidence supports further work on Jarvis's application contract. It does
not establish a hard inability of GPT-Live to respond to asynchronous completion:
thinking produced the requested notification and respected silence when the
saved preference was accurate.

The next candidate should address three mechanisms rather than add another
general relevance paragraph:

1. **Preference reconciliation with guaranteed invocation.** Do not rely on Live
   spontaneously delegating a policy change after early admission or after its
   own promise. Reconcile the complete current user request and relevant spoken
   commitments with the admitted task through an application-owned step. Use
   structured model decisions with task association and cited source spans;
   validate revision/session authority in code. Track which conversation context
   the decision covers. An old discretionary decision is not evidence that a
   later explicit request is absent. Keep ordinary announcement relevance with
   Live, and keep explicit user preferences separate from progress/importance.
2. **Interruption and delivery observability.** Measure spoken overlap from client
   playback and actual caller audio, retaining ASR uncertainty. Evaluate whether
   existing playback controls can stop buffered substantive speech when the user
   interrupts. An unfinished or blocked announcement must remain uncommunicated;
   listening sounds and completed-outcome announcements need separate evidence.
3. **Application-owned communication bookkeeping.** Replace reliance on a
   spontaneous model acknowledgment with a correlated announcement lifecycle:
   context accepted, decision/defer/suppress, speech generated, interruption or
   playback completed, then communication recorded. Match the task outcome,
   session/generation and playback span. Suppression/deferment is a disposition,
   not proof that an outcome was heard. Preserve ownership, duplicate and reconnect
   checks. A model report can supply a claim, but cannot alone prove playback.

These are additional architectural changes, not validated runtime behavior.
Another paid screen needs a new explicitly bounded diagnostic phase after those
changes pass offline validation; the current thirteen-case phase is exhausted.

OpenAI's [Live prompting guidance](https://developers.openai.com/api/docs/guides/live-prompting)
supports separating conversational instructions from backend task procedures.
Its [delegation/update guidance](https://developers.openai.com/api/docs/guides/live-delegation#send-the-right-kind-of-update)
distinguishes thinking context, commentary intended for speech, and context
acknowledgment from speech/playback completion. Those distinctions remain the
basis for the proposed application contract.
