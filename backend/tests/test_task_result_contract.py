"""Generic run/result association, independent of provider and Android.

A result is a record in an installed app (here the built-in Documents app); the
task keeps a link to it.
"""
from types import SimpleNamespace
import asyncio
import json

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import func, select

from app import app_platform
from app.db.session import session_scope
from app.db.tables import NotificationOutbox, TaskArtifact, TaskOperation, TaskEvent, TaskResult
from app.main import app
from app.services.tasks.context import portable_context
from app.services.tasks.service import task_service
from app.services.tasks.worker_coordinator import worker_coordinator
from app.jarvis_tasks.task_mcp import _call, _tool_catalog


def test_generic_voice_catalog_advertises_only_its_publication_contract():
    from app.services.jarvis.tool_catalog import get_jarvis_tool_definitions
    for tool in get_jarvis_tool_definitions():
        if tool["name"] in {"dispatch_task", "continue_task"}:
            # The kind is not the model's choice: agents publish Documents, workflows their own output.
            assert set(tool["parameters"]["properties"]["result"]["properties"]) == {"mode", "result_id", "expected_revision"}
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


async def results_for(task_id) -> list[TaskResult]:
    async with session_scope() as session:
        return list((await session.execute(select(TaskResult).where(TaskResult.task_id == task_id)
            .order_by(TaskResult.created_at, TaskResult.id))).scalars())


async def result_for(task_id) -> TaskResult:
    results = await results_for(task_id)
    assert len(results) == 1, results
    return results[0]


async def record(result: TaskResult) -> dict | None:
    return await app_platform.get_record(result.app_slug, result.collection, result.record_key)


@pytest.mark.asyncio
async def test_general_admission_publish_update_answer_additional_result_and_replay():
    worker = await worker_coordinator.activate(process_identity="generic-result-test")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        await http.post("/api/auth/login", json={"password": ""})
        request = {"goal": "Write a report", "idempotency_key": "general-result", "result": {"mode": "new"}}
        admission = await http.post("/api/tasks", json=request)
        assert admission.status_code == 201, admission.text
        # A new result has no page until it is published.
        task_id = admission.json()["id"]
        assert admission.json()["result_url"] is None
        fence = await claim(worker, task_id)
        await finish(worker, task_id, fence)
        await task_service.complete(task_id, summary="Document title", final_text="Body", idempotency_key="publish", **fence)
        with pytest.raises(ValueError, match="conflicts"):
            await task_service.complete(task_id, summary="Document title", final_text="Changed", idempotency_key="publish", **fence)
        first = await result_for(task_id)
        assert (first.app_slug, first.collection, first.record_key) == ("documents", "document", fence["execution_run_id"])
        stored = await record(first)
        assert stored["revision"] == 1 and stored["value"] == {"title": "Document title", "body": "Body"}
        url = f"/apps/documents?collection=document&key={first.record_key}"
        assert (await http.post("/api/tasks", json=request)).json()["result_url"] == url
        async with session_scope() as session:
            deliveries = (await session.execute(select(NotificationOutbox).where(NotificationOutbox.task_id == task_id))).scalars().all()
            assert len(deliveries) == 1 and deliveries[0].deep_link == url
        answer = {"mode": "answer", "result_id": first.id}
        await task_service.continue_task(task_id, content="Explain the report", origin="test", idempotency_key="answer", result_request=answer)
        answer_fence = await claim(worker, task_id)
        await finish(worker, task_id, answer_fence, "Answer only", "answer-done")
        assert await record(first) == stored
        from app.services.updates.presentations import task_presentation_service
        await task_presentation_service.sync_candidates()
        async with session_scope() as session:
            assert (await session.execute(select(NotificationOutbox).where(NotificationOutbox.task_id == task_id,
                NotificationOutbox.deep_link == f"/tasks/{task_id}"))).scalars().all()
        update = {"mode": "update", "result_id": first.id, "expected_revision": 1}
        edit = await task_service.continue_task(task_id, content="Edit the report", origin="test", idempotency_key="edit", result_request=update)
        assert edit.run.configuration_json["result"]["kind"] == "collection:documents.document"
        edit_fence = await claim(worker, task_id)
        await finish(worker, task_id, edit_fence, "Revision two", "edit-done")
        assert (await record(first))["value"]["body"] == "Revision two" and (await record(first))["revision"] == 2
        # Exact replay remains accepted after the record changed.
        replay = await task_service.continue_task(task_id, content="Edit the report", origin="test", idempotency_key="edit", result_request=update)
        assert replay.run.id == edit_fence["execution_run_id"]
        with pytest.raises(ValueError, match="stale"):
            await task_service.continue_task(task_id, content="Stale edit", origin="test", idempotency_key="stale", result_request=update)
        await task_service.continue_task(task_id, content="Add a second report", origin="test", idempotency_key="second", result_request={"mode": "new"})
        second_fence = await claim(worker, task_id)
        await finish(worker, task_id, second_fence, "Second body", "second-done")
        links = (await http.get(f"/api/tasks/{task_id}")).json()["results"]
        assert len(links) == 2 and first.id in {link["result_id"] for link in links}
        assert {link["url"] for link in links} >= {url}
        assert (await http.post("/api/tasks", json=request)).json()["result_url"] == url


