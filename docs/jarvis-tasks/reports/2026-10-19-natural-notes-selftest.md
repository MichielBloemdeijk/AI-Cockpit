# Natural notes and agent self-testing: first iterations

**Status (19 October 2026, end of day):** Five candidate iterations. The latest (commit f27fe91) passes L0 and L1 (20/20). The final live verification round stopped after four browser scenarios because the OpenAI credit balance ran out (HTTP 429 `insufficient_quota`). The conversation observer stays off by default; adopting it is the next decision.

## What changed (summary)

- **Plan reset.** The Codex plan was closed. Decisions was removed. The one-off evaluation stages were replaced by `backend/app/selftest` (see [self-testing](../self-testing.md)).
- **Bookkeeper.** It now waits for the user's reaction and judges from meaning. It has a new `possibly_unheard` class, and validation is narrowed to identity and playback.
- **Jarvis notes.** Live gets one readable notes view: waiting results, promises, already told. Requested results get one spoken prompt, plus one re-offer if possibly unheard.
- **Promises.** They are tracked from the bookkeeper's findings, then marked kept or dropped.
- **Self-test layers.** L0 offline, L1 replay of recorded conversations with agreed truth, L2 scripted browser, L3 adaptive simulated user, L4 Android. Runs get an automated reviewer (with an independent second review for material verdicts) and a doctor that proposes fixes.

## Results by layer

| Layer | Candidate | Runs | Outcome |
|---|---|---|---|
| L1 replay | iteration 3 | 18 recordings, twice | 18/18 match agreed truth, both times |
| L2 browser | iteration 1 (exploratory, mixed source) | 11 scenarios, 13 connections | 1 fine, 4 awkward, 5 material, 1 harness error |
| L2 browser | iteration 2 (92991db) | 11 scenarios, 12 connections | 2 fine, 3 awkward, 5 material, 1 provider error |
| L3 adaptive | iteration 2 | 8 scenarios, 8 connections plus 8 simulated-user sessions | 2 fine, 3 awkward, 2 material, 1 harness error. The simulated user got what it wanted in 4 of 7 |
| L4 Android | iteration 2 | 1 scripted scenario (pilot) | Conversation correct (update named the result right after completion). Bookkeeping never confirmed delivery, and the recording renderer failed. Both are fixed in iteration 3, not yet re-run |

Note on L2 iteration 1: some scenarios already ran with the first fix, because each scenario starts a fresh backend.

**Fixed between iterations, then confirmed live in iteration 2:**
- Jarvis gently corrects a false premise.
- The switch to silence is respected, and Jarvis no longer says "your notes are updated".
- Ordinary updates come at natural moments.

**Fixed in iteration 3; verified offline only:**
- Updates waiting through comma pauses.
- Wake-word transcript lag.
- The admission livelock.
- Android playback evidence.
- A stable `possibly_unheard` verdict for "tell me after I finish".

The L3 "chat while waiting" run is a good example of the target behaviour. Jarvis gave advice and added "Oh, and your Hello World page is ready" at a natural point. The simulated user rated it 5/5.

## Findings outside natural updates (not fixed; need a decision)

1. **Background orchestration silently retries failed tasks.** In the two-task scenario the failed task was re-queued with a rewritten goal, and the user was not told.
2. **Task questions are not relayed reliably.** Jarvis did not voice the task's clarification question before the user answered.
3. **Reminders and questions are not tracked by the bookkeeper.** A delivered reminder stays "waiting" in the notes.
4. **Admission often speaks twice** ("it's queued… still queued"). The request-context refresh and status checks each trigger a spoken reply.
5. **The full backend test suite has many failures and a hanging test.** These predate this work: the same failures occur on the pre-Codex commit. The Jarvis suites used by L0 are green. The personal `.env` leak into tests is fixed.

## Harness lessons

