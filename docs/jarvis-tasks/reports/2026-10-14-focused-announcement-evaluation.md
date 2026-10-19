# Focused GPT-Live context evaluation — 14 October 2026

The evaluator repairs and bounded A/B experiment are implemented. Twelve semantic
calibration fixtures passed, all 28 recent recordings were reassessed without
voice reruns, and seven new browser/WebRTC sessions ran. Both candidates hit
verified mandatory selection failures, so the remaining seventeen scheduled cells
were left unrun. No candidate qualifies for acceptance or production adoption.

The clearest new evidence is a spoken completion during explicitly requested
silence with configuration B, and a task-completion announcement over the caller
with configuration A. These have agreeing provider/audio transcripts, verified
client playback, durable task truth and context receipts. They are stronger
evidence than the earlier mislabeled admission acknowledgments. They do not
establish a hard limitation of GPT-Live or an overall failure rate.

## What was implemented

The existing `announcement_eval.runner` now accepts `--focused` for offline,
pilot and report stages. Its explicit manifest fixes two thinking-only profiles,
six conditions, two wording variants, alternate A/B ordering and a 24-session
ceiling. Configuration A preserves `live_thinking_listen_first`; B shares its
Live/backend prompts, tools and acknowledgment semantics, replacing only the
terminal append with concise factual JSON. Both use the existing isolated
immediate-context delivery mode. No application relevance chooser or automatic
speech/handled transition was added.

Version-two assessments separate evidence validity, context delivery, notification
selection, deadline, task truth, conversation interruption, preference persistence
and acknowledgment. They classify every ASR word into admission, progress,
promise, backchannel, outcome, conversation or ambiguity. Ordered transcript
alignment replaces whole-session token membership. A terminal claim needs actual
result words; “task added” and isolated “Done” cannot establish worker completion.

Recorded caller signal intervals distinguish voiced overlap from sentence pauses.
Every assessed utterance checks continuous received-audio coverage and actual
audio-element playback progression, including pause/mute/volume, track identity
and recording gaps. The production playback path is unchanged. This is evidence
of client playback, not physical speaker acoustics.

New ASR requests pack voiced regions with padding into one recording and restore
word timestamps through a saved mapping to the original timeline. This removes
long silent tails without moving the completion/deadline clocks. Common spellings
of the same nonlexical listening response are normalized in ordered alignment;
an additional sound absent from the provider transcript remains unsupported.

Missing acknowledgment prevents complete-interaction qualification even when
selection succeeds. Premature handling remains a blocker. No evaluation code
marks presentations handled.

## Recorded cases and stopping decisions

1. **A, requested idle, wording 1 — confounded.** Completion context arrived, but
   delayed admission speech began after the setup's settled marker. Jarvis spoke
   admission and a notification promise, not a completed outcome. The original
   automated failure is retained; this is now an inconclusive setup diagnostic.
2. **B, requested idle, wording 1 — confounded.** The same admission handoff race
   occurred. ASR also inserted “Hmm” absent from the provider transcript. This
   remains inconclusive and counted, with no replacement session.
3. **A, initial silence, wording 1 — selection passed.** No outcome announcement
   during the sixteen-second observation window. After a direct status question,
   Jarvis accurately answered that the page was ready in **3.870 seconds**.
   The silence preference was saved correctly. Missing acknowledgment prevented
   complete-interaction qualification.
4. **B, initial silence, wording 1 — verified selection failure.** Jarvis said
   “Staying quiet,” then announced “the Hello World page is ready” during the
   observation window, before the status question. It later answered the question
   correctly. The backend had failed to persist the silence preference, recording
   discretionary instead; that is a separate failed dimension. B's remaining
   cells stopped. This one sample does not prove concise JSON caused the failure.
5. **A, requested update during speech, wording 1 — script diagnostic.** The
   recording contains no completed-outcome announcement. However, its caller
   script ends with “The page can wait while we discuss this,” competing with
   the earlier notification request. It cannot establish a clean mandatory
   deadline failure. A provider “Mhm”/ASR “Hmm” spelling discrepancy was repaired
   locally. The original judgment and missing-outcome observation remain; the
   case is inconclusive and was not repeated. Wording 2 avoids that conflict.
6. **A, changed silence, wording 1 — selection passed.** Jarvis saved the changed
   preference, stayed quiet after completion and answered the later status
   question accurately in **3.742 seconds**. An earlier conversation interruption
   and missing acknowledgment prevented complete-interaction qualification.
7. **A, occupied conversation, wording 1 — verified selection failure.** Jarvis
   announced “Also your Hello World page task is finished” while the caller was
   describing the desk. The outcome span overlaps actual caller signal for
   approximately **2.160 seconds**. The result itself was accurate, and the
   recorded preference was discretionary. A's remaining cells stopped.

Final selection classifications: **two passed, two failed, three inconclusive**.
Complete-interaction classifications: **four failed, three inconclusive**.
These are case classifications, not a notification reliability estimate.