@pytest.mark.asyncio
async def test_queued_new_result_does_not_rebind_the_old_result():
    worker = await worker_coordinator.activate(process_identity="queued-result-test")
    task = await task_service.dispatch(goal="First", origin_surface="test", idempotency_key="first", result_request={"mode": "new"})
    first_fence = await claim(worker, task.task.id)
    pending = await task_service.continue_task(task.task.id, content="Second", origin="test", idempotency_key="second", result_request={"mode": "new"})
    assert pending.run.status == "pending"
    await finish(worker, task.task.id, first_fence)
    second_fence = await claim(worker, task.task.id)
    context = await portable_context(task.task.id, run_id=second_fence["execution_run_id"],
        attempt_id=second_fence["attempt_id"], claim_token=second_fence["claim_token"])
    assert json.loads(context)["maintained_result"] is None
    await finish(worker, task.task.id, second_fence, "Second", "second-publish")
    by_key = {result.record_key: result for result in await results_for(task.task.id)}
    first, second = by_key[first_fence["execution_run_id"]], by_key[second_fence["execution_run_id"]]
    assert (await record(first))["value"]["body"] == "Body" and (await record(second))["value"]["body"] == "Second"


@pytest.mark.asyncio
async def test_workflow_answer_cannot_publish_and_does_not_mutate_existing_record(monkeypatch):
    from test_reference_workflows import admit, content, researched
    args, worker = await admit(monkeypatch, "research_screen@1")
    _, source = await researched(args)
    await _call("result_publish", content(args, source) | {"operation_key": "first"}, args)
    await worker_coordinator.release_claim(worker.worker_id, args.attempt_id, claim_token=args.claim_token, writers_stopped=True)
    result = await result_for(args.task_id)
    original = await record(result)
    await task_service.continue_task(args.task_id, content="A question only", origin="test", idempotency_key="question",
        result_request={"mode": "answer", "result_id": result.id})
    fence = await claim(worker, args.task_id)
    answer_args = SimpleNamespace(task_id=args.task_id, run_id=fence["execution_run_id"], attempt_id=fence["attempt_id"],
        claim_token=fence["claim_token"], workspace=args.workspace, workflow_schema=args.workflow_schema)
    with pytest.raises(ValueError, match="allowlisted"):
        await _call("result_publish", content(args, source) | {"operation_key": "forbidden"}, answer_args)
    await _call("task_finish", {"summary": "Answer", "status": "completed", "operation_key": "answer"}, answer_args)
    assert await record(result) == original
    assert "result_publish" not in {tool["name"] for tool in await _tool_catalog(args.workflow_schema,
        ["task.context.read@1", "task.result.publish@1", "task.finish@1"], answer_only=True)}
    await worker_coordinator.release_claim(worker.worker_id, fence["attempt_id"], claim_token=fence["claim_token"], writers_stopped=True)
    await task_service.continue_task(args.task_id, content="An explicit edit", origin="test", idempotency_key="edit",
        result_request={"mode": "update", "result_id": result.id, "expected_revision": 1})
    edit_fence = await claim(worker, args.task_id)
    edit_args = SimpleNamespace(**{**vars(answer_args), "run_id": edit_fence["execution_run_id"],
        "attempt_id": edit_fence["attempt_id"], "claim_token": edit_fence["claim_token"]})
    with pytest.raises(ValueError, match="typed result"):
        await _call("task_finish", {"summary": "Pretend edited", "status": "completed", "operation_key": "skip-edit"}, edit_args)
    assert (await record(result))["revision"] == 1


