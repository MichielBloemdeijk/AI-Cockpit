# GPT-Live evaluation record

Status: complete. GPT-Live is the sole supported hosted voice provider for Jarvis.

The evaluation established that GPT-Live matches Jarvis’s desired conversational model: continuous speech interaction, natural interruption handling, and delegated substantive work. Jarvis keeps the local wake gate, backend authorization, durable task state, idempotency, notifications, and typed fallback under Cockpit control.

The implementation uses GPT-Live with Marin by default and managed Responses delegation through the existing restricted capability gateway. The browser and Android clients use the same Live session contract. Fake-provider mode remains available for deterministic developer-lab and regression tests.

Operational requirements remain explicit:

- A Live session is opened only after local wake or an explicit Arm action.
- Session closure is used when conversation ends; local wake listening does not require a cloud session.
- A spoken acknowledgment does not imply task completion. Durable task admission and completion remain separately recorded.
- Screen context, when requested, is captured and authorized independently of the voice stream.
- Raw audio is never persisted as application telemetry.

See [the task architecture](JARVIS_TASK_ARCHITECTURE.md) for provider-independent workflow guarantees and [the architecture reference](ARCHITECTURE.md) for the current module layout.
