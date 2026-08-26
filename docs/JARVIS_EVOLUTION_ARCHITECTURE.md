# Jarvis POC-to-Target Architecture Assessment

## Purpose

This document describes, at a high architectural level, how the current AI Cockpit and Jarvis POC should evolve toward [JARVIS_TARGET_ARCHITECTURE.md](JARVIS_TARGET_ARCHITECTURE.md). It records what should be preserved, refactored, replaced, retired, and added. It intentionally avoids endpoint payloads, database schemas, file-by-file changes, and individual implementation steps; those belong in later subsystem designs.

The assessment is based on the repository state on branch `codex/jarvis-voice-poc` at commit `281fc9f` (`fix: persist Jarvis voice transcript`). The historical POC contract and acceptance evidence remain in [JARVIS_VOICE_POC.md](JARVIS_VOICE_POC.md).

## Overall assessment

The repository should be refactored rather than discarded. The POC already proves several important foundations:

- durable conversations, runs, ordered per-conversation events, transcript projections, artifacts, and branches;
- an agent loop with tools, questions, cancellation, compaction, and restart interruption;
- supervised background execution;
- a phone-oriented PWA, replayable SSE, Web Push, task controls, and text fallback;
- generated-app ownership and lease semantics;
- reviewed, provenance-carrying knowledge;
- local SQLite operation suitable for a personal single-user system.

The largest redesign is the boundary between the realtime conversational shell and the existing brain. The next largest is promoting voice-specific task and delivery concepts into shared AI Cockpit domains.

The target shape is a **modular monolith with a separate worker process**. FastAPI remains the local coordination and API boundary, SQLite remains the durable data boundary, and the Next.js application remains the product shell. The voice experience is a separately bounded frontend and coordination module, not a separately owned brain or database.

## Preserve and promote

### Conversation persistence

Keep the existing conversation, run, event, message, artifact, and branch foundations. Promote them into a modality-neutral conversation service that supports both standard chats and Jarvis daily logs while enforcing their different product rules through conversation_kind.

The existing monotonic per-conversation sequence and replay behavior are the right foundation for ordered conversation streams. They should not be expanded into a universal cross-domain clock.

### Agent runtime and tools

Keep the agent runner, tool registry, workspace boundaries, question and resume behavior, cancellation, trace artifacts, compaction, and model-provider abstraction as the core of the brain. Jarvis delegates into this runtime rather than recreating tools inside the Realtime shell.

Keep `RunSupervisor` as a useful supervision concept, but move durable execution ownership out of the live API process and into the worker boundary.

### Generated-app ownership and boundaries

Keep the generated-app registry, write roots, and exclusive app lease semantics. Generalize the lease concept to protect repositories, named workspaces, Wiki targets, and other shared mutable resources through stable typed identities.

### Knowledge review

Keep the reviewed-memory tables, provenance, file-backed approved knowledge, and approval UI. Reframe them as the first Wiki capability. The brain may read approved knowledge, while all durable additions remain proposals until quality has been evaluated in normal use.

### PWA, notifications, and fallback surfaces

Keep the phone-first PWA shell, installable support, task cards, notification deep links, visibility handling, and text fallback. Promote the active experience to the /jarvis command-center route rather than preserving /voice as the target product boundary.

Keep the current POC documentation and acceptance evidence as historical records. Future architecture documents should not rewrite the POC as though it had already implemented the target.

## Refactor into shared domains

### Primary voice conversation becomes a Jarvis daily log

Replace the singleton “primary voice conversation” concept with a transactional pointer to the current Jarvis daily log. Standard chats and Jarvis logs share common conversation and transcript infrastructure, while conversation_kind prevents either from changing into the other. Voice and typed turns on /jarvis both use the Jarvis shell.

The operational day changes at 04:00 Europe/Amsterdam. Do not split an exchange at the boundary; close the old log on the next accepted activation after idle. Do not create an empty log from page launch or task completion. Explicit fresh start closes the current log and creates the next same-day sequence. Closed logs remain visible and read-only in normal history; closure is separate from hidden archival.

Daily rollover is independent of background work. Tasks remain global, retain their causal origin, and may deliver a result into whichever daily log is current when presentation occurs. Initial older-conversation lookup is recent browsing plus exact or substring search; richer retrieval is deferred.

### Background runs become global tasks

Today a background task is a child conversation run whose domain state lives substantially in run metadata. Promote it to a first-class task with stable identity, origin, goal, status, priority, presentation policy, pending questions, results, and execution attempts.

