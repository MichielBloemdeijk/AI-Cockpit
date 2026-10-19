# Production configuration A adoption

Configuration A is now the **provisional development default** for new Live
sessions. GPT-Live owns relevance, timing and acknowledgment. The production
implementation does not import the evaluation package. M0–M11 and all 55 earlier
voice sessions remain closed and unchanged. This is not full interaction
qualification.

## Production integration

`live_task_updates.py` owns the listen-first, relevance, preference and post-speech
policy and the original A preference-aware terminal payload. Completed, failed,
interrupted and cancelled facts use thinking only, irrespective of importance.
Reminder, clarification and progress routing retain their existing behavior.

Admission, workflow and update-policy tools accept a model-authored notification
intent and reason. Preferences live in `TaskEvent`, with monotonic revisions,
operation provenance, atomic writes and conflict-checked replay. Silent policy is
separate from progress frequency. Suppression retains terminal context for status
questions and retires unsent away notifications. No application-data reset or new
preference table was required.

Quiet terminal context can reach a connected, ready, wake-verified session on any
conversational floor. Transport receipts release the lease, preserve the actual
floor and never mark attention handled. Changed preferences refresh pending facts;
unchanged facts are deduplicated. Model acknowledgment still requires the current
session exposure token. Missing acknowledgment remains pending.

The concurrent baseline exposed an application defect: a failed result was hidden
by the orchestration gate while an automatic repair task was created. Live could
not announce facts it never received. The fix makes verified terminal facts
available to Live independently of worker-action decisions. Questions, worker
actions and away notifications keep their existing gate. An offline test proves
that acknowledgment changes communication state without changing task state or
authorizing a worker action. This repair has not yet received a qualifying live
concurrent run.

## Validation and evidence

The full network-blocked gate passed **263 tests**, browser continuous audio and
duplex checks (181.3 ms measured duplex timing), and Android build/unit checks.
Two prompt-only freezes each passed **52 targeted tests**, inheriting unchanged
service, browser and Android evidence from that full gate. Saved positive and
negative evidence was replayed without provider requests. Earlier failed local
gates remain recorded.

The browser runner exercises production prompts, tools, persistence, routing,
microphone/WebRTC and playback. It retains only isolated databases, deterministic
workers and instrumentation. Completion comes through normal worker/result
services. All attempts retain source, configuration, prompt and corpus identities.
No admission transcript or model tool decision is injected.

After the user's review preference, new cases used direct primary-agent review of
exact provider responses, durable task facts, tool calls, independently transcribed
audio, PCM timing and client playback. These reviews are explicitly separate from
the earlier blind semantic grader. Original automated assessments remain visible.
No reviewer, transcript process or append receipt marks an update handled.

## Baseline results

1. Requested completion while idle: correct fully played outcome in **2.82 s**.
2. Initial silence: no unsolicited outcome during the observation window; accurate
   later status answer in **2.24 s** from the actual question endpoint.
3. Changed preference to silence: requested → suppressed revision 2 persisted;
   no unsolicited outcome; later accurate status answer in **2.11 s**.
4. Requested completion during speech: provider received the floor instruction
   before completion. Correct outcome after the caller finished, in **7.98 s**
   from the acoustic opening. No outcome overlap in PCM or native word timing.
5. Ordinary occupied completion: discretionary preference saved; no unsolicited
   outcome. Admission/greeting alignment uncertainty prevents full conversation
   qualification.
6. Matched pending-task control: no false completion. A greeting transcription
   discrepancy leaves conversation quality uncertain.
7. Concurrent completion/failure: first outcome accurate, once, in **3.13 s**.
   Failed outcome context was never delivered because of the application defect
   described above. Duplicate publication did not repeat the first announcement.
8. Interruption/reconnect: fixture checked caller activity instead of received
   assistant audio, producing a setup failure before interruption/reconnect. The
   original recording is retained and counted. The fixture was repaired locally;
   no replacement session was run.

Every verified spoken baseline outcome remained pending: **Live did not delegate
acknowledgment**. These results demonstrate useful notification selection in
screening, not a fully qualified interaction or a reliability guarantee.

## Bounded tuning

The first variant clarified permission to delegate solely for post-speech
acknowledgment, without a new user request. Its first idle test spoke the correct
outcome in **4.18 s**, but still made no acknowledgment. It admitted before the
notification sentence, saved discretionary intent, then promised notification
without recording the correction. The remaining unchanged acknowledgment cases
were stopped; their unrun cells remain visible.

