"""Code-only recovery; never infer native termination from elapsed time."""
from __future__ import annotations

import socket

from sqlalchemy import select, update

from app.db.repositories.tasks import TaskRepository
from app.db.session import session_scope
from app.db.tables import OrchestrationAction, OrchestrationDecision, Task, TaskEvent, TaskSupervisorLease
from app.services.orchestration_authority import StaleDecision, assert_current, digest, observed_state
from app.services.windows_job import process_has_exited


def host_has_exited(binding: dict) -> bool:
    if binding.get("host_hostname") != socket.gethostname():
        return False
    pid, stamp = binding.get("host_pid"), binding.get("host_creation_stamp")
    if not isinstance(pid, int) or not isinstance(stamp, int) or pid <= 0 or stamp <= 0:
        return False
    return process_has_exited(pid, stamp) is True


async def recover(service, decision_id: str, *, resume: bool = False,
                  operation_key: str | None = None, reason: str = "", automatic: bool = False) -> dict:
    from app.services.task_orchestration import LEASE_KEY, now

    if resume and (not operation_key or len(operation_key) > 128 or not reason.strip() or len(reason) > 2000):
        raise ValueError("Resumption requires a bounded stable operation key and reason")
    if service.generation is None and not await service.acquire():
        raise StaleDecision("Another background supervisor still owns recovery")
    audit_key = f"orchestration-audit:{decision_id}:resume:{operation_key}"
    fingerprint = digest({"decision_id": decision_id, "reason": reason, "resume": resume})

    async def lock(session):
        result = await session.execute(update(TaskSupervisorLease).where(
            TaskSupervisorLease.lease_key == LEASE_KEY,
            TaskSupervisorLease.owner_identity == service.owner,
            TaskSupervisorLease.generation == service.generation,
        ).values(heartbeat_at=now()))
        if result.rowcount != 1:
            raise StaleDecision("Background recovery supervisor is stale")

    async with session_scope() as session:
        await lock(session)
        if resume:
            previous = (await session.execute(select(TaskEvent).where(TaskEvent.idempotency_key == audit_key))).scalar_one_or_none()
            if previous:
                if previous.payload_json.get("input_digest") != fingerprint:
                    raise ValueError("Resumption operation key conflicts with changed arguments")
                return dict(previous.payload_json["outcome"])
        row = await session.get(OrchestrationDecision, decision_id)
        if row is None:
            raise ValueError("Decision does not exist")
        if row.status != "deferred":
            raise ValueError("Only deferred decisions may be inspected or resumed")
        binding = dict(row.native_binding_json or {})
        version = (row.claim_generation, row.claim_owner, digest(binding), row.context_digest)
        receipt_ids = list((await session.execute(select(OrchestrationAction.id).where(
            OrchestrationAction.decision_id == row.id,
        ))).scalars().all())
        previous_inspection = (row.outcome_json or {}).get("recovery", {})
        if automatic and not receipt_ids and previous_inspection.get("binding_digest") == digest(binding):
            return {"decision_id": row.id, "status": "deferred", "recovery": previous_inspection}
        profile = row.auth_profile

    # Both OS proof and SDK inspection happen outside all write transactions.
    inspection = {"status": "no_native_binding", "safe_to_resume": not binding}
    if binding and not receipt_ids:
        if not service.native_host_has_exited(binding):
            inspection = {"status": "host_exit_unverified", "safe_to_resume": False}
        elif binding.get("before_turn_auth_unavailable") and not binding.get("thread_id") and not binding.get("turn_id"):
            inspection = {"status": "auth_unavailable_before_turn", "safe_to_resume": True}
        elif not binding.get("thread_id"):
            inspection = {"status": "native_identity_unknown", "safe_to_resume": False}
        else:
            runtime = service.runtime
            if runtime is None or not hasattr(runtime, "inspect"):
                from app.services.codex_orchestration_runtime import CodexOrchestrationRuntime
                runtime = CodexOrchestrationRuntime()  # Read-only; cannot generate a turn.
            try:
                observed = await runtime.inspect(binding, profile)
                # Inspector identities must match the durable native binding.
                if (observed.get("thread_id") != binding["thread_id"]
                        or observed.get("turn_id") != binding.get("turn_id")):
                    raise ValueError("Native inspection identity mismatch")
                inspection = {**observed, "safe_to_resume": observed.get("status") == "terminal"}
            except Exception as exc:
                # Missing/corrupt/unsupported logs do not prove a turn stopped.
                inspection = {"status": "inspection_failed", "safe_to_resume": False,
                              "error_type": type(exc).__name__}
    inspection.update(binding_digest=digest(binding), inspected_at=now().isoformat())

    async with session_scope() as session:
        await lock(session)
        if resume:
            previous = (await session.execute(select(TaskEvent).where(TaskEvent.idempotency_key == audit_key))).scalar_one_or_none()
            if previous:
                if previous.payload_json.get("input_digest") != fingerprint:
                    raise ValueError("Resumption operation key conflicts with changed arguments")
                return dict(previous.payload_json["outcome"])
        row = await session.get(OrchestrationDecision, decision_id)
        if row.status != "deferred" or version != (
            row.claim_generation, row.claim_owner, digest(row.native_binding_json or {}), row.context_digest,
        ):
            raise StaleDecision("Decision changed during native inspection")
        # Recheck receipts after inspection. A durable action always wins.
        receipt_ids = list((await session.execute(select(OrchestrationAction.id).where(
            OrchestrationAction.decision_id == row.id,
        ))).scalars().all())
        row.outcome_json = {**(row.outcome_json or {}), "recovery": inspection}
        if receipt_ids:
            row.status, row.completed_at, row.due_at = "completed", now(), None
            row.outcome_json = {**row.outcome_json, "reconciled_actions": receipt_ids}
        else:
            task = (await session.execute(select(Task).where(Task.id == row.task_id))).scalar_one_or_none()
            if task is not None:
                from app.db.tables import TaskQuestion
                question = await session.get(TaskQuestion, row.question_id) if row.question_id else None
                current = await observed_state(session, task, question)
                row.observed_json = {**row.observed_json, "instructions_digest": current["instructions_digest"]}
            try:
                await assert_current(session, row, task)
            except StaleDecision:
                row.status, row.last_error, row.completed_at, row.due_at = "obsolete", "stale_decision", now(), None
            if resume and row.status == "deferred" and inspection["safe_to_resume"]:
                row.status, row.due_at, row.last_error = "pending", now(), None
                row.claim_generation += 1  # Fence every old bridge before allowing fresh work.
                row.claim_owner = None
                # Preserve old native identity in the audit before a fresh host binds.
                row.native_binding_json = None
        outcome = {"decision_id": row.id, "status": row.status, "recovery": inspection}
        if resume:
            await TaskRepository(session).append_event(
                task_id=row.task_id, event_type="task.orchestration.resumption",
                payload_json={"decision_id": row.id, "reason": reason, "input_digest": fingerprint,
                              "previous_native_binding": binding, "outcome": outcome},
                idempotency_key=audit_key,
            )
    service._wake.set()
    return outcome
