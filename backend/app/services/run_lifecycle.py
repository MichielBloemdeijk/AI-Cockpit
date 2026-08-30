"""Authoritative run transitions that keep status and durable events aligned."""
from __future__ import annotations

from dataclasses import dataclass

from app.services.conversation_store import RunRecord, conversation_store


@dataclass(frozen=True, slots=True)
class RunTransitionResult:
    run: RunRecord | None
    changed: bool


class RunLifecycleService:
    async def cancel(self, run_id: str, *, summary: str = "The run was cancelled.") -> RunTransitionResult:
        result = await conversation_store.cancel_run_with_projection(run_id, summary=summary)
        if result is None:
            return RunTransitionResult(run=await conversation_store.get_run(run_id), changed=False)
        transitioned, _event = result
        return RunTransitionResult(run=transitioned, changed=True)


run_lifecycle = RunLifecycleService()
