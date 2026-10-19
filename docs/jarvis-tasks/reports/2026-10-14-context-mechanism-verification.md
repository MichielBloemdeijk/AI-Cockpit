# GPT-Live overlap and notification-deadline verification — 14 October 2026

Both requested investigations are complete. Seven new browser/WebRTC sessions
ran in a separate eight-session diagnostic phase. Two clean idle notifications
finished within ten seconds. A clean speaking case waited for the caller but
finished its notification after about eleven seconds. The original overlapping
task announcement is real; the earlier inference about ignoring an already
received floor instruction was too broad. No production defaults changed and
M0–M11 acceptance remains closed.

## What the original overlap actually establishes

The original A/occupied recording and its frozen artifacts were checked without
another voice session or another transcription request. Three independent
observations agree:

- Recorded caller and assistant PCM contain approximately **1.800 seconds** of
  simultaneous above-threshold voiced signal during the outcome. There is still
  at least **1.270 seconds** after trimming word-span edges and shifting the audio
  alignment by up to 250 ms. This calculation uses 10 ms frames without joining
  pauses; it does not infer speech from Whisper words.
- OpenAI's own output events identify “Also, your Hello World page task is
  finished” at provider times **18,400–20,400 ms**. Input transcript events on
  that same provider clock overlap for **800 ms**, independently establishing
  that caller content reached the provider during the generated outcome.
- The outcome has continuous received-audio coverage and advancing production
  audio-element playback, with the track live, volume enabled and no pause/mute.
  Capture callbacks were approximately 43–57 ms from their recorded frame times,
  not seconds out of alignment.

The previously reported **2.160 seconds** was overlap of the *outcome word span*
with caller voiced intervals. It was not 2.160 seconds of simultaneously voiced
assistant and caller PCM. The distinction matters, but does not erase the event.

There is also an attribution limit: the caller's explicit “please let me finish”
arrived at the provider **after** this outcome. The fixture started with a new
conversation topic and placed its floor request later in the description. This
recording cannot prove that Live ignored that request before speaking. It can
prove overlapping generated task speech under the tested context and conversation.
The test sent thinking context and a delegation configuration refresh, without a
commentary append or an explicit speak instruction at completion.

This attributes the output to the GPT-Live service/conversation behavior. It does
not distinguish model weights from the provider's internal turn-taking logic, or
establish how often this occurs with human callers who adapt to interruptions.

A replayable audit and a stereo excerpt are retained at:

- [Signal audit](/C:/Users/Sander/Documents/Repos/AI-Cockpit/data/announcement-evaluation/original-occupied-signal-audit/audit.json)
- [Caller left / assistant right audio](/C:/Users/Sander/Documents/Repos/AI-Cockpit/data/announcement-evaluation/original-occupied-signal-audit/caller-left-assistant-right.wav)

## Repaired evaluation and experimental controls

The existing runner now has `--verify-mechanisms`; it retains `offline`, `pilot`
and request-free `report` stages. This is a new bounded phase, not a reset of
previous counters or a restart of failed acceptance. Every attempt remains in
the seven-run ledger. A failed condition stops its second repetition; no failed
or inconclusive voice recording was replaced.

The same `live_thinking_listen_first` configuration uses thinking-only terminal
facts, existing immediate context delivery, `gpt-live-1`/`marin`, and the unchanged
managed backend/tool/acknowledgment contract. No application relevance decision,
speech trigger, commentary, assistant transcript injection or automatic handled
transition was added. Admission is spoken; deterministic workers publish through
the normal durable task/result services.

New transport observations record live/enabled senders, outgoing/incoming RTP
packets and microphone-source energy. A network-blocked duplex WebRTC exercise
calibrates recorder timing separately from ASR; the final snapshot's measured
offset was **170.667 ms**, within the retained 250 ms tolerance.

Setup now requires three seconds of quiet after backend and acoustic activity,
within the existing twenty-second setup bound. This only controls fixture
completion timing. The clean speaking case delivers completion after the provider
has recognized “finished speaking” in the caller's instruction, while the caller
continues a description without competing notification preferences.

Local evidence repairs preserve cached provider responses, raw recordings and
original grades:

- Split “Mm”/“hmm” owners and empty ASR entries no longer become invented words.
- Conventional “M-kay”/“Okay” and “notebooks”/“notebook's” transcription spelling
  differences are normalized. Lexical fragments and differing substantive words
  remain unsupported; “st” is not silently converted to “steady.”
