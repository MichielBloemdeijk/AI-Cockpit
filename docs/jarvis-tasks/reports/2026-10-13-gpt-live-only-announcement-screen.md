# GPT-Live-only task-announcement configuration screen

The additional model-only screen is complete for the defined configurations in
the current architecture. Fourteen candidate profiles and an acknowledgment
diagnostic were exercised in 28 fresh browser/WebRTC sessions. No candidate
qualified for the full acceptance gate. Reminder decisions remained with
GPT-Live; no application relevance chooser, automatic speech fallback, preference
reconciler or automatic handled transition was introduced. Production defaults
remain unchanged and M0–M11 remains closed.

This is a finite configuration screen, not proof that every possible prompt or
different backend architecture has been ruled out. In particular, unsuccessful
screening does not establish that GPT-Live cannot respond to asynchronous results:
several recorded runs did exactly that. No model/voice sweep was performed.

**Audit correction, 13 October:** the reassessment at the end of this report
supersedes the original characterization of the complete-request-first occupied
case as a partial completion announcement. The grader confused task admission
with task completion. Raw recordings, judgments and grades remain unchanged.

## Scope and reproducibility

The earlier thirteen-run phase is retained unchanged. This phase had a separate
44-session ceiling; all 28 runs, including inconclusive cases and diagnostics,
remain within it. Continuations preserve original source, prompt, configuration,
corpus and session/task identities. Failed or inconclusive voice cases were not
repeated unchanged. Report replays used saved transcription and grading responses.

All runs use `gpt-live-1`, voice `marin`, managed `gpt-5.6-luna` at low reasoning,
priority tier and disabled parallel tool calls. Cached English audio is frozen
and streamed through the real microphone/WebRTC path, including continuous
silence. Actual gateway admission, worker fencing, result publication, task
presentation, provider adaptation and client playback run in isolated migrated
databases. The deterministic worker publishes results through normal services;
it does not inject assistant transcripts or model tool decisions.

The final replayable report is
`data/announcement-evaluation/pilot-20261012-model-only-06/report.json`.
`assessment.json` records every case, both provider transcripts, task/session
identities, separate acknowledgment observations and usage.
`voiced-overlap-diagnostic.json` checks overlapping captured audio independently
of ASR timestamps. The reusable analysis script is
`data/announcement-evaluation/assess_model_only.py` and opens no provider session.

Final source digest:
`6392885d964bd01db1d7ec39905114201a4c298b71e4e1b0bcb8e1579350089a`.
Final passing offline evidence is
`data/announcement-evaluation/offline-20261012-model-only-07/`.
The current source/configuration still matches that evidence. The retained
thirteen-run report hash remains
`954c49d7a5d1e372035a2c71a6da561c9d0d8e6bf15484fa173a4456565360a2`.

## Configurations and observed results

Every fixed append sequence was exercised regardless of saved intent. The model
still had the latest conversation, its own promises, the relevance policy and
verified outcome facts. A stored discretionary preference was explicitly
described as potentially stale, never as proof that no notification was asked for.

- **Thinking only, corrected preference context:** requested notification passed.
  The silence case produced no completed-outcome announcement, but preference
  persistence failed and independent speech evidence disagreed.
- **Commentary only, corrected preference context:** requested completion was
  spoken, with a preference-persistence failure. Its listening-mode silence case
  never received terminal context and remains inconclusive. The immediate-mode
  diagnostic did receive it and announced the outcome against requested silence.
- **Thinking then instructions:** the requested outcome was not spoken despite
  receipts for both appends.
- **Commentary then instructions:** the requested outcome was not spoken; late
  preference persistence and an admission interruption also failed.
- **Instructions then commentary:** the requested outcome was not spoken.
- **Instructions then thinking:** the requested outcome was not spoken.
- **Outcome and policy in one instruction:** the requested outcome was not spoken.
- **Thinking plus commentary:** requested completion was spoken, but its silence
  case also announced completion. Both had incorrect saved preferences.
- **Thinking plus commentary, then instructions:** requested completion was spoken;
  explicit silence and changed silence produced no completed-outcome announcement.
  Preference/admission checks failed, and the occupied case announced completion
  while the caller was speaking.
- **Thinking receipt, then instructions:** both deliveries were observed, but no
  requested outcome was spoken. Waiting for context injection did not establish
  speech or communication.
