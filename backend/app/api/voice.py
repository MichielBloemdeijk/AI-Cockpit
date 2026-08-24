"""Authenticated voice endpoints for the local Jarvis POC."""
from __future__ import annotations

import re
from dataclasses import asdict

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response

from app.config import settings
from app.models.voice import (
    VoiceAnswerRequest,
    SpeechRequest,
    VoicePendingQuestion,
    VoiceConversationResponse,
    VoiceHealthResponse,
    VoiceRunControlResponse,
    VoiceStateResponse,
    VoiceTaskView,
    VoiceTurnAccepted,
    VoiceTurnRequest,
)
from app.models.conversations import ConversationEventView
from app.services.auth import require_auth
from app.services.background_tasks import cancel_task, get_task, list_tasks, snapshot
from app.services.chat_orchestrator import chat_orchestrator
from app.services.conversation_store import conversation_store
from app.services.run_supervisor import run_supervisor
from app.services.voice import InvalidVoiceAudio, VoiceDependencyUnavailable, strip_wake_word, voice_service
from app.services.voice_conversation import get_or_create_primary, reset_primary
from app.presenters.conversations import present_conversation_event

router = APIRouter(prefix="/api/voice", tags=["voice"], dependencies=[Depends(require_auth)])


def _voice_conversation_view(conversation) -> VoiceConversationResponse:
    return VoiceConversationResponse(
        conversation_id=conversation.id,
        title=conversation.title or "Jarvis",
        is_primary_voice=conversation.is_primary_voice,
        latest_sequence=0,
    )


def _strip_cloud_directive(text: str) -> tuple[str, bool]:
    normalized = " ".join(str(text or "").split()).strip()
    match = re.match(r"^(?:use|route) cloud(?: for this| for the next turn| now)?[,:;.!]?\s*", normalized, re.I)
    if not match:
        return normalized, False
    return normalized[match.end():].strip(), True


def _requested_notification(text: str) -> bool:
    return bool(re.search(r"\b(?:tell|notify|let) me (?:when|once|as soon as)\b", text, re.I))


@router.get("/health", response_model=VoiceHealthResponse)
async def voice_health() -> VoiceHealthResponse:
    return VoiceHealthResponse.model_validate(await voice_service.health())


@router.get("/conversation", response_model=VoiceConversationResponse)
async def voice_conversation() -> VoiceConversationResponse:
    conversation = await get_or_create_primary()
    response = _voice_conversation_view(conversation)
    response.latest_sequence = await conversation_store.get_latest_event_sequence(conversation.id)
    return response


@router.get("/state", response_model=VoiceStateResponse)
async def voice_state() -> VoiceStateResponse:
    conversation = await get_or_create_primary()
    # This cursor defines the start of the snapshot. Events committed while
    # projections are being read will therefore be replayed by the stream;
    # taking it last could skip an event that the projections did not see.
    snapshot_sequence = await conversation_store.get_latest_event_sequence(conversation.id)
    tasks = [VoiceTaskView.model_validate(asdict(task)) for task in await list_tasks(conversation.id, include_finished=False)]
    questions: list[VoicePendingQuestion] = []
    for run in await conversation_store.list_runs(conversation.id):
        metadata = run.metadata_json if isinstance(run.metadata_json, dict) else {}
        pending = metadata.get("pending_question")
        if run.status == "paused" and isinstance(pending, dict) and pending.get("question"):
            questions.append(VoicePendingQuestion(
                run_id=run.id,
                question_id=str(pending.get("id") or ""),
                question=str(pending["question"]),
                kind=str(pending.get("kind") or "question"),
                asked_at=str(pending.get("asked_at") or "") or None,
            ))
    response = _voice_conversation_view(conversation)
    response.latest_sequence = snapshot_sequence
    return VoiceStateResponse(
        conversation=response,
        latest_sequence=response.latest_sequence,
        tasks=tasks,
        pending_questions=questions,
    )


@router.post("/conversation/reset", response_model=VoiceConversationResponse)
async def reset_voice_conversation() -> VoiceConversationResponse:
    try:
        conversation = await reset_primary()
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _voice_conversation_view(conversation)


@router.get("/updates/{event_id}", response_model=ConversationEventView)
async def voice_update(event_id: str) -> ConversationEventView:
    event = await conversation_store.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Voice update not found")
    conversation = await conversation_store.get_conversation(event.conversation_id)
    if conversation is None or (conversation.mode_hint != "voice" and not conversation.is_primary_voice):
        raise HTTPException(status_code=404, detail="Voice update not found")
    return present_conversation_event(event)


