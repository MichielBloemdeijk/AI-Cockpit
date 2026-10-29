"""Which backend HTTP routes have no first-party caller?

From repository root: backend/.venv/Scripts/python.exe -I docs/audits/repo-maintainability/evidence/route-usage-scan.py
Imports the FastAPI app (no lifespan, no network) to list routes, then searches
first-party clients (frontend, Android main source, backend services/selftest,
scripts) for each route's static prefix. Path parameters become wildcards.
A route with no match is a removal candidate, not proof of dead code: tests
are reported separately and dynamic URL building can hide callers.
"""
from pathlib import Path
import json
import os
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
os.environ["AI_COCKPIT_ENV_FILE"] = str(ROOT / "docs/audits/repo-maintainability/evidence/no-personal-env")
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.routing import APIRoute  # noqa: E402
from app.main import app  # noqa: E402

CLIENTS = ["frontend/lib", "frontend/app", "frontend/components", "frontend/result-export", "frontend/scripts",
           "frontend/e2e", "frontend/public", "android/app/src/main", "android/app/src/debug", "android/scripts",
           "backend/app/selftest", "backend/app/jarvis_tasks", "backend/app/services", "scripts"]
TESTS = ["backend/tests", "frontend/lib", "frontend/components"]


def files(paths, test_only=False):
    listed = subprocess.run(["git", "ls-files", *paths], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    for name in listed:
        is_test = ".test." in name or name.startswith("backend/tests/")
        if is_test == test_only and not name.endswith((".png", ".onnx", ".wasm", ".jar")):
            yield ROOT / name


def corpus(paths, test_only=False):
    return "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in files(paths, test_only))


client_text = corpus(CLIENTS)
test_text = corpus(TESTS, test_only=True)
rows = []
for route in app.routes:
    if not isinstance(route, APIRoute) or not route.path.startswith("/api/") or not route.include_in_schema:
        continue
    # Search on the literal segments up to the first parameter, then require the rest as a loose pattern.
    parts = [re.escape(part) if not part.startswith("{") else r"[^\s\"'`/?]+" for part in route.path.strip("/").split("/")]
    loose = r"/".join(parts).replace(r"[^\s\"'`/?]+", r"(?:\$\{[^}]+\}|\{[^}]+\}|[^\s\"'`/?]+|\"\s*\+[^+]+\+\s*\")")
    pattern = re.compile(loose + r"(?![A-Za-z0-9_-])")
    rows.append({"methods": sorted(route.methods - {"HEAD", "OPTIONS"}), "path": route.path,
                 "client": bool(pattern.search(client_text)), "test": bool(pattern.search(test_text))})

unused = [row for row in rows if not row["client"]]
print(json.dumps({"routes": len(rows), "without_client": len(unused),
                  "without_client_or_test": sum(not row["test"] for row in unused), "unused": unused}, indent=1))
