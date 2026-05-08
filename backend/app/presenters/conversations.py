from __future__ import annotations

from app.models.conversations import (
    ConversationArtifactView,
    ConversationBranchView,
    ConversationDetail,
    ConversationEventView,
    ConversationEventsResponse,
    ConversationMessageView,
    ConversationSummary,
    ConversationWorkspaceView,
    MemoryItemView,
    WorkspaceFileView,
)


def present_conversation_message(record) -> ConversationMessageView:
    return ConversationMessageView(
        id=record.id,
        run_id=record.run_id,
        source_event_id=record.source_event_id,
        role=record.role,
        author_label=record.author_label,
        content=record.content,
        content_format=record.content_format,
        is_final=record.is_final,
        created_at=record.created_at,
    )


def present_conversation_event(record) -> ConversationEventView:
    return ConversationEventView(
        id=record.id,
        run_id=record.run_id,
        sequence=record.sequence,
        branch_key=record.branch_key,
        parent_event_id=record.parent_event_id,
        actor_kind=record.actor_kind,
        event_type=record.event_type,
        created_at=record.created_at,
        schema_version=record.schema_version,
        payload_json=record.payload_json,
    )


def present_conversation_artifact(record) -> ConversationArtifactView:
    return ConversationArtifactView(
        id=record.id,
        run_id=record.run_id,
        source_event_id=record.source_event_id,
        artifact_type=record.artifact_type,
        mime_type=record.mime_type,
        content_text=record.content_text,
        content_json=record.content_json,
        created_at=record.created_at,
    )


def present_memory_item(record) -> MemoryItemView:
    return MemoryItemView(
        id=record.id,
        scope=record.scope,
        kind=record.kind,
        title=record.title,
        content=record.content,
        status=record.status,
        confidence=record.confidence,
        source_conversation_id=record.source_conversation_id,
        source_event_id=record.source_event_id,
        knowledge_path=record.knowledge_path,
        created_at=record.created_at,
        reviewed_at=record.reviewed_at,
        deleted_at=record.deleted_at,
    )


def present_conversation_branch(record) -> ConversationBranchView:
    return ConversationBranchView(
        branch_key=record.branch_key,
        label=record.label,
        parent_branch_key=record.parent_branch_key,
        branched_from_message_id=record.branched_from_message_id,
        created_at=record.created_at,
    )


def present_conversation_summary(
    conversation_record,
    *,
    session_metadata,
    last_message_preview: str | None,
    latest_run_status: str | None,
) -> ConversationSummary:
    return ConversationSummary(
        id=conversation_record.id,
        title=conversation_record.title,
        mode_hint=conversation_record.mode_hint,
        session_metadata=session_metadata,
        workspace_path=conversation_record.workspace_path,
        created_at=conversation_record.created_at,
        updated_at=conversation_record.updated_at,
        archived_at=conversation_record.archived_at,
        last_message_preview=last_message_preview,
        latest_run_status=latest_run_status,
    )


def present_conversation_workspace(path: str, files: list[dict[str, str | int | None]]) -> ConversationWorkspaceView:
    return ConversationWorkspaceView(
        path=path,
        files=[WorkspaceFileView.model_validate(item) for item in files],
    )


def present_conversation_detail(
    summary: ConversationSummary,
    *,
    active_branch_key: str,
    branches: list,
    workspace_path: str | None,
    workspace_files: list[dict[str, str | int | None]],
    messages: list,
) -> ConversationDetail:
    return ConversationDetail(
        **summary.model_dump(),
        active_branch_key=active_branch_key,
        branches=[present_conversation_branch(branch) for branch in branches],
        workspace=None if not workspace_path else present_conversation_workspace(workspace_path, workspace_files),
        messages=[present_conversation_message(message) for message in messages],
    )


def present_conversation_events(
    *,
    conversation_id: str,
    active_branch_key: str,
    events: list,
    artifacts: list,
) -> ConversationEventsResponse:
    return ConversationEventsResponse(
        conversation_id=conversation_id,
        active_branch_key=active_branch_key,
        events=[present_conversation_event(event) for event in events],
        artifacts=[present_conversation_artifact(artifact) for artifact in artifacts],
    )