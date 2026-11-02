"""App platform step 2: lifecycle management, the package check and the build_app workflow."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
from types import SimpleNamespace

from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio

from app import app_platform
from app.config import settings
from app.jarvis_tasks.task_mcp import _call, _tool_catalog
from app.main import app
from app.services.tasks.service import task_service
from app.services.tasks.workflows import load_workflow
from app.services.tasks.worker_coordinator import worker_coordinator
from app.services.conversations.store import conversation_store
from tests.test_app_platform import SAMPLE, build


@pytest_asyncio.fixture(autouse=True)
async def without_builtin_apps():
    """These tests start from a platform with no apps; conftest installs Documents and Recipes."""
    for slug in ("documents", "recipes"):
        await app_platform.uninstall(slug)


needs_browser = pytest.mark.skipif(
    shutil.which("node") is None or not (settings.app_check_playwright_path / "node_modules" / "playwright").is_dir(),
    reason="The UI check needs Node.js and Playwright from the frontend")


def broken(target: Path, script: str) -> Path:
    """Replace the sample UI's script with one under test."""
    page = target / "ui" / "index.html"
    html = page.read_text(encoding="utf-8")
    start = html.rindex("<script>")
    page.write_text(html[:start] + f"<script>{script}</script></body></html>", encoding="utf-8")
    return target


@pytest.mark.asyncio
async def test_reinstall_is_a_no_op_and_reset_status_uninstall_manage_data() -> None:
    await app_platform.install_package(build())
    again = await app_platform.install_package(build())
    assert again.version == 1
    with pytest.raises(app_platform.ManifestError, match="not newer"):
        await app_platform.install_package(build(edit=lambda manifest, _schema: manifest.update(title="Changed")))

    await app_platform.put_record("notes", "note", "a", {"text": "one"})
    await app_platform.put_record("notes", "note", "b", {"text": "two"})
    await app_platform.record_error("notes", "cockpit is not defined", "at render")
    await app_platform.record_error("notes", "cockpit   is not defined")
    status = await app_platform.status("notes")
    assert status["versions"] == [1] and status["records"] == {"note": 2}
    assert [(error["message"], error["count"]) for error in status["recent_errors"]] == [("cockpit is not defined", 2)]

    with pytest.raises(app_platform.ManifestError, match="no collection"):
        await app_platform.reset("notes", "todo")
    assert await app_platform.reset("notes") == {"records": 2, "files": 0}
    assert (await app_platform.status("notes"))["records"] == {"note": 0}

    checkout = settings.app_builds_path / "checkout"
    assert (await app_platform.checkout("notes", checkout)).version == 1
    assert (checkout / "tests" / "ui.json").is_file()
    with pytest.raises(app_platform.ManifestError, match="not empty"):
        await app_platform.checkout("notes", checkout)

    await app_platform.uninstall("notes")
    assert await app_platform.list_apps() == []
    assert not (settings.apps_path / "notes").exists()
    with pytest.raises(app_platform.AppNotFound):
        await app_platform.uninstall("notes")
    # A fresh install after uninstalling starts over, without old records or errors.
    await app_platform.install_package(build())
    assert (await app_platform.status("notes"))["recent_errors"] == []


@pytest.mark.asyncio
async def test_http_management_and_scoped_error_reports() -> None:
    build()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": ""})
        assert (await client.post("/api/app-platform/apps/install", json={"source": "notes"})).status_code == 200
        token = (await client.post("/api/app-platform/apps/notes/session")).json()["token"]
        scoped = {"Authorization": f"AppPlatform {token}"}
        reported = await client.post("/api/app-platform/apps/notes/errors", headers=scoped,
                                     json={"message": "boom", "detail": "stack"})
        assert reported.status_code == 204
        status = (await client.get("/api/app-platform/apps/notes/status")).json()
        assert status["recent_errors"][0]["message"] == "boom"
        await app_platform.put_record("notes", "note", "a", {"text": "x"})
        assert (await client.post("/api/app-platform/apps/notes/reset", json={})).json() == {"records": 1, "files": 0}
        assert (await client.delete("/api/app-platform/apps/notes")).json() == {"uninstalled": True}
        assert (await client.get("/api/app-platform/apps/notes/status")).status_code == 404
    # The app's own token reaches its data and error reports, never management.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers=scoped) as app_client:
        assert (await app_client.post("/api/app-platform/apps/notes/reset", json={})).status_code == 401
        assert (await app_client.delete("/api/app-platform/apps/notes")).status_code == 401


