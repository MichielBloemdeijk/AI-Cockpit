from __future__ import annotations

import json
from pathlib import Path
import shutil

from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio
import app.services.agent_runner as agent_runner_module
import app.services.app_builder as app_builder_module
from app.models.chat import ModelResponse, ToolCall, ToolCallFunction

from app.main import app
from app.services.app_builder import bootstrap_generated_app
from app.services.agent_tools import (
    ToolExecutionContext,
    build_tool_context,
    execute_agent_tool,
    get_agent_tool_definitions,
    get_agent_tool_provider_definitions,
)
from app.services.app_registry import AppLeaseConflictError, app_registry_service, generated_app_contract, resolve_generated_app_contract
from app.services.conversation_store import conversation_store


_DURABLE_APP_SLUGS = {"app", "launchpad", "leaseable-launch-dashboard", "release-dashboard"}


@pytest_asyncio.fixture(autouse=True)
async def cleanup_generated_app_scaffolds():
    try:
        yield
    finally:
        repo_root = app_builder_module._repo_root()
        for record in await app_registry_service.list_apps():
            if record.slug in _DURABLE_APP_SLUGS:
                continue
            contract = resolve_generated_app_contract(record.slug, record.manifest_json)
            for relative_root in (contract.frontend_root, contract.asset_root):
                shutil.rmtree(repo_root / relative_root, ignore_errors=True)


@pytest.mark.asyncio
async def test_bootstrap_generated_app_uses_contract_override_roots():
    bootstrap = await bootstrap_generated_app(
        goal="create verifier app",
        title="Verifier App",
        contract_override={
            "route_path_prefix": "/apps",
            "frontend_root_base": "backend/data/verifier-runs/test-run/app/apps",
            "asset_root_base": "backend/data/verifier-runs/test-run/public/apps",
        },
    )

    assert bootstrap.route_path == "/apps/verifier-app"
    assert bootstrap.frontend_root == "backend/data/verifier-runs/test-run/app/apps/verifier-app"
    assert bootstrap.asset_root == "backend/data/verifier-runs/test-run/public/apps/verifier-app"
    assert bootstrap.allowed_write_roots == [
        "backend/data/verifier-runs/test-run/app/apps/verifier-app",
        "backend/data/verifier-runs/test-run/public/apps/verifier-app",
    ]


@pytest.mark.asyncio
async def test_resolve_generated_app_contract_prefers_manifest_contract_payload():
    record = await app_registry_service.create_app(
        title="Manifest Contract App",
        slug="manifest-contract-app",
        manifest_json={
            "contract": {
                "route_path": "/apps/manifest-contract-app",
                "frontend_root": "backend/data/verifier-runs/contract/app/apps/manifest-contract-app",
                "frontend_entry_path": "backend/data/verifier-runs/contract/app/apps/manifest-contract-app/page.tsx",
                "frontend_layout_path": "backend/data/verifier-runs/contract/app/apps/manifest-contract-app/layout.tsx",
                "manifest_path": "backend/data/verifier-runs/contract/app/apps/manifest-contract-app/cockpit-app.json",
                "asset_root": "backend/data/verifier-runs/contract/public/apps/manifest-contract-app",
                "allowed_write_roots": [
                    "backend/data/verifier-runs/contract/app/apps/manifest-contract-app",
                    "backend/data/verifier-runs/contract/public/apps/manifest-contract-app",
                ],
            }
        },
    )

    resolved = resolve_generated_app_contract(record.slug, record.manifest_json)

    assert resolved.frontend_root == "backend/data/verifier-runs/contract/app/apps/manifest-contract-app"
    assert resolved.asset_root == "backend/data/verifier-runs/contract/public/apps/manifest-contract-app"


