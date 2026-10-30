# App platform: apps Jarvis builds, runs and maintains

Status: **proposed** (owner review pending), 2026-10-30. Follows the
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

- **Collection schema migrations:** when a new app version changes a collection
  compatibly, are existing records migrated by a function the builder writes, or
  validated lazily on read?
- **Function language:** Python only (matches the backend and the sandbox), or
  Node as well?
- **Where app UIs open on the phone:** inside the WebView shell, or through
  `show_on_phone` like results?
- **Data backup:** is `apps.db` included in the existing SQLite backup and
  rehearsal tooling from day one?
