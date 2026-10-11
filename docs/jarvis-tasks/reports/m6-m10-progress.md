# M6–M10 implementation progress — 2026-10-06

### Phone recipe worker database repair — 2026-10-11

The [database repair report](phone-database-fix-2026-10-11.md) records a backend
relative-path bug after successful capture/upload. The exact MCP process error
is reproduced locally and repaired; 83 targeted backend tests pass with two live
tests skipped. Unpaid API37 normal-Chrome acceptance now includes real worker
image/history reads and typed recipe publication, rather than stopping at upload.
This is targeted repair evidence; historical combined gate passes retain their
original candidate identities and physical M11D confirmation remains open.

The current admission/execution revision below supersedes the 2026-10-03 cutoff,
single-image and login-dependency findings. Historical observations are retained
and are not current-candidate passes. M10 passed on 2026-10-09: fifteen required
assertions passed with four explicitly accepted M8 exceptions. M11A-C personal setup and automated final-candidate verification are complete
on 2026-10-10; the user's phone test remains the final step.

## Current implementation revision

### Physical phone screen feedback — 2026-10-11

Voice is reported improved after the setup repair. The recipe screenshot failure
occurred before Android was asked to capture: browser controller identity was not
bound to the paired native capture channel. [Screen repair report](phone-screen-fix-2026-10-11.md)
tracks the fix and unpaid actual Chrome/two-image admission-upload proof. No new
paid provider acceptance or physical success is claimed; M11D remains pending.

### Physical phone setup feedback — 2026-10-10

M11D found a browser microphone startup failure before any provider request,
and Settings showed owner-only pairing controls to an already paired device.
[Repair report](phone-setup-fix-2026-10-10.md) tracks explicit native/WebView
microphone handover, automatic supported-feature setup and durable paired status.
The earlier fifteen-gate proof is retained for source4f58a725; these changes use
targeted unpaid regression checks, with physical audio confirmation still pending.

### Personal setup ready for final phone test - 2026-10-10

[Completion report](personal-setup-2026-10-09.md): source4f58a725 passed all
15 required gates with four accepted M8 exceptions; 679 backend, 192 frontend
and 30 Android checks. Final ready admission 3676.2937 ms; real mobile admission
3275.5216 ms. Personal host Ready, installable APK, QR/link and unified scoped
phone sessions are verified. Only M11D physical testing/normal-use confirmation
remain; use the [phone checklist](../phone-test-checklist.md). Raw failed attempts
are retained and final assembly reuses validated same-candidate evidence.

### Remaining path simplified — 2026-10-09

The [personal setup and phone plan](../10-personal-setup-and-phone-acceptance.md)
now owns remaining work: A optional-password access and one-action startup,
B one-time QR/link pairing and no separate phone WebView login, C agent-run
verification, then D the user's final phone checks. Reuse Windows/Tailscale;
no new hosting or mandatory full backup for disposable data. Device/privacy and
durable recovery guarantees remain. M11A/B now implement these decisions. Readiness checks the actual private
transport and no longer requires a password or complete development-data backup. Raw M10
reports are retained unchanged; no paid rerun is needed for this documentation.

### Worker tool revision implemented; M10 complete — 2026-10-09

The [worker policy](../09-worker-tool-policy.md) supersedes the custom research and
all-broker profile. Hosted native research, persisted default/workflow/task tool
selection, approved skill loading and native child history are implemented.
Connector permissions have exact human-only approvals and uncertain-effect
journaling; real integrations remain deferred. Workflow source schemas,
fixtures, frontend settings/task choices and permission UI are updated together.
The development migration completed without a data reset.

Unelevated native probes passed basic boundaries twice, but Python temporary
folder permissions still fail. Native coding remains gated; the existing
explicit trusted broker passes real Python/Node scratch/subprocess/failing-exit
and Job Object termination checks. No unrestricted retry or new ACL repair was
introduced.

Offline regression: 604 backend cases passed, 13 skipped and 12 provider/browser
cases deselected; the separately selected legacy mock suites passed all 42.
Focused revised policy/recovery/publication checks passed, as did 192 frontend
tests, type checking and the production build. A credential-free runtime check
confirmed approved skills can be discovered/configured without a model request.

Three targeted research pilots used five worker turns in total: one failed
publication, then two initial/follow-up pairs. The first exposed noisy history
burying source references and an answer-only publication bypass. Both were
repaired. The second published and revised correctly but its assertion counted
research nested in a history receipt as fresh execution. Native parsing now stops
at actual protocol items, and evidence IDs are stable across runs. The final
pilot passed: initial publication 18.469 seconds, same-task revision 11.578
seconds, one hosted search, same URL, no new search on follow-up. Available
native usage is retained; invoice cost is unknown. Earlier failures are preserved
in data/worker-tools-native-research-pilot*.

The first frozen run at source 8a76b89d passed all eleven provider cases but
exposed one SQLite admission/telemetry race and a browser microphone fixture
reusing a stopped stream. Those failures are retained. Cleanup now finishes
in-flight short-budget SQL before closing, with one idempotent transient retry;
microphone requests get fresh tracks. The runner also launches missing privacy
and mobile stages. Final focused checks passed 39 cases, the offline suite
passed 656, and 700 measured offline admissions lost no observations. See the
[worker revision report](worker-tools-closure-2026-10-08.md).

The preceding repaired candidate was frozen at source
5f27ae33a5ff496049425a7f986def983fbe7dca2ab1f3f0ece624c9b2ac7625
(705 files). Its final admission passed at 3.668 seconds, coding/restart and
subscription/API handoff passed. Its combined run ended with 13 passes and four
accepted exceptions, but a backend launcher timeout and wake-only browser input
left M10 open. A local-only tail exposed a cancelled WAL connection setup and
database-file leak; no pass was synthesized. WAL setup now occurs at bootstrap,
and cancelled connection initialization finishes before close. Focused checks
passed, followed by all 661 offline cases and eleven timing checks. The final
browser recording included an existing substantive request and passed.

Final source c8a0aec7 passed all fifteen required combined assertions with four
accepted M8 exceptions. Backend: 673 passed, no skips; frontend: 192 passed,
types/build/result export passed; Android: 28 passed and built. Final ready
admission was 3689.1404 ms, priority tier with no fallback. Actual coding repair,
passing test and same-task restart passed; subscription ownership/restart and
real API handoff after simulated exhaustion passed unattended. Capture privacy,
browser, migration and descendant termination passed. The real mobile journey
used both screen roles and three sources, delivered/opened the notification,
reopened without a new run and updated the same result through UI continuation;
admission was 3473.7376 ms. Same-candidate browser proof was imported to avoid
another unchanged paid request. See data/worker-tools-m10-db-final/combined/acceptance.json.

Read-only readiness independently confirms implementation completion. Only
development owner-login configuration blocks rollout preparation; physical/OEM
testing, production opt-in and complete deployment backup remain deferred M11
work. No flags, account settings or credentials were changed and no data was
reset. Costs remain unknown. M8's four accepted exceptions remain explicit;
M11 deployment is not authorized.


### M9 complete; M10 one research-source failure — 2026-10-07

