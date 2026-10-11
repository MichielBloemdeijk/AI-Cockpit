from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config import settings
from app.jarvis_tasks.task_mcp import _call
from app.db.session import session_scope
from app.db.tables import TaskTurn
from app.services.task_context import portable_context
from app.services.task_semantic_compaction import ensure_semantic_compaction, latest_semantic_summary
from app.services.task_service import task_service
from app.services.worker_coordinator import worker_coordinator


@pytest.mark.asyncio
async def test_long_history_requires_and_retains_model_summary(monkeypatch) -> None:
    created = await task_service.dispatch(
        goal="Continue a long task", origin_surface="test", idempotency_key="long-task",
    )
    worker = await worker_coordinator.activate(process_identity="semantic-compaction-worker")
    assert worker is not None
    claim = await worker_coordinator.claim_next(worker.worker_id)
    assert claim.task is not None
    async with session_scope() as session:
        session.add_all([
            TaskTurn(
                id=str(uuid4()), task_id=created.task.id, sequence=sequence,
                role="user", content=f"Requirement {sequence}: keep original evidence",
                origin="test",
            ) for sequence in range(2, 32)
        ])
    monkeypatch.setattr(settings, "task_executor", "codex")
    with pytest.raises(ValueError, match="semantic compaction"):
        await portable_context(
            created.task.id, run_id=claim.task.execution_run_id,
            attempt_id=claim.task.attempt_id, claim_token=claim.task.claim_token,
        )
    prompts: list[str] = []

    async def summarize(prompt: str) -> str:
        prompts.append(prompt)
        return f"Summary batch {len(prompts)}: retain requirements and evidence identifiers."

    result = await ensure_semantic_compaction(
        created.task.id, run_id=claim.task.execution_run_id,
        attempt_id=claim.task.attempt_id, claim_token=claim.task.claim_token,
        summarize=summarize,
    )
    assert result is not None and result["through_turn_sequence"] == 19
    assert len(prompts) == 2
    artifact, latest = await latest_semantic_summary(created.task.id)
    assert artifact is not None and latest == result
    assert latest["previous_summary_artifact_id"] is not None
    handoff = json.loads(await portable_context(
        created.task.id, run_id=claim.task.execution_run_id,
        attempt_id=claim.task.attempt_id, claim_token=claim.task.claim_token,
    ))
    assert handoff["semantic_summary"]["artifact_id"] == artifact.id
    assert handoff["semantic_summary"]["through_turn_sequence"] == 19
    assert handoff["recent_turns"][0]["sequence"] == 20
    assert handoff["history_tool"] == "jarvis_task.task_history"
    args = SimpleNamespace(
        task_id=created.task.id, run_id=claim.task.execution_run_id,
        attempt_id=claim.task.attempt_id, claim_token=claim.task.claim_token,
        workspace=Path(claim.task.workspace_path), workflow_schema=None,
    )
    original = await _call("task_history", {"turn_sequence": 2}, args)
    assert json.loads(original["evidence_json_chunk"])["content"] == "Requirement 2: keep original evidence"
    retained = await _call("task_history", {"artifact_id": artifact.id}, args)
    assert json.loads(retained["evidence_json_chunk"])["content_json"]["summary"] == latest["summary"]
    await ensure_semantic_compaction(
        created.task.id, run_id=claim.task.execution_run_id,
        attempt_id=claim.task.attempt_id, claim_token=claim.task.claim_token,
        summarize=summarize,
    )
    assert len(prompts) == 2
