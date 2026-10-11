# Phase 5 — Result Pages, App Collections, and Delivery

Status: completed for the accepted personal-use scope on 2026-10-11. The
[completion ledger](00-implementation-roadmap.md) owns status and accepted
limits; this document retains the phase contract and historical evidence.

## Completion

This phase is complete under the [final personal-use acceptance](reports/personal-use-accepted-2026-10-11.md).
The [reports index](reports/README.md) retains candidate-scoped verification,
failed observations and the accepted M8 caveats. Historical implementation
notes below do not assign new work or override the completion ledger.

## Summary

Phase 5 gives accepted Jarvis tasks an immediate, stable result URL and a fast path from background work to a useful phone-friendly result. It introduces a unified app catalog, typed result persistence, immutable internal revisions, atomic publication, maintained renderers, live status delivery, and Android completion navigation.

The first maintained collection is **Documents**, backed by the generic `document.v1` result type. Phase 6 supplies a small `recipe.v1` schema, renderer and Recipes catalog metadata. Both collections use Phase 5's shared infrastructure; no specialised recipe browsing or controls are required. A one-off result does not generate or build a new frontend application; generated applications are reserved for genuinely new interactions or presentations that the maintained renderers cannot express.

The canonical result route is:

```text
/results/{public_id}
```

The URL is allocated before background execution begins, remains stable across updates, and uses the existing Cockpit session-cookie authentication over the user's Tailscale network.

Tasks are persistent sessions under Phase 1. A follow-up adds an execution run to the same task and can update the existing result or add another artifact/result. Result revisions record their producing run and attempt; they do not require a new task identity for each edit.

## Goals

- Allocate a stable result identity during task admission so Jarvis can return a usable URL immediately.
- For screenshot-dependent tasks, allocation follows Phase 3's capture preflight: require on-device captured/ready-to-upload status, waiting at most two additional seconds if pending. Rejected preflight creates neither task nor result. Upload remains after admission.
- Show truthful building, waiting, updating, ready, failed, and cancelled states from durable task facts.
- Publish validated typed content atomically and never replace a good result with a failed update.
- Make common result publication much faster than generating, installing, and building a new application.
- Organize results in maintained app collections such as Documents and, later, Recipes.
- Give generated apps and maintained collection apps one discoverable catalog without erasing their different runtimes.
- Deliver completed results through Android notifications that open the result on tap.
- Support autonomous end-to-end tests without routine dependence on Android UI, browser control, or computer-control tools.
- Preserve provenance, artifacts, and immutable revision history internally while keeping the initial user interface simple.

## Non-goals

- Public or anonymous sharing.
- Signed result links or a second bearer-token link system.
- Visible revision history, revision restore, archive, delete, or remove controls.
- Raw HTML, arbitrary scripts, or model-generated frontend source inside maintained result content.
- A recipe-specific renderer or final recipe workflow definition; those belong to Phase 6.
- A replacement task lifecycle, scheduler, worker protocol, or workflow runtime.
- Per-app databases or direct generated-app access to the Cockpit database.
- Automatically opening the Android app or WebView when a task finishes.

## Fixed Product Decisions

### Result identity and navigation

- Every publishable task result receives an opaque public ID and the canonical route `/results/{public_id}`.
- The route remains unchanged when the result is updated.
- The direct result route is a focused phone view rather than the complete desktop Cockpit shell and sidebar.
- The result page links back to its owning collection and source task.
- Android sends a completion notification and opens the result only after the user taps it.

### App organization

- Maintained result collections and generated applications share a logical `CockpitApp` catalog identity.
- **Documents** is the initial maintained collection for generic typed document outputs.
- **Recipes** registers Phase 6's minimal schema, renderer and catalog metadata and reuses the generic collection behavior. M4 requires the result view; M5 supplies the collection/catalog UI.
- A new generated app is appropriate only when the required presentation or interaction cannot be represented by a registered maintained result type.
- App-specific controls are allowed. Each maintained app registers its supported result actions and state namespaces.
- Demonstrate shared actions/storage with a per-result saved flag for Documents and Recipes. Recipe edits use ordinary task follow-ups; specialised recipe controls are deferred.

