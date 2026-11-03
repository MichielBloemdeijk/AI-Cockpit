# Jarvis GPT-Live migration

Status: implementation complete.

Jarvis now has one hosted voice path: GPT-Live 1 with Marin as the default voice and managed Responses delegation for substantive work. The browser and Android clients share the Live session, telemetry, replay, and close contracts. Fake-provider mode remains available for deterministic tests and the developer lab.

The migration removed the retired provider’s adapter, turn-control policy, engine selectors, barge-in endpoints, latency comparison harness, provider-specific tests, and obsolete configuration. Historical database migrations remain immutable; migration `20260914_0022_live_voice_surface` renames active voice tables and removes the retired policy column while preserving existing data.

Kept guarantees include local wake authorization, bounded wake-to-conversation audio handoff, durable task admission, idempotent tool execution, cancellation and stale-generation fencing, resumable live events, typed fallback, notifications, screen-context authorization, and non-voice Jarvis functionality.

Verification:

- Backend focused regression and migration suites pass.
- Frontend Vitest suite passes and lint is clean.
- Android unit tests pass with Gradle.