The [final checklist](m9-m10-closure-2026-10-07.md) supersedes earlier status.
Source cbb51a2e passed all 657 deterministic contracts, 191 frontend and 28 Android
tests, builds, migrations, capture privacy, process termination and production
browser checks. Actual subscription execution, active-owner competition,
supervisor restart and same-task API handoff passed; M9's evaluator passed.
Real coding failure/repair/pass/restart and the Android sourced research,
notification/reopen/same-task edit journey passed. Final admission: 4144.6398 ms,
actual priority tier, no fallback. The runner explicitly records four M8 accepted
exceptions and no longer schedules the waived bulk cohorts.

M10's final report has fourteen passing assertions, four accepted exceptions and
one failed backend assertion: 668 of 669 cases passed without skips. Food-research
publication failed after two discovery batches because chosen pages returned
403/challenge content. Jarvis reported the limitation without an unsupported
result. Ten of eleven provider cases passed; the failed observation is retained,
not replaced by the separate successful mobile research topic. No new login is
required. Stop unchanged paid reruns; investigate bounded source retrieval
locally before a targeted verification. Implementation readiness remains false.

### M8 accepted completion and remaining M9/M10 — 2026-10-07

The user explicitly accepted **M8 as completed with caveats**. The
[decision record](m8-accepted-with-caveats-2026-10-07.md) supersedes earlier
milestone status below without relabeling failed/missing automated checks.
Remaining development closure is M9's current subscription/restart/API handoff
proof and M10's final integrated regression/maintenance/report. M10 must first
align executable acceptance with this decision so waived bulk M8 cohorts are not
silently required again. Retain privacy, durable task, cancellation, recovery
and representative full result journeys. M11 remains deferred. This decision
used no provider calls and changed no executable source.

### M8 clearer-audio pilot — 2026-10-07

The user-requested [clearer-audio pilot](m8-hq-audio-pilot-2026-10-07.md) followed
37 passing offline checks. Three new recordings, three file-transcription
controls and two admission turns produced correct tasks at 3.052/3.500 seconds
on unchanged source 8c066a1c. Live still greeted at startup and imperfectly
recognized the notes prefix. The original recipe also transcribed correctly in
the file control, so recording quality alone is not an established root cause.
No broad paid rerun followed. M8 remains open; current gate evidence stays at
three of eight assertions pending processed-speech diagnosis and full acceptance.

### M8 final request and attached-sideband readiness — 2026-10-07

The [final-request report](m8-final-2026-10-07.md) retains twenty completed
capture pairs and five fresh continuations before a startup timeout. This
failure consumed no command audio; it differs from the earlier missing-prefix
transcript. Backend readiness now follows authenticated sideband attachment,
without depending on replay of a primary-connection startup event. Android
still requires its connected/open transport and durable current-generation
confirmation. All 117 focused offline tests passed. On frozen source 8c066a1c,
the correct recipe pilot took 4.909 seconds and stopped at its pilot limit;
the fixed ready07 case then passed at 3.534 seconds. The repaired capture run
completed eleven disabled baselines and ten enabled admissions, then enabled
sample eleven produced no user transcript or task despite successful startup.
Paid testing stopped. Native privacy passed eleven checks plus eleven contracts;
the required Android rebuild and all 28 tests passed. The current source has
three of eight passing assertions; full audio/cold/fresh/mobile/capture evidence
remains incomplete. The remaining blocker is intermittent speech delivery or
recognition, with the exact native-processing/provider boundary still unknown.
M8 remains open; no prior-source checkpoint substitutes for current evidence.

### M8 capture and mobile acceptance — 2026-10-06

The 2026-10-07 quiet-wake frozen checkpoint subsequently passed all twenty ready
cases, five cold starts, seven scenarios and final ready07 at 3445.699 ms.
Ready median/p95/max were 3909.222/5318.589/5764.868 ms, with zero failures in
that cohort. Seven of eight M8 assertions revalidate; capture performance stopped
on its fourth enabled sample after Live greeted and omitted the request prefix
from the observed transcript. The earlier local cleanup failures did not recur.
The full report exceeded the importer's two-million-byte limit; a bounded
eight-million-byte limit and diagnostic rejection reasons now have 48 passing
offline checks. This loader/test repair changes candidate identity, so retained
checkpoint passes do not close the new candidate. M8 remains open pending local
audio-boundary diagnosis and complete capture performance, followed by final
verification. See the detailed report for retained failure and usage counts.

[M8 progress](m8-progress-2026-10-06.md) records native wake staging/privacy,
ingredient recipe routing, mobile Send layout and plural image-tool repairs.
The local full delivery p95 is 313.075 ms across twenty network-blocked cases.
The prior 393 ms live slowdown occurred mostly in the managed provider stage;
two samples do not prove a regression. M8 now has eight required assertions
including native privacy and real result/notification/UI continuation; M10 has
nineteen. The complete mobile notification/reopen/edit and native privacy proofs
have passed on repaired candidates. Full cohorts stopped on unnecessary format
clarification, older-update substitution, and then a provider startup closure;
their failures remain recorded. Focused local repairs and small pilots precede
each changed-candidate attempt. Empty attention updates no longer inject
synthetic Live context; the two-startup pilot passed at 3.882/3.874 seconds.
Current M8 functional/capture acceptance is still open; see the linked report
for current source and evidence.

The contained-harness cohort later passed all twenty ready and five cold cases
(ready median 3.961 s, diagnostic p95 4.759 s), then follow-up startup failed.
The runner also hit local memory/resource failures; capture and final timing
did not run. Local cancellation-during-query tests reproduced a SQLite cleanup
failure under SSE cancellation. The 2026-10-07 repair protects lookup/connection
cleanup, retires terminal Live transports and bounds candidate traversal/hash
memory, including Android's legitimate data package in identity. All 129 focused
local tests passed; full offline validation precedes any further paid attempt.

### M6/M7 closure and revised M8 latency — 2026-10-06

[M6/M7 passed](m6-m7-closure-2026-10-06.md) on frozen source b5f5c8f7f115e44dde051f463186c92e55f16374c3c1e07169952ca17888c2e8. All 633 required deterministic backend contracts passed without skips. The shared manifest excludes optional diagnostics and retains broad provider/browser checks for M10. Atomic device authentication and a competing-writer regression repair the observed SQLite heartbeat conflict.

Real supervised coding produced failing/passing/restart exits 1/0/0 on one durable task and two runs; fresh Job Object descendant-stop proof passed. One final GPT-Live/managed Luna trial admitted the correct task in **4167.892 ms** and observed priority tier. M7's evaluator passed. This also passes the user's revised M8 latency assertion: one final ready admission below 4500 ms, without a p95 claim. M8 functional/cold/capture, current M9 subscription/API handoff and combined M10 remain open; M11 stays deferred.

Only two native coding runs and one voice trial used paid access. Available voice usage: 15 Live seconds, 7083 input/106 output managed tokens, including 9 reasoning tokens. Coding model-request counts and invoice cost remain unknown. No extra live sweep or retry ran; no user intervention or application-data reset was needed. Findings below retain their original scope and are superseded where this closure provides fresh evidence.

