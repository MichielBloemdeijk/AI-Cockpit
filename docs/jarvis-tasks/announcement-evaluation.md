# Autonomous task-announcement evaluation

This isolated experiment compares GPT-Live announcement selection and delivery.
It does not change production defaults or reopen M0–M11 acceptance. The actual
coordinator, provider adapter, tool gateway, presentation service, task admission,
result publication and client media path run in every live case.

Run from `backend`:

```powershell
uv run python -m app.announcement_eval.runner offline --output ..\data\announcement-evaluation\offline-01
uv run python -m app.announcement_eval.runner pilot --live --offline ..\data\announcement-evaluation\offline-01 --output ..\data\announcement-evaluation\pilot-01
uv run python -m app.announcement_eval.runner acceptance --live --offline ..\data\announcement-evaluation\offline-01 --pilot ..\data\announcement-evaluation\pilot-01 --output ..\data\announcement-evaluation\acceptance-01
uv run python -m app.announcement_eval.runner report --output ..\data\announcement-evaluation\pilot-01
```

`offline` runs provider mocks, fixture-publication and grading contracts, contained
process cleanup, and a network-blocked Chrome media exercise. It renders the fixed
English corpus through the existing offline SAPI renderer, verifies its format,
and freezes file hashes and candidate identity. Chrome and Android SDK dependencies
must already be installed. Android compilation and unit tests run with
`android\gradlew.bat :app:assembleDebug :app:testDebugUnitTest` before screening.

Paid stages require `--live` and an existing authorized API key. Every scenario
starts a fresh migrated database and dedicated owned hosts. The pilot allows at
most thirteen cases, including its baseline, conditional control and paired timing
comparison. An expected baseline behavior failure is retained. Missing media or
recognition evidence stops screening for local repair. A failing candidate stops
its screening cases. Provider authentication, billing and quota failures stop
paid execution. There are no automatic paid retries.

The runner alone can select `baseline`, `commentary_relevance`,
`thinking_relevance`, `thinking_reconsider` and `commentary_control`. Process-local
patches supply neutral verified terminal facts and the frozen relevance policy.
Candidates project terminal context even for an admitted `silent` task, retaining
its stored policy. This tests whether Live respects silence after seeing the
outcome. The baseline retains the production policy gate. A small projector
predicate keeps the normal behavior identical; only the isolated host overrides
it, and the normal projector still owns all presentation rows.
Reminders, clarification questions, stale generations, wake checks, deduplication
and acknowledgment semantics retain their existing contracts. The fallback adds
an application-authored reconsideration instruction. The control cannot qualify.

The browser driver streams cached speech through the real microphone/WebRTC
path, including continuous silence. Input, remote output and audio-element
playback progression share a recorded clock. Reconnect retains earlier audio.
The independent Live caller receives only its agenda and Jarvis's recorded audio;
it never receives fixture completion events, expected answers or grading rules.
Its primary WebSocket streams continuous PCM at 24 kHz and closes explicitly.

Android confirmation uses the existing owned API37 emulator and real device
pairing. Explicit Hello World completion uses the real worker. Other cases use
the deterministic worker's fenced result publication. Debug-only recording is
activated by the runner: a bounded queue retains input and output PCM, callback
timestamps and AudioTrack playback positions in private app storage. Queue loss,
unknown playback or recording gaps cannot pass. Sessions and owned processes are
closed in cleanup; original physical-device settings and credentials are unused.

Saved evidence includes provider transcripts, appends and acknowledgments, task
events, model tools, attention rows and client playback, alongside configuration,
prompt, source and corpus identities. `whisper-1` independently transcribes output
with word timestamps. A separate `gpt-5.6-luna` low-reasoning request grades exact
audio spans against verified task truth and the caller conversation. Deterministic
timing, truth, duplicate and premature-handled failures override the grader.
Negative relevance and interruption judgments require cited audio spans;
interruption spans must overlap the recorded caller audio. Brief listening
backchannels and admission acknowledgments are allowed. A withheld terminal
append makes a model-selection case inconclusive, even if it stayed quiet.
The ten-second deadline has a reported 250 ms clock/ASR tolerance. Queueing is
retained in the append and durable-completion clocks. Observed silence never
establishes model intent.

