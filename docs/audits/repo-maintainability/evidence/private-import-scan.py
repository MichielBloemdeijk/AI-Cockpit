"""Count cross-module imports of underscore-prefixed names in backend/app.

From repository root: python docs/audits/repo-maintainability/evidence/private-import-scan.py
Static AST scan only; includes function-local imports. Writes JSON to stdout.
"""
from collections import Counter, defaultdict
from pathlib import Path
import ast
import json

ROOT = Path(__file__).resolve().parents[4]
APP = ROOT / "backend" / "app"

edges = defaultdict(set)
for path in sorted(APP.rglob("*.py")):
    importer = path.relative_to(ROOT / "backend").with_suffix("").as_posix().replace("/", ".")
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
            for alias in node.names:
                if alias.name.startswith("_") and not alias.name.startswith("__"):
                    edges[(importer, node.module)].add(alias.name)

by_source = Counter()
for (_, source), names in edges.items():
    by_source[source] += len(names)

print(json.dumps({
    "importing_modules": len({importer for importer, _ in edges}),
    "private_names_imported": sum(len(n) for n in edges.values()),
    "top_sources": by_source.most_common(15),
    "edges": [{"importer": i, "source": s, "names": sorted(n)} for (i, s), n in sorted(edges.items())],
}, indent=1))
