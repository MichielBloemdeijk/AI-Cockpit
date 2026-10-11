# Voice and selector output investigation — 2026-10-05

The available output does not establish excessive selector reasoning. It does
establish unnecessary output imposed by provider schema normalization. The
remaining seconds include response startup and argument delivery; exact thinking
time and token throughput are still unknown because both diagnostic stream
exports failed. No further paid generation followed those failures.

## Subsequent optional-schema correction

Following the user's instruction, every function in the shared voice tool catalog
now explicitly sends `strict:false`. Both managed and client selection consume
this catalog, so the provider must not normalize omitted optional fields into
required ones. `dispatch_task` still requires `goal` and `intent_id`. Its description
now says so, and selector instructions explain that only schema `required` fields
are mandatory and that default importance can be omitted.

Jarvis's completed-call JSON-schema validation remains enforced before dispatch;
missing goal/intent ID, invalid optional values and unknown top-level fields are
rejected. Provider generation becomes best effort; application validation and
the durable restricted gateway remain authoritative. No automatic repair or
broader execution fallback was added. The change is covered by offline request
serialization and argument-validation tests in both modes, plus workflow,
execution-preference and durable admission regressions: **67 tests passed**.
No new paid requests were
made for this correction, and its latency benefit is not yet measured. Historical
evidence below describes the earlier strict-normalized candidate.

## Paid speed options checked

