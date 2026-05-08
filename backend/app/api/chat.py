"""Chat endpoints: single-model streaming + council mode."""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.models.chat import ChatRequest, ChatResponse, CouncilResponse
from app.models.settings import ChatSettingsUpdateRequest, ConversationSessionMetadata
from app.services.auth import require_auth
from app.services.chat_settings import chat_settings_service
from app.services.chat_orchestrator import chat_orchestrator
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"], dependencies=[Depends(require_auth)])


@router.post("", include_in_schema=False)
@router.post("/")
async def chat(req: ChatRequest):
    """
    Chat endpoint.
    - council_mode=False + stream=True  → SSE stream of text chunks
    - council_mode=False + stream=False → single JSON response
    - council_mode=True                 → JSON with all model responses + synthesized
    """
    if req.council_mode:
        response = await chat_orchestrator.run_council_response(
            messages=req.messages,
            conversation_id=req.conversation_id,
            session_metadata=req.session_metadata,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            branch_key=req.branch_key,
        )
        return CouncilResponse(
            conversation_id=response["conversation_id"],
            run_id=response["run_id"],
            model_responses=response["model_responses"],
            synthesized=response["synthesized"],
            synthesizer_model=response["synthesizer_model"],
            synthesizer_usage=response.get("synthesizer_usage"),
            total_usage=response.get("total_usage"),
        )

    model = req.model

    existing_conversation_id, resolved_session_metadata = await chat_orchestrator._resolve_session_metadata(
        conversation_id=req.conversation_id,
        session_metadata=req.session_metadata,
        council_mode=False,
        model=model,
    )
    if req.stream:
        return StreamingResponse(
            _sse_stream(req, model),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    response = await chat_orchestrator.run_single_response(
        messages=req.messages,
        conversation_id=req.conversation_id,
        session_metadata=req.session_metadata,
        model=model,
        temperature=req.temperature,
        max_tokens=req.max_tokens,
        branch_key=req.branch_key,
    )
    return ChatResponse.model_validate(response)


async def _sse_stream(req: ChatRequest, model: str) -> AsyncIterator[str]:
    """Yield SSE-formatted chunks from the LLM stream."""
    try:
        envelope = await chat_orchestrator.stream_single_response(
            messages=req.messages,
            conversation_id=req.conversation_id,
            session_metadata=req.session_metadata,
            model=model,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            branch_key=req.branch_key,
        )
        async for event in envelope.events:
            data = json.dumps(event)
            yield f"data: {data}\n\n"
    except Exception as e:
        logger.error("SSE stream error: %s", e)
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"


@router.get("/models")
async def list_models():
    """Return configured council models."""
    return {
        "council_models": settings.council_model_list,
        "synthesizer_model": settings.synthesizer_model,
    }


@router.get("/settings")
async def get_chat_settings():
    return await chat_settings_service.get_settings_response()


@router.put("/settings")
async def update_chat_settings(request: ChatSettingsUpdateRequest):
    return await chat_settings_service.update_settings(
        defaults=request.defaults,
        task_agent_model=request.task_agent_model,
    )
