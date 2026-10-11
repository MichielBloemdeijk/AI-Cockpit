"""Host fencing and listener verification without providers or real services."""
import json
import pytest
from app.jarvis_tasks import personal_host as host


def test_stale_pid_is_stopped_without_touching_other_processes(tmp_path, monkeypatch):
    state = tmp_path / "personal-host.json"
    state.write_text(json.dumps({"pid": 123, "creation_stamp": 1234, "status": "Ready"}))
    monkeypatch.setattr(host, "STATE", state)
    monkeypatch.setattr(host, "process_has_exited", lambda pid, stamp: True)
    monkeypatch.setattr(host, "health", lambda: pytest.fail("must not probe a reused PID"))
    assert host.status() == {"status": "Stopped"}


def test_tailscale_funnel_or_other_proxy_fails_closed(monkeypatch):
    monkeypatch.setattr(host.settings, "jarvis_public_url", "https://fixture.tail.ts.net")
    monkeypatch.setattr(host.shutil, "which", lambda value: value)
    own = {"Self": {"DNSName": "fixture.tail.ts.net.", "Online": True}, "BackendState": "Running"}
    serve = {"TCP": {"443": {"HTTPS": True}}, "Web": {"fixture.tail.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:3000"}}}}}
    monkeypatch.setattr(host.subprocess, "check_output", lambda args, **kwargs: json.dumps(serve if args[1] == "serve" else own))
    assert host.transport_check()["funnel"] is False
    serve["AllowFunnel"] = {"fixture.tail.ts.net:443": True}
    with pytest.raises(RuntimeError): host.transport_check()
    serve.pop("AllowFunnel")
    serve["Web"]["fixture.tail.ts.net:443"]["Handlers"]["/"]["Proxy"] = "http://0.0.0.0:3000"
    with pytest.raises(RuntimeError): host.transport_check()


def test_ready_requires_worker_frontend_and_transport(monkeypatch, tmp_path):
    state = tmp_path / "host.json"
    state.write_text(json.dumps({"pid": 123, "creation_stamp": 1234, "status": "Ready"}))
    monkeypatch.setattr(host, "STATE", state)
    monkeypatch.setattr(host, "process_has_exited", lambda pid, stamp: False)
    def fail(): raise RuntimeError("not ready")
    monkeypatch.setattr(host, "health", fail)
    assert host.status()["status"] == "Needs attention"