### Revisions and retention

- The user interface shows only the current revision in version 1.
- Published revisions are immutable and retained internally for debugging, provenance, and safe updates.
- Version 1 exposes no revision list or restore API.
- Results and their revisions are retained; version 1 has no archive or delete controls.

### Authentication and storage

- Result pages reuse the normal Cockpit session cookie and login flow.
- If the Android WebView lacks a valid cookie, it may redirect to login and then return to the original result route.
- Isolated password-backed configuration is the autonomous production-navigation test path; service fixtures may use development authentication without testing the login form.
- Each app receives namespaced JSON storage in the main Cockpit database rather than a separate SQLite file.
- Generated apps interact with storage only through scoped HTTP capabilities, never direct SQL or database-file access.

## Architecture

```text
Task admission
    |
    +-- allocate ResultPage + durable event
    |       |
    |       +-- stable /results/{id} is immediately readable
    |
Worker / workflow runtime
    |
    +-- task events and progress ----------------------+
    |                                                  |
    +-- validated publish request                      |
            |                                          |
            v                                          v
      Atomic result service                     Result event stream
            |                                          |
            +-- ResultPageRevision                     |
            +-- TaskArtifact                           |
            +-- result/task events                     |
            +-- current revision pointer               |
            +-- notification outbox                    |
            +-- task completion                        |
                                                       v
Documents / future app collection <---- focused /results/{id} page
                                                       |
                                                       v
                                             Android notification tap
```

The result service is a consumer of the task control plane, not a parallel workflow system. It relies on the task system for ownership, lifecycle, cancellation, waiting, worker supervision, and inspection. It adds the durable presentation layer needed after admission and publication.

## 1. Unified App Catalog

Introduce a `CockpitApp` model that represents anything shown as an app in Cockpit:

- stable internal ID and unique slug;
- title and description;
- kind: maintained collection or generated app;
- canonical route;
- renderer or runtime identity;
- enabled/build/status metadata appropriate to the kind;
- created and updated timestamps.

Existing `GeneratedApp` rows remain the source of generated-app build and source details. Add a catalog relationship and backfill every existing generated app with a corresponding `CockpitApp` record. Existing generated-app routes and API behavior remain compatible during migration.

Seed a maintained `documents` catalog entry. Do not seed `recipes` until its Phase 6 contract exists.

Add an aggregated app-catalog service and API. The Apps workspace consumes the catalog and visibly distinguishes maintained collections from generated apps. Keep `/api/apps` operational for existing generated-app clients while migrating them to the catalog abstraction.

## 2. Result Persistence

### `ResultPage`

Store the stable identity and mutable head of a result:

- internal ID and opaque unique `public_id`;
- owning `cockpit_app_id`;
- stable `result_kind`;
- stable owning task ID, initial source execution-run ID, and current/active publication or update-run ID where applicable;
- current revision ID and current revision number;
- publication state;
- title and search summary copied from the current validated revision;
- latest update outcome and warning metadata;
- state revision for optimistic state updates;
- created, updated, first-published, and last-published timestamps.

Supported persisted states are `building`, `updating`, `ready`, `failed`, and `cancelled`. Waiting-for-input and detailed progress come from the associated execution run's durable task facts rather than a competing result lifecycle. A task's later unrelated run must not make an earlier result appear to be updating or failed.

If an initial publication fails, the result becomes `failed`. If an update fails after a valid revision already exists, keep the previous revision current, return the page to `ready`, and expose a non-destructive update warning.

### `ResultPageRevision`

Store immutable publication records:

- result page ID and monotonically increasing revision number;
- schema/result kind and schema version;
- validated content JSON;
- provenance JSON;
- optional base revision ID/number;
- source task, execution run, attempt, and task-event identity;
- linked `TaskArtifact` ID;
- publisher/idempotency metadata;
- creation timestamp.

There is a unique constraint on `(result_page_id, revision_number)` and on the publication idempotency identity. Revisions are never edited in place.

### `ResultPageEvent`

