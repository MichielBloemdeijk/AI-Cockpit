# Task notification and bookkeeper repair checkpoint — 16 October 2026

Work is stopped at the user's requested intermediary checkpoint. No paid test,
owned server or test voice session remains active. The new mode remains off in
normal development; provisional configuration A remains the default. No application
data reset, normal-session restart or commit was performed.

## Implemented and checked

- Explicit completion notification intent/reason are required in enabled voice
  admission/update tools. Instructions distinguish a saved subscription from a
  conversational promise and keep completion intent separate from progress policy.
- Admission receipts pass native user fragments arriving after delegation back to
  the admission model. The model must apply corrections through ordinary tools;
  application code and the bookkeeper do not infer or rewrite preferences.
- Observer completion citations are limited to post-exposure assistant fragments.
  Earlier admission and future promises cannot establish delivery. The policy now
  requests minimal outcome clauses, excluding preceding backchannels and preambles.
- Observer batching starts with relevant outcome speech, rather than the age of an
  earlier admission. No transcript gap is treated as proof of a completed turn.
- The isolated browser audio bridge now injects and records through AudioWorklet,
  with exact frame accounting, flushes, and explicit coverage/gap checks. It passed
  a 750 ms main-thread stall without lost frames. Production playback is unchanged.
- Evaluation file reads specify UTF-8; cumulative observer usage preserves prior
  phases. Original evidence and automatic grades remain intact.

Final frozen offline gate: `data/announcement-evaluation/offline-20261016-bookkeeping-02`.
It passed 269 regression checks plus 32 observer checks, 18 frontend tests, recording
checks, and Android debug build/unit checks. A separate 13 migration/recovery tests
and frontend type check also passed. The two revised fixed observer calibration
batches each passed all twelve labels, including richer real admission failures.

## Recorded results

Four browser sessions were run across two explicitly frozen repair candidates.

Candidate v2, retained in `pilot-20261016-bookkeeping-01`:

- Idle: requested commentary and verified completion in 2.63 seconds, with observer
  acknowledgment. Earlier admission/promise ASR disagreements remain inconclusive.
- Speaking: explicit notification remained discretionary. Delegation started at
  4.4 seconds; the notification clause arrived at 5.2–6.6 seconds. A substantive
  announcement preamble overlapped caller audio for approximately 1.84 seconds.
  Observer correctly remained pending after its unnecessarily broad citation
  crossed a preceding backchannel gap. Recording was continuous.

Candidate v3, retained in `pilot-20261016-bookkeeping-02`:

- Idle: a real early-delegation case initially saved discretionary. After receiving
  later user context, the admission model called the normal update-policy tool and
  saved requested revision 2. Completion was fully played in 3.65 seconds and
  acknowledged independently. All assessed dimensions passed; one observer request.
- Speaking: the complete verified outcome was played about 6.42 seconds after the
  next acoustic opening, with no measured outcome overlap, and acknowledged using
  a precise citation. However, the admission receipt included only **Tell me**;
  **when it has finished** arrived after it returned. Discretionary remained saved,
  so the intended requested-commentary configuration was not exercised. Admission
  spelling and an earlier question's stretched ASR timing remain inconclusive.
  This case is not qualified; no unchanged repetitions followed.

These are diagnostic results, not a reliability guarantee. They do not establish a
hard GPT-Live limitation. Requested preference capture is still the immediate
application/model integration blocker. Silence, concurrent outcomes, reconnect,
between-session delivery, observer restart, and Android live confirmation remain
unrun for the latest candidate.

## Next repair — planned, not yet implemented

Move request-context freshness ahead of admission rather than relying only on a
post-admission receipt. Keep the Live receive loop free while collating arriving
user fragments. If context advanced since delegation, return the newer context to
the admission model for reconsideration **before** creating the task. A bounded
quiet interval is an input batching signal, never a claimed completed turn and
never a speech trigger. Preserve admission idempotency, generation authority,
normal tool ownership and cancellation/recovery. Do not add a notification phrase
parser, automatic preference repair, forced speech, or timed announcement retries.

Validate early delegation plus late notification/correction fixtures locally,
including partial clauses and continued speech. Freeze a new candidate and reuse
unchanged observer calibration by explicit model/prompt/fixture identity. Start
with the failing spoken request; preserve all attempts and the original session
ceiling instead of resetting counters. Reassess the remaining live matrix against
the available connection budget before further paid execution.

## Usage and cleanup

This checkpoint added four voice sessions (151 reported seconds), nine managed
backend requests, 24 observer calibration requests, eight runtime observer requests,
and four Whisper transcriptions. No new separate semantic grader, simulated caller,
real worker or Android voice requests ran. Monetary cost is unknown.

Cumulative retained evidence: 83 voice sessions, 2,924 reported seconds, 204 managed
backend requests, 80 transcriptions, 115 historical semantic grading requests, and
50 observer requests (36 calibration plus 14 runtime).

`pilot-20261016-bookkeeping-02/checkpoint-cleanup.json` records disconnected state
for all four owned sessions and all eight owned server ports closed. No recording
gaps were ignored, failed cases replaced, or uncertain utterances silently repaired.