### Earlier gate reconciliation — 2026-10-06
The [current gate review](implementation-gate-review-2026-10-06.md) checks the
roadmap against executable acceptance requirements and raw reports. M6/M7/M9
implementation is present but current-candidate acceptance remains open. M8's
largest gap is the full managed functional/ready/cold/capture cohort, not basic
voice feasibility. M10 requires all 17 assertions on one frozen candidate. The
latest pilot uses the current executable source, but only its real priority-tier
observation passes a complete assertion. Earlier full runs are different
candidates and cannot close current gates. SQLite contention remains a real
regression risk despite subsequent passing tests. Saved subscription login and
funded voice access are observed; no new account action is currently requested.
The acceptance runner also has a confirmed false blocker: opt-in diagnostics
skip without their report flag while required backend checks reject skips.
One network-free runner probe reproduced it; executable repair remains pending.
M11 remains deferred. No paid calls or full acceptance suite ran for this review.
### Final managed admission pilot — 2026-10-06
After 10 offline checks passed, the user-requested final ready07 voice trial
admitted one correct task/run in **3.7746 s** from speech end to Android durable
receipt. This is below four seconds and within the prior managed 3.551–3.918 s
range, but above the two-second target. Android relay intervals were **2.295 s**
to delegation, **1.093 s** to validated tool, and **0.387 s** to admission.
Managed selection started before correlation completed. There were no provider
errors/warnings, duplicate dispatches or tier fallbacks; worker inference and
image uploads were absent. One voice trial consumed 15 reported Live seconds
and 3390 input/72 output completed-selector tokens; remaining continuation/setup
usage and exact cost are unknown. No additional paid trial followed.
The one-sample pilot does not establish p95 or full acceptance. Its incomplete
corpus/gate statuses remain retained. See [the final live timing report](managed-admission-final-pilot-2026-10-06.md).
### Managed routing only — 2026-10-06
The user selected managed Responses delegation as the sole routing path.
Client selection, copied conversation history, result futures, mode-specific
response handling and the routing configuration switch are removed. Local
turn correlation, frozen tool validation, durable admission, capture preparation
and managed continuation remain. The acceptance manifest now has one emulator
profile; `verify-managed-admission.py` replaces the dual-route launcher without
a compatibility alias. Offline timing consumers use only managed profiles.
Completed stream outputs are recovered correctly from empty terminal envelopes
in diagnostic analysis.
The initial focused suite passed 72 checks. The broader run had 83 passes and
two SQLite/telemetry contention failures; both failed checks passed in a separate
isolated run. The repaired launcher harness passed 24 checks. The final combined
adapter, managed delegation, diagnostics, harness, acceptance, coordinator,
intent, task and screen-collection regression suite passed **129 checks in
121.59 s**. Completed managed
local-flow profiles had 20 measured admissions, median **662.19 ms**, p95
**1114.66 ms**, maximum **1476.28 ms**, with zero admission failures in those
profiles. Provider delay is mocked; HTTP/Android receipt is excluded. The broader
run remains failed evidence and no live p95 or improvement over other host-load
conditions is claimed. No paid calls, data resets or credential changes occurred.
See [implementation and remaining acceptance](managed-routing-only-2026-10-06.md)
and [the local timing breakdown](managed-offline-timings-2026-10-06.md).
The executable candidate changed; historical live passes are supporting evidence,
not current acceptance. M8/M10 stay open and M11 stays deferred.
### Matched managed/client comparison — 2026-10-05
After 68 offline adapter/client/harness/diagnostic tests passed, the explicitly
requested four voice trials used identical ready07 PCM, source and runtime, with
the same model/effort/tier/tools and only route/isolated-path settings differing.
Managed/client/client/managed order yielded **3.551 / 5.456 / 4.791 / 3.918 s**.
All four created exactly one correct task and queued run, with Android receipts,
no provider errors/warnings, no duplicate dispatch and no tier fallback.
Managed's mean was **3.734 s** versus client's **5.123 s**, a **1.389 s** advantage.
Both managed observations were below four seconds, but two samples do not
establish p95 or pass the ready-latency/corpus gate. M8/M10 remain open.
Managed overlapped selection with local transcript correlation. Its delegation
to validated-tool relay averaged 1.034 s versus client's 2.693 s. Remaining
managed delay concentrates in the 2.471 s mean input-end-to-delegation interval,
which includes unmeasured activation-buffer residence. Argument streams were
148–233 ms. No runtime setting or voice backend changed. Available usage was
eight completed selectors (23,627 input/406 output tokens), 60 reported Live
seconds, and zero worker/orchestration/new-TTS inference; setup usage and exact
cost remain unknown. No further paid trial followed the four requested tests.
The bounded tail clipped one spoken confirmation, whose complete selector
output is retained. See [the matched comparison](admission-routing-paired-2026-10-05.md).
### One-trial optional-schema live verification — 2026-10-05
The user-authorized single client voice trial confirmed returned `strict:false`,
only goal/intent ID in the 202-character call, and exactly one correct durable
task with a queued run. No duplicate dispatch, persisted provider error, tier
fallback or image upload occurred. Raw stream export now succeeds: both selector
responses and all retained argument chunks are available. Ready admission was
**6.748 s**, still failing the four-second gate; no p95 follows from one sample.
First arguments arrived 2.839 s after HTTP start; their stream took 0.221 s.
The remaining delay is mostly pre-delegation and selector response startup.
Usage was two completed selectors (4764 input/79 output tokens), 16 reported
Live seconds, and no worker/orchestration/new TTS inference. No further paid trial
followed. A duplicate transport-error report after clean session close produced
one fenced stale-session warning; the subsequent receiver correction suppresses
that report after committed closure and passed 46 offline adapter/client/diagnostic
tests. The later cleanup candidate is not claimed to have been tested live.
The harness clipped the spoken confirmation during its bounded tail; the full
selector confirmation is retained. See [the verification report](admission-optional-schema-verification-2026-10-05.md).
M8/M10 remain open; broader functionality was not tested by this ordinary request.
### Voice/selector output investigation — 2026-10-05
The subsequent user-requested correction explicitly sends `strict:false` for
every function in the shared voice catalog, keeping optional fields omittable
in managed and client mode. Dispatch descriptions and selector instructions
clarify required goal/intent ID and optional defaults. Local completed-call
validation still rejects missing required fields, invalid values and unknown
top-level fields. No paid requests accompanied this correction; latency impact
remains unmeasured. Published Fast mode is the existing `priority` tier. Ultrafast
would require changing from Luna to Astra (or Sol preview), at substantially
higher token prices; no tier/model change was made. See the linked report below.
The correction's adapter/client/diagnostic/intent/preferences/workflow suite passed
67 offline tests.
Read-only retrieval recovered both earlier managed selector output arrays. The
provider normalized `dispatch_task` to `strict:true`, requiring all seven fields
although our local schema requires only goal/intent ID. Its 564-character call
could retain the same goal/ID in 214 characters; this is a payload opportunity,
not a measured latency improvement. First-call reasoning counts across available
trials were 8–38 tokens; excessive thinking is not established.
Two targeted diagnostic client trials admitted correct tasks in 7.136/6.013 s,
but both lost raw stream exports. Those failures remain retained. Client responses
were not stored for retrieval, so exact first-delta/output throughput is unknown.
The final opt-in recorder now checkpoints provider termination before stale
coordinator callbacks and flushes on adapter close; verification is offline only.
The final focused recorder/adapter/client/harness/acceptance run passed 70 tests.
No further paid generation followed the second export failure. Additional usage
is four completed selectors (9315 input/279 output tokens) and 33 reported Live
seconds; eight read-only retrievals generated no inference. Exact cost is unknown.
See [the output report](admission-model-outputs-2026-10-05.md) and its full retained
output artifact. Correct optional-schema handling and validate locally before a
future targeted provider check. No route, p95 or M8/M10 pass follows from this.
### Targeted live admission pilot — 2026-10-05
After credits were restored and the user authorized economical end-to-end tests,
two real voice trials each admitted one correct notes-plan task. The managed
trial's expanded debug timing log was truncated, so its receipt remains failed
acceptance evidence; a forensic prefix indicates approximately **4.76 s**.
The locally repaired logger produced a complete client receipt at **7.71 s**.
The pilot stopped at the four-second limit, before further cases or broad suites.
These single observations establish no p95 or reliable mode choice.
The clean trace separates **2.72 s selector HTTP-to-headers**, **1.70 s headers-to-tool**
and **0.30 s tool-arrival-to-SSE**, including validation. Speech end to delegation
relay was approximately **2.25 s** in both trials. Proactive capture ran concurrently;
ordinary tasks uploaded no images. Further work should instrument final audio
handoff and selector connection phases before additional paid tests. Logger and
pilot fail-fast/capping regressions pass locally. Recorded usage is 3 completed
selector responses (9158 input/337 output tokens), one additional started request
with unknown usage and 67 reported Live seconds; exact cost is unknown.
See [the live pilot report](admission-live-pilot-2026-10-05.md).
M8/M10 remain open. The former exhausted-credit error did not recur in these trials;
current-candidate subscription/API handoff acceptance is still required separately.
### Admission timing follow-up — 2026-10-05
The latest local revision replaces full-history task-ID resolution with task-row
reads, removes lookup-only gateway/resolver commits and reuses already selected
admission turns for result lookup. Mutation services retain their original locks,
status checks, durable admissions and receipts. Result-page streams now wake on
local committed events with broadcast, page scoping, replay and missed-hint recovery.
Cross-process worker publications still use the bounded 500 ms recovery scan;
the local result-delivery improvement is not a cross-process latency guarantee.
[Read/delivery diagnostics](admission-read-2026-10-05.md): interleaved result
delivery p95 **432.28 → 20.10 ms**, 20 observations per wait mode. Current
gateway median/p95: plain **73.91/76.45 ms**, document **94.12/193.33 ms**,
screen-ready **149.40/176.87 ms**, continuation **103.49/122.60 ms**, replay
**7.58/7.90 ms**, 60 observations each. Continuation median decreased from
118.82 ms in the previous cohort; document tails worsened, so this is not a
universal improvement or controlled causal estimate. Full local flow p95 is
**281.92 ms managed / 315.76 ms client**, 20 observations per mode.
New spans separate flush, transaction commit, connection open/setup and read
cleanup. Flush medians remain under 1 ms. Native SQLite WAL/FULL commits have
p95 **5.04 ms** in 60 observations. A focused 50 ms loop-pulse cohort captured
a **115.33 ms** commit await with three overlapping pulses and maximum pulse
lateness **12.45 ms**; the main loop was not frozen for the entire await. Driver
thread scheduling and Windows filesystem/checkpoint activity remain unseparated.
Earlier 10 ms pulse diagnostics are exploratory: that cadence is below Windows'
event-loop clock resolution and is excluded from the final pulse conclusion.
Verification: **188 offline regressions passed in 207.75 s**, full profiling
**12 passed / 2 deselected in 93.51 s**, final loop cohort **3 passed in 35.34 s**.
No paid API calls, account/device changes or application-data reset occurred.
The local-first/small-live-pilot policy is now persistent in AGENTS.md and the
runbook. More credits do not trigger broad development paid reruns. M8/M10
remain open pending current-candidate live speech/provider/Android acceptance.
The subsequent trigger-based revision removes the normal SSE polling delay and
the optional-image readiness deadline. Local commit signals broadcast only after
the outer transaction commits; snapshot-before-read prevents lost wakeups. Recovery
rescans remain for cross-process/missed hints. Required captures still time out
after two seconds; optional roles are checked without waiting when the collection
freezes, after required readiness. Ownership, lock/expiry/provenance checks and
promised-upload failure behavior remain unchanged.
New offline evidence in `data/admission-timing-triggered-2026-10-05`: 20 SSE
samples median/p95/max **8.76/10.09/15.78 ms**, compared with the earlier
256.62/436.11/522.32 ms. Optional latest never-ready preflight dropped from
2037.10 to **47.75 ms** (single diagnostic observation); required-current timeout
remains **2020.35 ms**. These are local diagnostics, not a live admission pass.
The focused wakeup/capture benchmark suite passed eight tests, covering broadcast,
rollback/savepoints, read/wait races, cancellation and missed-hint recovery.
Final targeted regressions passed **78 tests in 86.79 seconds**. The
[triggered admission report](admission-triggered-2026-10-05.md) records the current
measurements and distinguishes them from the earlier polling baseline below.
The subsequent telemetry revision now queues capture/result observations into
bounded independent batches, without awaiting SQLite on admission. Capacity is
512 queued jobs plus a 32-job active batch; the background batching window is
20 ms. Queue health exposes losses/pending work and bounded timing windows.
Shutdown drains before engine disposal; busy/locked retries are bounded and
idempotent. Durable task/admission/event commits retain their original behavior.
Allocation/notification run/revision metadata is frozen, and publication lookup
uses the owning run even if another run completes before diagnostic persistence.
Current offline regressions: **180 passed in 259.17 seconds**, including actual
isolated API-process restart/restore and queue failure/overflow/cancellation/drain
coverage. Full gateway/routing/capture/SSE profiling passed **10 tests in 75.67 s**;
20 complete local delivery samples per mode produced receipt p95 **284.79 ms**
managed and **319.32 ms** client. Gateway cohorts retain 60 measured samples per
case; every queued job drained with zero failed/dropped/pending jobs. These fixture
runs exclude live ASR/provider/network/Android and do not close M8/M10. No paid API
calls, account/device changes or application-data reset occurred.
The [telemetry comparison report](admission-telemetry-2026-10-05.md) retains the
interleaved awaited/queued experiment and initial sequential cohorts. The initial
baseline had large disk/OS tails even for plain/replay cases; its entire drop must
not be attributed to the queue. Next investigate repeated durable transactions
and related authority reads while preserving ownership, replay and cancellation.
The controlled interleaved cohort passed **2 tests in 51.39 s**, with 40 measured
samples per case/mode: screen median/p95 **192.19/221.20 → 154.62/168.25 ms**;
document **114.87/137.48 → 101.44/133.79 ms**. Plain/replay are essentially
unchanged. Continuation p95 worsened **110.60 → 281.18 ms**; its three largest
outliers have zero observation calls and 131–262 ms durable commit stalls. Keep
those outcomes; the underlying disk/OS cause remains unisolated. Across queued
gateway/paired/capture cohorts, **814 jobs completed in 499 batches**, with no
failed/dropped/pending jobs after drain.
A final publication-metadata correction prevents an earlier failed operation
being attributed to a later successful revision of the same run; that revision
stays unknown. Its focused queue/result/restart regressions passed **17 tests in
28.63 s**. No admission code changed after the measured cohorts.
[The earlier baseline timing report](admission-timing-2026-10-05.md) records 300 gateway
measurements across three independent SQLite trials, 40 routing measurements,
20 controlled SSE samples and five pending-capture diagnostics. No paid API
calls were made. High-resolution server spans now separate task commit from
observation/focus/receipt work; Android capture encoding, status reports, stream
receipt/dispatch and upload completion have additional metadata instrumentation.
Timing metadata stays out of provider model inputs. Per-turn spans are isolated;
replay keeps its original commit. The audio harness retains the new boundaries.
Gateway median/p95: plain 75.53/77.95 ms, document 115.76/126.77 ms,
screen-ready 192.31/408.42 ms, continuation 108.89/145.47 ms and replay
7.70/7.87 ms. Zero-delay provider fixtures through real routing/coordinator/journal
take median/p95 214.02/250.42 ms managed and 216.65/235.95 ms client.
Before the trigger-based revision, commit-to-SSE emission added 256.62/436.11 ms
with polling every 500 ms. A never-ready optional latest image added 2037.10 ms before
proceeding with the valid invocation image. These are separate diagnostic
distributions, not additive percentiles or current live acceptance passes.
Optional-image waiting and event-stream delivery are now corrected. Prioritize
synchronous observations and repeated DB transactions next. Keep required-current capture,
durability, provenance, leases/fencing and cancellation. API/ASR/one-way transport
timing remains unmeasured for this source; the earlier multi-second combined
interval cannot be assigned entirely to provider delay. M8/M10 remain open.
Verification: final targeted backend regressions **95 passed** (88.94 s),
profiling cohort **7 passed** (65.49 s), pending capture **1 passed** (6.89 s).
The earlier collection/capture-inclusive regression suite passed 78 cases.
Android **27 JVM tests** and the debug APK build passed. No application database
reset, account change or paid live run was performed.
### Durable-task implementation and prior acceptance
- Removed cutoff-proof settings, validators, runtime checks and M9/M10/rollout gates.
  Provider account settings own limits; actual auth/quota/billing errors remain.
