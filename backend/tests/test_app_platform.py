from __future__ import annotations

import json
from pathlib import Path
import shutil

from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio

from app import app_platform
from app.app_platform.manifest import compatibility_problems
from app.config import settings
from app.main import app


@pytest_asyncio.fixture(autouse=True)
async def without_builtin_apps():
    """These tests start from a platform with no apps; conftest installs Documents and Recipes."""
    for slug in ("documents", "recipes"):
        await app_platform.uninstall(slug)


SAMPLE = Path(app_platform.__file__).parent / "samples" / "notes"


def build(name: str = "notes", *, version: int | None = None, edit=None) -> Path:
    """Copy the sample into the builds folder, optionally changing its manifest or schema."""
    target = settings.app_builds_path / name
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(SAMPLE, target)
    manifest_path = target / "cockpit-app.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if version is not None:
        manifest["version"] = version
    schema_path = target / "schemas" / "note.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if edit is not None:
        edit(manifest, schema)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    return target


@pytest.mark.asyncio
async def test_install_activates_and_lists_the_package() -> None:
    installed = await app_platform.install_package(build())

    assert installed.version == 1
    assert (settings.apps_path / "notes" / "1" / "ui" / "index.html").is_file()
    apps = [app.summary() for app in await app_platform.list_apps()]
    assert apps == [{
        "slug": "notes", "title": "Notes", "version": 1,
        "description": "Short pinned notes. The reference package for the app platform.",
        "collections": ["note"],
        "jarvis_summary": "Short notes the owner keeps; each has a text and can be pinned.",
        "url": "/apps/notes",
    }]


@pytest.mark.asyncio
async def test_records_are_validated_revisioned_and_queryable() -> None:
    await app_platform.install_package(build())

    first = await app_platform.put_record("notes", "note", "a", {"text": "milk", "pinned": True}, expected_revision=0)
    assert first["revision"] == 1
    with pytest.raises(app_platform.RevisionConflict):
        await app_platform.put_record("notes", "note", "a", {"text": "again"}, expected_revision=0)
    second = await app_platform.put_record("notes", "note", "a", {"text": "oat milk", "pinned": True},
                                           expected_revision=1)
    assert second["revision"] == 2
    await app_platform.put_record("notes", "note", "b", {"text": "bread", "pinned": False})

    with pytest.raises(app_platform.RecordError, match="text"):
        await app_platform.put_record("notes", "note", "c", {"text": ""})
    with pytest.raises(app_platform.RecordError, match="Additional properties"):
        await app_platform.put_record("notes", "note", "c", {"text": "x", "colour": "red"})
    with pytest.raises(app_platform.RecordError, match="no collection"):
        await app_platform.put_record("notes", "unknown", "c", {"text": "x"})

    pinned = await app_platform.query_records("notes", "note", where={"pinned": True})
    assert [record["key"] for record in pinned] == ["a"]
    ordered = await app_platform.query_records("notes", "note", order="text")
    assert [record["value"]["text"] for record in ordered] == ["bread", "oat milk"]
    assert await app_platform.delete_record("notes", "note", "b") is True
    assert await app_platform.get_record("notes", "note", "b") is None


@pytest.mark.asyncio
async def test_new_versions_must_be_newer_and_compatible_and_can_roll_back() -> None:
    await app_platform.install_package(build())
    await app_platform.put_record("notes", "note", "a", {"text": "keep me"})

    with pytest.raises(app_platform.ManifestError, match="not newer"):
        await app_platform.install_package(build("again", edit=lambda manifest, _schema: manifest.update(title="Again")))

    def retype(_manifest, schema):
        schema["properties"]["pinned"] = {"type": "string"}
    with pytest.raises(app_platform.ManifestError, match="changed type"):
        await app_platform.install_package(build("retyped", version=2, edit=retype))
    assert not (settings.apps_path / "notes" / "2").exists()

    def add_optional(manifest, schema):
        manifest["title"] = "Notes 2"
        schema["properties"]["colour"] = {"type": "string"}
    upgraded = await app_platform.install_package(build("v2", version=2, edit=add_optional))
    assert upgraded.version == 2
    await app_platform.put_record("notes", "note", "b", {"text": "red", "colour": "red"})

    rolled_back = await app_platform.rollback("notes")
    assert (rolled_back.version, rolled_back.title) == (1, "Notes")
    assert (await app_platform.get_record("notes", "note", "a"))["value"] == {"text": "keep me"}
    with pytest.raises(app_platform.ManifestError, match="no earlier version"):
        await app_platform.rollback("notes")


def test_compatibility_rules() -> None:
    old = {"note": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}
    assert compatibility_problems(old, old) == []
    assert compatibility_problems(old, {}) == ["collection note was removed"]
    stricter = {"note": {**old["note"], "properties": {"text": {"type": "string"}, "due": {"type": "string"}},
                         "required": ["text", "due"]}}
    assert compatibility_problems(old, stricter) == ["note.due became required"]


