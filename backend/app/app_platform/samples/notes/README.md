# Notes (reference package)

The smallest complete `cockpit-app.v1` package: one collection, a static UI and
the SDK. Builders start from this layout.

- `cockpit-app.json` declares the app, its UI entry and its collections.
- `schemas/note.json` is the JSON Schema every `note` record is validated
  against on write. Indexed fields (`pinned`) must be top-level properties.
- `ui/index.html` loads `/api/app-host/_sdk/cockpit-app.js` and uses
  `cockpit.data.*`. The UI runs in a sandbox without network access; all data goes
  through the SDK.
- `tests/ui.json` holds declarative UI tests the platform check runs in
  headless Chromium before install (see `app_platform/check/`).

Install a copy by placing the folder under `data/app-builds/<name>/` and calling
`POST /api/app-platform/apps/install` with `{"source": "<name>"}`. A new version
needs a higher `version` and must stay compatible with the stored records: add
optional fields, don't remove or retype them.
