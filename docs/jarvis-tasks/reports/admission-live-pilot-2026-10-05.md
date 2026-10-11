# Admission live pilot — 2026-10-05

The remaining admission delay is concentrated before delegation and in selector
HTTP/provider response time. Local durable admission remains roughly 0.3 seconds.
The four-second ready-path goal is not established by this pilot. Two paid voice
trials were attempted; each created one correct notes-plan task. Workers and
background orchestration were disabled. No broad live suite followed a slow sample.

## Scope and evidence

Real API37 custom wake, prerecorded speech through production WebRTC RTP,
GPT-Live, selector tools, SQLite admission and Android event-stream receipt.
Both trials use fixed `ready07` audio, unchanged model/tier and isolated migrated
databases. No new TTS, model sweep or worker inference was run. The first trial
used managed routing; after a local reporting repair, the second used client routing.
The candidates differ in diagnostic code, so these are separate single-sample
investigations, not a controlled route comparison or a p95 estimate.

- Managed: `data/admission-live-pilot-2026-10-05/responses/audio/acceptance.json`.
  The harness failed because logcat truncated the expanded admission timing
  payload. A correct task was committed. The tool-captured log prefix yields
  **about 4761 ms** from speech end to receipt, retained separately in
  `responses/forensic-receipt.json`. This is forensic evidence only; the original
  sample remains failed and is excluded from accepted latency statistics.
- Client: `data/admission-live-client-pilot-2026-10-05/client/audio/acceptance.json`.
  Correct admission with a complete receipt at **7709.17 ms**. The pilot stopped
  immediately after this sample exceeded four seconds; the remaining selected
  cases were not run. Functional sample status is passed; latency acceptance is open.
- Derived intervals: `data/admission-live-pilot-summary-2026-10-05.json`, generated
  by `scripts/summarize-admission-live-pilot.py` from the reports and read-only DBs.
  Candidate identities, failures and source artifacts remain retained.

## Timings

The clean client sample has a complete partition on Android's monotonic clock:

| Interval | ms |
| --- | ---: |
| Last speech sample → delegation event received | 2273.12 |
| Delegation received → selector-start event received | 727.34 |
| Selector-start received → validated-tool event received | 4496.24 |
| Validated-tool event received → durable admission received | 212.47 |
| Total speech end → durable admission receipt | **7709.17** |

These relay intervals include event-stream delivery and its variation. They are
not pure ASR/model durations. Their endpoints are all on Android's clock. Server
intervals below describe finer components and overlap the Android partition;
do not add these tables together or subtract Android/server absolute timestamps.

| Server interval | Managed ms | Client ms |
| --- | ---: | ---: |
| Delegation → correlated completed transcript | 579.25 | 721.38 |
| Completed transcript → client selector request stage | Unknown | 39.73 |
| Selector HTTP start → headers | Not exposed by managed route | **2721.49** |
| Headers → response-created event | Not exposed | 0.88 |
| Headers → complete tool item received | Not exposed | **1697.16** |
| Managed response-start observed → complete tool item received | **2198.11** | Not applicable |
| Tool validation | 58.71 | 58.12 |
| Validated call → shell start | 44.74 | 41.23 |
| Shell start → authority ready | 32.53 | 24.62 |
| Gateway start → durable task commit | 76.81 | 83.70 |
| Commit → gateway completion | 60.50 | 58.46 |
| Commit → server SSE emission | Unknown after truncated receipt | 93.52 |
| Tool item arrival → server SSE emission, including validation | Unknown | **301.19** |
| Result-record start → server SSE emission | Unknown | 19.58 |

Managed speech-end → delegation relay was **2241.34 ms**, close to the client's
2273.12 ms. Managed selection runs while transcript correlation completes;
its 579.25 ms transcript interval therefore overlaps selection. Client waits
for safe completed-turn correlation before starting selection. That wait was
about 0.72 seconds, not the former unconditional two-second sleep.

Fresh screenshots were proactively captured during selection. Request → callback
was **83.67 ms managed / 79.78 ms client**; callback → encoding finished was
**85.53 / 62.88 ms**. Client captured-status reporting took **82.89 ms**.
These ordinary tasks did not request screen context, so no images were uploaded
and capture was not on their admission critical path. Required screen admission,
both-image upload, continuation and cold-start performance remain unmeasured in
this pilot; do not treat these background capture callbacks as their acceptance.
Android dispatch after receipt was **0.51 ms** in the clean sample.