def _native_tool_response(*, model: str, name: str, arguments: dict[str, object], content: str) -> ModelResponse:
    return ModelResponse(
        model=model,
        content=content,
        usage=None,
        tool_calls=[
            ToolCall(
                id=f"call_{name}",
                function=ToolCallFunction(name=name, arguments=json.dumps(arguments)),
            )
        ],
        finish_reason="tool_calls",
    )


def _native_plan_response(*, model: str, summary: str, steps: list[str], open_questions: list[str] | None = None, assumptions: list[str] | None = None) -> ModelResponse:
    return _native_tool_response(
        model=model,
        name="task_plan",
        arguments={
            "summary": summary,
            "steps": steps,
            "open_questions": open_questions or [],
            "assumptions": assumptions or [],
        },
        content="Plan drafted.",
    )


@pytest.mark.asyncio
async def test_generated_app_registry_crud_and_contract():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": "dev"})

        created = await client.post(
            "/api/apps",
            json={
                "title": "Hello World",
                "description": "First generated frontend app",
                "status": "draft",
            },
        )
        assert created.status_code == 201
        payload = created.json()
        assert payload["slug"] == "hello-world"
        assert payload["route_path"] == "/apps/hello-world"
        assert payload["frontend_root"] == "frontend/app/apps/hello-world"
        assert payload["allowed_write_roots"] == [
            "frontend/app/apps/hello-world",
            "frontend/public/apps/hello-world",
        ]

        listing = await client.get("/api/apps")
        assert listing.status_code == 200
        assert listing.json()[0]["slug"] == "hello-world"

        updated = await client.patch(
            f"/api/apps/{payload['id']}",
            json={"status": "verified", "verification_status": "passed"},
        )
        assert updated.status_code == 200
        assert updated.json()["status"] == "verified"
        assert updated.json()["verification_status"] == "passed"

        by_slug = await client.get("/api/apps/slug/hello-world")
        assert by_slug.status_code == 200
        assert by_slug.json()["id"] == payload["id"]


@pytest.mark.asyncio
async def test_generated_app_write_roots_are_explicit_and_enforced():
    conversation = await conversation_store.create_conversation(title="Generated app writes")
    assert conversation.workspace_path is not None
    base_context = build_tool_context(conversation.id, conversation.workspace_path)
    app_record = await app_registry_service.create_app(title="Flappy Bird")

    context = ToolExecutionContext(
        conversation_id=base_context.conversation_id,
        run_id=None,
        workspace_path=base_context.workspace_path,
        workspace_root=base_context.workspace_root,
        read_roots=base_context.read_roots,
        write_roots=[
            base_context.workspace_root,
            *[Path(root) for root in app_registry_service.get_absolute_write_roots(app_record)],
        ],
    )

    result = await execute_agent_tool(
        context=context,
        tool="file_write",
        arguments={
            "path": "frontend/app/apps/flappy-bird/app.tsx",
            "content": "export function App() { return null; }",
        },
    )
    assert result.metadata["path"] == "frontend/app/apps/flappy-bird/app.tsx"
    assert (Path(app_registry_service.get_absolute_write_roots(app_record)[0]) / "app.tsx").exists()

    with pytest.raises(ValueError):
        await execute_agent_tool(
            context=context,
            tool="file_write",
            arguments={
                "path": "frontend/page.tsx",
                "content": "blocked",
            },
        )


@pytest.mark.asyncio
async def test_bootstrap_generated_app_uses_model_generated_unique_names(monkeypatch):
    async def _fake_structured(messages, model, schema_name, json_schema, temperature=0.2, max_tokens=120, session_id=None, prompt_segments=None):
        return {"title": "Hello World"}, ModelResponse(model=model, content='{"title": "Hello World"}', usage=None)

    async def _fake_task_model():
        return "anthropic/claude-sonnet-4-5"

    monkeypatch.setattr(app_builder_module, "chat_completion_structured", _fake_structured)
    monkeypatch.setattr(app_builder_module.chat_settings_service, "get_task_agent_model", _fake_task_model)

    first = await bootstrap_generated_app(goal="create a basic app that says hello world")
    second = await bootstrap_generated_app(goal="create a basic app that says hello world")

    assert first.app.title == "Hello World"
    assert first.app.slug == "hello-world"
    assert second.app.title == "Hello World 2"
    assert second.app.slug == "hello-world-2"


