# Natural updates and conversation bookkeeping

This development candidate uses the existing task notification intent and shared
attention inbox. `requested` terminal outcomes enter Live commentary; discretionary
and suppressed outcomes enter thinking. Live retains speech timing and relevance
judgment. There is no deadline retry. Pending requests survive sessions, including
requests completed while disconnected. Updated reasons refresh quiet facts without
repeating requested commentary. Existing away-notification preferences still apply.

`JARVIS_CONVERSATION_OBSERVER_MODE` controls staged adoption: `off` retains the
previous provisional development behavior; `shadow` observes without committing;
`enabled` combines subscriptions with observer-owned terminal acknowledgment.
The initial default remains off until the frozen candidate qualifies. Changing this
setting requires a backend restart; do not restart an active user voice session to
adopt it. This feature does not reopen M0–M11 acceptance.

The observer uses gpt-5.6-luna, low reasoning, priority tier and structured output,
without tools. It classifies exact task outcomes against immutable assistant
fragments. User assertions, promises and append receipts cannot establish delivery.
Flags for promises and preferences are diagnostic; they cannot modify preferences.
Transcript gaps only coalesce work, with an eight-second maximum batching interval
for continuous conversation. Neither transcript gaps nor generated speech establish
a completed playback turn.

Browser and Android playback reports use authenticated controller/session identities.
They record progression, receiving/concealment evidence, pause/mute/volume, track
replacement and origin bounds on the client monotonic clock. Unknown fields,
uncovered clock ranges, gaps and partial playback cannot clear attention. No production audio
recording or extra transcription service is required. Client playback is not proof
of physical hearing. The API has no documented exact generated-audio completion
signal, so live correlation and conservative treatment of uncertainty are essential.

Durable `conversation_observations` store work, input evidence, assessments, inference
attempts, usage and leases. The service holds no transaction during inference and
never blocks voice or task control. Complete validated observations use the existing
attention audit and notification supersession transaction. Closed-session evidence
can handle only its original unchanged outcome. Model self-report cannot bypass
verification in enabled/shadow mode. Notification opening and nonterminal handling
retain their separate contracts. Provider failures stop the observer, leave attention
pending and appear at `/api/jarvis/live/observer`; they do not trigger paid retries.

Run through the existing entry point:

```powershell
$env:PYTHONUTF8 = "1" # Windows: artifacts contain UTF-8 conversation text.
uv run python -m app.announcement_eval.runner offline --conversation-bookkeeper --output <absolute-dedicated-evaluation-directory>
uv run python -m app.announcement_eval.runner pilot --conversation-bookkeeper --live --offline <offline-directory> --output <live-directory>
uv run python -m app.announcement_eval.runner report --conversation-bookkeeper --output <live-directory>
```

The offline stage freezes source, manifests, rendered speech and twelve labeled
calibration fixtures, and replays saved positive/negative evidence without requests.
The pilot calibrates the production observer once, then records one voice case at a
time for direct agent review of exact speech, audio and logs. `agent-review.json`
records cited findings and qualification; the next invocation checks it before
advancing. This is agent review, not manual user testing. No failing or inconclusive
cell is replaced. Browser connections including reconnects are capped at sixteen;
six Android connections are gated on the completed qualified browser schedule.
Reports can be replayed without provider requests and preserve old evidence.

## Four-configuration comparison (17 October)

The new `--natural-updates` phase compares repaired admission plus simplified
Responses bookkeeping (configuration 1), Decisions input preference detection (2),
Decisions output bookkeeping (3), and both Decisions capabilities (4). Runtime
settings are `JARVIS_DECISIONS_PREFERENCES=false|true` and
`JARVIS_BOOKKEEPER=responses|decisions`, alongside the staged observer mode.
They are internal development settings; the existing task preference history and
attention ledger remain the stores of truth. Exact quotations are not required.
Source dialogue windows, semantic meaning and usable client playback establish
communication. Uncertain or unplayed outcomes stay pending.

