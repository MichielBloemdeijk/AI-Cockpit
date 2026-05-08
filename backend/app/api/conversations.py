"""Conversation-first APIs for transcript history and trace access."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.config import settings
from app.models.chat import Message
from app.models.conversations import (
    ConversationArtifactView,
    ConversationBranchResendRequest,
    ConversationBranchResendResponse,
    ConversationBranchView,
    ConversationCreateResponse,
    ConversationCreateRequest,
    ConversationDetail,
    ConversationEventView,
    ConversationEventsResponse,
    ConversationMessageCreateRequest,
    ConversationMessageView,
    ConversationSummary,
    ConversationToolRequest,
    ConversationToolResponse,
    ConversationTurnResponse,
    ConversationToolResult,
    ConversationWorkspaceView,
    MemoryApprovalResponse,
    MemoryExtractionRequest,
    MemoryItemView,
    WorkspaceFileView,
)
from app.models.settings import ConversationSessionMetadata
from app.presenters.conversations import (
    present_conversation_branch,
    present_conversation_message,
    present_memory_item,
)
from app.services.auth import require_auth
from app.services.agent_tools import build_tool_context, execute_chat_tool
from app.services.chat_settings import chat_settings_service
from app.services.chat_orchestrator import chat_orchestrator
from app.services.conversation_read_model import conversation_read_model
from app.services.conversation_store import conversation_store
from app.services.conversation_workspace import MAIN_BRANCH_KEY
from app.services.knowledge_extractor import extract_memory_proposals
from app.services.knowledge_memory import write_approved_memory
from app.services.memory_review_store import memory_review_store

router = APIRouter(prefix="/api/conversations", tags=["conversations"], dependencies=[Depends(require_auth)])


@router.get("", response_model=list[ConversationSummary], include_in_schema=False)
@router.get("/", response_model=list[ConversationSummary])
async def list_conversations(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    include_archived: bool = Query(default=False),
):
    conversations = await conversation_store.list_conversations(
        limit=limit,
        offset=offset,
        include_archived=include_archived,
    )
    return [await conversation_read_model.present_summary(conversation) for conversation in conversations]


@router.post("", status_code=status.HTTP_201_CREATED, include_in_schema=False)
@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_conversation(req: ConversationCreateRequest) -> ConversationCreateResponse:
    if req.initial_message:
        initial_messages = [Message(role="user", content=req.initial_message)]
        _, resolved_session_metadata = await chat_orchestrator._resolve_session_metadata(
            conversation_id=None,
            session_metadata=req.session_metadata,
            council_mode=False,
            model=req.model,
        )

        result = await chat_orchestrator.run_council_response(
            messages=initial_messages,
            conversation_id=None,
            session_metadata=resolved_session_metadata,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
        ) if resolved_session_metadata.mode == "council" else await chat_orchestrator.run_single_response(
            messages=initial_messages,
            conversation_id=None,
            session_metadata=resolved_session_metadata,
            model=resolved_session_metadata.single_model,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
        )
        conversation = await conversation_store.get_conversation(result.conversation_id if hasattr(result, "conversation_id") else result["conversation_id"])
        if conversation is None:
            raise HTTPException(status_code=500, detail="Conversation creation failed")
        return ConversationCreateResponse(
            conversation=await conversation_read_model.present_summary(conversation),
            run_id=result.run_id if hasattr(result, "run_id") else result["run_id"],
        )

    session_metadata = req.session_metadata or await chat_settings_service.get_defaults()
    conversation = await conversation_store.create_conversation(
        title=req.title,
        mode_hint=req.mode_hint or session_metadata.mode,
        session_metadata_json=session_metadata.model_dump(),
    )
    return ConversationCreateResponse(conversation=await conversation_read_model.present_summary(conversation), run_id=None)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: str, branch_key: str = Query(default=MAIN_BRANCH_KEY)):
    detail = await conversation_read_model.get_conversation_detail(conversation_id, branch_key=branch_key)
    if detail is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return detail


@router.post("/{conversation_id}/archive", response_model=ConversationSummary)
async def archive_conversation(conversation_id: str):
    conversation = await conversation_store.archive_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await conversation_read_model.present_summary(conversation)


@router.post("/{conversation_id}/unarchive", response_model=ConversationSummary)
async def unarchive_conversation(conversation_id: str):
    conversation = await conversation_store.unarchive_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await conversation_read_model.present_summary(conversation)


@router.get("/{conversation_id}/events", response_model=ConversationEventsResponse)
async def get_conversation_events(conversation_id: str, branch_key: str = Query(default=MAIN_BRANCH_KEY)):
    response = await conversation_read_model.get_conversation_events(conversation_id, branch_key=branch_key)
    if response is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return response


@router.post("/{conversation_id}/messages", response_model=ConversationTurnResponse)
async def create_conversation_message(conversation_id: str, req: ConversationMessageCreateRequest):
    conversation = await conversation_store.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    user_messages = [Message(role="user", content=req.content)]
    _, session_metadata = await chat_orchestrator._resolve_session_metadata(
        conversation_id=conversation_id,
        session_metadata=req.session_metadata,
        council_mode=False,
        model=req.model,
    )

    if session_metadata.mode == "council":
        result = await chat_orchestrator.run_council_response(
            messages=user_messages,
            conversation_id=conversation_id,
            session_metadata=session_metadata,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            branch_key=req.branch_key,
        )
        final_message = (await conversation_read_model.list_messages_for_branch(
            conversation_id,
            branch_key=req.branch_key,
            final_only=True,
        ))[-1]
        if final_message is None:
            raise HTTPException(status_code=500, detail="Conversation message was not persisted")
        return ConversationTurnResponse(
            conversation_id=conversation_id,
            run_id=result["run_id"],
            message=present_conversation_message(final_message),
            council_data={
                "model_responses": result["model_responses"],
                "synthesized": result["synthesized"],
                "synthesizer_model": result["synthesizer_model"],
                "synthesizer_usage": result.get("synthesizer_usage"),
                "total_usage": result.get("total_usage"),
            },
        )

    result = await chat_orchestrator.run_single_response(
        messages=user_messages,
        conversation_id=conversation_id,
        session_metadata=session_metadata,
        model=session_metadata.single_model,
        temperature=req.temperature,
        max_tokens=req.max_tokens,
        branch_key=req.branch_key,
    )
    if result.error:
        raise HTTPException(status_code=502, detail=result.error)
    final_message = (await conversation_read_model.list_messages_for_branch(
        conversation_id,
        branch_key=req.branch_key,
        final_only=True,
    ))[-1]
    if final_message is None or final_message.role != "assistant":
        raise HTTPException(status_code=500, detail="Conversation message was not persisted")
    return ConversationTurnResponse(
        conversation_id=conversation_id,
        run_id=result.run_id,
        message=present_conversation_message(final_message),
    )


def _tool_user_message(req: ConversationToolRequest) -> str:
    if req.tool == "workspace_search":
        return f"Search workspace for: {req.query or ''}".strip()
    if req.tool == "python_execution":
        return "Run Python code:\n```python\n" + (req.code or "") + "\n```"
    return f"Run tool: {req.tool}"


@router.post("/{conversation_id}/tools", response_model=ConversationToolResponse)
async def execute_conversation_tool(conversation_id: str, req: ConversationToolRequest):
    conversation = await conversation_store.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    defaults = await chat_settings_service.get_defaults()
    session_metadata = chat_settings_service.resolve_conversation_metadata(conversation, defaults)
    run = await conversation_store.start_run(
        conversation_id,
        kind="tool",
        metadata_json={
            "tool": req.tool,
            "session_metadata": session_metadata.model_dump(),
        },
    )
    await conversation_store.append_user_message(
        conversation_id,
        run_id=run.id,
        content=_tool_user_message(req),
        branch_key=req.branch_key,
    )
    started_event = await conversation_store.append_event(
        conversation_id,
        run_id=run.id,
        actor_kind="system",
        event_type=f"tool.{req.tool}.started",
        payload_json={
            "tool": req.tool,
            "query": req.query,
            "working_directory": req.working_directory,
            "goal": req.goal,
            "title": req.title,
            "app_slug": req.app_slug,
        },
        branch_key=req.branch_key,
    )

    try:
        result = await execute_chat_tool(
            context=build_tool_context(conversation_id, conversation.workspace_path or ".cockpit/conversations"),
            tool=req.tool,
            query=req.query,
            code=req.code,
            working_directory=req.working_directory,
        )
    except Exception as exc:
        error_detail = str(exc) or repr(exc)
        await conversation_store.mark_run_failed(run.id, error_detail)
        raise HTTPException(status_code=400, detail=error_detail) from exc

    completed_event, message = await conversation_store.complete_message(
        conversation_id,
        run_id=run.id,
        role="assistant",
        content=result.output,
        actor_kind="assistant",
        event_type=f"tool.{req.tool}.completed",
        author_label=req.tool,
        payload_json={
            "tool": req.tool,
            "metadata": result.metadata,
            "output": result.output,
        },
        branch_key=req.branch_key,
    )
    await conversation_store.attach_artifact(
        conversation_id,
        run_id=run.id,
        source_event_id=completed_event.id,
        artifact_type=f"tool.{req.tool}.result",
        mime_type="application/json",
        content_json={
            "tool": req.tool,
            "metadata": result.metadata,
            "output": result.output,
            "started_event_id": started_event.id,
        },
    )
    await conversation_store.mark_run_completed(run.id)

    return ConversationToolResponse(
        conversation_id=conversation_id,
        run_id=run.id,
        message=present_conversation_message(message),
        result=result if isinstance(result, ConversationToolResult) else ConversationToolResult(
            tool=result.tool,
            output=result.output,
            metadata=result.metadata,
        ),
    )


@router.get("/{conversation_id}/memory-items", response_model=list[MemoryItemView])
async def list_memory_items(conversation_id: str):
    conversation = await conversation_store.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    memory_items = await memory_review_store.list_memory_items(conversation_id)
    return [present_memory_item(item) for item in memory_items]


@router.post("/{conversation_id}/memory-items/extract", response_model=list[MemoryItemView])
async def extract_memory_items(conversation_id: str, req: MemoryExtractionRequest):
    conversation = await conversation_store.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = await conversation_store.list_messages(conversation_id, final_only=True)
    message_by_id = {message.id: message for message in messages}
    if req.start_message_id and req.start_message_id not in message_by_id:
        raise HTTPException(status_code=400, detail="Invalid start_message_id")
    if req.end_message_id and req.end_message_id not in message_by_id:
        raise HTTPException(status_code=400, detail="Invalid end_message_id")

    proposals = req.proposals
    if not proposals:
        proposals = [
            {
                "scope": proposal.scope,
                "kind": proposal.kind,
                "title": proposal.title,
                "content": proposal.content,
                "confidence": proposal.confidence,
                "source_event_id": proposal.source_event_id,
            }
            for proposal in extract_memory_proposals(
                messages,
                start_message_id=req.start_message_id,
                end_message_id=req.end_message_id,
            )
        ]

    if not proposals:
        return []

    memory_items = []
    for proposal in proposals:
        memory_item = await memory_review_store.create_memory_item(
            scope=proposal["scope"] if isinstance(proposal, dict) else proposal.scope,
            kind=proposal["kind"] if isinstance(proposal, dict) else proposal.kind,
            title=proposal["title"] if isinstance(proposal, dict) else proposal.title,
            content=proposal["content"] if isinstance(proposal, dict) else proposal.content,
            confidence=proposal["confidence"] if isinstance(proposal, dict) else proposal.confidence,
            source_conversation_id=conversation_id,
            source_event_id=(proposal.get("source_event_id") if isinstance(proposal, dict) else None),
        )
        memory_items.append(present_memory_item(memory_item))

    return memory_items


@router.post("/{conversation_id}/memory-items/{memory_item_id}/approve", response_model=MemoryApprovalResponse)
async def approve_memory_item(conversation_id: str, memory_item_id: str):
    conversation = await conversation_store.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    memory_item = await memory_review_store.get_memory_item(memory_item_id)
    if memory_item is None or memory_item.source_conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="Memory item not found")

    if memory_item.status != "approved":
        knowledge_path = write_approved_memory(memory_item)
        memory_item = await memory_review_store.approve_memory_item(memory_item_id, knowledge_path=str(knowledge_path))
    else:
        knowledge_path = write_approved_memory(memory_item)

    return MemoryApprovalResponse(
        memory_item=present_memory_item(memory_item),
        knowledge_path=str(knowledge_path),
    )


@router.post("/{conversation_id}/memory-items/{memory_item_id}/reject", response_model=MemoryItemView)
async def reject_memory_item(conversation_id: str, memory_item_id: str):
    conversation = await conversation_store.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    memory_item = await memory_review_store.get_memory_item(memory_item_id)
    if memory_item is None or memory_item.source_conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="Memory item not found")

    rejected = await memory_review_store.reject_memory_item(memory_item_id)
    return present_memory_item(rejected)


@router.post("/{conversation_id}/branches/resend", response_model=ConversationBranchResendResponse)
async def branch_resend(conversation_id: str, req: ConversationBranchResendRequest):
    conversation = await conversation_store.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    source_message = await conversation_store.get_message(req.source_message_id)
    if source_message is None or source_message.conversation_id != conversation_id or source_message.role != "user":
        raise HTTPException(status_code=400, detail="source_message_id must reference a user message in this conversation")

    branch = await conversation_store.create_branch(
        conversation_id,
        parent_branch_key=req.parent_branch_key,
        branched_from_message_id=req.source_message_id,
        label=req.label,
    )
    existing_messages = await conversation_read_model.list_messages_for_branch(
        conversation_id,
        branch_key=req.parent_branch_key,
        final_only=True,
    )
    cutoff = next(
        (index for index, message in enumerate(existing_messages) if message.id == req.source_message_id),
        len(existing_messages),
    )
    history = [
        Message(role=message.role, content=message.content)
        for message in existing_messages[:cutoff]
        if message.role in {"system", "user", "assistant"}
    ]
    history.append(Message(role="user", content=req.content))

    _, session_metadata = await chat_orchestrator._resolve_session_metadata(
        conversation_id=conversation_id,
        session_metadata=None,
        council_mode=False,
        model=None,
    )

    if session_metadata.mode == "council":
        result = await chat_orchestrator.run_council_response(
            messages=history,
            conversation_id=conversation_id,
            session_metadata=session_metadata,
            temperature=0.7,
            max_tokens=4096,
            branch_key=branch.branch_key,
            parent_event_id=source_message.source_event_id,
        )
        final_message = (await conversation_read_model.list_messages_for_branch(
            conversation_id,
            branch_key=branch.branch_key,
            final_only=True,
        ))[-1]
        return ConversationBranchResendResponse(
            conversation_id=conversation_id,
            branch=present_conversation_branch(branch),
            run_id=result["run_id"],
            message=present_conversation_message(final_message),
            council_data={
                "model_responses": result["model_responses"],
                "synthesized": result["synthesized"],
                "synthesizer_model": result["synthesizer_model"],
                "synthesizer_usage": result.get("synthesizer_usage"),
                "total_usage": result.get("total_usage"),
            },
        )

    result = await chat_orchestrator.run_single_response(
        messages=history,
        conversation_id=conversation_id,
        session_metadata=session_metadata,
        model=session_metadata.single_model,
        temperature=0.7,
        max_tokens=4096,
        branch_key=branch.branch_key,
        parent_event_id=source_message.source_event_id,
    )
    final_message = (await conversation_read_model.list_messages_for_branch(
        conversation_id,
        branch_key=branch.branch_key,
        final_only=True,
    ))[-1]
    return ConversationBranchResendResponse(
        conversation_id=conversation_id,
        branch=present_conversation_branch(branch),
        run_id=result.run_id,
        message=present_conversation_message(final_message),
        council_data=None,
    )