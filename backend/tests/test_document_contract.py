"""Document extensions stay additive and expose only explicitly retained artifacts."""
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import func, select

from app.db.session import session_scope
from app.db.tables import ResultPage, ResultPageRevision, TaskArtifact
from app.jarvis_tasks.task_mcp import _call
from app.main import app
from app.services.task_result_types import result_type
from app.services.task_service import task_service
from test_m4_workflows import admit, content, researched


def test_document_optional_fields_preserve_legacy_publication_fingerprints():
    legacy = {"title": "Legacy", "summary": "", "body": "Body", "source_event_sequences": []}
    assert result_type("document.v1").validate(legacy) == legacy
    minimal = {key: value for key, value in legacy.items() if key != "summary"}
    assert result_type("document.v1").validate(minimal) == minimal
    assert result_type("document.v1").validate(minimal | {"sources": [{"url": "https://example.com", "label": "Reference"}]})["sources"] == [{"url": "https://example.com", "label": "Reference"}]
    for url in ["javascript:alert(1)", "data:text/html,x", "https://user:pass@example.com", " https://example.com", "https://exam\nple.com"]:
        with pytest.raises(ValueError):
            result_type("document.v1").validate(minimal | {"sources": [{"url": url, "label": "Bad"}]})


@pytest.mark.asyncio
async def test_document_attachments_validate_before_completion_and_download_current_only(monkeypatch):
    args, _ = await admit(monkeypatch, "research_screen@1")
    _, source = await researched(args)
    foreign = await task_service.dispatch(goal="Another task", origin_surface="test", idempotency_key=str(uuid4()))
    async with session_scope() as session:
        artifacts = []
        for task_id, kind, mime, text in [(args.task_id, "document_attachment", "text/plain", "Useful attachment"),
                (args.task_id, "document_attachment", "application/json", None),
                (foreign.task.id, "document_attachment", "text/plain", "Foreign secret"),
                (args.task_id, "context", "text/plain", "Private context"),
                (args.task_id, "document_attachment", "text/plain", None),
                (args.task_id, "document_attachment", "text/html", "<script>unsafe</script>")]:
            artifact = TaskArtifact(id=str(uuid4()), task_id=task_id, artifact_type=kind, mime_type=mime,
                title="Fixture", content_text=text, uri="file:///private/never-read")
            artifacts.append(artifact)
            session.add(artifact)
        artifacts[1].content_json = {"retained": True}
        ids = [item.id for item in artifacts]
        page = (await session.execute(select(ResultPage).where(ResultPage.task_id == args.task_id))).scalar_one()
        public_id = page.public_id
    payload = content(args, source) | {"operation_key": "document-extension",
        "sources": [{"url": source["url"], "label": "Provider source", "source_event_sequence": source["source_event_sequence"]},
                    {"url": "https://example.com/reference", "label": "Provider reference", "source_event_sequence": source["source_event_sequence"]}]}
    for invalid in ids[2:] + [str(uuid4())]:
        with pytest.raises(ValueError, match="owned, retained"):
            await _call("result_publish", payload | {"attachments": [{"artifact_id": invalid, "label": "Bad"}]}, args)
        assert (await task_service.get_task(args.task_id)).task.status == "running"
    async with session_scope() as session:
        assert (await session.execute(select(func.count()).select_from(ResultPageRevision))).scalar_one() == 0
        assert (await session.execute(select(func.count()).select_from(TaskArtifact).where(TaskArtifact.artifact_type == "workflow_result"))).scalar_one() == 0
    payload["attachments"] = [{"artifact_id": identity, "label": "Download"} for identity in ids[:2]]
    await _call("result_publish", payload, args)
    await _call("result_publish", payload, args)  # replay fingerprints include extensions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        prefix = f"/api/results/{public_id}/attachments/"
        assert (await http.get(prefix + ids[0])).status_code == 401
        await http.post("/api/auth/login", json={"password": ""})
        downloaded = await http.get(prefix + ids[0])
        assert downloaded.text == "Useful attachment" and downloaded.status_code == 200
        assert downloaded.headers["content-disposition"] == 'attachment; filename="attachment.txt"'
        assert downloaded.headers["x-content-type-options"] == "nosniff"
        assert downloaded.headers["cache-control"] == "private, no-store"
        assert (await http.get(prefix + ids[1])).json() == {"retained": True}
        for invalid in ids[2:] + [str(uuid4())]:
            assert (await http.get(prefix + invalid)).status_code == 404
        # Replacing the head cannot expose old-only attachments through the API.
        async with session_scope() as session:
            page = (await session.execute(select(ResultPage).where(ResultPage.public_id == public_id))).scalar_one()
            replacement = ResultPageRevision(id=str(uuid4()), result_page_id=page.id, revision_number=2,
                execution_run_id=args.run_id, artifact_id=ids[0], schema_id="document.v1",
                provenance_json={}, content_json={"title": "Replacement", "body": "New body", "source_event_sequences": []})
            session.add(replacement)
            await session.flush()
            page.current_revision_id = replacement.id
        assert (await http.get(prefix + ids[0])).status_code == 404
