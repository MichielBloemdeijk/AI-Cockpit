from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from httpx import ASGITransport, AsyncClient
import pytest
import yaml

from app.config import settings
from app.jarvis_tasks.task_mcp import _call, _tool_catalog
from app.main import app
from app.services.agent_execution import AgentExecutionResult
from app.services.task_service import task_service
from app.services.task_worker import TaskWorker
from app.services.task_workflows import (
    _UniqueSafeLoader, _safe_definition_file, load_workflow, validate_workflow_result,
)
from app.services.worker_coordinator import worker_coordinator


def test_workflow_loader_rejects_duplicate_keys_and_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        yaml.load("id: first\nid: second", Loader=_UniqueSafeLoader)
    with pytest.raises(ValueError, match="inside"):
        _safe_definition_file(tmp_path, "../outside.yaml")
    definition = load_workflow("text_report@1")
    assert definition.output_schema == "text-report.v1"
    screen = load_workflow("screen_document@1")
    assert screen.output_schema == "document.v1"
    assert "task.context.images@2" in screen.capabilities
    assert len(definition.digest) == 64
    with pytest.raises(ValueError, match="unavailable"):
        load_workflow("missing_workflow@1")
    with pytest.raises(ValueError, match="does not match"):
        validate_workflow_result("text-report.v1", {"title": "Only a title"})


@pytest.mark.asyncio
async def test_workflow_admission_publication_followup_and_api_result(monkeypatch) -> None:
    definition = load_workflow("text_report@1")
    monkeypatch.setattr(settings, "task_executor", "codex")
    monkeypatch.setattr(settings, "task_workflow_allowlist", "")
    with pytest.raises(ValueError, match="not enabled"):
        await task_service.dispatch(
            goal="Summarize this task", origin_surface="test", idempotency_key="disabled-workflow",
            execution_kind="workflow", execution_config={"definition": "text_report@1"},
        )
    monkeypatch.setattr(settings, "task_workflow_allowlist", f"text_report@1:{definition.digest}")
    created = await task_service.dispatch(
        goal="Summarize this task", origin_surface="test", idempotency_key="enabled-workflow",
        execution_kind="workflow", execution_config={"definition": "text_report@1"},
    )
    worker = await worker_coordinator.activate(process_identity="workflow-worker")
    assert worker is not None
    claim = await worker_coordinator.claim_next(worker.worker_id)
    assert claim.task is not None and claim.task.task_id == created.task.id
    assert claim.task.execution_config["workflow"]["digest"] == definition.digest
    args = SimpleNamespace(
        task_id=created.task.id, run_id=claim.task.execution_run_id,
        attempt_id=claim.task.attempt_id, claim_token=claim.task.claim_token,
        workspace=Path(claim.task.workspace_path), workflow_schema="text-report.v1",
    )
    assert {tool["name"] for tool in _tool_catalog(args.workflow_schema)} == {
        "task_history", "result_publish",
    }
    with pytest.raises(ValueError, match="not allowlisted"):
        await _call("file_write", {"path": "escape.txt", "content": "BAD", "operation_key": "bad"}, args)
    before = await task_service.get_task(created.task.id)
    assert before is not None
    source = before.events[0].sequence
    with pytest.raises(ValueError, match="typed result"):
        await task_service.complete(
            created.task.id, summary="Not published", final_text="Not published",
            idempotency_key="bypass-workflow-publication",
            attempt_id=args.attempt_id, execution_run_id=args.run_id,
            claim_token=args.claim_token,
        )
    with pytest.raises(ValueError, match="outside this task"):
        await _call("result_publish", {
            "title": "Report", "body": "Done", "source_event_sequences": [999999],
            "operation_key": "publish-1",
        }, args)
    assert (await task_service.get_task(created.task.id)).task.status == "running"
    published = await _call("result_publish", {
        "title": "Report", "body": "The summary is complete.",
        "source_event_sequences": [source], "operation_key": "publish-1",
    }, args)
    assert published["status"] == "completed"
    detail = await task_service.get_task(created.task.id)
    assert detail is not None and detail.task.status == "completed"
    assert detail.runs[0].result_reference == f"task-artifact:{detail.artifacts[0].id}"
    assert detail.artifacts[0].content_json == {
        "schema": "text-report.v1", "content": {
            "title": "Report", "body": "The summary is complete.",
            "source_event_sequences": [source],
        },
    }
    assert detail.operations[0].operation_type == "task.result.publish@1"
    assert detail.operations[0].state == "succeeded"
    replayed = await _call("result_publish", {
        "title": "Report", "body": "The summary is complete.",
        "source_event_sequences": [source], "operation_key": "publish-1",
    }, args)
    assert replayed == published
    replay_detail = await task_service.get_task(created.task.id)
    assert replay_detail is not None and len(replay_detail.artifacts) == 1
    assert len(replay_detail.operations) == 1
    with pytest.raises(ValueError, match="conflicts"):
        await _call("result_publish", {
            "title": "Report", "body": "Changed result",
            "source_event_sequences": [source], "operation_key": "publish-1",
        }, args)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": ""})
        response = await client.get(f"/api/tasks/{created.task.id}")
        assert response.status_code == 200
        assert response.json()["artifacts"][0]["content_json"]["schema"] == "text-report.v1"
    followup = await task_service.continue_task(
        created.task.id, content="Add one sentence", idempotency_key="workflow-followup",
        origin="test", execution_kind="workflow",
    )
    assert followup.run.ordinal == 2
    assert followup.run.configuration_json["workflow"]["digest"] == definition.digest
    assert await _call("result_publish", {
        "title": "Report", "body": "The summary is complete.",
        "source_event_sequences": [source], "operation_key": "publish-1",
    }, args) == published
    with pytest.raises(ValueError):
        await _call("result_publish", {
            "title": "Late", "body": "Stale", "source_event_sequences": [],
            "operation_key": "stale-publication",
        }, args)