@pytest.mark.asyncio
async def test_queued_edit_never_rebases_and_failed_update_retains_last_good_content():
    worker = await worker_coordinator.activate(process_identity="stale-edit-test")
    created = await task_service.dispatch(goal="First", origin_surface="test", idempotency_key="first",
        result_request={"mode": "new"})
    task_id = created.task.id
    await finish(worker, task_id, await claim(worker, task_id))
    result = await result_for(task_id)
    update = {"mode": "update", "result_id": result.id, "expected_revision": 1}
    await task_service.continue_task(task_id, content="First edit", origin="test", idempotency_key="edit1", result_request=update)
    first_edit = await claim(worker, task_id)
    queued = await task_service.continue_task(task_id, content="Queued edit", origin="test", idempotency_key="edit2", result_request=update)
    assert queued.run.status == "pending"
    await finish(worker, task_id, first_edit, "Accepted revision", "edit1-publish")
    second_edit = await claim(worker, task_id)
    with pytest.raises(ValueError, match="changed since it was read"):
        await task_service.complete(task_id, summary="Stale edit", final_text="Overwrite", idempotency_key="edit2-publish", **second_edit)
    await task_service.complete(task_id, summary="Cannot update the stale base", status="failed", idempotency_key="edit2-failed", **second_edit)
    await worker_coordinator.release_claim(worker.worker_id, second_edit["attempt_id"], claim_token=second_edit["claim_token"], writers_stopped=True)
    stored = await record(result)
    assert stored["revision"] == 2 and stored["value"]["body"] == "Accepted revision"


@pytest.mark.asyncio
async def test_an_edit_in_the_app_fences_a_queued_update_and_a_deleted_record_is_not_revised():
    worker = await worker_coordinator.activate(process_identity="app-edit-test")
    created = await task_service.dispatch(goal="Note", origin_surface="test", idempotency_key="note", result_request={"mode": "new"})
    task_id = created.task.id
    await finish(worker, task_id, await claim(worker, task_id), "Original")
    result = await result_for(task_id)
    await task_service.continue_task(task_id, content="Shorter", origin="test", idempotency_key="shorter")
    fence = await claim(worker, task_id)
    # The owner edits the record in the app while Jarvis works on it.
    await app_platform.put_record("documents", "document", result.record_key, {"title": "Mine", "body": "Owner text"},
                                  expected_revision=1)
    with pytest.raises(ValueError, match="changed since it was read"):
        await task_service.complete(task_id, summary="Shorter", final_text="Short", idempotency_key="shorter-done", **fence)
    await task_service.complete(task_id, summary="Changed meanwhile", status="failed", idempotency_key="shorter-failed", **fence)
    await worker_coordinator.release_claim(worker.worker_id, fence["attempt_id"], claim_token=fence["claim_token"], writers_stopped=True)
    assert (await record(result))["value"]["body"] == "Owner text"
    await app_platform.delete_record("documents", "document", result.record_key)
    follow_up = await task_service.continue_task(task_id, content="Another thought", origin="test", idempotency_key="after-delete")
    assert "result" not in follow_up.run.configuration_json
    with pytest.raises(ValueError, match="deleted"):
        await task_service.continue_task(task_id, content="Edit it", origin="test", idempotency_key="edit-deleted",
            result_request={"mode": "update"})


