"""Isolated current-source memory lifecycle probe; no application DB or provider.

Run from repository root: backend/.venv/Scripts/python.exe docs/audits/repo-maintainability/evidence/memory-probe.py
Uses real file publication/search and PATCH body with fake persistence only.
"""
import ast
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]

def source_namespace(relative, names=None, **values):
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    if names:
        tree.body = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
        for n in tree.body:
            n.decorator_list = []
    else:
        tree.body = [n for n in tree.body if not isinstance(n, ast.ImportFrom) or not (n.module or "").startswith("app.")]
    tree.body = [n for n in tree.body if not isinstance(n, ast.ImportFrom) or n.module != "__future__"]
    tree.body.insert(0, ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0))
    ast.fix_missing_locations(tree)
    exec(compile(tree, relative, "exec"), values)
    return values

async def main():
    with TemporaryDirectory(prefix="cockpit-memory-audit-") as folder:
        root = Path(folder)
        row = SimpleNamespace(id="audit-memory", title="Audit note", content="Approved content", kind="note", scope="note", knowledge_path=None, source_conversation_id="audit", source_event_id=None, status="approved")
        knowledge = source_namespace("backend/app/services/knowledge_memory.py", settings=SimpleNamespace(knowledge_notes_path=root / "notes", knowledge_preferences_path=root / "preferences.yaml"))
        row.knowledge_path = str(knowledge["write_approved_memory"](row))
        class Session:
            async def get(self, *args): return row
            async def flush(self): pass
        @asynccontextmanager
        async def session_scope(): yield Session()
        async def get_memory_item(_): return row
        patch = source_namespace("backend/app/api/memory.py", {"patch_memory_proposal"}, session_scope=session_scope, MemoryItem=object, memory_review_store=SimpleNamespace(get_memory_item=get_memory_item), present_memory_item=lambda value: value)
        await patch["patch_memory_proposal"](SimpleNamespace(id=row.id, status="deleted", title=None, content=None))
        remains = Path(row.knowledge_path).is_file()
        visible = any(d["path"] == Path(row.knowledge_path).as_posix() for d in knowledge["list_knowledge_documents"]())
        print(f"PATCH status={row.status}; approved file remains={remains}; knowledge listing includes it={visible}")
        assert row.status == "deleted" and remains and visible

asyncio.run(main())