def test_agent_tools_expose_provider_native_schemas():
    tools = {tool.name: tool for tool in get_agent_tool_definitions()}

    assert tools["file_write"].input_schema["required"] == ["path", "content"]
    assert tools["app_initialize"].input_schema["additionalProperties"] is False
    assert tools["app_initialize"].input_schema["properties"]["title"]["type"] == "string"

    openai_definitions = get_agent_tool_provider_definitions("openai")
    anthropic_definitions = get_agent_tool_provider_definitions("anthropic")

    file_write_definition = next(tool for tool in openai_definitions if tool["function"]["name"] == "file_write")
    app_initialize_definition = next(tool for tool in anthropic_definitions if tool["name"] == "app_initialize")

    assert file_write_definition["function"]["parameters"]["required"] == ["path", "content"]
    assert app_initialize_definition["input_schema"]["properties"]["app_slug"]["type"] == "string"


@pytest.mark.asyncio
async def test_app_initialize_tool_creates_app_from_title_only():
    conversation = await conversation_store.create_conversation(title="Create app from title")
    assert conversation.workspace_path is not None
    context = build_tool_context(conversation.id, conversation.workspace_path)

    result = await execute_agent_tool(
        context=context,
        tool="app_initialize",
        arguments={"title": "Hello World"},
    )

    assert result.tool == "app_initialize"
    assert result.metadata["app"]["title"] == "Hello World"
    assert result.metadata["app"]["slug"] == "hello-world"


@pytest.mark.asyncio
async def test_chat_agent_flow_accepts_top_level_app_initialize_fields(monkeypatch):
    native_responses = [
        {
            "tool": "task_plan",
            "arguments": {
                "summary": "Create the app.",
                "steps": ["Initialize app", "Return confirmation"],
                "open_questions": [],
                "assumptions": [],
            },
            "content": "Plan drafted.",
        },
        {
            "tool": "app_initialize",
            "arguments": {"title": "Hello World", "app_slug": "hello-world"},
            "content": "Initialize app boundary first.",
        },
        {
            "tool": "task_finalize",
            "arguments": {"summary": "Created app.", "result": "App scaffold is ready."},
            "content": "Creation complete.",
        },
    ]

    async def _fake_chat_completion(
        messages,
        model,
        temperature=0.7,
        max_tokens=4096,
        response_format=None,
        tools=None,
        tool_choice=None,
        session_id=None,
        prompt_segments=None,
        request_overrides=None,
        **kwargs,
    ):
        payload = native_responses.pop(0)
        return _native_tool_response(
            model=model,
            name=str(payload["tool"]),
            arguments=dict(payload["arguments"]),
            content=str(payload["content"]),
        )

    monkeypatch.setattr(agent_runner_module, "chat_completion", _fake_chat_completion)
    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_chat_completion)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": "dev"})

        response = await client.post(
            "/api/conversations/",
            json={"initial_message": "please create a simple hello world app"},
        )

    assert response.status_code == 201
    conversation_id = response.json()["conversation"]["id"]

    app_record = await app_registry_service.get_app_by_slug("hello-world")
    assert app_record is not None
    assert app_record.lease_conversation_id == conversation_id

    runs = await conversation_store.list_runs(conversation_id)
    run_id = runs[-1].id
    run = await conversation_store.get_run(run_id)
    assert run is not None
    metadata = dict(run.metadata_json or {})
    events = await conversation_store.list_events_for_run(conversation_id, run_id)
    called_tools = [
        (event.payload_json or {}).get("tool")
        for event in events
        if event.event_type == "agent.tool.called"
    ]
    assert called_tools == ["app_initialize"]
    assert isinstance(metadata.get("app_context"), dict)
    assert metadata["app_context"]["mode"] == "app_builder"
    assert metadata["app_context"]["app"]["slug"] == "hello-world"

    messages = await conversation_store.list_messages(conversation_id, final_only=True)
    assert messages[-1].content == "App scaffold is ready."
    assert native_responses == []


