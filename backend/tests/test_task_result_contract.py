"""Generic run/result association, independent of provider and Android."""
from types import SimpleNamespace
import asyncio

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import func, select

from app.db.session import session_scope
from app.db.tables import ResultPage, ResultPageRevision, NotificationOutbox, TaskArtifact, TaskOperation, TaskEvent
from app.main import app
from app.services.task_context import portable_context
from app.services.task_service import task_service
from app.services.worker_coordinator import worker_coordinator
from app.jarvis_tasks.task_mcp import _call, _tool_catalog


def test_generic_voice_catalog_advertises_only_its_publication_contract():
    from app.services.jarvis_tool_catalog import get_jarvis_tool_definitions
    for tool in get_jarvis_tool_definitions():
        if tool["name"] in {"dispatch_task", "continue_task"}:
            kind = tool["parameters"]["properties"]["result"]["properties"]["kind"]
            assert kind["anyOf"] == [{"type": "string", "enum": ["document.v1"]}, {"type": "null"}]
        if tool["name"] == "continue_task":
            properties = tool["parameters"]["properties"]
            assert properties["screen_context_mode"]["default"] is None
            assert {"type": "null"} in properties["screen_context_mode"]["anyOf"]
            assert "null" in properties["capture_id"]["type"]


def test_document_adapter_never_fabricates_a_completion_body():
    from app.services.codex_task_runtime import _completion_text
    assert _completion_text(["Body"], require_document=True) == "Body"
    with pytest.raises(RuntimeError, match="without Document content"):
        _completion_text([], require_document=True)
    assert _completion_text([]) == "Codex completed the requested work."


async def claim(worker, task_id):
    claimed = await worker_coordinator.claim_next(worker.worker_id)
    assert claimed.task.task_id == task_id
    return dict(attempt_id=claimed.task.attempt_id, execution_run_id=claimed.task.execution_run_id,
        claim_token=claimed.task.claim_token)


async def finish(worker, task_id, fence, text="Body", key="publish"):
    await task_service.complete(task_id, summary="Document title", final_text=text, idempotency_key=key, **fence)
    await worker_coordinator.release_claim(worker.worker_id, fence["attempt_id"],
        claim_token=fence["claim_token"], writers_stopped=True)


async def page_for(task_id, public_id=None):
    async with session_scope() as session:
        query = select(ResultPage).where(ResultPage.task_id == task_id)
        if public_id:
            query = query.where(ResultPage.public_id == public_id)
        return (await session.execute(query)).scalar_one()


@pytest.mark.asyncio
async def test_general_admission_publish_update_answer_additional_result_and_replay():
    worker = await worker_coordinator.activate(process_identity="generic-result-test")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        await http.post("/api/auth/login", json={"password": ""})
        request = {"goal": "Write a report", "idempotency_key": "general-result", "result": {"mode": "new"}}
        admission = await http.post("/api/tasks", json=request)
        assert admission.status_code == 201, admission.text
        task_id, url = admission.json()["id"], admission.json()["result_url"]
        assert url and (await http.post("/api/tasks", json=request)).json()["result_url"] == url
        first = await page_for(task_id)
        fence = await claim(worker, task_id)
        await finish(worker, task_id, fence)
        await task_service.complete(task_id, summary="Document title", final_text="Body", idempotency_key="publish", **fence)
        with pytest.raises(ValueError, match="conflicts"):
            await task_service.complete(task_id, summary="Document title", final_text="Changed", idempotency_key="publish", **fence)
        snapshot = (await http.get(f"/api/results/{first.public_id}")).json()
        assert snapshot["revision_number"] == 1 and snapshot["content"]["body"] == "Body"
        async with session_scope() as session:
            deliveries = (await session.execute(select(NotificationOutbox).where(NotificationOutbox.task_id == task_id))).scalars().all()
            assert len(deliveries) == 1 and deliveries[0].deep_link == url
        answer = {"mode": "answer", "public_id": first.public_id}
        await task_service.continue_task(task_id, content="Explain the report", origin="test", idempotency_key="answer", result_request=answer)
        answer_fence = await claim(worker, task_id)
        await finish(worker, task_id, answer_fence, "Answer only", "answer-done")
        assert (await http.get(f"/api/results/{first.public_id}")).json() == snapshot
        from app.services.task_presentations import task_presentation_service
        await task_presentation_service.sync_candidates()
        async with session_scope() as session:
            assert (await session.execute(select(NotificationOutbox).where(NotificationOutbox.task_id == task_id,
                NotificationOutbox.deep_link == f"/tasks/{task_id}"))).scalars().all()
        update = {"mode": "update", "public_id": first.public_id, "expected_revision": 1}
        await task_service.continue_task(task_id, content="Edit the report", origin="test", idempotency_key="edit", result_request=update)
        edited = await page_for(task_id)
        assert edited.status == "updating" and edited.current_revision_id is not None and edited.revision_number == 1
        edit_fence = await claim(worker, task_id)
        await finish(worker, task_id, edit_fence, "Revision two", "edit-done")
        # Exact replay remains accepted after the head changed.
        replay = await task_service.continue_task(task_id, content="Edit the report", origin="test", idempotency_key="edit", result_request=update)
        assert replay.run.id == edit_fence["execution_run_id"]
        with pytest.raises(ValueError, match="stale"):
            await task_service.continue_task(task_id, content="Stale edit", origin="test", idempotency_key="stale", result_request=update)
        await task_service.continue_task(task_id, content="Add a second report", origin="test", idempotency_key="second", result_request={"mode": "new"})
        second_fence = await claim(worker, task_id)
        await finish(worker, task_id, second_fence, "Second body", "second-done")
        links = (await http.get(f"/api/tasks/{task_id}")).json()["results"]
        assert len(links) == 2 and {link["revision_number"] for link in links} == {1, 2}
        assert first.public_id in {link["public_id"] for link in links}
        assert (await http.post("/api/tasks", json=request)).json()["result_url"] == url
        async with session_scope() as session:
            assert (await session.execute(select(func.count()).select_from(ResultPageRevision))).scalar_one() == 3


