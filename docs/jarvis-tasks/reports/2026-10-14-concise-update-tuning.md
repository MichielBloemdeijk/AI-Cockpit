# Concise GPT-Live task-update tuning

The shared Live policy was reduced from **501 to 213 words**. It prioritizes a
brief requested outcome before a longer conversational reply, and has one
post-speech acknowledgment instruction. Standard delegation labels follow
[OpenAI's prompting guidance](https://developers.openai.com/api/docs/guides/live-prompting#delegation).
The backend's conflicting request-priority instruction and prompt-rewriting
compatibility code were removed. Defined outcomes, screenshots, authorization,
wake activation and orchestration ownership remain in the production adapter.
The original A thinking-only payload and acknowledgment authority are unchanged.

Keep the shorter policy as the **provisional development default**. Routine
requested updates looked useful with less instruction text, but this small pilot
does not prove equal reliability or qualify the complete interaction.

## Six controlled cases

One frozen production candidate was tested with fresh databases, spoken admission,
normal deterministic worker publication, continuous WebRTC and client playback.
The ceiling was eight provider connections. Seven actually started because the
last reconnect did not establish a replacement session. Models remained
gpt-live-1 / Marin and gpt-5.6-luna / low / priority, with parallel tool calls off.
No speech trigger, application relevance chooser, automatic acknowledgment or
notification instructions in task definitions were introduced.

1. **Requested update while idle:** requested preference saved correctly. Complete
   outcome played in **3.10 s** from durable completion.
2. **Requested update during speech:** requested preference saved correctly.
   Outcome waited for the opening and finished playing in **4.46 s**, with no
   outcome overlap. A brief “Okay, go on” replaced the earlier long desk reply.
   One admission-promise word has uncertain ASR timing across removed silence;
   this remains separate conversation-assessment uncertainty.
3. **Concurrent success/failure and duplicates:** page completion played in
   **2.63 s**, checklist failure in **5.19 s**. Both were accurately associated
   and true when spoken. Duplicate publication caused no repetition. This also
   verifies the repaired failed-result context delivery. Both preferences were
   nevertheless saved incorrectly as discretionary despite the notification
   request and promise.
4. **Completed between sessions:** first session disconnected; worker completed;
   replacement session opened; caller asked the task status. Saved completion was
   answered accurately in **3.28 s** after the question, without recreating the
   task. The original notification preference was incorrectly discretionary.
5. **Silence, then status question:** suppressed preference saved correctly;
   no unsolicited outcome during sixteen seconds. The later accurate answer
   played in **3.73 s**. This guard remained despite requested notifications being
   the primary optimization target.
6. **Interruption/reconnect diagnostic:** the initial short outcome played fully,
   starting before the caller barged in. Client PCM confirms overlap; native
   provider output ended before its “Hold on” input span. This does not establish
   that Live chose to start over an already-speaking caller, and the fixture did
   not exercise a truncated announcement. After the caller finished, Live
   promised a short result but gave none within sixteen seconds. That fails the
   frozen recovery criterion; wording suggesting more speech to come and the
   missing truncation limit causal conclusions. No replacement session was
   recorded, so repetition after communication/reconnect remains unqualified.

Latency ends at fully played outcome speech, from durable completion or the actual
caller opening, with the existing 250 ms tolerance. Application context queueing
does not restart the clock. Evidence concerns client playback, not physical
speaker verification.

## Acknowledgment and durable notes

**No acknowledgment tool call occurred.** Seven complete outcome utterances were
recorded, including the historical failure. Their presentation records remain
pending. No premature handled transition occurred. Pending means delivery is
**unconfirmed**, not proof that nothing was spoken. Conversation speech history,
task/attempt outcomes and acknowledgment state remain separate.

New network-blocked tests establish that completion after Live closes is retained
and exposed in a new session; both pending and model-acknowledged states survive
session replacement; old tokens cannot handle updates in a new session; and a
completed result remains retrievable after acknowledgment removes its pending
attention entry. The Live between-session case confirms actual later recall.

This supports status questions without coupling notifications to worker/workflow
definitions. Reliable association of spoken outcomes with delivery confirmation
is still unresolved. Missing acknowledgment is retained truthfully rather than
silently ticking the checkbox.

## Direct review and checker disagreements

All six recordings were reviewed directly using exact provider responses, task
events, independent transcription and playback. No new semantic-grading request
was made. Original assessments and separately labeled agent reviews are retained.

The concurrent checker falsely flags task truth because it uses the final task
snapshot. Failure event `785b43de-5a4b-4251-ba7f-e737387e49f2` was recorded at
`1792013552000`; the durable worker completion origin is `1792013552831.967`.
Failure speech finished playing around `1792013558023`. Automatic continuation
event `d687c23c-2398-44a3-b41e-621623723156` followed at `1792013563000`, changing
the final snapshot to queued. Direct event review proves the announcement
preceded that continuation and was truthful. The original failed machine grade
remains visible as a checker disagreement. Genuine preference and acknowledgment
failures are not discounted.

The reconnect fixture used fixed sleeps without verifying another session existed.
It now waits for disconnect, wake readiness and a replacement ready session, and
records those boundaries. Syntax validation passed. This recording-only repair
happened after paid execution; no replacement run was performed or counted as a
pass. Interruption/reconnect qualification remains outstanding. The separate
between-session case did establish a real replacement session.

## Validation, usage and cleanup

The offline suite had **266 passing checks and one obsolete prompt assertion**.
The assertion was repaired and passed separately, accounting for **267 checks**
without repeating the unchanged suite. Browser continuous audio/duplex, JavaScript
syntax, mock host and Android build/unit checks passed. Failed local checks remain
recorded. Cached calibration was reused; network-blocked report replay made zero
provider requests.

New usage: **7 Live sessions / 253 seconds**, **13 managed Jarvis backend responses**,
**6 transcription requests**, **0 semantic-grading requests**. Managed backend usage
was 60,291 input and 664 output tokens. One background orchestration decision is
recorded separately with its runtime events and metrics. No simulated caller,
real-worker or Android voice cases ran. Monetary cost is unknown.

Cumulative usage: **75 Live sessions / 2,614 seconds**, **184 managed Jarvis backend
responses**, **72 transcriptions** and **115 semantic-grading requests**, retaining
the historical failed request. Backend totals are 848,823 input and 9,818 output
tokens, including cached input in reported input usage. Prior phase evidence and
counters remain unchanged.

All owned provider sessions finalized, database sessions are disconnected, and
owned processes/ports were cleaned up. M0–M11 acceptance remains closed.

Evidence: `data/announcement-evaluation/pilot-20261014-concise-01/report.json`,
`final-results.json`, each case's `agent-review.json`, caller/assistant audio and
provider logs. Offline gates are `offline-20261014-concise-01` and `-02`.

## Decision

Keep the shorter policy provisionally. Routine notifications, failure updates and
later status answers worked; occupied timing improved in this sample. Avoid more
repetitive instructions on this evidence. Preference recording remains
inconsistent, post-speech acknowledgment is absent, and interruption recovery is
not dependable/qualified. Do not claim full qualification or a hard GPT-Live
limitation. Central task truth and pending attention support recall and should
remain independent of task definitions. Further evaluation should isolate valid
interruption recovery and model-owned delivery confirmation rather than repeat
the successful idle corpus.