@pytest.mark.asyncio
async def test_chat_agent_native_tool_flow_uses_tool_calls_for_supported_models(monkeypatch):
    call_count = {"count": 0}

    async def _fake_chat_completion(
        messages,
        model,
        temperature=0.7,
        max_tokens=4096,
        response_format=None,
        tools=None,
        tool_choice=None,
        session_id=None,
        prompt_segments=None,
        request_overrides=None,
        **kwargs,
    ):
        call_count["count"] += 1
        assert tools is not None
        assert tool_choice == "required"
        if call_count["count"] == 1:
            return _native_plan_response(
                model=model,
                summary="Create the app.",
                steps=["Initialize app", "Return confirmation"],
            )
        if call_count["count"] == 2:
            return ModelResponse(
                model=model,
                content="Initialize the app boundary first.",
                usage=None,
                tool_calls=[
                    ToolCall(
                        id="call_app_initialize",
                        function=ToolCallFunction(
                            name="app_initialize",
                            arguments='{"title":"Hello World","app_slug":"hello-world"}',
                        ),
                    )
                ],
                finish_reason="tool_calls",
            )
        return ModelResponse(
            model=model,
            content="Creation complete.",
            usage=None,
            tool_calls=[
                ToolCall(
                    id="call_finalize",
                    function=ToolCallFunction(
                        name="task_finalize",
                        arguments='{"summary":"Created app.","result":"App scaffold is ready."}',
                    ),
                )
            ],
            finish_reason="tool_calls",
        )

    monkeypatch.setattr(agent_runner_module, "chat_completion", _fake_chat_completion)
    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_chat_completion)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": "dev"})

        response = await client.post(
            "/api/conversations/",
            json={"initial_message": "please create a simple hello world app"},
        )

    assert response.status_code == 201
    assert call_count["count"] == 3
    conversation_id = response.json()["conversation"]["id"]

    app_record = await app_registry_service.get_app_by_slug("hello-world")
    assert app_record is not None
    assert app_record.lease_conversation_id == conversation_id

    runs = await conversation_store.list_runs(conversation_id)
    run_id = runs[-1].id
    events = await conversation_store.list_events_for_run(conversation_id, run_id)
    called_tools = [
        (event.payload_json or {}).get("tool")
        for event in events
        if event.event_type == "agent.tool.called"
    ]
    raw_tool_calls = [
        event.payload_json or {}
        for event in events
        if event.event_type == "agent.response.tool_call"
    ]
    assert called_tools == ["app_initialize"]
    assert {event["tool"] for event in raw_tool_calls} == {"task_plan", "app_initialize", "task_finalize"}

    messages = await conversation_store.list_messages(conversation_id, final_only=True)
    assert messages[-1].content == "App scaffold is ready."


