"""Authoritative run transitions that keep status and durable events aligned."""
from __future__ import annotations

from dataclasses import dataclass

from app.services.conversation_store import RunRecord, conversation_store


@dataclass(frozen=True, slots=True)
class RunTransitionResult:
    run: RunRecord | None
    changed: bool


class RunLifecycleService:
    async def cancel(self, run_id: str, *, summary: str = "The task was cancelled.") -> RunTransitionResult:
        result = await conversation_store.cancel_run_with_projection(run_id, summary=summary)
        if result is None:
            return RunTransitionResult(run=await conversation_store.get_run(run_id), changed=False)
        transitioned, event = result
        metadata = transitioned.metadata_json if isinstance(transitioned.metadata_json, dict) else {}
        if metadata.get("execution_mode") == "background" and event is not None:
            global_task_id = str(metadata.get("global_task_id") or "").strip()
            if global_task_id:
                from app.services.task_service import task_service

                requested = await task_service.cancel(
                    global_task_id,
                    idempotency_key=f"legacy-run-cancel:{run_id}",
                )
                if requested.task.status == "running":
                    await task_service.finish_cancellation(
                        global_task_id,
                        idempotency_key=f"legacy-run-cancelled:{run_id}",
                    )
            from app.services.notifications import notify_event

            await notify_event(event.id)
        return RunTransitionResult(run=transitioned, changed=True)


run_lifecycle = RunLifecycleService()
