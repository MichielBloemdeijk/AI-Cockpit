"""Real Python/Node scratch, subprocess, nonzero exits and cleanup in both modes."""
import os
import shutil
import sys

import pytest

from app.config import settings
from app.services.task_project_tools import execute_sandboxed, execute_trusted


@pytest.mark.skipif(os.name != "nt", reason="Windows command boundary")
@pytest.mark.parametrize("mode", [settings.task_command_mode])
def test_representative_command_workloads(tmp_path, mode):
    root = tmp_path / "project"
    root.mkdir()
    execute = execute_sandboxed if mode == "native" else execute_trusted
    python = """import tempfile,subprocess,sys
from pathlib import Path
with tempfile.TemporaryDirectory() as folder:
    Path(folder,'probe').write_text('ok')
    child=subprocess.run([sys.executable,'-c','print(123)'],capture_output=True)
    assert child.returncode == 0 and b'123' in child.stdout
print('python-ok')
"""
    node = """const fs=require('fs'),os=require('os'),path=require('path'),cp=require('child_process');
const folder=fs.mkdtempSync(path.join(os.tmpdir(),'jarvis-node-'));
fs.writeFileSync(path.join(folder,'probe'),'ok');
const child=cp.spawnSync(process.execPath,['-e','console.log(123)']);
if(child.status!==0 || !child.stdout.toString().includes('123'))process.exit(9);
fs.rmSync(folder,{recursive:true});console.log('node-ok');"""
    for argv, expected in ([sys.executable, "-c", python], "python-ok"), ([shutil.which("node"), "--eval", node], "node-ok"):
        result = execute(argv, root, root, tmp_path / "private-profile")
        assert result["exit_code"] == 0, result
        assert expected in result["stdout"]
    failure = execute([sys.executable, "-c", "import sys;print('failed test');sys.exit(3)"], root, root, tmp_path / "private-profile")
    assert failure["exit_code"] == 3 and "failed test" in failure["stdout"]
    assert not list(root.glob("jarvis-command-output-*"))


@pytest.mark.skipif(os.name != "nt", reason="Windows command boundary")
def test_native_never_retries_as_trusted(tmp_path, monkeypatch):
    from app.services.task_project_tools import SandboxBoundaryUnavailable
    import app.jarvis_tasks.codex_probe as probe
    from types import SimpleNamespace
    monkeypatch.setattr(probe, "_sandbox_checks", lambda *a, **kw: [SimpleNamespace(id="denied", status="fail")])
    marker = tmp_path / "marker"
    with pytest.raises(SandboxBoundaryUnavailable):
        execute_sandboxed([sys.executable, "-c", f"from pathlib import Path;Path({str(marker)!r}).write_text('wrong')"],
            tmp_path, tmp_path, tmp_path / "private-profile")
    assert not marker.exists()
