# M8 completed with accepted caveats — 2026-10-07

The user explicitly accepted the demonstrated M8 behavior as sufficient and
requested that M8 be labeled completed with caveats. This supersedes the earlier
milestone exit requirement to pass every complete voice/capture cohort. It is
an acceptance decision, not a claim that the original automated suite passed.

## Evidence and accepted limitations

The [clearer-audio pilot](m8-hq-audio-pilot-2026-10-07.md) admitted the correct
recipe and notes tasks in **3.052/3.500 seconds** on executable source
`8c066a1c7deb94d921056387ff8f3f4429a049b2e7202509ce8924f66b0a78a2`.
Raw provider events contain the imperfect notes prefix, while the managed
selector returned the correct goal/result type. GPT-Live supplies transcription;
Jarvis collects its text fragments. OpenAI supplies managed selector context
rather than Jarvis reconstructing it from the displayed transcript.

Accepted caveats:

- Live can return imperfect first-word transcription and an unwanted startup
  greeting despite the instruction to listen quietly.
- Earlier intermittent missing speech prevented task admission. Its precise
  native-audio/provider cause remains unproven; two successes do not prove a fix.
- Complete functional/cold/fresh/capture cohorts have not all passed on the
  latest source. The last strict M8 review remains **three of eight assertions**.
  Historical successes retain their candidate identity; failed/missing checks
  are not relabeled passed.
- The timings meet the user's practical expectation for this iteration, but
  establish no p95 guarantee. These pilots tested admission, not worker completion.

The [final-attempt report](m8-final-2026-10-07.md) retains all failures. Speech
diagnosis is follow-up work rather than an M8 closure blocker. No new tests or
provider calls were made for this decision.

## Remaining development closure

Subsequent [M9/M10 closure](m9-m10-closure-2026-10-07.md) supersedes the remaining
status recorded here: M9 passed, M8's acceptance policy is implemented, and all
M10 assertions except one research-source backend case passed. The following
requirements preserve the scope of the original decision.

**M9:** verify actual work through the persistent dedicated subscription profile,
restart/continuation, and same-task real API fallback after simulated exhaustion.
Retain credential competition, refresh ownership, expired authentication, denied
fallback and uncertain-side-effect coverage. Earlier real successes establish
implementation, not final-candidate acceptance.

**M10:** align executable acceptance with this explicit M8 decision. Record
accepted exceptions separately from passed tests; do not automatically replay
the waived bulk voice/capture cohorts as a disguised M8 requirement. This policy
alignment is now implemented in the shared manifest/runner with twenty passing
checks; see [M9/M10 closure](m9-m10-closure-2026-10-07.md).
Retain final backend/frontend/Android/browser verification, coding repair and
restart, descendant termination/lease safety, subscription/API proof, migrations
and representative complete result/notification/follow-up journeys. Keep capture
privacy and durable ownership/recovery contracts tested. Fix new failures, freeze
the final candidate and publish one coherent report with these limitations visible.

M6/M7 retain completed checkpoints; final M10 regression verifies their contracts
after subsequent changes. Trusted command mode remains explicit: commands inherit
Windows-user access; broker edits, journaling, leases, fencing and Job Object
cancellation retain their safeguards.

**M11 remains deferred:** physical wake/audio, OEM screen/lock policy,
background/battery/notification behavior and deployment opt-in are outside current
development closure. M10 will not claim physical deployment acceptance.

No new login, spending-limit evidence, physical-device action or manual spoken
corpus is currently requested. Login interaction may be needed only if the existing
dedicated session expires and cannot refresh normally.
