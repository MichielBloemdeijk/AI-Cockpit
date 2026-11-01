"""Apps sharing data through grants, schema changes decided by stored data, and system collections (step 5)."""
from __future__ import annotations

import json
from pathlib import Path
import shutil

from httpx import ASGITransport, AsyncClient
import pytest

from app import app_platform
from app.app_platform import runtime
from app.app_platform.check import check_package
from app.config import settings
from app.main import app
from tests.test_builtin_apps import needs_browser

PLANNER = Path(app_platform.__file__).parent / "samples" / "meal-planner"
NOTES = Path(app_platform.__file__).parent / "samples" / "notes"
SOUP = {"title": "Tomato soup", "ingredients": ["tomatoes"], "instructions": ["Simmer."],
        "sources": [{"url": "https://example.org/soup", "title": "Soup"}]}


def package(source: Path, name: str, edit=None) -> Path:
    target = settings.app_builds_path / name
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(source, target)
    if edit is not None:
        path = target / "cockpit-app.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        edit(manifest, target)
        path.write_text(json.dumps(manifest), encoding="utf-8")
    return target


def notes_schema(edit):
    """A Notes package whose note schema `edit` changes."""
    def change(manifest, root):
        path = root / "schemas" / "note.json"
        schema = json.loads(path.read_text(encoding="utf-8"))
        edit(manifest, schema)
        path.write_text(json.dumps(schema), encoding="utf-8")
    return change


@pytest.mark.parametrize(("grants", "problem"), [
    ({"write": ["system.tasks"]}, "read-only"),
    ({"read": ["recipes"]}, "Data grants name"),
    ({"read": ["system.secrets"]}, "Data grants name"),
    ({"read": ["meal-planner.meal"]}, "own collections"),
    ({"read": ["calendar.event"]}, "no app calendar is installed"),
    ({"read": ["recipes.dish"]}, "Recipes has no collection dish"),
])
@pytest.mark.asyncio
async def test_grants_must_name_existing_data_of_other_apps(grants, problem) -> None:
    with pytest.raises(app_platform.ManifestError, match=problem):
        await app_platform.install_package(package(PLANNER, "planner", lambda m, _root: m.update(grants={
            "read": ["recipes.recipe", *grants.get("read", [])], "write": grants.get("write", [])})))


@pytest.mark.asyncio
async def test_an_app_reads_and_writes_others_data_only_through_its_grants() -> None:
    await app_platform.put_record("recipes", "recipe", "r1", SOUP)
    await app_platform.install_package(package(PLANNER, "planner"))

    [recipe] = await app_platform.query_records("meal-planner", "recipes.recipe")
    assert (recipe["key"], recipe["value"]["title"]) == ("r1", "Tomato soup")
    assert (await app_platform.get_record("meal-planner", "recipes.recipe", "r1"))["revision"] == 1
    with pytest.raises(app_platform.RecordError, match="no write grant for recipes.recipe"):
        await app_platform.put_record("meal-planner", "recipes.recipe", "r2", SOUP)
    with pytest.raises(app_platform.RecordError, match="no read grant for documents.document"):
        await app_platform.query_records("meal-planner", "documents.document")
    with pytest.raises(app_platform.RecordError, match="no read grant"):
        await app_platform.query_records("recipes", "meal-planner.meal")

    def writer(manifest, _root):
        manifest["version"] = 2
        manifest["grants"] = {"read": ["system.tasks"], "write": ["recipes.recipe"]}
    await app_platform.install_package(package(PLANNER, "planner-v2", writer))
    with pytest.raises(app_platform.RecordError, match="recipes.recipe record is invalid"):
        await app_platform.put_record("meal-planner", "recipes.recipe", "r2", {"title": "No steps"})
    written = await app_platform.put_record("meal-planner", "recipes.recipe", "r2", {**SOUP, "title": "Pie"})
    assert written["revision"] == 1
    assert (await app_platform.get_record("recipes", "recipe", "r2"))["value"]["title"] == "Pie"
    assert await app_platform.delete_record("meal-planner", "recipes.recipe", "r2") is True
    with pytest.raises(app_platform.RecordError, match="read-only"):
        await app_platform.put_record("meal-planner", "system.tasks", "t", {})


