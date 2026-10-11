from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from PIL import Image
import pytest
from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.db.tables import ResultPage
from app.jarvis_tasks.task_mcp import _call, _image_result, _tool_catalog
from app.main import app
from app.services.jarvis_tool_catalog import get_jarvis_tool_definitions
from app.services.task_capture import CaptureCollection, task_capture_service
from app.services.task_service import task_service, workflow_attempt_outcome
from app.services.task_workflows import load_workflow, validate_pinned_workflow
from app.services.worker_coordinator import worker_coordinator


WORKFLOWS = ["recipe_from_screen@1", "research_screen@1"]


def test_defined_screen_outcome_routes_before_worker_image_analysis(monkeypatch):
    from app.services.openai_live_adapter import OpenAILiveAdapter
    recipe = load_workflow("recipe_from_screen@1")
    monkeypatch.setattr(settings, "task_executor", "codex")
    monkeypatch.setattr(settings, "task_research_enabled", True)
    monkeypatch.setattr(settings, "task_workflow_allowlist", f"recipe_from_screen@1:{recipe.digest}")
    workflow = next(tool for tool in get_jarvis_tool_definitions("openai_live") if tool["name"] == "start_workflow")
    assert "ingredients" in workflow["description"]
    assert "capture_id" not in workflow["parameters"]["required"]
    assert "screen_context_mode" not in workflow["parameters"]["required"]
    prompt = OpenAILiveAdapter._router_instructions({})
    assert "worker inspects those images after admission" in prompt
    assert "admit it without reconfirmation" in prompt
    voice = OpenAILiveAdapter._session_config({})["instructions"]
    for instructions in (prompt, voice):
        assert "summary, report, plan, checklist or recipe is a defined outcome" in instructions
        assert "do not ask note versus document" in instructions
        assert "save something useful" in instructions


async def admit(monkeypatch, definition, *, image_bytes=None, goal=None, claim_task=True, password=""):
    loaded = load_workflow(definition)
    monkeypatch.setattr(settings, "task_executor", "codex")
    monkeypatch.setattr(settings, "task_research_enabled", True)
    monkeypatch.setattr(settings, "task_workflow_allowlist", f"{definition}:{loaded.digest}")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        login = await client.post("/api/auth/login", json={"password": password})
        assert login.status_code == 200, login.text
        code = (await client.post("/api/jarvis/devices/pairing-code")).json()["code"]
        pair = (await client.post("/api/jarvis/devices/pair", json={"code": code, "label": "M4 fixture"})).json()
        client.headers["Authorization"] = f"Bearer {pair['credential']}"
        await client.patch(f"/api/jarvis/devices/{pair['device_id']}", json={"lock_state": "unlocked"})
        activation, capture = str(uuid4()), str(uuid4())
        live = (await client.post("/api/jarvis/live/calls", json={
            "controller_id": pair["device_id"], "provider_adapter": "fake",
            "activation_context": {"source": "system", "activation_id": activation},
        })).json()["session"]
        now = datetime.now(timezone.utc)
        response = await client.put(f"/api/jarvis/captures/{capture}/status", json={
            "activation_id": activation, "status": "captured", "revision": 1,
            "captured_at": now.isoformat(), "expires_at": (now + timedelta(seconds=80)).isoformat(),
            "session_id": live["id"], "generation": live["generation"],
        })
        assert response.status_code == 200, response.text
        binding = await task_capture_service.preflight(device_id=pair["device_id"], session_id=live["id"],
            generation=live["generation"], activation_id=activation, capture_id=capture)
        created = await task_service.dispatch(goal=goal or "Explain this vegetarian tomato pasta with a source.",
            origin_surface="test", idempotency_key=str(uuid4()), execution_kind="workflow",
            execution_config={"definition": definition}, capture_binding=CaptureCollection((("invocation", binding),), missing_roles=("latest",)))
        request = (await client.get("/api/jarvis/context-requests/pending")).json()["requests"][0]
        image = BytesIO()
        Image.new("RGB", (32, 24), (110, 70, 35)).save(image, format="JPEG")
        data = image_bytes or image.getvalue()
        upload = await client.put(f"/api/jarvis/context-requests/{request['request_id']}/content", content=data,
            headers={"Content-Type": "image/jpeg", "X-Upload-Token": request["upload_token"],
                     "X-Content-SHA256": sha256(data).hexdigest(), "X-Capture-Id": capture,
                     "X-Activation-Id": activation, "X-Generation": str(live["generation"]),
                     "X-Captured-At": now.isoformat(), "X-Capture-Monotonic-Ms": "100"})
        assert upload.status_code == 200, upload.text
    if not claim_task:
        return SimpleNamespace(task_id=created.task.id, workflow_schema=loaded.output_schema), None
    worker = await worker_coordinator.activate(process_identity="m4-worker")
    claim = await worker_coordinator.claim_next(worker.worker_id)
    assert claim.task.task_id == created.task.id
    return SimpleNamespace(task_id=created.task.id, run_id=claim.task.execution_run_id,
        attempt_id=claim.task.attempt_id, claim_token=claim.task.claim_token,
        workspace=Path(claim.task.workspace_path), workflow_schema=loaded.output_schema), worker