- Whisper invents repeated phrases over silence. Only voiced audio is transcribed now, and the reviewer drops phrases without audio behind them.
- Synthetic test speech pauses at commas, which Live treats as turn ends. Scenario lines were made natural, because contrived lines invite odd replies.
- The automated reviewer matched the agreed human verdicts on calibration. It once confused admission with completion, so spot-checking the packs stays worthwhile.

## Usage

- **Voice:** 33 runs, 35 Jarvis connections, 8 simulated-user sessions.
- **Harness requests:** 38 transcriptions and 68 review/questionnaire requests.
- **L1 replay:** about 200 bookkeeping requests.
- **Monetary cost:** unknown.

## Next

Run iteration 3 live:
- L2 suite (≤16 connections)
- L3 suite (≤16)
- L4 Android scripted plus adaptive (≤12)

Then decide adoption: no confirmed material issue in natural updates. After that, the 15-minute manual phone check.


## Iterations 3 to 5 (later on 19 October)

| Layer | Candidate | Runs | Outcome |
|---|---|---|---|
| L2 browser | iteration 3 (7112b7a) | 11 scenarios, 13 connections | 4 fine, 5 awkward, 2 material |
| L3 adaptive | iteration 4 (63b4fc2) | 8 scenarios, 8 connections plus 8 simulated-user sessions | 3 fine, 4 awkward, 1 material. The simulated user was satisfied in 5 of 8 |
| L4 Android | iteration 4 plus harness fixes | 9 runs, 12 connections | Adaptive interruption fine (talked over, then re-offered). Streamed scripted false-premise case fine. Earlier Android verdicts were limited by harness bugs, now fixed |
| L1 replay | iteration 4 | 20 recordings | 20/20 |
| Final round | iteration 5 (f27fe91) | 4 browser scenarios before credits ran out | 2 fine, 1 awkward, 1 not reviewed |

### Remaining material findings, and what was done

- **Talked-over update (L2 iteration 3).** Jarvis yielded, answered, then re-offered: the target behaviour. A push still went out as the session ended, 0.6 s before the re-offer was recorded as heard. Pushes now wait about 15 s after a session ends (iteration 4).
- **Question and reminder (L2 iteration 3).** The delivered reminder stayed pending: Live no longer self-reports and the bookkeeper did not cover reminders. The bookkeeper now confirms reminders (iteration 4). The question reached the user about 13 s late, because background orchestration decides first. Pending questions now appear in the notes immediately (iteration 5).
- **Urgent failure (L3 iteration 4).** Jarvis waited 12 s, then cut in politely. A failure of an important task may now interrupt politely straight away (iteration 5).
- **Double reply at admission** ("it's queued… still queued"). The backend router made unprompted status lookups. It now replies once (iteration 4).
- **Admission livelock.** When the user kept talking, task creation failed every retry. The newer-speech refresh now happens once per request (iteration 3).
- **Android playback evidence.** Reports longer than 600 ms were ignored, so delivery was never confirmed. Now fixed, and confirmed on the emulator (handled).
- **Retries.** A failed task is retried by background orchestration, which you consider a feature. Retries must now keep the original goal and intent (orchestrator instructions), and Jarvis mentions a running retry (notes).

### Harness fixes found by running it

- Android scripted speech now uses the live stream; the old clip path dropped every utterance after the first.
- The Android renderer is sequential (bursty callbacks overwrote audio) and accepts 8 kHz.
- The questionnaire is written on Android.
- `wait_output` measures from completion.
- Pairing logs in as the owner first.

### Usage across the day

- **Voice:** 70 voice runs, 74 Jarvis connections, 18 simulated-user sessions.
- **Harness requests:** 68 transcriptions and 116 review/questionnaire requests.
- **L1 replay:** about 218 bookkeeping requests.
- **Monetary cost:** unknown. The credit balance is now exhausted.

### Next

1. Once credits are available, run the final verification round on f27fe91 (L2 core, L3 adaptive, Android subset).
2. If no confirmed material issue in natural updates remains, enable the conversation observer by default and remove the legacy branches (plan step 2f).
3. Repair the full backend test suite (separate session).
4. The 15-minute manual phone check.