@pytest.mark.asyncio
async def test_chat_agent_native_tool_flow_can_pause_for_user_question(monkeypatch):
    async def _fake_chat_completion(
        messages,
        model,
        temperature=0.7,
        max_tokens=4096,
        response_format=None,
        tools=None,
        tool_choice=None,
        session_id=None,
        prompt_segments=None,
        request_overrides=None,
        **kwargs,
    ):
        assert tools is not None
        assert tool_choice == "required"
        if any(getattr(tool.get("function"), "get", lambda *_: None)("name") == "task_plan" for tool in tools if isinstance(tool, dict)):
            return _native_plan_response(
                model=model,
                summary="Clarify the app scope.",
                steps=["Ask one clarifying question"],
                open_questions=["What should the app be named?"],
            )
        return ModelResponse(
            model=model,
            content="Need clarification before creating the app.",
            usage=None,
            tool_calls=[
                ToolCall(
                    id="call_ask_user",
                    function=ToolCallFunction(
                        name="task_ask_user",
                        arguments='{"question":"What should the app be called?"}',
                    ),
                )
            ],
            finish_reason="tool_calls",
        )

    monkeypatch.setattr(agent_runner_module, "chat_completion", _fake_chat_completion)
    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_chat_completion)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": "dev"})

        response = await client.post(
            "/api/conversations/",
            json={"initial_message": "please create an app for me"},
        )

    assert response.status_code == 201
    conversation_id = response.json()["conversation"]["id"]

    messages = await conversation_store.list_messages(conversation_id, final_only=True)
    assert messages[-1].content == "What should the app be called?"

    runs = await conversation_store.list_runs(conversation_id)
    run = await conversation_store.get_run(runs[-1].id)
    assert run is not None
    assert run.status == "paused"

    events = await conversation_store.list_events_for_run(conversation_id, run.id)
    event_types = [event.event_type for event in events]
    assert "agent.response.tool_call" in event_types
    assert "agent.question.asked" in event_types
    assert "agent.tool.called" not in event_types


@pytest.mark.asyncio
async def test_chat_agent_retry_feedback_recovers_missing_app_initialize_arguments(monkeypatch):
    decision_attempts: list[list[str]] = []
    state = {"decision_calls": 0}

    async def _fake_chat_completion(
        messages,
        model,
        temperature=0.7,
        max_tokens=4096,
        response_format=None,
        tools=None,
        tool_choice=None,
        session_id=None,
        prompt_segments=None,
        request_overrides=None,
        **kwargs,
    ):
        if state["decision_calls"] == 0:
            state["decision_calls"] += 1
            return _native_plan_response(
                model=model,
                summary="Create the app.",
                steps=["Initialize app", "Return confirmation"],
            )
        state["decision_calls"] += 1
        decision_attempts.append([message.content for message in messages])
        if state["decision_calls"] == 2:
            return _native_tool_response(
                model=model,
                name="app_initialize",
                arguments={},
                content="Initialize the app.",
            )
        if state["decision_calls"] == 3:
            assert any(
                "called app_initialize without the required arguments" in message.content
                for message in messages
                if message.role == "system"
            )
            return _native_tool_response(
                model=model,
                name="app_initialize",
                arguments={"title": "Hello World"},
                content="Initialize the app.",
            )
        return _native_tool_response(
            model=model,
            name="task_finalize",
            arguments={"summary": "Created app.", "result": "App scaffold is ready."},
            content="Creation complete.",
        )

    monkeypatch.setattr(agent_runner_module, "chat_completion", _fake_chat_completion)
    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_chat_completion)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": "dev"})

        response = await client.post(
            "/api/conversations/",
            json={"initial_message": "please create a simple hello world app"},
        )

    assert response.status_code == 201
    assert state["decision_calls"] >= 4
    conversation_id = response.json()["conversation"]["id"]
    app_record = await app_registry_service.get_app_by_slug("hello-world")
    assert app_record is not None
    assert app_record.lease_conversation_id == conversation_id
    assert any(
        "called app_initialize without the required arguments" in message
        for attempt in decision_attempts[1:2]
        for message in attempt
    )


@pytest.mark.asyncio
async def test_app_list_tool_returns_registered_apps():
    conversation = await conversation_store.create_conversation(title="List apps")
    assert conversation.workspace_path is not None
    context = build_tool_context(conversation.id, conversation.workspace_path)

    await app_registry_service.create_app(title="Status Board")

    result = await execute_agent_tool(
        context=context,
        tool="app_list",
        arguments={},
    )

    assert result.tool == "app_list"
    assert result.metadata["count"] >= 1
    assert any(item["slug"] == "status-board" for item in result.metadata["apps"])