- Restored timestamps now flag words crossing silence removed for transcription.
  One control's “it” previously stretched across eleven seconds and could turn a
  backchannel into a false substantive interruption. An uncertain utterance end
  cannot establish an interruption or a qualified outcome deadline. Internal word
  boundary warnings are retained without moving a verified utterance's endpoint.
- Missing requested outcomes now fail the deadline dimension as well as selection;
  absence can no longer leave the deadline marked passed.

The same twelve previously passed semantic calibration fixtures and unchanged
semantic rubric were reused. The timing/normalization changes have independent
network-free tests. No paid recalibration or historical re-transcription occurred.

## New observations

1. **Occupied control, wording 1 — inconclusive full grade.** No completion was
   published; the worker remained queued. The recording contains **1.840 seconds**
   of simultaneous PCM during the desk turn, including listening responses and
   cut-off conversation. An unresolved “st”/“steady” fragment prevents semantic
   qualification. Raw transport evidence remains usable, not a semantic pass.
2. **Occupied completion, wording 1 — admission diagnostic.** The task was never
   admitted: Live asked whether updates were wanted and the fixed caller had no
   answer. No completion was published. This is not a reminder failure; the
   provider had recognized the goal. The driver's combined recognition/admission
   flag is false because there is no admitted task. The attempt remains counted.
3. **Requested idle, wording 1 — notification selection demonstrated.** The
   requested preference was saved correctly. The complete, accurate outcome was
   fully played at **5.036 seconds** from durable completion using the conservative
   acoustic endpoint; the word endpoint was **4.996 seconds**. Context transmission
   occurred at **0.487 seconds**, receipt at **1.507 seconds**. Unrelated admission
   speech/timing issues and missing delivery acknowledgment prevent whole-interaction
   qualification; they do not negate the verified outcome/deadline.
4. **Requested during speech, wording 1 — valid deadline failure, floor respected.**
   The provider received the floor instruction before completion. There was **zero
   simultaneous caller/assistant PCM during the description**, and zero outcome
   overlap. Live then spoke filler and a desk summary before the outcome. The
   complete, played outcome ended **11.004 seconds** after the caller opening
   (**10.964 seconds** at the word endpoint), beyond 10 seconds plus 250 ms tolerance.
   Context had arrived roughly eight seconds before that opening, so late delivery
   does not explain this delay.

   This case also exposed a separate persistence failure: admission delegation
   began approximately **1.314 seconds before the task request clip ended**, despite
   the listen-first instruction. The backend saved discretionary notification.
   Live subsequently promised notification but did not save a correction. The
   supplied context accurately represented that backend state, and instructed
   Live to consider its latest promises. This is an end-to-end deadline failure,
   not proof that a correctly persisted requested preference is intrinsically
   insufficient. The second speaking repetition was stopped, not retried.
5. **Occupied control, wording 2 — inconclusive full grade.** No completion was
   published; the task remained queued. Raw simultaneous PCM during the desk turn
   was **0.340 seconds**. “Cute” versus “queued” remains unresolved, and the packed
   “it” timing is flagged. It does not establish a long substantive interruption.
6. **Ordinary completion, wording 2 — inconclusive full grade.** Context was
   received with a discretionary preference; no task announcement was observed.
   Simultaneous PCM during the desk turn was **0.650 seconds**, associated with
   conversation/listening responses. “Cued”/“queued” and “makes”/“make” agreement
   remains unresolved. This does not earn a selection pass or prove intentional
   silence. The 0.310-second raw difference from the matched control is not a
   statistical or causal finding about completion-driven interruption.
7. **Requested idle, wording 2 — notification selection demonstrated.** Live
   corrected the preference through `set_task_update_policy`, resulting in requested
   revision 2. The accurate outcome was fully played at **3.189 seconds** after
   durable completion (**3.119 seconds** at the word endpoint). Transmission was
   at **0.506 seconds**, receipt at **1.607 seconds**. Unrelated initial conversation
   overlap and missing acknowledgment prevent whole-interaction qualification.

Final selection classifications: **two passed, one failed, four inconclusive**.
There is no complete-interaction-qualified candidate. The two idle successes are
diagnostic demonstrations, not a reliability estimate. Every original and revised
assessment is retained; the unresolved controls are not counted as passes.

