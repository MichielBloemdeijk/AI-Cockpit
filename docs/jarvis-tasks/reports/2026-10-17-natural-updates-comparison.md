# Natural task updates — four-configuration comparison

This phase follows the 16 October checkpoint and retains the historical 83 voice
sessions. It has a separate ceiling of sixty text calibration requests, eight
screening browser connections, sixteen winner-confirmation browser connections,
and six Android connections. Failed attempts and reconnects count. No new default
is enabled merely because the implementation or offline checks are complete.

## Configurations

1. Repaired admission and simplified Responses conversation bookkeeper.
2. Configuration 1 plus native input Decisions triggering a normal backend
   preference review.
3. Configuration 1 with native output Decisions as the semantic bookkeeper.
4. Both Decisions capabilities.

Voice remains gpt-live-1/marin; managed backend and Responses bookkeeping remain
gpt-5.6-luna, low reasoning, priority. Decisions uses gpt-6-luna. Task-result routing,
task services, durable preferences and playback validation are shared. Speech
remains model-owned. Exact quotations are no longer required for bookkeeping.

## Implementation

Admission can run without blocking the Live receive loop. Before task mutation,
new speech can return a factual request-context refresh to the managed backend;
no task is created on that refresh. The backend reconsiders normal tool arguments.

The conversation bookkeeper assesses semantic meaning and references source
fragments. It no longer rejects a valid paraphrase because it cannot reproduce an
exact quotation. It checks the playback of selected outcome fragments, rather
than requiring continuous playback of intervening silence or unrelated speech.
Partial outcomes and unusable playback remain pending.

Native Decisions events preserve provider source-event identity, accepted result
configuration, model/usage and session/generation. Input review is separate from
communication records and can use only the existing preference tool. Its writes
are fenced against newer preference revisions and user speech. Output Decisions
uses native results without another semantic inference. Both bookkeepers share
result identity, playback and acknowledgment persistence.

The comparison extends the existing evaluation entry point with
`--natural-updates`, `--configuration`, `--calibrate`, and `--confirm`. Calibration,
source, prompts, fixtures, schedule and corpus have explicit identities. Report
replay performs no provider requests. Original grades are not overwritten.

## Validation and adoption status

**No configuration qualified; the everyday development default remains unchanged.**
The new observer mode remains off. There were no confirmation or Android provider
sessions. M0–M11 remain closed.

The fifth frozen offline candidate passed 269 backend regression tests and 48
observer/configuration tests, 18 frontend tests, browser continuous-audio/duplex
checks (42.67 ms calibration), and Android debug build/unit checks. Frontend
TypeScript checking also passed. Earlier freezes and results remain retained.

The first Responses batch passed all 24 fixed calibration cases. The first
Decisions request then returned HTTP 503, `Decision admission timed out.` No
Decisions retry was made. This is service unavailability, not evidence that its
classifier misunderstood the dialogue. Configurations 2–4 remain unavailable for
qualification. A locally tested runner repair permitted independently calibrated
configuration 1 to proceed, preserving the failed request and counters.

### First browser screen: prompt notification, pending communication record

The spoken request included the trailing “Tell me when it has finished.” Normal
admission saved `requested`, revision 1. Jarvis said:

> I'll set that up. I've created the task, and I'll let you know as soon as it's
> finished. The Hello World page is ready.

Provider transcript and independent recorded-audio transcription agreed. The full
outcome played **2,834.066 ms after durable completion**, within the ten-second
target. It did not overlap caller speech. A separate 160 ms admission-preamble
overlap is retained as a conversation-quality diagnostic; it was not an outcome
interruption. One successful idle case does not establish reliability.

The bookkeeper made two requests but left the result pending. Investigation found:

- An early observation labeled the unfinished prefix “The Hello World page”
  complete. SQLite's second-rounded event timestamp let a short transcript pause
  appear to satisfy the batching interval. Collection now uses the timestamp's
  conservative upper bound; a regression test covers the premature prefix.