@pytest.mark.asyncio
async def test_app_leases_allow_same_conversation_handoff_and_block_active_other_conversation(monkeypatch):
    conversation = await conversation_store.create_conversation(title="Lease handoff")
    assert conversation.workspace_path is not None

    async def _fake_structured(messages, model, schema_name, json_schema, temperature=0.2, max_tokens=120, session_id=None, prompt_segments=None):
        return {"title": "Release Dashboard"}, ModelResponse(model=model, content='{"title":"Release Dashboard"}', usage=None)

    monkeypatch.setattr(app_builder_module, "chat_completion_structured", _fake_structured)

    bootstrap = await bootstrap_generated_app(
        goal="build a release dashboard",
        source_conversation_id=conversation.id,
    )
    first_run = await conversation_store.start_run(
        conversation.id,
        kind="assistant",
        metadata_json={
            "task_status": "running",
            "payload": {"app": {"app_id": bootstrap.app.id}},
        },
    )
    await app_registry_service.acquire_lease(
        app_id=bootstrap.app.id,
        conversation_id=conversation.id,
        holder_run_id=first_run.id,
    )

    second_run = await conversation_store.start_run(
        conversation.id,
        kind="assistant",
        metadata_json={
            "task_status": "running",
            "payload": {"app": {"app_id": bootstrap.app.id}},
        },
    )

    handed_off = await app_registry_service.acquire_lease(
        app_id=bootstrap.app.id,
        conversation_id=conversation.id,
        holder_run_id=second_run.id,
    )

    assert handed_off.lease_conversation_id == conversation.id
    assert handed_off.lease_task_run_id == second_run.id

    other_conversation = await conversation_store.create_conversation(title="Lease takeover")
    other_run = await conversation_store.start_run(
        other_conversation.id,
        kind="assistant",
        metadata_json={
            "task_status": "running",
            "payload": {"app": {"app_id": bootstrap.app.id}},
        },
    )

    with pytest.raises(AppLeaseConflictError):
        await app_registry_service.acquire_lease(
            app_id=bootstrap.app.id,
            conversation_id=other_conversation.id,
            holder_run_id=other_run.id,
        )

    await conversation_store.mark_run_interrupted(first_run.id)
    await conversation_store.mark_run_interrupted(second_run.id)

    leased = await app_registry_service.acquire_lease(
        app_id=bootstrap.app.id,
        conversation_id=other_conversation.id,
        holder_run_id=other_run.id,
    )

    assert leased.lease_conversation_id == other_conversation.id
    assert leased.lease_task_run_id == other_run.id