async def researched(args):
    history = await _call("task_history", {}, args)
    existing = next((row for row in history["events"] if row["type"] == "agent.native.item"
                     and row["payload"].get("item", {}).get("type") == "webSearch"), None)
    if existing:
        sequence = existing["sequence"]
    else:
        # Provider protocol fixture: blocked first source, then another source.
        event = await task_service.record_event(args.task_id, event_type="agent.native.item",
            native_item_id="native-search", payload={"method": "item/completed", "item": {
                "type": "webSearch", "id": "native-search", "query": "tomato pasta",
                "action": {"type": "openPage", "url": "https://example.com/recipe"}}},
            idempotency_key="native-source", attempt_id=args.attempt_id,
            execution_run_id=args.run_id, claim_token=args.claim_token)
        sequence = event.sequence
    source = {"source_event_sequence": sequence, "url": "https://example.com/recipe", "title": "Tomato pasta"}
    return {"sources": [source]}, source


def content(args, source):
    common = {"title": "Tomato pasta", "source_event_sequences": [source["source_event_sequence"]]}
    if args.workflow_schema == "recipe.v1":
        return common | {"ingredients": ["100g pasta", "200g tomatoes"],
            "instructions": ["Boil pasta.", "Simmer tomatoes and combine."],
            "sources": [{"source_event_sequence": source["source_event_sequence"], "url": source["url"], "title": source["title"]}]}
    return common | {"summary": "A simple vegetarian dish", "body": "Boil pasta and combine with tomatoes. [Source](https://example.com/recipe).", "sources": [{"url": source["url"], "label": source["title"], "source_event_sequence": source["source_event_sequence"]}]}


@pytest.mark.asyncio
async def test_research_reference_is_directly_available_despite_transport_noise(monkeypatch):
    args, _ = await admit(monkeypatch, "research_screen@1")
    for number in range(25):
        await task_service.record_native_observation(args.task_id, event_type="agent.native.event",
            payload={"method": "item/agentMessage/delta"}, idempotency_key=f"noise:{number}",
            attempt_id=args.attempt_id, execution_run_id=args.run_id, claim_token=args.claim_token)
    _, source = await researched(args)
    history = await _call("task_history", {}, args)
    assert history["native_research"][0]["source_event_sequence"] == source["source_event_sequence"]
    assert all(event["type"] != "agent.native.event" for event in history["events"])
    with pytest.raises(ValueError, match="publish its typed result"):
        await _call("task_finish", {"status": "completed", "summary": "An answer is not publication",
            "operation_key": "bypass"}, args)
    await _call("result_publish", content(args, source) | {"operation_key": "published"}, args)