## Interpretation and next work

1. **Investigate the roughly 2.25-second pre-delegation delay first.** This
   interval includes final audio transport, provider endpoint/utterance processing,
   delegation generation and relay delivery. Current evidence cannot isolate
   these components. The session already instructs immediate delegation before
   acknowledgment, so repeating that instruction is not a demonstrated fix.
   Add a command-end audio-send marker and expose any available provider boundary
   timing before evaluating a supported boundary setting. Earlier boundaries
   trade latency against prematurely splitting multi-intent or paused speech;
   retain clarification, overlap and cancellation tests.
2. **Investigate selector HTTP/response latency.** The client spent 2.72 seconds
   before headers and 1.70 seconds awaiting complete tool arguments afterward.
   Both routes actually received the requested priority tier. HTTP-start →
   headers mixes pool/connection/TLS/network/provider work; add connection-phase
   timing to distinguish it before changing infrastructure. Managed has no
   equivalent local wire-request start, so its observed response-start interval
   is a narrower measurement. A single faster managed observation cannot select
   the reliable route. Prompt/catalog size and argument length may affect
   generation, but existing one-decision/minimal-argument instructions already
   apply; evaluate a concrete locally validated reduction, not repeated model sweeps.
3. **Small local opportunity: validation startup.** First-call schema validation
   was about 58 ms in both freshly started backends, versus sub-ms warm offline
   checks. Preloading imports/validators could move that setup to startup.
   This is much smaller than the seconds spent before and during selection.
4. **Retain durable execution semantics.** Current local processing is consistent
   with the earlier 282/316 ms fixture p95. Repeated connections and transactions
   remain possible incremental improvements; weakening SQLite durability cannot
   account for the measured multi-second provider seam.

Proceed locally on these instrumentation/correction seams before another paid
pilot. Full acceptance remains 20 ready samples per evaluated mode, five independent
cold starts and all functional/capture/provider/recovery cases on a frozen candidate.
No gate is waived or marked complete here. M8/M10 remain open; M11 stays deferred.

## Repairs and economical testing

The debug receipt now logs an allowlisted timing summary rather than all database
spans; full spans remain in server evidence. This keeps receipt JSON below
logcat's entry limit and preserves task, turn, run, commit and client timestamps.
The clean client trial exercised this fix successfully.

The harness now offers `--pilot` with one to three distinct ready cases, excludes
cold/scenario sessions, caps each diagnostic sample at 30 seconds through the
routing runner and stops on failure, missing latency or a >=4000 ms result.
Malformed Android timing entries abort immediately; raw per-sample logs are
retained in `android-logs` for future runs. These last harness-only safeguards
were verified offline after the pilot and did not trigger another paid run.
The original truncation kept the first voice session alive until timeout;
prompt failure handling now prevents that avoidable test cost.

Offline verification: **39 focused admission/capture/handoff tests passed**;
the final audio-harness suite passed **19 tests**, including mocked runner tests
that prove slow/failed/missing-latency samples do not launch more paid sessions.
Android **28 JVM tests** and the debug APK build passed, including the large-span
receipt regression. Source/configuration was stable within each paid trial.
Only fresh project-owned fixture data was created; no existing application data reset.

Recorded provider usage across the two trials:

- **3 completed selector responses:** 9158 input tokens, 337 output tokens,
  including 2923 reported cached input tokens. All completed responses report priority.
- One additional client selector request began after admission and was cancelled
  when the sample ended; its usage is unavailable. The managed route's second
  completed response was post-admission continuation, not a screenshot round trip.
- Reported Live usage: **52 seconds managed + 15 seconds client = 67 seconds**.
  This is available provider usage, not a guarantee of complete account billing
  coverage. There are two persisted session rows per trial, including the
  superseded setup session; do not infer paid request counts solely from task count.
- New speech synthesis and worker model runs: **zero**. Exact monetary cost and
  remaining account balance were not returned and remain **unknown**.
