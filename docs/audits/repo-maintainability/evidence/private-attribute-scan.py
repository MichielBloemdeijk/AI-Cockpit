"""Count `<module_singleton>._private` attribute uses across backend/app modules.

From repository root: python -I docs/audits/repo-maintainability/evidence/private-attribute-scan.py
Static AST scan. A use counts when the base name is a module-level singleton
imported from another app module (e.g. `task_service._lock_task`). `self._x`
and module-internal uses are ignored. Writes JSON to stdout.
"""
from collections import Counter, defaultdict
from pathlib import Path
import ast
import json

ROOT = Path(__file__).resolve().parents[4]
APP = ROOT / "backend" / "app"

uses = defaultdict(Counter)
for path in sorted(APP.rglob("*.py")):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
            for alias in node.names:
                if not alias.name.startswith("_") and alias.name[:1].islower():
                    imported[alias.asname or alias.name] = node.module
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr.startswith("_") and not node.attr.startswith("__")
                and isinstance(node.value, ast.Name) and node.value.id in imported):
            target = f"{imported[node.value.id]}.{node.value.id}"
            uses[target][(path.relative_to(ROOT / "backend").as_posix(), node.attr)] += 1

print(json.dumps({
    "targets": {
        target: {
            "distinct_private_members": len({attr for _, attr in counter}),
            "importing_modules": len({module for module, _ in counter}),
            "uses": sum(counter.values()),
            "members": sorted({attr for _, attr in counter}),
            "modules": sorted({module for module, _ in counter}),
        }
        for target, counter in sorted(uses.items(), key=lambda item: -sum(item[1].values()))
    }
}, indent=1))
