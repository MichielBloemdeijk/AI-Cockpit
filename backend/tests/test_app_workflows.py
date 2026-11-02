"""Apps start their own workflows; Jarvis writes app data and starts app workflows."""
from __future__ import annotations

import json
from pathlib import Path
import shutil

from httpx import ASGITransport, AsyncClient
import pytest

from app import app_platform
from app.app_platform.builtin import builtin_root
from app.app_platform.check import check_package
from app.config import settings
from app.main import app
from app.services.app_workflows import AppWorkflowError, start_app_workflow
from app.services.tasks.service import task_service
from tests.test_app_builder import _app_tool
from tests.test_app_platform import build
from tests.test_builtin_apps import needs_browser
from app_packages import staging_path


@pytest.fixture(autouse=True)
def research_enabled(monkeypatch):
    # The recipe workflow researches; its capability is off unless enabled.
    monkeypatch.setattr(settings, "task_research_enabled", True)


def cookbook(edit=None, *, name: str = "cookbook") -> Path:
    """A Recipes-like app whose workflow takes a text request, and a function and button that start it."""
    target = staging_path() / name
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(builtin_root() / "recipes", target)
    workflows = target / "workflows"
    (workflows / "recipe_from_screen").rename(workflows / "recipe_from_text")
    yaml = workflows / "recipe_from_text" / "workflow.yaml"
    yaml.write_text(yaml.read_text(encoding="utf-8")
                    .replace("id: recipe_from_screen", "id: recipe_from_text")
                    .replace("input_schema: screen-request.v2", "input_schema: text-request.v1")
                    .replace("required_initial_context: [screen_images]", "required_initial_context: []")
                    .replace("  - task.context.images@2\n", ""), encoding="utf-8")
    (target / "functions").mkdir()
    (target / "functions" / "weekly.py").write_text(
        "def handle(input, cockpit):\n"
        "    return cockpit.workflows.start('recipe_from_text', input.get('goal') or 'A new weeknight recipe')\n",
        encoding="utf-8")
    (target / "tests" / "functions.json").write_text(json.dumps({"tests": [
        {"name": "starts the recipe workflow", "function": "weekly", "input": {"goal": "A quick soup"},
         "expect": {"output": {"task_id": "test-task-1"},
                    "workflows": [{"workflow": "recipe_from_text", "count": 1}]}}]}), encoding="utf-8")
    path = target / "cockpit-app.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest.update(slug="cookbook", title="Cookbook", workflows=["recipe_from_text"],
                    functions={"weekly": {"file": "functions/weekly.py", "trigger": {"schedule": "0 9 * * SUN"}}})
    if edit is not None:
        edit(manifest, target)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return target


@pytest.mark.asyncio
async def test_an_app_function_starts_its_workflow_as_a_task(monkeypatch) -> None:
    await app_platform.install_package(cookbook())
    ran = await app_platform.run_function("cookbook", "weekly", {"goal": "A quick soup"})
    assert ran.ok, ran.error
    task = (await task_service.get_task(ran.output["task_id"])).task
    assert (task.goal, task.origin_surface, task.execution_kind) == ("A quick soup", "app_platform", "workflow")
    assert ran.output["url"] == f"/tasks/{task.id}"

    # A retried start returns the same task; a new run starts another.
    again = await start_app_workflow("cookbook", "recipe_from_text", "A quick soup", request_key="fixed")
    assert (await start_app_workflow("cookbook", "recipe_from_text", "A quick soup",
                                     request_key="fixed"))["task_id"] == again["task_id"]
    monkeypatch.setattr(settings, "app_workflow_daily_limit", 2)
    with pytest.raises(AppWorkflowError, match="already started 2 workflows"):
        await start_app_workflow("cookbook", "recipe_from_text", "One more", request_key="third")
    failed = await app_platform.run_function("cookbook", "weekly", {})
    assert not failed.ok and "its limit" in failed.error

    with pytest.raises(AppWorkflowError, match="no workflow"):
        await start_app_workflow("cookbook", "recipe_from_screen", "x", request_key="k")
    # Screen workflows need the phone's screen, which only Jarvis captures.
    with pytest.raises(AppWorkflowError, match="only Jarvis can start it"):
        await start_app_workflow("recipes", "recipe_from_screen", "Find a recipe", request_key="k")


