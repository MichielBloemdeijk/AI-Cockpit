"""Built-in apps (Recipes, Documents), workflows inside packages and record views."""
from __future__ import annotations

import json
from pathlib import Path
import shutil

from httpx import ASGITransport, AsyncClient
import pytest

from app import app_platform
from app.app_platform.builtin import builtin_root
from app.config import settings
from app.main import app
from app.services.tasks.workflows import enabled_workflows, load_workflow, workflow_catalog_revision

needs_browser = pytest.mark.skipif(
    shutil.which("node") is None or not (settings.app_check_playwright_path / "node_modules" / "playwright").is_dir(),
    reason="the UI check needs Node.js and the frontend's Playwright")


def package_copy(tmp_path: Path, slug: str, edit=None) -> Path:
    """A copy of a built-in package, optionally with its manifest changed."""
    target = tmp_path / "sources" / slug
    shutil.copytree(builtin_root() / slug, target)
    if edit is not None:
        path = target / "cockpit-app.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        edit(manifest, target)
        path.write_text(json.dumps(manifest), encoding="utf-8")
    return target


@pytest.mark.asyncio
async def test_builtin_apps_install_once_enable_their_workflows_and_never_overwrite_newer_versions(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "task_research_enabled", True)
    # conftest installed them, as Cockpit does on start.
    assert sorted(app.slug for app in await app_platform.list_apps()) == ["documents", "recipes"]
    assert await app_platform.install_builtin_apps() == []
    provided = app_platform.installed_workflows()
    assert {name: item.slug for name, item in provided.items()} == {
        "recipe_from_screen": "recipes", "research_screen": "documents", "screen_document": "documents"}
    # Installed is enabled: no allowlist entry needed.
    assert settings.task_workflow_allowlist == "" or "recipe_from_screen" not in settings.task_workflow_allowlist
    assert {item.id for item in enabled_workflows()} >= {"recipe_from_screen", "research_screen", "screen_document"}
    revision = workflow_catalog_revision()

    def bump(manifest, _root):
        manifest["version"] = 2
    root = tmp_path / "builtins"
    shutil.copytree(package_copy(tmp_path, "recipes", bump), root / "recipes")
    assert await app_platform.install_builtin_apps(root) == ["recipes@2"]
    assert (await app_platform.get_active("recipes")).version == 2
    # Jarvis's own version 3 is never replaced by an older source package on start.
    shutil.rmtree(tmp_path / "sources")
    def three(manifest, _root):
        manifest["version"] = 3
    await app_platform.install_package(package_copy(tmp_path, "recipes", three))
    assert await app_platform.install_builtin_apps(root) == []
    assert (await app_platform.get_active("recipes")).version == 3
    assert workflow_catalog_revision() == revision  # same workflow definitions

    await app_platform.uninstall("recipes")
    assert "recipe_from_screen" not in app_platform.installed_workflows()
    with pytest.raises(ValueError, match="not installed"):
        load_workflow("recipe_from_screen@1")
    assert workflow_catalog_revision() != revision


def taken_id(workflow_id):
    """Give the package's workflow an id the core or another app already uses."""
    def change(manifest, root):
        source, target = root / "workflows" / "recipe_from_screen", root / "workflows" / workflow_id
        source.rename(target)
        yaml = target / "workflow.yaml"
        yaml.write_text(yaml.read_text(encoding="utf-8").replace("id: recipe_from_screen", f"id: {workflow_id}"),
                        encoding="utf-8")
        manifest["workflows"] = [workflow_id]
    return change


@pytest.mark.asyncio
@pytest.mark.parametrize("change,problem", [
    (taken_id("text_report"), "already in use"),
    (taken_id("research_screen"), "already in use"),
    (lambda m, root: m["collections"]["recipe"].update(result_type=False), "result collections"),
    (lambda m, root: (root / "workflows" / "recipe_from_screen" / "workflow.yaml").write_text(
        (root / "workflows" / "recipe_from_screen" / "workflow.yaml").read_text(encoding="utf-8")
        .replace("  - task.finish@1", "  - task.finish@1\n  - app.package@1"), encoding="utf-8"), "core-only"),
    (lambda m, root: m.update(workflows=["missing_flow"]), "missing_flow is invalid"),
])
async def test_package_workflows_are_validated_on_install(tmp_path, change, problem):
    def bump(manifest, root):
        manifest["version"] = 2
        change(manifest, root)
    with pytest.raises(app_platform.ManifestError, match=problem):
        await app_platform.install_package(package_copy(tmp_path, "recipes", bump))
    assert (await app_platform.get_active("recipes")).version == 1


@pytest.mark.asyncio
async def test_a_result_collection_must_require_a_title(tmp_path):
    def untitled(manifest, root):
        manifest["version"] = 2
        schema = json.loads((root / "schemas" / "recipe.json").read_text(encoding="utf-8"))
        schema["required"].remove("title")
        (root / "schemas" / "recipe.json").write_text(json.dumps(schema), encoding="utf-8")
    with pytest.raises(app_platform.ManifestError, match="must require a string title"):
        await app_platform.install_package(package_copy(tmp_path, "recipes", untitled))


@pytest.mark.asyncio
async def test_apps_report_the_record_on_screen_with_their_own_token():
    session = await app_platform.get_active("documents")
    token = app_platform.issue_token(session.slug)
    headers = {"Authorization": f"{app_platform.TOKEN_SCHEME} {token}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        endpoint = "/api/app-platform/apps/documents/views"
        assert (await http.post(endpoint, json={"collection": "document", "key": "doc-1"})).status_code == 401
        assert (await http.post(endpoint, json={"collection": "document", "key": "doc-1"}, headers=headers)).status_code == 204
        assert (await http.post("/api/app-platform/apps/recipes/views", json={"collection": "recipe", "key": "r"},
                                headers=headers)).status_code == 403
        assert (await http.post(endpoint, json={"collection": "nope", "key": "doc-1"}, headers=headers)).status_code == 422
        assert (await http.post(endpoint, json={"collection": "document", "key": "a/b"}, headers=headers)).status_code == 422
    from datetime import datetime, timedelta, timezone
    view = await app_platform.last_view(datetime.now(timezone.utc) - timedelta(minutes=1))
    assert (view["app"], view["app_title"], view["collection"], view["key"]) == ("documents", "Documents", "document", "doc-1")
    assert await app_platform.last_view(datetime.now(timezone.utc) + timedelta(minutes=1)) is None


@needs_browser
@pytest.mark.asyncio
@pytest.mark.parametrize("slug", ["recipes", "documents"])
async def test_builtin_packages_pass_their_ui_tests_in_a_real_browser(slug):
    report = await app_platform.check_package(builtin_root() / slug)
    assert report["ok"], report
    assert len(report["tests"]) >= 3 and all(test["ok"] for test in report["tests"])