Store a replayable ordered event log for result presentation:

- result page ID;
- monotonically increasing per-result sequence;
- stable event type and display-safe payload;
- related task event or revision where relevant;
- timestamp.

Events cover allocation, state changes, normalized progress, publication, update start, update failure, warning changes, cancellation, and terminal failure. They provide SSE replay without making the frontend infer durable state from transient worker messages.

### `AppDataRecord`

Provide app-scoped JSON storage:

- owning `cockpit_app_id`;
- optional result page ID;
- registered namespace and record key;
- JSON value;
- optimistic revision number;
- timestamps.

Enforce uniqueness by app, optional result, namespace, and key. Canonical result content does not live in app data; it remains in immutable result revisions.

## 3. Schema and Renderer Registry

Create a backend result-type registry keyed by stable `result_kind`. Each registration defines:

- owning maintained app;
- schema ID and version;
- Pydantic validation model;
- title and search-summary extraction;
- content normalization rules;
- allowed action definitions;
- allowed app-data namespaces;
- frontend renderer identity;
- optional canonical-content mutation handler.

The frontend has a matching renderer registry keyed by the same stable result kind. An unknown or unsupported type renders a safe error state with a link to the source task; it never attempts to interpret arbitrary content.

### Initial `document.v1` contract

The Documents app initially supports:

- title;
- optional summary;
- safe Markdown body;
- optional attachments referencing Cockpit artifacts;
- optional source links with labels.

Source URLs must use `http` or `https`. Markdown rendering must sanitize dangerous markup and links. The contract cannot contain raw HTML, JavaScript, stylesheet source, package definitions, or a model-selected renderer.

## 4. Atomic Result Service

Implement one transaction-aware result service that operates inside a single database transaction with the task repository.

### `allocate_result(...)`

- Validate that the result kind is registered and belongs to the selected app.
- Create the `ResultPage` in `building` state.
- Associate it with the admitted task.
- Append the allocation event.
- Return the canonical URL before work begins.
- Support admission idempotency so retries return the same result rather than allocating duplicates.

Where a task is known to produce a typed result at admission, allocation occurs in the same admission transaction. Generic tasks may allocate later when their registered output type becomes known, but still before substantive background publication work.

### Implemented admission/follow-up contract

An optional `result` object is separate from model/reasoning preferences on task
dispatch and continuation. Its backend-validated, run-owned contract is:

- `new`: allocate an additional maintained result. General agents default to
  `document.v1`; a pinned workflow uses its registered output schema. A queued
  follow-up receives its own public identity immediately without rebinding the
  current run's result.
- `update`: select an owned `public_id` and `expected_revision`. Only this
  association rebinds that result to an explicit follow-up. Publication must use
  the admitted base and the current head; queued edits never silently rebase.
- `answer`: optionally refer to an owned result, but do not rebind it or publish
  a revision. A workflow must already have the registered finish capability;
  its publication tool is removed from the verified runtime inventory and the
  backend also rejects direct publication calls.

Task detail and Jarvis inspection return current result metadata/URLs for
selection. Admission replays return the original association even after later
runs add other results. No worker argument can select an unregistered renderer,
capability or foreign task's result.

General agent final Markdown is adapted into the generic Document envelope and
uses the same fenced artifact/revision/journal/event/completion transaction as
workflows. The final response must be the complete Document body, not a workspace
file confirmation; no surrogate document file or new frontend build is needed.
Publication delivery intent is committed in that transaction and deduplicated by
the existing attention projector. Silent task policy still creates no outbox row.

Omitted fields preserve existing behavior: general agents still produce ordinary
task artifacts, and a legacy single-result workflow may implicitly update its
result. With multiple maintained results, a publishing workflow must select an
explicit association instead of guessing. Background repair tools retain their
existing authority surface; this change does not grant them arbitrary result
selection or publication rights.

### `publish_result(...)`

Within one transaction:

1. Lock or compare the current result head.
2. Verify task and active publishing-run ownership, attempt fencing, publication idempotency, and expected base revision.
3. Validate and normalize content through the registered schema.
4. Create a `TaskArtifact` containing the canonical typed payload or its artifact reference.
5. Insert an immutable `ResultPageRevision`.
6. Append result and task publication events.
7. Update the result's current revision pointer, title, summary, and state.
8. Complete the producing execution run where publication is its terminal success and update the task's current status transactionally. Preserve the persistent session and earlier run history; queued follow-up work must not be incorrectly marked completed.
9. Enqueue the Android/browser notification with the result URL.

Commit all steps together. A validation error, stale base revision, fencing error, or transaction failure changes neither the current revision nor the completion state.

Publication uses a logical idempotency key. Repeating the same successful request returns the original revision. A different request based on a stale revision returns a conflict and never overwrites a newer result.

Include execution-run and logical-operation identity in publication and completion-notification deduplication. A prior run's completion must not suppress the next run's valid revision/notification or mutate the newer run. Reopening a task for follow-up does not reopen an older terminal run.

### Failure handling

- Initial task failure with no published revision records a failed result state and task link.
- Required-context/upload failure after admission finalizes the task and failed initial result through backend handling even if no worker was ever claimed and no Live session is active. No automatic capture/upload retry or replacement-image wait is introduced by the result service.
- Failed updates preserve the existing revision and add a visible warning.
- Cancellation records a cancelled state only when no useful current revision exists; cancellation of an update preserves the current result with a warning or update outcome.
- Error payloads exposed to result clients use stable safe codes and user-facing summaries, not raw worker traces.
- Late failure/expiry events must not overwrite a successfully accepted upload or a newer result state. Apply the control plane's fencing, idempotency, and state-transition checks to these paths.
- Bind failure/cancellation handling to the run actually updating that result. A different follow-up run on the same task must not change the outcome of an unrelated result.

## 5. HTTP APIs

Add authenticated endpoints with existing Cockpit authorization rules:

```text
GET  /api/app-catalog
GET  /api/app-catalog/{slug}
GET  /api/app-catalog/{slug}/results

GET  /api/results/{public_id}
GET  /api/results/{public_id}/events
POST /api/results/{public_id}/actions/{action_id}

GET  /api/app-catalog/{slug}/data/{namespace}/{key}
PUT  /api/app-catalog/{slug}/data/{namespace}/{key}
```

The collection-results endpoint supports cursor pagination, search, and app-defined ordering. It returns compact current-only cards, not revision history.

The result snapshot includes:

- public and canonical identity;
- owning app identity and collection route;
- current validated content, when present;
- normalized display state and progress label;
- stable task identity/route and source/active execution-run identities;
- non-destructive warning information;
- registered actions available in the current state;
- state revision and content revision;
- timestamps.

No version 1 endpoint lists historical revisions or restores an older revision.

Result actions must be registered by the owning result type. The server validates their input schema, authorization, current state, and optimistic revision. An action that modifies canonical content creates a new hidden immutable result revision; UI-only state is stored separately in the registered app-data namespace.

## 6. Namespaced App Storage

App storage is a logical database area, not an unrestricted database:

- namespaces and value schemas are registered in trusted backend configuration;
- every request is scoped to its catalog app and, when applicable, a result page;
- writes use an expected revision and return a conflict on stale data;
- canonical result content cannot be mutated through app-data endpoints;
- large files and binary data belong in the artifact store;
- generated apps receive only scoped HTTP capabilities and never database credentials or SQL access.

Initial default limits:

- 64 KiB serialized JSON per record;
- 10 MiB aggregate logical quota per app;
- trusted configuration may raise a quota for a specific maintained app.

Quota accounting and conflict responses must be deterministic and covered by tests.

Implemented storage capabilities use a separate signing purpose, expire after
15 minutes and support app-wide generation revocation. Issuance requires an
owner session; generated-app ID bootstrap resolves catalog slug collisions.
Subsequent client data calls omit cookies and carry only `AppCapability` for the
registered `state` namespace. Revocation is rechecked inside data transactions.
These are HTTP authority limits, not isolation of arbitrary JavaScript: existing
generated routes are trusted same-origin project code. Running untrusted code
would additionally require an isolated-origin/host-mediated sandbox.