`report` regrades existing audio and semantic judgments without opening voice
sessions. Add `--live` only to produce missing transcription/grading evidence.
Judgments are stored by rubric hash, with explicit word indices and a bounded
JSON schema; an older rubric's judgment cannot qualify a newer rubric. Repaired
recording can recover the initial recorder's missing receive-loop events from
its retained provider diagnostics using the shared process monotonic clock.
Derived evidence is saved separately and original recordings remain intact.
After repairing the harness, a pilot can use `--baseline` with the prior pilot
directory to reuse its single baseline voice run. Profile and corpus-file hashes
must match. The reused baseline's original source identity and usage remain
visible, and it cannot qualify any experimental candidate.
Existing paid results are reused; report changes cannot silently resample sessions.
`--resume` retains earlier screening, original source identities and usage. A
projection repair can reuse a recorded case only when the routing/prompt policy
matches and every terminal task had a non-silent policy, so that repair leaves
the case's behavior unchanged. A case whose silent policy withheld context is
retired as an inconclusive diagnostic; its evidence, earlier grades and place
in the thirteen-case limit remain. Final acceptance never reuses screening.
The report records individual voice sessions, backend response IDs, transcription,
grading, caller and real-worker usage. Unknown monetary cost remains unknown.
UTF-8 is explicit on every evidence read. Paid request accounting is persisted
before a call and after its completion. If the report writer fails after a
recorded scenario, `report --resume <prior-pilot> --offline <recorded-offline>`
can reconstruct the missing report from retained artifacts without opening
another voice session; add `--live` only for missing grading evidence.

A recommendation requires all thirty-six scripted, six adaptive and six Android
cases for the same frozen candidate. Every failure and inconclusive run remains
in the report. Partial or duplicated cohorts cannot qualify. Physical speaker
acoustics and subjective voice naturalness remain outside this gate.

The [initial experiment report](reports/2026-10-12-announcement-evaluation.md)
records the eight-run screening result and why final acceptance was not started.
The [preference follow-up](reports/2026-10-12-preference-announcement-followup.md)
retains all thirteen diagnostic runs, the new successes and remaining blockers.
The [GPT-Live-only configuration report](reports/2026-10-13-gpt-live-only-announcement-screen.md)
records the subsequent 28-session screen, all channel combinations, targeted
prompt variants, native acknowledgment/timing diagnostics and grading limitations.

The authorized follow-up adds two isolated profiles: `thinking_preference` and
`preference_routed`. They replace the Live prompt with the documented
Backchannel/Interruption/Delegation structure. The backend retains task
procedures and records notification intent through real gateway tools. Intent
is requested, discretionary, or suppressed; ordered durable task events retain
the reason, revision, voice session, generation and tool-call provenance.
Later preferences must be saved even when Live delegated before the caller
finished the original sentence. No fixture or transcript parser chooses intent.

Every terminal append reads the latest durable preference, includes it beside
short verified task facts, and retains opaque acknowledgment handles. The first
profile always uses thinking. The fallback uses commentary only for requested
announcements; all other outcomes use thinking. Both continue to expose silent
outcomes to test actual preference compliance. Changes remain process-local;
production tools, prompts, routing and acknowledgment transitions are unchanged.

```powershell
uv run python -m app.announcement_eval.runner offline --corpus-from ..\data\announcement-evaluation\pilot-20261011-04 --output ..\data\announcement-evaluation\offline-followup
uv run python -m app.announcement_eval.runner pilot --live --offline ..\data\announcement-evaluation\offline-followup --resume ..\data\announcement-evaluation\pilot-20261011-04 --profiles thinking_preference preference_routed --output ..\data\announcement-evaluation\pilot-followup
```