@pytest.mark.asyncio
async def test_watchers_follow_changes_in_granted_collections() -> None:
    await app_platform.install_package(package(PLANNER, "planner"))
    await app_platform.put_record("recipes", "recipe", "r1", SOUP)
    await runtime.run_events()
    await app_platform.put_record("meal-planner", "meal", "monday", {"day": "monday", "recipe": "r1",
                                                                       "title": "Tomato soup"})
    await app_platform.put_record("recipes", "recipe", "r1", {**SOUP, "title": "Roasted tomato soup"})
    assert await runtime.run_events() == 1
    assert (await app_platform.get_record("meal-planner", "meal", "monday"))["value"]["title"] == "Roasted tomato soup"
    await app_platform.delete_record("recipes", "recipe", "r1")
    await runtime.run_events()
    assert (await app_platform.get_record("meal-planner", "meal", "monday"))["value"]["missing"] is True
    runs = (await app_platform.status("meal-planner"))["recent_runs"]
    assert [run["function"] for run in runs] == ["follow_recipe"] * 3
    assert all(run["status"] == "ok" for run in runs)


@pytest.mark.asyncio
async def test_schema_changes_are_decided_by_stored_data_and_consumers() -> None:
    await app_platform.install_package(package(NOTES, "notes"))
    await app_platform.put_record("notes", "note", "a", {"text": "milk"})

    # Retyping a field no stored record uses is fine.
    def retype_pinned(manifest, schema):
        manifest["version"] = 2
        schema["properties"]["pinned"] = {"type": "string"}
    assert (await app_platform.install_package(package(NOTES, "v2", notes_schema(retype_pinned)))).version == 2

    # A new required field that stored records lack is refused, naming an example.
    def require_due(manifest, schema):
        manifest["version"] = 3
        schema["properties"]["due"] = {"type": "string"}
        schema["required"] = ["text", "due"]
    with pytest.raises(app_platform.ManifestError, match=r"1 of 1 stored note records don't fit .*'a'"):
        await app_platform.install_package(package(NOTES, "v3", notes_schema(require_due)))
    report = await check_package(package(NOTES, "v3", notes_schema(require_due)))
    assert any(problem.startswith("Incompatible with stored data:") and "note.due became required" in problem
               for problem in report["problems"])

    # Dropping a collection needs it to be empty.
    def replace_collection(manifest, _schema):
        manifest["version"] = 3
        manifest["collections"] = {"memo": {"schema": "schemas/note.json"}}
    with pytest.raises(app_platform.ManifestError, match="note was removed but still holds 1 records"):
        await app_platform.install_package(package(NOTES, "v3b", notes_schema(replace_collection)))
    await app_platform.reset("notes")
    assert (await app_platform.install_package(package(NOTES, "v3c", notes_schema(replace_collection)))).version == 3

    # Once another app reads a collection, it may only gain optional fields.
    def reads_memos(manifest, _root):
        manifest["slug"], manifest["title"] = "memo-reader", "Memo reader"
        manifest["grants"] = {"read": ["notes.memo"]}
        manifest["collections"] = {}
        manifest.pop("functions")
        (_root / "tests" / "functions.json").unlink()
    await app_platform.install_package(package(PLANNER, "reader", reads_memos))
    def retype_text(manifest, schema):
        manifest["version"] = 4
        manifest["collections"] = {"memo": {"schema": "schemas/note.json"}}
        schema["properties"]["text"] = {"type": "integer"}
    with pytest.raises(app_platform.ManifestError, match="Memo reader uses notes.memo"):
        await app_platform.install_package(package(NOTES, "v4", notes_schema(retype_text)))
    status = await app_platform.status("notes")
    assert status["used_by"] == {"memo": ["Memo reader"]}


