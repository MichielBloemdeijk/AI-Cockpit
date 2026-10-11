from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time
import json
from datetime import datetime, timezone

import pytest
from app.services.windows_job import process_creation_stamp, process_has_exited
from app.config import settings


def process_tree_configuration():
    from app.jarvis_tasks.acceptance import resolve_test_configuration
    return resolve_test_configuration("process_tree", paths={key: getattr(settings, key) for key in (
        "task_workspaces_dir", "task_context_blobs_dir", "conversation_workspaces_dir")})


@pytest.mark.skipif(os.name != "nt", reason="Windows process identity")
def test_process_creation_stamp_distinguishes_live_writer() -> None:
    stamp = process_creation_stamp(os.getpid())
    assert isinstance(stamp, int) and stamp > 0
    assert process_has_exited(os.getpid(), stamp) is False


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object containment")
def test_worker_job_closes_descendant_writer_after_parent_crash(tmp_path: Path) -> None:
    marker = tmp_path / "writer.txt"
    writer_code = "\n".join((
        "import pathlib, time",
        f"path = pathlib.Path({str(marker)!r})",
        "while True:",
        "    with path.open('a', encoding='utf-8') as output:",
        "        output.write('x')",
        "    time.sleep(0.03)",
    ))
    parent_code = "\n".join((
        "import os, subprocess, sys, time, pathlib",
        "from app.services.windows_job import contain_current_process_tree",
        "contain_current_process_tree()",
        f"child = subprocess.Popen([sys.executable, '-c', {writer_code!r}], "
        "creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))",
        f"path = pathlib.Path({str(marker)!r})",
        "deadline = time.monotonic() + 5",
        "while not path.exists() and time.monotonic() < deadline:",
        "    time.sleep(0.02)",
        "print(child.pid, flush=True)",
        "os._exit(9)",
    ))
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    writer_pid: int | None = None
    try:
        stdout, stderr = parent.communicate(timeout=10)
        assert parent.returncode == 9, stderr
        writer_pid = int(stdout.strip())
        assert marker.exists()
        before = marker.stat().st_size
        time.sleep(0.3)
        assert marker.stat().st_size == before
        if destination := os.getenv("JARVIS_PROCESS_TREE_REPORT"):
            from app.jarvis_tasks.acceptance import SCHEMA, candidate_identity
            Path(destination).write_text(json.dumps({"schema": SCHEMA,
                "gate": os.getenv("JARVIS_ACCEPTANCE_GATE", "M10"), "candidate": candidate_identity(),
                "test_configuration": process_tree_configuration(),
                "finished_at": datetime.now(timezone.utc).isoformat(), "checks": [{"id": "process_tree",
                    "status": "passed", "evidence": {"exact_pid_creation_stamp": False,
                        "descendants_stopped": True, "writer_growth_after_stop": 0}}]}), encoding="utf-8")
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if writer_pid is not None:
            subprocess.run(
                ["taskkill", "/PID", str(writer_pid), "/T", "/F"],
                capture_output=True,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )


@pytest.mark.skipif(os.name != "nt", reason="Windows supervisor death watch")
def test_child_exits_when_its_exact_supervisor_process_dies(tmp_path: Path) -> None:
    marker = tmp_path / "orphan-writer.txt"
    child_code = "\n".join((
        "import os, pathlib, sys, time",
        "from app.services.windows_job import contain_current_process_tree, exit_when_parent_exits",
        "contain_current_process_tree()",
        "exit_when_parent_exits(int(sys.argv[1]))",
        f"path = pathlib.Path({str(marker)!r})",
        "while True:",
        "    with path.open('a', encoding='utf-8') as output:",
        "        output.write('x')",
        "    time.sleep(0.03)",
    ))
    parent_code = "\n".join((
        "import os, pathlib, subprocess, sys, time",
        "from app.services.windows_job import process_creation_stamp",
        f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}, str(os.getpid())], "
        "creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))",
        f"path = pathlib.Path({str(marker)!r})",
        "deadline = time.monotonic() + 5",
        "while not path.exists() and time.monotonic() < deadline:",
        "    time.sleep(0.02)",
        "print(child.pid, process_creation_stamp(child.pid), flush=True)",
        "os._exit(9)",
    ))
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    child_pid: int | None = None
    try:
        stdout, stderr = parent.communicate(timeout=10)
        assert parent.returncode == 9, stderr
        pid_text, stamp_text = stdout.strip().split()
        child_pid = int(pid_text)
        stamp = int(stamp_text)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and process_has_exited(child_pid, stamp) is not True:
            time.sleep(0.03)
        assert process_has_exited(child_pid, stamp) is True
        before = marker.stat().st_size
        time.sleep(0.2)
        assert marker.stat().st_size == before
        if destination := os.getenv("JARVIS_PROCESS_TREE_REPORT"):
            from app.jarvis_tasks.acceptance import SCHEMA, candidate_identity
            Path(destination).write_text(json.dumps({"schema": SCHEMA,
                "gate": os.getenv("JARVIS_ACCEPTANCE_GATE", "M10"), "candidate": candidate_identity(),
                "test_configuration": process_tree_configuration(),
                "finished_at": datetime.now(timezone.utc).isoformat(), "checks": [{"id": "process_tree",
                    "status": "passed", "evidence": {"exact_pid_creation_stamp": True,
                        "pid": child_pid, "creation_stamp": stamp, "descendants_stopped": True,
                        "writer_growth_after_stop": marker.stat().st_size - before}}]}), encoding="utf-8")
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if child_pid is not None:
            subprocess.run(
                ["taskkill", "/PID", str(child_pid), "/T", "/F"],
                capture_output=True,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
