# Recipe worker database repair — 2026-10-11

The phone recipe admitted at 14:24 Amsterdam captured and uploaded both images,
then failed inside the subscription worker. Task
`eb9e4210-aad6-4e8e-adfa-d165ca0d05e7`, run
`39655553-ff2d-4fae-8212-8e083d9ee7b6`: `task_context_images`, `task_history`
and `task_question` returned SQLite `OperationalError: unable to open database
file`. No typed result was published. The retained failed task is not replayed.

The configured SQLite URL was relative to the backend. Codex launches its task
MCP bridge from the task's attempt directory, so the bridge interpreted that URL
relative to the wrong directory. This was a Jarvis backend configuration bug;
it does not establish a Chrome permission failure, database-lock problem or
provider-credit failure.

## Fix and missing coverage

SQLite file URLs now resolve against the backend root everywhere: normal and
observation engines, Alembic and migration locks, task workers and orchestration
handoffs. Driver/query settings are preserved; memory and non-SQLite URLs retain
their meanings. Runtime hands the bridge an absolute URL. Task ownership and
claim fencing remain enforced. No ACL changes, data resets or credential changes
are involved.

The previous emulator proof stopped after the two-image upload, while backend
image/history tests called the bridge functions in process with absolute test DB
URLs. Neither reproduced the worker's actual working directory. The new process
regression reproduced the exact observed error before the fix and passes after it.

Unpaid acceptance now uses the production bridge arguments and actual stdio MCP
processes launched from task directories. It verifies screenshot/history reads,
typed recipe publication, idempotent publication replay, process restart, durable
HTTP result retrieval, clarification/resumption and stale-claim rejection.
Migration/reopen coverage also changes the backend's working directory while
using a relative DB URL.

The Android browser-capture harness now admits `recipe_from_screen@1` and completes
that task through the actual worker bridge. It checks both actual Chrome images
and a ready saved recipe. Its isolated server also uses a relative DB URL. Tool
choices, recipe content and native research events are explicitly simulated;
this verifies transport/storage without claiming live recognition or generation.
The normal Android privacy contract stage includes these process regressions,
and now passes its declared HTTP cookie configuration to the contract subprocess.

## Validation

- The targeted backend run passed 83 tests; two optional live provider tests were
  skipped. Evidence: `data/phone-database-fix/backend.xml`.
- The initial API37 Chrome-to-worker run passed all six checks, including both
  image reads and publication from a restarted worker process. Evidence:
  `data/phone-database-fix/emulator/report.json`.
- Final capture contracts passed all 18 checks (`capture-contracts-final.xml`).
  Final API37 Chrome-to-worker acceptance passed all six checks
  (`emulator-final/report.json`), with both image roles read, two worker process
  starts, a saved recipe and idempotent publication replay.
- `data/phone-database-fix/candidate.json` verifies the running host's candidate
  and private APK availability. Host and final emulator use source digest
  `cb9b10695a419a1697332b1a8d4c70392e3d8cc1aaa2757a05dc56cb0ee4bdc9`;
  emulator configuration differs intentionally under the declared isolated
  `capture_privacy` profile. The unchanged APK SHA256 is
  `f82d4115987f9383174888cd6e9d4a0aa73baf4a0191b6cedcd0b97f0be443fc`.
- All checks make zero model calls. No paid recipe replay is required to reproduce
  or verify this database failure.

Jarvis is restarted with the repair. The Android APK is unchanged, so the installed
updated app and existing pairing can be reused. Retry the recipe from the phone;
physical M11D confirmation remains open. Earlier combined provider/gate evidence
still belongs to its original frozen candidate.