- **Thinking plus the original shorter reconsideration instruction:** completion
  was spoken in 6.450 seconds, with a preference-persistence failure. Its silence
  case remains inconclusive because ASR introduced unsupported words.
- **Direct conditional instruction containing the outcome:** requested completion
  was not communicated after durable completion. See the grading caveat below;
  a secondary truthfulness flag is not reliable evidence of a task-failure claim.
- **Thinking with complete-request-first delegation:** requested completion passed
  in 3.783 seconds. Both initial silence and changed silence were respected and
  saved correctly; admission interruptions still failed. In the occupied case,
  the grader mislabeled a task-admission acknowledgment as a partial completion
  announcement. Neither transcript contains a completion announcement in that
  case; admission and conversational overlap need separate assessment.
- **Thinking with stronger description priority and no acknowledgment of internal
  appends:** the occupied case retained the task outcome quietly, but an unrelated
  desk reply interrupted the caller. The explicit idle case subsequently missed
  the requested notification despite delivered context.

The final acknowledgment diagnostic added an explicit backend capability and
delegation exception for `acknowledge_task_update` after fully spoken delivery.
It used requested commentary only for this diagnostic and cannot become a winner.
Completion was spoken in 3.963 seconds, but no acknowledgment call occurred and
the presentation remained pending.

## Delivery timing and interruption findings

One important application limitation was identified independently of model
selection. The existing coordinator may remain in `thinking` after backend work
ends without subsequent spoken output. Its listening-only gate then withholds
terminal context. Silence in that state cannot test the model's announcement
choice. The original undelivered case is retained as inconclusive.

An isolated immediate-context mode permits task facts to pass during thinking,
user speech or assistant speech. Connection, wake verification, generation and
duplicate checks still run, and the durable floor is not rewritten. This changes
context availability, not the decision to speak.

The occupied immediate-context diagnostic used the same complete-request-first
profile as its retained listening-mode case. Append delay was 580 ms rather than
1,139 ms. No task outcome was announced in the immediate case, but its desk reply
still interrupted the caller. Thus immediate delivery did not satisfy the full
interruption gate. This single diagnostic is not the planned two-pair timing
qualification, and it does not establish that one timing mode is generally better.

Audio-energy checks corroborate actual caller/assistant overlap in the occupied
cases. The three-channel announcement span alone contains about 2.76 seconds of
overlapping signals. The strict-floor case has about 0.64 seconds in its cited
non-task reply span. Energy checks identify signals, not word identity, relevance,
private model intent or physical speaker acoustics; original grades are preserved.

General conversation interruptions, admission interruptions and task-announcement
interruptions are separate findings. The first two may permit further reminder
characterization, but remain failed checks and cannot qualify a configuration.
An incomplete task announcement remains uncommunicated, but admission must not
be classified as a task-outcome announcement merely because the task finishes
while the admission acknowledgment is playing.

## Evidence and grading limitations

The final automated status counts are **3 passed, 23 failed and 2 inconclusive**.
These are whole-case statuses, not 23 missed notifications or a reliability
estimate. Several failures were only preference persistence or admission/general
conversation interruptions. The acknowledgment diagnostic's pass concerns speech;
it does not imply successful bookkeeping. Some failed cases also retain additional
evidence uncertainties.

Whisper added words absent from provider transcripts in several quiet recordings,
including “Thank you” and “All right.” Negative semantic penalties now need
provider-transcript support in addition to cited ASR words. Unsupported penalties
remain inconclusive rather than becoming passes. Original transcripts, judgments
and grades are retained; this repair did not resample voice sessions.

One conditional-instruction judgment labeled a solitary pre-completion “Done” as
the task outcome `failed`. That is an unsupported interpretation: a grading
failure is not a task execution failure, and a short admission acknowledgment is
ambiguous. The associated automated truthfulness flag remains in the raw evidence,
but should not be used to claim GPT-Live reported a worker failure or lied about
completion. The separate absence of a post-completion notification still rejects
that case. Further grader calibration should address this distinction.

Across all 28 new runs, every presentation remained pending. There were 28 task
admission calls, five policy-update calls and **zero acknowledgment calls**.
No premature handled transition occurred. Context acceptance, complete speech,
client playback and durable communication state remain distinct observations.

## Local validation, usage and cleanup

