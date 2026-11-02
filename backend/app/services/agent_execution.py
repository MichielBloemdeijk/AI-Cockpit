"""Execution seam between the task worker and its runtime (task attempts only)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Protocol

from app.services.tasks.service import task_service


CancellationProbe = Callable[[], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class AgentExecutionContext:
    execution_id: str
    task_id: str
    attempt_id: str
    execution_run_id: str
    claim_token: str
    execution_kind: str
    execution_config: dict[str, Any]
    goal: str
    workspace_path: str
    origin_conversation_id: str | None
    targets: tuple[tuple[str, str, bool], ...]
    checkpoint: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AgentExecutionResult:
    status: str
    summary: str | None = None


class AgentExecutionSink(Protocol):
    async def event(self, event_type: str, payload: dict[str, Any]) -> None: ...
    async def progress(self, summary: str) -> None: ...
    async def checkpoint(self, value: dict[str, Any]) -> None: ...
    async def question(self, question: str) -> None: ...
    async def wait_until(self, resume_at: datetime, reason: str) -> None: ...
    async def complete(self, summary: str, final_text: str) -> None: ...
    async def fail(self, error: str) -> None: ...
    async def cancelled(self) -> None: ...


class TargetExpansionRequested(RuntimeError):
    def __init__(self, targets: tuple[tuple[str, str, bool], ...], checkpoint: dict[str, Any]) -> None:
        super().__init__("Agent execution discovered additional mutable targets")
        self.targets = targets
        self.checkpoint = checkpoint


class TaskExecutionSink:
    def __init__(self, context: AgentExecutionContext) -> None:
        self.context = context
        self.counter = int(context.checkpoint.get("event_counter", 0) or 0)

    def _key(self, event_type: str) -> str:
        self.counter += 1
        return f"runtime:{self.context.attempt_id}:{self.counter}:{event_type}"

    async def event(self, event_type: str, payload: dict[str, Any]) -> None:
        await task_service.record_event(
            self.context.task_id,
            attempt_id=self.context.attempt_id,
            event_type=event_type,
            payload=payload,
            idempotency_key=self._key(event_type),
            execution_run_id=self.context.execution_run_id,
            claim_token=self.context.claim_token,
        )

    async def progress(self, summary: str) -> None:
        await task_service.publish_progress(
            self.context.task_id,
            summary=summary,
            idempotency_key=self._key("task.progress"),
            attempt_id=self.context.attempt_id,
            execution_run_id=self.context.execution_run_id,
            claim_token=self.context.claim_token,
        )

    async def checkpoint(self, value: dict[str, Any]) -> None:
        value["event_counter"] = self.counter
        await task_service.save_checkpoint(
            self.context.task_id,
            checkpoint=value,
            attempt_id=self.context.attempt_id,
            execution_run_id=self.context.execution_run_id,
            claim_token=self.context.claim_token,
        )

    async def question(self, question: str) -> None:
        await task_service.ask_question(
            self.context.task_id,
            question=question,
            idempotency_key=self._key("task.requires_input"),
            attempt_id=self.context.attempt_id,
            execution_run_id=self.context.execution_run_id,
            claim_token=self.context.claim_token,
        )

    async def wait_until(self, resume_at: datetime, reason: str) -> None:
        await task_service.wait_until(
            self.context.task_id,
            resume_at=resume_at,
            reason=reason,
            idempotency_key=self._key("task.waiting_until"),
            attempt_id=self.context.attempt_id,
            execution_run_id=self.context.execution_run_id,
            claim_token=self.context.claim_token,
        )

    async def complete(self, summary: str, final_text: str) -> None:
        await task_service.complete(
            self.context.task_id,
            summary=summary or final_text,
            idempotency_key=self._key("task.completed"),
            final_text=final_text,
            attempt_id=self.context.attempt_id,
            execution_run_id=self.context.execution_run_id,
            claim_token=self.context.claim_token,
        )

    async def fail(self, error: str) -> None:
        await task_service.complete(
            self.context.task_id,
            status="failed",
            summary=error,
            idempotency_key=self._key("task.failed"),
            attempt_id=self.context.attempt_id,
            execution_run_id=self.context.execution_run_id,
            claim_token=self.context.claim_token,
        )

    async def cancelled(self) -> None:
        raise RuntimeError("A task worker cannot confirm its own descendant process tree stopped")


class TaskRuntime(Protocol):
    """What the task worker needs from an execution runtime."""

    async def execute(
        self,
        context: AgentExecutionContext,
        sink: AgentExecutionSink,
        *,
        cancellation_requested: CancellationProbe,
    ) -> AgentExecutionResult: ...
