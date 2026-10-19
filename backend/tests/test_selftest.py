import json
import subprocess
import sys

import pytest

from app.selftest import harness as h
from app.selftest.scenarios import load_suite, speech_texts, validate


def test_core_suite_is_valid_and_speech_is_derivable():
    scenarios = load_suite("core")
    assert len(scenarios) >= 10
    assert all(s["expect"].strip() for s in scenarios)
    texts = speech_texts(scenarios)
    assert "Yes, tell me when the task finishes." in texts
    assert all(t.strip() for t in texts)


@pytest.mark.parametrize("broken", [
    {"id": "x", "expect": "e", "expectation": "required", "steps": [{"type": "nonsense"}]},
    {"id": "x", "expect": "e", "expectation": "required", "steps": [{"type": "speak"}]},
    {"id": "x", "expect": "e", "expectation": "maybe", "steps": [{"type": "wait", "seconds": 1}]},
    {"id": "x", "expectation": "required", "steps": [{"type": "wait", "seconds": 1}]},
    {"id": "x", "expect": "e", "adaptive": {"agenda": " "}},
])
def test_invalid_scenarios_are_rejected(broken):
    with pytest.raises(ValueError):
        validate(broken)


def test_utterance_id_matches_driver_convention():
    # frontend/e2e/announcement-eval.mjs and verify-announcements.py: sha256(text)[:16].
    assert h.utterance_id("Hey Jarvis") == __import__("hashlib").sha256(b"Hey Jarvis").hexdigest()[:16]


def test_run_root_is_confined(tmp_path):
    with pytest.raises(ValueError):
        h.run_root("../outside")
    with pytest.raises(ValueError):
        h.run_root("")


def test_service_environment_isolates_personal_settings(tmp_path):
    env = h.service_environment(tmp_path, 1234, overrides={"JARVIS_CONVERSATION_OBSERVER_MODE": "enabled"})
    assert env["AUTH_PASSWORD"] == "" and env["SESSION_COOKIE_SECURE"] == "false"
    assert env["DATABASE_URL"].endswith((tmp_path / "application.db").as_posix())
    assert env["TASK_POOL_ENABLED"] == "false" and env["JARVIS_CONVERSATION_OBSERVER_MODE"] == "enabled"


def test_drain_observer_waits_for_settled_jobs():
    clock = [0.0]
    states = iter([{"jobs": [{"id": "j", "status": "running"}], "presentations": []}] * 3
        + [{"jobs": [{"id": "j", "status": "completed", "completed_at": "t"}], "presentations": []}] * 50)
    result = h.drain_observer(None, timeout=30, clock=lambda: clock[0],
        sleep=lambda s: clock.__setitem__(0, clock[0] + max(s, .2)), inspect=lambda: next(states))
    assert not result["timed_out"] and result["jobs"][0]["status"] == "completed"


def test_provider_failure_detection():
    assert h.provider_failure("Error: insufficient_quota for this key")
    assert h.provider_failure("upstream HTTP 401")
    assert not h.provider_failure("session closed normally")


def test_connections_are_counted_from_recorded_events(tmp_path):
    rows = [{"event": {"type": "provider.connect.request"}}, {"event": {"type": "session.started"}},
        {"event": {"type": "provider.connect.request"}}]
    (tmp_path / "events.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    assert h.connections_used(tmp_path) == 2


def test_production_never_imports_selftest():
    from pathlib import Path
    app = Path(h.BACKEND / "app")
    offenders = [p for p in app.rglob("*.py") if "selftest" not in p.parts
        and "app.selftest" in p.read_text(encoding="utf-8", errors="ignore")]
    assert offenders == []


def test_cli_refuses_paid_runs_without_live():
    completed = subprocess.run([sys.executable, "-m", "app.selftest", "run", "--name", "refused"],
        cwd=h.BACKEND, capture_output=True, text=True)
    assert completed.returncode != 0 and "--live" in (completed.stderr + completed.stdout)