Final validation passed **142 backend/harness tests**, the network-blocked Chrome
continuous-silence/playback/reconnect exercise, Android debug assembly and JVM
tests, and fresh migrated startup with zero provider requests. A preliminary
offline full-suite invocation timed out at 180 seconds; its failure is retained.
The enlarged suite subsequently passed with a 360-second local guard. Voice
deadlines and the paid case ceiling were unchanged.

New paid usage: 28 finalized Jarvis sessions, **845 provider-reported seconds**;
66 unique managed backend responses; 28 successful Whisper transcription requests;
28 successful blind grading requests. There were no authentication, billing,
quota or transcription/grading HTTP failures in this phase. Available token usage
is retained; monetary cost is unknown.

Including the original thirteen-run phase: **41 finalized voice sessions**, 1,205
provider-reported seconds, 100 unique managed backend responses, 41 transcription
requests and 56 grader requests. The grader count includes the one previously
retained initial HTTP 400. Cumulative counts are not new requests on report replay.

Simulated-caller, Android Live confirmation and real-worker requests remain zero.
No candidate qualified for the 36 scripted, six adaptive and six Android final
acceptance runs. No manual user test was required. All owned voice sessions and
evaluation hosts closed; existing application data and device configuration were
not reset.

## What the evidence supports next

Keep the model-controlled approach as an experimental direction. Thinking plus
complete-request-first delegation gave the clearest combination of timely idle
notification and respected silence; it is not accepted for production. Adding
channels or a stronger instruction did not consistently improve behavior. None
of the tested configurations establishes all required notification, preference,
interruption and bookkeeping guarantees.

The evidence supports correcting factual context availability independently of
speech selection. It also supports treating general turn-taking and truthful
playback bookkeeping as separate engineering problems. Such mechanical ownership
does not require replacing Live's ordinary relevance judgment with hardcoded
importance rules.

No application-owned relevance chooser has been adopted. A different architecture
using native pending backend/tool completion is another model-led possibility,
not evaluated by this context-append screen. It would need to preserve immediate
durable admission, concurrent tasks, clarification, cancellation and reconnect;
simply holding a task-creation tool open until its worker finishes would not be a
coherent replacement for the existing background-task contract. This report does
not claim a hard inability of GPT-Live to announce asynchronous outcomes.

