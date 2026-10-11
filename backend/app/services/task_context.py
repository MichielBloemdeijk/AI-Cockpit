"""Bounded, portable handoff when a native Codex thread cannot be resumed.

This is a deterministic index over durable task records, not a replacement for
the evidence itself. The task-scoped MCP history reader retrieves original
events by cursor when a compact handoff needs verification.
"""
from __future__ import annotations

from hashlib import sha256
import json
from uuid import uuid4

from sqlalchemy import func, or_, select

from app.config import settings
from app.db.session import session_scope
from app.db.tables import (
    TaskArtifact, TaskContextBundle, TaskEvent, TaskExecutionRun,
    TaskOperation, TaskQuestion, TaskTurn, ResultPage,
)
from app.db.repositories.tasks import TaskRepository
from app.services.task_service import task_service


def _short(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value if len(value) <= limit else value[:limit] + "… [truncated; inspect durable evidence]"


async def portable_context(task_id: str, *, run_id: str, attempt_id: str, claim_token: str) -> str:
    """Pin once, then return the exact same evidence index across attempts."""
    async with session_scope() as session:
        task = await task_service._lock_task(session, task_id)
        await task_service._assert_fence(
            session, task, attempt_id=attempt_id,
            execution_run_id=run_id, claim_token=claim_token,
        )
        current_run = await session.get(TaskExecutionRun, run_id)
        if current_run is None or current_run.task_id != task_id:
            raise ValueError("Current execution run is unavailable")
        existing = (await session.execute(
            select(TaskContextBundle).where(TaskContextBundle.execution_run_id == run_id)
        )).scalar_one_or_none()
        if existing is not None:
            if (
                existing.task_id != task_id
                or existing.schema_version != "task-context.v1"
                or current_run.context_reference != f"task-context:{existing.id}"
            ):
                raise ValueError("Pinned task context identity is inconsistent")
            serialized = json.dumps(existing.content_json, ensure_ascii=False, separators=(",", ":"))
            if sha256(serialized.encode("utf-8")).hexdigest() != existing.content_sha256:
                raise ValueError("Pinned task context digest does not match")
            return serialized
        if current_run.context_reference is not None:
            raise ValueError("Execution run has an unknown context reference")
        current_input = await session.get(TaskTurn, current_run.input_turn_id)
        if current_input is None or current_input.task_id != task_id:
            raise ValueError("Execution run input turn is unavailable")
        turns = list((await session.execute(
            select(TaskTurn).where(
                TaskTurn.task_id == task_id,
                or_(TaskTurn.execution_run_id != run_id, TaskTurn.execution_run_id.is_(None)),
            )
            .order_by(TaskTurn.sequence.desc()).limit(12)
        )).scalars().all())
        prior_turn_count = int((await session.execute(
            select(func.count()).select_from(TaskTurn).where(
                TaskTurn.task_id == task_id,
                or_(TaskTurn.execution_run_id != run_id, TaskTurn.execution_run_id.is_(None)),
            )
        )).scalar_one())
        summary_artifact = (await session.execute(select(TaskArtifact).where(
            TaskArtifact.task_id == task_id,
            TaskArtifact.artifact_type == "task.context.summary",
        ).order_by(TaskArtifact.content_json["through_turn_sequence"].as_integer().desc()).limit(1))).scalar_one_or_none()
        from app.services.task_semantic_compaction import _summary_payload

        semantic_summary = _summary_payload(summary_artifact)
        if (settings.task_executor == "codex" and prior_turn_count > len(turns)
                and (semantic_summary is None or
                     semantic_summary["through_turn_sequence"] < turns[-1].sequence - 1)):
            raise ValueError("Older task turns need semantic compaction before context can be pinned")
        runs = list((await session.execute(
            select(TaskExecutionRun)
            .where(TaskExecutionRun.task_id == task_id, TaskExecutionRun.ordinal < current_run.ordinal)
            .order_by(TaskExecutionRun.ordinal.desc()).limit(8)
        )).scalars().all())
        operations = list((await session.execute(
            select(TaskOperation)
            .where(TaskOperation.task_id == task_id, TaskOperation.execution_run_id != run_id)
            .order_by(TaskOperation.started_at.desc(), TaskOperation.id.desc()).limit(12)
        )).scalars().all())
        artifacts = list((await session.execute(
            select(TaskArtifact)
            .where(
                TaskArtifact.task_id == task_id,
                or_(TaskArtifact.execution_run_id != run_id, TaskArtifact.execution_run_id.is_(None)),
            )
            .order_by(TaskArtifact.created_at.desc(), TaskArtifact.id.desc()).limit(12)
        )).scalars().all())
        questions = list((await session.execute(
            select(TaskQuestion).where(TaskQuestion.task_id == task_id)
            .order_by(TaskQuestion.asked_at.desc(), TaskQuestion.id.desc()).limit(8)
        )).scalars().all())
        evidence = list((await session.execute(
            select(TaskEvent).where(TaskEvent.task_id == task_id)
            .order_by(TaskEvent.sequence.desc()).limit(12)
        )).scalars().all())
        latest_event = int((await session.execute(
            select(func.max(TaskEvent.sequence)).where(TaskEvent.task_id == task_id)
        )).scalar_one_or_none() or 0)
        from app.services.task_result_contract import context_result
        page = await context_result(session, task_id, run_id)
        handoff = {
            "schema": "task-context.v1",
            "task_id": task_id,
            "current_run_ordinal": current_run.ordinal,
            "current_request": _short(current_input.content, 2_000),
            "current_input_artifact": current_input.artifact_reference,
            "result_intent": (current_run.configuration_json or {}).get("result"),
            "screen_context": next((run.configuration_json["screen_context"] for run in [current_run, *runs]
                if (run.configuration_json or {}).get("screen_context")), None),
            "maintained_result": None if page is None else {"public_id": page.public_id,
                "revision_number": page.revision_number, "schema": page.result_kind,
                "current_revision_id": page.current_revision_id},
            "semantic_summary": (
                {"artifact_id": summary_artifact.id,
                 "through_turn_sequence": semantic_summary["through_turn_sequence"],
                 "summary": semantic_summary["summary"],
                 "source_sha256": semantic_summary["source_sha256"],
                 "truncated_source_count": semantic_summary["truncated_source_count"]}
                if semantic_summary is not None else None
            ),
            "recent_turns": [
                {"sequence": turn.sequence, "role": turn.role,
                 "content": _short(turn.content, 800), "artifact_reference": turn.artifact_reference}
                for turn in reversed(turns)
            ],
            "prior_runs": [
                {"ordinal": run.ordinal, "status": run.status,
                 "summary": _short(run.terminal_summary, 800),
                 "result_reference": run.result_reference}
                for run in reversed(runs)
            ],
            "recent_operations": [
                {"id": operation.id, "key": operation.operation_key, "type": operation.operation_type,
                 "state": operation.state, "result_reference": operation.result_reference,
                 "error": _short(operation.error, 200)}
                for operation in reversed(operations)
            ],
            "recent_artifacts": [
                {"id": artifact.id, "type": artifact.artifact_type,
                 "title": artifact.title, "run_id": artifact.execution_run_id,
                 "source_event_id": artifact.source_task_event_id,
                 "uri": artifact.uri}
                for artifact in reversed(artifacts)
            ],
            "recent_questions": [
                {"id": question.id, "state": question.state,
                 "question": _short(question.question, 400),
                 "answer": _short(question.answer, 800)}
                for question in reversed(questions)
            ],
            "recent_evidence": [
                {"sequence": event.sequence, "id": event.id, "type": event.event_type}
                for event in reversed(evidence)
            ],
            "older_history_may_be_omitted": any(
                len(items) == limit for items, limit in
                ((turns, 12), (runs, 8), (operations, 12),
                 (artifacts, 12), (questions, 8), (evidence, 12))
            ),
            "latest_event_sequence": latest_event,
            "history_tool": "jarvis_task.task_history",
        }
        serialized = json.dumps(handoff, ensure_ascii=False, separators=(",", ":"))
        bundle = TaskContextBundle(
            id=str(uuid4()), task_id=task_id, execution_run_id=run_id,
            schema_version="task-context.v1",
            content_sha256=sha256(serialized.encode("utf-8")).hexdigest(),
            content_json=handoff,
        )
        session.add(bundle)
        current_run.context_reference = f"task-context:{bundle.id}"
        await session.flush()
        await TaskRepository(session).append_event(
            task_id=task_id, attempt_id=attempt_id, execution_run_id=run_id,
            event_type="task.context.pinned",
            payload_json={"context_reference": current_run.context_reference,
                          "sha256": bundle.content_sha256,
                          "evidence_cursor": latest_event},
            idempotency_key=f"task.context.pinned:{run_id}",
        )
        return serialized


async def current_run_delta(
    task_id: str, *, run_id: str, attempt_id: str, claim_token: str,
    after_sequence: int,
) -> str:
    """Bounded journal state since the pinned context for a replacement thread."""
    if after_sequence < 0:
        raise ValueError("Context evidence cursor cannot be negative")
    async with session_scope() as session:
        task = await task_service._lock_task(session, task_id)
        await task_service._assert_fence(
            session, task, attempt_id=attempt_id,
            execution_run_id=run_id, claim_token=claim_token,
        )
        total_operations = int((await session.execute(
            select(func.count()).select_from(TaskOperation).where(TaskOperation.execution_run_id == run_id)
        )).scalar_one())
        uncertain_operations = int((await session.execute(
            select(func.count()).select_from(TaskOperation).where(
                TaskOperation.execution_run_id == run_id, TaskOperation.state == "uncertain",
            )
        )).scalar_one())
        operations = list((await session.execute(
            select(TaskOperation).where(TaskOperation.execution_run_id == run_id)
            .order_by(TaskOperation.started_at.desc(), TaskOperation.id.desc()).limit(20)
        )).scalars().all())
        total_events = int((await session.execute(
            select(func.count()).select_from(TaskEvent).where(
                TaskEvent.execution_run_id == run_id, TaskEvent.sequence > after_sequence,
            )
        )).scalar_one())
        events = list((await session.execute(
            select(TaskEvent).where(
                TaskEvent.execution_run_id == run_id, TaskEvent.sequence > after_sequence,
            ).order_by(TaskEvent.sequence.desc()).limit(20)
        )).scalars().all())
        run = await session.get(TaskExecutionRun, run_id)
        user_turns = list((await session.execute(select(TaskTurn).where(
            TaskTurn.task_id == task_id, TaskTurn.execution_run_id == run_id,
            TaskTurn.id != run.input_turn_id, TaskTurn.role == "user",
        ).order_by(TaskTurn.sequence.desc()).limit(12))).scalars().all())
    delta = {
        "schema": "task-run-delta.v1",
        "run_id": run_id,
        "from_event_sequence": after_sequence,
        "user_turns": [{"sequence": turn.sequence, "content": _short(turn.content, 4000),
                        "question_id": turn.question_id} for turn in reversed(user_turns)],
        "operation_count": total_operations,
        "uncertain_operation_count": uncertain_operations,
        "operations_truncated": total_operations > len(operations),
        "recent_operations": [
            {"key": item.operation_key, "type": item.operation_type,
             "state": item.state, "intent": item.intent_json,
             "result_reference": item.result_reference,
             "result": _short(json.dumps(item.result_json, ensure_ascii=False)
                              if item.result_json is not None else None, 800),
             "error": _short(item.error, 300)}
            for item in reversed(operations)
        ],
        "event_count": total_events,
        "events_truncated": total_events > len(events),
        "recent_events": [
            {"sequence": item.sequence, "type": item.event_type,
             "payload": _short(json.dumps(item.payload_json, ensure_ascii=False)
                               if item.payload_json is not None else None, 800)}
            for item in reversed(events)
        ],
    }
    return json.dumps(delta, ensure_ascii=False, separators=(",", ":"))
