"""List backend functions longer than a threshold (default 150 lines).

From repository root: python -I docs/audits/repo-maintainability/evidence/long-functions-scan.py [threshold]
Length alone is not a finding; this locates where branching concentrates so
reviewers can read those sites. Writes JSON to stdout.
"""
from pathlib import Path
import ast
import json
import sys

ROOT = Path(__file__).resolve().parents[4]
threshold = int(sys.argv[1]) if len(sys.argv) > 1 else 150
rows = []
for path in sorted((ROOT / "backend" / "app").rglob("*.py")):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            length = node.end_lineno - node.lineno + 1
            if length >= threshold:
                branches = sum(isinstance(child, (ast.If, ast.Match, ast.Try, ast.For, ast.While)) for child in ast.walk(node))
                rows.append({"path": path.relative_to(ROOT).as_posix(), "function": node.name,
                             "line": node.lineno, "lines": length, "branch_nodes": branches})
rows.sort(key=lambda row: -row["lines"])
print(json.dumps({"threshold": threshold, "count": len(rows), "functions": rows}, indent=1))