Document attachments currently support explicitly retained task-owned text,
Markdown and JSON artifacts. They exclude private context, HTML and URI-only
files. Downloads are authenticated, current-revision-only and forced attachments;
binary/file-backed attachment support requires an artifact-store extension.

### M5 reference interaction: saved result

Register the reusable `set_saved` action with a boolean `saved` value for Documents and Recipes. Store it in the owning app's `saved` namespace, keyed by result public ID, using the existing authorization and expected-revision rules. A missing record means unsaved. This is viewer/app state, so it must not publish a content revision, change the canonical payload, or start a worker. Display the same saved control in the common result/collection components. Recipe-specific storage or controls are unnecessary.

## 7. General Task Integration

The task system gains a result association without becoming coupled to a specific workflow representation.

- A task may declare an expected registered result kind at admission.
- General tasks whose final output validates as `document.v1` may publish into Documents.
- Registered specialized output types publish into their owning maintained app.
- Tasks requiring custom interaction may instead create or update a generated app.
- Task details show the stable result link as soon as allocation succeeds.
- Result snapshots derive waiting and progress from their associated execution run's durable task facts and normalized events.
- Jarvis context receives the visible result public ID, owning app, stable task ID, current execution-run ID, and current content revision so follow-ups can continue the correct task and result.
- Route feedback, new criteria, questions, and additions through the general `continue_task` contract. It appends a turn/run to the same task, loads retained context/evidence, and revises the existing result only when the request calls for it. A question about a result need not create a content revision.
- Mark an existing result `updating` only when a new run is associated with editing it; retain its current content during execution. An addition may create another artifact/result within the same task. Completion of one result does not erase session context or previous results.
- Ordinary continuation needs neither a new screenshot nor another task-specific conversation mechanism. Newly required context is resolved through the shared run-admission contract.

Phase 5 defines this integration seam independently of Codex adapter details or a workflow DSL/step representation. Codex is the selected harness; its native events do not bypass the result-service publication contract.

## 8. Frontend Experience

### Apps workspace

Update the Apps workspace to consume the unified app catalog:

- distinguish maintained collections and generated apps;
- open Documents as a collection rather than a generated runtime;
- list document results with title, summary, state, and update time;
- support search, pagination, and app-defined ordering;
- preserve existing generated-app management and runtime navigation.

### Focused result route

Implement `/results/[publicId]` as a focused responsive page optimized for phone use. It includes:

- result title and owning-app identity;
- current typed renderer;
- building/updating/waiting/failure/cancellation state;
- reconnect indication for lost live updates;
- non-destructive update warning when the last good revision remains visible;
- registered app-specific actions;
- links to the owning app and source task;
- copy and print controls.

It does not include the full desktop sidebar, revision history, restore, archive, or delete controls.

After 30 seconds without completion, the page says the task is taking longer and offers the task link. Thirty seconds is a product latency goal, not a failure deadline.

Serve authenticated result responses with private/no-store cache policy and a `noindex` directive.

## 9. Static Export Compatibility

Next static export cannot prebuild arbitrary future `/results/{public_id}` paths. Production must therefore use a runtime shell fallback:

- keep the normal dynamic route for Next development and server-capable operation;
- produce one exportable focused result shell in the static frontend build;
- have FastAPI serve that shell for `/results/{public_id}` before mounting static assets;
- let the client parse the public ID from `window.location.pathname` and fetch the authenticated snapshot;
- return normal API authorization and not-found behavior rather than embedding private data in the shell.

Add a production-build integration test that builds the static frontend, mounts it through FastAPI, and loads a previously unknown result ID route successfully.

The focused export also imports the existing login, maintained collection,
task detail/list and Apps screens. FastAPI maps allowlisted HTML shells to these
routes; arbitrary future task IDs use one task shell, just like result IDs.
Focused-export links and login returns load full documents instead of requesting
unbuilt Next RSC routes. The normal server-capable Next frontend retains its
client navigation. Preserve query strings and fragments through authentication,
reject network-path/backslash/control-character return URLs, and test the real
password form rather than relying only on API pre-login. The companion Apps
screen lists maintained collections; generated-app management and other legacy
Cockpit routes remain in the full Next frontend, not this focused export.