@pytest.mark.asyncio
async def test_pending_new_result_gets_immediate_identity_and_does_not_rebind_old_result():
    worker = await worker_coordinator.activate(process_identity="queued-result-test")
    task = await task_service.dispatch(goal="First", origin_surface="test", idempotency_key="first", result_request={"mode": "new"})
    first = await page_for(task.task.id)
    first_fence = await claim(worker, task.task.id)
    pending = await task_service.continue_task(task.task.id, content="Second", origin="test", idempotency_key="second", result_request={"mode": "new"})
    assert pending.run.status == "pending"
    async with session_scope() as session:
        pages = (await session.execute(select(ResultPage).where(ResultPage.task_id == task.task.id))).scalars().all()
        assert len(pages) == 2
        second = next(page for page in pages if page.initial_execution_run_id == pending.run.id)
        assert next(page for page in pages if page.id == first.id).active_execution_run_id == first_fence["execution_run_id"]
        second_id = second.public_id
    await finish(worker, task.task.id, first_fence)
    second_fence = await claim(worker, task.task.id)
    context = await portable_context(task.task.id, run_id=second_fence["execution_run_id"],
        attempt_id=second_fence["attempt_id"], claim_token=second_fence["claim_token"])
    import json
    assert json.loads(context)["maintained_result"]["public_id"] == second_id
    await finish(worker, task.task.id, second_fence, "Second", "second-publish")


@pytest.mark.asyncio
async def test_workflow_answer_cannot_publish_and_does_not_mutate_existing_page(monkeypatch):
    from test_m4_workflows import admit, content, researched
    args, worker = await admit(monkeypatch, "research_screen@1")
    _, source = await researched(args)
    await _call("result_publish", content(args, source) | {"operation_key": "first"}, args)
    await worker_coordinator.release_claim(worker.worker_id, args.attempt_id, claim_token=args.claim_token, writers_stopped=True)
    page = await page_for(args.task_id)
    original_state = page.state_revision
    await task_service.continue_task(args.task_id, content="A question only", origin="test", idempotency_key="question",
        result_request={"mode": "answer", "public_id": page.public_id})
    fence = await claim(worker, args.task_id)
    answer_args = SimpleNamespace(task_id=args.task_id, run_id=fence["execution_run_id"], attempt_id=fence["attempt_id"],
        claim_token=fence["claim_token"], workspace=args.workspace, workflow_schema=args.workflow_schema)
    with pytest.raises(ValueError, match="allowlisted"):
        await _call("result_publish", content(args, source) | {"operation_key": "forbidden"}, answer_args)
    await _call("task_finish", {"summary": "Answer", "status": "completed", "operation_key": "answer"}, answer_args)
    after = await page_for(args.task_id)
    assert after.state_revision == original_state and after.revision_number == 1
    assert "result_publish" not in {tool["name"] for tool in _tool_catalog(args.workflow_schema,
        ["task.context.read@1", "task.result.publish@1", "task.finish@1"], answer_only=True)}
    await worker_coordinator.release_claim(worker.worker_id, fence["attempt_id"], claim_token=fence["claim_token"], writers_stopped=True)
    await task_service.continue_task(args.task_id, content="An explicit edit", origin="test", idempotency_key="edit",
        result_request={"mode": "update", "public_id": page.public_id, "expected_revision": 1})
    edit_fence = await claim(worker, args.task_id)
    edit_args = SimpleNamespace(**{**vars(answer_args), "run_id": edit_fence["execution_run_id"],
        "attempt_id": edit_fence["attempt_id"], "claim_token": edit_fence["claim_token"]})
    with pytest.raises(ValueError, match="typed result"):
        await _call("task_finish", {"summary": "Pretend edited", "status": "completed", "operation_key": "skip-edit"}, edit_args)
    assert (await page_for(args.task_id)).revision_number == 1