def test_registry_catalog_and_retired_schema_rejection(monkeypatch):
    monkeypatch.setattr(settings, "task_research_enabled", True)
    definitions = [load_workflow(identity) for identity in WORKFLOWS + ["text_report@1", "screen_document@1"]]
    monkeypatch.setattr(settings, "task_workflow_allowlist", ",".join(f"{item.id}@1:{item.digest}" for item in definitions))
    catalog = next(tool for tool in get_jarvis_tool_definitions() if tool["name"] == "start_workflow")
    assert set(catalog["parameters"]["properties"]["definition"]["enum"]) == set(WORKFLOWS + ["screen_document@1", "text_report@1"])
    assert "Find me a recipe" in catalog["description"] and "Explain" not in definitions[0].purpose
    assert "concise sourced explanation" in catalog["description"]
    legacy = definitions[2].pinned()
    legacy["schema"] = "workflow-pinned.v1"
    with pytest.raises(ValueError):
        validate_pinned_workflow({"workflow": legacy})
    for item in definitions[:2]:
        assert {tool["name"] for tool in _tool_catalog(item.output_schema, list(item.capabilities))} == {
            "task_history", "task_context_images", "task_question", "result_publish", "task_finish"}
        tools = _tool_catalog(item.output_schema, list(item.capabilities))
        image_tool = next(tool for tool in tools if tool["name"] == "task_context_images")
        assert image_tool["name"] in item.instructions
        assert "task_context_imagess" not in item.instructions
    monkeypatch.setattr(settings, "task_research_enabled", False)
    catalog = next(tool for tool in get_jarvis_tool_definitions() if tool["name"] == "start_workflow")
    assert set(catalog["parameters"]["properties"]["definition"]["enum"]) == {"screen_document@1", "text_report@1"}
    from app.services.task_workflows import resolve_workflow_config
    with pytest.raises(ValueError, match="research capability"):
        resolve_workflow_config({"definition": WORKFLOWS[0]})


@pytest.mark.asyncio
@pytest.mark.parametrize("definition", WORKFLOWS)
async def test_clear_partial_failure_replay_and_invalid_output(monkeypatch, definition):
    args, _ = await admit(monkeypatch, definition)
    image = await _image_result({}, args)
    assert sum(item["type"] == "image" for item in image["content"]) == 1
    response, source = await researched(args)
    # Native evidence is retained and reused without another request or local fetch.
    again, repeated = await researched(args)
    assert repeated == source
    payload = content(args, source)
    with pytest.raises(ValueError):
        await _call("result_publish", {**payload, "title": "", "operation_key": "invalid"}, args)
    with pytest.raises(ValueError, match="cited completed native"):
        await _call("result_publish", {**payload, "source_event_sequences": [], "operation_key": "ungrounded"}, args)
    with pytest.raises(ValueError, match="completed native research"):
        await _call("result_publish", {**payload, "sources": [{**payload["sources"][0],
            "source_event_sequence": 999999}], "operation_key": "foreign"}, args)
    result = await _call("result_publish", payload | {"operation_key": "publish"}, args)
    assert result["status"] == "completed"
    assert await _call("result_publish", payload | {"operation_key": "publish"}, args) == result
    async with session_scope() as session:
        page = (await session.execute(select(ResultPage).where(ResultPage.task_id == args.task_id))).scalar_one()
        assert page.result_kind == args.workflow_schema and page.revision_number == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("definition", WORKFLOWS)
