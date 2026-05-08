from __future__ import annotations

from app.presenters.conversations import (
    present_conversation_detail,
    present_conversation_events,
    present_conversation_summary,
)
from app.services.chat_settings import chat_settings_service
from app.services.conversation_store import ArtifactRecord, BranchRecord, EventRecord, MessageRecord, RunRecord, conversation_store
from app.services.conversation_workspace import MAIN_BRANCH_KEY


class ConversationReadModel:
    @staticmethod
    def _message_branch_key(message: MessageRecord, event_by_id: dict[str, EventRecord]) -> str:
        if not message.source_event_id:
            return MAIN_BRANCH_KEY
        event = event_by_id.get(message.source_event_id)
        if event is None or not event.branch_key:
            return MAIN_BRANCH_KEY
        return event.branch_key

    @staticmethod
    def _event_branch_key(event: EventRecord) -> str:
        return event.branch_key or MAIN_BRANCH_KEY

    def _build_branch_messages(
        self,
        *,
        branch_key: str,
        messages: list[MessageRecord],
        event_by_id: dict[str, EventRecord],
        branch_by_key: dict[str, BranchRecord],
    ) -> list[MessageRecord]:
        normalized_branch_key = branch_key or MAIN_BRANCH_KEY
        if normalized_branch_key == MAIN_BRANCH_KEY or normalized_branch_key not in branch_by_key:
            return [
                message for message in messages
                if self._message_branch_key(message, event_by_id) == MAIN_BRANCH_KEY
            ]

        branch = branch_by_key[normalized_branch_key]
        parent_messages = self._build_branch_messages(
            branch_key=branch.parent_branch_key or MAIN_BRANCH_KEY,
            messages=messages,
            event_by_id=event_by_id,
            branch_by_key=branch_by_key,
        )
        if branch.branched_from_message_id:
            cutoff = next(
                (index for index, message in enumerate(parent_messages) if message.id == branch.branched_from_message_id),
                len(parent_messages),
            )
            parent_messages = parent_messages[:cutoff]

        branch_messages = [
            message for message in messages
            if self._message_branch_key(message, event_by_id) == normalized_branch_key
        ]
        return parent_messages + branch_messages

    def _build_branch_events(
        self,
        *,
        branch_key: str,
        events: list[EventRecord],
        messages_by_id: dict[str, MessageRecord],
        branch_by_key: dict[str, BranchRecord],
    ) -> list[EventRecord]:
        normalized_branch_key = branch_key or MAIN_BRANCH_KEY
        ordered_events = sorted(events, key=lambda item: (item.sequence, item.created_at, item.id))
        if normalized_branch_key == MAIN_BRANCH_KEY or normalized_branch_key not in branch_by_key:
            return [event for event in ordered_events if self._event_branch_key(event) == MAIN_BRANCH_KEY]

        branch = branch_by_key[normalized_branch_key]
        parent_events = self._build_branch_events(
            branch_key=branch.parent_branch_key or MAIN_BRANCH_KEY,
            events=ordered_events,
            messages_by_id=messages_by_id,
            branch_by_key=branch_by_key,
        )

        if branch.branched_from_message_id:
            branched_message = messages_by_id.get(branch.branched_from_message_id)
            cutoff_sequence = None
            if branched_message and branched_message.source_event_id:
                cutoff_event = next((event for event in ordered_events if event.id == branched_message.source_event_id), None)
                if cutoff_event is not None:
                    cutoff_sequence = cutoff_event.sequence
            if cutoff_sequence is not None:
                parent_events = [event for event in parent_events if event.sequence < cutoff_sequence]

        branch_events = [
            event for event in ordered_events
            if self._event_branch_key(event) == normalized_branch_key
        ]
        return parent_events + branch_events

    def _build_branch_artifacts(
        self,
        *,
        artifacts: list[ArtifactRecord],
        visible_events: list[EventRecord],
        runs_by_id: dict[str, RunRecord],
        branch_key: str,
        branch_by_key: dict[str, BranchRecord],
    ) -> list[ArtifactRecord]:
        visible_event_ids = {event.id for event in visible_events}
        visible_run_ids = {event.run_id for event in visible_events if event.run_id}
        visible_branch_chain = {branch_key or MAIN_BRANCH_KEY}
        current_branch_key = branch_key or MAIN_BRANCH_KEY
        while current_branch_key in branch_by_key:
            parent_branch_key = branch_by_key[current_branch_key].parent_branch_key
            if not parent_branch_key:
                break
            visible_branch_chain.add(parent_branch_key)
            current_branch_key = parent_branch_key

        filtered: list[ArtifactRecord] = []
        for artifact in sorted(artifacts, key=lambda item: (item.created_at, item.id)):
            if artifact.source_event_id:
                if artifact.source_event_id in visible_event_ids:
                    filtered.append(artifact)
                continue
            if not artifact.run_id or artifact.run_id not in visible_run_ids:
                continue
            run = runs_by_id.get(artifact.run_id)
            run_branch_key = MAIN_BRANCH_KEY
            if run and isinstance(run.metadata_json, dict):
                run_branch_key = str(run.metadata_json.get("branch_key") or MAIN_BRANCH_KEY)
            if run_branch_key in visible_branch_chain:
                filtered.append(artifact)
        return filtered

    async def list_messages_for_branch(
        self,
        conversation_id: str,
        *,
        branch_key: str = MAIN_BRANCH_KEY,
        final_only: bool = False,
    ) -> list[MessageRecord]:
        messages = await conversation_store.list_messages(conversation_id, final_only=final_only)
        events = await conversation_store.list_events(conversation_id, limit=5000)
        branches = await conversation_store.list_branches(conversation_id)
        event_by_id = {event.id: event for event in events}
        branch_by_key = {branch.branch_key: branch for branch in branches}
        return self._build_branch_messages(
            branch_key=branch_key,
            messages=messages,
            event_by_id=event_by_id,
            branch_by_key=branch_by_key,
        )

    async def list_events_for_branch(
        self,
        conversation_id: str,
        *,
        branch_key: str = MAIN_BRANCH_KEY,
        limit: int = 5000,
    ) -> list[EventRecord]:
        events = await conversation_store.list_events(conversation_id, limit=limit)
        messages = await conversation_store.list_messages(conversation_id, final_only=False)
        branches = await conversation_store.list_branches(conversation_id)
        messages_by_id = {message.id: message for message in messages}
        branch_by_key = {branch.branch_key: branch for branch in branches}
        return self._build_branch_events(
            branch_key=branch_key,
            events=events,
            messages_by_id=messages_by_id,
            branch_by_key=branch_by_key,
        )

    async def list_artifacts_for_branch(
        self,
        conversation_id: str,
        *,
        branch_key: str = MAIN_BRANCH_KEY,
    ) -> list[ArtifactRecord]:
        visible_events = await self.list_events_for_branch(conversation_id, branch_key=branch_key, limit=5000)
        artifacts = await conversation_store.list_artifacts(conversation_id)
        runs = await conversation_store.list_runs(conversation_id)
        branches = await conversation_store.list_branches(conversation_id)
        runs_by_id = {run.id: run for run in runs}
        branch_by_key = {branch.branch_key: branch for branch in branches}
        return self._build_branch_artifacts(
            artifacts=artifacts,
            visible_events=visible_events,
            runs_by_id=runs_by_id,
            branch_key=branch_key,
            branch_by_key=branch_by_key,
        )

    async def get_conversation_summary(self, conversation_id: str):
        conversation = await conversation_store.get_conversation(conversation_id)
        if conversation is None:
            return None
        return await self.present_summary(conversation)

    async def present_summary(self, conversation_record):
        last_message = await conversation_store.get_last_message(conversation_record.id)
        latest_run = await conversation_store.get_latest_run(conversation_record.id)
        preview = None if last_message is None else last_message.content[:120]
        defaults = await chat_settings_service.get_defaults()
        session_metadata = chat_settings_service.resolve_conversation_metadata(conversation_record, defaults)
        return present_conversation_summary(
            conversation_record,
            session_metadata=session_metadata,
            last_message_preview=preview,
            latest_run_status=None if latest_run is None else latest_run.status,
        )

    async def list_conversation_summaries(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        include_archived: bool = False,
    ) -> list:
        conversations = await conversation_store.list_conversations(
            limit=limit,
            offset=offset,
            include_archived=include_archived,
        )
        return [await self.present_summary(conversation) for conversation in conversations]

    async def get_conversation_detail(self, conversation_id: str, *, branch_key: str = MAIN_BRANCH_KEY):
        conversation = await conversation_store.get_conversation(conversation_id)
        if conversation is None:
            return None
        summary = await self.present_summary(conversation)
        messages = await self.list_messages_for_branch(
            conversation_id,
            branch_key=branch_key,
            final_only=True,
        )
        branches = await conversation_store.list_branches(conversation_id)
        workspace_files = await conversation_store.get_workspace_files(conversation_id)
        return present_conversation_detail(
            summary,
            active_branch_key=branch_key,
            branches=branches,
            workspace_path=conversation.workspace_path,
            workspace_files=workspace_files,
            messages=messages,
        )

    async def get_conversation_events(self, conversation_id: str, *, branch_key: str = MAIN_BRANCH_KEY):
        conversation = await conversation_store.get_conversation(conversation_id)
        if conversation is None:
            return None
        events = await self.list_events_for_branch(conversation_id, branch_key=branch_key)
        artifacts = await self.list_artifacts_for_branch(conversation_id, branch_key=branch_key)
        return present_conversation_events(
            conversation_id=conversation_id,
            active_branch_key=branch_key,
            events=events,
            artifacts=artifacts,
        )


conversation_read_model = ConversationReadModel()