As of 2026-10-05, [Fast mode](https://developers.openai.com/api/docs/guides/priority-processing)
accepts `service_tier:fast` and `service_tier:priority` with the same behavior.
Jarvis already requests `priority`, and all completed selectors in these trials
actually received it. Renaming that parameter would not buy a further speed tier.

[Ultrafast mode](https://developers.openai.com/api/docs/guides/ultrafast-mode)
is a further paid option for GPT-6 Astra, with preview access for GPT-5.6 Sol.
The documentation lists no Ultrafast tier for our current GPT-5.6 Luna selector.
Trying it would require a model change and measured functional/latency evaluation;
the guide does not establish that Astra Ultrafast would beat Luna Fast for this
specific admission request. It also does not target the measured pre-delegation
voice delay outside the selector.

The [published short-context prices](https://developers.openai.com/api/docs/pricing?latest-pricing=ultrafast)
are $60 input / $300 output per million tokens for Astra Ultrafast versus
$0.40 input / $2.40 output for Luna Fast. These are respectively 150×/125×
the current selector's uncached-input/output rates; actual request costs depend
on token mix, cache reads/writes and reasoning output. Prices are not measured
account charges. No model/tier/account setting was changed or paid speed test run.

## Recovered outputs

[Full retained outputs and usage](../../../data/admission-model-output-analysis-2026-10-05.json)
contains four trials' retained spoken transcripts, executed tool calls and
selector usage, plus the two complete managed response output arrays retrieved
from the API. These arrays include every exposed output item, rather than just
the executed tool arguments. Client responses used `store:false`; their five
completed response IDs returned 404 and could not be recovered. Complete client
response arrays and raw stream arrival traces remain unavailable.

For the fixed request, “Create a task to draft a plan for a simple notes
application,” the managed selector returned one reasoning item with empty
`content`/`summary`, followed by `dispatch_task`:

```json
{
  "goal": "Draft a clear, practical plan for building a simple notes application, including core features, suggested structure, implementation steps, and sensible next actions.",
  "result": {"mode": "answer", "kind": null, "public_id": null, "expected_revision": null},
  "execution_preferences": {"model": "gpt-5.6-luna", "reasoning_effort": "low", "override_reason": "Straightforward planning task."},
  "intent_id": "create-notes-app-plan-1",
  "importance": "normal",
  "presentation_policy": "critical_only",
  "targets": [{"resource_type": "task", "resource_key": "notes-app-plan", "mutable": true}]
}
```

The subsequent managed response was one short confirmation message, with
20 output tokens and zero reasoning tokens. The provider-returned text is
preserved exactly in the artifact, including replacement characters.

Retained voice output was short. The two new trials contain “Yes.” / “Sure, on
it.” / “Task created to draft that plan.” and “Yeah.” / “Sure, starting that
now.” / “Task created:”. The latter final utterance is incomplete in the capture;
do not interpret the transcript ending as the intended end of the model response.
Earlier transcripts are retained alongside them. Voice usage reports seconds,
not an exposed reasoning-token count.

OpenAI does not expose raw hidden reasoning. The optional
[reasoning summary](https://developers.openai.com/api/docs/guides/reasoning#reasoning-summaries)
is a separate provider-visible output and is model dependent. The old managed
request did not ask for a summary; its empty summary is not evidence of absent
reasoning. The new trials requested `summary:auto`, but their full output buffers
were lost, so summary availability in those trials is unknown. Application
`session.thinking.append` context is not model-generated internal reasoning.

## Confirmed avoidable output

Our local `dispatch_task` schema requires only `goal` and `intent_id` and omits
`strict`. The recovered provider response reports **`strict:true`** and requires
all seven top-level fields. Optional fields were therefore mandatory in that
request, despite our prompt asking for minimal arguments. This is the documented
[Responses strict-mode normalization](https://developers.openai.com/api/docs/guides/function-calling#strict-mode).

The actual argument string is **564 characters**. Retaining only its existing
goal and intent ID takes **214 characters**, a 62% character reduction. This is
a potential payload reduction, not a measured 62% latency or token reduction.
Some optional fields remain useful when a request actually needs them.

The concrete next correction is to explicitly set `strict:false` for tools that
intentionally allow omitted fields, retain application JSON-schema validation,
and test omitted/default and malformed arguments locally. Simply reiterating
“omit optional arguments” cannot override a schema requiring them. This
initial investigation did not change the strict setting or measure the latency
benefit; the subsequent correction above now sets it explicitly. Redesigning a
strict schema with required nullable fields would
still generate those field names and null values.

## Reasoning counts and timing

First admission selector calls reported:

- Earlier managed: **38 reasoning / 175 total output tokens** (137 nonreasoning).
- Earlier client: **35 / 142** (107 nonreasoning).
- First diagnostic client: **8 / 113** (105 nonreasoning).
- Second diagnostic client: **31 / 132** (101 nonreasoning).

The counts are modest for these calls. There is no clear evidence of lengthy
overthinking, but token counts cannot identify how much wall time was spent in
hidden computation. Most output tokens are outside the reasoning category;
that alone does not establish their generation speed.

Existing clean client observations, with each duration measured within one clock
domain, give the following stage ranges across the earlier sample and the two
diagnostics:

- Speech end to delegation relay: **2.24–2.32 seconds**. This includes final
  audio transport, provider utterance/delegation processing and relay delivery;
  it occurs before the client selector request.
- Selector HTTP start to headers: **0.71–2.72 seconds**. This mixes connection,
  network, queueing and provider startup; it is not a pure reasoning interval.
- Headers to complete function-call item: **1.25–1.85 seconds**. This includes
  any post-header reasoning and argument generation/delivery. It does not reveal
  time to first argument or steady output throughput.
- Complete tool item to server SSE emission: **0.30–0.31 seconds**, including
  about **58–63 ms** first-call schema validation.

The diagnostics admitted correct tasks in **7.136 s** and **6.013 s** respectively.
Both exceeded four seconds and stopped before another selected case. They are
different diagnostic candidates, include a changed summary request, and are not
controlled before/after comparisons, p95 estimates or route selection evidence.
The lower HTTP-header wait in the second trial is an observation, not a proven
cache or connection improvement. Post-admission selector responses do not delay
admission, but contribute to paid usage.

We still need a successfully retained raw stream to distinguish first-argument
delay from argument-delivery duration. The API supplies variable-sized
[argument deltas](https://developers.openai.com/api/reference/resources/responses/streaming-events#response.function_call_arguments.delta),
not token IDs. Delta arrival times support chunk gaps and character throughput;
they cannot expose exact hidden reasoning time or exact provider tokens/second.
Final audio-send and HTTP connection-phase timing remain separate gaps.

## Instrumentation and failed captures

The opt-in recorder buffers bounded, sanitized provider output outside the
admission database path. It records exposed output arrays, reasoning-summary
deltas, argument/text deltas, usage, requested/returned schema metadata and
monotonic arrival stamps for both routing modes, plus voice transcript deltas.
It omits audio, credentials and opaque encrypted reasoning. Normal sessions
leave it disabled; requesting summaries is separately opt-in and changes the
diagnostic candidate. See the [runbook](../rollout-runbook.md).

Both paid attempts lost the raw buffered export during terminal-session cleanup.
Explicit cleanup alone did not fix the second attempt: its durable session was
already disconnected, leaving no active session for that cleanup path. The final
local correction checkpoints output at provider terminal/transport events before
coordinator callbacks can reject a stale session. Adapter close then performs
the final flush; checkpoint snapshots are frozen before thread-based file writes.
Generation fencing and task authority remain intact. The correction is verified
offline, **not by another paid run**. The export failures remain recorded as
failures and must not be treated as full-output diagnostic success.

`scripts/summarize-model-outputs.py` analyzes future successfully retained traces,
separates reasoning/nonreasoning output counts, and reports first/last argument
arrival, maximum chunk gaps and exposed summaries. Regression coverage verifies
that a preceding reasoning-item completion cannot be mistaken for function-call
completion. Focused backend verification covers recorder bounds/privacy,
managed/client output retention, terminal cleanup, pilot limits and candidate
identity: **70 tests passed**. No existing application data was reset.

## Spending and next step

This investigation added **two voice trials**, **four completed selector
responses**, **9315 input / 279 output tokens**, and **33 reported Live seconds**.
Workers, background orchestration and new speech synthesis were disabled.
There were **eight read-only response retrieval requests**, including one duplicate
managed lookup; these generated no inference. Exact monetary cost and remaining
balance were not returned and remain unknown. These counts are additional to the
[earlier pilot](admission-live-pilot-2026-10-05.md), not replacements for it.

Proceed with the explicit optional-schema correction and offline validation
before any future paid check. A single targeted stream capture, only after those
results are promising, can quantify how much shorter argument output helps and
how much response startup remains. No broad suite or model sweep is justified
by the current evidence. M8/M10 remain open; full acceptance and its required
sample counts are unchanged, and M11 physical testing remains deferred.