The resumed screen retains the earlier eight runs and their identities in the
same thirteen-run ceiling. Each new candidate must pass explicit notification,
silence, a separately spoken change from notification to silence, and occupied
conversation. Original corpus files must remain identical; the changed-preference
clip extends the frozen corpus. Acceptance retains twelve families and 36 scripted
runs, with the second silence-family repetition exercising changed preference.
There are no automatic paid retries or additional budgets. The existing listening
timing remains selected unless the required paired timing comparison completes.

This diagnostic phase has now used all thirteen runs. Do not restart from an
older checkpoint to bypass that ceiling. The current preference profiles include
an offline-only wording correction after screening; their saved live evidence
belongs to the earlier frozen source. Additional paid screening needs a new
explicitly bounded phase after new candidate changes and offline validation.

Grading separately reports complete spoken outcomes whose presentations remain
pending. It checks saved notification intent against explicit caller preferences,
and retains the existing premature-handled release blocker. Playback observations
never auto-handle a presentation or silently alter acknowledgment semantics.

## Model-controlled context phase

The user's direction to exhaust GPT-Live-controlled options authorizes a separate
bounded diagnostic phase. The earlier thirteen runs remain immutable evidence;
they are referenced, never reclassified or counted as evidence for new candidates.
The test harness records and grades behavior. It does not choose announcements,
reconcile preferences, force playback or acknowledge communication.

Ten fixed profiles cover all seven nonempty combinations of thinking, commentary
and instructions, plus instruction-before-facts ordering and a receipt barrier:

- `live_thinking_context`: thinking only, with corrected advisory preference wording.
- `live_commentary_context`: commentary only, with the same facts and preference.
- `live_outcome_instruction`: a single instruction containing policy and outcome.
- `live_thinking_instruction`: thinking followed by a reconsideration instruction.
- `live_commentary_instruction`: commentary followed by the same instruction.
- `live_instruction_commentary`: the instruction before commentary.
- `live_instruction_thinking`: the instruction before thinking.
- `live_thinking_commentary`: the same outcome in thinking and commentary.
- `live_thinking_commentary_instruction`: both factual channels, then instruction.
- `live_thinking_receipt_instruction`: thinking, its exact provider receipt, then instruction.

Every channel sequence is fixed regardless of the preference. The initial Live
prompt, backend, tools, acknowledgment semantics and cached corpus are identical.
The model alone chooses relevance, speech, deferral and silence. The receipt
barrier is bounded to five seconds, matches session/generation/event identity,
and cannot imply speech. Instruction-only delivery uses the same context receipt
contract without changing handled state.

Each profile screens explicit notification, silence, changed silence and occupied
conversation. Stop that profile on its first clear reminder-selection failure,
preserve it, and move to the next distinct configuration. Failures confined to
pre-completion admission interruption or preference persistence are retained and
still prevent qualification; further cases characterize reminder selection
without repeating the failed case. Missing evidence and provider auth/quota/billing
failures stop the entire phase. At most forty screening sessions and four paired
timing sessions are allowed; there are no unchanged-case retries. A qualifying
frozen profile may proceed to the original 48-case acceptance contract. No finite
experiment can exhaust arbitrary prompt wordings; this phase exhausts the defined
documented channel combinations and meaningful ordering controls.

A paused model-only phase can continue with `--model-only --resume <phase>` after
local repairs and new offline evidence. It must retain the same corpus, prompt
and profile identities, historical reference and 44-run limit. Recorded cases
are reused with their original source identities and grades retained; no failed
or inconclusive voice case is repeated unchanged. Negative semantic penalties
require provider-transcript support as well as cited recorded-audio words.
Disagreements remain inconclusive. One immediate-context diagnostic may investigate
a retained undelivered commentary silence case. Immediate delivery permits
thinking, user-speaking and assistant-speaking floors only for task context;
the normal connection, wake, generation and duplicate checks still run, and the
durable floor remains unchanged. The model retains the speech decision.
Unrelated conversation interruptions are likewise retained as failures and
prevent qualification, but may permit further task-announcement characterization
when no completed or partial task outcome overlaps them. This never makes the
failed conversation check pass.

