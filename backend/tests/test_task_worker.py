from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.db.session import session_scope
from app.db.tables import ResourceLease
from app.models.chat import ModelResponse, ToolCall, ToolCallFunction
from app.services import agent_execution as agent_execution_module
from app.services.task_presentations import task_presentation_service
from app.services.task_service import task_service
from app.services.task_worker import TaskWorker
from app.services.worker_coordinator import ClaimResult
from app.worker import wait_for_worker_activation


def control_response(name: str, arguments: dict, *, content: str = "") -> ModelResponse:
    return ModelResponse(
        model="fake/task-agent",
        content=content,
        tool_calls=[
            ToolCall(
                id="control-1",
                function=ToolCallFunction(name=name, arguments=json.dumps(arguments)),
            )
        ],
        finish_reason="tool_calls",
    )


@pytest.mark.asyncio
async def test_worker_startup_waits_for_an_abrupt_predecessor_lease(monkeypatch) -> None:
    class DelayedWorker:
        def __init__(self) -> None:
            self.calls = 0

        async def activate(self) -> bool:
            self.calls += 1
            return self.calls >= 3

    worker = DelayedWorker()

    async def immediate_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", immediate_sleep)
    await wait_for_worker_activation(worker, timeout_seconds=1)  # type: ignore[arg-type]
    assert worker.calls == 3


@pytest.mark.asyncio
async def test_deterministic_real_agent_task_completes_through_worker(monkeypatch) -> None:
    calls = 0

    async def fake_completion(messages, model, **kwargs):
        nonlocal calls
        calls += 1
        assert "Write the exact result" in messages[-1].content
        assert kwargs["priority"] == "background"
        if calls == 1:
            return control_response(
                "file_write",
                {"path": "result.txt", "content": "42\n"},
            )
        return control_response(
            "task_finalize",
            {"summary": "Returned the exact result", "final_text": "The exact result is 42."},
        )

    monkeypatch.setattr(agent_execution_module, "chat_completion", fake_completion)
    created = await task_service.dispatch(
        goal="Write the exact result",
        origin_surface="tasks_ui",
        idempotency_key="real-worker-task",
    )
    worker = TaskWorker()
    assert await worker.activate(process_identity="test-real-agent")

    result = await worker.run_once()

    assert result.task_id == created.task.id
    assert result.status == "completed"
    await task_presentation_service.sync_candidates()
    detail = await task_service.get_task(created.task.id)
    assert detail is not None
    assert detail.task.status == "completed"
    assert detail.task.terminal_summary == "Returned the exact result"
    assert detail.artifacts[0].content_text == "The exact result is 42."
    assert detail.presentations[0].candidate_type == "terminal_result"
    assert (settings.task_workspaces_path / created.task.id / "attempt-1" / "result.txt").read_text() == "42\n"
    assert {event.event_type for event in detail.events} >= {
        "task.queued",
        "task.claimed",
        "task.progress",
        "task.artifact.created",
        "task.completed",
    }


@pytest.mark.asyncio
async def test_question_checkpoints_and_releases_all_leases(monkeypatch) -> None:
    async def fake_completion(*args, **kwargs):
        return control_response("task_ask_user", {"question": "Which environment?"})

    monkeypatch.setattr(agent_execution_module, "chat_completion", fake_completion)
    created = await task_service.dispatch(
        goal="Deploy safely",
        origin_surface="tasks_ui",
        idempotency_key="worker-question",
        targets=(("repository", "AI-Cockpit", True),),
    )
    worker = TaskWorker()
    assert await worker.activate(process_identity="test-question")

    result = await worker.run_once()

    assert result.status == "waiting_for_input"
    detail = await task_service.get_task(created.task.id)
    assert detail is not None
    assert detail.task.status == "waiting_for_input"
    assert detail.questions[0].question == "Which environment?"
    assert detail.attempts[0].checkpoint_json["step"] == 1
    async with session_scope() as session:
        assert list((await session.execute(select(ResourceLease))).scalars().all()) == []


@pytest.mark.asyncio
async def test_running_cancellation_is_observed_and_releases_leases(monkeypatch) -> None:
    entered = asyncio.Event()

    async def slow_completion(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(agent_execution_module, "chat_completion", slow_completion)
    monkeypatch.setattr(settings, "worker_heartbeat_interval_seconds", 0.01)
    created = await task_service.dispatch(
        goal="Wait until cancelled",
        origin_surface="tasks_ui",
        idempotency_key="worker-cancel",
        targets=(("repository", "AI-Cockpit", True),),
    )
    worker = TaskWorker()
    assert await worker.activate(process_identity="test-cancel")
    running = asyncio.create_task(worker.run_once())
    await entered.wait()
    requested = await task_service.cancel(created.task.id, idempotency_key="cancel-from-api")
    assert requested.task.status == "running"

    result = await asyncio.wait_for(running, timeout=1)

    assert result.status == "cancelled"
    detail = await task_service.get_task(created.task.id)
    assert detail is not None and detail.task.status == "cancelled"
    async with session_scope() as session:
        assert list((await session.execute(select(ResourceLease))).scalars().all()) == []


@pytest.mark.asyncio
async def test_worker_retries_transient_sqlite_lock_instead_of_exiting(monkeypatch) -> None:
    stop = asyncio.Event()

    class LockThenIdleCoordinator:
        def __init__(self) -> None:
            self.claim_calls = 0
            self.stopped = False

        async def claim_next(self, worker_id: str) -> ClaimResult:
            self.claim_calls += 1
            if self.claim_calls == 1:
                raise OperationalError(
                    "UPDATE worker_instances",
                    {},
                    Exception("database is locked"),
                )
            stop.set()
            return ClaimResult(task=None)

        async def heartbeat(self, worker_id: str, *, state: str | None = None) -> bool:
            return True

        async def stop(self, worker_id: str) -> None:
            self.stopped = True

    coordinator = LockThenIdleCoordinator()
    worker = TaskWorker(coordinator=coordinator)  # type: ignore[arg-type]
    worker.worker_id = "worker-1"
    monkeypatch.setattr(settings, "worker_poll_interval_seconds", 0.01)

    await asyncio.wait_for(worker.run_forever(stop), timeout=1)

    assert coordinator.claim_calls == 2
    assert coordinator.stopped is True
