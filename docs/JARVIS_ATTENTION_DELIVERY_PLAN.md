# Simplify Jarvis Attention with Model-Reported Delivery

## 1. Summary and agreed behavior

Keep durable tasks and events. Make `TaskPresentation` the sole authority for whether Jarvis still owes the user an update, with only `pending`, `handled`, and `superseded`.

**Trust the model’s explicit self-report for this version.** GPT‑Live chooses its wording and conversational timing, then calls a checklist command after mentioning an update. Neither transcript matching nor a separate LLM verifier is required.

This replaces the original transcript-as-proof design:

- `handled` means the model reported mentioning the update, the notification was opened, or a documented migration rule retired it.
- Transcript and playback events remain history and diagnostics; neither changes attention state.
- An accepted context append does not handle attention.
- A missing self-report leaves attention pending and permits push fallback.
- Previously attempted proactive announcements are not automatically repeated after reconnecting.
- Remove snooze entirely.
- Leave GPT‑Live’s independent timer behavior unchanged.

## 2. Model context, checklist commands, and delivery

### Route information according to its purpose

Use the following deterministic routing:

- **`session.thinking.append`:** routine progress, normal terminal results, checklist changes, handled/superseded status, and pending-item context restored after reconnecting.
- **`session.commentary.append`:** due reminders, blocking questions, and terminal results with `importance=high`.
- Preserve existing task update policies: silent tasks produce no unsolicited attention; progress attention requires `follow_progress`.
- Use startup instructions for general behavior. Do not repeatedly append instructions to force announcements.

