"""The Android harnesses' backends never read the personal .env (audit finding A03)."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "android" / "scripts"


@pytest.mark.skipif(os.name != "nt", reason="the harnesses contain children in a Windows job")
def test_started_backend_ignores_a_poisoned_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    poisoned = tmp_path / "poisoned.env"
    poisoned.write_text("TASK_PROJECT_ROOTS=C:/poisoned\nOPENROUTER_API_KEY=poisoned-key\n", encoding="utf-8")
    monkeypatch.setenv("AI_COCKPIT_ENV_FILE", str(poisoned))
    spec = importlib.util.spec_from_file_location("harness_support", SCRIPTS / "harness_support.py")
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)

    output = tmp_path / "settings.txt"
    probe = ("from app.config import settings; import sys; "
             "open(sys.argv[1], 'w').write(settings.task_project_roots + '|' + settings.openrouter_api_key)")
    process = harness.start_owned_process([sys.executable, "-c", probe, str(output)],
                                          env={"OPENROUTER_API_KEY": "scenario-key"})
    assert process.wait(timeout=60) == 0
    assert os.environ["AI_COCKPIT_ENV_FILE"] == str(harness.HARNESS_ENV_FILE)
    assert output.read_text() == "|scenario-key"