The final variant changed only the preference block: explicitly delegate a policy
correction if admission began before the rest of the spoken request, before
promising notification or silence. Its idle outcome was fully played in **3.46 s**,
but durable intent remained discretionary and acknowledgment was missing. The
backend initially rejected a missing/invalid reason and conflicting replay
arguments before successful admission. This is recorded as a preference/tool
behavior failure, separately from the correct completion speech. Admission ASR
timing/alignment uncertainty remains visible.

The final variant's remaining cases showed:

- Initial silence: later correction saved suppressed revision 2; no unsolicited
  outcome; accurate status answer fully played in **2.22 s**.
- Changed silence: requested → suppressed revision 2; no unsolicited outcome;
  accurate later status answer in **5.09 s**. Admission/clarification overlap and
  some uncertain backchannel transcription remain separate conversation issues.
- Requested completion during speech: requested revision 2 persisted. Correct
  outcome was fully played **13.12 s** after the actual caller opening, beyond the
  ten-second target and 250 ms tolerance. Outcome speech did not overlap the caller.
  The substantive “I'll hold off and just listen…” response did overlap continuing
  speech, then a longer desk-layout reply preceded the terminal update.

The last deadline failure is supported by continuous PCM, client playback,
independent transcription and native provider timing. Native word timing also
places the outcome end **10.8 s** after the final caller word. Durable completion
was at `1792009923816.828`; transmission at `1792009924490.855` (674 ms application
queueing); provider receipt at `1792009925410.890`, before the caller's final opening.
Late application context therefore does not explain this miss. The held-floor
instruction was recognized before controlled completion. The longer conversational
reply explains much of the delay; this is an observed behavior, not proof of a
hard model limitation. A matched control for this final prompt was not run, so
the conversational overlap cannot be attributed specifically to the completion.

No verified spoken outcome in either tuning batch received model acknowledgment.
Neither variant qualified. The final preference and acknowledgment wording remains
part of A's **provisional** development policy; it is not presented as a proven
improvement. No larger acceptance or Android voice confirmation was run. The
three remaining first-tuning cells were stopped, not replaced. Both tuning batches
are used; the three unused browser slots do not authorize a third tuning batch.

## Usage and cleanup

New evidence consists of **13 browser sessions**, including the retained setup
failure: **579 Live seconds**, **39 managed backend responses**, **12 transcription
requests** and **6 semantic grading requests**. Six transcribed cases were
reviewed directly without new semantic-grading requests; the interruption fixture
failure was reviewed from logs without requesting transcription. There were no simulated
caller, real-worker or Android voice sessions in this phase. Backend usage recorded
201,156 input tokens and 2,031 output tokens across these responses; cached input
is included in input usage. Background orchestration decisions and metrics remain
separately recorded in the machine ledger.

The cumulative ledger contains **68 voice sessions**, **2,361 Live seconds**,
**171 managed backend responses**, **66 transcriptions** and **115 semantic grading
requests**, including the retained historical failed request. Cumulative backend
usage is 788,532 input and 9,154 output tokens. Monetary cost remains unknown.

All thirteen provider sessions finalized and all isolated databases show
disconnected sessions. No owned test backend/frontend listening ports or owned
driver processes remained. Replay recomputed cached assessments and direct-review
dimensions with **zero provider requests**, preserving the original reviews and
grades.

## Decision

Keep A as the provisional development default. Its context routing, durable
preferences and transport/acknowledgment separation are implemented. Screening
shows useful completion and silence behavior, including a successful occupied
case, but reliability is not established. Remaining blockers are inconsistent
model-authored preference correction, absent post-speech acknowledgment, and
occasionally late or intrusive conversational speech. Interruption/reconnect and
the repaired concurrent-failure delivery still need qualifying live evidence.

The next evaluation should isolate post-speech backend delegation and a brief
outcome before longer conversational replies. The current bounded phase is closed;
additional tuning needs a new explicit budget. No application-controlled speech
selection or automatic acknowledgment was introduced.

## Evidence locations and practical limits

- New phase: `data/announcement-evaluation/pilot-20261014-adoption-01/report.json`.
- Full local gate: `data/announcement-evaluation/offline-20261014-adoption-08`.
- Prompt freezes: `offline-20261014-adoption-09` and `offline-20261014-adoption-10`.
- Each case contains separate caller/assistant WAVs, provider and application
  events, task state, playback/RTC evidence, transcripts, assessment and any direct
  `agent-review.json`.

Latency ends at the fully played outcome, from durable completion when idle or the
actual caller opening while occupied, with the stated 250 ms measurement tolerance.
Application queueing is retained; append receipt does not restart the deadline.
This establishes client playback evidence, not physical speaker verification.
Monetary cost is unknown where the provider supplies no cost. These small samples
do not establish a hard limitation of GPT-Live.