@pytest.mark.asyncio
async def test_manifest_rejects_reserved_sections_and_escaping_paths() -> None:
    def functions(manifest, _schema):
        manifest["functions"] = {"tick": {"file": "functions/tick.py"}}
    with pytest.raises(app_platform.ManifestError, match="Not supported .*functions"):
        await app_platform.install_package(build(edit=functions))

    def escape(manifest, _schema):
        manifest["ui"]["entry"] = "../outside.html"
    with pytest.raises(app_platform.ManifestError, match="escapes"):
        await app_platform.install_package(build(edit=escape))


@pytest.mark.asyncio
async def test_http_install_host_and_token_scoped_data() -> None:
    build()
    build("other", edit=lambda manifest, _schema: manifest.update(slug="other", title="Other"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        assert (await client.post("/api/app-platform/apps/install", json={"source": "notes"})).status_code == 401
        await client.post("/api/auth/login", json={"password": ""})
        assert (await client.post("/api/app-platform/apps/install", json={"source": "../.."})).status_code == 404
        assert (await client.post("/api/app-platform/apps/install", json={"source": "notes"})).status_code == 200
        assert (await client.post("/api/app-platform/apps/install", json={"source": "other"})).status_code == 200

        session = (await client.post("/api/app-platform/apps/notes/session")).json()
        assert session["frame_url"] == "/api/app-host/notes/1/index.html"
        page = await client.get(session["frame_url"])
        assert page.status_code == 200 and "cockpit.data" in page.text
        assert "sandbox allow-scripts" in page.headers["content-security-policy"]
        assert "connect-src 'none'" in page.headers["content-security-policy"]
        # Encoded so the client does not normalize it away: the handler must refuse to leave ui/.
        assert (await client.get("/api/app-host/notes/1/%2e%2e/cockpit-app.json")).status_code == 404
        assert (await client.get("/api/app-host/_sdk/cockpit-app.js")).status_code == 200

    headers = {"Authorization": f"AppPlatform {session['token']}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers=headers) as scoped:
        stored = await scoped.put("/api/app-platform/data/notes/note/n1", json={"value": {"text": "hi"},
                                                                                 "expected_revision": 0})
        assert stored.status_code == 200 and stored.json()["revision"] == 1
        conflict = await scoped.put("/api/app-platform/data/notes/note/n1", json={"value": {"text": "x"},
                                                                                   "expected_revision": 0})
        assert conflict.status_code == 409
        assert (await scoped.put("/api/app-platform/data/notes/note/n2", json={"value": {}})).status_code == 422
        listed = await scoped.get("/api/app-platform/data/notes/note", params={"where": json.dumps({"text": "hi"})})
        assert [record["key"] for record in listed.json()["records"]] == ["n1"]
        assert (await scoped.get("/api/app-platform/data/other/note")).status_code == 403
        # The token reaches app data only, never the rest of Cockpit.
        assert (await scoped.get("/api/tasks")).status_code == 401
    forged = {"Authorization": "AppPlatform forged"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers=forged) as bad:
        assert (await bad.get("/api/app-platform/data/notes/note")).status_code == 401


async def _app_tool(tool_name: str, arguments: dict, **session) -> dict:
    from app.services.jarvis_brain import restricted_brain_gateway

    result = await restricted_brain_gateway.execute(
        tool_name=tool_name, arguments=arguments, call_id=f"call-{tool_name}",
        conversation_id="conversation-apps", origin_turn_id=None, **session)
    return result.output


@pytest.mark.asyncio
async def test_jarvis_lists_reads_and_opens_installed_apps() -> None:
    from contextlib import aclosing
    from tests.test_phone_display import _open_requests, _paired_live_phone

    await app_platform.install_package(build())
    await app_platform.put_record("notes", "note", "a", {"text": "call the plumber", "pinned": True})
    await app_platform.put_record("notes", "note", "b", {"text": "buy stamps", "pinned": False})

    listed = await _app_tool("list_apps", {})
    assert [app["slug"] for app in listed["apps"]] == ["notes"]
    assert listed["apps"][0]["collections"] == ["note"]

    pinned = await _app_tool("query_app_data", {"app": "Notes", "where": {"pinned": True}})
    assert pinned["records"] == [{"key": "a", "value": {"text": "call the plumber", "pinned": True}}]
    missing = await _app_tool("query_app_data", {"app": "calendar"})
    assert missing["found"] is False
    bad = await _app_tool("query_app_data", {"app": "notes", "collection": "todo"})
    assert "no collection" in bad["error"] and bad["collections"] == ["note"]

    # Without a phone session Jarvis gets the URL to offer instead.
    assert (await _app_tool("open_app", {"app": "notes"}))["url"] == "/apps/notes"
    device, _, live_session = await _paired_live_phone(ASGITransport(app=app))
    async with aclosing(device):
        opened = await _app_tool("open_app", {"app": "notes"}, session_id=live_session["id"],
                                 generation=live_session["generation"])
        assert opened["opened"] is True and opened["delivered_as"] == "screen"
        [event] = await _open_requests(device)
        assert event["payload"]["path"] == "/apps/notes"