- Screen work freezes invocation/latest roles with provenance and missing roles.
  First delegation proactively requests a turn-bound current capture. Distinct
  uploads run concurrently; identical content shares a blob while retaining roles.
  Explicit-current failure, overlap/replay, optional missing context, expiry/lock
  fencing and partial promised-upload failure have regression coverage.
- Both routers dispatch complete schema-validated calls. Safe timeline/completion
  evidence bypasses transcript settling; ambiguous/incomplete input still waits.
  Managed mode exposes response-start receipt; client mode records request start.
  Stage reports keep Android/server clock domains separate.
- Native Windows uses ordinary inherited-permission task/test directories,
  project-local scratch, explicit roots and elevated native provisioning. Real
  Python/Node tempfile/subprocess workloads and nonzero exits pass. The original
  unelevated failure remains diagnostic evidence; no third-party patches or broad
  parent ACL changes were made. Explicit trusted command mode remains available
  and correctly declares ordinary Windows-user access; denied native commands
  never retry more broadly.
- Acceptance hashes executable workflow definitions/instructions, excludes report
  documentation and uses a shared manifest of declared gate-specific test settings.
  Audio and capture-performance stages execute separately. Startup uses the
  enabled-minus-disabled p95 difference; frame regression compares normalized
  equal-window observations against baseline rather than failing baseline alone.