OpenAI's [update-channel documentation](https://developers.openai.com/api/docs/guides/live-delegation#send-the-right-kind-of-update)
distinguishes thinking context, commentary intended for speech and instructions
that can interrupt current behavior. Receipts estimate context injection, not
speech or playback completion. Its [playback-control guidance](https://developers.openai.com/api/docs/guides/voice-server-controls#check-speech-before-playback)
describes application ownership of audio/transcript/playback association and
interruption cleanup, and warns that a pause alone does not establish a complete
answer. Those are interface constraints, not proof that model relevance selection
is impossible.

## Reassessment of evidence and test validity — 13 October

This read-only evaluation inspected all 28 saved case transcripts and claimed
outcome spans, the delivery records, prompts, scripted audio driver, recording
implementation, deterministic grader and semantic judgments. It used no new
voice, transcription or grading requests. It did not independently listen to or
retranscribe the recordings. The original result counts remain historical
automated classifications, not an audited model failure rate.

### A material classification error in the strongest candidate

In `pilot-20261012-model-only-04/pilot-live_thinking_listen_first-occupied-1-listening-browser`,
the semantic grader labeled “your non urgent task to write a Hello World page
has been added” as `outcome=completed, complete=false`. Both the saved Whisper
transcript and provider transcript identify task admission. The provider
transcript contains no completed-outcome announcement. The cited ASR span starts
about two seconds before durable completion; its last word ends about 57 ms
after the terminal append was sent. The admission wording was already underway.
Consequently, this is not evidence that the terminal context provoked an
interrupted completion announcement. The earlier report's attribution was wrong.

The other cited overlap span consists of “Yeah, sure, let's talk. That could
work. Okay, go ahead. Makes sense.” Some of these may be conversational
backchannels; their acceptability needs a calibrated distinction between brief
listening responses and substantive takeovers. The desk-layout recommendation
starts after the scripted caller ends, according to the retained word timestamps.
Audio overlap is evidence of simultaneous signals, not automatically an unwanted
task notification or substantive interruption. The original failed grade is
preserved; this audit does not turn it into an acceptance pass.

For this same profile, the recorded requested update arrived in 3.783 seconds;
initial silence and changed silence produced no outcome announcement and the
preferences were saved correctly. Neither occupied run (listening or immediate
context delivery) contains a completion announcement. These examples support
continued investigation of this profile's selection behavior. They do not prove
reliable deferral, deliberate silence, or acceptable turn-taking across sessions.

### Test weaknesses that limit the conclusion

- **Outcome classification:** the admission/completion error above and the
  earlier `Done`/`failed` error show that the semantic grader needs additional
  negative fixtures and span review. Its output is not independent ground truth.
- **Interruption measurement:** scripted runs use the entire utterance interval,
  including pauses, as the caller window. The caller continues on its schedule
  even when Jarvis is still acknowledging admission. This is useful stress
  testing, but does not isolate completion-triggered speech from ordinary
  barge-in behavior. Energy-overlap analysis alone does not repair that attribution.
  Matched conversations without a completion event are missing.
- **Playback assurance:** `playback_verified` currently requires only one sample
  with an unpaused, live track and an advancing position. The recording taps the
  remote MediaStream, rather than the audio element's final rendered output.
  This supports reception and active playback, but does not establish playback
  of each claimed utterance. The flag also does not check mute/volume. An
  utterance-level playback gate is still needed for final acceptance.
- **Transcript agreement:** the deterministic comparison checks token membership
  against the whole provider transcript, not aligned spans. It can miss timing,
  repetition and meaning mismatches. Unclaimed ASR hallucinations can also escape
  validation: the successful listen-first idle recording's Whisper output has
  extra thanks/bye words absent from the provider transcript, without an unknown
  flag. Its actual completion phrase is corroborated, but the overall pass is
  not evidence that every transcribed word was verified.
- **Acknowledgment gate:** premature model-reported handling is rejected, but
  absent acknowledgment is merely reported. `recommendation()` uses case status
  and does not separately reject a fully spoken but still-pending presentation.
  Thus the current selection logic cannot enforce every promised acknowledgment
  acceptance condition. Zero acknowledgments remain an unresolved delivery-state
  issue, not proof that Live cannot choose a relevant announcement.
- **Causal separation and sample size:** most configurations have only one run
  per exercised condition and stop at a failure. The tests measure the whole
  Live/backend/application system, including saved-preference races and context
  delivery. They do not provide repeatability estimates or isolate which prompt
  clause caused a difference. Several cases stopped on evidence uncertainty.

These weaknesses do not erase the clear unwanted “page is ready” announcements
in silence cases or the delivered-context cases with no requested outcome.
They do prevent interpreting “no qualifying candidate” as “context guidance
cannot work.” A one-shot failure is sufficient to withhold acceptance, but not
to declare a mechanism incapable of improvement.

### Recommended direction

Retain thinking context plus complete-request-first delegation as the leading
GPT-Live-controlled candidate. Repair and calibrate the evaluator on saved
evidence first, preserving original grades and reporting separate dimensions:
notification selection, deadline, task truth, conversational interruption,
preference persistence and playback/acknowledgment. Repair context starvation
independently; supplying verified facts must not depend on an obsolete floor
state. None of this requires an application relevance chooser.

Then run a small, predefined diagnostic comparison of the same frozen candidate
with a genuine idle opening, explicit notification during user speech, requested
silence, and occupied conversation with a matched no-completion control. Include
a later status question after quiet completion to establish context usability;
even that does not prove intentional earlier silence. Distinguish timing-only
delivery changes from prompt changes, retain all results, and do not rerun until
green. This comparison has not been executed by this reassessment.

A concise factual append and simpler relevance prompt, with bookkeeping tested
separately, is another bounded hypothesis. The current payload still contains
policy reminders, repeated outcome text and opaque handles. No clean prompt
ablation has established whether those extra responsibilities interfere with
selection. Separating the experimental questions must not silently weaken final
acknowledgment requirements or change production semantics.

Native task-correlated client delegation or a separate pending completion tool
remains an untested architectural possibility. It is a larger change and is not
a documented guarantee of better relevance decisions. OpenAI's documented
thinking/commentary/instruction roles explain why adding all channels is not
necessarily better: thinking provides quiet facts, commentary encourages speech,
and instructions may interrupt behavior. The documentation does not promise a
deadline-bound announce/defer/silence decision from a context append.

Current position: **GPT-Live-controlled selection is still plausible and has
positive examples; reliable end-to-end delivery is unproven. The evidence does
not justify abandoning it for application-controlled reminder decisions yet.**