@router.post("/turns", response_model=VoiceTurnAccepted, status_code=status.HTTP_202_ACCEPTED)
async def start_voice_turn(request: VoiceTurnRequest) -> VoiceTurnAccepted:
    content, directive_cloud = _strip_cloud_directive(request.text)
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Voice command cannot be empty")
    conversation = await get_or_create_primary()
    use_cloud = bool(request.cloud_next_turn or directive_cloud)
    model = settings.voice_cloud_model if use_cloud else settings.task_agent_model
    try:
        prepared = await chat_orchestrator.prepare_voice_turn(
            conversation_id=conversation.id,
            content=content,
            model=model,
            requested_notification=_requested_notification(content),
        )
        run_supervisor.start(prepared.run.id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return VoiceTurnAccepted(
        conversation_id=conversation.id,
        run_id=prepared.run.id,
        status="queued",
        accepted_sequence=await conversation_store.get_latest_event_sequence(conversation.id),
        model=model,
    )


@router.get("/tasks", response_model=list[VoiceTaskView])
async def voice_tasks(include_finished: bool = False) -> list[VoiceTaskView]:
    conversation = await get_or_create_primary()
    return [VoiceTaskView.model_validate(asdict(task)) for task in await list_tasks(conversation.id, include_finished=include_finished)]


@router.get("/tasks/{run_id}", response_model=VoiceTaskView)
async def voice_task(run_id: str) -> VoiceTaskView:
    conversation = await get_or_create_primary()
    task = await get_task(conversation.id, run_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Background task not found")
    return VoiceTaskView.model_validate(asdict(task))


@router.post("/tasks/{run_id}/cancel", response_model=VoiceTaskView)
async def cancel_voice_task(run_id: str) -> VoiceTaskView:
    conversation = await get_or_create_primary()
    task = await cancel_task(conversation.id, run_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Background task not found")
    return VoiceTaskView.model_validate(asdict(task))


@router.post("/runs/{run_id}/cancel", response_model=VoiceRunControlResponse)
async def cancel_voice_run(run_id: str) -> VoiceRunControlResponse:
    conversation = await get_or_create_primary()
    run = await conversation_store.get_run(run_id)
    if run is None or run.conversation_id != conversation.id:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status not in {"completed", "failed", "cancelled", "interrupted"}:
        if run_supervisor.get(run_id) is not None:
            await run_supervisor.cancel(run_id)
        else:
            from app.services.run_lifecycle import run_lifecycle

            await run_lifecycle.cancel(run_id)
    refreshed = await conversation_store.get_run(run_id)
    if refreshed is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return VoiceRunControlResponse(run_id=refreshed.id, conversation_id=refreshed.conversation_id, status=refreshed.status)


@router.post("/runs/{run_id}/answers", response_model=VoiceTaskView)
async def answer_voice_question(run_id: str, request: VoiceAnswerRequest) -> VoiceTaskView:
    conversation = await get_or_create_primary()
    run = await conversation_store.get_run(run_id)
    if run is None or run.conversation_id != conversation.id:
        raise HTTPException(status_code=404, detail="Run not found")
    metadata = run.metadata_json if isinstance(run.metadata_json, dict) else {}
    pending = metadata.get("pending_question")
    if run.status != "paused" or not isinstance(pending, dict):
        raise HTTPException(status_code=409, detail="Run is not waiting for an answer")
    answer = " ".join(request.answer.split()).strip()
    metadata = dict(metadata)
    metadata["pending_question"] = None
    metadata["task_status"] = "queued"
    metadata["current_action"] = "Queued after user answer"
    resumed = await conversation_store.answer_paused_run(
        conversation.id,
        run_id=run.id,
        answer=answer,
        question_id=str(pending.get("id") or "") or None,
        metadata_json=metadata,
        branch_key=str(metadata.get("branch_key") or "main"),
    )
    if resumed is None:
        raise HTTPException(status_code=409, detail="Run is no longer waiting for an answer")
    run_supervisor.start(run.id)
    refreshed = await conversation_store.get_run(run.id)
    if refreshed is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if metadata.get("execution_mode") == "background":
        task = await get_task(conversation.id, run.id)
        if task is not None:
            return VoiceTaskView.model_validate(asdict(task))
    return VoiceTaskView(
        run_id=refreshed.id,
        conversation_id=refreshed.conversation_id,
        parent_run_id=refreshed.parent_run_id,
        title=str(metadata.get("title") or "Jarvis run"),
        goal=str(metadata.get("goal") or ""),
        status=refreshed.status,
        priority=str(metadata.get("task_priority") or "normal"),
        notify_policy=str(metadata.get("notify_policy") or "important"),
        requested_notification=bool(metadata.get("requested_notification")),
        summary=None,
        error=None,
        started_at=refreshed.started_at,
        finished_at=refreshed.finished_at,
    )


@router.post("/transcriptions")
async def transcribe_voice_segment(file: UploadFile = File(...)):
    audio = await file.read()
    try:
        result = await voice_service.transcribe(audio)
    except InvalidVoiceAudio as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except VoiceDependencyUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    command, wake_word_match = strip_wake_word(result.transcript)
    return {
        "transcript": result.transcript,
        "command": command,
        "wake_word_match": wake_word_match,
        "duration_seconds": result.duration_seconds,
        "inference_ms": result.inference_ms,
    }


@router.post("/speech")
async def synthesize_voice(request: SpeechRequest) -> Response:
    try:
        audio, inference_ms = await voice_service.synthesize(
            request.text,
            voice=request.voice,
            speed=request.speed,
        )
    except VoiceDependencyUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return Response(
        content=audio,
        media_type="audio/wav",
        headers={"X-Inference-Ms": str(inference_ms)},
    )