@pytest.mark.asyncio
async def test_queued_edit_never_rebases_and_failed_update_retains_last_good_content():
    worker = await worker_coordinator.activate(process_identity="stale-edit-test")
    created = await task_service.dispatch(goal="First", origin_surface="test", idempotency_key="first",
        result_request={"mode": "new"})
    task_id = created.task.id
    await finish(worker, task_id, await claim(worker, task_id))
    page = await page_for(task_id)
    update = {"mode": "update", "public_id": page.public_id, "expected_revision": 1}
    await task_service.continue_task(task_id, content="First edit", origin="test", idempotency_key="edit1", result_request=update)
    first_edit = await claim(worker, task_id)
    queued = await task_service.continue_task(task_id, content="Queued edit", origin="test", idempotency_key="edit2", result_request=update)
    assert queued.run.status == "pending"
    await finish(worker, task_id, first_edit, "Accepted revision", "edit1-publish")
    second_edit = await claim(worker, task_id)
    with pytest.raises(ValueError, match="stale"):
        await task_service.complete(task_id, summary="Stale edit", final_text="Overwrite", idempotency_key="edit2-publish", **second_edit)
    async with session_scope() as session:
        assert (await session.execute(select(func.count()).select_from(ResultPageRevision))).scalar_one() == 2
    await task_service.complete(task_id, summary="Cannot update the stale base", status="failed", idempotency_key="edit2-failed", **second_edit)
    await worker_coordinator.release_claim(worker.worker_id, second_edit["attempt_id"], claim_token=second_edit["claim_token"], writers_stopped=True)
    after = await page_for(task_id)
    assert after.revision_number == 2 and after.current_revision_id is not None and after.status == "ready" and after.warning
    async with session_scope() as session:
        revision = await session.get(ResultPageRevision, after.current_revision_id)
        assert revision.content_json["body"] == "Accepted revision"


