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
    env = h.service_environment(tmp_path, 1234, overrides={"OPENAI_LIVE_VOICE": "cedar"})
    assert env["AUTH_PASSWORD"] == "" and env["SESSION_COOKIE_SECURE"] == "false"
    assert env["DATABASE_URL"].endswith((tmp_path / "application.db").as_posix())
    assert env["TASK_POOL_ENABLED"] == "false" and env["OPENAI_LIVE_VOICE"] == "cedar"


def test_drain_observer_waits_for_settled_jobs():
    clock = [0.0]
    states = iter([{"jobs": [{"id": "j", "status": "running"}], "presentations": []}] * 3
        + [{"jobs": [{"id": "j", "status": "completed", "completed_at": "t"}], "presentations": []}] * 200)
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
    import re
    pattern = re.compile(r"^\s*(from|import)\s+app\.selftest", re.MULTILINE)
    offenders = [p for p in app.rglob("*.py") if "selftest" not in p.parts
        and pattern.search(p.read_text(encoding="utf-8", errors="ignore"))]
    assert offenders == []


def test_cli_refuses_paid_runs_without_live():
    completed = subprocess.run([sys.executable, "-m", "app.selftest", "run", "--name", "refused"],
        cwd=h.BACKEND, capture_output=True, text=True)
    assert completed.returncode != 0 and "--live" in (completed.stderr + completed.stdout)


def test_review_pack_reads_both_recordings_and_state(tmp_path):
    import math, struct, wave
    from app.selftest.review import evidence_pack, pack_markdown, voiced
    def tone(path, spans, seconds=6, rate=24000):
        samples = [int(8000 * math.sin(i / 5)) if any(a <= i / rate < b for a, b in spans) else 0 for i in range(seconds * rate)]
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(struct.pack("<%dh" % len(samples), *samples))
    tone(tmp_path / "caller.wav", [(1, 2)]); tone(tmp_path / "assistant.wav", [(3, 4.5)])
    assert voiced(tmp_path / "assistant.wav")[0][0] == 3.0
    (tmp_path / "evidence.json").write_text(json.dumps({"assistant_audio_epoch_ms": 1000, "markers": [
        {"type": "caller.start", "epoch_ms": 2000, "text": "Tell me when it is done."}, {"type": "completion", "epoch_ms": 3500}],
        "state": {"provider_events": [{"event": {"type": "session.output_transcript.delta", "delta": "It is ready."}}]}}), encoding="utf-8")
    (tmp_path / "assistant-transcription.json").write_text(json.dumps({"words": [
        {"word": "It", "start": 3.0, "end": 3.2}, {"word": "is", "start": 3.2, "end": 3.4}, {"word": "ready", "start": 3.4, "end": 3.8}]}), encoding="utf-8")
    pack = evidence_pack(tmp_path, {"id": "demo", "expect": "Jarvis tells the user."})
    text = pack_markdown(pack)
    assert 'JARVIS (played, transcribed) 3.0-3.8s: "It is ready"' in text
    assert "user voice 1.0" in text and "EVENT completion" in text and "Jarvis tells the user." in text


@pytest.mark.asyncio
async def test_doctor_reports_undelivered_requests_and_failed_bookkeeping(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.db.session import session_scope
    from app.db.tables import ConversationObservation, LiveSession, TaskPresentation
    from app.services.diagnostics import diagnose
    from app.services.jarvis_coordinator import jarvis_coordinator
    from app.services.task_presentations import task_presentation_service
    from app.services.task_service import task_service
    task = (await task_service.dispatch(goal="Doctor page", origin_surface="test", idempotency_key="doctor-task",
        notification_intent="requested", notification_reason="Tell me")).task
    await task_service.complete(task.id, summary="Ready.", idempotency_key="doctor-done")
    await task_presentation_service.sync_candidates()
    await jarvis_coordinator.accept_text_turn("Begin", idempotency_key="doctor-start")
    live = await jarvis_coordinator.get_active_session()
    async with session_scope() as db:
        db.add(ConversationObservation(session_id=live.id, generation=live.generation, evidence_hash="x", input_json={},
            status="failed", error="ValueError: Observer must classify each exact presentation once"))
    class StoppedObserver:
        failure = "HTTPStatusError"
    async with session_scope() as db:
        findings = await diagnose(db, now=datetime.now(timezone.utc) + timedelta(minutes=20), observer=StoppedObserver())
    symptoms = " | ".join(f["symptom"] for f in findings)
    assert "Requested update for \"Doctor page\" is still undelivered" in symptoms
    assert "bookkeeper stopped" in symptoms and "assessment(s) failed" in symptoms
    assert all(f["proposed_fix"] for f in findings)