@needs_browser
@pytest.mark.asyncio
async def test_check_runs_the_ui_under_the_real_sandbox(tmp_path: Path) -> None:
    sample = tmp_path / "notes"
    shutil.copytree(SAMPLE, sample)
    report = await app_platform.check_package(sample, screenshots=tmp_path / "shots")
    assert report["ok"], report
    assert [test["name"] for test in report["tests"]] == [
        "loads", "adds and pins a note", "lists stored notes with pinned first"]
    assert "buy milk" in report["tests"][1]["text"]
    assert all(Path(test["screenshot"]).is_file() for test in report["tests"])

    crashing = tmp_path / "crashing"
    shutil.copytree(SAMPLE, crashing)
    broken(crashing, "cockpit.ready.then(() => { throw new Error('render exploded'); });")
    report = await app_platform.check_package(crashing)
    assert not report["ok"]
    assert any("render exploded" in problem for problem in report["tests"][0]["problems"])

    networked = tmp_path / "networked"
    shutil.copytree(SAMPLE, networked)
    broken(networked, "fetch('/api/tasks').catch(() => {});")
    report = await app_platform.check_package(networked)
    assert not report["ok"]
    assert any("connect" in problem.casefold() for problem in report["tests"][0]["problems"])

    invalid = tmp_path / "invalid"
    shutil.copytree(SAMPLE, invalid)
    broken(invalid, "cockpit.data.put('note', 'a', { text: '' });")
    report = await app_platform.check_package(invalid)
    assert any("invalid note record" in problem for problem in report["tests"][0]["problems"])

    untested = tmp_path / "untested"
    shutil.copytree(SAMPLE, untested)
    (untested / "tests" / "ui.json").unlink()
    report = await app_platform.check_package(untested)
    assert report["problems"] == ["Add UI tests in tests/ui.json that exercise the app's main actions"]


@pytest.mark.asyncio
async def test_check_reports_version_and_compatibility_against_the_installed_app(monkeypatch) -> None:
    async def no_browser(job):
        return {"tests": [{"name": "loads", "ok": True, "error": None, "problems": [], "text": "", "records": []}]}
    monkeypatch.setattr("app.app_platform.check._run_harness", no_browser)
    await app_platform.install_package(build())
    assert (await app_platform.check_package(build()))["ok"] is True  # the active package itself
    await app_platform.put_record("notes", "note", "a", {"text": "milk", "pinned": True})

    def retype(manifest, schema):
        manifest["title"] = "Notes 2"
        schema["properties"]["pinned"] = {"type": "string"}
    report = await app_platform.check_package(build("changed", edit=retype))
    assert report["problems"][0] == "Set version to 2: version 1 is installed"
    assert report["problems"][1].startswith("Incompatible with stored data: note.pinned changed type, and 1 of 1 "
                                            "stored note records don't fit the new schema")
    # The package's own test seeds are validated against its new schema too.
    assert "seed 'a' is invalid" in report["problems"][2]


async def _app_tool(tool_name: str, arguments: dict) -> dict:
    from app.services.jarvis_brain import restricted_brain_gateway

    conversation = await conversation_store.create_conversation(title="Apps")
    result = await restricted_brain_gateway.execute(
        tool_name=tool_name, arguments=arguments, call_id=f"call-{tool_name}-{conversation.id}",
        conversation_id=conversation.id, origin_turn_id=None)
    return result.output


def _build_workflow_enabled(monkeypatch) -> None:
    definition = load_workflow("build_app@1")
    monkeypatch.setattr(settings, "task_workflow_allowlist", f"build_app@1:{definition.digest}")


