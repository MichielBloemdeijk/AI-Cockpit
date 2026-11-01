# App platform: apps Jarvis builds, runs and maintains

Status: **approved** 2026-10-30; build steps 1 to 5 implemented (see
[Implementation notes](#implementation-notes)). Follows the
[vision](../VISION.md) principles "modular add-ons that Jarvis builds and
maintains" and "simple first, migrate when needed".

## Goal

I ask for an app, Jarvis builds it, and I or Jarvis can use it. Jarvis can
design, build, test, run, fix, update, roll back and reset apps on its own.
Apps can show a UI, store structured data and files, run background work (AI
agent or plain code), and share data with other apps. Built-in apps such as
Recipes and Documents use the same path, so Jarvis could build their equivalent
itself.

## Non-goals for this version

- A high-performance or multi-user system. One owner, one host, SQLite.
- Apps with their own always-on servers, ports or databases.
- Raw SQL or filesystem access for apps.
- A plugin marketplace or third-party distribution.

Each can be added later if a real need appears; the manifest leaves room for them.

## Concepts

**App.** A versioned package installed into Cockpit. It is data the platform
loads, never part of Cockpit's own source tree, so installing or breaking an app
never requires rebuilding or restarting Cockpit.

**Package layout** (`data/apps/<slug>/<version>/`):

```text
cockpit-app.json      manifest (below)
ui/                   prebuilt static web bundle; index.html is the entry
functions/            optional server code: one file per handler
workflows/<id>/       optional workflow definitions, same format as backend/workflows
schemas/              JSON Schemas referenced by the manifest
tests/                checks the builder runs before install
source/               editable source the bundle was built from (for later fixes)
```

**Manifest** (`cockpit-app.json`) declares everything the platform needs; nothing
is inferred from code:

```json
{
  "schema": "cockpit-app.v1",
  "slug": "recipes",
  "title": "Recipes",
  "version": 3,
  "ui": {"entry": "ui/index.html"},
  "collections": {
    "recipe": {"schema": "schemas/recipe.json", "indexes": ["title", "created_at"], "result_type": true}
  },
  "files": {"quota_mb": 200},
  "functions": {
    "import_url": {"file": "functions/import_url.py", "trigger": {"http": "POST"}},
    "weekly_digest": {"file": "functions/weekly_digest.py", "trigger": {"schedule": "0 8 * * MON"}}
  },
  "workflows": ["workflows/recipe_from_screen"],
  "grants": {
    "read": ["system.tasks", "documents.document"],
    "write": [],
    "network": ["*.example.org"],
    "secrets": []
  },
  "jarvis": {"summary": "Saved recipes from screens and the web.", "tools": ["recipe.search", "import_url"]}
}
```

## Platform services

Cockpit provides these services once, shared by every app. Isolation comes from
grants, not from separate infrastructure.

| Service | What it does | Built on |
|---|---|---|
| UI hosting | Serves `ui/` at `/apps/<slug>/` (replacing today's Next route of that name) inside a sandboxed iframe with a strict CSP and a short-lived capability token | `result_static` CSP serving, `app_capabilities` grants |
| App data store | Typed collections: records validated against the declared JSON Schema, declared indexes, simple queries, revisions | Generalizes today's `app_data_records` |
| File store | Per-app namespaces with quotas; upload, download, list, delete | Generalizes `task_context_blobs` |
| Function host | Runs handler code on demand in a sandboxed worker with timeouts and the app's grants | Task pool supervisor and `windows_job` containment |
| Triggers | HTTP, schedule, and Cockpit events (record changed, task finished) that start functions or workflows | Reminder scheduler and `commit_signals` |
| Workflows | Agent work: an app's workflows run as Codex tasks | Existing workflow runtime |
| Jarvis tools | Jarvis can list, open, query, call and manage apps | `jarvis_tool_catalog` |
| Lifecycle | Build, validate, install, activate, roll back, reset, uninstall | New, small |

### Storage

All app data lives in one separate SQLite file, `data/apps.db`, next to the core
database. App writes never block task writes and can't corrupt core data.
Cross-database transactions aren't needed: an app that reacts to a task does so
through a trigger.

- **Collections** are addressed as `<app>.<collection>`. Records are JSON
  documents, validated on write against the collection's schema. Indexes are
  declared in the manifest and created on install. Queries cover equality, range
  and ordering on indexed fields, plus a text match. Anything more complex is a
  function.
- **Sharing**: an app reads or writes another app's collection only through a
  declared grant, for example `"read": ["recipes.recipe"]`. A collection's
  schema is its contract. Schema changes that drop or retype fields require a
  new collection version, so consumers don't break silently.
- **System collections** expose Cockpit's own data read-only through the same API:
  `system.tasks`, `system.results`, `system.conversations`.
- **Files** are stored on disk under `data/app-files/<slug>/` and indexed in
  `apps.db`.

### Background processes

- **Code:** functions with a schedule or event trigger, run by the shared
  function host. Each run is bounded (timeout, memory, network allowlist) and
  logged with its inputs, output and errors.
- **Agent:** an app's workflows, started by a trigger, by a function, by the UI
  or by Jarvis. They run as ordinary Codex tasks with the existing fencing,
  recovery and notifications.
- Long-running services are out of scope for v1. A scheduled function or a
  workflow covers most needs; a supervised "service" trigger can be added later.

### Permissions

The manifest's `grants` are the complete list of what an app's UI, functions and
workflows may do. The platform enforces them at the data API, the file API, the
function host's network allowlist and the workflow capability list. Per the
owner's decision, installing an app or a new version activates it immediately,
with no approval step. Grants stay explicit so they can be shown, audited and
tightened later.

## Lifecycle and Jarvis control

Jarvis manages apps through one Codex workflow, `build_app@1`, plus a few direct
tools.

1. **Build or change.** `build_app@1` gets the request (or a bug report) and the
   current package source if the app exists. It designs, writes the code, builds
   the UI bundle, writes tests, and runs them in its workspace, including a
   headless-browser smoke test of the UI and test calls of every function.
2. **Validate.** The install step checks the manifest schema, schema
   compatibility with the previous version and with consumers' grants, bundle
   size, and that tests passed. It runs outside the model.
3. **Install and activate.** The new version is unpacked under its version
   number, indexes and collections are created or migrated, triggers are
   registered, and the active pointer moves atomically. The previous version stays
   available for rollback.
4. **Operate.** Jarvis tools:
   - `list_apps`, `open_app` (browser or phone), `query_app_data`,
     `write_app_data`, `call_app_function`, and `start_workflow` for app workflows.
   - `app_status`: health, recent errors, last runs.
   - `rollback_app`, `reset_app` (clears data, optionally keeping files),
     `uninstall_app`.
   - `fix_app` starts `build_app@1` with the error log and the failing test.
5. **Autonomous repair.** A failing function or UI error is recorded with context.
   Jarvis can start `fix_app` itself and announce the result in its usual
   understated way.

## Built-in apps become packages

Recipes and Documents move onto the platform as the first packages:

- `recipe.v1` and `document.v1` become JSON Schemas of collections that are
  marked `result_type`. Workflow results publish into them, replacing the
  Python result classes in `task_result_types.py`.
- **Results are app records** (decided by the owner on 2026-10-31). A published
  recipe is a record in the Recipes app, and the app shows it. The separate
  result page with its live progress stream, revisions table and "Saved" toggle
  goes: while a task runs, its progress is on the task page, and the
  notification opens the app on the record. A follow-up ("use grams instead")
  revises the same record. Everything published stays; the owner deletes what
  they don't want in the app.
- Their renderers move into each app's `ui/` bundle, replacing
  `result-renderers.tsx` and the per-collection Next pages and static exports.
- Their workflows move into the packages: `recipe_from_screen` into Recipes, and
  `screen_document` and `research_screen` into Documents. `text_report` has no
  app (`text-report.v1` publishes no collection) and stays a core workflow. The
  app's install record replaces `TASK_WORKFLOW_ALLOWLIST` for app-provided
  workflows.
- The seeded collection rows, the hardcoded `documents`/`recipes` slugs and the
  static shells for them are removed.

Acceptance for the migration: an existing recipe flow (screen, then workflow,
then a saved recipe in the app, opened on the phone) works end to end. After that,
`build_app@1`, given only the Recipes package's README and a request, can produce
an equivalent working app.

## What this replaces

The old generated-apps code goes once the new path works: `app_registry` and
live-route copying into `frontend/app/apps`, `/api/apps`, the `generated_apps`
table, the `generated_app` task target, `frontend/app/apps/[slug]`, and the
`generated-apps` frontend folders. The 15 old app folders were already deleted on
2026-10-30. `lib/app-storage.ts` is replaced by the platform's client SDK, which
covers data, files, functions and workflows.

## Build order

Each step ends in something usable and tested.

1. **Packages, hosting and data.** Manifest, `apps.db`, collections API, UI
   hosting with capability tokens, install/activate/rollback, and the
   `list_apps`/`open_app`/`query_app_data` tools. Test: a hand-written sample
   package installs, renders and stores data.
2. **Builder.** `build_app@1` with test execution and autonomous `fix_app`.
   Test: Jarvis builds a small app (for example tic-tac-toe with saved scores)
   from a spoken request.
3. **Recipes and Documents as packages.** Migrate them, including result
   publishing into collections, then remove the old result-type and collection
   code. Test: the acceptance above.
4. **Functions and triggers.** Shared function host, HTTP/schedule/event
   triggers, file store, network allowlist. Test: an app with a scheduled function
   and a file upload.
5. **Cross-app sharing and system collections.** Grants across apps, schema
   versioning, `system.*` collections. Test: a meal planner that reads recipes.
6. **Remove the old generated-apps code.**

Steps 4 and 5 can swap if the first real app needs sharing before functions.

## Open questions

- ~~Collection schema migrations~~: stored data decides (owner's decision,
  2026-11-01). A version may change a collection in any way when no other app
  has a grant on it and every stored record fits the new schema; shared
  collections only gain optional fields. Code that rewrites records comes when
  a real need appears.
- ~~Function language~~: Python only (owner's decision, 2026-11-01). Node can
  be added if an app needs it.
- ~~Where app UIs open on the phone~~: in the WebView shell, through the same
  phone-display path as `show_on_phone` (settled in step 1).
- **Data backup:** is `apps.db` included in the existing SQLite backup and
  rehearsal tooling from day one?

## Implementation notes

### Step 1 (2026-10-30)

Code lives in the `backend/app/app_platform/` feature package; callers import
only from `app.app_platform`.

- **Packages:** `cockpit-app.v1` manifests are validated completely by
  `manifest.py`. The sections reserved for later steps (`functions`,
  `workflows`, `files`, `triggers`) are rejected rather than ignored. The
  reference package is `app_platform/samples/notes/`.
- **`apps.db`:** `store.py` owns its own engine and models; the schema is created
  from them on start (`ensure_apps_database`), so there is no migration chain to
  drift. Declared indexes become partial expression indexes on
  `json_extract(value_json, '$.<field>')`.
- **Records:** JSON Schema (2020-12) validation on write, revisions with
  `expected_revision` (0 means "must not exist"), equality filters and ordering
  on top-level fields, 256 KiB per record.
- **Lifecycle:** install from a folder under `app_builds_dir`, through
  `POST /api/app-platform/apps/install` (owner only). Versions must increase and
  stay compatible with the active version's schemas. Every version is kept;
  `POST /apps/{slug}/rollback` moves the active pointer back.
- **UI hosting:** `/api/app-host/<slug>/<version>/<path>` serves `ui/` with
  `CSP: sandbox allow-scripts …; connect-src 'none'; frame-ancestors 'self'`, so
  an app's frame has an opaque origin and no network access. The Cockpit page
  `/apps/<slug>` frames it and serves the SDK
  (`/api/app-host/_sdk/cockpit-app.js`) through a `postMessage` bridge
  (`frontend/lib/app-platform.ts`).
- **Scoping:** the bridge calls the data API with an `AppPlatform <token>`
  minted per app (15 minutes, renewed on 401) and without cookies. The server
  restricts that token to its own app's data; it reaches no other Cockpit API.
  Owner and paired-device sessions can read any app's data, which is how Jarvis's
  tools and future builders work.
- **Jarvis tools:** `list_apps`, `open_app` (opens `/apps/<slug>` on the phone
  through the same display event as `show_on_phone`; the Android app now
  accepts `/apps/...` paths), and `query_app_data`.

Deliberate simplifications to revisit:

- UI bundles are served without authentication. They hold code, not data, and an
  opaque-origin frame can't send the owner's cookies on its sub-resource
  requests. If a bundle ever needs to be private, put a short-lived token in its
  path.
- The old generated-apps host page at `/apps/[slug]` was replaced by this one in
  step 1 rather than in step 6. The old system had no installed apps left.

### Step 2 (2026-10-31)

- **Builder workflow:** `backend/workflows/build_app/1` is an ordinary Codex
  workflow (input `text-request.v1`, output `app-build.v1`). It keeps local
  shell and file editing in its sandboxed workspace and drops research,
  subagents and skills. The package lives in `./app` in the task workspace.
- **Capability `app.package@1`** adds two bridge tools:
  - `app_checkout` copies the active package of an installed app into `./app`
    and returns its next version, record counts and recent errors.
  - `app_check` runs the same check as installation.
- **The check runs outside the model** (`app_platform/check/`). It covers the
  manifest, size limits (20 MiB, 2000 files), the version and compatibility
  with the installed app, and the UI.
  - The UI runs in headless Chromium (`check/harness.mjs`, Playwright from the
    frontend) under the real CSP sandbox, framed by a stand-in host whose
    bridge keeps data in memory.
  - Every package gets a "loads" test that fails on console errors, uncaught
    errors and blocked network use. It also runs the package's declarative
    tests from `tests/ui.json`: fill, click, press, select, check, wait, and
    the text, count and record expectations.
  - Records the UI wrote are validated against the collection schemas
    afterwards.
  - Tests are data, never code, so checking a package never runs model-written
    code outside the browser sandbox.
- **Install on publish:** `result_publish` for `app-build.v1` checks and
  installs `./app` before it completes the run. If the check fails, nothing is
  installed and the problems go back to the builder. `slug` and `version` in
  the result come from the installed package, not from the model. Installing
  the same package again is a no-op (by package digest), so retries are safe.
  A replay without execution authority installs nothing.
- **Error reports:** the SDK reports uncaught errors and unhandled rejections
  (at most 20 per page load). The bridge posts them with the app token to
  `POST /apps/{slug}/errors`. They are stored deduplicated per version and
  message in `app_errors` (the latest 50 per app).
- **Management:** `GET /apps/{slug}/status`, `POST /apps/{slug}/reset`
  (all records or one collection) and `DELETE /apps/{slug}` (versions,
  records, errors, indexes and files). The last two are owner-only; app tokens
  never reach management.
- **Jarvis tools:**
  - `build_app` starts the workflow for a new app, or for a change when `app`
    is given.
  - `fix_app` starts it with the app's reported errors. It declines when there
    are none and the user named no problem.
  - `app_status` reports versions, record counts and errors.
  - `manage_app` handles rollback, reset and uninstall. The tool description
    asks Jarvis to confirm before reset or uninstall.
- **Results:** the task page shows an `app-build.v1` result with an "Open app"
  link.

Deliberate simplifications to revisit:

- Autonomous repair is available but not automatic. Jarvis sees errors through
  `app_status` and can start `fix_app` when it judges that useful. Nothing yet
  starts a repair on its own when an error arrives; that belongs with the
  triggers in step 4. (Done in step 4: automatic repair.)
- Builds and fixes are separate tasks, each with its own result. They don't
  revise one maintained result.
- `build_app@1` must be enabled with its digest in `TASK_WORKFLOW_ALLOWLIST`,
  like every workflow.

### Step 3 (2026-10-31)

- **Results are app records.** A workflow whose output is a result collection
  names it `collection:<name>` in its `workflow.yaml`; it is pinned as
  `collection:<app>.<name>`. Publishing validates the content against the
  collection's JSON Schema (plus the core checks: a title, task event
  citations, research evidence and safe source URLs) and writes it as a record.
  A new result's record key is the id of the run that created it. A general
  agent asked for a maintained result publishes `collection:documents.document`.
- **The core keeps only a link** (`task_results`: task, app, collection,
  record key, title, last publishing run). Migration `0042` drops the result
  pages, revisions, events and metrics, the seeded collection rows and the
  saved flags.
- **Revisions are record revisions.** An update is fenced by the record's
  revision as admitted. An edit the owner makes in the app meanwhile makes
  Jarvis's update fail instead of overwriting it, and a deleted record is not
  revised (a plain follow-up then becomes an ordinary answer). Workers see
  `maintained_result` with the live record and its `revision`.
- **Two databases, one order.** The record is written to `apps.db` before the
  task transaction commits. If that commit fails, the retry finds the identical
  record and links it. A record whose task never completes is possible but
  harmless.
- **Links and "this one".** A result opens at
  `/apps/<app>?collection=<c>&key=<k>`. The host page passes that `target` to
  the app (`cockpit.ready`, `cockpit.onTarget`) and records the view; apps call
  `cockpit.viewing(collection, key)` when the owner opens a record. The latest
  view (`app_views`) is what Jarvis's situation reports as
  `last_opened_result`, with the task that made it if any. Notifications, the
  task page, `show_on_phone` and the phone's open policy use these links. The
  phone needs a new APK for the record links.
- **Workflows in packages.** `workflows: [ids]` in the manifest, each in
  `workflows/<id>/` with the usual `workflow.yaml`, instructions and fixtures.
  They are validated when a package is checked or installed. An app workflow
  may not use `app.package@1`, and its id must not collide with a core or other
  app's workflow. Installing an app enables its workflows; the allowlist stays
  for core workflows only. A follow-up on a task whose app workflow changed
  since continues with the workflow's current version.
- **Built-in apps** live in `backend/apps/` (Recipes, Documents) and are
  installed on start when their version is newer than every installed one, so
  a version Jarvis built is never overwritten. Each has a list with search, a
  record view (Documents renders Markdown itself, without network or a build
  step), delete, and UI tests. `recipe_from_screen` moved into Recipes;
  `research_screen` and `screen_document` into Documents.
- **Check harness:** a test may open the app on a record (`"open"`), and
  confirmation dialogs are accepted. Seed records no longer break the stand-in
  host page when they contain `</script>`.
- **Removed:** the result API and its SSE stream, result telemetry and the
  observation queue, the result and collection pages and static shells, the
  saved flag, document attachments (never used), and the frontend result
  renderers and collections.

- **Live pilot:** one Codex task through Jarvis's `dispatch_task` wrote a
  Document. It became a record in the Documents app, and its notification
  opened the app on it in the real host. That pilot led to two fixes: a title
  made from a Markdown heading summary is now plain text, and Documents
  (version 2, installed on start) no longer repeats the title when the body
  opens with it.
- **Not yet verified:** a screen workflow (recipe or research) end to end on
  the phone, which needs the phone and a new APK.

Deliberate simplifications to revisit:

- A record keeps only its content. The task provenance (event citations) stays
  in the task's artifact, and the revision history is in the task's artifacts,
  not in the app.
- An app can't yet ask Jarvis to revise a record the owner created in the app;
  Jarvis can only change records a task made.
- The old Android emulator acceptance scripts under `android/scripts/`
  (`verify-*.py`) still use `/results/` links and need updating before the next
  emulator acceptance run.

### Step 4 (2026-11-01)

- **Functions.** `functions: {name: {file, trigger?, timeout_seconds?,
  description?}}` in the manifest; each file in `functions/` defines
  `def handle(input, cockpit)` and returns JSON. Installing parses the file
  (it never runs it) and rejects a missing or async `handle`.
- **Function host** (`function_host.py`, `function_runner.py`). Each run is a
  fresh `python -I -B` process: a timeout (30 s default, 300 s at most), a
  512 MiB Windows job-object memory limit, at most four runs at once, and an
  environment without the backend's secrets. The runner talks JSON lines over
  stdio; `cockpit.data`, `cockpit.files` and `cockpit.fetch` are served by the
  host with the app's grants. Before the app's code is imported, an audit hook
  blocks sockets, processes, native libraries, file writes and reads outside
  the Python installation and the package's `functions/` folder.
- **Isolation is a soft guard** (owner's decision, 2026-11-01): it stops
  mistakes and keeps network and data access going through the grants, but
  code written to escape it can (for example through an already loaded native
  module). An OS-level sandbox (AppContainer or a restricted user) is the
  upgrade if apps ever run code the owner didn't ask Jarvis for.
- **Triggers.** `{"http": "POST"}` lets the app's UI call the function
  (`cockpit.functions.call`); Jarvis and the owner can call any function
  (`call_app_function`, `POST /api/app-platform/functions/{slug}/{name}`).
  `{"schedule": cron}` uses five-field cron in the owner's time zone
  (`cron.py`, no dependency). `{"event": "record_changed", "collection"}` and
  `{"event": "file_changed", "prefix"}` run after a change.
- **Runtime** (`runtime.py`): one loop in the API process
  (`APP_RUNTIME_ENABLED`). Changes are queued in `app_events` in the same
  transaction as the write, so a restart loses none; one app's changes run in
  order, different apps side by side. Event chains stop after three levels
  (an error tells the builder why). Schedules keep their next time in
  `app_schedules`; a new or changed schedule starts counting from now, and
  runs missed while Cockpit was off collapse into one.
- **Runs and errors.** Every run is kept in `app_function_runs` (the latest
  100 per app). A failure is also an app error, the same evidence UI errors
  give; `app_status` shows functions, next scheduled times, recent runs and
  stored files.
- **Automatic repair** (`APP_AUTO_REPAIR`, on by default). When an app's
  active version reports errors that arrived after its last repair started,
  the runtime starts `build_app@1` with them (`services/app_repair.py`), at
  most once a day per app. It is an ordinary build task, so its completion
  notifies like any build; nothing announces the start.
- **File store** (`files.py`). `files: {quota_mb}` enables it (25 MiB per
  file). Bytes live under `app_files_dir/<slug>/<id>`; names are validated
  paths that never reach the filesystem. The API serves downloads as
  attachments with `CSP: sandbox`, so a stored HTML file can't run in
  Cockpit's origin. `reset` keeps files unless asked (`files: true`);
  uninstall removes them.
- **Network.** `grants.network` lists hosts (`*.example.org` covers the domain
  and its subdomains). `cockpit.fetch` refuses other hosts and names that
  resolve to non-public addresses, follows redirects itself and checks every
  hop, and limits bodies to 5 MiB.
- **SDK and bridge.** `cockpit.files.list/put/get/delete` (Blobs over
  `postMessage`) and `cockpit.functions.call`.
- **Package check.** Every function needs a test in `tests/functions.json`.
  Those tests run the real code in the function host against in-memory
  records and files, with mocked fetches whose URLs must still be granted. UI
  tests gained file seeds, function results, an `upload` step and
  `expect_files`.
- **Reference package:** `app_platform/samples/clippings` uses every part: an
  upload, an event function, an HTTP function with a network grant and a
  scheduled function.
- **Builder:** `build_app@1`'s instructions cover functions, triggers, files,
  grants and function tests.
- **Live pilot:** one Codex task through Jarvis's `build_app` built "Plant
  Journal" (plants, photo uploads with a note, and an 08:00 check for plants
  without a photo for seven days). It passed its own check and installed. In
  the real host a photo upload landed in the file store, and the live runtime
  ran the scheduled function when it fell due (155 ms) and filled the
  check-on list. Two gaps it showed: the app never shows the photos it stores,
  and its UI tests don't cover the upload. Since then the instructions require
  showing stored photos, and the check requires an upload test when the UI
  uploads files and a seeded-files test when it shows them.

Deliberate simplifications to revisit:

- Triggers start functions only. A function can't yet start an app workflow
  (agent work); that comes when an app needs it.
- A tick waits for its runs, so a long function delays the next schedule
  check by up to its timeout.
- Events are delivered at least once: a crash during a run repeats it. The
  builder is told to make event functions idempotent.
- `apps.db` and the app file store are not in a backup routine yet; nothing
  in Cockpit has one (open question above).

### Step 5 (2026-11-01)

- **Grants.** `grants.read` and `grants.write` list other apps' collections as
  `<app>.<collection>`, and `system.tasks`, `system.results` and
  `system.conversations` (read only). Write includes read. An app uses the
  qualified name everywhere: `cockpit.data.*` in its UI and functions, test
  seeds and `record_changed` triggers. The data API resolves it in
  `records.py`: without the grant the call fails, and writes are validated
  against the owning app's schema and trigger its event functions. An app
  token still reaches only its own app's routes; the grant decides what those
  routes may reach. Installing needs the granted app and collection to exist;
  the slug `system` is reserved.
- **Watching another app's data.** A function may subscribe to a granted
  collection (`{"event": "record_changed", "collection": "recipes.recipe"}`).
  A change is queued once for the owner and once for each watcher, which sees
  the qualified name and the owner's slug in its input.
- **Schema changes: stored data decides.** Adding optional fields is always
  allowed. Any other change (`lifecycle.schema_change_problems`) needs the
  collection to be unshared and every stored record to fit the new schema; a
  removed collection must be empty. Installation and the package check apply
  the same rule and name a record that doesn't fit. `app_status` shows
  `used_by`.
- **System collections** (`system.py`) are read-only views of the core
  database with the usual record shape: tasks (goal, status, summary,
  timestamps), results (the task, app, collection, key, title and url) and
  conversations (title and timestamps; `get` adds the latest 100 final
  messages). Queries filter and order the latest 500 entries in memory.
- **Builder.** A new bridge tool, `app_catalog`, returns every installed app's
  collections with schemas, record counts, an example and who uses them, plus
  the system collections. The instructions cover grants, qualified names,
  watching, system data and the schema rule. Package checks validate seeds of
  granted collections against the owner's schema and refuse writes to
  read-only grants, in UI tests as well as function tests.
- **Reference package:** `app_platform/samples/meal-planner` plans dinners from
  Recipes through a read grant and follows renamed or deleted recipes with an
  event function.

Deliberate simplifications to revisit:

- Uninstalling or resetting an app others read is allowed; their reads then
  fail or come back empty. Jarvis sees the failures as app errors.
- Rolling an app back to a version with an older schema doesn't check stored
  records against it.
- System collections can't trigger functions yet (for example "when a task
  finishes"); apps poll them from a scheduled function instead.
- Grants are all-or-nothing per collection: no field- or record-level limits.