Runs remain execution attempts under a task. Voice, text chat, and the task dashboard become three views and control surfaces over the same global task truth.

### Events split along domain boundaries

Retain append-only events, but keep conversation and task activity in separate ordered domain streams:

- conversation events remain monotonically ordered within each conversation;
- task events are monotonically ordered within the task domain;
- state and events are committed consistently within each domain;
- projections expose independent conversation and task cursors from one SQLite snapshot.

Cross-domain meaning comes from causal links such as `origin_turn_id`, `task_id`, `result_event_id`, and `presented_in_turn_id`, not from a global sequence. This preserves the two useful live views—audible dialogue and background-task state—without making unrelated task completion and speech events compete for an artificial total order.

### Audible speech becomes durable conversation truth

The current browser delivery cache avoids duplicate playback but treats an interrupted utterance as wholly dismissed and does not persist what was heard. Promote best-effort heard speech into the durable conversation projection.

For each Jarvis utterance, keep the generated transcript, a best-effort heard prefix, a completed/interrupted/unheard state, and source task or result links. Do not add cutoff confidence scores or precision-heavy playback accounting. The complete structured result stays in the task domain so the shell can naturally regenerate anything important that was not heard.

### Knowledge becomes a brain-facing Wiki boundary

Expand reviewed memory into explicit brain-facing search and proposal capabilities across approved Wiki documents, standard chats, and closed Jarvis daily logs. The Realtime shell receives selected facts from the brain rather than reading memory files directly.

## Replace the active voice path

### Batch speech orchestration becomes Realtime WebRTC

The current path uses browser Silero VAD, uploaded WAV segments, faster-whisper, a local task model, Kokoro synthesis, sentence-by-sentence playback, and tap-based interruption. This proves the end-to-end product flow but cannot deliver natural spoken barge-in while speech detection is paused during Jarvis output.

Replace the active media path with direct PWA-to-OpenAI WebRTC using `gpt-realtime-2.1` at low reasoning, with a FastAPI sideband connection to the same provider session. Keep a provider-neutral `VoiceSessionAdapter` so the coordinator does not depend directly on OpenAI event shapes. Realtime 2.1 Mini is a later evaluation option; Realtime 1.5 is not part of the implementation or test plan.

Local wake detection remains a small, separate foreground capability. During an active conversation, semantic VAD handles end-of-turn timing and the user may interrupt Jarvis by speaking. Jarvis never needs to talk over the user.

The local STT and TTS services cease to be required runtime components. They may remain temporarily as POC diagnostics during the refactor, but they are not the v1 fallback. Hosted voice failure falls back to text.

### Browser speech scheduling becomes a smaller media controller

The current `JarvisSpeechScheduler` owns synthesis, sentence queueing, VAD pausing, interruption, and delivery acknowledgement. Provider WebRTC takes over streaming audio and base interruption truncation. A smaller client media controller remains responsible for immediate output mute, provider control events, wake and follow-up state, and simple heard-prefix reporting.

### Direct voice execution becomes an asynchronous shell-to-brain bridge

The current `/api/voice/turns` path immediately creates an agent run for every accepted command. The Realtime shell should answer conversational turns itself and call a restricted sideband capability only when brain work is needed.

All delegation is asynchronous. The shell acknowledges immediately, dispatch returns an accepted request or task identifier, and structured facts, statuses, questions, and results arrive later through durable task events. The shell phrases those facts naturally instead of receiving prewritten TTS sentences from backend components.

## Retire or demote

The following POC concepts should not remain first-class in the target architecture:

- local faster-whisper and Kokoro as the default or fallback voice path;
- pausing speech detection whenever Jarvis speaks;
- tap-only barge-in;
- the “cloud next turn” toggle, because hosted Realtime is the normal shell while brain model choice remains a backend concern;
- a permanent singleton voice-only conversation;
- background-task identity encoded only in `conversation_runs.metadata_json`;
- task progress automatically projected as assistant transcript messages;
- browser-only truth for whether an utterance was delivered;
- voice-specific task APIs once the shared task boundary exists;
- the disabled background-page placeholder once the global task surface exists;
- a universal cross-domain ordering mechanism or cursor;
- a parallel, feature-flagged second voice architecture.

## Add the missing architectural capabilities

### Realtime session coordination

Add a bounded coordinator responsible for short-lived Realtime credentials, sideband control, context hydration, restricted shell capabilities, reconnect behavior, one-active-session ownership, and a replaceable provider adapter.

