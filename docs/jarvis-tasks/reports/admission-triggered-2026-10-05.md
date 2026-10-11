# Triggered admission follow-up — 2026-10-05

This report precedes the [queued telemetry follow-up](admission-telemetry-2026-10-05.md).
Its measurements and then-pending telemetry recommendation are historical.

Screenshot readiness and local event delivery now wake on committed database
changes. The two-second capture setting is a maximum failure deadline for a
required image, not a mandatory delay. A ready image proceeds immediately.
Optional images are checked once when the collection freezes, after the required
image is ready; an unavailable optional image no longer consumes that deadline.

The former polling loops were a simple way to discover changes in database state.
Commit notifications now broadcast to listeners. Readers snapshot their signal
before reading so a commit between the read and wait cannot be missed. Outer
transaction commit is the trigger; rollback and savepoint release do not publish
uncommitted state. Bounded recovery scans remain for writes from another process
or missed notifications. The database remains authoritative.

## Offline measurements

Compared with the [earlier baseline](admission-timing-2026-10-05.md):

- Commit to SSE generator receipt: median **8.76 ms**, p95 **10.09 ms**, maximum
  **15.78 ms**, 20 samples, zero failures. Baseline was 256.62 / 436.11 / 522.32 ms.
- Unavailable optional latest image: **47.75 ms**, compared with 2037.10 ms.
- Unavailable required current image: **2020.35 ms**, an expected deadline failure.
- Simulated captures ready after 100 / 300 / 1000 ms: preflight completed in
  **165.42 / 358.29 / 1066.91 ms**, respectively.

Capture cases have one diagnostic observation each, not a percentile cohort.
Measurements include local validation, database work and awaited observations.
They exclude real Android capture, provider latency and network transport and
are not a live admission acceptance pass. No paid API calls were made.

Raw evidence: [SSE samples](../../../data/admission-timing-triggered-2026-10-05/triggered-sse.json)
and [capture samples](../../../data/admission-timing-triggered-2026-10-05/triggered-pending-capture.json).

## Verification and telemetry

The focused signal/capture benchmark suite passed **8 tests**. Final targeted
regressions passed **78 tests in 86.79 seconds**, covering capture collections,
live delegation/admission, task control, recovery and Jarvis behavior. Signal
tests cover multiple listeners, read/wait races, rollback/savepoints,
cancellation and recovery when a notification is missed.

Telemetry remains synchronously awaited in this revision. The next useful change
is a bounded asynchronous queue with batched best-effort observation writes.
Another thread or event loop would add lifecycle and synchronization work without
addressing the main issue: admission currently awaits each observation. The
aiosqlite driver already executes SQLite operations on worker threads.

Only diagnostic observations should move to the queue. Authoritative task state,
events, ownership and admission commits must retain their durable contracts.
Queue saturation must report missing observations, and shutdown must drain with
a bounded deadline. Measure admission again after the change; cumulative
observation spans are not a guarantee of equivalent elapsed time savings.
