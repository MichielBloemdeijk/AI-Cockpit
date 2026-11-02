from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.brain_retrieval import search_wiki
from app.services.conversation_store import conversation_store
from app.services.knowledge_memory import list_knowledge_documents
from app.services.memory_review_store import memory_review_store


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
        await client.post("/api/auth/login", json={"password": ""})

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

async def _memory_item(conversation_id: str, *, title: str, kind: str = "decision"):
    return await memory_review_store.create_memory_item(
        scope=kind, kind=kind, title=title, content=f"{title} content.",
        source_conversation_id=conversation_id, confidence=0.9,
    )


@pytest.mark.asyncio
async def test_withdrawing_an_approved_memory_unpublishes_it_on_every_route():
    conversation = await conversation_store.create_conversation(title="Withdrawal")
    rejected = await _memory_item(conversation.id, title="Use the green lane")
    deleted = await _memory_item(conversation.id, title="Use the amber lane")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": ""})
        for item in (rejected, deleted):
            approved = await client.post(f"/api/knowledge/memory-items/{item.id}/approve")
            assert approved.status_code == 200
            again = await client.post(f"/api/knowledge/memory-items/{item.id}/approve")
            assert again.json()["knowledge_path"] == approved.json()["knowledge_path"]
        assert {result["provenance"]["memory_item_id"] for result in search_wiki("lane")} == {rejected.id, deleted.id}

        assert (await client.post(f"/api/knowledge/memory-items/{rejected.id}/reject")).json()["status"] == "rejected"
        assert (await client.delete(f"/api/knowledge/memory-items/{deleted.id}")).json()["status"] == "deleted"

        assert search_wiki("lane") == []
        assert not any("lane" in document["content"] for document in list_knowledge_documents())
        assert (await client.post(f"/api/knowledge/memory-items/{deleted.id}/approve")).status_code == 409
        assert (await client.post("/api/knowledge/memory-items/missing/reject")).status_code == 404


@pytest.mark.asyncio
async def test_same_title_memories_publish_separate_notes():
    conversation = await conversation_store.create_conversation(title="Collisions")
    first = await _memory_item(conversation.id, title="Deployment lane")
    second = await _memory_item(conversation.id, title="Deployment lane")
    _, first_path = await memory_review_store.approve(first.id)
    _, second_path = await memory_review_store.approve(second.id)
    assert first_path != second_path
    await memory_review_store.delete(first.id)
    assert not first_path.exists() and second_path.exists()
    assert [document["title"] for document in list_knowledge_documents()] == ["Deployment lane"]


@pytest.mark.asyncio
async def test_failed_approval_commit_removes_the_fresh_publication(monkeypatch):
    conversation = await conversation_store.create_conversation(title="Approval failure")
    item = await _memory_item(conversation.id, title="Unsaved decision")

    async def failing_commit(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(conversation_store, "approve_memory_item", failing_commit)
    with pytest.raises(RuntimeError, match="database unavailable"):
        await memory_review_store.approve(item.id)
    assert list_knowledge_documents() == []
    assert (await memory_review_store.get_memory_item(item.id)).status == "proposed"
