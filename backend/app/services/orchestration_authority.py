"""Backend-owned optimistic authority, checked under the task write lock."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from uuid import uuid4

from sqlalchemy import func, select, update

from app.db.tables import OrchestrationAction, OrchestrationDecision, Task, TaskQuestion, TaskTurn


class StaleDecision(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DecisionAuthority:
    decision_id: str
    generation: int
    owner: str
    operation_key: str
    reason: str = ""

    @property
    def idempotency_key(self) -> str:
        return f"orchestration:{self.decision_id}:{self.operation_key}"


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


async def observed_state(session, task: Task, question: TaskQuestion | None = None) -> dict:
    user_sequence = int((await session.execute(select(func.max(TaskTurn.sequence)).where(
        TaskTurn.task_id == task.id, TaskTurn.role == "user", TaskTurn.origin != "orchestration",
    ))).scalar_one_or_none() or 0)
    from app.services.orchestration_context import instructions
    return {
        "task_id": task.id, "run_id": task.current_run_id,
        "goal_revision": task.goal_revision, "control_revision": task.control_revision,
        "workspace_generation": task.workspace_generation, "user_sequence": user_sequence,
        "presentation_policy": task.presentation_policy,
        "instructions_digest": digest(await instructions(session)),
        "question_id": question.id if question else None,
        "question_state": question.state if question else None,
        "question_revision": question.resolution_revision if question else None,
        "asking_run_id": question.asking_run_id if question else None,
    }


async def assert_current(session, decision: OrchestrationDecision, task: Task) -> None:
    if task is None:
        raise StaleDecision("Decision task no longer exists")
    question = await session.get(TaskQuestion, decision.question_id) if decision.question_id else None
    if decision.observed_json != await observed_state(session, task, question):
        raise StaleDecision("Background decision observed stale task/question state")
    if task.pending_control_id or task.status in {"paused", "cancelled"}:
        raise StaleDecision("User control prevents automatic background action")
    if decision.trigger_kind == "failure" and task.status not in {"failed", "interrupted"}:
        raise StaleDecision("Failure no longer needs background recovery")
    if decision.question_id and (question is None or question.state != "asked"
                                 or question.asking_run_id != task.current_run_id):
        raise StaleDecision("Question no longer belongs to the current asking run")


async def begin_action(session, authority: DecisionAuthority | None, *, action: str,
                       arguments: dict, task_id: str | None = None) -> OrchestrationAction | None:
    if authority is None:
        return None
    # This write locks SQLite before reading either authority or idempotency.
    result = await session.execute(update(OrchestrationDecision).where(
        OrchestrationDecision.id == authority.decision_id,
        OrchestrationDecision.status == "running",
        OrchestrationDecision.claim_generation == authority.generation,
        OrchestrationDecision.claim_owner == authority.owner,
    ).values(status=OrchestrationDecision.status))
    if result.rowcount != 1:
        raise StaleDecision("Background decision claim is stale")
    decision = await session.get(OrchestrationDecision, authority.decision_id)
    if task_id is not None and decision.task_id != task_id:
        raise ValueError("Decision cannot mutate an unrelated task")
    fingerprint = digest({"action": action, "arguments": arguments, "reason": authority.reason})
    existing = (await session.execute(select(OrchestrationAction).where(
        OrchestrationAction.decision_id == authority.decision_id,
        OrchestrationAction.operation_key == authority.operation_key,
    ))).scalar_one_or_none()
    if existing:
        if existing.input_digest != fingerprint:
            raise ValueError("Orchestration operation key conflicts with changed arguments")
        return existing
    task = (await session.execute(select(Task).where(Task.id == decision.task_id))).scalar_one_or_none()
    if task is None:
        raise StaleDecision("Decision task no longer exists")
    await assert_current(session, decision, task)
    receipt = OrchestrationAction(
        id=str(uuid4()), decision_id=decision.id, operation_key=authority.operation_key,
        action=action, input_digest=fingerprint, outcome_json={"task_id": task.id, "reason": authority.reason},
    )
    session.add(receipt)
    await session.flush()
    return receipt
