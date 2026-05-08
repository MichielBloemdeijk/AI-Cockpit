from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.conversation_store import conversation_store


@pytest.mark.asyncio
async def test_conversation_tools_persist_trace_and_transcript():
    conversation = await conversation_store.create_conversation(title="Tool session")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": "dev"})

        search = await client.post(
            f"/api/conversations/{conversation.id}/tools",
            json={"tool": "workspace_search", "query": "Knowledge review"},
        )
        assert search.status_code == 200
        assert "workspace" in search.json()["message"]["content"].lower()

        python_run = await client.post(
            f"/api/conversations/{conversation.id}/tools",
            json={"tool": "python_execution", "code": "print('phase2 tool')"},
        )
        assert python_run.status_code == 200
        assert "phase2 tool" in python_run.json()["message"]["content"]

        events = await client.get(f"/api/conversations/{conversation.id}/events")
        assert events.status_code == 200
        event_types = {event["event_type"] for event in events.json()["events"]}
        assert "tool.workspace_search.started" in event_types
        assert "tool.workspace_search.completed" in event_types
        assert "tool.python_execution.started" in event_types
        assert "tool.python_execution.completed" in event_types


@pytest.mark.asyncio
async def test_python_tool_writes_inside_workspace_and_blocks_outside():
    conversation = await conversation_store.create_conversation(title="Tool safety")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": "dev"})

        allowed = await client.post(
            f"/api/conversations/{conversation.id}/tools",
            json={
                "tool": "python_execution",
                "code": "from pathlib import Path\nPath('allowed.txt').write_text('ok')\nprint(Path('allowed.txt').read_text())",
            },
        )
        assert allowed.status_code == 200
        assert "ok" in allowed.json()["message"]["content"]

        detail = await client.get(f"/api/conversations/{conversation.id}")
        assert detail.status_code == 200
        assert detail.json()["workspace"]["path"].endswith(conversation.id)
        assert any(file["path"] == "allowed.txt" for file in detail.json()["workspace"]["files"])

        blocked = await client.post(
            f"/api/conversations/{conversation.id}/tools",
            json={
                "tool": "python_execution",
                "code": "from pathlib import Path\nPath('../blocked.txt').write_text('nope')",
            },
        )
        assert blocked.status_code == 400
        assert "outside the allowed write roots" in blocked.json()["detail"]