@pytest.mark.asyncio
async def test_an_app_ui_starts_its_workflow_with_its_own_token() -> None:
    await app_platform.install_package(cookbook())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as owner:
        await owner.post("/api/auth/login", json={"password": ""})
        token = (await owner.post("/api/app-platform/apps/cookbook/session")).json()["token"]
        recipes_token = (await owner.post("/api/app-platform/apps/recipes/session")).json()["token"]
    headers = {"Authorization": f"AppPlatform {token}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers=headers) as scoped:
        body = {"goal": "A pasta for tonight", "request_key": "button-1"}
        started = await scoped.post("/api/app-platform/workflows/cookbook/recipe_from_text", json=body)
        assert started.status_code == 200
        repeated = await scoped.post("/api/app-platform/workflows/cookbook/recipe_from_text", json=body)
        assert repeated.json()["task_id"] == started.json()["task_id"]
        assert (await scoped.post("/api/app-platform/workflows/recipes/recipe_from_screen", json=body)).status_code == 403
        missing = await scoped.post("/api/app-platform/workflows/cookbook/nope", json=body)
        assert missing.status_code == 422 and "no workflow" in missing.json()["detail"]
    other = {"Authorization": f"AppPlatform {recipes_token}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers=other) as scoped:
        refused = await scoped.post("/api/app-platform/workflows/recipes/recipe_from_screen",
                                    json={"goal": "x", "request_key": "k"})
        assert refused.status_code == 422 and "only Jarvis" in refused.json()["detail"]


@pytest.mark.asyncio
async def test_jarvis_starts_app_workflows_and_writes_app_data() -> None:
    from app.services.jarvis.tool_catalog import get_jarvis_tool_definitions

    await app_platform.install_package(cookbook())
    start = next(tool for tool in get_jarvis_tool_definitions() if tool["name"] == "start_workflow")
    assert "recipe_from_text@1" in start["parameters"]["properties"]["definition"]["enum"]
    assert "recipe_from_text@1 (app cookbook)" in start["description"]
    started = await _app_tool("start_workflow", {"goal": "A recipe for leek soup",
                                                 "definition": "recipe_from_text@1", "intent_id": "i1"})
    assert started["accepted"] is True

    await app_platform.install_package(build())
    added = await _app_tool("write_app_data", {"app": "Notes", "action": "add", "value": {"text": "buy milk"}})
    assert added["done"] is True and added["key"].startswith("note-")
    updated = await _app_tool("write_app_data", {"app": "notes", "action": "update", "key": added["key"],
                                                  "value": {"pinned": True}})
    assert updated["value"] == {"text": "buy milk", "pinned": True}
    invalid = await _app_tool("write_app_data", {"app": "notes", "action": "add", "value": {"colour": "red"}})
    assert invalid["done"] is False and "invalid" in invalid["error"]
    assert (await _app_tool("write_app_data", {"app": "notes", "action": "update", "key": "nope",
                                               "value": {"pinned": True}}))["done"] is False
    deleted = await _app_tool("write_app_data", {"app": "notes", "action": "delete", "key": added["key"]})
    assert deleted["done"] is True
    assert await app_platform.query_records("notes", "note") == []


@needs_browser
@pytest.mark.asyncio
async def test_the_check_records_workflow_starts_in_ui_and_function_tests() -> None:
    def button(manifest, root):
        page = root / "ui" / "index.html"
        page.write_text(page.read_text(encoding="utf-8").replace(
            "</body>", "<button id=\"plan\" onclick=\"cockpit.workflows.start('recipe_from_text', "
                       "'A recipe for tonight').then((s) => { this.textContent = s.task_id; })\">Plan</button></body>"),
            encoding="utf-8")
        tests_path = root / "tests" / "ui.json"
        tests = json.loads(tests_path.read_text(encoding="utf-8"))
        tests["tests"].append({"name": "starts the workflow", "steps": [
            {"click": "#plan"}, {"expect_workflows": {"count": 1, "workflow": "recipe_from_text"}},
            {"expect_text": "test-task-1", "selector": "#plan"}]})
        tests_path.write_text(json.dumps(tests), encoding="utf-8")
    await app_platform.uninstall("recipes")  # its workflow id stays reserved while installed
    report = await check_package(cookbook(button))
    assert report["ok"], report["problems"]
    names = [test["name"] for test in report["tests"]]
    assert "starts the workflow" in names and "function weekly: starts the recipe workflow" in names


@pytest.mark.asyncio
async def test_a_workflow_folder_must_be_listed_in_the_manifest() -> None:
    def stray(manifest, root):
        (root / "workflows" / "recipe_from_text").rename(root / "workflows" / "unlisted_flow")
        manifest["workflows"] = []
    with pytest.raises(app_platform.ManifestError, match="workflows/unlisted_flow is not listed"):
        await app_platform.install_package(cookbook(stray))


def test_the_builders_workflow_example_is_valid(tmp_path: Path) -> None:
    """The workflow.yaml the build_app instructions show must load as written."""
    import re
    from app.services.tasks.workflows import load_package_workflow

    instructions = (Path(__file__).parents[1] / "workflows" / "build_app" / "1" / "instructions.md").read_text(
        encoding="utf-8")
    example = re.search(r"```yaml\n(.*?)```", instructions, re.S).group(1)
    folder = tmp_path / "plant_from_photo"
    (folder / "fixtures").mkdir(parents=True)
    (folder / "workflow.yaml").write_text("\n".join(line.removeprefix("  ") for line in example.splitlines()),
                                          encoding="utf-8")
    (folder / "instructions.md").write_text("Identify the plant and publish it.", encoding="utf-8")
    (folder / "fixtures" / "cases.yaml").write_text(
        'cases: [{id: clear, request: "What plant is this?", expected: sourced_record}]', encoding="utf-8")
    loaded = load_package_workflow(folder, "plant_from_photo", app="plants", result_collections=("plant",))
    assert loaded.output_schema == "collection:plants.plant" and loaded.required_initial_context == ("screen_images",)
