# Acknowledgment plumbing, prompts and GPT-Live memory audit

This is a targeted investigation of the missing delivery acknowledgments, not a
new voice acceptance phase. Production behavior and all previous evidence remain
unchanged. No new voice session, transcription, grading or paid request was made.
The cumulative voice-session count remains 75.

## 1. What the saved provider evidence proves

Replayed the six latest concise-tuning cases from their raw incoming/outgoing
events, saved startup configuration and durable application snapshots. The
seventh provider session is the second session in the between-session case.

- All six startup configurations register `acknowledge_task_update`.
- Seven terminal thinking appends contain session/generation-bound exposure
  tokens matching the durable exposed presentation. Every append has a matching
  `session.thinking.appended` receipt.
- There are six frontend delegation events, all for admission, and seven nested
  function calls, all `dispatch_task`. None is a post-outcome bookkeeping
  delegation. No raw provider error event is recorded in these cases.
- No acknowledgment function call reached the adapter, coordinator or service.
  Pending presentation state therefore reflects missing confirmation, not a
  rejected acknowledgment and not proof that no outcome was spoken.

The exact identities, timestamps and immutable source-evidence hashes are in
`data/announcement-evaluation/acknowledgment-audit-20261015/saved-evidence-audit.json`.
This audit does not redo the prior audio grading or qualify interrupted speech.

The observed break is **before acknowledgment execution**, at initiating the
post-speech delegation. This localizes the symptom; it does not establish why
GPT-Live failed to initiate it or prove a universal provider limitation.

## 2. Tool-path verification

Added a network-blocked integration test to
`backend/tests/test_model_reported_attention.py`. It sends real adapter-format
`session.delegation.created`, nested `response.created`, completed function-item
and `response.completed` events through the actual OpenAI Live adapter and
coordinator, with only transport and provider creation replaced by local fixtures.

With no new user transcript/origin match, a valid acknowledgment:

1. Passes catalog/schema validation and reaches the coordinator's atomic path.
2. Persists the presentation as handled by model report, with a durable tool
   result, while leaving task status completed.
3. Returns a matching `function_call_output` and sends exactly one backend
   continuation. Replayed function items do not repeat the action.

A stale token returns `invalid_exposure`, leaves the presentation pending, and
still returns a tool result and continuation. These checks exercise the seam
that the previous separate adapter/service tests did not cover together.

The four-file network-blocked run initially passed 67 of 68 tests. Its sole
failure was an existing question-serialization fixture relying on the local
orchestration setting: an unreviewed worker question correctly remained hidden.
Made that fixture explicitly test exposed-question serialization, without the
orchestration gate. The complete attention test file then passed **15/15**,
including both new seam cases; the other 53 checks had passed unchanged.
No acknowledgment implementation defect was found in these exercised paths.
Fixtures cannot prove that a real provider will originate a correctly formed call.

## 3. Instruction audit: not fully cleared

The reusable policy explicitly permits acknowledgment without a new user request
and exempts delivery bookkeeping from the ordinary no-delegation rule. The
backend likewise permits acknowledgment-only requests and requires Live to report
that it finished speaking. This prevents backend replies or append receipts from
being mistaken for delivery.

However, the **assembled** frontend prompt later says:

> Delegate task actions only for a direct user request

Our intended meaning excludes delivery bookkeeping, and another sentence says
acknowledging an update never creates, continues or cancels work. Nevertheless,
the model could interpret the later broad restriction as encompassing the
acknowledgment tool. This is a plausible ambiguity, not a demonstrated cause.
The backend's orchestration restriction concerns answers/retries/repairs, and
the specific acknowledgment permission is appended afterward.

There is also avoidable frontend procedure complexity: exact function names and
opaque identifiers are included in the voice prompt. OpenAI recommends describing
frontend capabilities and delegation conditions in plain language, placing full
procedures and tool definitions in the backend. Our shared policy is 213 words,
but the full prompt also contains admission, workflow, activation, orchestration
and attention instructions. The shared-policy count is not the total prompt size.

References: [GPT-Live delegation prompting](https://developers.openai.com/api/docs/guides/live-prompting#delegation),
[migration prompt guidance](https://developers.openai.com/api/docs/guides/live-migration#write-the-live-prompt).

## 4. Scratchpad, internal memory and synchronization

The documented API provides history/context inputs, thinking/commentary/instruction
appends, transcripts, delegation events, backend output items and stored-session
forking. I found no documented API to read or edit a frontend scratchpad or obtain
a model-maintained delivery checklist. `session.thinking.append` supplies context;
its receipt is not an export of internal thoughts or a model-authored memory write.

Managed Responses reasoning summaries concern the backend and cannot establish
frontend speech delivery. Forking preserves a saved conversation/configuration,
but OpenAI still instructs applications to save current task state alongside the
session and supply outstanding outcomes when resuming. It is continuity, not a
transactional delivered/pending ledger. No storage or account settings were changed.

References: [context injection](https://developers.openai.com/api/docs/guides/live-conversations#add-context-during-the-conversation),
[stored-session continuation](https://developers.openai.com/api/docs/guides/live-conversations#continue-after-a-session-ends),
[delegation and application-owned state](https://developers.openai.com/api/docs/guides/live-delegation).

An app-owned structured notebook can be model-editable through a backend tool.
That is an external tool write, so it still needs the missing delegation. Giving
the same operation a more intuitive name may help prompting, but adding a second
memory store does not solve the handoff mechanism.

## 5. Recommended next experiment and architecture boundary

First isolate a small prompt correction: explicitly scope the direct-user-request
restriction to answering worker questions, retrying and repairing work; state that
recording communicated outcomes is independent bookkeeping. Describe that
capability to Live in ordinary language and keep function selection/handle lookup
in the backend. This preserves model-owned announcement selection and the existing
durable acknowledgment authority. Do not add more general instructions or move
delivery bookkeeping into individual task/workflow definitions.

Use one requested idle outcome to test spontaneous post-speech delegation. If
absent, a separately labeled explicit request to record the already-spoken outcome
would be a positive control: success would distinguish conversational handoff from
tool executability. It must not count as spontaneous-acknowledgment acceptance.
No such new paid experiment ran in this audit.

If spontaneous post-speech reporting remains unreliable, consider a centralized
model-authored delivery intent plus client-playback reconciliation, or an independent
observer correlating spoken outcomes and playback with durable task versions.
Neither needs to select when Jarvis speaks or force an announcement. Both would
change delivery bookkeeping and require dedicated association/interruption tests;
uncertain evidence must remain unconfirmed. An intent, transcript or generic audio
activity alone must never tick the delivered checkbox.

Existing durable task results and pending attention already survive closed and
replacement sessions. Accurate later status answers do not depend on successful
acknowledgment. The unresolved issue is recording what was communicated, so the
system should continue distinguishing pending/unconfirmed delivery from verified
task completion rather than assuming silence or missing bookkeeping means either.