- Dedicated subscription device-code sign-in succeeded on 2026-10-04. The saved
  private profile reports account type ChatGPT; tokens are reused/refreshed by
  Codex across runs. No credentials were copied from the desktop profile.

The broader native acceptance run produced repeated Windows elevation prompts for isolated
execution roots. This violates the bounded native-setup objective despite the earlier native
workload pass. That run was stopped; its partial evidence is not acceptance. Local setup now
explicitly selects `TASK_COMMAND_MODE=trusted` and `TASK_WINDOWS_SANDBOX=unelevated`.
Commands inherit the user's Windows access. Broker edits, journaling, fencing, directory
leases and Job Object descendant termination remain required. No rejected native command
is retried automatically. Account-provided app tools caused subscription inventory rejection;
Jarvis hosts now disable apps/plugins and retain the existing tool-inventory fail-closed check.

Current focused verification passed 67 admission/capture/command/acceptance tests,
then 32 collection/capture cases including the delegated-turn binding regression.
The routing/audio fixture suite passed 44 tests; the final upload-order fixture
regression and related audio checks passed 14 tests. Android unit/build succeeded.
Actual trusted-mode subscription execution, simulated quota handoff and real
coding fail/repair/pass/restart both passed (2 live tests in 63.20 seconds).
Those earlier observations are diagnostic until repeated for the final candidate.

The first full comparison attempted 20 ready admissions and five cold starts per
mode. Managed: one failed ready sample, median 4098.40 ms, p95/max 5249.06 ms;
one failed cold sample. Client: one failed ready sample, median 4757.55 ms,
p95/max 5868.98 ms; all five cold starts passed. Neither route met the ready gate.
Managed selector observation-to-arguments median/p95 was 1281/1813 ms; client
request-to-arguments was 1515/2329 ms. Arguments-to-commit median was 219 ms
in both. These are within-server durations; Android end-to-end measurements
remain separate. Managed source changed during that diagnostic run and cannot
serve as final acceptance.

The comparison exposed fixture issues: question setup raced promised uploads,
scenario tasks were not focused, and the fixed color-check region was covered
by the Jarvis overlay. Fixes preserve actual uploads, task state and captured
pixels rather than synthesizing admissions. Fresh performance observations now
use independent activations so twenty windows cannot outlive image expiry.
Vague outcome requests explicitly require clarification in both routing prompts.

The first combined run under `data/implementation-final-m10` was stopped
before completion after review found a production task-reference scoring defect:
recency could outweigh a more complete descriptive match, selecting the wrong
Python task for cancellation. The resolver now prioritizes descriptor coverage
and specificity before recency. Its regression and related cases passed
24 tests. The preparatory full backend suite passed 590 tests with 12 opt-in live skips
in 637.67 seconds. No pass is claimed for the stopped run. The replacement
frozen-candidate combined run is `data/implementation-final-m10-v2`; it enables
the live provider/browser checks and executes the audio/capture stages explicitly. The corrected managed corpus completed on the frozen source: 17/20 ready
admissions, successful-sample median 4241.66 ms and p95/max 5774.70 ms. All five
cold starts and cancellation, question answer, ordinary continuation,
clarification, nonmatch conversation and fresh-screen continuation passed.
Ready02/14/20 and the multiple-outcome case failed. Recorded complete transcripts
in recipe/multiple cases still produced unnecessary clarification or list_tasks;
ready20 also lost words. Voice provider tier evidence passed. Capture performance
was stopped after two pairs to compare client mode on this exact candidate before
selecting the final combined route. That partial capture run is not acceptance.
Managed server-stage measurements on 17 successful admissions: receipt-to-arguments
median/p95 1375/2094 ms, observed selector start-to-arguments 1344/2062 ms,
and arguments-to-commit 188/375 ms. Subtracting each server processing duration
from its own Android end-to-end duration leaves a combined unassigned interval
with median/p95 2729/3727 ms (upstream speech/provider/transport plus downstream
delivery). This is subtraction of durations, not unsynchronized timestamps; it
does not isolate provider delay from network or client delivery.
Current client comparison: `data/implementation-routing-client-current`.
It completed with 19/20 ready admissions (median 5001.26 ms, p95/max 7441.65 ms),
4/5 cold starts (successful-sample median 6693.07 ms, p95/max 8626.00 ms), and
passed control/question/follow-up/clarification/multiple/fresh cases. Ready14,
cold ready04 and nonmatch failed. Ready14 asked which ingredient source; the
other failures had missing words in the captured transcript. No route is fully
reliable or below four seconds after the focused correction. Managed remains
the existing default, not a claimed qualifying winner.