- Playback evidence rejected every interval containing any concealed audio
  sample. The recording and progressing playback showed the result was delivered.
  RTC stats showed real minor concealment, rather than a recording gap. Browser
  and Android now report reception and concealment separately. The validator
  retains coverage/track/mute/pause checks and permits at most 5% weighted
  concealment, rejecting intervals with more than 50 ms estimated concealment.
  These are practical usability thresholds, not proof of intelligibility or
  physical hearing. Old false reception reports are not retroactively passed.

No premature handled transition occurred. The failed screen, original grades,
audio, database, direct assessment and agent review remain intact under
`data/announcement-evaluation/pilot-20261017-natural-01/`.

### Repaired observer: calibration stopped further voice execution

The repaired observer underwent a new fixed batch; the first 25 requests remain
in a retained batch and still consume the same ceiling. The new batch passed
**21/24**:

- `real_admission_1`: partial instead of none; delivery correctly remained false.
- `quoted`: partial instead of none; delivery correctly remained false.
- `ambiguous`: complete instead of uncertain; delivery validation accepted it.
  The fixture says only “Please tell me when the page finishes” / “Done.” It
  cannot distinguish noting the subscription from reporting the completed page.

The last finding is a disagreement with the frozen ambiguity standard, not proof
of a false acknowledgment in a real voice session. The fixture omits a fuller
admission/completion chronology. That limitation remains visible; its label was
not changed after seeing the answer. The first two are taxonomy errors without a
false handled result. All three fail the agreed all-label calibration gate.
The new unfinished-prefix negative passed.

The policy was subsequently clarified locally: admission/quotations are none,
partial means an unfinished outcome assertion, and a bare “Done” after a request
for notifications requires conversational disambiguation. Backend completion
facts cannot supply that missing anchor. This final prompt repair has **not**
been provider-calibrated. Local checks include the all-pass gate regression.
All 51 observer/configuration tests passed after the policy clarification. A
subsequent fixed-batch budget preflight passed six targeted gate tests: the runner
refuses an unaffordable batch before making any provider request.

Paid testing stopped. There are eleven requests left under the sixty-request
ceiling, insufficient for another required fixed batch of twenty-four. No labels
were relaxed, failed cells replaced, or confirmation sessions run. No winner was
selected or enabled. Source/prompt revisions and all failures remain preserved.

## Usage and cleanup

This phase used 49 calibration attempts: 48 successful Responses requests and
one failed Decisions request. It also used one browser voice connection
(35 reported seconds), four managed backend requests, two runtime observer
requests and one transcription (8 reported seconds). No independent semantic
grader, simulated caller, real worker or Android provider session ran.

Successful calibration usage was 63,700 input / 5,804 output tokens; runtime
observer usage 8,824 / 595; managed backend usage 21,002 / 295. Failed Decisions
usage and monetary cost are unknown.

Cumulative voice usage is **84 sessions / 2,959 reported seconds**, including the
historical 83 sessions. Cumulative managed backend requests: 208. Independent
transcriptions: 81; historical semantic grading requests: 115, unchanged. Including
the historical 50 observer requests, classifier/observer attempts total 101
(100 Responses and the one failed Decisions attempt). Categories remain separate
in the saved usage ledger.

Owned voice sessions and evaluation processes were closed; no isolated server or
driver remained. Existing application data and ordinary sessions were not reset
or restarted. Report-only replay uses no provider calls.

## Next bounded experiment

Configuration 1 remains the practical next candidate because its API is available
and the one idle notification worked. It is not a validated winner over untested
Decisions configurations. Before another voice test, freeze an expanded-context
ambiguity fixture (subscription acknowledgment versus completion acknowledgment),
review its label independently of model output, and calibrate the locally repaired
policy on a fresh fixed batch. That requires a new explicit calibration allowance;
the current phase must not silently expand. Then run the two screening scenarios
before the frozen confirmation and gated Android suites. Decisions requires a
separate availability check and its own calibration before native-trigger testing.

Interface references: [Live Decisions integration](https://developers.openai.com/api/docs/guides/decisions-voice),
[Decisions](https://developers.openai.com/api/docs/guides/decisions), and
[source-event correlation](https://developers.openai.com/api/reference/resources/live/sideband-websocket#decision.event).
Decisions is transcript-based; it does not establish physical hearing or expose
Live's internal thoughts.
