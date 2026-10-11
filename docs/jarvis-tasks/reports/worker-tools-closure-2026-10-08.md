# Worker tools implementation and acceptance — 2026-10-08

The worker tool revision is implemented and M10 passed on 2026-10-09. All fifteen
required assertions passed on frozen source c8a0aec7, with four explicitly
accepted M8 exceptions. Final ready admission: 3689.1404 ms. Physical deployment
remains deferred to M11; native coding remains gated and trusted mode is explicit.

Subsequent setup decision (2026-10-09): the [personal-use plan](../10-personal-setup-and-phone-acceptance.md)
makes the password and full development-data backup optional, simplifies linking
and startup, and puts the user's phone test last. Password-block observations
below describe the validator at this M10 checkpoint; the planned M11A update has
not changed it yet. Raw acceptance/readiness evidence remains unchanged.

## Implementation

- Native hosted Codex research replaces the separate discovery model and local
  HTTP fetch prerequisite. Workers can try alternate sources and assess their
  adequacy. Published sources retain ordinary URLs and completed same-task
  native event references, without claiming independent full-page retrieval.
- Persisted global defaults/disablement, workflow choices and task overrides
  resolve into a frozen run policy. Global restrictions win; revocation pauses
  affected active execution. Settings and task/follow-up controls expose this.
- Native subagent observations include recursively discovered children, without
  stopping children during an ordinary parent wait. Approved skills are loaded
  from exact owner-approved paths; implicit apps/plugins/hooks/installers remain
  disabled.
- Jarvis owns frozen plural images, questions, task history, atomic typed
  publication and completion. Obsolete private file aliases and custom research
  code/configuration are removed. No replacement budget system is introduced.
- The connector framework enforces per-account/tool allow, ask-first or disabled
  permission, exact human-only decisions, revision checks and uncertain-effect
  journaling. Real connectors and browser automation remain deferred.
- Migration `20261008_0037` was applied to the development database. No data reset
  was performed. Old research-source schemas/workflow identities are intentionally
  unsupported; current fixtures and consumers use native event references.

## Windows decision

Two credential-free unelevated native probes passed basic allowed-root and
outside-root checks. Real Python temporary directory permissions still fail.
Native coding remains gated. The already selected explicit `trusted` command
broker is retained; there is no broader retry after native rejection.

Trusted commands inherit the Windows user's filesystem/network access. Brokered
file edits remain root-constrained. Durable records, task directory leases,
stale-run fencing, scratch ownership and Windows Job Object descendant stopping
remain enforced. Representative trusted Python/Node workloads passed. This does
not establish native filesystem confinement.

## Retained failures and corrections

Three targeted research pilots used five worker turns. The first exposed source
references buried in noisy history and a publication bypass through ordinary
finish. Both were repaired. The second published and revised correctly, but its
assertion counted search nested inside a history receipt as a fresh search.
Native item parsing now treats embedded receipt content as data, with stable
thread/item provenance across runs. The final pilot passed: initial result
18.469 seconds, same-task update 11.578 seconds, one hosted search, same URL and
no additional search on follow-up.

The first frozen combined run passed 665 backend cases, including all eleven
provider cases, but failed one admission/telemetry race. Cancelling an aiosqlite
await can leave its driver operation running; closing the connection too early
can interfere with the next admission. Diagnostic connections now have a short
lock budget from birth, and in-flight SQL finishes before cleanup. A batch gets
one fresh-transaction retry for a transient lock/deadline failure; metric keys
prevent duplicates. The initial stress benchmark's two lost observations and
all failed reports remain retained.

The first browser smoke failed before any GPT-Live session. Its injected
microphone reused a stream after React had stopped its track. Each request now
gets a new stream. The corrected check passed with one live session. An attempted
empty-key PowerShell override removed the variable, allowing configured-key
fallback; this is counted as live use, not an offline pass. PowerShell also
reported misleading failure exits for successful Python runs that wrote stderr;
a separate zero-exit stderr probe reproduced this. Explicit native exit codes
are used in subsequent diagnostic commands.

## Offline admission measurements

All eleven benchmark checks passed. Five repeated/interleaved trials retained
700 measured admissions, no admission failures and no dropped/failed observations.
The current queued path has 100 samples per category:

- Ready screen context: median 190.8 ms, p95 277.2 ms, maximum 613.8 ms.
- Document creation: median 145.7 ms, p95 237.4 ms, maximum 699.0 ms.
- Ordinary task: median 122.8 ms, p95 187.7 ms, maximum 506.6 ms.
- Continuation: median 149.1 ms, p95 332.8 ms, maximum 695.4 ms.
- Replay: median 8.0 ms, p95 10.3 ms, maximum 15.2 ms.

These are network-blocked gateway measurements on real migrated durable SQLite,
with mocked provider streams and ready capture records. They exclude Android
capture, provider generation, HTTP framing and network transport. They ran under
concurrent regression load and do not replace the final real admission check.
Raw observations are in `data/worker-tools-local-timing-verified*`.

After the connection-setup correction, all eleven benchmark checks passed again
(`data/worker-tools-local-timing-db-final*`). Queued samples have 100 observations
per category, including interleaved trials, with zero failed/dropped observations:

- Ordinary task: median 83.14 ms, p95 101.19 ms, maximum 641.46 ms.
- Document: median 105.44 ms, p95 134.53 ms, maximum 722.63 ms.
- Ready screen context: median 163.75 ms, p95 203.03 ms, maximum 709.22 ms.
- Continuation: median 106.80 ms, p95 149.43 ms, maximum 593.37 ms.
- Replay: median 7.84 ms, p95 8.51 ms, maximum 10.15 ms.

These ran alongside local regression tests. They are observed improvements in
this cohort, not a controlled causal estimate of the WAL correction or a
guarantee for provider-inclusive latency.

## Final candidate and acceptance

The preceding frozen source was `5f27ae33a5ff496049425a7f986def983fbe7dca2ab1f3f0ece624c9b2ac7625`
(705 files). Executable workflows, tests, runtime/configuration, persisted
permissions and approved skill contents participate in candidate identity.
Documentation updates do not change it.

The acceptance runner now executes privacy and mobile-result stages itself when
their evidence is missing. Successful launchers alone cannot pass those gates.
Focused telemetry/publication checks passed 34 cases; final retry/cleanup/runner
checks passed 39. The broad offline regression passed 656 cases, with two
dedicated provider cases intentionally skipped and 23 cases deselected. The
combined run in `data/worker-tools-m10-verified/` stopped with 13 passing gates,
four accepted M8 exceptions, and two failures. Backend acceptance exceeded its
1200-second launcher budget after 599 successful call reports. The browser
connected correctly but its selected calibration sample contained only a wake
phrase, with no substantive request to answer. Neither failure was waived.

A local-only continuation conservatively reran the last reported backend case
and the remaining cases; 70 passed and one failed (with a teardown error).
The failed read was cancelled during per-connection `PRAGMA journal_mode=WAL`,
leaving the test database open. No combined pass was synthesized from that
attempt. WAL is now established as persistent database state during setup and
migrations; each connection retains foreign keys, full synchronous durability
and its appropriate lock budget. Cancelled connection initialization finishes
before rollback/close. Focused database/orchestration/telemetry/runner checks
passed 63 cases, and the added connection/read regressions passed all 11 database
cases. Broad offline and timing revalidation subsequently passed. The backend launcher
budget is declared as 2400 seconds in the shared acceptance manifest.

The final browser input combines the retained human wake sample with the retained
HQ request, "Tell me a joke about a penguin." No new TTS request is needed;
its source and content hash are recorded in
`data/worker-tools-browser-audio/manifest.json`. These executable corrections
change candidate identity; prior provider observations cannot pass the new source.

The 2026-10-09 offline regression passed all 661 selected cases (23 diagnostic,
provider or browser cases deselected), with no failures or skips. Current frozen
source is `c8a0aec7ed5e5f334941f7c51b728e323dcd1753a56d72c973f476911c38226d`.
The substantive-request browser pilot passed with one real Live session; its
same-candidate evidence is imported rather than spending on another unchanged
browser request. Final combined acceptance passed in
`data/worker-tools-m10-db-final/`; ready admission passed at 3689.1404 ms, real
coding recovery passed, subscription recovery/API fallback passed unattended,
and all three descendant-containment cases passed. The combined report has
nineteen assertions: fifteen passed and four accepted M8 exceptions, with no
failed/skipped required checks and no candidate-stability failure.

