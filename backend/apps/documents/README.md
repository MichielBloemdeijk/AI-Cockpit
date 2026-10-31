# Documents

A built-in app: documents Jarvis writes for the owner. The `research_screen`
and `screen_document` workflows publish into it, and so does a general Jarvis
task asked for a written result. The owner reads, searches and deletes them here.

## What it does

- **List:** every document, newest first, with its summary. A search box filters
  by title and summary as you type.
- **Document:** the title, the summary, the body rendered from Markdown
  (headings, paragraphs, lists, bold, italic, inline and block code, quotes,
  tables and links), and the sources as links that open outside Cockpit. Raw
  HTML in the body is shown as text, and only http(s) links are clickable.
- **Opening a document:** a notification, a link or Jarvis can open the app on
  one document (`cockpit.ready` gives its `target`, and `cockpit.onTarget` reports
  later ones). Opening a document calls `cockpit.viewing`, so Jarvis knows which
  one "this page" is.
- **Delete:** removes a document after a confirmation.

## Package

- `cockpit-app.json`: one result collection, `document`, and the two workflows.
- `schemas/document.json`: the record schema; workflow results and app writes
  are both validated against it.
- `ui/index.html`: the whole UI, including the small Markdown renderer, with no
  build step or dependencies.
- `tests/ui.json`: the UI tests the platform check runs before install.
- `workflows/`: `research_screen` (a sourced explanation of what is on screen)
  and `screen_document` (answers a question about the screen). Both publish
  `collection:document`.

A change ships as a new `version` in `cockpit-app.json`; Cockpit installs this
package on start only when it is newer than every installed version.
