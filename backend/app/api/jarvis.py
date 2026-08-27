"""Jarvis daily-log state and durable typed-turn endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.models.jarvis import (
    JarvisFreshLogRequest,
    JarvisFreshLogResponse,
    JarvisStateResponse,
    JarvisTextTurnRequest,
    JarvisTurnAcceptedResponse,
)
from app.presenters.conversations import present_conversation_message
from app.services.auth import require_auth
from app.services.conversation_read_model import conversation_read_model
from app.services.jarvis_daily import jarvis_daily_service


router = APIRouter(prefix="/api/jarvis", tags=["jarvis"], dependencies=[Depends(require_auth)])


@router.get("/state", response_model=JarvisStateResponse)
async def get_jarvis_state() -> JarvisStateResponse:
    state = await jarvis_daily_service.get_state()
    detail = None
    if state.current_log is not None:
        detail = await conversation_read_model.get_conversation_detail(state.current_log.id)
    return JarvisStateResponse(
        operational_day=state.operational_day,
        state_revision=state.state_revision,
        current_log=detail,
    )


@router.post(
    "/turns/text",
    response_model=JarvisTurnAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def accept_jarvis_text_turn(request: JarvisTextTurnRequest) -> JarvisTurnAcceptedResponse:
    try:
        accepted = await jarvis_daily_service.accept_text_turn(
            request.content,
            idempotency_key=request.idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JarvisTurnAcceptedResponse(
        conversation=await conversation_read_model.present_summary(accepted.conversation),
        message=present_conversation_message(accepted.message),
        event_sequence=accepted.event_sequence,
    )


@router.post("/logs/new", response_model=JarvisFreshLogResponse)
async def start_fresh_jarvis_log(request: JarvisFreshLogRequest) -> JarvisFreshLogResponse:
    current_log = await jarvis_daily_service.start_fresh_log(
        idempotency_key=request.idempotency_key,
    )
    return JarvisFreshLogResponse(
        current_log=await conversation_read_model.present_summary(current_log)
    )