## 10. Dedicated Result SSE

`GET /api/results/{public_id}/events` is a result-specific SSE stream rather than reuse of the broader Jarvis stream.

- Send an initial snapshot event after authorization.
- Use monotonic per-result sequence values as event IDs.
- Accept `Last-Event-ID` and an explicit cursor for replay.
- Replay durable missed events before switching to live delivery.
- On an expired or invalid cursor, send a fresh snapshot and continue safely.
- Emit only normalized maintained status codes, labels, revisions, actions, and warnings.
- Never present speculative model narration as factual progress.
- Send heartbeats suitable for proxy and mobile connection survival.

The frontend reconnects with its last event ID, periodically reconciles with a full snapshot, and falls back to bounded polling only when SSE is unavailable.

## 11. Android Delivery

Update completion delivery so a successful result-producing task enqueues `/results/{public_id}` rather than `/tasks/{task_id}`.

- Notification title/body come from safe maintained templates and validated result metadata.
- The notification action reads **Open result**.
- Tapping opens the existing in-app WebView at the result path.
- Completion never auto-launches the WebView or steals focus.
- Questions and failures without a published result continue to link to the task.
- If login is required, the existing `next` parameter preserves the result route through authentication.
- Same-origin result navigation stays in the WebView; external source links open in the system browser.
- Notification delivery and open acknowledgement remain observable through the existing delivery path.

Apply [Phase 8](08-background-orchestration.md)'s shared attention contract: factual completion queueing does not wait for a model, and voice-active delivery projects the same durable attention item rather than duplicating it. Raw worker questions reach background judgment before promotion as unresolved user questions. Task state and history remain immediately inspectable. Opening/communicating a question update does not answer the underlying question.

No new Android authentication token is introduced in this phase.

Verified implementation uses direct activity PendingIntents for notification taps,
with best-effort open acknowledgement from the activity. This receipt accepts the
existing paired-device credential or owner cookie; VAPID/subscription management
remains owner-cookie-only. WebView cookies flush on page completion/backgrounding,
and system-bar/cutout insets keep navigation tappable. The model-free API37 harness
uses the production static shell, native UI and a separate read-only test-APK
hierarchy reader; its pairing/publication helpers are absent from production.

## 12. Security and Safety Requirements

- Require normal Cockpit authentication for catalog, result, event, action, and app-data APIs.
- Authorize every result through its owning task/app access scope even though the initial deployment is single-user.
- Use opaque public IDs that do not reveal database sequence or task count.
- Sanitize Markdown and validate all outbound URLs.
- Never execute content fields as HTML, JavaScript, CSS, templates, or component names.
- Reject unregistered result kinds, actions, namespaces, and schemas.
- Apply optimistic concurrency to publications, canonical actions, and app-data writes.
- Keep worker diagnostics, prompts, credentials, and raw exception text out of client result events.
- Add CSP, `X-Content-Type-Options`, `Referrer-Policy`, private/no-store caching, and noindex behavior appropriate to the focused page.
- Rate-limit result actions and app-data writes using the existing API protection pattern.

## 13. Observability

Record metrics and structured logs for:

- admission-to-result-allocation latency;
- result allocation-to-first-client-view latency;
- publication validation and transaction duration;
- task completion-to-notification-enqueue latency;
- result page first snapshot and first ready-content latency;
- SSE connection, reconnect, replay, and fallback counts;
- publication conflicts, idempotent replays, validation failures, and rollbacks;
- Android notification delivery and result-open acknowledgement;
- result type, app, task, execution run, attempt, revision, and public-result correlation IDs.

The existing admission path must retain its sub-two-second screenshot/context goal. Result allocation is a small local transaction and must not add remote calls, frontend builds, or model work to admission.

Include any capture preflight wait in end-to-end admission measurements and report capture rejection/timeout counts. Measure worker queue/start separately: the shared pool can be saturated, and allocating a result URL does not imply execution has started. Present truthful waiting-for-worker, waiting-for-upload, and paused state from task facts.

