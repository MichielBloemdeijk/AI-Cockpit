# Clippings

The reference package for app files and functions: text clippings from
uploaded files and web pages, with a count every morning.

- **Files:** an upload is stored as `clips/<file name>` in the app's file store
  (`"files": {"quota_mb": 20}`).
- **Event function** `index_clip` runs after every change under `clips/` and
  keeps one `clip` record per file: its name, word count and a preview. It
  removes the record when the file is deleted.
- **HTTP function** `fetch_page` is what the UI's "Clip" button calls. It
  fetches the page (only `example.org` and its subdomains are granted), strips
  the HTML and stores the text as a clip file; `index_clip` does the rest.
- **Scheduled function** `daily_summary` runs at 07:00 and writes the record
  `summary/latest`: `{"clips", "words", "counted_for"}`.

`functions/clips.py` holds helpers the functions import by module name.
