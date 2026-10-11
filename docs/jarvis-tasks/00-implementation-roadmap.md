# Jarvis task completion ledger

**Completed and accepted for personal use on 2026-10-11.** The user confirmed
that tasks work in the current Jarvis version, including creating a recipe from
a screenshot, and explicitly accepted all work as complete. M11D is closed by
that decision; there are no remaining milestones in this implementation plan.
See the [final acceptance record](reports/personal-use-accepted-2026-10-11.md).

## Completed milestones

- [x] **M0:** pinned native Codex contract and guarded Windows/API investigation.
- [x] **M1:** durable task control plane, turns/runs, controls and recovery.
- [x] **M2:** Live routing, admission, continuation and task inspection.
- [x] **M3:** shared context, capabilities and background orchestration.
- [x] **M4:** versioned workflows and the thin recipe/research reference tasks.
- [x] **M5:** generic result platform, collections, storage and mobile delivery.
- [x] **M6:** supervisor assignments, project coding, restart and cancellation.
- [x] **M7:** managed intent routing, execution preferences and serving tier.
- [x] **M8:** wake/capture/audio integration and admission, with the
  [user-accepted caveats](reports/m8-accepted-with-caveats-2026-10-07.md).
- [x] **M9:** dedicated subscription execution, ownership and real API handoff.
- [x] **M10:** combined implementation acceptance under the accepted M8 policy.
  [Frozen worker-tools evidence](reports/worker-tools-closure-2026-10-08.md)
  records fifteen required passing assertions and four accepted exceptions.
- [x] **M11A:** optional-password personal access and one-action startup.
- [x] **M11B:** one-time QR/link pairing and unified phone access.
- [x] **M11C:** automated personal-setup verification; the
  [setup report](reports/personal-setup-2026-10-09.md) records fifteen required
  passing assertions and final ready admission of 3676.2937 ms.
- [x] **M11D:** user acceptance of current phone/task use on 2026-10-11,
  including screenshot-based recipe creation.

## Maintained references

The [task architecture](../JARVIS_TASK_ARCHITECTURE.md), phase contracts 01–08,
[worker tool policy](09-worker-tool-policy.md),
[completed personal setup](10-personal-setup-and-phone-acceptance.md),
[Android audio controls](11-android-audio-controls.md) and
[operational runbook](rollout-runbook.md) remain references for maintenance.
The [phone guide](phone-test-checklist.md) is available for setup and future
troubleshooting; it is no longer an outstanding acceptance assignment.

Latest targeted repairs and evidence:

- [Phone setup](reports/phone-setup-fix-2026-10-10.md).
- [Browser voice/native screenshot binding](reports/phone-screen-fix-2026-10-11.md).
- [Worker database paths and recipe publication](reports/phone-database-fix-2026-10-11.md).
- [Persistent native audio controls](reports/android-audio-controls-2026-10-11.md).

## Implementation defaults and limits

These are initial configurable operational values, not new product restrictions or promises of measured performance:

- Existing worker heartbeat every five seconds; stale fallback at twenty seconds. Observe actual exits immediately. Cancellation never waits for that fallback.
- Local feedback wake on committed events, plus a one-second missed-wakeup/restart scan; consume up to 100 events per short batch. Scanning never calls a model merely because time passed.
- Provider connection timeout ten seconds and no-progress transport timeout 120 seconds. Streaming activity resets the no-progress timer. Long-running tasks have no overall deadline. A lost stream creates an uncertain native turn until reconciled; it does not authorize starting a duplicate turn.
- At most two adapter retries for safe transient reads/connects, with short jittered backoff and server retry guidance. Classify quota/billing separately. Never automatically retry capture/upload or unknown side effects.
- Retrieval uses native hosted web tools. Source errors are observations; the worker can select alternatives. Publication retains native event provenance and normal URLs without claiming independent complete downloads.
- Background working context initially targets 12,000 input tokens plus retrieved evidence as needed. Preserve current instructions, question and action preconditions; retrieve additional history instead of silently dropping required facts. This is context shaping, not a hard task thinking budget.
- One background decision invocation at a time. There is no worker-task timeout, mandatory planner/grader call or fixed total tool-round count.


## Accepted limits and evidence

Native Windows coding remains gated by its recorded workload failure; the
selected trusted command broker is the accepted coding mode. M8's four
exceptions remain explicit. The performance acceptance is one final ready
admission below 4.5 seconds with a two-second target; full-cohort speech,
cold-start, fresh-screen and capture-performance guarantees are not established.
User acceptance does not invent individual physical-check measurements or
all-day battery evidence. Public/multi-user deployment, additional connectors
and browser automation remain separate future scope.

Historical failures, sample counts, candidate identities and usage stay in the
[reports index](reports/README.md). The
[archived roadmap](reports/implementation-roadmap-history-2026-10-11.md)
preserves the former delivery plan and decision history. No paid provider rerun,
device reset, account change or runtime reconfiguration accompanies this closure.
