"""Serve an arbitrary future result ID through a data-free, prebuilt Next shell."""
from __future__ import annotations

import base64
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import HTMLResponse


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = False
        self.parts = []
        self.hashes = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.active, self.parts = not dict(attrs).get("src"), []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            digest = base64.b64encode(sha256("".join(self.parts).encode()).digest()).decode()
            self.hashes.append(f"'sha256-{digest}'")
            self.active = False


SHELL_FILES = {
    "result": "result-shell.html",
    "task": "task-shell.html",
    "login": "login.html",
    "tasks": "tasks.html",
    "apps": "workspace/apps.html",
    "documents": "collections/documents.html",
    "recipes": "collections/recipes.html",
}


def result_shell_response(export_root: Path, page: str = "result", *, fallback_root: Path | None = None):
    # Only code-owned names may select files; no request path reaches the disk.
    filename = SHELL_FILES.get(page)
    if filename is None:
        raise HTTPException(404, "Unknown result companion page")
    shell = export_root / filename
    # Preserve pre-existing fixed-page deployments before the companion build
    # is installed. Result IDs still require the dedicated result export.
    if not shell.is_file() and fallback_root is not None and page != "result":
        legacy = fallback_root / filename
        if legacy.is_file():
            shell = legacy
    if not shell.is_file():
        raise HTTPException(503, "Result shell is not built; run npm run build:results")
    html = shell.read_text(encoding="utf-8")
    scripts = _Scripts()
    scripts.feed(html)
    from app.api.results import PRIVATE_HEADERS
    csp = f"default-src 'self'; script-src 'self' {' '.join(scripts.hashes)}; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    return HTMLResponse(html, headers={**PRIVATE_HEADERS, "Content-Security-Policy": csp})
