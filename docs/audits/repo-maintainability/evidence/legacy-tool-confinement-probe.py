"""Check whether the legacy agent's python/shell tools stay inside write roots.

From repository root: backend/.venv/Scripts/python.exe docs/audits/repo-maintainability/evidence/legacy-tool-confinement-probe.py
Uses only a fresh temporary directory: the workspace is <tmp>/workspace and the
"outside" target is <tmp>/outside. No personal config, DB or network.
"""
from pathlib import Path
import asyncio
import json
import os
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[4]
os.environ["AI_COCKPIT_ENV_FILE"] = str(ROOT / "docs/audits/repo-maintainability/evidence/no-personal-env")
sys.path.insert(0, str(ROOT / "backend"))

from app.services.agent_tools.base import ToolExecutionContext  # noqa: E402
from app.services.agent_tools.execution import run_python_execution, run_shell_command  # noqa: E402


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="cockpit-confinement-") as folder:
        base = Path(folder).resolve()
        workspace = base / "workspace"
        outside = base / "outside"
        workspace.mkdir()
        outside.mkdir()
        context = ToolExecutionContext(conversation_id="probe", run_id=None, workspace_path=str(workspace),
                                       workspace_root=workspace, read_roots=[workspace], write_roots=[workspace])
        results = {}

        guarded = outside / "guarded.txt"
        try:
            await run_python_execution(f"open({str(guarded)!r}, 'w').write('x')", context=context)
        except ValueError:
            pass
        results["python_builtin_open_blocked"] = not guarded.exists()

        via_io = outside / "via_io.txt"
        try:
            await run_python_execution(f"import _io; _io.open({str(via_io)!r}, 'w').write('x')", context=context)
        except ValueError as exc:
            results["python_io_error"] = str(exc)[:200]
        results["python_io_open_escaped"] = via_io.exists()

        via_shell = outside / "via_shell.txt"
        # shlex runs with posix=False, so the argument is written without quotes or spaces.
        command = f"{sys.executable} -c open(r'{via_shell.as_posix()}','w').write('x')"
        try:
            await run_shell_command(command, context=context)
        except ValueError as exc:
            results["shell_error"] = str(exc)[:200]
        results["shell_python_escaped"] = via_shell.exists()
        print(json.dumps(results, indent=1))


asyncio.run(main())
