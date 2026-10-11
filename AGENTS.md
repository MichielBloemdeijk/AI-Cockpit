# Project-wide agent instructions

## Development-stage priorities

AI Cockpit / Jarvis is in active development, not a production deployment.
Existing application data is disposable development/test data. Keeping the
implementation current, correct and well designed for the long term is more
important than preserving that data or backward compatibility.

- Prefer a coherent current architecture over legacy behavior. Update related
  consumers, schemas, fixtures, tests and documentation together.
- Remove obsolete code, aliases, fallback paths and compatibility layers when
  they no longer serve the intended design. Do not add or retain them merely to
  support old development data, schemas, configuration or clients.
- Apply necessary local development migrations and configuration/schema updates
  as normal implementation work. Scoped application-data resets or rebuilds are
  acceptable when they are the cleanest solution; do not require a backup or user
  approval solely to preserve disposable development data. Briefly report resets
  and meaningful compatibility changes.
- Still test migrations, recovery, cancellation and persistence behavior when
  they are part of the intended long-term contract. Disposable existing data does
  not make durable task semantics or security guarantees optional.
- Prefer agent-runnable tests and emulator acceptance. Clearly identify anything
  that cannot be tested autonomously; physical-device testing remains deferred
  as far as practical.

## Scope and safety

Disposable application data does **not** include source code, uncommitted work,
credentials, account configuration or unrelated user files. Before a reset,
resolve the exact project-owned application-data targets and stop any affected
writers. Never use a workspace/home-wide destructive operation.

This policy does not authorize production/external-service enablement, new account
access, device opt-ins or security weakening. Those still require appropriate
user direction. A future production rollout must explicitly revisit data
preservation and compatibility requirements rather than assuming this development
policy applies forever.

## Provider test discipline

- Run local/mock and network-blocked tests first. Paid live tests should follow
  promising offline results and investigate a specific remaining provider seam.
- Use a small targeted live pilot before broad suites. Stop on billing/quota/auth
  failures or clear regressions, repair locally, and avoid repeated unchanged
  corpora or model sweeps. Restored credits do not trigger paid reruns by themselves.
- Run required full live acceptance only for a stable frozen candidate; retain
  the required sample counts and failures. Reuse valid same-candidate evidence
  for report-only changes. Report request purpose/counts and available usage;
  unavailable cost stays unknown. This adds no runtime spending-limit system.