Implemented observations are stored in `result_metrics`, independently of task
transactions and result presentation events. The authenticated per-result
`/observability` endpoint returns durable counts, outcomes and recent duration
samples. Browser `/observations` receipts accept only bounded registered metadata
and deduplicate first milestones. Metric failure must not fail publication or
rendering; server writes have short contention/deadline bounds.

Monotonic timings describe server stages or elapsed browser view lifetime.
Allocation/publication-to-view receipts are labelled wall-clock/network proxies,
not exact paint timings. SSE replay counters describe server emission, not
verified client delivery. Notification transport and open acknowledgements do
not verify Android display, and retain their original run/revision correlation
even when an old notification is opened after a newer update. No prompt, body,
claim token or raw exception text belongs in metric records or structured logs.
See the [current measurements and restart proof](reports/m5-progress.md).

## 14. Autonomous Test Strategy

Routine tests use APIs, services, fake workers, and deterministic fixtures. They do not require browser control, computer-control tools, a microphone, a live model, or a physical Android device.

### Backend tests

- migration, generated-app backfill, and Documents seed idempotency;
- unified catalog compatibility and collection pagination/search;
- result allocation and admission idempotency;
- initial publish, update publish, artifact and provenance linkage;
- duplicate publish replay and concurrent/stale base conflicts;
- same-task follow-up revisions, additional artifacts/results, per-run completion-notification idempotency, and late previous-run events rejected;
- unrelated later runs leave existing result state/content intact, and question-only follow-ups do not publish unsolicited revisions;
- schema validation rollback and transaction failure rollback;
- initial failure, failed update preservation, cancellation, and warning semantics;
- no allocation on capture-preflight rejection and backend-owned failed-result transition on post-admission upload failure without worker execution;
- result action registration, authorization, and revision behavior;
- app-data namespace, schema, quota, and optimistic-concurrency enforcement;
- shared saved action for document and recipe results, missing-record default, persistence across reload, stale-write conflict, and isolation between apps/results without a canonical content revision;
- result authentication, unknown ID, hostile Markdown, URL validation, and response headers;
- SSE initial snapshot, ordered replay, reconnect, cursor reset, authorization, and terminal events;
- notification outbox targeting the result URL.

### Frontend tests

- catalog distinction between maintained and generated apps;
- Documents and Recipes using the same list, search, pagination, ordering and saved-control components;
- every result state and transition;
- `document.v1` Markdown, attachment, and source rendering;
- hostile content sanitization;
- update-warning behavior with the last good revision visible;
- reconnect and slow-task messaging;
- mobile-width layout, keyboard accessibility, copy, and print behavior;
- unsupported result-kind fallback;
- production static-export fallback for arbitrary IDs.

### Android tests

- notification payload chooses result URL on success and task URL on question/failure;
- action label is **Open result**;
- tap intent opens the correct WebView route;
- no completion path launches an activity automatically;
- login redirect preserves the result path;
- external links leave the WebView.

### Final integration test

Use one Playwright flow against an isolated password-backed local backend,
including unauthenticated navigation, wrong-password rejection and login return:

1. Admit a fake result-producing task and allocate its result.
2. Navigate directly to the unknown-at-build-time result route.
3. Observe the building snapshot.
4. Publish deterministic `document.v1` content through the fake worker/service.
5. Receive the ready state over SSE without a page reload.
6. Refresh and verify the committed result.
7. Submit a follow-up on the same completed task, verify that its new run reuses context/result identity, and publish a new revision at the same URL.
8. Submit another follow-up whose update fails and verify that previous content remains with a warning.
9. Deliver a late event from an older run and verify that the latest run/result is unchanged.

Android instrumentation is reserved for the final notification-to-WebView seam. Computer and browser control tools are used only for a final manual visual check when automated assertions cannot validate appearance.

## 15. Implementation Work Packages

### Early integration checkpoint

