"""Maintained docs must point at files that exist.

Checks paths, not wording: relative Markdown links, and backticked file paths in
the current docs. History (`docs/history`, `docs/jarvis-tasks/reports`, audits)
is exempt from the path check because it describes what existed then.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TRACKED = [f for f in subprocess.run(
    ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout.split("\n") if f]
TRACKED_SET = set(TRACKED)
BARE_NAMES = {Path(f).name for f in TRACKED}

MAINTAINED = ["README.md", "AGENTS.md", "frontend/README.md", "android/README.md"] + sorted(
    f for f in TRACKED if re.fullmatch(r"docs/(jarvis-tasks/)?[^/]+\.md", f))
# A frozen copy of the old ARCHITECTURE.md, whose links were written for docs/.
FROZEN_SNAPSHOTS = {"docs/jarvis-tasks/reports/architecture-before-2026-11.md"}
LINKED = MAINTAINED + sorted(
    f for f in TRACKED
    if f.startswith(("docs/history/", "docs/jarvis-tasks/reports/")) and f.endswith(".md") and f not in FROZEN_SNAPSHOTS)

LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)\s]*)?\)")
CODE_PATH = re.compile(r"`([A-Za-z0-9_./\[\]()@-]+\.(?:py|ts|tsx|kt|mjs|js|md|ps1|json|yaml|toml))(?::[0-9,-]+)?`")
SEARCH_ROOTS = ["", "backend/", "backend/app/", "backend/app/services/", "backend/tests/", "frontend/", "frontend/lib/",
                "android/", "android/scripts/", "android/app/src/main/java/nl/sander/aicockpit/jarvis/", "docs/", "docs/jarvis-tasks/"]

# Paths that are not repository files: runtime output, files inside an app package or
# run folder, or things a doc says were removed.
NOT_REPOSITORY_FILES = {
    "knowledge/preferences.yaml",          # runtime file; the example is tracked
    "/api/app-host/_sdk/cockpit-app.js",   # served URL
    "check/harness.mjs", "tests/ui.json", "tests/functions.json",  # inside an app package
    "auth.json", "diagnosis.json",         # written into run and profile folders
    "lib/app-storage.ts", "lib/api.ts",    # named as removed in 13-app-platform
    "task_result_types.py", "result-renderers.tsx",
}


def _exists(doc: str, ref: str) -> bool:
    base = (Path(doc).parent / ref).as_posix()
    candidates = [base] + [root + ref for root in SEARCH_ROOTS]
    if any(Path(REPO / Path(c)).exists() for c in candidates):
        return True
    return "/" not in ref and ref in BARE_NAMES


def test_markdown_links_resolve() -> None:
    broken = []
    for doc in LINKED:
        for target in LINK.findall((REPO / doc).read_text(encoding="utf-8")):
            if re.match(r"[a-z][a-z0-9+.-]*:", target) or target.startswith("/"):
                continue
            if not (REPO / doc).parent.joinpath(target).resolve().exists():
                broken.append(f"{doc}: {target}")
    assert not broken, "broken relative links:\n" + "\n".join(broken)


def test_backticked_file_paths_exist() -> None:
    missing = []
    for doc in MAINTAINED:
        for ref in sorted(set(CODE_PATH.findall((REPO / doc).read_text(encoding="utf-8")))):
            if any(ch in ref for ch in "*<{") or ref in NOT_REPOSITORY_FILES:
                continue
            if not _exists(doc, ref):
                missing.append(f"{doc}: {ref}")
    assert not missing, "paths in maintained docs that do not exist:\n" + "\n".join(missing)