Transcript batching accounts for SQLite's second-rounded event timestamps; a
brief pause must not appear to be a full quiet interval. Client reports distinguish
receiving audio from packet-loss concealment. Playback validation permits up to
10% aggregate concealment over the union of conservatively mapped outcome ranges,
rejects reports with more than 250 ms estimated concealment, and retains coverage,
progression, track, clock, mute and pause checks. These are usability thresholds,
not a guarantee that every word was intelligible or physically heard.

The first 17 October comparison did not qualify a default: its first idle outcome played
within 2.84 seconds but bookkeeping stayed pending; local repairs then scored
21/24 in the new fixed calibration batch. A final policy clarification is locally
tested but remains uncalibrated online. See the
[comparison report](reports/2026-10-17-natural-updates-comparison.md) for retained
failures, ambiguity in one fixture, usage and the gated next experiment.

Admission refreshes newer request context before creating the task, without
parsing notification phrases. An input Decisions finding can ask a bounded backend
review to use the normal update-policy tool. That review cannot create tasks or
acknowledge communication, and newer preferences/user speech invalidate its writes.
Output Decisions findings are correlated to provider source events and the
acknowledged task-result configuration; no second classifier request is made for
a native output finding. Both classifiers use the same playback/result checks.

```powershell
uv run python -m app.announcement_eval.runner offline --natural-updates --output <offline-directory>
uv run python -m app.announcement_eval.runner pilot --natural-updates --live --calibrate --offline <offline-directory> --output <live-directory>
uv run python -m app.announcement_eval.runner pilot --natural-updates --live --configuration 1 --offline <offline-directory> --output <live-directory>
uv run python -m app.announcement_eval.runner pilot --natural-updates --live --configuration 1 --confirm --offline <offline-directory> --output <live-directory>
uv run python -m app.announcement_eval.runner report --natural-updates --output <live-directory>
```

The new phase separately caps calibration at sixty requests (24 Responses, 24
Decisions, 12 preference Decisions), screening at eight browser connections,
confirmation at sixteen, and gated Android confirmation at six. Original evidence
and the prior 83 sessions are retained. A failed transport/auth/quota/integrity
request stops paid execution. Classification failures disqualify that classifier;
there are no automatic retries or replacement sessions. Before confirmation,
`selection.json` records the directly reviewed winner and frozen source digest.
Each new case requires a direct `agent-review.json` before advancing. No classifier
is enabled by merely completing the schedule.

A confirmed Decisions service-admission outage blocks both Decisions components
without retrying that endpoint. An already fully calibrated Responses-only
configuration may continue after offline validation of the runner; the original
stop and failed request remain recorded. This exception never applies to
authentication, billing, quota or evidence-integrity failures.

Official interface references: [transcripts and playback](https://developers.openai.com/api/docs/guides/live-conversations#transcript-deltas),
[thinking and commentary](https://developers.openai.com/api/docs/guides/live-migration#route-updates-and-corrections).


The [second comparison and final review](reports/2026-10-17-natural-updates-round-two.md)
passed Responses calibration (24/24) and preference Decisions calibration (12/12).
Decisions bookkeeping scored 19/24 and remains unqualified. Configuration 2 is the
leading candidate, but a substantive overlapping promise leaves interruption
attribution unresolved. No new default was enabled. All cited fragments still
require coverage and one consistent playback track/clock origin; disjoint gaps
are never averaged away. Concealed silence is not assumed to have been silence
in the original audio.


The [natural assistant comparison](reports/2026-10-18-natural-assistant-comparison.md)
supersedes zero-overlap and absolute ten-second conversational gates. Agent review
assesses relevance, yielding and recovery; objective telemetry remains evidence.
Uncertain observer findings are retained in attention context, without asserting
delivery or requiring a repeat. Input/output Decisions questions have independently
selectable existing, concise and separate variants behind development settings.
