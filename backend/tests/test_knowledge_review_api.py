from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.conversation_store import conversation_store


@pytest.mark.asyncio
async def test_knowledge_review_queue_and_documents_flow():
    conversation = await conversation_store.create_conversation(title="Knowledge review")
    run = await conversation_store.start_run(conversation.id, kind="assistant")
    await conversation_store.append_user_message(
        conversation.id,
        run_id=run.id,
        content="I prefer concise answers. We decided to keep SQLite for local persistence.",
    )
    await conversation_store.complete_message(
        conversation.id,
        run_id=run.id,
        role="assistant",
        content="Understood.",
        actor_kind="assistant",
        event_type="conversation.assistant.message.completed",
    )
    await conversation_store.mark_run_completed(run.id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": "dev"})

        extracted = await client.post(
            f"/api/conversations/{conversation.id}/memory-items/extract",
            json={},
        )
        assert extracted.status_code == 200
        assert len(extracted.json()) >= 2

        queue = await client.get("/api/knowledge/review-items")
        assert queue.status_code == 200
        assert len(queue.json()["items"]) >= 2

        preference_item = next(item for item in queue.json()["items"] if item["kind"] == "preference")
        approve = await client.post(f"/api/knowledge/memory-items/{preference_item['id']}/approve")
        assert approve.status_code == 200

        documents = await client.get("/api/knowledge/documents")
        assert documents.status_code == 200
        assert any(document["kind"] == "preferences" for document in documents.json()["documents"])

        decision_item = next(item for item in queue.json()["items"] if item["kind"] == "decision")
        reject = await client.post(f"/api/knowledge/memory-items/{decision_item['id']}/reject")
        assert reject.status_code == 200
        assert reject.json()["status"] == "rejected"