- Before the expanded platform below, bring forward only the stable identity/allocation and minimal maintained result view needed by a deterministic fixture task, plus notification navigation. Include only persistence/publication dependencies necessary for that seam; do not require full catalog migration/UI, collections, actions, or app storage first.
- Connect the real admission/capture/upload/control path to a fixture executor. Demonstrate normal completion, capture rejection, upload failure, shared-pool saturation, directory exclusion, default/immediate pause/yield, prompt cancellation, and checkpoint recovery.
- Keep these pieces compatible with the final result service. Connect Phase 4's early Codex proof before broad presentation expansion; this is an integration milestone, not a competing publication system or a new harness selection.
- Proceed to the full work packages below after the capture/execution/recovery path has been exercised.

### 5A — Database and catalog foundation

- Add catalog, result, revision, event, and app-data tables and constraints.
- Backfill generated apps and seed Documents.
- Add repository models and migration tests.
- Add the aggregated catalog API while preserving `/api/apps` compatibility.

### 5B — Typed publication core

- Add the backend result-type registry and `document.v1` schema.
- Implement allocation, atomic publication, idempotency, CAS, failure handling, artifacts, and outbox integration.
- Add unit and concurrency tests before frontend integration.

### 5C — Read, event, action, and storage APIs

- Add current snapshot and collection query APIs.
- Add durable result SSE with replay.
- Add registered result actions and namespaced app-data endpoints.
- Register the shared saved action/namespace for document and recipe results without changing canonical content.
- Complete authorization, quotas, headers, sanitization, and API tests.

### 5D — Maintained frontend experience

- Add frontend API types and renderer registry.
- Implement the common collection UI for Documents and Recipes and the unified Apps workspace; register `document.v1` and Phase 6's minimal recipe renderer.
- Implement the focused result route and all durable states.
- Add static-export fallback and frontend tests.

### 5E — Android delivery

- Route successful notifications to results.
- Update labels, WebView navigation behavior, and acknowledgements.
- Add JVM/instrumentation coverage for navigation and authentication return paths.

### 5F — End-to-end hardening

- Add deterministic fake-worker fixtures and the Playwright journey.
- Verify production frontend build through FastAPI.
- Measure allocation and completion-to-visible-result latency.
- Run one final mobile visual and notification-to-WebView check.

## 16. Completion Gate

Phase 5 is complete when:

- a task can receive a stable authenticated result URL during admission;
- the URL loads through both development routing and the production static export before content exists;
- valid `document.v1` content publishes in one atomic transaction with artifact, provenance, task completion, events, and notification;
- duplicates are idempotent per run/operation, stale updates conflict, and failed updates preserve the last good revision;
- same-task follow-ups can revise or extend results using retained context, with prior run outcomes preserved and late prior-run events fenced;
- Documents and Recipes appear as maintained collections beside generated apps, sharing search/pagination and collection behavior;
- registered actions and namespaced storage are demonstrated by a persistent per-result saved flag, with authorization/concurrency enforcement and no canonical content mutation;
- the focused page transitions from building to ready through replayable SSE and survives refresh/reconnect;
- Android success notifications say **Open result** and open the stable URL only on tap;
- hostile content, unregistered capabilities, unauthorized access, and quota violations are rejected;
- the routine backend, frontend, static-build, SSE, and Android suites run unattended without computer or browser control;
- result allocation fits the accepted M8 ready-path admission/context gate of one
  final sample below 4.5 seconds (two-second target), verified with the complete routing/capture path.

## Phase Boundary

Phase 5 supplies the generic publication, delivery, catalog, collection, action and storage platform. Phase 6 registers only the minimal `recipe.v1` schema/renderer and Recipes metadata and connects its reference definition to the shared services. Phase 4 owns workflow authoring/capability contracts. M4 verified typed publication and the small recipe view; M5 verified the full generic platform. M10 repeats its acceptance against the completed system. Recipe-specific controls, specialised browsing and culinary feature expansion remain deferred.
## Completion evidence

See the [completion ledger](00-implementation-roadmap.md) and
[reports index](reports/README.md). Earlier progress reports are historical;
M0–M11 are closed under the user's accepted scope and caveats.