The final combined run is `data/implementation-final-m10-v3`. It loads the valid
same-source managed audio observations under the declared emulator profile and
executes the full separate capture-performance, live provider, backend/frontend,
Android/browser and recovery stages. It finished blocked: coding, frontend,
Android (27 tests), browser and migration passed. Backend had 599 passes and
three live failures. Subscription execution failed on that attempt; saved-login
execution and simulated quota/API handoff subsequently passed without human
action. All three process-tree tests passed, but their report lacked the shared
configuration declaration and was correctly rejected. Capture completed twenty
disabled/enabled pairs, with six enabled admission failures and no usable fresh
performance observations after initial recipe admission failed. Missing data is
not a capture performance pass.

The follow-up repair removed a shadowing settings import in the background host,
declared process-tree isolation in the shared manifest, and preserved bounded,
credential-redacted provider errors in failed task summaries. Resumed workers
now receive the frozen run input and durable updates, including answers committed
after context was pinned; native history alone cannot contain those user turns.
The answer/context regression and acceptance tests passed (22 tests), the live
background/subscription/process checks passed (19 tests), and real sourced
research with same-result continuation passed after this repair (48.30 seconds).
Routing mode selection also requires the functional corpus and independent cold
starts rather than ready-sample counts alone.

The replacement frozen run is `data/implementation-final-m10-v4`, including new
audio and capture observations and every full-stack/provider/recovery stage.
It finished blocked on the unchanged candidate
`8b267900d6a90a269338635c31663e9bf766e74e4c9cfb8b1c598bddef0b9f30`.
Evidence: `data/implementation-final-m10-v4/responses/combined/acceptance.json`.
Frontend unit tests (191), types, application/result-shell builds, Android (27),
migration rehearsal and declared process-tree evidence passed. Backend finished
with 593 passes and 11 failures in provider-dependent tests, with no skips.
Actual coding and subscription-to-API continuation failed on the no-credits error.
Browser voice smoke also received HTTP 429. All twenty ready and five cold voice
attempts failed at startup; twenty disabled and twenty enabled capture attempts
also lacked a working voice session, with no fresh performance samples. Their
latency/frame gates remain blocked or unknown; no passing values are synthesized.
Earlier candidate observations remain diagnostic.

During v4, every voice startup failed before delegation with HTTP 429. A direct
authorized GPT-Live request using a fresh browser-generated offer confirmed
`insufficient_quota` / `credit_balance_exhausted`: the configured API account has
no credits remaining. Evidence: `data/implementation-live-429.json`. The frozen
run therefore cannot establish current admission latency or capture overhead.
The account subscription does not supply this API balance. The human action now
required is restoring credits in the API organization used by Jarvis's configured
key, or explicitly selecting another already authorized funded API account. No
automatic top-up, spending-limit integration, credential copying or re-login is
introduced. The current v4 subscription test also completed its subscription-backed first
run, then failed on the actual API continuation with the same no-credits message.
Device-code authentication itself is working on the frozen candidate. Restoring
credits alone does not resolve the earlier measured routing/latency misses.

M10 and its ready-path performance gate remain
open. M11 physical-device testing remains deferred.

---

# Historical implementation progress — 2026-10-03

M6–M10 remain open. M10 is the implementation-completion gate; this report does
not declare completion or authorize account/device/production enablement. M11
remains deferred.

## Historical implemented-contract snapshot
The snapshot below retains the contracts described before the later admission
revision. Its client-selection, terminal-array reconstruction, standalone capture
and provider-cutoff statements are superseded by the current revision above and
the gate review. They are not current requirements or runtime paths.

- M6: the elected supervisor commits generation/child/run/attempt/token-bound
  assignments. Supervised children consume one assignment and cannot claim the
  queue. The general broker discovers leased, configured filesystem projects,
  reads/edits bounded source and journals sandboxed build/test argv. Nonzero
  test exits are durable results. In-flight/uncertain effects block new mutation
  keys across execution runs; interruption never authorizes replay. Workflow
  and background tool inventories remain narrower.
- M7: durable Live turn/outcome receipts deduplicate dispatch, workflow start
  and continuation across delivery IDs and gateway instances. Route/argument
  conflicts reject. Continuation pins its target before committing; recovery
  returns the committed turn/run after a crash. Shared execution preferences
  validate model/effort and require an override reason. The public fresh-screen
  gateway binds current device/session/activation state. Delegated Responses
  request priority, record actual tier/usage/latency, recover terminal-output
  calls and permit one explicit unsupported-tier fallback per session, including
  an explicit rejection during initial HTTP session creation. Session-domain
  replay now identifies the Live session rather than the individual event; migration
  0036 retains cursor/deduplication identities. Generic tool result kinds advertise
  only document.v1. Both API surfaces use the same current task presenter, so a
  later Live activation remains readable after work is admitted.
- M8: API37 custom-wake capture stages once the assistant window is visible.
  Screenshot/basic context and separately opted-in rich structure have distinct
  flags. Fresh capture preserves the activation/session. Pending-run upload
  failure terminates that run without failing a different current run. Debug-only
  prerecorded input traverses wake inference, handoff and WebRTC input; playback,
  RTP, durable admission IDs and capture timings are observed on Android's clock.
  A versioned corpus and ready/cold harness are present. Validated API speech
  replaces this host's empty offline rendering. Audio transfer uses adb push,
  private copy and SHA verification. Real API37 WebRTC requires the normal
  ACCESS_NETWORK_STATE permission. Android scopes global events to the active
  session, preventing a superseded previous session from closing its replacement.
  Client selection is implemented as a separate session-pinned mode using the
  same gateway, bounded dialogue and exact completed-turn correlation. Unknown
  HTTP outcomes are not retried. Scenario setup uses normal task services in the
  explicitly selected isolated DB, and command-only continuation retains an
  existing ready activation. Client routing waits for the final timeline-matched
  transcript fragment before freezing its HTTP input. Waiting-for-worker and
  paused tasks participate in natural active-task resolution. Optional capture
  fields accept null, allowing ordinary same-task follow-ups without asking for
  another screen. Missing or failed fixed corpus cases cannot pass; the nonmatch
  check also requires the actual joke request in the received transcript.
  Native Live assistant transcripts retry task updates deferred by a busy floor,
  while an explicit user-speaking observation still fences assistant history.
  Bounded attention facts discard redundant metadata before shortening semantic
  text, preserving the actual question and acknowledgement identities. The screen
  harness preserves remote ADB argument boundaries, waits for screen B's real
  draw acknowledgement and checks both uploaded panel regions away from lettering.
  The performance collector uses twenty real screenshot-disabled baselines,
  twenty activation uploads and twenty fresh continuations, retaining failed
  observations and restoring the emulator's original screenshot setting. Debug
  health checkpoints measure native playout underruns, process/system ANR data,
  and window frame deadlines. First-draw data remains separate from later-frame
  jank; missing counters, no later frames or dropped observations block the gate.
  Reads run off the audio/UI paths without holding the input-buffer lock.
  Authenticated Android request construction also decrypts the device credential
  on IO rather than the assistant UI thread; credentials are not cached as a
  performance workaround. Client origin matching uses the shared bounded
  timeline rule, while pending fragments and ambiguous/used origins still reject.
  Unresolved origins retain bounded timing diagnostics without transcript text.
- Admission focus writes acquire the SQLite writer before reading/updating the
  shared state row, preventing duplicate singleton insertion and lost references
  across concurrent gateway instances. Provider close waiters release only after
  durable disconnection, and cancellation before sideband startup reports closure.