After all ten channel profiles have recorded screening evidence, a continuation
may add `--prompt-followup` for specifically motivated model-only variants:
`live_thinking_simple_reconsider` uses the original short reconsideration text,
`live_outcome_conditional` directly conditions speech on the latest conversational
request and makes verified completion supersede stale admission reports, and
`live_thinking_listen_first` asks Live to hear the complete request before
delegating admission. The backend/tool contract is unchanged. These variants
address observed missed outcomes and partial-request preference capture; they
use the remaining allowance within the same 44-session phase, never a fresh count.
The final `live_thinking_strict_floor` variant strengthens user-description
priority and discourages spoken acknowledgments of internal appends. It tests
occupied conversation first because that is the specific remaining failure;
explicit notification, silence and changed silence follow only if that case
passes. It retains thinking-only delivery and the same backend/tools.

The optional continuation flag `--native-diagnostics` tests two remaining seams
within the same 44-run limit: one requested commentary announcement with an
explicit Live delegation exception for post-speech `acknowledge_task_update`, and
one occupied `live_thinking_listen_first` case with immediate context delivery.
The acknowledgment probe is diagnostic-only and cannot become a winner. It uses
the existing model tool and acknowledgment semantics, never an automatic handled
transition. The timing case compares with its retained listening-mode evidence;
it is not final acceptance or an unchanged-case retry.

```powershell
uv run python -m app.announcement_eval.runner offline --corpus-from ..\data\announcement-evaluation\offline-20261012-preference-04 --output ..\data\announcement-evaluation\offline-model-only
uv run python -m app.announcement_eval.runner pilot --live --model-only --prior ..\data\announcement-evaluation\pilot-20261012-preference-02 --offline ..\data\announcement-evaluation\offline-model-only --output ..\data\announcement-evaluation\pilot-model-only
```

Sources used for the design:

- [OpenAI voice evaluation stages](https://developers.openai.com/cookbook/examples/audio/voice_agent_evaluation)
- [Live delegation and update channels](https://developers.openai.com/api/docs/guides/live-delegation)
- [Live continuous audio and session lifecycle](https://developers.openai.com/api/docs/guides/voice-websockets)
- [Whisper word timestamps](https://developers.openai.com/api/docs/guides/speech-to-text#timestamps)

## Focused calibrated comparison (14 October)

The authorized `focused-context-v2` phase has its own **24-session ceiling**;
the original 13 and subsequent 28 sessions remain historical evidence. Run the
same entry point with `--focused`. Configuration A is the exact
`live_thinking_listen_first` prompt and existing preference-aware thinking payload.
Configuration B, `live_thinking_concise`, shares its prompt, tools, backend and
acknowledgment contract, but supplies each terminal fact once as compact JSON.
Neither configuration selects channels by notification preference. Both use the
existing isolated immediate-context delivery mode. Production is unchanged.

```powershell
uv run python -m app.announcement_eval.runner offline --focused --output ..\data\announcement-evaluation\offline-focused
uv run python -m app.announcement_eval.runner pilot --focused --live --offline ..\data\announcement-evaluation\offline-focused --prior ..\data\announcement-evaluation\pilot-20261012-model-only-06 --output ..\data\announcement-evaluation\pilot-focused
uv run python -m app.announcement_eval.runner report --focused --output ..\data\announcement-evaluation\pilot-focused
```

Offline freezes the full source identity, experiment manifest, rubric and newly
rendered corpus. The manifest specifies six conditions, two wording variants,
and alternating A/B order. Before controlled completion, admission must settle
with no pending backend response and 1.5 seconds without recorded assistant
speech or backend activity, within a twenty-second setup guard. Backend handoff
can queue later Live speech; a post-hoc admission span that resumes around
completion after the settled marker retires that cell as a confounded setup
diagnostic. It remains counted and is never replaced. Completion during a description is
scheduled one second after its input starts; observation lasts sixteen seconds
after the caller finishes. A matched control omits completion. Both silence
conditions ask status only after their silent observation window.

Paid execution first grades twelve fixed semantic calibration fixtures. Every
fixture must match before reassessing the 28 historical recordings or opening a
new voice session. Historical reassessments use cached ASR and write into the
new phase's `historical/` directory. Original evidence and grades are immutable.
There are at most 64 additional grading and 24 transcription requests, persisted
individually in the request ledger, with no automatic retries. Report replay
without `--live` uses saved responses only. Resume uses the same output directory
with `--resume`; identities and all attempted cells remain in its count.
Local harness repairs may append a new source identity after passing offline
checks, provided the experiment manifest, application configuration, profile
prompts/payloads and audio bytes remain identical. Each recording retains its
own source identity. This does not make earlier confounded cells qualify.

Version-two assessments classify every audio word into admission, progress,
promise, backchannel, outcome, conversation or ambiguity. Ordered transcript
alignment, cited terminal-result words, actual caller signal intervals and
utterance-level playback traces accompany the semantic judgment. Playback traces
include audio timeline coverage, position, paused/muted/volume and track identity.
Earlier recordings lack some of these fields and cannot receive retroactive
playback qualification. New transcription packs voiced regions with padding and
a reversible timing map into one request, avoiding lengthy silent ASR tails.

Reports separate evidence validity, context delivery, notification selection,
deadline, task truth, interruption, preference persistence and acknowledgment.
Missing delivery is inconclusive for model selection. Conversation-only overlap
can fail interaction without failing selection. Missing acknowledgment prevents
complete interaction qualification; a premature handled transition remains a
blocker. No recorder, grader or report marks presentations handled.

Authentication, billing, quota or evidence-integrity failures halt paid work.
Confirmed mandatory selection failures stop that profile's unrun cells; all
failures and inconclusive recordings stay in the report. The focused stage never
starts broad acceptance, simulated callers or Android Live runs automatically.
Two repetitions characterize behavior and cannot establish a reliability rate.

The [14 October focused report](reports/2026-10-14-focused-announcement-evaluation.md)
records twelve passing semantic calibration fixtures, 28 historical reassessments,
seven new sessions and the two mandatory failures that stopped the remaining
seventeen cells. It preserves two admission-setup diagnostics and one caller-script
diagnostic. No candidate qualified and no broad acceptance was started.

## Independent mechanism verification

`--verify-mechanisms` selects a separate, at-most-eight-session diagnostic using
the existing offline/pilot/report entry points. It audits original PCM and native
provider timelines, records duplex WebRTC statistics, and tests two idle wordings,
provider-received floor instructions, and matched no-completion conversations.
Original session counters and evidence remain unchanged. Paid execution requires
`--live`; replay requires no provider request.

The [mechanism verification report](reports/2026-10-14-context-mechanism-verification.md)
records seven attempts: two verified idle outcomes within ten seconds, a verified
speaking-case deadline miss with no outcome overlap, and four inconclusive
diagnostics. Its one remaining speaking repetition was stopped after the failure.
It explains transcription tokenization and removed-silence timing repairs, and
limits conclusions about interruption to the evidence actually established.
No complete interaction qualified and no production default changed.

## Production configuration A adoption

The new `--adoption` phase uses production prompts, tool schemas, event-sourced
notification preferences and quiet terminal routing. It supplies no behavioral
monkeypatches. Only process isolation, deterministic workers and recording remain
evaluation-owned. Production does not import this package.

```
uv run python -m app.announcement_eval.runner offline --adoption --output ../data/announcement-evaluation/OFFLINE
uv run python -m app.announcement_eval.runner pilot --adoption --live --offline ../data/announcement-evaluation/OFFLINE --prior ../data/announcement-evaluation/pilot-20261014-verification-01 --output ../data/announcement-evaluation/ADOPTION
uv run python -m app.announcement_eval.runner report --adoption --output ../data/announcement-evaluation/ADOPTION
```

The baseline has eight frozen conditions. Subsequent `--batch preference`,
`--batch acknowledgment`, or `--batch timing` invocations require `--resume` of
the same adoption directory and newly passing offline evidence. Each batch has
four cases; the phase permits two tuning batches and at most sixteen browser
provider sessions, including reconnects. Original attempts are never replaced, and report replay makes no provider
requests. Each candidate retains its source/configuration identity and corpus.

Task admission and policy changes save optional notification intent atomically
in `TaskEvent`, independently of progress frequency. Quiet terminal context may
arrive during speech; context receipt releases its transport lease without taking
the conversational floor or marking an update handled. Suppression retains the
outcome for later questions and retires queued away notifications. Only Live's
delegated model report can acknowledge spoken delivery. Unacknowledged outcomes
stay pending, including after reconnect.

Configuration A is a provisional development default. Adoption does not reopen
M0–M11 or imply that timing, interruption and acknowledgment blockers are resolved.
Android voice confirmation requires mandatory browser behavior and acknowledgment
to pass; builds and local Android checks run before the browser pilot.

`--agent-review` records one case and pauses for a cited primary-agent review of
the saved responses, logs, audio transcription and playback evidence. It makes
no new semantic-grading request. Reviews are saved separately from the original
blind grades and do not change application state. Report replay also recomputes
their deterministic dimensions from the saved cited spans without provider calls.

The [production adoption report](reports/2026-10-14-production-a-adoption.md)
records the implementation, full offline gate, eight baseline attempts and two
bounded prompt tuning batches. A remains provisional: missing acknowledgment,
inconsistent notification preference persistence and a verified occupied deadline
miss prevent complete qualification and Android voice confirmation.

## Concise task-update tuning

`--adoption --concise-updates` starts a separate phase with one production candidate,
six cases and at most eight provider connections, including reconnects. Use the
same flags for `offline`, `pilot` and `report`, with `--agent-review` for direct
cited review after each case. `--prior` references the closed adoption report so
cumulative usage is retained. This does not reopen its counter or acceptance.
The matrix includes normal worker completion while disconnected, followed by a
real replacement session and a spoken status question.

The [concise policy report](reports/2026-10-14-concise-update-tuning.md) records seven
sessions, successful routine notifications and recall, missing acknowledgment,
inconsistent preferences and the unqualified interruption/reconnect case. Final
snapshot task-truth grades remain visible alongside direct review of the failure
announcement before its later automatic continuation.

## Plain-language delivery-recording probe

`--adoption --acknowledgment-probe` is a separate two-provider-session diagnostic
phase. The frozen cases are requested idle completion and a separately labeled
explicit bookkeeping control after the unprompted observation window. Use these
flags for `offline`, `pilot` and `report`, and `--agent-review` to inspect each case
before resuming. `--prior` references the latest closed phase for cumulative usage.
This phase neither resets prior counters nor starts Android voice acceptance.

The explicit control is not evidence of spontaneous acknowledgment. Its wording
presupposes a previous announcement: if that announcement did not happen, a
handled transition is a false-premise failure, not a successful control. Check the
actual preceding audio and provider transcript before drawing a conclusion.

The [delivery-recording probe report](reports/2026-10-15-plain-delivery-recording.md)
records the two attempts, real acknowledgment-tool execution, missing spontaneous
reporting and the premature model-reported handled transition. It also documents
the native event/API boundary for alternatives without a separate transcript model.
