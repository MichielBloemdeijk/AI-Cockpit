from __future__ import annotations

import json
import shutil

from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio
import app.services.app_registry as app_registry_module

from app.main import app
from app.services.app_registry import app_registry_service, resolve_generated_app_contract


_DURABLE_APP_SLUGS = {"app", "launchpad", "leaseable-launch-dashboard", "release-dashboard"}


@pytest_asyncio.fixture(autouse=True)
async def cleanup_generated_app_scaffolds():
    repo_root = app_registry_module._repo_root()
    protected_roots = {
        child.resolve()
        for base in (
            repo_root / "frontend" / "generated-apps",
            repo_root / "frontend" / "public" / "apps",
            repo_root / "frontend" / "app" / "apps",
        )
        if base.is_dir()
        for child in base.iterdir()
        if child.is_dir()
    }
    try:
        yield
    finally:
        for record in await app_registry_service.list_apps():
            if record.slug in _DURABLE_APP_SLUGS:
                continue
            contract = resolve_generated_app_contract(record.slug, record.manifest_json)
            roots = (
                repo_root / contract.frontend_root,
                repo_root / contract.asset_root,
                repo_root / "frontend" / "app" / "apps" / record.slug,
            )
            for root in roots:
                if root.resolve() not in protected_roots:
                    shutil.rmtree(root, ignore_errors=True)


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


@pytest.mark.asyncio
async def test_generated_app_registry_crud_and_contract():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": ""})

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
        assert payload["frontend_root"] == "frontend/generated-apps/hello-world"
        assert payload["allowed_write_roots"] == [
            "frontend/generated-apps/hello-world",
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