### Consistent context projection

Add a projection boundary that reads audible dialogue and relevant task state from one consistent SQLite snapshot. It exposes independent per-domain cursors and includes selected approved Wiki or historical-conversation facts supplied by the brain.

### Task worker and durable claims

Add one separate SQLite-backed worker process that initially claims and executes one task at a time. It uses the existing agent runtime, emits structured task events, and never handles live user audio. On restart, unfinished work becomes `interrupted` and requires explicit user action rather than automatic resume.

### Resource lease manager

Add generalized, durable exclusive-writer leases around named mutable targets. Every task has an isolated environment. If a task discovers a new mutable target, it pauses, releases leases, expands its declared target set, requeues, and reacquires the complete set. This avoids deadlocks and piecemeal ownership while preserving a later path to safe concurrency.

### Update timing and delivery

Add durable pending/presented state, update coalescing, and a replaceable `UpdateTimingPolicy`. At safe conversational boundaries, the same Realtime model makes an out-of-band text-only judgement to present now, fold the information into the next reply, or defer. Deterministic rules prevent speech over the user and duplicate presentation.

### Conversation lab

Add a coded scenario runner for fast human verification, with fake voice-session behavior and injectable commands, provider events, and task updates. Automated tests remain focused on deterministic persistence and concurrency rules, while real acoustic feel is evaluated manually.

## Intended ownership after the refactor

- **Voice PWA:** microphone, WebRTC, wake and follow-up UX, immediate playback stop, visible projections, and simple heard-prefix telemetry.
- **Conversation coordinator:** live shell session, floor and delivery state, context projection, update timing, and provider translation.
- **Conversation service:** standard conversations and Jarvis daily logs, turns, messages, audible history, lifecycle, recent/exact search, and ordered per-conversation streams.
- **Brain gateway:** direct-versus-delegate policy, asynchronous dispatch, and structured answer and result envelopes.
- **Task service:** global task truth, presentation state, pending questions, results, execution attempts, and the ordered task stream.
- **Worker:** agent execution, tools, checkpoints, isolated environments, and task events.
- **Lease manager:** exclusive mutation ownership for apps, repositories, workspaces, Wiki targets, and other named resources.
- **Wiki service:** approved knowledge, proposals, review, provenance, and retrieval.

No module other than the voice client and coordinator handles live audio. No Realtime provider invokes unrestricted backend tools directly. No worker decides what was audibly delivered. No module requires a global order across conversation, task, and Wiki activity.

## High-level migration posture

Refactor the current system in place. This is not a clean repository rewrite, and it is not a long-lived parallel or feature-flagged migration. Temporary POC voice breakage is acceptable while the shared conversation, task, and coordination boundaries change.

Preserve working durable foundations and historical documentation, reshape the shared domains, replace the active voice path, and remove superseded voice-specific concepts once the new path owns them. Avoid maintaining two competing sources of conversation, task, or delivery truth.

[ARCHITECTURE.md](ARCHITECTURE.md) continues to describe shipped behavior. It should be updated subsystem by subsystem only when implementation changes, while this assessment and the target document describe the agreed future direction.

## Implementation handoff

The decision-complete execution sequence now lives in [JARVIS_IMPLEMENTATION_PLAN.md](JARVIS_IMPLEMENTATION_PLAN.md). It defines the persistence, lifecycle, Realtime, task, worker, lease, presentation, wake, Wiki, notification, retirement, and verification phases. [JARVIS_IMPLEMENTATION_LEDGER.md](JARVIS_IMPLEMENTATION_LEDGER.md) is the restart point across sessions.

## Verification posture and remaining research

Verification is mostly human at first. Repeatable scenarios cover wake, long pauses, interruption, topic changes, continuation, task completion and questions, reconnect, duplicate delivery, the 04:00 daily rollover, and explicit same-day fresh starts. Automated coverage protects lifecycle, task claims, leases, stream ordering, idempotency, cancellation, restart interruption, presentation, and projection behavior.

Latency and provider cost are recorded but are not initial acceptance gates. The primary early acceptance question is whether Jarvis feels conversational and remains truthful under interruption and asynchronous task updates.

Remaining research is implementation-focused: choose and tune the lightweight local wake detector, find a reliable-enough heard-prefix estimate, validate SQLite behavior with the separate worker, exercise Realtime reconnect and interruption behavior, and evaluate whether the model-based update-timing policy feels consistent. These investigations do not reopen the high-level ownership decisions above.