async def test_ambiguity_resumes_asking_run(monkeypatch, definition):
    args, worker = await admit(monkeypatch, definition)
    question = await _call("task_question", {"question": "Which dish do you mean?", "operation_key": "clarify"}, args)
    assert await _call("task_question", {"question": "Which dish do you mean?", "operation_key": "clarify"}, args) == question
    with pytest.raises(ValueError, match="conflicts"):
        await _call("task_question", {"question": "Changed question", "operation_key": "clarify"}, args)
    detail = await task_service.get_task(args.task_id)
    assert detail.task.status == "waiting_for_input" and len(detail.runs) == 1
    with pytest.raises(ValueError):
        await _call("research_sources", {"queries": ["late"], "operation_key": "late"}, args)
    await worker_coordinator.release_claim(worker.worker_id, args.attempt_id, claim_token=args.claim_token, writers_stopped=True)
    await task_service.answer_question(args.task_id, question["question_id"], answer="The tomato pasta", idempotency_key="answer")
    assert workflow_attempt_outcome(await task_service.get_task(args.task_id), args.run_id, args.attempt_id) == "waiting_for_input"
    claim = await worker_coordinator.claim_next(worker.worker_id)
    assert claim.task.execution_run_id == args.run_id
    args.attempt_id, args.claim_token = claim.task.attempt_id, claim.task.claim_token
    _, source = await researched(args)
    assert (await _call("result_publish", content(args, source) | {"operation_key": "publish"}, args))["status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("definition", WORKFLOWS)
async def test_followup_reuses_source_image_and_same_url_then_failed_update(monkeypatch, definition):
    args, worker = await admit(monkeypatch, definition)
    _, source = await researched(args)
    payload = content(args, source)
    await _call("result_publish", payload | {"operation_key": "first"}, args)
    async with session_scope() as session:
        first = (await session.execute(select(ResultPage).where(ResultPage.task_id == args.task_id))).scalar_one()
        public_id = first.public_id
    await worker_coordinator.release_claim(worker.worker_id, args.attempt_id, claim_token=args.claim_token, writers_stopped=True)
    for ordinal in (2, 3):
        admitted = await task_service.continue_task(args.task_id, content="Clarify the wording", idempotency_key=f"follow-{ordinal}", origin="test")
        claim = await worker_coordinator.claim_next(worker.worker_id)
        args.run_id, args.attempt_id, args.claim_token = claim.task.execution_run_id, claim.task.attempt_id, claim.task.claim_token
        assert admitted.run.ordinal == ordinal
        assert sum(item["type"] == "image" for item in (await _image_result({}, args))["content"]) == 1
        history = await _call("task_history", {}, args)
        assert any(row["sequence"] == source["source_event_sequence"] for row in history["events"])
        if ordinal == 2:
            for base in (None, 0):
                with pytest.raises(ValueError, match="base revision"):
                    await _call("result_publish", {**payload, "operation_key": "stale", **({"base_revision": base} if base is not None else {})}, args)
            history = await _call("task_history", {}, args)
            assert history["maintained_result"]["revision_number"] == 1
            revised = {**payload, "title": "Clearer instructions", "operation_key": "revise", "base_revision": 1}
            await _call("result_publish", revised, args)
            await _call("result_publish", revised, args)
            with pytest.raises(ValueError, match="conflicts"):
                await _call("result_publish", revised | {"base_revision": 0}, args)
        else:
            await _call("task_finish", {"summary": "Cannot support the update", "status": "failed", "operation_key": "failed"}, args)
        await worker_coordinator.release_claim(worker.worker_id, args.attempt_id, claim_token=args.claim_token, writers_stopped=True)
    async with session_scope() as session:
        final = (await session.execute(select(ResultPage).where(ResultPage.task_id == args.task_id))).scalar_one()
        assert final.public_id == public_id and final.revision_number == 2 and final.status == "ready"
        assert final.warning == "Cannot support the update"
    for active in (False, True):
        await task_service.continue_task(args.task_id, content="Revise this result", idempotency_key=f"cancel-follow-{active}", origin="test")
        claim = await worker_coordinator.claim_next(worker.worker_id) if active else None
        controlled = await task_service.control(args.task_id, action="cancel", requester="test", idempotency_key=f"cancel-{active}")
        if active:
            assert controlled.command.state == "stop_requested"
            await task_service.confirm_control(args.task_id, command_id=controlled.command.id,
                stopped_attempt_id=claim.task.attempt_id, stop_evidence={"kind": "fixture_writer_stopped"})
            await worker_coordinator.release_claim(worker.worker_id, claim.task.attempt_id, writers_stopped=True)
        async with session_scope() as session:
            preserved = (await session.execute(select(ResultPage).where(ResultPage.task_id == args.task_id))).scalar_one()
            assert preserved.public_id == public_id and preserved.revision_number == 2 and preserved.status == "ready"
            assert preserved.warning == "Task cancelled by user."


@pytest.mark.asyncio
@pytest.mark.parametrize("definition", WORKFLOWS)
async def test_all_source_failure_and_capability_denial(monkeypatch, definition):
    args, _ = await admit(monkeypatch, definition)
    with pytest.raises(ValueError, match="not allowlisted"):
        await _call("file_write", {}, args)
    await _call("task_finish", {"summary": "No adequate source", "status": "failed", "operation_key": "no-source"}, args)
    await _call("task_finish", {"summary": "No adequate source", "status": "failed", "operation_key": "no-source"}, args)
    with pytest.raises(ValueError, match="conflicts"):
        await _call("task_finish", {"summary": "Changed failure", "status": "failed", "operation_key": "no-source"}, args)
    assert (await task_service.get_task(args.task_id)).task.status == "failed"