- M9: subscription selection uses a separate private profile and one durable
  worker/background execution-and-refresh owner. Exact process exit is required
  before release/transfer. Busy/unavailable capacity selects authorized API
  fallback. Terminal structured auth/quota/rate denial selects API at the next
  explicit boundary; unknown stream loss does not. Missing background auth before
  a turn permits one fallback after the contained host exits. Compatible native
  bindings require the same profile; otherwise the same task uses portable context.
  The obsolete budget boolean has been removed. Unattended background decisions
  require current candidate-bound provider-cutoff evidence on every invocation.
- M10: frontend type-check is required; test fixtures are repaired without
  weakening production types. The acceptance CLI composes existing suites,
  builds, process-tree/provider checks, selected emulator audio and migration
  rehearsal. Missing/skipped/stale/different-candidate evidence blocks readiness.
  Readiness evaluates evidence and keeps deployment authorization separate.
  Audio evidence verifies the authenticated running backend's immutable startup
  candidate, selected database and instance before and after measurement.
  Different CLI/backend configurations, source changes or backend restart reject;
  reports cannot label an isolated backend using caller defaults.
  Its composed audio invocation prepares isolated scenario prerequisites instead
  of depending on manual question/control/follow-up setup.
  Additive migrations 0034–0036 were applied to the configured development DB;
  migration 0036 is tested for replay and included in current isolated runs.
  Configured development migration status was checked after writers stopped;
  no application data reset or credential/account configuration change was made.

## Verified local results

The earlier aggregate run passed 531 backend tests (12 explicitly skipped live
checks), all 191 frontend tests, required frontend types/builds, Android tests/APK,
process containment and migration rehearsal. It is historical evidence under
`data/acceptance-m6-onward/acceptance.json`.

This continuation passed focused checks of session replay/backfill, shared state,
client selection, audio transfer/scenario setup and acceptance containment. The
Android debug APK and unit tests passed after the continuation seam. A real
browser recorded-wake smoke passed in `data/browser-live-retry.xml`. The second
aggregate run passed 550 backend tests with 12 opt-in skips, browser, Android,
types/builds, containment and migration rehearsal. It detected a frontend test
inheriting the smoke audio environment, now isolated with restored test-local
environment variables; that test passes with the actual smoke setting present.
Its source changed during the run, so it cannot attest to the current candidate.
The third aggregate run exposed a concurrent `jarvis_state` insertion race and
a browser shutdown race; neither is treated as a pass. Both are repaired with
focused tests. The real browser retry `data/browser-close-race-fixed.xml` passed
the exact-session close in 18.468 seconds. Coordinator test teardown now stops
DB work before closing its event loop. The fourth aggregate was stopped before
completion to finish these fixes. The fifth aggregate passed 557 backend tests
with 12 opt-in skips; the sixth passed 560 with 12 skips. Both also passed all
191 frontend tests, types/builds, Android tests/APK, browser, result shell,
containment and migration rehearsal. Source changed during both runs, so their
candidate-stability assertion failed and they cannot attest to the final source.
The frozen-source runner `data/acceptance-m6-onward-current-7/acceptance.json`
completed with its candidate matching source/configuration at that checkpoint. It
passed 562 backend tests (12 opt-in provider checks skipped), all 191 frontend
tests, 25 Android unit tests and a clean APK rebuild, the real recorded-wake
browser smoke, frontend types/builds, result shell, process containment and
migration rehearsal. There were no test failures. Its exact candidate ID is
`a0709bddded45c1d4bccfcb4743db9ecfece704651c5a9a51dedceba210e9167`.
The backend assertion and M10 readiness correctly remain blocked by required
skipped provider checks and missing current-candidate live/emulator/account
evidence. Stable source does not turn those missing checks into passes. The
performance collector and runtime attestation above landed after that checkpoint.
The final aggregate `data/acceptance-m6-onward-current-8/acceptance.json`
passed 567 backend tests with 12 opt-in provider checks skipped, all 191 frontend
tests, 27 Android unit tests and a clean APK rebuild, the real recorded-wake
browser smoke, frontend types/builds, result shell, process containment and
migration rehearsal. Its candidate was checked again after completion and still
matched the source/configuration at that checkpoint:
`0352b10e1c858abcd621e2565c00acb0b39384360294957efe0a14778c0cabcb`.
There were no regression failures. Required provider skips and absent
current-candidate live/emulator/account proofs keep its backend assertion,
implementation readiness and enablement readiness blocked.
The bounded follow-up fixes below supersede that aggregate source; its counts
are retained as historical regression evidence, not a pass for newer code.

Real custom-wake API37 screen callbacks, RTP, assistant playback and client
receipt of durable admission were observed in `data/real-audio-probe-11`. The
managed repeated run `data/real-audio-corpus-13/samples.json` admitted 18 of 20
ready cases and all five independent cold cases. Its passing-ready p95 was
5973.928 ms, and two recipe requests did not admit. Because failed samples remain
required, this is a failed M8 gate, not a p95 pass. Priority was actually reported
by the delegated provider; costs unavailable from it remain unknown.

After the two measured managed catalog iterations, client selection was exercised
under `data/real-audio-client-probe-14` and `data/real-audio-client-corpus-15`.
The initial client sample admitted durably at 6573.731 ms. Client mode remains
optional; the configured default is managed Responses. Diagnostic run directories
are not imported as current-candidate readiness attestations.

The client repeated run admitted 18 of 20 ready cases and all five cold cases;
its passing-ready p95 was 8397.425 ms. This also fails the M8 gate. Subsequent real
audio in `data/real-audio-scenarios-17` durably answered the pending recipe question
and created a new execution run under the existing notes task. Spoken clarification
was observed. The nonmatch transcript lost the joke request; the strengthened
check prevents promoting its earlier keyword-only pass. In the isolated retry
`data/real-audio-scenarios-18`, the intended waiting Python task was durably cancelled.
The multiple-task request was transcribed as “two separate lists” and answered
conversationally, so it failed. Fresh-screen preparation did not voice the pending
question before screen B and remained blocked. Real audio exposed a native
floor-release retry gap and then semantic question text being discarded by the
append-fact bound. Both were fixed and tested. Native transcript question
segments are checked together; the fixture now confirms its draw before the
continuation rather than assuming its broadcast completed.

`data/real-audio-scenarios-24/audio/acceptance.json` passes the real fresh-screen
scenario: blue screen A with its ingredient text, actual spoken vegetarian
question, then red screen B with different ingredients. The foreground images
were visually inspected. Command-only speech retained Live session
`1ad1c486-7a24-4612-bfcc-51e9c8460532` and activation
`efb75518-fad8-43d9-9997-aa5afa1552e2`, admitted a new run under task
`ab75fca4-bf9f-46d8-9350-13bd79b35abd`, and uploaded B to that run. Real RTP and
assistant playback were observed. Its isolated subset does not pass the complete
corpus or twenty/five latency gates; continuation admission took 6249.691 ms.
As with the other isolated diagnostics, its configuration is not imported as an
M10 current-candidate attestation.

Partial capture diagnostics are retained in `data/capture-performance-diagnostics.json`.
Twenty-five callbacks per routing mode had synchronous p95 of 1.143 ms (managed)
and 0.914 ms (client). Cached upload observations were fewer than the required
twenty. The successful fresh-screen diagnostic observed an 87.187 ms request-to-
callback interval, 0.219 ms synchronous callback and 53.269 ms cached upload start;
one interval does not establish p95 acceptance. These earlier observations alone
cannot pass capture performance.

