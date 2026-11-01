"""App functions, triggers, file stores, network grants and automatic repairs (build step 4)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil

import httpx
from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio
from sqlalchemy import select

from app import app_platform
from app.app_platform import cron, network, runtime
from app.app_platform.check import check_package
from app.app_platform.function_host import FetchMock, MemoryServices, execute
from app.app_platform.store import AppEvent, AppSchedule, apps_session
from app.config import settings
from app.main import app
from tests.test_builtin_apps import needs_browser

CLIPPINGS = Path(app_platform.__file__).parent / "samples" / "clippings"


@pytest_asyncio.fixture(autouse=True)
async def without_builtin_apps():
    for slug in ("documents", "recipes"):
        await app_platform.uninstall(slug)


def clippings(edit=None, *, name: str = "clippings") -> Path:
    target = settings.app_builds_path / name
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(CLIPPINGS, target)
    if edit is not None:
        path = target / "cockpit-app.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        edit(manifest, target)
        path.write_text(json.dumps(manifest), encoding="utf-8")
    return target


@pytest.fixture
def web(monkeypatch):
    """The internet as seen by cockpit.fetch: public addresses and canned pages."""
    pages: dict[str, httpx.Response] = {}
    requested: list[str] = []

    async def resolve(host, port):
        return ["10.0.0.5"] if host.startswith("intranet.") else ["93.184.216.34"]

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return pages.get(str(request.url), httpx.Response(404, text="missing"))

    monkeypatch.setattr(network, "_resolve", resolve)
    monkeypatch.setattr(network, "_client", lambda timeout: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False, timeout=timeout))
    return pages, requested


def test_cron_schedules_follow_the_owners_time_zone() -> None:
    monday_8 = cron.parse("0 8 * * MON")
    # Saturday 31 Oct 2026, 12:00 UTC; Amsterdam is UTC+1 after the DST change on 25 Oct.
    after = monday_8.next_after(datetime(2026, 10, 31, 12, tzinfo=timezone.utc))
    assert after == datetime(2026, 11, 2, 7, tzinfo=timezone.utc)
    every_15 = cron.parse("*/15 9-17 * * 1-5")
    assert every_15.next_after(datetime(2026, 11, 2, 8, 50, tzinfo=timezone.utc)) == datetime(
        2026, 11, 2, 9, 0, tzinfo=timezone.utc)
    # Both day fields restricted: either matches, as in standard cron.
    either = cron.parse("0 0 1 * FRI")
    assert either.next_after(datetime(2026, 11, 2, tzinfo=timezone.utc)).astimezone(
        timezone(timedelta(hours=1))).day == 6
    assert cron.parse("@daily").next_after(datetime(2026, 11, 2, 12, tzinfo=timezone.utc)).hour == 23
    for bad in ("0 8 * *", "61 * * * *", "0 0 30 2 *", "*/0 * * * *", "0 8 * * FUNDAY"):
        with pytest.raises(cron.CronError):
            cron.parse(bad)


@pytest.mark.parametrize(("change", "problem"), [
    (lambda m, root: m["functions"]["index_clip"].update(file="functions/missing.py"), "existing .py file"),
    (lambda m, root: m["functions"]["index_clip"].update(file="ui/index.html"), "existing .py file"),
    (lambda m, root: (root / "functions" / "index_clip.py").write_text("def handle(:\n"), "does not parse"),
    (lambda m, root: (root / "functions" / "index_clip.py").write_text("async def handle(input, cockpit):\n    pass\n"),
     "not async"),
    (lambda m, root: (root / "functions" / "index_clip.py").write_text("def handle(input):\n    pass\n"),
     "exactly two arguments"),
    (lambda m, root: (root / "functions" / "index_clip.py").write_text("def run(input, cockpit):\n    pass\n"),
     "must define handle"),
    (lambda m, root: m["functions"]["daily_summary"]["trigger"].update(schedule="0 25 * * *"), "Invalid schedule"),
    (lambda m, root: m["functions"]["index_clip"].update(trigger={"event": "record_changed", "collection": "nope"}),
     "record_changed needs"),
    (lambda m, root: m.pop("files"), "file_changed needs"),
    (lambda m, root: m["grants"].update(network=["https://example.org"]), "Network grants"),
    (lambda m, root: m["functions"].update(Bad={"file": "functions/clips.py"}), "lowercase identifiers"),
])
@pytest.mark.asyncio
async def test_functions_are_validated_without_running_them(change, problem) -> None:
    with pytest.raises(app_platform.ManifestError, match=problem):
        await app_platform.install_package(clippings(change))


async def _run(tmp_path: Path, code: str, *, timeout: float = 20, grants=("*.example.org",), mocks=()):
    file = tmp_path / "functions" / "probe.py"
    file.parent.mkdir(exist_ok=True)
    file.write_text(code, encoding="utf-8")
    services = MemoryServices(grants=list(grants), schemas={"note": {"type": "object"}}, has_files=True,
                              records={}, files={}, mocks=list(mocks))
    return await execute(file, input={"n": 2}, app={"slug": "probe"}, trigger="test", timeout=timeout,
                         services=services), services


@pytest.mark.asyncio
async def test_the_function_host_serves_the_platform_and_guards_the_rest(tmp_path: Path) -> None:
    result, services = await _run(tmp_path, (
        "import json\n"
        "def handle(input, cockpit):\n"
        "    print('working')\n"
        "    cockpit.data.put('note', 'a', {'n': input['n']})\n"
        "    cockpit.files.write('out/a.txt', 'hi')\n"
        "    page = cockpit.fetch('https://api.example.org/v')\n"
        "    return {'notes': len(cockpit.data.query('note')), 'file': cockpit.files.read_text('out/a.txt'),\n"
        "            'fetched': page.json(), 'status': page.status}\n"),
        mocks=[FetchMock(url="https://api.example.org/v", body=b'{"v": 1}')])
    assert result.ok, result.error
    assert result.output == {"notes": 1, "file": "hi", "fetched": {"v": 1}, "status": 200}
    assert "working" in result.logs
    assert services.records[("note", "a")]["value"] == {"n": 2}

    blocked = {
        "import socket\ndef handle(input, cockpit):\n    socket.getaddrinfo('example.org', 80)\n": "socket.getaddrinfo",
        "import subprocess\ndef handle(input, cockpit):\n    subprocess.run(['cmd', '/c', 'echo'])\n": "subprocess.Popen",
        "def handle(input, cockpit):\n    open('elsewhere.txt', 'w')\n": "cannot write files",
        f"def handle(input, cockpit):\n    open({str(Path(__file__))!r}).read()\n": "only read their own package",
        "def handle(input, cockpit):\n    cockpit.fetch('https://evil.test/')\n": "not in the app's network grants",
        "def handle(input, cockpit):\n    return object()\n": "not JSON",
        "def handle(input, cockpit):\n    raise ValueError('bad input')\n": "ValueError: bad input",
    }
    for code, message in blocked.items():
        result, _ = await _run(tmp_path, code)
        assert result.status == "error" and message in result.error, (code, result.error)

    environment, _ = await _run(tmp_path, "import os\ndef handle(input, cockpit):\n    return sorted(os.environ)\n")
    assert not [name for name in environment.output if "KEY" in name or "TOKEN" in name or "SECRET" in name]
    endless, _ = await _run(tmp_path, "def handle(input, cockpit):\n    while True:\n        pass\n", timeout=2)
    assert endless.status == "timeout"


@pytest.mark.asyncio
async def test_fetch_reaches_only_granted_public_hosts(web) -> None:
    pages, requested = web
    pages["https://example.org/a"] = httpx.Response(302, headers={"location": "https://www.example.org/b"})
    pages["https://www.example.org/b"] = httpx.Response(200, text="landed")
    pages["https://example.org/out"] = httpx.Response(302, headers={"location": "https://elsewhere.test/"})
    grants = ["example.org", "*.example.org", "intranet.example.com"]

    landed = await network.fetch(grants, url="https://example.org/a")
    assert (landed["status"], landed["url"]) == (200, "https://www.example.org/b")
    assert network.host_allowed("deep.www.example.org", grants) and not network.host_allowed("example.org.evil", grants)
    with pytest.raises(network.NetworkError, match="not in the app's network grants"):
        await network.fetch(grants, url="https://example.org/out")
    with pytest.raises(network.NetworkError, match="non-public"):
        await network.fetch(grants, url="https://intranet.example.com/")
    for url, message in (("file:///etc/passwd", "http and https"), ("https://user:pw@example.org/", "credentials")):
        with pytest.raises(network.NetworkError, match=message):
            await network.fetch(grants, url=url)
    assert "https://elsewhere.test/" not in requested


@pytest.mark.asyncio
async def test_files_are_stored_within_the_quota_and_trigger_event_functions() -> None:
    await app_platform.install_package(clippings(lambda m, root: m["files"].update(quota_mb=1)))
    meta = await app_platform.put_file("clippings", "clips/a.txt", b"milk bread eggs", "text/plain; charset=utf-8")
    assert (meta["size"], meta["content_type"]) == (15, "text/plain")
    replaced = await app_platform.put_file("clippings", "clips/a.txt", b"milk bread", "text/plain")
    assert replaced["size"] == 10
    assert len(list((settings.app_files_path / "clippings").iterdir())) == 1  # the old bytes are gone
    assert [item["name"] for item in await app_platform.list_files("clippings", "clips/")] == ["clips/a.txt"]
    for name in ("../x", "/abs", "a//b", "clips/", "clips/../x", "a\\b"):
        with pytest.raises(app_platform.FileError):
            await app_platform.put_file("clippings", name, b"x")
    with pytest.raises(app_platform.FileError, match="full"):
        await app_platform.put_file("clippings", "big.bin", b"x" * (1024 * 1024))

    # Two puts queued two changes for index_clip; the runtime runs it for each.
    assert await runtime.run_events() == 2
    record = await app_platform.get_record("clippings", "clip", "clip:a.txt")
    assert record["value"] == {"file": "clips/a.txt", "words": 2, "preview": "milk bread"}
    assert await app_platform.delete_file("clippings", "clips/a.txt") is True
    assert await runtime.run_events() == 1
    assert await app_platform.get_record("clippings", "clip", "clip:a.txt") is None
    status = await app_platform.status("clippings")
    assert [run["function"] for run in status["recent_runs"]] == ["index_clip"] * 3
    assert status["stored_files"] == {"count": 0, "bytes": 0}

    # Files outside the watched prefix, and changes nobody listens to, queue nothing.
    await app_platform.put_file("clippings", "other/b.txt", b"x")
    await app_platform.put_record("clippings", "summary", "latest", {"clips": 0, "words": 0})
    async with apps_session() as session:
        assert (await session.execute(select(AppEvent))).scalars().all() == []

    assert await app_platform.reset("clippings", files=True) == {"records": 1, "files": 1}
    await app_platform.uninstall("clippings")
    assert not (settings.app_files_path / "clippings").exists()


@pytest.mark.asyncio
async def test_scheduled_functions_run_once_when_due(monkeypatch) -> None:
    await app_platform.install_package(clippings())
    await app_platform.put_record("clippings", "clip", "clip:a.txt", {"file": "clips/a.txt", "words": 4, "preview": "a"})
    now = datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc)
    # The first look only schedules: 07:00 Amsterdam is 06:00 UTC.
    assert await runtime.run_schedules(now) == []
    async with apps_session() as session:
        [row] = (await session.execute(select(AppSchedule))).scalars().all()
        assert row.next_run_at.replace(tzinfo=timezone.utc) == datetime(2026, 11, 2, 6, tzinfo=timezone.utc)
    assert await runtime.run_schedules(now + timedelta(minutes=59)) == []
    # Cockpit was off for two days: the missed runs collapse into one.
    late = now + timedelta(days=2)
    assert await runtime.run_schedules(late) == ["clippings.daily_summary"]
    assert await runtime.run_schedules(late) == []
    summary = await app_platform.get_record("clippings", "summary", "latest")
    assert summary["value"]["clips"] == 1 and summary["value"]["words"] == 4
    assert summary["value"]["counted_for"].startswith("2026-11-02T06:00")
    status = await app_platform.status("clippings")
    assert status["functions"]["daily_summary"]["next_run_at"] == "2026-11-04T06:00:00+00:00"
    assert status["recent_runs"][0]["trigger"] == "schedule"

    # A changed schedule starts counting again; a removed one is forgotten.
    def hourly(manifest, _root):
        manifest["version"] = 2
        manifest["functions"]["daily_summary"]["trigger"]["schedule"] = "@hourly"
    await app_platform.install_package(clippings(hourly, name="v2"))
    assert await runtime.run_schedules(late) == []
    async with apps_session() as session:
        [row] = (await session.execute(select(AppSchedule))).scalars().all()
        assert (row.cron, row.next_run_at.replace(tzinfo=timezone.utc)) == ("@hourly", late + timedelta(hours=1))
    def unscheduled(manifest, _root):
        manifest["version"] = 3
        manifest["functions"]["daily_summary"].pop("trigger")
    await app_platform.install_package(clippings(unscheduled, name="v3"))
    await runtime.run_schedules(late)
    async with apps_session() as session:
        assert (await session.execute(select(AppSchedule))).scalars().all() == []


@pytest.mark.asyncio
async def test_event_chains_stop_and_failures_become_app_errors(tmp_path: Path) -> None:
    def looping(manifest, root):
        (root / "functions" / "loop.py").write_text(
            "def handle(input, cockpit):\n"
            "    cockpit.data.put('summary', 'latest', {'clips': input['value']['clips'] + 1, 'words': 0})\n")
        manifest["functions"]["loop"] = {"file": "functions/loop.py",
                                         "trigger": {"event": "record_changed", "collection": "summary"}}
    await app_platform.install_package(clippings(looping))
    await app_platform.put_record("clippings", "summary", "latest", {"clips": 0, "words": 0})
    for _ in range(5):
        await runtime.run_events()
    # The owner's write, then three event-triggered writes; the fourth level is refused.
    assert (await app_platform.get_record("clippings", "summary", "latest"))["value"]["clips"] == 3
    errors = await app_platform.recent_errors("clippings")
    assert "Stopped a chain of 3" in errors[0]["message"]

    failed = await app_platform.run_function("clippings", "fetch_page", {"url": "https://evil.test/"})
    assert failed.status == "error" and "network grants" in failed.error
    errors = await app_platform.recent_errors("clippings")
    assert errors[0]["message"].startswith("Function fetch_page failed (call)")
    with pytest.raises(app_platform.FunctionError, match="no function"):
        await app_platform.run_function("clippings", "missing")


@pytest.mark.asyncio
async def test_new_errors_start_one_repair_a_day(monkeypatch) -> None:
    await app_platform.install_package(clippings())
    started = []

    async def starter(app, errors):
        started.append((app.slug, [error["message"] for error in errors]))
        return f"task-{len(started)}"

    now = datetime.now(timezone.utc)
    assert await runtime.start_repairs(starter, now) == []
    await app_platform.record_error("clippings", "TypeError: x is undefined")
    assert await runtime.start_repairs(starter, now) == ["clippings"]
    assert started == [("clippings", ["TypeError: x is undefined"])]
    # The same day, nothing more starts, even for new errors.
    await app_platform.record_error("clippings", "Another error")
    assert await runtime.start_repairs(starter, now + timedelta(hours=1)) == []
    # A day later, errors seen after the last repair started trigger the next one...
    assert await runtime.start_repairs(starter, now + timedelta(days=1, minutes=1)) == ["clippings"]
    # ...but old errors alone do not, and neither do errors of older versions.
    assert await runtime.start_repairs(starter, now + timedelta(days=3)) == []

    def bump(manifest, _root):
        manifest["version"] = 2
    await app_platform.install_package(clippings(bump, name="v2"))
    async def failing(app, errors):
        raise RuntimeError("builder unavailable")
    assert await runtime.start_repairs(failing, now + timedelta(days=5)) == []


@pytest.mark.asyncio
async def test_repairs_start_the_builder_with_the_reported_errors(monkeypatch) -> None:
    from app.services.app_repair import start_app_repair
    from app.services.task_service import task_service
    from app.services.task_workflows import load_workflow

    app_ = await app_platform.install_package(clippings())
    errors = [{"version": 1, "message": "Function fetch_page failed (schedule): boom", "count": 2}]
    assert await start_app_repair(app_, errors) is None  # the builder is not enabled
    monkeypatch.setattr(settings, "task_workflow_allowlist", f"build_app@1:{load_workflow('build_app@1').digest}")
    task_id = await start_app_repair(app_, errors)
    task = (await task_service.get_task(task_id)).task
    assert task.origin_surface == "app_platform" and task.execution_kind == "workflow"
    assert task.goal.startswith("Fix the installed app Clippings (slug clippings, version 1)")
    assert "boom (x2)" in task.goal
    assert await start_app_repair(app_, errors) == task_id  # idempotent per error set


@pytest.mark.asyncio
async def test_http_files_and_function_calls_with_the_app_token(web) -> None:
    pages, _ = web
    pages["https://example.org/soup"] = httpx.Response(200, text="<h1>Soup</h1><p>Simmer gently.</p>")
    await app_platform.install_package(clippings())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as owner:
        await owner.post("/api/auth/login", json={"password": ""})
        token = (await owner.post("/api/app-platform/apps/clippings/session")).json()["token"]
        # The owner (and Jarvis) may call functions the UI cannot.
        called = (await owner.post("/api/app-platform/functions/clippings/daily_summary", json={})).json()
        assert called["ok"] is True and called["output"]["clips"] == 0

    headers = {"Authorization": f"AppPlatform {token}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers=headers) as scoped:
        stored = await scoped.put("/api/app-platform/files/clippings/clips/a b.txt", content=b"one two",
                                  headers={"Content-Type": "text/plain"})
        assert stored.status_code == 200 and stored.json()["name"] == "clips/a b.txt"
        listed = (await scoped.get("/api/app-platform/files/clippings", params={"prefix": "clips/"})).json()
        assert [item["name"] for item in listed["files"]] == ["clips/a b.txt"]
        downloaded = await scoped.get("/api/app-platform/files/clippings/clips/a b.txt")
        assert downloaded.content == b"one two"
        assert downloaded.headers["content-security-policy"] == "sandbox"
        assert downloaded.headers["content-disposition"].startswith("attachment")
        assert (await scoped.get("/api/app-platform/files/clippings/clips/none.txt")).status_code == 404
        assert (await scoped.put("/api/app-platform/files/clippings/%2e%2e/x", content=b"x")).status_code == 422

        clipped = (await scoped.post("/api/app-platform/functions/clippings/fetch_page",
                                     json={"input": {"url": "https://example.org/soup"}})).json()
        assert clipped == {"ok": True, "status": "ok", "duration_ms": clipped["duration_ms"],
                           "output": {"file": "clips/example.org-soup.txt", "characters": 19},
                           "logs": clipped["logs"]}
        failed = (await scoped.post("/api/app-platform/functions/clippings/fetch_page",
                                    json={"input": {"url": "https://example.org/gone"}})).json()
        assert failed["ok"] is False and "answered 404" in failed["error"]
        assert (await scoped.post("/api/app-platform/functions/clippings/daily_summary", json={})).status_code == 403
        assert (await scoped.post("/api/app-platform/functions/clippings/nope", json={})).status_code == 404
        assert (await scoped.delete("/api/app-platform/files/clippings/clips/a b.txt")).json() == {"deleted": True}


@pytest.mark.asyncio
async def test_jarvis_calls_functions_and_sees_runs(web) -> None:
    from tests.test_app_builder import _app_tool

    await app_platform.install_package(clippings())
    listed = await _app_tool("list_apps", {})
    assert listed["apps"][0]["functions"] == ["daily_summary", "fetch_page", "index_clip"]
    counted = await _app_tool("call_app_function", {"app": "Clippings", "function": "daily_summary"})
    assert counted["ok"] is True and counted["output"]["clips"] == 0
    missing = await _app_tool("call_app_function", {"app": "clippings", "function": "nope"})
    assert "no function" in missing["error"] and "fetch_page" in missing["functions"]
    status = await _app_tool("app_status", {"app": "clippings"})
    assert status["recent_runs"][0]["function"] == "daily_summary"
    assert status["functions"]["daily_summary"]["trigger"] == {"schedule": "0 7 * * *"}
    await app_platform.put_file("clippings", "clips/a.txt", b"x")
    reset = await _app_tool("manage_app", {"app": "clippings", "action": "reset", "files": True})
    assert (reset["deleted_records"], reset["deleted_files"]) == (1, 1)


@needs_browser
@pytest.mark.asyncio
async def test_the_reference_package_passes_its_ui_and_function_tests(tmp_path: Path) -> None:
    report = await check_package(clippings(), screenshots=tmp_path / "shots")
    assert report["ok"], report["problems"]
    names = [test["name"] for test in report["tests"]]
    assert "uploads a text file into the file store" in names and "function daily_summary: counts the clips" in names

    def untested(manifest, root):
        tests = json.loads((root / "tests" / "functions.json").read_text(encoding="utf-8"))
        tests["tests"] = [test for test in tests["tests"] if test["function"] != "daily_summary"]
        (root / "tests" / "functions.json").write_text(json.dumps(tests), encoding="utf-8")
        (root / "functions" / "fetch_page.py").write_text(
            "def handle(input, cockpit):\n    return cockpit.fetch(input['url']).status\n")
    broken = await check_package(clippings(untested, name="broken"))
    assert not broken["ok"]
    assert "Add a test for function daily_summary in tests/functions.json" in broken["problems"]
    failed = {test["name"]: test for test in broken["tests"] if not test["ok"]}
    assert "function fetch_page: saves a page's text" in failed
