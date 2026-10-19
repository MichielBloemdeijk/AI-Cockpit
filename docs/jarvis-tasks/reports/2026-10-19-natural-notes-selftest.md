# Natural notes and agent self-testing: first iterations

**Status (19 October 2026):** Iteration 3 (commit 7112b7a) passes every offline layer: L0, and L1 at 18/18 twice. It has **not run live yet**: the plan caps work at two live candidate iterations before reporting back. The conversation observer stays off by default, so nothing is adopted yet.

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