- Backend: 673 passed, 11 optional diagnostics deselected, zero skips. This
  includes all eleven required provider cases and the formerly failing read
  and cancellation checks. Duration 1294.29 seconds; the old 1200-second launcher
  allowance was insufficient. Two dependency deprecation warnings remain.
- Frontend: 192 passed, type checks, production build and result export passed.
- Android: all 28 JVM cases and the debug build passed.
- Browser: one actual Live pilot passed; exact same-candidate proof was reused.
- Capture privacy: eleven native observations and eleven contract checks passed.
- Coding: an actual failing test, source repair, passing test, host restart and
  same-task continuation passed in explicitly labeled trusted mode.
- Subscription: actual subscription execution, shared-owner competition, host
  restart and real API continuation after simulated exhaustion passed unattended.
- Mobile: custom wake, both screen roles, three sources, real notification,
  result reopening without a new run and same-task UI continuation passed.
  Its admission was 3473.7376 ms; this is separate from the final latency sample.
- Migration and descendant containment passed.

Raw final proof: `data/worker-tools-m10-db-final/combined/acceptance.json`.
Read-only `data/worker-tools-readiness-final.json` independently confirms
implementation readiness. Deployment preparation is still blocked only by the
development owner-login configuration (no valid password hash). Physical/OEM
testing, production opt-in and complete deployment backup remain deferred to M11.
The readiness check made zero provider calls and no mutations. No normal-use
flags, account settings or credentials were changed, and no application data was
reset. The roadmap, runbook and current ledgers are updated as report-only changes.

The preceding repaired candidate's final admission passed at 3668.0117 ms, with actual
priority tier and no fallback. This is the user-approved single under-4.5-second
check, not a provider p95 assertion. Its selector response-start-to-complete-tool
arguments interval was 1433.08 ms versus 674.80 ms in the preceding sample.
Reported reasoning tokens stayed at 15; output tokens were 77 versus 60. The
local gateway-start-to-admission-commit interval was 135.84 ms versus 132.25 ms.
These two samples attribute the larger delay to the selector interval, without
establishing an admission transaction regression or a token-generation rate.
All compared server intervals use server monotonic time; Android timestamps are
not subtracted from server timestamps.

The preceding candidate's final voice admission passed at 3120.4552 ms, actual
priority tier, no fallback. Its real mobile result/notification/reopen/follow-up
journey passed at 3288.4052 ms admission. Real coding failure/repair/pass/restart
and dedicated subscription execution/ownership/restart/API handoff also passed.
Saved subscription login worked unattended. These are preceding-candidate
observations, not automatic passes for the repaired candidate.

## Provider use and cost

Purpose/counts: three focused research pilots (five worker turns), one preceding
combined acceptance with eleven provider cases plus dedicated coding and
subscription cases (two worker turns each), one Android mobile journey (two
worker turns), one final voice admission, and one successful browser repair
pilot. The initial failed browser check created no provider session. The second
combined run added eleven provider cases, two coding turns, two subscription/API
handoff turns, one mobile journey with two turns, one final voice admission and
one browser session that connected but received no request. No new TTS or file
transcription request was made; the retained HQ corpus was reused.

The final frozen run added eleven backend provider cases, two real coding turns,
two subscription/API handoff turns, a two-turn mobile journey and one final voice
admission. Its preceding substantive-request browser pilot used one Live session;
that evidence was imported, with no additional browser session in the combined
run. Final selector usage: 3768 input and 113 output tokens (31 reasoning), actual
priority tier and no fallback. Test/turn counts are not a complete HTTP request
count or an invoice. Total cost remains unknown.

Native usage is retained in local task evidence. The last research pilot's
cumulative thread usage was 269709 input tokens, 229420 cached input tokens,
1611 output tokens and 144 reasoning tokens. Do not sum cumulative same-thread
totals across attempts. The preceding final selector recorded 3750 input and
60 output tokens (15 reasoning). Total provider HTTP request count and invoice
cost are unavailable; cost remains unknown. Subscription exhaustion was
simulated, with real authorized API continuation rather than deliberate spending
to exhaust an account.