@pytest.mark.asyncio
async def test_result_authority_owned_by_admission_and_fenced_at_publication():
    worker = await worker_coordinator.activate(process_identity="result-authority-test")
    first = await task_service.dispatch(goal="First", origin_surface="test", idempotency_key="first", result_request={"mode": "new"})
    with pytest.raises(ValueError, match="fence"):
        await task_service.complete(first.task.id, summary="Unclaimed", final_text="Body", idempotency_key="unclaimed")
    first_fence = await claim(worker, first.task.id)
    await finish(worker, first.task.id, first_fence)
    first_result = await result_for(first.task.id)
    other = await task_service.dispatch(goal="Other", origin_surface="test", idempotency_key="other")
    with pytest.raises(ValueError, match="belong"):
        await task_service.continue_task(other.task.id, content="Foreign", origin="test", idempotency_key="foreign",
            result_request={"mode": "update", "result_id": first_result.id, "expected_revision": 1})
    with pytest.raises(ValueError, match="cannot declare"):
        await task_service.continue_task(first.task.id, content="Bad answer", origin="test", idempotency_key="bad-answer",
            result_request={"mode": "answer", "expected_revision": 1})
    with pytest.raises(ValueError, match="Invalid result request"):
        await task_service.dispatch(goal="Unknown kind", origin_surface="test", idempotency_key="unknown-kind",
            result_request={"mode": "new", "kind": "worker-chosen.v1"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        await http.post("/api/auth/login", json={"password": ""})
        response = await http.post("/api/tasks", json={"goal": "Invalid", "idempotency_key": "invalid-capability",
            "result": {"mode": "new", "capabilities": ["shell"]}})
        assert response.status_code == 422
        response = await http.post(f"/api/tasks/{first.task.id}/turns", json={"content": "Invalid base", "idempotency_key": "invalid-base", "result": {
            "mode": "update", "result_id": first_result.id, "expected_revision": True}})
        assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["critical_only", "silent"])
async def test_publication_delivery_failure_rolls_back_task_writes_and_silent_stays_silent(monkeypatch, policy):
    from app.services.updates import notifications as task_notifications
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
            await task_service.complete(task.task.id, summary="Document title", final_text="Body", idempotency_key="publish", **fence)
        async with session_scope() as session:
            for model in (TaskResult, TaskArtifact, TaskOperation, NotificationOutbox):
                assert (await session.execute(select(func.count()).select_from(model))).scalar_one() == 0
            assert (await session.execute(select(TaskEvent).where(TaskEvent.event_type == "task.completed"))).scalars().all() == []
        assert (await task_service.get_task(task.task.id)).task.status == "running"
        # The record is written before the task commits; the retry links the identical record.
        assert (await app_platform.get_record("documents", "document", fence["execution_run_id"]))["revision"] == 1
    monkeypatch.setattr(task_notifications, "enqueue_task_notification", original)
    await finish(worker, task.task.id, fence)
    assert (await record(await result_for(task.task.id)))["revision"] == 1
    async with session_scope() as session:
        deliveries = (await session.execute(select(NotificationOutbox))).scalars().all()
        assert len(deliveries) == (0 if policy == "silent" else 1)


@pytest.mark.asyncio
async def test_workflow_outcome_and_replay_survive_promotion_to_a_general_result(monkeypatch):
    from app.services.tasks.service import workflow_attempt_outcome
    from app.services.results.contract import admission_result_url
    from test_reference_workflows import admit, researched, content
    args, worker = await admit(monkeypatch, "research_screen@1")
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
    first = await result_for(args.task_id)
    async with session_scope() as session:
        assert await admission_result_url(session, args.task_id, initial_identity) == \
            f"/apps/documents?collection=document&key={first.record_key}"
    await worker_coordinator.release_claim(worker.worker_id, args.attempt_id, claim_token=args.claim_token, writers_stopped=True)
    await finish(worker, args.task_id, await claim(worker, args.task_id), "New Document", "general-publish")
    assert len(await results_for(args.task_id)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_error", [False, True])
async def test_general_committed_outcome_wins_over_heartbeat_during_runtime_cleanup(monkeypatch, cleanup_error):
    from app.config import settings
    from app.services.agent_execution import AgentExecutionResult
    from app.services.tasks.worker import TaskWorker
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
    assert (await record(await result_for(task.task.id)))["revision"] == 1


@pytest.mark.asyncio
async def test_plain_follow_up_revises_the_result_in_place_with_its_current_content():
    from app.services.jarvis.daily import jarvis_daily_service
    from app.services.jarvis.situation import jarvis_situation_projector
    worker = await worker_coordinator.activate(process_identity="implicit-edit-test")
    created = await task_service.dispatch(goal="Write a pudding recipe", origin_surface="test",
        idempotency_key="pudding", result_request={"mode": "new"})
    task_id = created.task.id
    await finish(worker, task_id, await claim(worker, task_id), "Use 2 tbsp butter.", "first")
    result = await result_for(task_id)
    # Opening the record in its app tells Jarvis which result "it" is.
    await app_platform.record_view("documents", "document", result.record_key)
    accepted = await jarvis_daily_service.accept_text_turn("Edit it", idempotency_key="implicit-edit-log")
    situation = (await jarvis_situation_projector.project(conversation_id=accepted.conversation.id)).payload
    opened = situation["last_opened_result"]
    assert opened["task_id"] == task_id and opened["app"] == "documents" and opened["title"] == "Document title"
    listed = next(item for item in situation["recent_terminal_tasks"] if item["task_id"] == task_id)
    assert listed["results"] == [{"title": "Document title", "app": "documents", "collection": "document"}]

    # Jarvis names no result intent: the follow-up still edits the same record.
    edit = await task_service.continue_task(task_id, content="Use grams instead of tablespoons",
        origin="test", idempotency_key="metric")
    assert edit.run.configuration_json["result"] == {"mode": "update", "result_id": result.id,
        "expected_revision": 1, "kind": "collection:documents.document"}
    replay = await task_service.continue_task(task_id, content="Use grams instead of tablespoons",
        origin="test", idempotency_key="metric")
    assert replay.run.id == edit.run.id
    fence = await claim(worker, task_id)
    context = json.loads(await portable_context(task_id, run_id=fence["execution_run_id"],
        attempt_id=fence["attempt_id"], claim_token=fence["claim_token"]))
    assert context["maintained_result"]["current_content"]["body"] == "Use 2 tbsp butter."
    assert context["maintained_result"]["revision"] == 1
    history = await _call("task_history", {}, SimpleNamespace(task_id=task_id, run_id=fence["execution_run_id"],
        attempt_id=fence["attempt_id"], claim_token=fence["claim_token"]))
    assert history["maintained_result"]["current_content"]["body"] == "Use 2 tbsp butter."
    await finish(worker, task_id, fence, "Use 28 g butter.", "metric-done")
    stored = await record(result)
    assert stored["revision"] == 2 and stored["value"]["body"] == "Use 28 g butter."
    assert (await result_for(task_id)).record_key == result.record_key

    # An update may also name the result without IDs; questions stay answer-only.
    named = await task_service.continue_task(task_id, content="Add a serving scaler", origin="test",
        idempotency_key="scaler", result_request={"mode": "update"})
    assert named.run.configuration_json["result"]["expected_revision"] == 2
    await finish(worker, task_id, await claim(worker, task_id), "Scalable.", "scaler-done")
    question = await task_service.continue_task(task_id, content="How much butter?", origin="test",
        idempotency_key="question", result_request={"mode": "answer"})
    assert question.run.configuration_json["result"] == {"mode": "answer"}
    await finish(worker, task_id, await claim(worker, task_id), "28 g.", "question-done")
    assert (await record(result))["revision"] == 3

    await task_service.continue_task(task_id, content="Also a sauce recipe", origin="test",
        idempotency_key="sauce", result_request={"mode": "new"})
    await finish(worker, task_id, await claim(worker, task_id), "Sauce.", "sauce-done")
    with pytest.raises(ValueError, match="multiple results"):
        await task_service.continue_task(task_id, content="Change it", origin="test", idempotency_key="ambiguous")
    with pytest.raises(ValueError, match="multiple results"):
        await task_service.continue_task(task_id, content="Change it", origin="test", idempotency_key="ambiguous-update",
            result_request={"mode": "update"})


@pytest.mark.asyncio
async def test_follow_up_without_a_result_stays_answer_only():
    worker = await worker_coordinator.activate(process_identity="no-page-follow-up")
    created = await task_service.dispatch(goal="Check the weather", origin_surface="test", idempotency_key="weather")
    await finish(worker, created.task.id, await claim(worker, created.task.id), "Sunny", "weather-done")
    follow_up = await task_service.continue_task(created.task.id, content="And tomorrow?", origin="test",
        idempotency_key="tomorrow")
    assert "result" not in follow_up.run.configuration_json
    with pytest.raises(ValueError, match="no result to update"):
        await task_service.continue_task(created.task.id, content="Update the page", origin="test",
            idempotency_key="no-page", result_request={"mode": "update"})


@pytest.mark.asyncio
async def test_a_document_needs_the_documents_app():
    await app_platform.uninstall("documents")
    with pytest.raises(ValueError, match="documents app is not installed"):
        worker = await worker_coordinator.activate(process_identity="no-documents")
        created = await task_service.dispatch(goal="Write", origin_surface="test", idempotency_key="no-app",
            result_request={"mode": "new"})
        await task_service.complete(created.task.id, summary="Title", final_text="Body", idempotency_key="publish",
            **await claim(worker, created.task.id))


@pytest.mark.asyncio
async def test_a_markdown_heading_summary_becomes_a_plain_title():
    worker = await worker_coordinator.activate(process_identity="heading-title")
    created = await task_service.dispatch(goal="Write a checklist", origin_surface="test", idempotency_key="heading",
        result_request={"mode": "new"})
    fence = await claim(worker, created.task.id)
    await task_service.complete(created.task.id, summary="## Packing checklist", final_text="## Packing checklist\n\n- Socks",
        idempotency_key="publish", **fence)
    result = await result_for(created.task.id)
    assert result.title == "Packing checklist" and (await record(result))["value"]["title"] == "Packing checklist"
