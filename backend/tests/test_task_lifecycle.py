"""The task lifecycle module owns every task, run and attempt transition."""
from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.repositories.conversations import utc_now
from app.db.session import session_scope
from app.db.tables import ResourceLease, TaskEvent
from app.services.tasks.reminder_scheduler import reminder_scheduler
from app.services.tasks.service import task_service
from app.services.tasks.vocabulary import CONTROL_ACTIONS, ControlAction
from app.services.tasks.worker_coordinator import worker_coordinator

APP = Path(__file__).resolve().parents[1] / "app"
LIFECYCLE = APP / "services" / "tasks" / "lifecycle.py"


async def _claimed(key: str, worker_id: str):
    created = await task_service.dispatch(
        goal=f"lifecycle {key}", origin_surface="test", idempotency_key=key,
        targets=(("directory", f"lifecycle-{key}", True),),
    )
    worker = await worker_coordinator.activate(process_identity=worker_id, worker_id=worker_id)
    assert worker is not None
    claim = (await worker_coordinator.claim_next(worker.worker_id)).task
    assert claim is not None and claim.task_id == created.task.id
    return claim


def _fence(claim) -> dict:
    return {"attempt_id": claim.attempt_id, "execution_run_id": claim.execution_run_id,
            "claim_token": claim.claim_token}


@pytest.mark.asyncio
async def test_an_elapsed_wait_continues_from_the_waiting_attempt() -> None:
    claim = await _claimed("wait", "wait-worker")
    await task_service.save_checkpoint(claim.task_id, checkpoint={"step": 3}, **_fence(claim))
    due = utc_now() + timedelta(minutes=5)
    await task_service.wait_until(
        claim.task_id, resume_at=due, reason="check back later", idempotency_key="wait", **_fence(claim),
    )

    assert await reminder_scheduler._resume_one(claim.task_id, timestamp=due + timedelta(seconds=1))

    detail = await task_service.get_task(claim.task_id)
    assert detail is not None
    waiting, resumed = detail.attempts
    # Five minutes on, the worker's heartbeat is stale: the task waits for one.
    assert detail.task.status == "waiting_for_worker" and detail.runs[0].status == "queued"
    assert detail.task.resume_at is None and detail.task.wait_reason is None
    assert waiting.terminal_reason == "waiting_until"
    # Like every other resume path: lineage, checkpoint and its version carry over.
    assert resumed.parent_attempt_id == waiting.id
    assert resumed.resume_reason == "wait_elapsed"
    assert resumed.checkpoint_json == {"step": 3}
    assert resumed.checkpoint_version == waiting.checkpoint_version
    assert detail.task.current_attempt_id == resumed.id == detail.runs[0].current_attempt_id


@pytest.mark.asyncio
async def test_an_answered_question_continues_from_the_asking_attempt() -> None:
    claim = await _claimed("question", "question-worker")
    await task_service.save_checkpoint(claim.task_id, checkpoint={"step": 1}, **_fence(claim))
    question = await task_service.ask_question(
        claim.task_id, question="Which folder?", idempotency_key="ask", **_fence(claim),
    )
    await task_service.answer_question(claim.task_id, question.id, answer="Documents", idempotency_key="answer")

    detail = await task_service.get_task(claim.task_id)
    assert detail is not None
    asking, answered = detail.attempts
    assert detail.task.status in {"queued", "waiting_for_worker"} and detail.runs[0].status == "queued"
    assert answered.parent_attempt_id == asking.id
    assert answered.resume_reason == "question_answered"
    assert answered.checkpoint_json == {"step": 1}


@pytest.mark.parametrize("evidence_source", ["runtime", "process_exit"])
@pytest.mark.asyncio
async def test_a_stop_confirms_the_same_way_whatever_proves_it(evidence_source: str) -> None:
    worker_id = f"stop-{evidence_source}"
    claim = await _claimed(f"stop-{evidence_source}", worker_id)
    await task_service.save_checkpoint(claim.task_id, checkpoint={"step": 2}, **_fence(claim))
    controlled = await task_service.control(
        claim.task_id, action="yield", idempotency_key=f"yield-{evidence_source}", parameters={"immediate": True},
    )
    assert controlled.command.state == "stop_requested"

    if evidence_source == "runtime":
        await task_service.confirm_control(
            claim.task_id, command_id=controlled.command.id, stopped_attempt_id=claim.attempt_id,
            stop_evidence={"kind": "worker_execution_stopped", "worker_id": worker_id},
        )
    else:
        await worker_coordinator.worker_exited(worker_id, process_identity=worker_id, exit_code=0)

    detail = await task_service.get_task(claim.task_id)
    assert detail is not None
    stopped, requeued = detail.attempts
    assert detail.task.status in {"queued", "waiting_for_worker"} and detail.runs[0].status == "queued"
    assert detail.task.pending_control_id is None
    assert stopped.terminal_reason == "control:yield"
    assert (stopped.recovery_metadata_json or {})["stop_evidence"]["kind"] in {
        "worker_execution_stopped", "observed_process_tree_exit",
    }
    assert requeued.parent_attempt_id == stopped.id and requeued.resume_reason == "yield"
    assert requeued.checkpoint_json == {"step": 2}
    command = next(item for item in detail.controls if item.id == controlled.command.id)
    assert command.state == "confirmed" and command.detail
    async with session_scope() as session:
        leases = (await session.execute(
            select(ResourceLease).where(ResourceLease.owner_attempt_id == stopped.id))).scalars().all()
        confirmed = (await session.execute(select(TaskEvent).where(
            TaskEvent.idempotency_key == f"task.control.confirmed:{controlled.command.id}"))).scalar_one()
    assert leases == []
    assert isinstance(confirmed.payload_json["stop_evidence"], dict)
    assert confirmed.execution_run_id == claim.execution_run_id


def test_only_the_lifecycle_module_writes_task_and_run_status() -> None:
    """Task and run statuses change only through ``app.services.tasks.lifecycle``."""
    writers = []
    for path in sorted(APP.rglob("*.py")):
        if path == LIFECYCLE:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(
                node, (ast.AugAssign, ast.AnnAssign)) else []
            for target in targets:
                if (isinstance(target, ast.Attribute) and target.attr == "status"
                        and isinstance(target.value, ast.Name)
                        and ("task" in target.value.id or target.value.id.endswith("run"))):
                    writers.append(f"{path.relative_to(APP.parent).as_posix()}:{node.lineno}")
    assert writers == []


def test_every_control_surface_offers_the_control_vocabulary() -> None:
    from app.jarvis_tasks.orchestration_mcp import catalog
    from app.models.tasks import TaskControlRequest
    from app.services.jarvis.tool_catalog import get_jarvis_tool_definitions

    api_actions = TaskControlRequest.model_json_schema()["$defs"]["ControlAction"]["enum"]
    voice_tool = next(tool for tool in get_jarvis_tool_definitions() if tool["name"] == "control_task")
    control = next(tool for tool in catalog() if tool["name"] == "control")
    orchestration_actions = control["inputSchema"]["properties"]["action"]["enum"]
    assert tuple(api_actions) == CONTROL_ACTIONS
    assert tuple(voice_tool["parameters"]["properties"]["action"]["enum"]) == CONTROL_ACTIONS
    # Background orchestration gets the subset that never overrides the owner.
    assert set(orchestration_actions) == {"retry", "set_importance", "suspend", "yield"}
    assert ControlAction.cancel not in orchestration_actions