There were no matched no-completion controls and no second-repetition sessions:
mandatory failures stopped the profiles first. Consequently, this phase cannot
quantify how completion changes general interruption frequency. It does directly
establish that the cited task announcement overlapped the caller. The full
unambiguous idle and requested-during-speech comparisons remain incomplete.

## Local repairs and preserved evidence

The first pilot pause exposed a fixture race: backend completion preceded queued
Live admission speech, while the original settling guard checked only current
audio silence. The guard now resets for backend activity as well and retains a
twenty-second setup timeout. Post-hoc checks retire resumed admission around
completion as an infrastructure confound. Later status answers are excluded
from this setup check.

The second pause exposed the nonlexical transcription spelling difference and
the conflicting first speaking script. Both were investigated locally. Only
unrun cells resumed after passing offline checks. The three diagnostics retained
their slots; no recording was retried or replaced. Each continuation records its
source identity while preserving profile prompts/payloads, corpus bytes, rubric,
original assessments, usage and the 24-session ceiling.

The prior 41 sessions and their original grades remain untouched. Revised
historical assessments live in the new phase's `historical/` directory. The
corrected grader identifies the previously mislabeled “task has been added” as
admission and the isolated “Done” as ambiguity. Earlier playback traces lack the
new utterance-level fields, so they cannot retrospectively qualify. Unsupported
ASR tails remain visible rather than being converted into passes.

## Validation, provenance and usage

The final pre-voice offline snapshot passed **170 tests**, network-blocked Chrome
continuous-silence/recording/mute/pause/reconnect checks, Android debug assembly
and JVM tests, and fresh migrated startup without provider requests. A later
report-only cumulative-accounting repair passed all **30 focused tests**. It
changed neither runtime profiles nor recorded voice evidence.

The semantic rubric remained
`dade1f0a3548074d33ac5edac622cd32dbbe9308297fdf2f7e46e904adb046e4`.
The final runtime source digest is
`786c1d4dc0541f3aa9c00aafa1ce72d46c9880e3250bb89b9075becd8d2f4e52`.
The report records its separate report-generation source identity. Source,
configuration, corpus and actual session/task identities are retained per run.

New usage: **7 finalized voice sessions, 294 provider-reported seconds, 16 managed
backend responses, 7 transcription requests and 47 grading requests**. Grading
comprises 12 calibration, 28 historical reassessment and 7 new-case requests.
Calibration and historical grading were reused during continuations. There were
no new HTTP/authentication/billing/quota failures. Seven task admissions and one
preference-update tool call occurred; **zero acknowledgment calls** and **zero
handled presentations** were observed.

Cumulative retained usage: **48 finalized voice sessions, 1,499 seconds, 116
managed backend responses, 48 transcription requests and 103 grading requests**.
This includes the previously recorded grading HTTP 400. The audit reconstructed
earlier usage from case records, persisted ledgers and embedded historical usage;
combining only top-level report fields would have omitted eight older sessions.
All request/session identifiers are retained; monetary cost remains unknown.

Simulated-caller, Android Live confirmation and real-worker requests were zero.
Report replay opened no voice session and made no provider request. All owned
evaluation processes stopped, and every new voice session finalized. Existing
application data, credentials and device configuration were not reset.

## Assessment and next direction

Configuration A remains the more useful experimental starting point: it
demonstrated both forms of silence compliance and accurate later access to the
quiet result. Configuration B is rejected for this screen because of its verified
silence violation. Neither qualifies; the experiment does not establish a general
format advantage or reliable deadline-bound model selection.

The strongest remaining blocker demonstrated here is **task-outcome turn-taking**:
with context delivered and a correct discretionary preference, A spoke a routine
completion over the caller. Separately, model delegation did not record any
completed delivery. More channel combinations are unlikely to resolve these
specific seams by themselves; they were already screened earlier.

The remaining GPT-Live-led investigation is native task-correlated completion
delivery, preserving immediate durable admission and letting Live decide
relevance. It has not been tested here and is not guaranteed to improve behavior.
Before another paid round, explicitly settle the intended caller floor/deferral
contract and isolate that delivery seam with local tests and a small pilot.
Do not adopt an application relevance chooser, unconditional speech, or broaden
to acceptance on this evidence. Production defaults and M0–M11 remain unchanged.

OpenAI's [update-channel contract](https://developers.openai.com/api/docs/guides/live-delegation#send-the-right-kind-of-update)
describes thinking as quiet context, commentary as speakable content and
instructions as behavioral guidance. It does not promise that a context receipt
establishes a spoken or heard outcome. This phase demonstrates specific failures
of the tested configurations, not a hard inability of GPT-Live.

Replayable evidence:
`data/announcement-evaluation/pilot-20261014-focused-01/report.json`.
Its `results-summary.json` contains the per-case findings and usage summary;
`unrun_cells` in the report lists all seventeen unrun cells and stopping reasons.