This follows the documented distinction between quiet context and information the model should speak. `commentary.append` is not specifically an interruption command; appended instructions can interrupt behavior. [Official guidance](https://developers.openai.com/api/docs/guides/live-delegation#send-the-right-kind-of-update)

Remove canonical-phrase requirements, contradiction phrase lists, transcript-based settlement, and correction/retry speech prompts.

### Add a narrow checklist interface

Expose two tools to GPT‑Live’s delegated Responses model and the Live adapter:

- `get_attention_checklist(cursor?, limit=20)` returns pending items, factual summaries, priority, and opaque acknowledgement identifiers. Cap the limit at 50 and support pagination.
- `acknowledge_task_update(presentation_id, exposure_token)` handles exactly one item. It cannot edit task execution, acknowledge multiple items, or set arbitrary states.

The application supplies session ID and generation from the authenticated provider callback; those are not model-controlled arguments.

Both model prompts must explain:

- Mention updates naturally, without reading internal identifiers aloud.
- Acknowledge only after communicating the update, never merely after receiving it.
- Quiet context does not require an immediate announcement.
- Checklist bookkeeping should not generate an additional spoken confirmation.
- Handled and superseded updates should not be announced proactively again.

Tool calls still use the existing Responses delegation mechanism. Do not introduce a second model or require every response to call a tool.

### Keep model context current

Provide the same authoritative attention view to the live voice model and its delegated tool model:

- Send initial checklist context once the session is ready.
- Feed changes through quiet context appends, coalescing redundant changes over 250 ms.
- Refresh the delegated model’s checklist context through supported session configuration updates; retain its fixed routing instructions.
- Return authoritative state directly in acknowledgement tool results.
- Publish handled/superseded changes to the active session even when they originate from notification opening.
- Send only changed facts during a session. Rebuild context from the database after reconnecting.
- Respect the 500-token append limit. Split checklist context into independent items; do not truncate structured identifiers.
- Stop presenting `SpeechDelivery` or outbox status as alternative attention authorities in model context.

Do not force multiple updates into one speech response. Multiple pending checklist items may exist in context, while each proactive append and acknowledgement addresses one item.

### Separate append ownership from acknowledgement identity

Keep reservation as a short transport lease on a pending presentation:

- Use existing reservation session/generation/timestamp fields plus `lease_token`.
- Default lease duration: 30 seconds.
- Enforce at most one proactive append lease per voice session with a database partial unique index.
- Claim, release, and expire leases using conditional database updates, not read-then-write ownership checks.
- Release on append acknowledgement, rejection, disconnect, or expiry. Do not hold a lease while waiting for the model to check off an item.

Use a separate durable **exposure token** for self-reports:

- Reuse the ID of an existing `VoiceSessionEvent` recording `attention.context_exposed`.
- Record presentation ID, source task event, session, and generation in that event.
- Issue one token per presentation per session generation and reuse it in context refreshes.
- Validate that a report references an exposure belonging to the current session and the requested presentation.
- Lease expiry does not invalidate a legitimate report from that current session.
- Superseded sessions cannot report against old exposure tokens.

This requires no additional delivery-state table or announced/heard state machine.

### Prevent automatic announcement loops

Add `commentary_attempted_at` to presentations.

Set it in the same transaction that claims the first commentary attempt, **before network submission**. Once set, that task event receives no further automatic commentary attempts, including after an uncertain send or process crash.

On reconnect, pending items are restored through thinking context only. A newer task event creates a new attention item with its own eligibility.

An unrelated response, interruption, or missing report does not cause correction speech. The item remains pending and notification fallback continues. The model may still mention it naturally from its checklist.

## 3. Durable handling, schema, and migration

### Attention schema and atomic handling

Add:

- `handled_at`
- `handled_by`
- `handled_turn_id`
- `handled_event_id`
- `lease_token`
- `commentary_attempted_at`

Rename stored `presented_at` to `handled_at`; migrate existing turn references. Keep obsolete delivery references only if needed for historical diagnostics, with no eligibility reads.

Add database constraints for the three attention states and unique non-null `source_task_event_id`. Preserve existing idempotency keys.

Implement a transaction-aware attention service used by both model reports and notification opening. A successful handling transaction must:

1. Validate current session/exposure identity for model reports.
2. Conditionally transition a pending row to handled.
3. Write an `attention.handled` audit event and reference it through `handled_event_id`.
4. Record `handled_by=model_report` or `notification_opened` and the timestamp.
5. Clear the lease.
6. Supersede queued/failed outbox work for that task event.
7. Publish the attention change through the existing durable live-event stream.

Record the originating provider tool event or notification delivery in the audit payload. Leave `handled_turn_id` null when no specific assistant message is known; do not substitute the latest user turn.

Handle the acknowledgement tool within the transaction that durably processes its provider event. Persist its result before returning it to the provider. Duplicate callbacks return the stored result instead of being discarded before unfinished handling can recover.

Repeated valid reports return `already_handled` without overwriting the first evidence. Reports for superseded items do not affect their replacements. Invalid or stale tokens perform no mutation.

Reminder tasks remain completed when their due event commits. Attention handling never reopens or completes task execution.

### Migration policy

Run the migration with API and worker processes stopped and a database backup available.

Map existing states:

- `pending`, `reserved` → `pending`, with leases cleared.
- `presented`, `acknowledged`, `deferred` → `handled`.
- `superseded` remains unchanged.

Apply the agreed legacy-reminder exception:

- Capture the maximum committed task-event sequence as a persistent migration cutoff.
- Retire pre-cutoff pending reminder-due attention whose prior delivery history is uncertain, including rows previously converted from deferred by migration 0013.
- Retire legacy reminder attention without a usable event link.
- Mark these `handled_by=migration_legacy_retired`, with an audit reason.
- Apply the same cutoff rule when the projector later encounters previously unprojected pre-cutoff due events.
- Preserve scheduled reminders that have not committed their due event. Preserve ordinary pending task-result attention.

Transfer existing notification acknowledgement evidence before dropping its columns. Prefer existing evidence timestamps; label historical provenance accurately rather than inventing transcript evidence.

Map outbox `acknowledged` to `delivered`, preserving existing delivery timestamps, including null values. Supersede unsent transport work associated with handled or superseded attention.

Do not modify historical migration 0013. Test complete upgrade chains through it.

## 4. Notifications, compatibility, and snooze removal

### Transport-only outbox

Restrict outbox states to `queued`, `sending`, `delivered`, `failed`, and `superseded`. Remove `acknowledged_at` and `acknowledgement_state`, including their use in generated payloads and eligibility checks.

The push worker must:

- Check pending attention when selecting and atomically claiming transport work.
- Reconcile attention before startup recovery schedules sends.
- Preserve already-delivered rows as history.
- Record the actual outcome of an already-in-flight send.
- Suppress further retries if attention became handled or superseded while that send was underway.

Use the existing eight-second voice grace as the default. Quiet context updates must not repeatedly reset it. Bound a busy-session deferral to 30 seconds from initial notification queuing so continuous conversation cannot postpone fallback indefinitely.

An external push already in flight may still arrive after handling. Accept that narrow duplicate-delivery race; do not claim exactly-once delivery across voice and push.

### Public interfaces

Preserve:

- `pending_presentations`
- Task-detail `presentations`
- `POST /api/notifications/deliveries/{id}/ack`

Return only the new attention state values. Expose `handled_at`, with `presented_at` as a deprecated response alias for one compatibility release.

The notification acknowledgement endpoint accepts `opened` and handles the attention associated with the delivery’s exact task event. It does not change task execution or relabel delivered transport history.

Reject legacy `spoken` requests without changing attention; voice handling now requires the session-bound model tool.

Update browser types and replace “waiting for a safe boundary” copy with “pending updates.” No browser or Android playback-reporting changes are required.

### Remove snooze

Remove:

- Android “Snooze 10m” notification actions and receivers.
- Android snooze API calls.
- The model’s snooze tool and gateway branch.
- Scheduler snooze implementation and active request types.
- Snooze-specific documentation and tests.

Keep a temporary compatibility tombstone for the old reminder snooze route, returning `410 Gone`. A notification acknowledgement with `state=snoozed` also returns `410` without creating a reminder or handling attention.

Previously created replacement reminders remain ordinary scheduled reminders. Historical snooze acknowledgements remain migration evidence.

## 5. Implementation order and acceptance tests

Implement in this order:

1. Persist this plan and capture a baseline of focused tests.
2. Add migration, constraints, and the transaction-aware attention service.
3. Add exposure tokens, checklist tools, context synchronization, and append routing.
4. Remove speech-driven settlement and automatic commentary retries.
5. Update notification transport, compatibility responses, and clients; remove snooze.
6. Run regression suites and instrument a real GPT‑Live session.

Required tests:

- **Migration:** every old state; full upgrades from before 0013; previously converted deferred reminders; projector lag across the cutoff; future schedules; orphaned legacy reminders; acknowledgement evidence; interrupted migration recovery.
- **Projection:** one eligible task event creates exactly one attention row and one outbox row; replay is idempotent; progress coalescing and supersession remain correct.
- **Leases:** concurrent claims, separate sessions, stale generations/tokens, expiration, restart recovery, and quiet updates not blocking urgent updates indefinitely.
- **Self-report:** a valid tool report handles exactly one item; duplicates preserve first evidence; invalid/stale reports do nothing; superseded items cannot settle replacements.
- **Atomicity:** crash before commit changes nothing; crash after commit does not repeat handling; duplicate provider events recover their stored tool result.
- **Protocol:** thinking/commentary routing matches policy; append acknowledgements and transcripts alone never handle attention; no exact-word instructions; checklist changes reach both model contexts.
- **Reconnect:** report an update, reconnect, and assert no second proactive announcement; omit the report, reconnect, and assert quiet pending context plus fallback, without another commentary append.
- **Notifications:** opened handles attention; queued/failed pushes are suppressed after handling; in-flight outcomes remain historical; failed in-flight sends cannot resurrect retries.
- **Snooze removal:** no exposed tool or Android action; legacy calls return 410 without side effects.

Run the focused backend attention, coordinator, adapter, notification, reminder, projection, and migration suites; frontend Vitest; the existing realistic wake/audio smoke fixture; and Android unit tests/debug build.

The instrumented GPT‑Live session must demonstrate a quiet update, a time-sensitive update, a model acknowledgement, handled context feedback, and reconnect behavior. Capture task/event IDs, append type, report result, attention state, and outbox outcome. If device or provider access is unavailable, report that acceptance step as incomplete.

The accepted limitation is explicit: **model self-report may be premature or missing**. This version trusts valid self-reports and uses push for missing reports; it does not silently restore transcript verification or add another delivery authority.