@pytest.mark.asyncio
async def test_system_collections_expose_cockpits_own_data_read_only() -> None:
    from app.services.conversation_store import conversation_store
    from app.services.task_service import task_service

    conversation = await conversation_store.create_conversation(title="Dinner ideas")
    created = await task_service.dispatch(goal="Find a soup recipe", origin_surface="test",
                                          idempotency_key="system-collections")
    def system_reader(manifest, _root):
        manifest["grants"] = {"read": ["recipes.recipe", "system.tasks", "system.results", "system.conversations"]}
    await app_platform.install_package(package(PLANNER, "planner", system_reader))

    tasks = await app_platform.query_records("meal-planner", "system.tasks", where={"status": created.task.status})
    assert [(task["key"], task["value"]["goal"]) for task in tasks] == [(created.task.id, "Find a soup recipe")]
    task = await app_platform.get_record("meal-planner", "system.tasks", created.task.id)
    assert task["value"]["origin"] == "test" and task["value"]["finished_at"] is None
    assert await app_platform.query_records("meal-planner", "system.results") == []
    found = await app_platform.get_record("meal-planner", "system.conversations", conversation.id)
    assert found["value"]["title"] == "Dinner ideas" and found["value"]["messages"] == []
    listed = await app_platform.query_records("meal-planner", "system.conversations", order="-created_at", limit=5)
    assert conversation.id in [item["key"] for item in listed]
    assert "messages" not in listed[0]["value"]
    assert await app_platform.get_record("meal-planner", "system.tasks", "missing") is None


@pytest.mark.asyncio
async def test_http_access_and_the_builders_data_catalog() -> None:
    await app_platform.put_record("recipes", "recipe", "r1", SOUP)
    await app_platform.install_package(package(PLANNER, "planner"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as owner:
        await owner.post("/api/auth/login", json={"password": ""})
        token = (await owner.post("/api/app-platform/apps/meal-planner/session")).json()["token"]
    headers = {"Authorization": f"AppPlatform {token}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers=headers) as scoped:
        listed = await scoped.get("/api/app-platform/data/meal-planner/recipes.recipe")
        assert [record["key"] for record in listed.json()["records"]] == ["r1"]
        refused = await scoped.put("/api/app-platform/data/meal-planner/recipes.recipe/r2", json={"value": SOUP})
        assert refused.status_code == 422 and "no write grant" in refused.json()["detail"]
        # The token still reaches only its own app's routes.
        assert (await scoped.get("/api/app-platform/data/recipes/recipe")).status_code == 403

    catalog = await app_platform.data_catalog()
    recipes = next(item for item in catalog["apps"] if item["slug"] == "recipes")
    entry = recipes["collections"]["recipes.recipe"]
    assert entry["records"] == 1 and entry["example"]["title"] == "Tomato soup"
    assert entry["used_by"] == ["Meal planner"] and entry["schema"]["required"][0] == "title"
    assert set(catalog["system"]) == {"system.tasks", "system.results", "system.conversations"}


@needs_browser
@pytest.mark.asyncio
async def test_the_meal_planner_passes_its_tests_against_the_recipes_schema(tmp_path: Path) -> None:
    report = await check_package(package(PLANNER, "planner"), screenshots=tmp_path / "shots")
    assert report["ok"], report["problems"]

    def bad_seed_and_write(manifest, root):
        tests_path = root / "tests" / "ui.json"
        tests = json.loads(tests_path.read_text(encoding="utf-8"))
        tests["tests"][0]["records"]["recipes.recipe"][0]["value"].pop("sources")
        tests_path.write_text(json.dumps(tests), encoding="utf-8")
        functions = root / "functions" / "follow_recipe.py"
        functions.write_text(functions.read_text(encoding="utf-8").replace(
            "    updated = 0\n", "    updated = 0\n    cockpit.data.delete('recipes.recipe', input['key'])\n"))
    broken = await check_package(package(PLANNER, "broken", bad_seed_and_write))
    assert any("seed 'r1' is invalid" in problem for problem in broken["problems"])
    failed = [test for test in broken["tests"] if not test["ok"]]
    assert any("read-only for this app" in (test.get("error") or "") for test in failed)
