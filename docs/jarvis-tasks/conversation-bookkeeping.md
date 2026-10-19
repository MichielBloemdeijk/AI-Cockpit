# Natural updates and conversation bookkeeping

Jarvis keeps two kinds of memory about task updates:

- **What the user asked for.** Each task has a durable notification preference
  (`requested`, `discretionary` or `suppressed`, with a reason and revision) in the
  task event history. Live saves it through the normal admission and
  update-policy tools. Requested outcomes reach Live as commentary, so Live is
  prompted to speak. Other outcomes arrive as quiet thinking context. Live decides
  wording and timing. There is no forced speech and no timed retry.
- **What Jarvis actually told the user.** A background conversation observer, the
  "bookkeeper", reads the dialogue (transcript fragments from both sides) plus
  client playback evidence. It decides per outcome whether it was communicated:
  complete, partial, none or uncertain. A complete finding is validated against
  result identity, staleness and client playback, then marks the attention item
  handled (`handled_by=conversation_observer`) and supersedes any queued away
  notification. Uncertain communication stays pending. It is shown to Live as a
  fact, not as an order to repeat.

## How the bookkeeper judges communication

- **It waits for the user's reaction.** An update is assessed only after the
  user's next turn has ended, or after about 6 s of silence following it
  (30 s at most in continuous conversation). User speech that arrives during
  inference defers the commit to the next batch.
- **It judges meaning from the dialogue.** If the user's reaction shows they did
  not take the update in (asks to wait, to hear it later or again, or talks over
  it with something unrelated), the outcome is `possibly_unheard`. It stays
  pending and Live sees that as a fact. A later status answer the user accepts
  counts as complete.
- **Validation is deterministic and narrow.** It checks result identity and
  staleness, ignores citations made before the outcome was known, and requires
  at least one cited utterance to be fully played by the client. Short telemetry
  holes are bridged only when the player position advanced through them.
- **The model sees short handles** (`a1`, `u2`, `r1`) instead of UUIDs.
- **One malformed model answer fails only that assessment.** Provider and
  transport failures still stop the observer.

The L1 replay (`python -m app.selftest replay`) re-checks these rules against
13 recorded real conversations with agreed truth (`app/selftest/replay_labels.yaml`).

`JARVIS_CONVERSATION_OBSERVER_MODE`:

| Value | Behavior |
|---|---|
| `off` | Previous behavior; still the default until adoption |
| `shadow` | Observes without committing |
| `enabled` | Observer-owned terminal acknowledgment |

Changing it requires a backend restart. Existing sessions keep their startup
configuration.

The observer uses `gpt-5.6-luna` with low reasoning, the priority tier and
structured output, and no tools. It never blocks voice or task control. It holds
no database transaction during inference. Its failures leave attention pending
and are shown at `/api/jarvis/live/observer`; there are no paid retries. Work and
evidence are durable in `conversation_observations`.

## History

The OpenAI Decisions integration (native transcript triggers for preference
detection and bookkeeping) was evaluated in October 2026 and removed: it showed no
benefit over the Responses observer and gives no transcript-independent delivery
proof. See the
[natural assistant comparison](reports/2026-10-18-natural-assistant-comparison.md)
and earlier reports for the evidence. The confirmation there stopped on an
interruption case, where the observer committed "handled" before seeing the
user's reaction. The follow-up design (reaction-aware bookkeeping, assistant
notes, promise tracking) is tracked in [self-testing](self-testing.md) and the
reports index.

Official interface references:
[transcripts and playback](https://developers.openai.com/api/docs/guides/live-conversations#transcript-deltas),
[thinking and commentary](https://developers.openai.com/api/docs/guides/live-migration#route-updates-and-corrections).