async def _claimed_build(goal: str) -> SimpleNamespace:
    created = await task_service.dispatch(goal=goal, origin_surface="test", idempotency_key=f"build:{goal}",
                                          execution_kind="workflow", execution_config={"definition": "build_app@1"})
    worker = await worker_coordinator.activate(process_identity="builder-worker")
    claim = await worker_coordinator.claim_next(worker.worker_id)
    assert claim.task is not None and claim.task.task_id == created.task.id
    workspace = Path(claim.task.workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(task_id=created.task.id, run_id=claim.task.execution_run_id,
                           attempt_id=claim.task.attempt_id, claim_token=claim.task.claim_token,
                           workspace=workspace, workflow_schema="app-build.v1")


@pytest.mark.asyncio
async def test_build_workflow_checks_out_installs_on_publish_and_replays(monkeypatch) -> None:
    _build_workflow_enabled(monkeypatch)
    checks = []

    async def fake_check(root, *, screenshots=None):
        checks.append(root)
        manifest = json.loads((root / "cockpit-app.json").read_text(encoding="utf-8"))
        ok = manifest["version"] == 2
        return {"ok": ok, "problems": [] if ok else ["Set version to 2: version 1 is installed"],
                "tests": [{"name": "loads", "ok": ok, "error": None, "problems": []}]}
    monkeypatch.setattr(app_platform, "check_package", fake_check)
    await app_platform.install_package(build())
    args = await _claimed_build("Change the installed app Notes: add a colour")

    catalog = {tool["name"]: tool for tool in await _tool_catalog("app-build.v1", load_workflow("build_app@1").capabilities)}
    assert set(catalog) == {"task_history", "app_checkout", "app_catalog", "app_check", "task_question", "result_publish",
                            "task_finish"}
    publish_fields = set(catalog["result_publish"]["inputSchema"]["properties"])
    assert {"slug", "version"}.isdisjoint(publish_fields) and {"title", "body"} <= publish_fields

    missing = await _call("app_checkout", {"app": "calendar"}, args)
    assert missing == {"found": False, "apps": ["notes"]}
    checked_out = await _call("app_checkout", {"app": "Notes"}, args)
    assert checked_out["next_version"] == 2 and checked_out["records"] == {"note": 0}
    package = args.workspace / "app"
    assert (package / "ui" / "index.html").is_file()

    publication = {"title": "Added colours", "body": "Notes can have a colour.", "source_event_sequences": [],
                   "operation_key": "publish"}
    refused = await _call("result_publish", publication, args)
    assert refused["status"] == "not_installed" and "version 1 is installed" in refused["problems"][0]
    assert (await app_platform.get_active("notes")).version == 1
    assert (await task_service.get_task(args.task_id)).task.status == "running"

    manifest = json.loads((package / "cockpit-app.json").read_text(encoding="utf-8"))
    manifest["version"] = 2
    (package / "cockpit-app.json").write_text(json.dumps(manifest), encoding="utf-8")
    published = await _call("result_publish", publication, args)
    assert published["status"] == "completed"
    assert (await app_platform.get_active("notes")).version == 2
    detail = await task_service.get_task(args.task_id)
    assert detail.task.status == "completed"
    assert detail.artifacts[0].content_json == {"schema": "app-build.v1", "content": {
        "title": "Added colours", "body": "Notes can have a colour.", "source_event_sequences": [],
        "slug": "notes", "version": 2}}

    checks.clear()
    assert await _call("result_publish", publication, args) == published
    assert checks == []  # a replay after completion installs nothing


@pytest.mark.asyncio
async def test_jarvis_builds_fixes_reports_and_manages_apps(monkeypatch) -> None:
    _build_workflow_enabled(monkeypatch)
    started = await _app_tool("build_app", {"request": "A tic-tac-toe game that keeps score"})
    assert started["started"] is True
    task = (await task_service.get_task(started["task_id"])).task
    assert task.goal == "Build a new app: A tic-tac-toe game that keeps score"
    assert task.execution_kind == "workflow"

    await app_platform.install_package(build())
    assert (await _app_tool("build_app", {"request": "x", "app": "calendar"}))["found"] is False
    change = await _app_tool("build_app", {"request": "Add due dates", "app": "notes"})
    assert (await task_service.get_task(change["task_id"])).task.goal == (
        "Change the installed app Notes (slug notes, version 1): Add due dates")

    quiet = await _app_tool("fix_app", {"app": "notes"})
    assert quiet["started"] is False and "no errors" in quiet["speak"]
    await app_platform.record_error("notes", "cockpit is not defined")
    fixing = await _app_tool("fix_app", {"app": "notes"})
    goal = (await task_service.get_task(fixing["task_id"])).task.goal
    assert goal.startswith("Fix the installed app Notes") and "v1: cockpit is not defined (x1)" in goal

    status = await _app_tool("app_status", {"app": "notes"})
    assert status["versions"] == [1] and status["recent_errors"][0]["count"] == 1

    await app_platform.put_record("notes", "note", "a", {"text": "x"})
    assert (await _app_tool("manage_app", {"app": "notes", "action": "rollback"}))["done"] is False
    assert (await _app_tool("manage_app", {"app": "notes", "action": "reset"}))["deleted_records"] == 1
    assert (await _app_tool("manage_app", {"app": "notes", "action": "uninstall"}))["uninstalled"] is True
    assert (await _app_tool("list_apps", {}))["apps"] == []

    monkeypatch.setattr(settings, "task_workflow_allowlist", "")
    disabled = await _app_tool("build_app", {"request": "A plant tracker"})
    assert disabled["started"] is False and "not enabled" in disabled["error"]