@pytest.mark.asyncio
async def test_result_authority_owned_by_admission_and_fenced_at_publication():
    worker = await worker_coordinator.activate(process_identity="result-authority-test")
    first = await task_service.dispatch(goal="First", origin_surface="test", idempotency_key="first", result_request={"mode": "new"})
    first_page = await page_for(first.task.id)
    with pytest.raises(ValueError, match="fence"):
        await task_service.complete(first.task.id, summary="Unclaimed", final_text="Body", idempotency_key="unclaimed")
    first_fence = await claim(worker, first.task.id)
    await finish(worker, first.task.id, first_fence)
    other = await task_service.dispatch(goal="Other", origin_surface="test", idempotency_key="other")
    with pytest.raises(ValueError, match="belong"):
        await task_service.continue_task(other.task.id, content="Foreign", origin="test", idempotency_key="foreign",
            result_request={"mode": "update", "public_id": first_page.public_id, "expected_revision": 1})
    with pytest.raises(ValueError, match="cannot declare"):
        await task_service.continue_task(first.task.id, content="Bad answer", origin="test", idempotency_key="bad-answer",
            result_request={"mode": "answer", "expected_revision": 1})
    with pytest.raises(ValueError, match="registered|Unsupported|Unknown|supported"):
        await task_service.dispatch(goal="Unknown kind", origin_surface="test", idempotency_key="unknown-kind",
            result_request={"mode": "new", "kind": "worker-chosen.v1"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        await http.post("/api/auth/login", json={"password": ""})
        response = await http.post("/api/tasks", json={"goal": "Invalid", "idempotency_key": "invalid-capability",
            "result": {"mode": "new", "capabilities": ["shell"]}})
        assert response.status_code == 422
        response = await http.post(f"/api/tasks/{first.task.id}/turns", json={"content": "Invalid base", "idempotency_key": "invalid-base", "result": {
            "mode": "update", "public_id": first_page.public_id, "expected_revision": True}})
        assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["critical_only", "silent"])
async def test_publication_delivery_failure_rolls_back_every_write_and_silent_stays_silent(monkeypatch, policy):
    from app.services import task_notifications
    worker = await worker_coordinator.activate(process_identity="atomic-result-test")
    task = await task_service.dispatch(goal="Document", origin_surface="test", idempotency_key="atomic",
        presentation_policy=policy, result_request={"mode": "new"})
    fence = await claim(worker, task.task.id)
    original = task_notifications.enqueue_task_notification
    async def broken(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("Injected outbox failure")
    monkeypatch.setattr(task_notifications, "enqueue_task_notification", broken)
    if policy == "critical_only":
        with pytest.raises(RuntimeError, match="outbox failure"):
            await task_service.complete(task.task.id, summary="Title", final_text="Body", idempotency_key="publish", **fence)
        async with session_scope() as session:
            for model in (ResultPageRevision, TaskArtifact, TaskOperation, NotificationOutbox):
                assert (await session.execute(select(func.count()).select_from(model))).scalar_one() == 0
            assert (await session.execute(select(TaskEvent).where(TaskEvent.event_type == "task.completed"))).scalars().all() == []
        page = await page_for(task.task.id)
        assert page.revision_number == 0 and page.status == "building"
        assert (await task_service.get_task(task.task.id)).task.status == "running"
    monkeypatch.setattr(task_notifications, "enqueue_task_notification", original)
    await finish(worker, task.task.id, fence)
    async with session_scope() as session:
        deliveries = (await session.execute(select(NotificationOutbox))).scalars().all()
        assert len(deliveries) == (0 if policy == "silent" else 1)


@pytest.mark.asyncio
async def test_workflow_outcome_and_replay_survive_promotion_to_a_general_result(monkeypatch):
    from app.services.task_service import workflow_attempt_outcome
    from app.services.task_result_contract import admission_result_url
    from test_m4_workflows import admit, researched, content
    args, worker = await admit(monkeypatch, "research_screen@1")
    first = await page_for(args.task_id)
    detail = await task_service.get_task(args.task_id)
    initial_identity = detail.turns[0].request_identity
    pending = await task_service.continue_task(args.task_id, content="A separate Document", origin="test", idempotency_key="general-followup",
        execution_kind="agent", result_request={"mode": "new"})
    assert pending.run.status == "pending"
    _, source = await researched(args)
    payload = content(args, source) | {"operation_key": "first"}
    await _call("result_publish", payload, args)
    detail = await task_service.get_task(args.task_id)
    assert detail.task.execution_kind == "agent"
    assert workflow_attempt_outcome(detail, args.run_id, args.attempt_id) == "completed"
    await _call("result_publish", payload, args)  # read-only publication replay
    async with session_scope() as session:
        assert await admission_result_url(session, args.task_id, initial_identity) == f"/results/{first.public_id}"
    await worker_coordinator.release_claim(worker.worker_id, args.attempt_id, claim_token=args.claim_token, writers_stopped=True)
    await finish(worker, args.task_id, await claim(worker, args.task_id), "New Document", "general-publish")


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_error", [False, True])
async def test_general_committed_outcome_wins_over_heartbeat_during_runtime_cleanup(monkeypatch, cleanup_error):
    from app.config import settings
    from app.services.agent_execution import AgentExecutionResult
    from app.services.task_worker import TaskWorker
    completed, cleanup = asyncio.Event(), asyncio.Event()
    class Coordinator:
        def __getattr__(self, name):
            return getattr(worker_coordinator, name)
        async def renew_claim(self, worker_id, attempt_id, **kwargs):
            # Wrap only the worker heartbeat. Admission/reacquisition retain
            # their real coordinator-bound renewal before execution starts.
            await completed.wait()
            renewed = await worker_coordinator.renew_claim(worker_id, attempt_id, **kwargs)
            assert not renewed
            cleanup.set()
            return renewed
    monkeypatch.setattr(settings, "worker_heartbeat_interval_seconds", 0.01)
    class Runtime:
        async def execute(self, context, sink, **kwargs):
            await sink.complete("Result", "Body")
            completed.set()
            await cleanup.wait()
            if cleanup_error:
                raise RuntimeError("Cleanup transport failure")
            return AgentExecutionResult(status="completed", summary="Result")
    task = await task_service.dispatch(goal="Document", origin_surface="test", idempotency_key="heartbeat-race",
        result_request={"mode": "new"})
    worker = TaskWorker(runtime=Runtime(), coordinator=Coordinator())
    assert await worker.activate(process_identity="heartbeat-race")
    outcome = await asyncio.wait_for(worker.run_once(), 10)
    assert outcome.status == "completed" and completed.is_set() and cleanup.is_set()
    assert (await task_service.get_task(task.task.id)).task.status == "completed"
    assert (await page_for(task.task.id)).revision_number == 1
