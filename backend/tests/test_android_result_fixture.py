"""Deterministic checks for the emulator harness; no SDK or provider needed."""
import importlib.util
from pathlib import Path

from httpx import AsyncClient, ASGITransport
import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "android/scripts"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_emulator_fixture_controls_require_auth_and_use_real_publication(monkeypatch):
    fixture = load("m5_fixture_test", "result_platform_fixture.py")
    monkeypatch.setenv("RESULT_FIXTURE_EXTERNAL_URL", "http://10.0.2.2:12345/source")
    assert not any(getattr(route, "path", "").startswith("/_fixture") for route in fixture.cockpit.routes)
    async with AsyncClient(transport=ASGITransport(app=fixture.app), base_url="http://testserver") as client:
        assert (await client.post("/_fixture/publish/unknown")).status_code == 401
        assert (await client.post("/api/auth/login", json={"password": ""})).status_code == 200
        created = await client.post("/api/tasks", json={"goal": "Emulator fixture", "idempotency_key": "m5-android-test", "result": {"mode": "new"}})
        assert created.status_code == 201
        task_id = created.json()["id"]
        published = (await client.post(f"/_fixture/publish/{task_id}")).json()
        assert published["revision"] == 1 and published["url"].startswith("/apps/documents?collection=document&key=")
        key = published["url"].rsplit("=", 1)[1]
        record = (await client.get(f"/api/app-platform/data/documents/document/{key}")).json()
        assert "RESULT-NATIVE-DOCUMENT-READY" in record["value"]["body"]
        assert "http://10.0.2.2:12345/source" in record["value"]["body"]
        task = (await client.get(f"/api/tasks/{task_id}")).json()
        assert task["status"] == "completed" and len(task["attempts"]) == 1


def test_native_foreground_proof_requires_browser_and_url(monkeypatch):
    harness = load("m5_harness_test", "verify-result-platform.py")
    url = "http://10.0.2.2:12345/source"
    chrome = "topResumedActivity=ActivityRecord{abc u0 com.android.chrome/.Main t12}"
    assert harness.resumed_activity(chrome) == chrome
    for state, expected in [("ResumedActivity: null", False),
        (chrome + "\nIntent dat=" + url, True),
        (chrome, False),
        ("topResumedActivity=ActivityRecord{abc u0 launcher/.Main t1}\nIntent dat=" + url, False),
        ("mResumedActivity=ActivityRecord{abc u0 nl.sander.aicockpit.jarvis/.web.JarvisWebViewActivity t1}\nIntent dat=" + url, False)]:
        monkeypatch.setattr(harness, "foreground", lambda _serial, state=state: state)
        assert bool(harness.external_activity("unused", url, "com.android.chrome")) is expected


def test_native_hierarchy_output_is_fresh_complete_and_not_a_stale_file():
    harness = load("m5_hierarchy_test", "verify-result-platform.py")
    xml = '<hierarchy><node text="Open result" /></hierarchy>'
    assert harness.parse_hierarchy("INSTRUMENTATION_RESULT: hierarchy=" + xml + "\nINSTRUMENTATION_CODE: -1") == xml
    for incomplete in ["ERROR: could not get idle state", "INSTRUMENTATION_RESULT: error=NoWindow",
        "INSTRUMENTATION_RESULT: hierarchy=<hierarchy>"]:
        with pytest.raises(RuntimeError):
            harness.parse_hierarchy(incomplete)