The repeated performance run `data/real-capture-performance-26/audio` measured
20 screenshot-disabled baselines and 20 activation attempts. Baseline startup
p95 was 3235.466 ms; enabled startup p95 was 4189.540 ms. These are startup
measurements, not the admission-latency gate. Only 18 activation admissions
passed. The 20 synchronous callbacks had p95 0.595 ms, but only 17 cached upload
starts were observed (p95 199.040 ms). The first fresh continuation was actually
transcribed and acknowledged, but did not request a new capture before timeout;
measurement stopped without replaying the unknown action. No fresh intervals or
health checkpoints can be inferred from that failure.

Measured native playout underrun and ANR counters were zero in the 20 baseline
and 18 successful activation checkpoints. Later-frame jank was present: 90
baseline frames and 108 activation frames exceeded their deadlines. First-draw
overruns are recorded separately (20 and 18); dropped observations were zero.
The original screenshot setting was restored. The extracted diagnostic is
`data/capture-performance-repeated-diagnostics.json`. This run predates backend
runtime binding and does not attest to the final candidate.

The shorter `data/real-capture-performance-27/audio/acceptance.json` verified the
same authenticated backend candidate, selected database and startup instance
before and after measurement. One baseline, one activation and one same-session
fresh continuation passed their functional observations. Fresh callback latency
was 80.969 ms and cached activation upload start was 142.509 ms. Native underrun
and ANR counters were zero, but later-frame jank remained (4/7/5 frames). Its
capture-performance assertion correctly blocks on insufficient samples. It uses
the final source with an explicitly isolated test configuration, distinct from
the aggregate candidate above; it is not imported into M10. Repeated fresh
capture, admission reliability, latency and frame performance remain open.

## Bounded follow-up and decisions to re-evaluate

After the user requested continued work without substitute functionality or
complexity-increasing fixes, investigation found two concrete defects and one
acceptance interpretation issue:

- Authenticated Android requests decrypted their credential using AndroidKeyStore
  before switching HTTP work to IO. That could block the assistant UI thread.
  Authentication now switches to IO too, without adding a plaintext cache.
- A superseded session could reject a delayed transcript flush, leave an
  unobserved task exception, and abort socket/background-task cleanup during
  close. Flush errors are observed, and close guarantees cleanup while still
  propagating explicit persistence failure. Tests verify the old socket,
  receiver, idle task and pending tool work all stop.
- Client-only origin matching required a transcript within 250 ms of delegation,
  despite the shared matcher allowing a bounded 2500 ms. The runtime-bound
  diagnostic `data/real-capture-performance-28` recorded a valid delegation at
  6800 ms for a transcript ending at 6200 ms. This 600 ms separation caused a
  rejection before any selection HTTP call. The client-specific restriction was
  removed; both modes share the existing bounded matcher. Pending-fragment
  waiting, ambiguity rejection and prohibition on reusing a consumed turn stay
  enforced. This corrects inconsistent correlation, not a new routing fallback
  or a third catalog/transport tuning experiment.

The final focused runs passed 86 backend tests: 49 adapter/client/acceptance/audio
harness tests (`data/m8-correlation-tests.log`) and 37 coordinator/admission/capture
tests (`data/m8-gateway-regression-tests.log`). The prior adapter/client/coordinator
check passed 58 (`data/m8-live-cleanup-tests.log`). Android passed all 27 unit
tests and rebuilt the APK (`data/m8-auth-io-android.log`).

The corrected runtime-bound diagnostic `data/real-capture-performance-29/audio`
observed one screenshot-disabled baseline, one passing activation upload and
one passing real A/question/fresh-B continuation. Fresh request-to-callback was
72.330 ms, cached activation upload start 72.131 ms and synchronous callbacks
0.247/0.968 ms. Native underrun/ANR counters were zero. Later-frame jank remained
6/7/5 frames in baseline/activation/fresh checkpoints. These are single samples,
not p95 acceptance; the performance gate remains blocked. No source/configuration
or backend-instance change occurred during measurement. The subsequent runbook
documentation correction changes the source candidate again; this isolated
diagnostic is not imported into M10.

Two requirements merit explicit re-evaluation before more paid tuning:

1. **Sub-two-second durable admission with the planned two-model route.** The
   repeated managed/client gates still fail after the permitted managed tuning
   and client repeat. The corrected short check took 5721.209 ms for activation
   admission and 6222.253 ms for fresh continuation. Actual priority selector
   calls alone took 1407–2359 ms. This does not prove no architecture could meet
   the target; it is evidence that the selected configurations have not met it
   within the agreed tuning budget. Retaining the target calls for evaluating
   the routing architecture; retaining the architecture calls for explicitly
   revising the gate. No speculative admission, keyword dispatcher or early
   success acknowledgement has been substituted.
2. **Capture-induced jank versus absolute zero missed frames.** Phase 3 says
   enabling capture must introduce no visible overlay jank. The current checker
   instead rejects any later-frame deadline miss, including the disabled
   baseline, on the harness's software-rendered SwiftShader emulator. Native
   `data/real-capture-performance-28/gfxinfo.txt` also reports slow UI frames;
   moving authentication to IO did not establish a smoothness pass. A defined
   baseline-regression criterion or an explicitly agreed measurement environment
   is needed. Counts are retained; no frames are discarded to create a pass.

The pinned Windows sandbox remains a separate runtime/host decision: no new
unrestricted route, broader ACL grant or runtime upgrade was introduced. Account
evidence and M11 opt-in boundaries also remain unchanged. The user has been asked
which latency constraint and jank interpretation to retain; neither requirement
has been relaxed pending that answer.

## Remaining gates and implementation

The new supervised leased-project live test failed before model work: Codex
0.155.1's unelevated Windows sandbox denied a write inside its declared writable
root in a fresh profile. Read-only and outside-root probes denied writes as
expected. See `data/closure-m6-live.json` and its log. The historical M0 scratch
parent has broader sandbox-user ACLs; its pass does not prove a fresh profile.
No unrestricted execution, broader group ACL grant or runtime upgrade was used.
M6 cannot close until the pinned host boundary passes and the real failing-test
repair/restart proof succeeds.

The complete spoken corpus, sourced-result completion, callback/upload and
Live-ready baseline performance gates remain open until their executable
observations pass. The isolated A/clarification/fresh-B scenario now passes;
the whole M8 contract and its current-candidate attestation remain open. The
harness asserts separate multiple-outcome tasks,
spoken clarification/nonmatch and durable cancellation/question/follow-up state;
missing setup is blocked. Fresh capture requires the original session/activation,
a real pending question voiced before B, a run-bound fulfilled fresh image and
B's foreground pixels. No fake-Live or injected bitmap is accepted. Persistent
admission latency misses in either selection mode, missing repeated fresh
observations and measured later-frame jank leave M8 open. These are M8
contracts, not deferred physical M11 tests.

No separately authorized Jarvis subscription profile or provider-enforced cutoff
evidence was supplied. M9's real subscription/restart/API fallback and cutoff
acceptance remain blocked. Fixtures do not substitute for those observations.
Account setup cannot be inferred from silence or borrowed desktop credentials.
M10 also requires current browser/emulator/provider evidence and every required
phase contract. Continue these gates with the runbook; do not mark M10 complete
from local regression counts.
