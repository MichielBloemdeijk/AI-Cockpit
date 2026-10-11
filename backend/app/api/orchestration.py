"""Background diagnostics and explicit audited recovery, independent of inference."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.config import settings
from app.db.session import session_scope
from app.db.tables import JarvisLiveEvent, OrchestrationAction, OrchestrationDecision, TaskProjectionCheckpoint
from app.services.auth import require_auth
from app.services.orchestration_authority import StaleDecision
from app.services.task_orchestration import CURSOR_KEY, now, task_orchestration_service
from app.services.orchestration_telemetry import age_ms, snapshot

router = APIRouter(prefix="/api/orchestration", tags=["orchestration"], dependencies=[Depends(require_auth)])


class ResumptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_key: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=2000)


async def _recover(decision_id: str, **kwargs) -> dict:
    if not settings.task_orchestration_enabled:
        raise HTTPException(status_code=409, detail="Background orchestration is disabled")
    try:
        return await task_orchestration_service.recover(decision_id, **kwargs)
    except (StaleDecision, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{decision_id}/inspect")
async def inspect_decision(decision_id: str) -> dict:
    return await _recover(decision_id)


@router.post("/{decision_id}/resume")
async def resume_decision(decision_id: str, request: ResumptionRequest) -> dict:
    # Does not fund an account, enable live execution, or alter auth/model settings.
    return await _recover(decision_id, resume=True, **request.model_dump())


@router.get("")
async def status(limit: int = Query(default=50, ge=1, le=100)) -> dict:
    async with session_scope() as session:
        checkpoint = await session.get(TaskProjectionCheckpoint, CURSOR_KEY)
        counts = dict((await session.execute(select(OrchestrationDecision.status, func.count())
                                            .group_by(OrchestrationDecision.status))).all())
        pending = list((await session.execute(select(OrchestrationDecision).where(
            OrchestrationDecision.status.in_(["pending", "running", "deferred"]),
        ).order_by(OrchestrationDecision.source_sequence).limit(limit))).scalars().all())
        latest = int((await session.execute(select(func.max(JarvisLiveEvent.sequence)))).scalar_one_or_none() or 0)
        cursor = checkpoint.last_sequence if checkpoint else 0
        timestamp = now()
        oldest_pending = (await session.execute(select(func.min(OrchestrationDecision.created_at)).where(
            OrchestrationDecision.status == "pending",
        ))).scalar_one_or_none()
        return {
            "enabled": settings.task_orchestration_enabled,
            "live_enabled": settings.task_orchestration_live_enabled,
            "feedback_cursor": cursor, "event_sequence_lag": latest - cursor,
            "decision_counts": counts,
            "oldest_pending_age_seconds": age_ms(oldest_pending) / 1000 if oldest_pending else None,
            "telemetry": await snapshot(session, limit=limit),
            "committed_action_count": int((await session.execute(select(func.count()).select_from(OrchestrationAction))).scalar_one()),
            "decisions": [{"id": row.id, "task_id": row.task_id, "run_id": row.execution_run_id,
                           "question_id": row.question_id, "status": row.status,
                           "source_event_ids": row.source_event_ids_json, "claim_generation": row.claim_generation,
                           "model": row.model, "effort": row.effort, "last_error": row.last_error,
                           "age_seconds": max(0, (timestamp - row.created_at.replace(tzinfo=timestamp.tzinfo)).total_seconds()),
                           "due_at": row.due_at.isoformat() if row.due_at else None,
                           "native_binding": row.native_binding_json} for row in pending],
        }