@pytest.mark.asyncio
async def test_worker_accepts_durable_publication_before_native_turn_drains(monkeypatch) -> None:
    definition = load_workflow("text_report@1")
    monkeypatch.setattr(settings, "task_executor", "codex")
    monkeypatch.setattr(settings, "task_workflow_allowlist", f"text_report@1:{definition.digest}")
    monkeypatch.setattr(settings, "worker_heartbeat_interval_seconds", 0.01)
    created = await task_service.dispatch(
        goal="Report ready", origin_surface="test", idempotency_key="publication-drain-race",
        execution_kind="workflow", execution_config={"definition": "text_report@1"},
    )

    class PublishingRuntime:
        async def execute(self, context, sink, *, cancellation_requested):
            await task_service.complete(
                context.task_id, summary="Published", idempotency_key="published-early",
                workflow_result={"title": "Published", "body": "Ready", "source_event_sequences": []},
                workflow_operation_key="published-early", attempt_id=context.attempt_id,
                execution_run_id=context.execution_run_id, claim_token=context.claim_token,
            )
            await asyncio.sleep(0.1)
            return AgentExecutionResult(status="completed", summary="Published")

    worker = TaskWorker(runtime=PublishingRuntime())
    assert await worker.activate(process_identity="publication-drain-worker")
    result = await worker.run_once()
    assert result.status == "completed"
    assert (await task_service.get_task(created.task.id)).task.status == "completed"


@pytest.mark.asyncio
async def test_workflow_http_admission_is_disabled_until_exact_digest_is_enabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "task_executor", "codex")
    monkeypatch.setattr(settings, "task_workflow_allowlist", "")
    body = {
        "goal": "Produce a report", "idempotency_key": "workflow-http-gate",
        "execution_kind": "workflow", "execution_config": {"definition": "text_report@1"},
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.post("/api/auth/login", json={"password": ""})
        denied = await client.post("/api/tasks", json=body)
        assert denied.status_code == 409
        definition = load_workflow("text_report@1")
        monkeypatch.setattr(settings, "task_workflow_allowlist", f"text_report@1:{definition.digest}")
        accepted = await client.post("/api/tasks", json=body)
        assert accepted.status_code == 201, accepted.text
        assert accepted.json()["execution_kind"] == "workflow"