@pytest.mark.asyncio
async def test_chat_agent_flow_keeps_and_reacquires_app_leases(monkeypatch):
    native_payload_queue = [
        {
            "tool": "task_plan",
            "arguments": {"summary": "Create the app.", "steps": ["Initialize app", "Return confirmation"], "open_questions": [], "assumptions": []},
            "content": "Plan drafted.",
        },
        {
            "tool": "app_initialize",
            "arguments": {"title": "Leaseable Launch Dashboard", "app_slug": "leaseable-launch-dashboard"},
            "content": "Initialize app boundary first.",
        },
        {
            "tool": "task_finalize",
            "arguments": {"summary": "Created app.", "result": "App scaffold is ready."},
            "content": "Creation complete.",
        },
        {
            "tool": "task_plan",
            "arguments": {"summary": "Edit the app without reinitializing.", "steps": ["Write update", "Return confirmation"], "open_questions": [], "assumptions": []},
            "content": "Plan drafted.",
        },
        {
            "tool": "file_write",
            "arguments": {
                "path": "frontend/app/apps/leaseable-launch-dashboard/page.tsx",
                "content": "export default function Page() { return <main>v2</main>; }",
            },
            "content": "Write directly using retained app context.",
        },
        {
            "tool": "task_finalize",
            "arguments": {"summary": "Updated app.", "result": "Applied the requested update."},
            "content": "Edit complete.",
        },
        {
            "tool": "task_plan",
            "arguments": {"summary": "Take over the lease from another conversation.", "steps": ["Attach app", "Return confirmation"], "open_questions": [], "assumptions": []},
            "content": "Plan drafted.",
        },
        {
            "tool": "app_initialize",
            "arguments": {"app_slug": "leaseable-launch-dashboard"},
            "content": "Acquire by attaching app in this conversation.",
        },
        {
            "tool": "task_finalize",
            "arguments": {"summary": "Lease moved.", "result": "Lease now belongs to the second conversation."},
            "content": "Takeover complete.",
        },
        {
            "tool": "task_plan",
            "arguments": {"summary": "Reacquire lease for original conversation.", "steps": ["Attach app", "Return confirmation"], "open_questions": [], "assumptions": []},
            "content": "Plan drafted.",
        },
        {
            "tool": "app_initialize",
            "arguments": {"app_slug": "leaseable-launch-dashboard"},
            "content": "Reacquire before writing again.",
        },
        {
            "tool": "task_finalize",
            "arguments": {"summary": "Lease restored.", "result": "Original conversation can edit again."},
            "content": "Reacquire complete.",
        },
    ]

    async def _fake_chat_completion(
        messages,
        model,
        temperature=0.7,
        max_tokens=4096,
        response_format=None,
        tools=None,
        tool_choice=None,
        session_id=None,
        prompt_segments=None,
        request_overrides=None,
        **kwargs,
    ):
        payload = native_payload_queue.pop(0)
        return _native_tool_response(
            model=model,
            name=str(payload["tool"]),
            arguments=dict(payload["arguments"]),
            content=str(payload["content"]),
        )

    monkeypatch.setattr(agent_runner_module, "chat_completion", _fake_chat_completion)
    monkeypatch.setattr(agent_runner_module, "chat_completion_stream_response", _fake_chat_completion)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": "dev"})

        first = await client.post(
            "/api/conversations/",
            json={"initial_message": "build a leaseable launch dashboard"},
        )
        assert first.status_code == 201
        conversation_a = first.json()["conversation"]["id"]

        second = await client.post(
            f"/api/conversations/{conversation_a}/messages",
            json={"content": "make a small follow-up update", "branch_key": "main"},
        )
        assert second.status_code == 200

        app_record = await app_registry_service.get_app_by_slug("leaseable-launch-dashboard")
        assert app_record is not None
        assert app_record.lease_conversation_id == conversation_a

        runs_a = await conversation_store.list_runs(conversation_a)
        second_run_id = runs_a[-1].id
        events_a_second = await conversation_store.list_events_for_run(conversation_a, second_run_id)
        called_tools_second = [
            (event.payload_json or {}).get("tool")
            for event in events_a_second
            if event.event_type == "agent.tool.called"
        ]
        assert len(called_tools_second) >= 1

        other = await client.post(
            "/api/conversations/",
            json={"initial_message": "attach leaseable-launch-dashboard and make it yours"},
        )
        assert other.status_code == 201
        conversation_b = other.json()["conversation"]["id"]

        app_after_takeover = await app_registry_service.get_app_by_slug("leaseable-launch-dashboard")
        assert app_after_takeover is not None
        assert app_after_takeover.lease_conversation_id == conversation_b

        third = await client.post(
            f"/api/conversations/{conversation_a}/messages",
            json={"content": "take the lease back and continue", "branch_key": "main"},
        )
        assert third.status_code == 200

        app_after_reacquire = await app_registry_service.get_app_by_slug("leaseable-launch-dashboard")
        assert app_after_reacquire is not None
        assert app_after_reacquire.lease_conversation_id == conversation_a
    assert native_payload_queue == []