## What to expect from context-only model decisions

Requested spontaneous speech within ten seconds is **achievable**, including
without another user prompt. Retaining a result quietly and answering later was
already demonstrated. The new clean speaking case demonstrates that Live can
wait for a previously received instruction to finish speaking. The ordinary case
also contains no unsolicited task outcome, although its full grade is inconclusive.

The current implementation still offers **best-effort conversational updates**,
not a qualified ten-second contract. We now have a valid observed deadline miss,
but it includes a preference-persistence defect. The evidence therefore supports
fixing early admission/full-request handoff, saving promises/corrections consistently
and prioritizing requested outcomes at openings before concluding a model limit.
These improvements can retain GPT-Live as the relevance/speech decision-maker.

The original overlap should be described narrowly: real overlapping task speech
in that conversation, not proof that Live cannot defer or ignores every floor
instruction. Matched controls also contain overlap, and their semantic uncertainty
prevents a qualified causal comparison of overall interruption frequency. No
claim of a hard GPT-Live limitation is justified.

Delivery bookkeeping remains independent: **zero `acknowledge_task_update` calls**,
zero premature handled transitions, and all four completed presentations still
pending. Three outcomes were verified as played. Even good announcement selection
does not qualify the present delivery acknowledgment contract.

Native task-correlated completion remains an untested model-led direction. This
phase did not implement it or establish that it will fix the observed problems.
No broader acceptance or production adoption is recommended on these samples.

The official [update-channel contract](https://developers.openai.com/api/docs/guides/live-delegation#send-the-right-kind-of-update)
describes thinking as quiet context usable in replies. Append acknowledgment waits
for estimated context injection, not completed speech/playback; it offers no
documented ten-second notification guarantee.

## Validation, provenance, usage and cleanup

The final frozen offline snapshot passed **181 tests**, network-blocked browser
continuous-silence/playback/duplex/mute/pause/reconnect checks, Android debug build
and JVM tests, and fresh migrated startup without provider requests. Repairs ran
locally before only unrun voice cells continued; no source changes occurred within
a voice run. Source history, per-recording identities, corpus, prompts, rubric and
raw hashes remain in the report. The runtime source digest is
`904aee3659e66b1bcd8444f4f883f1228cae0f49b5fac17962937c921a7e3a9f`.

New usage: **7 finalized voice sessions, 283 provider seconds, 16 managed backend
responses, 6 transcription requests and 6 grading requests**. No new paid request
failed. Six tasks were admitted; one importance change and one notification-policy
correction occurred. There were no simulated-caller, Android Live or real-worker
requests. Monetary cost remains unknown; returned usage is retained separately.

Cumulative usage, including the unchanged earlier 48 sessions: **55 finalized
voice sessions, 1,782 provider seconds, 132 managed backend responses, 54
transcriptions and 109 grading requests**. The one historical grading HTTP 400
remains included. Original earlier reports and evidence were not rewritten.

All owned sessions finalized and owned evaluation processes stopped. Report replay
makes zero provider requests. Existing application data, credentials and device
configuration were not reset. No manual user tests or physical speaker verification
were required.

Reproduction from the backend directory:

```powershell
uv run python -m app.announcement_eval.runner offline --verify-mechanisms --output ../data/announcement-evaluation/NEW-OFFLINE-DIRECTORY
uv run python -m app.announcement_eval.runner pilot --verify-mechanisms --live --offline ../data/announcement-evaluation/NEW-OFFLINE-DIRECTORY --prior ../data/announcement-evaluation/pilot-20261014-focused-01 --output ../data/announcement-evaluation/NEW-PILOT-DIRECTORY
uv run python -m app.announcement_eval.runner report --verify-mechanisms --output ../data/announcement-evaluation/pilot-20261014-verification-01
```

The first two commands are documentation, not an instruction to rerun completed
tests. The last replays saved audits without voice or grading requests.

- [Replayable report](/C:/Users/Sander/Documents/Repos/AI-Cockpit/data/announcement-evaluation/pilot-20261014-verification-01/report.json)
- [Frozen offline checks](/C:/Users/Sander/Documents/Repos/AI-Cockpit/data/announcement-evaluation/offline-20261014-verification-06/offline.json)
